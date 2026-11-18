import json
import os
from pathlib import Path

import bpy
from mathutils import Matrix

ADDON_ID = __package__ or "sanctuary_modding_toolkit"

# One coordinate contract for model geometry, skeletons and animations.
#
# Sanctuary's serialized unit coordinates use the historical Y/Z swap used by
# the original working .sanmodel importer/exporter:
#
#     Blender -> Sanctuary: (x, y, z) -> (x, z, y)
#
# This is a reflection (determinant -1), so mesh triangle winding is reversed
# during model export.  Bone matrices do NOT need an additional hard-coded
# local-axis rotation.  A skeleton can use any internally consistent local
# basis; the model bind poses and animation transforms simply have to use the
# exact same basis.
#
# For every object/bone transform M, crossing formats is the involution
#
#     M' = F @ M @ F
#
# where F swaps Y and Z.  Applying it twice returns the original matrix exactly.
_COORD_SWAP = Matrix((
    (1.0, 0.0, 0.0, 0.0),
    (0.0, 0.0, 1.0, 0.0),
    (0.0, 1.0, 0.0, 0.0),
    (0.0, 0.0, 0.0, 1.0),
))


def blender_to_san_matrix(matrix):
    return _COORD_SWAP @ matrix @ _COORD_SWAP


def san_to_blender_matrix(matrix):
    return _COORD_SWAP @ matrix @ _COORD_SWAP


def blender_bone_global_to_san(matrix):
    # Bone transforms use the exact same basis change as all other transforms.
    return _COORD_SWAP @ matrix @ _COORD_SWAP


def san_bone_global_to_blender(matrix):
    # _COORD_SWAP is its own inverse.
    return _COORD_SWAP @ matrix @ _COORD_SWAP


def blender_to_san_vector(v):
    return (float(v[0]), float(v[2]), float(v[1]))


def san_to_blender_vector(v):
    return (float(v[0]), float(v[2]), float(v[1]))


def console(msg):
    print(f"[Sanctuary Toolkit] {msg}")


def get_addon_preferences(context=None):
    context = context or bpy.context
    addon = context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


def resolve_engine_root(user_path):
    """Return the Sanctuary engine folder from a user supplied game path."""
    if not user_path:
        return None
    p = Path(bpy.path.abspath(user_path)).expanduser()
    candidates = [p, p / "engine"]
    if p.name.lower() == "sanctuary_data":
        candidates.append(p.parent)
    for c in candidates:
        if (c / "Sanctuary_Data" / "Gamedata").is_dir():
            return c
    return None


def cache_root():
    path = bpy.utils.user_resource("CONFIG", path="sanctuary_modding_toolkit", create=True)
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def database_path():
    return cache_root() / "unit_database.json"


def icon_cache_dir():
    p = cache_root() / "icon_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def texture_cache_dir():
    p = cache_root() / "texture_preview_cache"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _topological_bones(armature_obj):
    bones = list(armature_obj.data.bones)
    order_index = {b.name: i for i, b in enumerate(bones)}
    roots = sorted((b for b in bones if b.parent is None), key=lambda b: order_index[b.name])
    out = []

    def visit(b):
        out.append(b)
        children = sorted(b.children, key=lambda c: order_index[c.name])
        for child in children:
            visit(child)

    for root in roots:
        visit(root)
    # Defensive fallback for malformed/disconnected data.
    seen = {b.name for b in out}
    out.extend(b for b in bones if b.name not in seen)
    return out


def _is_auto_leaf_end_bone(bone):
    """True for Blender/FBX-style generated leaf end bones we should not export."""
    name = bone.name.lower()
    return (name.endswith("_end") or name.endswith("_endbone")) and not bone.children


def _actual_bone_path(bone):
    """Return the current Blender hierarchy path, ignoring imported metadata."""
    names = [bone.name]
    parent = bone.parent
    while parent:
        names.append(parent.name)
        parent = parent.parent
    return "/".join(reversed(names))


