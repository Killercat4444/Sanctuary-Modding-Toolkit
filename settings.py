import bpy
from bpy.types import AddonPreferences, PropertyGroup
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)

from .utils import ADDON_ID
from .texture_help import texture_input_description



def _armature_object_poll(self, obj):
    return bool(obj and getattr(obj, "type", None) == "ARMATURE")


def _mesh_object_poll(self, obj):
    return bool(obj and getattr(obj, "type", None) == "MESH")


def _action_owner_armatures(action):
    if action is None:
        return []
    owners = []
    for obj in bpy.data.objects:
        if getattr(obj, "type", None) != "ARMATURE":
            continue
        ad = getattr(obj, "animation_data", None)
        if ad and ad.action == action:
            owners.append(obj)
    return owners




def _default_clip_name(action_name):
    n = str(action_name or "").strip()
    low = n.lower().replace("_", "")
    if low in {"walk", "walk1", "animwalk", "animwalk1", "animwalk01"}:
        return "AnimWalk01"
    if low in {"build", "animbuild"}:
        return "AnimBuild"
    if low in {"rest", "idle", "animrest"}:
        return "Rest"
    return n

def _action_update(self, context):
    action = self.action
    if not action:
        return
    if not self.clip_name:
        self.clip_name = _default_clip_name(action.name)
    # Bind each clip to the armature that actually owns it.  Older builds
    # sampled every Action on whichever armature happened to be selected when
    # Export was pressed, which made the exported feet/rest pose depend on UI
    # selection rather than the animation source.
    if not getattr(self, "source_armature", None):
        active = getattr(context, "active_object", None) if context else None
        if active and getattr(active, "type", None) == "ARMATURE":
            ad = getattr(active, "animation_data", None)
            if ad and ad.action == action:
                self.source_armature = active
        if not getattr(self, "source_armature", None):
            owners = _action_owner_armatures(action)
            if len(owners) == 1:
                self.source_armature = owners[0]
    try:
        start, end = action.frame_range
        self.frame_start = int(round(start))
        self.frame_end = int(round(end))
    except Exception:
        pass




def _preview_action_items(self, context):
    items = [("__NONE__", "No clip selected", "No imported Sanctuary animation clip is selected")]
    try:
        obj = context.active_object if context else None
        arm = None
        if obj and obj.type == "ARMATURE":
            arm = obj
        elif obj and obj.type == "MESH":
            arm = obj.find_armature()
        if arm is None:
            return items
        import json
        raw = arm.get("san_animation_clips_json", "[]")
        metadata = json.loads(raw)
        for meta in metadata:
            name = str(meta.get("name", ""))
            if not name:
                continue
            fps = float(meta.get("fps", 0.0))
            frames = int(meta.get("frame_count", 0))
            duration = float(meta.get("duration", 0.0))
            desc = f"{frames} serialized frames" + (f" @ {fps:g} FPS" if fps else "")
            if duration:
                desc += f" • {duration:g}s"
            items.append((name, name, desc))
        if len(items) > 1:
            return items
        # Compatibility fallback for scenes made with v1.2.x.
        names = json.loads(arm.get("san_animation_actions_json", "[]"))
        for action_name in names:
            action = bpy.data.actions.get(action_name)
            if not action:
                continue
            label = action.get("san_clip_name", action.name)
            items.append((action.name, label, "Legacy imported preview Action"))
    except Exception:
        pass
    return items



def _browser_filter_update(self, context):
    # Any search/filter change returns the visual browser to its first page.
    try:
        self.browser_page = 0
    except Exception:
        pass


class SanctuaryAddonPreferences(AddonPreferences):
    bl_idname = ADDON_ID

    sanctuary_root: StringProperty(
        name="Sanctuary Game Folder",
        description="Folder containing the game 'engine' folder, or the engine folder itself",
        subtype="DIR_PATH",
        default="",
    )
    max_browser_results: IntProperty(
        name="Legacy Browser Results",
        description="Retained for compatibility with v1.0; the visual browser now uses pages",
        default=30,
        min=5,
        max=100,
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="Sanctuary Installation")
        layout.prop(self, "sanctuary_root")
        layout.label(text="The toolkit reads base .sanpack files but does not modify them.", icon="INFO")


