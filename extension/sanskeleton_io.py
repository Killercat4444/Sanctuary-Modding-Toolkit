import json
import math
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion, Vector

from .utils import (
    atomic_overwrite_text,
    blender_bone_global_to_san,
    console,
    get_bone_id_map,
    q_to_dict,
    san_bone_global_to_blender,
    v_to_dict,
)


def parse_sanskeleton_bytes(blob):
    if isinstance(blob, bytes):
        text = blob.decode("utf-8-sig")
    else:
        text = str(blob)
    data = json.loads(text)
    if "bonesHierarchy" not in data or not isinstance(data["bonesHierarchy"], list):
        raise ValueError("sanskeleton is missing bonesHierarchy")
    return data


def parse_sanskeleton_file(path):
    return parse_sanskeleton_bytes(Path(path).read_bytes())


def _san_local_matrix(entry):
    p = entry.get("localPosition", {})
    r = entry.get("localRotation", {})
    pos = Vector((float(p.get("x", 0)), float(p.get("y", 0)), float(p.get("z", 0))))
    q = Quaternion((
        float(r.get("w", 1)),
        float(r.get("x", 0)),
        float(r.get("y", 0)),
        float(r.get("z", 0)),
    ))
    # mathutils.Quaternion has no `.length` attribute in Blender 4.x.
    # Test the four components directly so imported stock skeletons do not fail.
    if (abs(q.w) + abs(q.x) + abs(q.y) + abs(q.z)) <= 1e-12:
        q = Quaternion((1, 0, 0, 0))
    else:
        q.normalize()
    return Matrix.Translation(pos) @ q.to_matrix().to_4x4()


def create_armature_from_skeleton(context, data, name="SanctuaryRig", swap_yz=True):
    entries = sorted(data["bonesHierarchy"], key=lambda e: int(e.get("boneId", 0)))
    if not entries:
        raise ValueError("sanskeleton contains no bones")
    ids = [int(e["boneId"]) for e in entries]
    if ids != list(range(len(entries))):
        raise ValueError("sanskeleton bone IDs must be contiguous from 0")

    # Build exact Sanctuary paths before Blender has a chance to suffix duplicate
    # bone names.  Sanctuary identifies animation curves by hierarchy path, while
    # Blender requires every bone name in an armature to be unique.
    san_paths = []
    for i, entry in enumerate(entries):
        original = str(entry.get("name", f"Bone_{i}"))
        parent_id = int(entry.get("parentBoneId", -1))
        if parent_id >= 0:
            san_paths.append(san_paths[parent_id] + "/" + original)
        else:
            san_paths.append(original)

    globals_san = []
    for entry in entries:
        local = _san_local_matrix(entry)
        parent_id = int(entry.get("parentBoneId", -1))
        global_m = globals_san[parent_id] @ local if parent_id >= 0 else local
        globals_san.append(global_m)

    globals_bl = [san_bone_global_to_blender(m) if swap_yz else m.copy() for m in globals_san]

    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    context.collection.objects.link(arm_obj)
    context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    arm_obj.show_in_front = True

    bpy.ops.object.mode_set(mode="EDIT")
    edit_by_id = {}
    actual_name_by_id = {}
    for i, entry in enumerate(entries):
        requested = str(entry.get("name", f"Bone_{i}"))
        eb = arm_data.edit_bones.new(requested)
        # Blender may turn a duplicate requested name into Name.001; keep the
        # actual Blender name tied to this numeric Sanctuary bone ID.
        actual_name_by_id[i] = eb.name
        child_ids = [j for j, e in enumerate(entries) if int(e.get("parentBoneId", -1)) == i]
        if child_ids:
            distances = [(globals_bl[j].translation - globals_bl[i].translation).length for j in child_ids]
            length = min((d for d in distances if d > 1e-5), default=0.1)
        else:
            parent_id = int(entry.get("parentBoneId", -1))
            if parent_id >= 0:
                length = max((globals_bl[i].translation - globals_bl[parent_id].translation).length * 0.35, 0.05)
            else:
                length = 0.1
        eb.length = max(length, 0.01)
        # A new EditBone has zero length. Blender cannot preserve its intended
        # direction when .matrix is set before a nonzero length is established.
        # Size first, then assign the complete source orientation and position.
        eb.matrix = globals_bl[i]
        edit_by_id[i] = eb

    for i, entry in enumerate(entries):
        parent_id = int(entry.get("parentBoneId", -1))
        if parent_id >= 0:
            edit_by_id[i].parent = edit_by_id[parent_id]
            edit_by_id[i].use_connect = False
    bpy.ops.object.mode_set(mode="OBJECT")

    mapping = {}
    path_to_blender_name = {}
    for i, entry in enumerate(entries):
        actual_name = actual_name_by_id[i]
        b = arm_data.bones.get(actual_name)
        if b is None:
            raise RuntimeError(f"Could not resolve imported bone ID {i}: {actual_name}")
        b["san_bone_id"] = i
        b["san_bind_pose_index"] = int(entry.get("bindPoseIndex", i))
        b["san_original_name"] = str(entry.get("name", actual_name))
        b["san_path"] = san_paths[i]
        mapping[actual_name] = i
        path_to_blender_name[san_paths[i]] = actual_name

        error = max(abs(float(b.matrix_local[r][c] - globals_bl[i][r][c]))
                    for r in range(4) for c in range(4))
        if error > 1e-4:
            raise ValueError(f"Imported rest orientation did not match bone '{actual_name}' ({error:.3g})")

    arm_obj["san_bone_map_json"] = json.dumps(mapping, sort_keys=True)
    arm_obj["san_bone_paths_json"] = json.dumps(path_to_blender_name, sort_keys=True)
    arm_obj["san_original_skeleton"] = json.dumps(data, separators=(",", ":"))
    return arm_obj