def _finish_existing_bone_map(armature_obj, bones, ordered_existing, persist):
    """Preserve imported IDs while appending newly-created deform bones.

    Imported rigs deliberately retain their original Sanctuary IDs.  Artists can
    subsequently add animation/deformation bones, though, and those bones must be
    appended to the manifest instead of being mistaken for helper controls.
    Non-deform parents required by an added deform bone are included as well.
    """
    mapped_names = {b.name for b in ordered_existing}
    additions = {
        b.name for b in bones
        if b.name not in mapped_names and b.use_deform and not _is_auto_leaf_end_bone(b)
    }

    # An exported bone cannot refer to a parent absent from the manifest.
    for bone in list(bones):
        if bone.name not in additions:
            continue
        parent = bone.parent
        while parent and parent.name not in mapped_names:
            if not _is_auto_leaf_end_bone(parent):
                additions.add(parent.name)
            parent = parent.parent

    ordered = list(ordered_existing)
    ordered.extend(
        b for b in _topological_bones(armature_obj)
        if b.name in additions and b.name not in mapped_names
    )
    mapping = {b.name: i for i, b in enumerate(ordered)}

    if persist:
        appended_names = {b.name for b in ordered[len(ordered_existing):]}
        path_to_blender_name = {}
        for bone in bones:
            if bone.name in mapping:
                bone["san_bone_id"] = mapping[bone.name]
                if bone.name in appended_names:
                    # Duplicating an imported bone also duplicates its custom
                    # properties.  Normalize them so the new bone does not export
                    # under the source bone's name/path/bind index.
                    bone["san_bind_pose_index"] = mapping[bone.name]
                    bone["san_original_name"] = bone.name
                    bone["san_path"] = _actual_bone_path(bone)
                path_to_blender_name[str(bone.get("san_path", _actual_bone_path(bone)))] = bone.name
            elif "san_bone_id" in bone:
                del bone["san_bone_id"]
        armature_obj["san_bone_map_json"] = json.dumps(mapping, sort_keys=True)
        armature_obj["san_bone_paths_json"] = json.dumps(path_to_blender_name, sort_keys=True)
    return mapping, ordered


def get_bone_id_map(armature_obj, persist=True):
    """Get one stable name -> Sanctuary bone ID mapping for all exports.

    Imported Sanctuary skeletons keep their original `san_bone_id` values.
    Newly-created deform bones are appended after those stable IDs; non-deform
    helper controls remain omitted unless needed as an exported bone's parent.

    For fresh Blender rigs we create a deterministic parent-before-child map and
    omit obvious autogenerated ``*_end`` leaves.  The resulting map is persisted
    and reused by model, skeleton and animation export so all three files share
    exactly the same bone IDs.
    """
    if not armature_obj or armature_obj.type != "ARMATURE":
        return {}, []

    bones = list(armature_obj.data.bones)

    # First preference: any existing contiguous mapped subset.  Unmapped helper
    # bones are deliberately ignored instead of invalidating the mapping.
    by_prop = {}
    duplicate = False
    for b in bones:
        if "san_bone_id" not in b:
            continue
        try:
            bid = int(b["san_bone_id"])
        except Exception:
            continue
        if bid < 0 or bid in by_prop:
            duplicate = True
            break
        by_prop[bid] = b
    if not duplicate and by_prop and sorted(by_prop) == list(range(len(by_prop))):
        ordered_existing = [by_prop[i] for i in range(len(by_prop))]
        # v1.3.0/1.3.1 could persist IDs onto Blender/FBX autogenerated
        # ``*_end`` leaves. If they are not genuine imported Sanctuary bones,
        # strip them now and compact the IDs so an old bad mapping cannot poison
        # a new export. Genuine imported bones carry san_original_name.
        has_stale_auto_ends = any(
            _is_auto_leaf_end_bone(b) and "san_original_name" not in b
            for b in ordered_existing
        )
        if not has_stale_auto_ends:
            mapped_names = {b.name for b in ordered_existing}
            if all((b.parent is None or b.parent.name in mapped_names) for b in ordered_existing):
                return _finish_existing_bone_map(
                    armature_obj, bones, ordered_existing, persist
                )
        else:
            ordered = [
                b for b in ordered_existing
                if not (_is_auto_leaf_end_bone(b) and "san_original_name" not in b)
            ]
            mapping = {b.name: i for i, b in enumerate(ordered)}
            if persist:
                for b in bones:
                    if b.name in mapping:
                        b["san_bone_id"] = mapping[b.name]
                    elif "san_bone_id" in b:
                        del b["san_bone_id"]
                armature_obj["san_bone_map_json"] = json.dumps(mapping, sort_keys=True)
            return _finish_existing_bone_map(armature_obj, bones, ordered, persist)

    # Older toolkit builds may have stored a JSON name/id map on the object.
    # Accept it as a subset as long as all named bones still exist and IDs are
    # contiguous; extra unmapped helper bones are okay.
    raw = armature_obj.get("san_bone_map_json")
    if raw:
        try:
            stored = {str(k): int(v) for k, v in json.loads(raw).items()}
            existing = {b.name: b for b in bones}
            if stored and all(name in existing for name in stored):
                ids = sorted(stored.values())
                if ids == list(range(len(stored))):
                    ordered = [existing[name] for name, _ in sorted(stored.items(), key=lambda kv: kv[1])]
                    ordered = [
                        b for b in ordered
                        if not (_is_auto_leaf_end_bone(b) and "san_original_name" not in b)
                    ]
                    mapping = {b.name: i for i, b in enumerate(ordered)}
                    mapped_names = set(mapping)
                    if all((b.parent is None or b.parent.name in mapped_names) for b in ordered):
                        if persist:
                            for b in bones:
                                if b.name in mapping:
                                    b["san_bone_id"] = mapping[b.name]
                                elif "san_bone_id" in b:
                                    del b["san_bone_id"]
                            armature_obj["san_bone_map_json"] = json.dumps(mapping, sort_keys=True)
                        return _finish_existing_bone_map(
                            armature_obj, bones, ordered, persist
                        )
        except Exception:
            pass

    # Fresh rig: parent-before-child order, excluding obvious autogenerated leaf
    # end bones.  Build_L/Build_R and other intentional attachment bones are kept.
    candidates = [b for b in bones if not _is_auto_leaf_end_bone(b)]
    candidate_names = {b.name for b in candidates}
    topological = _topological_bones(armature_obj)
    ordered = [b for b in topological if b.name in candidate_names]

    # If excluding a helper would orphan a retained child, include its parent.
    # This is rare but keeps the exported hierarchy valid.
    changed = True
    while changed:
        changed = False
        names = {b.name for b in ordered}
        for b in list(ordered):
            p = b.parent
            if p is not None and p.name not in names:
                ordered.append(p)
                changed = True
        if changed:
            rank = {b.name: i for i, b in enumerate(_topological_bones(armature_obj))}
            ordered.sort(key=lambda b: rank[b.name])

    mapping = {b.name: i for i, b in enumerate(ordered)}
    if persist:
        # Clear stale IDs from bones not in the export manifest so they cannot
        # silently reappear on a later export.
        for b in bones:
            if b.name in mapping:
                b["san_bone_id"] = mapping[b.name]
            elif "san_bone_id" in b:
                del b["san_bone_id"]
        armature_obj["san_bone_map_json"] = json.dumps(mapping, sort_keys=True)
    return mapping, ordered