class SanctuaryAnimationItem(PropertyGroup):
    enabled: BoolProperty(name="Export", default=True)
    action: PointerProperty(type=bpy.types.Action, name="Action", update=_action_update)
    source_armature: PointerProperty(
        type=bpy.types.Object,
        name="Source Rig",
        description="Armature on which this Action was authored/evaluated. Each clip is sampled on its own source rig.",
        poll=_armature_object_poll,
    )
    source_evaluation: EnumProperty(
        name="Source Evaluation",
        description="Choose whether the Action already contains final bone poses or still needs authoring constraints/IK",
        items=(
            ("BAKED", "Baked Pose", "Preserve the already-baked bone poses; do not solve pose constraints again"),
            ("AUTHORED", "Authored + Constraints", "Evaluate the authoring constraints using their captured mute states"),
        ),
        default="BAKED",
    )
    preserve_native_curves: BoolProperty(
        name="Preserve Native Curves",
        description="Pass through an unchanged imported Sanctuary clip with its original keys, tangents, timing and wrap modes instead of sampling it again",
        default=True,
    )
    clip_name: StringProperty(name="Clip Name", default="")
    loop: BoolProperty(
        name="Loop",
        description="Close the clip by repeating its first sampled pose at the clip duration",
        default=True,
    )
    frame_start: IntProperty(name="Start", default=1)
    frame_end: IntProperty(name="End", default=1)
    fps: FloatProperty(name="FPS", default=24.0, min=1.0, max=240.0)
    source_pose_snapshot: StringProperty(
        name="Source Pose Snapshot",
        description="Internal frozen pose baseline captured when this clip is added; makes unkeyed IK/control channels independent of later selection/save state.",
        default="",
        options={"HIDDEN"},
    )
    source_constraint_snapshot: StringProperty(
        name="Source Constraint Snapshot",
        description="Internal per-clip constraint state. Imported baked Actions disable constraints; authored Actions restore this state for deterministic IK baking.",
        default="",
        options={"HIDDEN"},
    )


