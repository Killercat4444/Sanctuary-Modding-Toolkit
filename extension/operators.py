from pathlib import Path
import json

import bpy
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty

from .dds_tools import convert_texture_set
from .material_io import apply_companion_textures_from_paths
from .sananimation_io import (
    begin_direct_preview,
    export_sananimation_file,
    get_cached_preview_clip,
    import_sananimation_file,
    register_preview_handler,
    stop_direct_preview,
    unregister_preview_handler,
)
from .sanmodel_io import (
    export_sanmodel_file,
    import_sanmodel,
    parse_sanmodel_file,
    validate_model_against_armature,
)
from .sanskeleton_io import (
    build_sanskeleton_dict,
    create_armature_from_skeleton,
    export_sanskeleton_file,
    parse_sanskeleton_file,
)
from .utils import (
    console,
    ensure_output_dir,
    find_mesh_for_export,
    full_bone_path,
    get_bone_id_map,
    safe_unit_id,
)


def _settings(context):
    return context.scene.sanctuary_toolkit


def _unit_id(context):
    uid = safe_unit_id(_settings(context).unit_id)
    if not uid:
        raise ValueError("Enter a Unit ID first (for example ucl1501)")
    return uid


def _output_dir(context):
    settings = _settings(context)
    # Once a mod project is active every export shares its canonical unit
    # folder. This prevents model/skeleton/animation/texture files drifting
    # into unrelated ad-hoc output paths.
    try:
        from .mod_projects import active_project
        project = active_project(context, required=False)
        if project is not None:
            return ensure_output_dir(project / "Units" / _unit_id(context) / "LOD0")
    except (ImportError, OSError, ValueError):
        pass
    return ensure_output_dir(settings.output_dir)


def _active_armature(context):
    obj = context.active_object
    return obj if obj and obj.type == "ARMATURE" else None


def _matrix_flat(m):
    return [float(m[r][c]) for r in range(4) for c in range(4)]