def full_bone_path(bone):
    # Imported Sanctuary skeletons preserve their exact original hierarchy path.
    # This matters because Sanctuary curve paths can disambiguate repeated bone
    # names even though Blender itself requires globally-unique bone names.
    stored = bone.get("san_path") if bone is not None else None
    if stored:
        return str(stored)
    names = [bone.name]
    p = bone.parent
    while p:
        names.append(p.name)
        p = p.parent
    return "/".join(reversed(names))


def find_mesh_for_export(context):
    active = context.active_object
    if active and active.type == "MESH":
        return active, active.find_armature()
    if active and active.type == "ARMATURE":
        meshes = [c for c in active.children_recursive if c.type == "MESH" and c.find_armature() == active]
        if not meshes:
            meshes = [c for c in active.children if c.type == "MESH"]
        if meshes:
            return meshes[0], active
    # Fall back to selected objects.
    for obj in context.selected_objects:
        if obj.type == "MESH":
            return obj, obj.find_armature()
    return None, None


def safe_unit_id(text):
    return "".join(c.lower() for c in (text or "").strip() if c.isalnum() or c in "_-")


def ensure_output_dir(path):
    # bpy.path.abspath expects a string and calls startswith internally. Export
    # routing now also supplies pathlib.Path objects for mod-project folders.
    p = Path(bpy.path.abspath(str(path))).expanduser()
    p.mkdir(parents=True, exist_ok=True)
    return p



def atomic_overwrite_bytes(path, data):
    """Atomically replace *path* with bytes, overwriting an existing file.

    Using os.replace avoids the annoying manual-delete cycle on Windows and
    prevents readers from seeing a partially written Sanctuary asset.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_bytes(data)
        os.replace(str(tmp), str(p))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def atomic_overwrite_text(path, text, encoding="utf-8"):
    """Atomically replace *path* with text, overwriting an existing file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    try:
        tmp.write_text(text, encoding=encoding, newline="\n")
        os.replace(str(tmp), str(p))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass

def q_to_dict(q):
    return {"x": float(q.x), "y": float(q.y), "z": float(q.z), "w": float(q.w)}


def v_to_dict(v):
    return {"x": float(v.x), "y": float(v.y), "z": float(v.z)}