class SanctuarySettings(PropertyGroup):
    unit_id: StringProperty(
        name="Unit ID",
        description="Sanctuary unit id, for example ucl1501",
        default="ucl1501",
    )
    output_dir: StringProperty(
        name="Output Folder",
        subtype="DIR_PATH",
        default="//SanctuaryExport/",
    )

    swap_yz_axis: BoolProperty(
        name="Convert Blender/Sanctuary Axes",
        description="Use Sanctuary unit coordinate conversion (swap Blender Y/Z) consistently for model, skeleton, and animation data.",
        default=True,
    )
    # Deprecated: v1.2.6 exposed a UV flip that proved incorrect for stock
    # Sanctuary models. Preserve the property only so older .blend files load;
    # import/export now always preserves serialized UV coordinates verbatim.
    flip_sanctuary_uv_v: BoolProperty(
        name="Flip Sanctuary UV V (Deprecated)",
        default=False,
        options={"HIDDEN"},
    )
    # Deprecated values retained only so old .blend files still load.
    mirror_uv_vertically: BoolProperty(
        name="Mirror UV Vertically (Deprecated)",
        default=False,
        options={"HIDDEN"},
    )
    legacy_flip_uv_v: BoolProperty(
        name="Legacy Flip UV V (Deprecated)",
        default=False,
        options={"HIDDEN"},
    )

    texture_albedo_png: StringProperty(
        name="Albedo PNG", description=texture_input_description("ALBEDO"),
        subtype="FILE_PATH", default="",
    )
    texture_mask_png: StringProperty(
        name="Mask PNG", description=texture_input_description("MASK"),
        subtype="FILE_PATH", default="",
    )
    texture_normal_png: StringProperty(
        name="Normal PNG", description=texture_input_description("NORMAL"),
        subtype="FILE_PATH", default="",
    )
    texture_size: IntProperty(name="Texture Size", default=2048, min=4, max=8192)
    texture_mips: EnumProperty(
        name="Mipmaps",
        items=(
            ("TOP_ONLY", "Top Mip Only", "Write one BC3/DXT5 level; avoids the distance-fade issue seen in custom Sanctuary textures"),
            ("FULL", "Full Mip Chain", "Generate semantic mipmaps down to 1x1"),
        ),
        default="TOP_ONLY",
    )
    mask_input_mode: EnumProperty(
        name="Mask Input",
        items=(
            ("SANCTUARY_RGBA", "Sanctuary RGBA", "R=metallic, G=AO, B=emission, A=smoothness"),
            ("AO_ONLY", "AO / Grayscale", "Use grayscale as AO; metallic/emission are zero and smoothness uses the value below"),
        ),
        default="AO_ONLY",
    )
    mask_default_smoothness: FloatProperty(
        name="Default Smoothness",
        description="Material gloss for AO / Grayscale conversion: 0 is rough, 1 is glossy. Does not smooth mesh faces or change geometry",
        default=118.0 / 255.0,
        min=0.0,
        max=1.0,
    )
    albedo_alpha_mode: EnumProperty(
        name="Albedo Alpha",
        items=(
            ("PRESERVE", "Preserve PNG Alpha", "Preserve alpha when present; RGB PNGs receive alpha 0"),
            ("ZERO", "No Team Color (0)", "Force albedo alpha to zero"),
            ("OPAQUE", "Full Team Color (255)", "Force albedo alpha to 255"),
        ),
        default="ZERO",
    )

    browser_search: StringProperty(name="Search", default="", update=_browser_filter_update)
    browser_selected_unit: StringProperty(name="Selected Unit", default="")
    browser_import_skeleton: BoolProperty(name="Import Skeleton", default=True)
    browser_import_textures: BoolProperty(name="Import Textures", default=True)
    browser_import_animations: BoolProperty(name="Import Animations", default=True)

    import_companion_textures: BoolProperty(
        name="Auto-load Companion Textures",
        description="When manually importing a sanmodel, load nearby albedo/mask/normal DDS files into a Blender preview material",
        default=True,
    )
    import_companion_animations: BoolProperty(
        name="Auto-load Companion Animation",
        description="When manually importing a sanmodel with a skeleton, load the matching/nearby sananimation into Blender Actions",
        default=True,
    )
    preview_animation: EnumProperty(
        name="Clip",
        description="Imported Sanctuary animation to preview on the selected model/armature",
        items=_preview_action_items,
    )
    browser_page: IntProperty(name="Browser Page", default=0, min=0)
    browser_page_size: IntProperty(name="Units Per Page", default=24, min=8, max=48)

    browser_faction_chosen: BoolProperty(name="Chosen", default=True, update=_browser_filter_update)
    browser_faction_guard: BoolProperty(name="Guard", default=True, update=_browser_filter_update)
    browser_faction_eda: BoolProperty(name="EDA", default=True, update=_browser_filter_update)
    browser_faction_other: BoolProperty(name="Other Faction", default=True, update=_browser_filter_update)

    browser_tech_commander: BoolProperty(name="Commander", default=True, update=_browser_filter_update)
    browser_tech1: BoolProperty(name="T1", default=True, update=_browser_filter_update)
    browser_tech2: BoolProperty(name="T2", default=True, update=_browser_filter_update)
    browser_tech3: BoolProperty(name="T3", default=True, update=_browser_filter_update)
    browser_tech4: BoolProperty(name="T4 / Experimental", default=True, update=_browser_filter_update)
    browser_tech_other: BoolProperty(name="Other Tech", default=True, update=_browser_filter_update)

    browser_domain_land: BoolProperty(name="Land", default=True, update=_browser_filter_update)
    browser_domain_air: BoolProperty(name="Air", default=True, update=_browser_filter_update)
    browser_domain_navy: BoolProperty(name="Navy", default=True, update=_browser_filter_update)
    browser_domain_structure: BoolProperty(name="Structure", default=True, update=_browser_filter_update)
    browser_domain_other: BoolProperty(name="Other Unit Type", default=True, update=_browser_filter_update)

    animation_default_fps: FloatProperty(
        name="Default Custom Action FPS",
        description="FPS used when adding ordinary Blender Actions that do not already carry Sanctuary source-FPS metadata",
        default=24.0,
        min=1.0,
        max=240.0,
    )

    export_model_object: PointerProperty(
        type=bpy.types.Object,
        name="Canonical Model Mesh",
        description="Pinned mesh used by Complete Unit Export so changing the active animation/source armature cannot change the model/skeleton being written.",
        poll=_mesh_object_poll,
    )

    animation_target_armature: PointerProperty(
        type=bpy.types.Object,
        name="Target / Skeleton Rig",
        description="Pinned canonical rig whose bone IDs/rest pose define model, skeleton and exported animations. Source animation armatures never replace it implicitly.",
        poll=_armature_object_poll,
    )
    animations: CollectionProperty(type=SanctuaryAnimationItem)
    animation_index: IntProperty(default=0)


CLASSES = (
    SanctuaryAddonPreferences,
    SanctuaryAnimationItem,
    SanctuarySettings,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.sanctuary_toolkit = PointerProperty(type=SanctuarySettings)


def unregister():
    if hasattr(bpy.types.Scene, "sanctuary_toolkit"):
        del bpy.types.Scene.sanctuary_toolkit
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