def _quat_euler_fields(q):
    # Euler fields appear to be redundant with localRotation in shipped files,
    # but write them for compatibility and readability.
    e = q.to_euler("XYZ")
    radians = []
    degrees = []
    for val in e:
        v = float(val) % (2.0 * math.pi)
        radians.append(v)
        degrees.append(math.degrees(v))
    return (
        {"x": degrees[0], "y": degrees[1], "z": degrees[2]},
        {"x": radians[0], "y": radians[1], "z": radians[2]},
    )


def build_sanskeleton_dict(armature_obj, swap_yz=True):
    mapping, ordered = get_bone_id_map(armature_obj)
    global_san_by_name = {}
    for b in ordered:
        g = b.matrix_local.copy()
        if swap_yz:
            g = blender_bone_global_to_san(g)
        global_san_by_name[b.name] = g

    entries = []
    for b in ordered:
        bid = mapping[b.name]
        parent_id = mapping[b.parent.name] if b.parent else -1
        global_san = global_san_by_name[b.name]
        if b.parent:
            local_san = global_san_by_name[b.parent.name].inverted_safe() @ global_san
        else:
            local_san = global_san

        loc, rot, scale = local_san.decompose()
        rot.normalize()
        # Non-unit rest scale is unsupported by the current skeleton schema.
        if max(abs(float(s) - 1.0) for s in scale) > 1e-4:
            raise ValueError(f"Bone '{b.name}' has non-unit rest scale {tuple(scale)}. Apply/fix armature rest scale before export.")

        bind = global_san.inverted_safe()
        bind_pos, bind_rot, bind_scale = bind.decompose()
        bind_rot.normalize()
        euler_deg, euler_rad = _quat_euler_fields(rot)
        entries.append({
            # Preserve the source Sanctuary name on round-trip. Blender may have
            # suffixed duplicate names solely to satisfy its uniqueness rule.
            "name": str(b.get("san_original_name", b.name)),
            "boneId": bid,
            "bindPoseIndex": bid,
            "parentBoneId": parent_id,
            "boneType": "Root" if parent_id < 0 else "Undefined",
            "localPosition": v_to_dict(loc),
            "boneRotationAxis": "default",
            "localRotation": q_to_dict(rot),
            "localRotationEulerDegrees": euler_deg,
            "localRotationRadians": euler_rad,
            "bindPosePosition": v_to_dict(bind_pos),
            "bindPoseRotation": q_to_dict(bind_rot),
        })
    return {"bonesHierarchy": entries, "turrets": []}


def export_sanskeleton_file(armature_obj, path, swap_yz=True):
    data = build_sanskeleton_dict(armature_obj, swap_yz)
    atomic_overwrite_text(path, json.dumps(data, indent=2), encoding="utf-8")
    return len(data["bonesHierarchy"])