def _capture_pose_snapshot(arm):
    """Freeze the source rig pose used for channels an Action does not key.

    Blender Actions do not reset unkeyed channels.  Saving/selecting another rig
    can therefore change IK target/control defaults.  Capture one baseline per
    animation item when it is added so export is deterministic afterward.
    """
    if arm is None or getattr(arm, "type", None) != "ARMATURE":
        return ""
    payload = {pb.name: _matrix_flat(pb.matrix_basis.copy()) for pb in arm.pose.bones}
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _capture_constraint_snapshot(arm):
    """Freeze intentional per-clip constraint mute states for authored Actions."""
    if arm is None or getattr(arm, "type", None) != "ARMATURE":
        return ""
    payload = {
        pb.name: {constraint.name: bool(constraint.mute) for constraint in pb.constraints}
        for pb in arm.pose.bones
        if pb.constraints
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _canonical_model_and_rig(context):
    settings = _settings(context)
    obj = getattr(settings, "export_model_object", None)
    arm = getattr(settings, "animation_target_armature", None)
    if obj is not None and getattr(obj, "type", None) != "MESH":
        obj = None
    if arm is not None and getattr(arm, "type", None) != "ARMATURE":
        arm = None

    # First setup may use the current mesh; after that the pins own Complete Export.
    if obj is None:
        active_obj, active_arm = find_mesh_for_export(context)
        if active_obj is not None:
            obj = active_obj
            settings.export_model_object = obj
            if arm is None and active_arm is not None:
                arm = active_arm
                settings.animation_target_armature = arm

    if obj is not None:
        linked = obj.find_armature()
        if arm is None and linked is not None:
            arm = linked
            settings.animation_target_armature = arm
        elif arm is not None and linked is not None and linked != arm:
            raise ValueError(
                f"Pinned model '{obj.name}' is linked to '{linked.name}', but the canonical rig is '{arm.name}'. "
                "Select the final model mesh and export it once to repin the canonical pair."
            )
    return obj, arm


def _target_armature(context):
    obj = context.active_object
    if obj and obj.type == "ARMATURE":
        return obj
    if obj and obj.type == "MESH":
        return obj.find_armature()
    return None


def _action_owner_armatures(action):
    owners = []
    if action is None:
        return owners
    for obj in bpy.data.objects:
        if getattr(obj, "type", None) != "ARMATURE":
            continue
        ad = getattr(obj, "animation_data", None)
        if ad and ad.action == action:
            owners.append(obj)
    return owners


def _animation_target_armature(context):
    settings = _settings(context)
    target = getattr(settings, "animation_target_armature", None)
    if target and getattr(target, "type", None) == "ARMATURE":
        return target

    # Never infer the canonical target from an animation Source Rig.  A source
    # rig may contain IK controls/helper bones that are intentionally absent
    # from the exported model/skeleton manifest.
    obj = context.active_object
    if obj and obj.type == "MESH":
        arm = obj.find_armature()
        if arm:
            return arm
    if obj and obj.type == "ARMATURE":
        return obj
    return None



def _default_export_clip_name(action):
    n = str(action.get("san_clip_name", "") or getattr(action, "name", "") or "").strip()
    low = n.lower().replace("_", "")
    if low in {"walk", "walk1", "animwalk", "animwalk1", "animwalk01"}:
        return "AnimWalk01"
    if low in {"build", "animbuild"}:
        return "AnimBuild"
    if low in {"rest", "idle", "animrest"}:
        return "Rest"
    return n


def _action_source_fps(action, settings):
    for key in ("san_preview_fps", "san_fps", "san_source_fps", "san_animation_fps"):
        try:
            value = float(action.get(key, 0.0))
            if value > 0.0:
                return value
        except Exception:
            pass
    return float(settings.animation_default_fps)


def _validate_target_against_existing_outputs(context, arm, uid, out_dir):
    """Reject animation export if target rig does not match model/skeleton outputs."""
    target_data = build_sanskeleton_dict(arm, _settings(context).swap_yz_axis)
    target_entries = target_data.get("bonesHierarchy", [])

    sk_path = out_dir / f"{uid}.sanskeleton"
    if sk_path.is_file():
        disk = parse_sanskeleton_file(sk_path).get("bonesHierarchy", [])
        if len(disk) != len(target_entries):
            raise ValueError(
                f"Animation Target Rig exports {len(target_entries)} bones, but {sk_path.name} has {len(disk)}. "
                "Select the SAME armature used to export the model/skeleton as Target / Skeleton Rig."
            )
        for i, (a, b) in enumerate(zip(target_entries, disk)):
            if (str(a.get("name")) != str(b.get("name")) or
                    int(a.get("parentBoneId", -1)) != int(b.get("parentBoneId", -1))):
                raise ValueError(
                    f"Animation Target Rig does not match {sk_path.name} at bone ID {i}: "
                    f"target={a.get('name')!r}, file={b.get('name')!r}. "
                    "Use the same canonical export rig for model, skeleton, and animation."
                )

    model_path = out_dir / f"{uid}_lod0.sanmodel"
    if model_path.is_file():
        model = parse_sanmodel_file(model_path)
        expected_model_name = f"{uid}_lod0"
        if str(model.name).lower() != expected_model_name.lower():
            raise ValueError(
                f"{model_path.name} internally identifies itself as {model.name!r}, expected {expected_model_name!r}. "
                "Re-export the model with the correct Unit ID instead of renaming an older .sanmodel."
            )
        if len(model.bindposes) != len(target_entries):
            raise ValueError(
                f"Animation Target Rig exports {len(target_entries)} bones, but {model_path.name} contains "
                f"{len(model.bindposes)} bind poses. Re-select the model's actual armature as Target / Skeleton Rig."
            )

    return len(target_entries)

def _companion_skeleton_path(model_path: Path):
    stem = model_path.stem
    low = stem.lower()
    if low.endswith("_lod0"):
        base = stem[:-5]
    else:
        base = stem
    candidate = model_path.with_name(base + ".sanskeleton")
    return candidate if candidate.is_file() else None


def _companion_animation_path(model_path: Path):
    stem = model_path.stem
    base = stem[:-5] if stem.lower().endswith("_lod0") else stem
    exact = model_path.with_name(base + ".sananimation")
    if exact.is_file():
        return exact
    nearby = sorted(model_path.parent.glob("*.sananimation"))
    return nearby[0] if len(nearby) == 1 else None


class SANCTUARY_OT_import_sanmodel(Operator, ImportHelper):
    bl_idname = "sanctuary.import_sanmodel"
    bl_label = "Import .sanmodel"
    bl_description = "Import a Sanctuary .sanmodel and automatically use a matching .sanskeleton beside it when available"

    filename_ext = ".sanmodel"
    filter_glob: StringProperty(default="*.sanmodel", options={"HIDDEN"})

    def execute(self, context):
        settings = _settings(context)
        path = Path(self.filepath)
        try:
            from .shield_import import reuse_model_import
            reused=reuse_model_import(context,path)
            if reused:
                self.report({'INFO'},'Refreshed blueprint shields on the existing imported generator')
                return {'FINISHED'}
            model = parse_sanmodel_file(path)
            skeleton_obj = None
            companion = _companion_skeleton_path(path)
            if companion:
                skeleton = parse_sanskeleton_file(companion)
                skeleton_obj = create_armature_from_skeleton(
                    context,
                    skeleton,
                    name=companion.stem + "_Rig",
                    swap_yz=settings.swap_yz_axis,
                )
            obj = import_sanmodel(
                context,
                model,
                skeleton_obj=skeleton_obj,
                swap_yz=settings.swap_yz_axis,
                mirror_uv=False,
            )
            obj["san_source_model_path"] = str(path)
            # Imported reference units never replace the project's export ID.
            stem = path.stem.lower()
            uid = stem[:-5] if stem.endswith("_lod0") else stem
            loaded = []
            if settings.import_companion_textures:
                try:
                    _mat, images, paths = apply_companion_textures_from_paths(obj, path, uid)
                    if images:
                        loaded.append(f"{len(images)} texture(s)")
                except Exception as tex_exc:
                    console(f"Companion texture preview failed: {tex_exc}")
            if skeleton_obj and settings.import_companion_animations:
                anim_path = _companion_animation_path(path)
                if anim_path:
                    try:
                        clips, actions = import_sananimation_file(skeleton_obj, anim_path, settings.swap_yz_axis)
                        if clips:
                            settings.preview_animation = clips[0].name
                            loaded.append(f"{len(actions)} editable animation Action(s)")
                    except Exception as anim_exc:
                        console(f"Companion animation import failed: {anim_exc}")
            # Keep the mesh active so its material is immediately visible and the
            # animation preview panel can resolve its linked armature.
            from .shield_import import attach_imported_unit
            attach_imported_unit(context,obj,skeleton_obj,model_path=path)
            if obj.get('san_shield_import_diagnostics'):
                self.report({'WARNING'},obj['san_shield_import_diagnostics'])
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            context.view_layer.objects.active = obj
            suffix = (" + " + ", ".join(loaded)) if loaded else ""
            self.report({"INFO"}, f"Imported {path.name}" + (" + companion skeleton" if companion else "") + suffix)
            return {"FINISHED"}
        except Exception as exc:
            console(f"Model import failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SANCTUARY_OT_export_sanmodel(Operator):
    bl_idname = "sanctuary.export_sanmodel"
    bl_label = "Export LOD0 Model"
    bl_description = "Export the selected mesh with bone IDs matched by vertex-group name to the selected/linked armature"

    @classmethod
    def poll(cls, context):
        obj, _arm = find_mesh_for_export(context)
        return obj is not None

    def execute(self, context):
        settings = _settings(context)
        try:
            uid = _unit_id(context)
            out_dir = _output_dir(context)
            obj, armature = find_mesh_for_export(context)
            if obj is None:
                raise ValueError("Select a mesh, or select an armature that has a child mesh")
            if armature is None and obj.vertex_groups:
                raise ValueError("The mesh has vertex groups but no Armature modifier/linked armature. Link the rig before export.")
            path = out_dir / f"{uid}_lod0.sanmodel"
            stats = export_sanmodel_file(
                context,
                obj,
                armature,
                path,
                f"{uid}_lod0",
                swap_yz=settings.swap_yz_axis,
                mirror_uv=False,
            )
            settings.export_model_object = obj
            if armature is not None:
                settings.animation_target_armature = armature
            msg = f"Exported {path.name}: {stats['triangles']:,} tris, {stats['bones']} bones"
            if stats["multi_weight_vertices"]:
                msg += f", {stats['multi_weight_vertices']:,} multi-weight vertices reduced to strongest bone"
            if stats["unknown_vertex_groups"]:
                msg += f", {stats['unknown_vertex_groups']:,} unknown groups fell back to Root"
            self.report({"WARNING" if stats["unknown_vertex_groups"] else "INFO"}, msg)
            return {"FINISHED"}
        except Exception as exc:
            console(f"Model export failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SANCTUARY_OT_export_skeleton(Operator):
    bl_idname = "sanctuary.export_skeleton"
    bl_label = "Export Selected Armature"
    bl_description = "Export the active armature as a Sanctuary .sanskeleton using the same stable bone IDs as model and animation export"

    @classmethod
    def poll(cls, context):
        settings = _settings(context)
        return bool(getattr(settings, "animation_target_armature", None) or _active_armature(context))

    def execute(self, context):
        settings = _settings(context)
        arm = getattr(settings, "animation_target_armature", None) or _active_armature(context)
        try:
            uid = _unit_id(context)
            out_dir = _output_dir(context)
            count = export_sanskeleton_file(arm, out_dir / f"{uid}.sanskeleton", settings.swap_yz_axis)
            if settings.animation_target_armature is None:
                settings.animation_target_armature = arm
            self.report({"INFO"}, f"Exported {uid}.sanskeleton ({count} bones) from canonical rig {arm.name}")
            return {"FINISHED"}
        except Exception as exc:
            console(f"Skeleton export failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SANCTUARY_OT_animation_set_target(Operator):
    bl_idname = "sanctuary.animation_set_target"
    bl_label = "Use Selected as Animation Target"
    bl_description = "Use the active armature as the canonical skeleton/rest rig for all exported animation clips"

    @classmethod
    def poll(cls, context):
        return _active_armature(context) is not None

    def execute(self, context):
        arm = _active_armature(context)
        _settings(context).animation_target_armature = arm
        self.report({"INFO"}, f"Animation target rig: {arm.name}")
        return {"FINISHED"}


class SANCTUARY_OT_animation_add_current(Operator):
    bl_idname = "sanctuary.animation_add_current"
    bl_label = "Add Current Action"

    @classmethod
    def poll(cls, context):
        arm = _active_armature(context)
        return bool(arm and arm.animation_data and arm.animation_data.action)

    def execute(self, context):
        arm = _active_armature(context)
        action = arm.animation_data.action
        settings = _settings(context)
        if any(item.action == action for item in settings.animations):
            self.report({"INFO"}, f"{action.name} is already in the export list")
            return {"FINISHED"}
        item = settings.animations.add()
        item.source_armature = arm
        item.action = action
        item.source_pose_snapshot = _capture_pose_snapshot(arm)
        item.source_constraint_snapshot = _capture_constraint_snapshot(arm)
        item.clip_name = _default_export_clip_name(action)
        start, end = action.frame_range
        item.frame_start = int(round(start))
        item.frame_end = int(round(end))
        item.fps = _action_source_fps(action, settings)
        settings.animation_index = len(settings.animations) - 1
        return {"FINISHED"}


class SANCTUARY_OT_animation_add_all(Operator):
    bl_idname = "sanctuary.animation_add_all"
    bl_label = "Add All Selected Actions"
    bl_description = "Add selected Action datablocks, or all Actions owned by the selected armatures, without adding animations from unrelated imported models"

    def execute(self, context):
        settings = _settings(context)
        existing = {item.action for item in settings.animations if item.action}
        selected_actions = {
            item for item in (getattr(context, "selected_ids", None) or ())
            if isinstance(item, bpy.types.Action)
        }
        selected_armatures = {
            obj for obj in (getattr(context, "selected_objects", None) or ())
            if getattr(obj, "type", None) == "ARMATURE"
        }
        if selected_actions:
            candidates = selected_actions
        else:
            candidates = {
                action for action in bpy.data.actions
                if selected_armatures.intersection(_action_owner_armatures(action))
            }
        if not candidates:
            self.report({"WARNING"}, "Select one or more Actions, or select the armatures that own them")
            return {"CANCELLED"}
        added = 0
        for action in sorted(candidates, key=lambda a: a.name.lower()):
            if action in existing:
                continue
            item = settings.animations.add()
            owners = _action_owner_armatures(action)
            active = _active_armature(context)
            if active in owners:
                item.source_armature = active
            elif len(owners) == 1:
                item.source_armature = owners[0]
            elif active is not None:
                # Fallback only; the Source Rig remains visible/editable in UI.
                item.source_armature = active
            if item.source_armature is not None:
                item.source_pose_snapshot = _capture_pose_snapshot(item.source_armature)
                item.source_constraint_snapshot = _capture_constraint_snapshot(item.source_armature)
            item.action = action
            item.clip_name = _default_export_clip_name(action)
            start, end = action.frame_range
            item.frame_start = int(round(start))
            item.frame_end = int(round(end))
            item.fps = _action_source_fps(action, settings)
            added += 1
        if settings.animations:
            settings.animation_index = min(settings.animation_index, len(settings.animations) - 1)
        self.report({"INFO"}, f"Added {added} selected Action{'s' if added != 1 else ''}")
        return {"FINISHED"}


class SANCTUARY_OT_animation_remove(Operator):
    bl_idname = "sanctuary.animation_remove"
    bl_label = "Remove Animation"

    def execute(self, context):
        settings = _settings(context)
        idx = settings.animation_index
        if 0 <= idx < len(settings.animations):
            settings.animations.remove(idx)
            settings.animation_index = min(max(0, idx - 1), max(0, len(settings.animations) - 1))
        return {"FINISHED"}


class SANCTUARY_OT_animation_clear(Operator):
    bl_idname = "sanctuary.animation_clear"
    bl_label = "Clear Animation List"

    def execute(self, context):
        _settings(context).animations.clear()
        _settings(context).animation_index = 0
        return {"FINISHED"}


class SANCTUARY_OT_export_animations(Operator):
    bl_idname = "sanctuary.export_animations"
    bl_label = "Export .sananimation"
    bl_description = "Sample each selected Action on its stored Source Rig and retarget it to the fixed Target/Skeleton Rig"

    @classmethod
    def poll(cls, context):
        return _animation_target_armature(context) is not None

    def execute(self, context):
        settings = _settings(context)
        arm = _animation_target_armature(context)
        try:
            uid = _unit_id(context)
            out_dir = _output_dir(context)
            expected_bones = _validate_target_against_existing_outputs(context, arm, uid, out_dir)
            stats = export_sananimation_file(
                context,
                arm,
                settings.animations,
                out_dir / f"{uid}.sananimation",
                swap_yz=settings.swap_yz_axis,
            )
            self.report({"INFO"}, f"Exported {stats['clips']} clips / {expected_bones} bones to {uid}.sananimation")
            return {"FINISHED"}
        except Exception as exc:
            console(f"Animation export failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SANCTUARY_OT_convert_textures(Operator):
    bl_idname = "sanctuary.convert_textures"
    bl_label = "Convert PNGs to Sanctuary DDS"
    bl_description = "Convert the populated Albedo, Mask and Normal PNG slots to BC3/DXT5 Sanctuary textures"

    def execute(self, context):
        settings = _settings(context)
        try:
            uid = _unit_id(context)
            out_dir = _output_dir(context)
            outputs = convert_texture_set(settings, out_dir, uid)
            if not outputs:
                raise ValueError("Choose at least one PNG texture first")
            names = ", ".join(path.name for path, _stats in outputs.values())
            self.report({"INFO"}, f"Converted: {names}")
            return {"FINISHED"}
        except Exception as exc:
            console(f"Texture conversion failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SANCTUARY_OT_load_sananimation(Operator, ImportHelper):
    bl_idname = "sanctuary.load_sananimation"
    bl_label = "Load .sananimation"
    bl_description = "Load Sanctuary animation clips onto the selected model's armature for Blender preview"
    filename_ext = ".sananimation"
    filter_glob: StringProperty(default="*.sananimation", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return _target_armature(context) is not None

    def execute(self, context):
        arm = _target_armature(context)
        settings = _settings(context)
        try:
            clips, actions = import_sananimation_file(arm, self.filepath, settings.swap_yz_axis)
            if clips:
                settings.preview_animation = clips[0].name
            self.report({"INFO"}, f"Loaded {len(actions)} editable Sanctuary Action(s)")
            return {"FINISHED"}
        except Exception as exc:
            console(f"Animation import failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SANCTUARY_OT_load_companion_textures(Operator):
    bl_idname = "sanctuary.load_companion_textures"
    bl_label = "Load Companion Textures"
    bl_description = "Load nearby Sanctuary DDS textures into a Blender preview material for the selected imported sanmodel"

    @classmethod
    def poll(cls, context):
        return bool(context.active_object and context.active_object.type == "MESH" and context.active_object.get("san_source_model_path"))

    def execute(self, context):
        obj = context.active_object
        try:
            model_path = Path(obj["san_source_model_path"])
            uid = obj.get("san_unit_id") or _settings(context).unit_id
            _mat, images, _paths = apply_companion_textures_from_paths(obj, model_path, uid)
            if not images:
                raise ValueError("No companion albedo/mask/normal DDS files were found beside this sanmodel")
            self.report({"INFO"}, f"Loaded {len(images)} DDS texture(s) for viewport preview")
            return {"FINISHED"}
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SANCTUARY_OT_preview_animation_play(Operator):
    bl_idname = "sanctuary.preview_animation_play"
    bl_label = "Play Selected Clip"
    bl_description = "Assign the selected imported Sanctuary clip to this rig and start Blender playback"

    @classmethod
    def poll(cls, context):
        return _target_armature(context) is not None

    def execute(self, context):
        settings = _settings(context)
        arm = _target_armature(context)
        clip_name = settings.preview_animation
        if not clip_name or clip_name == "__NONE__" or get_cached_preview_clip(arm, clip_name) is None:
            self.report({"ERROR"}, "Choose a loaded Sanctuary clip first; reload the .sananimation if this scene was reopened")
            return {"CANCELLED"}
        try:
            clip = begin_direct_preview(context, arm, clip_name)
            self.report({"INFO"}, f"Playing {clip.name} on the Blender timeline")
            return {"FINISHED"}
        except Exception as exc:
            console(f"Direct animation preview failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SANCTUARY_OT_preview_animation_stop(Operator):
    bl_idname = "sanctuary.preview_animation_stop"
    bl_label = "Pause Preview"
    bl_description = "Pause Blender playback but keep the imported Action active and editable"

    def execute(self, context):
        stop_direct_preview(context, reset_pose=False)
        return {"FINISHED"}


class SANCTUARY_OT_preview_animation_restore(Operator):
    bl_idname = "sanctuary.preview_animation_restore"
    bl_label = "Restore Rig"
    bl_description = "Leave imported-animation preview mode, restore constraints/NLA/timing, and return the armature to its prior Action"

    def execute(self, context):
        from .sananimation_io import restore_action_preview
        restore_action_preview(context)
        return {"FINISHED"}


class SANCTUARY_OT_validate_rig(Operator):
    bl_idname = "sanctuary.validate_rig"
    bl_label = "Validate Model / Rig"
    bl_description = "Validate the active mesh/armature bone map and, when possible, report the bind-pose identity contract"

    def execute(self, context):
        settings = _settings(context)
        obj, arm = find_mesh_for_export(context)
        if obj is None:
            self.report({"ERROR"}, "Select a mesh or its armature")
            return {"CANCELLED"}
        if arm is None:
            self.report({"WARNING"}, "Mesh is not linked to an armature; it will export as an unskinned model")
            return {"FINISHED"}
        mapping, ordered = get_bone_id_map(arm)
        unknown_groups = [vg.name for vg in obj.vertex_groups if vg.name not in mapping and not vg.name.startswith("Bone_")]
        if unknown_groups:
            self.report({"WARNING"}, f"{len(unknown_groups)} vertex groups do not match armature bones; see console")
            console("Unknown vertex groups: " + ", ".join(unknown_groups))
        else:
            self.report({"INFO"}, f"Bone map valid: {len(ordered)} bones, vertex-group names resolve by name")
        return {"FINISHED"}


class SANCTUARY_OT_export_complete_assets(Operator):
    bl_idname = "sanctuary.export_complete_assets"
    bl_label = "Export Complete Unit Assets"
    bl_description = "Export the available model, skeleton, selected animation clips, and populated texture slots into the project output folder"

    def execute(self, context):
        settings = _settings(context)
        try:
            uid = _unit_id(context)
            out_dir = _output_dir(context)
            obj, arm = _canonical_model_and_rig(context)
            written = []
            warnings = []

            if obj:
                stats = export_sanmodel_file(
                    context,
                    obj,
                    arm,
                    out_dir / f"{uid}_lod0.sanmodel",
                    f"{uid}_lod0",
                    swap_yz=settings.swap_yz_axis,
                    mirror_uv=False,
                )
                written.append(f"model ({stats['triangles']:,} tris)")
                if stats["unknown_vertex_groups"]:
                    warnings.append(f"{stats['unknown_vertex_groups']} unknown mesh vertex groups")
            else:
                warnings.append("no canonical model mesh pinned")

            if arm:
                count = export_sanskeleton_file(arm, out_dir / f"{uid}.sanskeleton", settings.swap_yz_axis)
                written.append(f"skeleton ({count} bones)")
                if any(item.enabled and item.action for item in settings.animations):
                    astats = export_sananimation_file(
                        context,
                        arm,
                        settings.animations,
                        out_dir / f"{uid}.sananimation",
                        swap_yz=settings.swap_yz_axis,
                    )
                    written.append(f"animation ({astats['clips']} clips)")
            elif any(item.enabled and item.action for item in settings.animations):
                warnings.append("animation list exists but no armature is linked/selected")

            textures = convert_texture_set(settings, out_dir, uid)
            if textures:
                written.append(f"{len(textures)} DDS texture(s)")

            if not written:
                raise ValueError("Nothing was available to export")
            msg = "Exported " + ", ".join(written)
            if warnings:
                msg += ". Warnings: " + "; ".join(warnings)
            self.report({"WARNING" if warnings else "INFO"}, msg)
            return {"FINISHED"}
        except Exception as exc:
            console(f"Complete asset export failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}


class SANCTUARY_OT_update_team_preview(Operator):
    bl_idname="sanctuary.update_team_preview";bl_label="Apply Faction Color Preview"
    bl_description="Update faction-color nodes in imported Sanctuary preview materials"
    def execute(self,context):
        color=context.scene.sanctuary_toolkit.preview_team_color;changed=0
        for material in bpy.data.materials:
            if not material.use_nodes:continue
            node=material.node_tree.nodes.get("SMT Team Color")
            if node:node.outputs[0].default_value=color;changed+=1
        self.report({"INFO"},f"Updated {changed} Sanctuary preview material(s)")
        return {"FINISHED"}


CLASSES = (
    SANCTUARY_OT_import_sanmodel,
    SANCTUARY_OT_export_sanmodel,
    SANCTUARY_OT_export_skeleton,
    SANCTUARY_OT_animation_set_target,
    SANCTUARY_OT_animation_add_current,
    SANCTUARY_OT_animation_add_all,
    SANCTUARY_OT_animation_remove,
    SANCTUARY_OT_animation_clear,
    SANCTUARY_OT_export_animations,
    SANCTUARY_OT_convert_textures,
    SANCTUARY_OT_load_sananimation,
    SANCTUARY_OT_load_companion_textures,
    SANCTUARY_OT_preview_animation_play,
    SANCTUARY_OT_preview_animation_stop,
    SANCTUARY_OT_preview_animation_restore,
    SANCTUARY_OT_validate_rig,
    SANCTUARY_OT_export_complete_assets,
    SANCTUARY_OT_update_team_preview,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    register_preview_handler()


def unregister():
    unregister_preview_handler()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
