import re
import struct
from dataclasses import dataclass
from pathlib import Path

import bpy
import bmesh
import numpy as np
from mathutils import Matrix, Vector

from .utils import (
    atomic_overwrite_bytes,
    blender_bone_global_to_san,
    blender_to_san_vector,
    console,
    get_bone_id_map,
    san_to_blender_vector,
)


@dataclass
class SanModelData:
    name: str
    vertices: np.ndarray
    normals: np.ndarray
    tangents: np.ndarray
    uv1: np.ndarray
    uv2: np.ndarray
    uv3: np.ndarray
    colors: np.ndarray
    indices: np.ndarray
    bindposes: np.ndarray


_COMPONENTS = (3, 3, 4, 2, 2, 2, 4)


def parse_sanmodel_bytes(blob: bytes) -> SanModelData:
    try:
        end = blob.index(b"\0")
    except ValueError as exc:
        raise ValueError("sanmodel is missing its null-terminated model name") from exc
    name = blob[:end].decode("utf-8", "replace")
    off = end + 1

    arrays = []
    for comps in _COMPONENTS:
        if off + 4 > len(blob):
            raise ValueError("sanmodel ended while reading an array count")
        count = struct.unpack_from("<i", blob, off)[0]
        off += 4
        if count < 0:
            raise ValueError("sanmodel contains a negative array count")
        byte_count = count * comps * 4
        if off + byte_count > len(blob):
            raise ValueError("sanmodel ended inside a float array")
        if count:
            arr = np.frombuffer(blob, dtype="<f4", count=count * comps, offset=off).copy().reshape(count, comps)
        else:
            arr = np.empty((0, comps), dtype=np.float32)
        arrays.append(arr)
        off += byte_count

    if off + 4 > len(blob):
        raise ValueError("sanmodel ended before triangle indices")
    index_count = struct.unpack_from("<i", blob, off)[0]
    off += 4
    if index_count < 0 or index_count % 3:
        raise ValueError("sanmodel triangle index count is invalid")
    index_bytes = index_count * 4
    if off + index_bytes > len(blob):
        raise ValueError("sanmodel ended inside triangle indices")
    indices = np.frombuffer(blob, dtype="<i4", count=index_count, offset=off).copy().reshape(-1, 3)
    off += index_bytes

    if off + 4 > len(blob):
        raise ValueError("sanmodel ended before bind poses")
    bind_count = struct.unpack_from("<i", blob, off)[0]
    off += 4
    if bind_count < 0:
        raise ValueError("sanmodel bind-pose count is invalid")
    bind_bytes = bind_count * 16 * 4
    if off + bind_bytes > len(blob):
        raise ValueError("sanmodel ended inside bind poses")
    if bind_count:
        raw = np.frombuffer(blob, dtype="<f4", count=bind_count * 16, offset=off).copy().reshape(bind_count, 16)
        bindposes = np.stack([r.reshape((4, 4), order="F") for r in raw], axis=0)
    else:
        bindposes = np.empty((0, 4, 4), dtype=np.float32)
    off += bind_bytes

    if off != len(blob):
        raise ValueError(f"sanmodel has {len(blob) - off} unexpected trailing bytes")

    return SanModelData(name, *arrays, indices, bindposes)


def parse_sanmodel_file(path):
    return parse_sanmodel_bytes(Path(path).read_bytes())


def _create_uv_layer(mesh, name, per_vertex_uv, mirror_v=False):
    if len(per_vertex_uv) == 0:
        return
    layer = mesh.uv_layers.new(name=name)
    # RNA assignment per loop dominates import time on large units. Gather the
    # vertex indices once and upload the complete UV buffer in one operation.
    vertex_indices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", vertex_indices)
    values = np.asarray(per_vertex_uv, dtype=np.float32)[vertex_indices].copy()
    if mirror_v:
        values[:, 1] = 1.0 - values[:, 1]
    layer.data.foreach_set("uv", values.ravel())


def _apply_custom_normals(mesh, normals, swap_yz=True):
    if len(normals) != len(mesh.vertices):
        return
    values = np.asarray(normals, dtype=np.float32)
    if swap_yz:
        values = values[:, (0, 2, 1)]
    try:
        mesh.normals_split_custom_set_from_vertices(values.tolist())
    except Exception as exc:
        console(f"Custom normals could not be applied ({exc}); Blender will recalculate them.")


def _generic_armature_from_bindposes(context, model: SanModelData, swap_yz=True):
    if len(model.bindposes) == 0:
        return None
    arm_data = bpy.data.armatures.new(model.name + "Rig")
    arm_obj = bpy.data.objects.new(model.name + "Rig", arm_data)
    context.collection.objects.link(arm_obj)
    context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    arm_obj.show_in_front = True

    bpy.ops.object.mode_set(mode="EDIT")
    for i, bp_np in enumerate(model.bindposes):
        bind = Matrix(bp_np.tolist())
        global_san = bind.inverted_safe()
        global_bl = global_san
        if swap_yz:
            # Apply the Sanctuary->Blender basis conversion; import locally to avoid a dependency cycle.
            from .utils import san_bone_global_to_blender
            global_bl = san_bone_global_to_blender(global_san)
        eb = arm_data.edit_bones.new(f"Bone_{i}")
        eb.length = 0.1
        eb.matrix = global_bl
    bpy.ops.object.mode_set(mode="OBJECT")
    for i, b in enumerate(arm_data.bones):
        b["san_bone_id"] = i
    return arm_obj


def import_sanmodel(context, model: SanModelData, skeleton_obj=None, swap_yz=True, mirror_uv=False):
    verts = np.asarray(model.vertices, dtype=np.float32)
    faces = np.asarray(model.indices, dtype=np.int32)
    if swap_yz:
        verts = verts[:, (0, 2, 1)]
    if swap_yz:
        # The axis swap reflects handedness. Undo the serialized winding flip
        # just as export does, so front faces agree with the imported normals.
        faces = faces[:, (0, 2, 1)]
    mesh = bpy.data.meshes.new(model.name + "Mesh")
    mesh.from_pydata(verts.tolist(), [], faces.tolist())
    mesh.update(calc_edges=True)

    obj = bpy.data.objects.new(model.name, mesh)
    context.collection.objects.link(obj)
    context.view_layer.objects.active = obj
    obj.select_set(True)

    _create_uv_layer(mesh, "UV1Map", model.uv1, mirror_uv)
    _create_uv_layer(mesh, "UV3Map", model.uv3, mirror_uv)
    _apply_custom_normals(mesh, model.normals, swap_yz)

    if skeleton_obj is None and len(model.bindposes):
        skeleton_obj = _generic_armature_from_bindposes(context, model, swap_yz)

    if skeleton_obj is not None and len(model.uv2) == len(model.vertices):
        mapping, ordered = get_bone_id_map(skeleton_obj)
        id_to_name = {bid: name for name, bid in mapping.items()}
        bone_ids = np.rint(np.asarray(model.uv2)[:, 0]).astype(np.int32)
        for bid in range(len(ordered)):
            name = id_to_name.get(bid, f"Bone_{bid}")
            vg = obj.vertex_groups.new(name=name)
            indices = np.flatnonzero(bone_ids == bid)
            if len(indices):
                vg.add(indices.tolist(), 1.0, "REPLACE")
        mod = obj.modifiers.new(name="Armature", type="ARMATURE")
        mod.object = skeleton_obj
        obj.parent = skeleton_obj
        obj.matrix_parent_inverse = skeleton_obj.matrix_world.inverted_safe()

    obj["san_model_name"] = model.name
    obj["san_bindpose_count"] = int(len(model.bindposes))
    return obj


def _store_export_corner_normals(mesh):
    """Keep evaluated normals as vectors, independent of Blender's split fans.

    Custom normals are internally encoded relative to the surrounding geometry.
    Splitting all edges changes that basis; carrying the encoded layer through
    BMesh alone rotates the normals. A temporary CORNER vector attribute follows
    each loop through splitting/triangulation without changing the directions.
    This operates only on the evaluated export mesh, never the user's mesh.
    """
    if not mesh.loops:
        return None
    # Blender 4.0 requires explicit split-normal calculation. Newer versions
    # compute corner_normals on demand and removed calc_normals_split().
    if hasattr(mesh, "calc_normals_split"):
        mesh.calc_normals_split()
    normals = np.empty(len(mesh.loops) * 3, dtype=np.float32)
    if hasattr(mesh, "corner_normals"):
        mesh.corner_normals.foreach_get("vector", normals)
    else:
        mesh.loops.foreach_get("normal", normals)
    attribute = mesh.attributes.new("_san_export_corner_normal", "FLOAT_VECTOR", "CORNER")
    attribute.data.foreach_set("vector", normals)
    # Save the allocated name: Blender suffixes it if the mesh already has an
    # attribute with this name. RNA references become invalid after bm.to_mesh.
    return attribute.name


def _restore_export_corner_normals(mesh, attribute_name):
    if attribute_name is None:
        return None
    attribute = mesh.attributes.get(attribute_name)
    if attribute is None or len(attribute.data) != len(mesh.loops):
        raise RuntimeError("Export could not preserve the mesh's corner normals")
    normals = np.empty(len(mesh.loops) * 3, dtype=np.float32)
    attribute.data.foreach_get("vector", normals)
    mesh.attributes.remove(attribute)
    if hasattr(mesh, "use_auto_smooth"):
        mesh.use_auto_smooth = True
    mesh.normals_split_custom_set(normals.reshape((-1, 3)).tolist())
    # Keep the full-precision vectors for serialization. Re-encoding custom
    # normals against very thin triangles can lose precision even after the
    # correct directions were supplied to Blender.
    return normals.reshape((-1, 3))


def _prepare_evaluated_mesh(context, obj, armature):
    """Create an evaluated triangulated mesh and keep it alive for extraction.

    Blender's evaluated mesh belongs to the depsgraph.  Older toolkit builds
    restored the armature pose and called ``view_layer.update()`` *before* the
    caller finished reading that mesh.  The depsgraph update invalidated the
    temporary StructRNA and produced ``StructRNA of type Mesh has been removed``.

    The caller now owns cleanup/restoration and must call ``to_mesh_clear()``
    before restoring the pose and updating the depsgraph.
    """
    old_pose_position = None
    obj_eval = None
    mesh = None
    if armature is not None:
        old_pose_position = armature.data.pose_position
        armature.data.pose_position = "REST"
        context.view_layer.update()
    try:
        depsgraph = context.evaluated_depsgraph_get()
        obj_eval = obj.evaluated_get(depsgraph)
        mesh = obj_eval.to_mesh()
        normal_attribute = _store_export_corner_normals(mesh)
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bmesh.ops.split_edges(bm, edges=list(bm.edges))
            bmesh.ops.triangulate(bm, faces=list(bm.faces))
            bm.to_mesh(mesh)
        finally:
            bm.free()
        corner_normals = _restore_export_corner_normals(mesh, normal_attribute)
        mesh.calc_loop_triangles()
        if mesh.uv_layers:
            uv_name = mesh.uv_layers.get("UV1Map")
            try:
                mesh.calc_tangents(uvmap=uv_name.name if uv_name else mesh.uv_layers[0].name)
            except Exception:
                pass
        return obj_eval, mesh, old_pose_position, corner_normals
    except Exception:
        if obj_eval is not None:
            try:
                obj_eval.to_mesh_clear()
            except Exception:
                pass
        if armature is not None and old_pose_position is not None:
            armature.data.pose_position = old_pose_position
            context.view_layer.update()
        raise


def _prepare_corner_export_mesh(context, obj, armature):
    """Evaluate once and serialize triangle corners without a BMesh edge split.

    Sanctuary stores one normal/UV per vertex, so seams require duplicated
    vertices.  The older exporter achieved that by physically splitting every
    evaluated edge with BMesh.  Meshy-derived unit meshes can make that step
    pathologically slow.  Emitting one output vertex per triangle corner is
    equivalent for the file format and keeps export time linear.
    """
    old_pose_position = None
    obj_eval = None
    if armature is not None:
        old_pose_position = armature.data.pose_position
        armature.data.pose_position = "REST"
        context.view_layer.update()
    try:
        depsgraph = context.evaluated_depsgraph_get()
        obj_eval = obj.evaluated_get(depsgraph)
        mesh = obj_eval.to_mesh()
        normal_attribute = _store_export_corner_normals(mesh)
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bmesh.ops.triangulate(bm, faces=list(bm.faces))
            bm.to_mesh(mesh)
        finally:
            bm.free()
        _restore_export_corner_normals(mesh, normal_attribute)
        mesh.calc_loop_triangles()
        if mesh.uv_layers:
            uv_name = mesh.uv_layers.get("UV1Map")
            try:
                mesh.calc_tangents(uvmap=uv_name.name if uv_name else mesh.uv_layers[0].name)
            except Exception:
                pass
        return obj_eval, mesh, old_pose_position
    except Exception:
        if obj_eval is not None:
            try:
                obj_eval.to_mesh_clear()
            except Exception:
                pass
        if armature is not None and old_pose_position is not None:
            armature.data.pose_position = old_pose_position
            context.view_layer.update()
        raise

def _mesh_to_armature_matrix(obj, armature):
    if armature is None:
        return Matrix.Identity(4)
    return armature.matrix_world.inverted_safe() @ obj.matrix_world


def _transform_position(co, mesh_to_arm, swap_yz):
    v = mesh_to_arm @ co
    return blender_to_san_vector(v) if swap_yz else tuple(v)


def _transform_direction(v, mesh_to_arm, swap_yz):
    nmat = mesh_to_arm.to_3x3().inverted_safe().transposed()
    out = nmat @ v
    if out.length_squared:
        out.normalize()
    return blender_to_san_vector(out) if swap_yz else tuple(out)


def _extract_per_vertex_normals(mesh, mesh_to_arm, swap_yz, corner_normals=None):
    out = np.zeros((len(mesh.vertices), 3), dtype="<f4")
    seen = np.zeros(len(mesh.vertices), dtype=bool)
    for loop in mesh.loops:
        vi = loop.vertex_index
        if not seen[vi]:
            normal = Vector(corner_normals[loop.index]) if corner_normals is not None else loop.normal
            out[vi] = _transform_direction(normal, mesh_to_arm, swap_yz)
            seen[vi] = True
    return out


def _extract_per_vertex_tangents(mesh, mesh_to_arm, swap_yz, mirror_uv, normals=None):
    out = np.zeros((len(mesh.vertices), 4), dtype="<f4")
    if not mesh.uv_layers:
        return out
    seen = np.zeros(len(mesh.vertices), dtype=bool)
    for loop in mesh.loops:
        vi = loop.vertex_index
        if seen[vi]:
            continue
        try:
            t = Vector(loop.tangent)
            t = mesh_to_arm.to_3x3() @ t
            if t.length_squared:
                t.normalize()
            xyz = blender_to_san_vector(t) if swap_yz else tuple(t)
            if normals is not None:
                # Tangents were computed using Blender's encoded normals.
                # Re-orthogonalize against the full-precision exported normal.
                tangent = Vector(xyz)
                normal = Vector(normals[vi])
                tangent -= normal * tangent.dot(normal)
                if tangent.length_squared:
                    tangent.normalize()
                xyz = tuple(tangent)
            # Y/Z swap is a reflection (determinant -1). Preserve tangent
            # handedness unless an explicit UV mirror cancels the reflection.
            sign = float(loop.bitangent_sign if (mirror_uv == swap_yz) else -loop.bitangent_sign)
            out[vi] = (*xyz, sign)
        except Exception:
            pass
        seen[vi] = True
    return out


def _extract_uv(mesh, layer_name, fallback_index, mirror_uv):
    layer = mesh.uv_layers.get(layer_name)
    if layer is None and len(mesh.uv_layers) > fallback_index:
        layer = mesh.uv_layers[fallback_index]
    if layer is None:
        return np.empty((0, 2), dtype="<f4")
    out = np.zeros((len(mesh.vertices), 2), dtype="<f4")
    seen = np.zeros(len(mesh.vertices), dtype=bool)
    for loop in mesh.loops:
        vi = loop.vertex_index
        if seen[vi]:
            continue
        uv = layer.data[loop.index].uv
        out[vi] = (float(uv.x), float(1.0 - uv.y if mirror_uv else uv.y))
        seen[vi] = True
    return out


def _extract_colors(obj, mesh):
    # Keep this conservative: sanmodel colors are optional and most Sanctuary
    # unit meshes use textures. Preserve a simple material color when present.
    if not obj.active_material:
        return np.empty((0, 4), dtype="<f4")
    rgba = tuple(float(x) for x in obj.active_material.diffuse_color)
    return np.tile(np.array(rgba, dtype="<f4"), (len(mesh.vertices), 1))


def _root_bone_id(mapping):
    if "Root" in mapping:
        return mapping["Root"]
    return min(mapping.values()) if mapping else 0


def _extract_bone_ids(obj, mesh, armature, mapping):
    if armature is None or not mapping:
        return np.empty((0, 2), dtype="<f4"), 0, 0
    root_id = _root_bone_id(mapping)
    uv2 = np.zeros((len(mesh.vertices), 2), dtype="<f4")
    multi_weight = 0
    unknown = 0

    for v in mesh.vertices:
        weighted = [(g.group, float(g.weight)) for g in v.groups if g.weight > 1e-8]
        if len(weighted) > 1:
            multi_weight += 1
        if not weighted:
            uv2[v.index, 0] = float(root_id)
            continue
        group_index, _weight = max(weighted, key=lambda x: x[1])
        try:
            group_name = obj.vertex_groups[group_index].name
        except Exception:
            group_name = ""

        if group_name in mapping:
            bid = mapping[group_name]
        else:
            # Round-trip safety for a manually imported sanmodel without a
            # companion .sanskeleton: Bone_12 still means numeric bone ID 12.
            m = re.fullmatch(r"Bone_(\d+)", group_name)
            if m and int(m.group(1)) in mapping.values():
                bid = int(m.group(1))
            else:
                bid = root_id
                unknown += 1
        uv2[v.index, 0] = float(bid)
    return uv2, multi_weight, unknown


def _bindposes_from_armature(armature, ordered_bones, swap_yz):
    if armature is None:
        return np.empty((0, 4, 4), dtype="<f4")
    mats = []
    for bone in ordered_bones:
        global_bl = bone.matrix_local.copy()
        global_san = blender_bone_global_to_san(global_bl) if swap_yz else global_bl
        bind = global_san.inverted_safe()
        mats.append(np.array(bind, dtype=np.float32))
    return np.stack(mats, axis=0) if mats else np.empty((0, 4, 4), dtype="<f4")


def _write_float_array(out, arr, components):
    arr = np.asarray(arr, dtype="<f4")
    if arr.size == 0:
        out += struct.pack("<i", 0)
        return
    arr = arr.reshape((-1, components))
    out += struct.pack("<i", len(arr))
    out += arr.astype("<f4", copy=False).tobytes(order="C")


def build_sanmodel_bytes(context, obj, armature, model_name, swap_yz=True, mirror_uv=False):
    mapping, ordered = get_bone_id_map(armature) if armature else ({}, [])
    obj_eval, mesh, old_pose_position = _prepare_corner_export_mesh(context, obj, armature)
    try:
        mesh_to_arm = _mesh_to_armature_matrix(obj, armature)
        corner_loops = [li for tri in mesh.loop_triangles for li in tri.loops]
        corner_index = np.asarray(corner_loops, dtype=np.int32)
        loop_vertex_ids = np.empty(len(mesh.loops), dtype=np.int32)
        mesh.loops.foreach_get("vertex_index", loop_vertex_ids)
        source_vertices = loop_vertex_ids[corner_index]

        source_positions = np.empty((len(mesh.vertices), 3), dtype=np.float64)
        mesh.vertices.foreach_get("co", source_positions.reshape(-1))
        matrix = np.asarray(mesh_to_arm, dtype=np.float64)
        source_positions = source_positions @ matrix[:3, :3].T + matrix[:3, 3]
        vertices = source_positions[source_vertices]
        if swap_yz:
            vertices = vertices[:, [0, 2, 1]]
        vertices = np.asarray(vertices, dtype="<f4")

        all_normals = np.empty((len(mesh.loops), 3), dtype=np.float64)
        mesh.corner_normals.foreach_get("vector", all_normals.reshape(-1))
        normal_matrix = np.asarray(mesh_to_arm.to_3x3().inverted_safe().transposed(), dtype=np.float64)
        normals = all_normals[corner_index] @ normal_matrix.T
        normal_lengths = np.linalg.norm(normals, axis=1)
        valid = normal_lengths > 1e-20
        normals[valid] /= normal_lengths[valid, None]
        if swap_yz:
            normals = normals[:, [0, 2, 1]]
        normals = np.asarray(normals, dtype="<f4")

        tangents = np.zeros((len(corner_loops), 4), dtype=np.float64)
        if mesh.uv_layers:
            all_tangents = np.empty((len(mesh.loops), 3), dtype=np.float64)
            all_signs = np.empty(len(mesh.loops), dtype=np.float64)
            mesh.loops.foreach_get("tangent", all_tangents.reshape(-1))
            mesh.loops.foreach_get("bitangent_sign", all_signs)
            tangent_matrix = np.asarray(mesh_to_arm.to_3x3(), dtype=np.float64)
            xyz = all_tangents[corner_index] @ tangent_matrix.T
            xyz_lengths = np.linalg.norm(xyz, axis=1)
            valid = xyz_lengths > 1e-20
            xyz[valid] /= xyz_lengths[valid, None]
            if swap_yz:
                xyz = xyz[:, [0, 2, 1]]
            xyz -= normals * np.sum(xyz * normals, axis=1)[:, None]
            xyz_lengths = np.linalg.norm(xyz, axis=1)
            valid = xyz_lengths > 1e-20
            xyz[valid] /= xyz_lengths[valid, None]
            tangents[:, :3] = xyz
            signs = all_signs[corner_index]
            tangents[:, 3] = signs if mirror_uv == swap_yz else -signs
        tangents = np.asarray(tangents, dtype="<f4")

        def corner_uv(layer_name, fallback_index):
            layer = mesh.uv_layers.get(layer_name)
            if layer is None and len(mesh.uv_layers) > fallback_index:
                layer = mesh.uv_layers[fallback_index]
            if layer is None:
                return np.empty((0, 2), dtype="<f4")
            all_uv = np.empty((len(mesh.loops), 2), dtype=np.float32)
            layer.data.foreach_get("uv", all_uv.reshape(-1))
            result = all_uv[corner_index]
            if mirror_uv:
                result[:, 1] = 1.0 - result[:, 1]
            return np.asarray(result, dtype="<f4")

        uv1 = corner_uv("UV1Map", 0)
        uv3 = corner_uv("UV3Map", 1)
        source_uv2, multi_weight, unknown_groups = _extract_bone_ids(obj, mesh, armature, mapping)
        uv2 = source_uv2[source_vertices] if len(source_uv2) else source_uv2
        source_colors = _extract_colors(obj, mesh)
        colors = source_colors[source_vertices] if len(source_colors) else source_colors
        indices = np.arange(len(corner_loops), dtype="<i4").reshape((-1, 3))
        if swap_yz:
            # Y/Z swap reflects handedness, so preserve front faces by swapping
            # the second and third index of every triangle.
            indices = indices[:, [0, 2, 1]]
        bindposes = _bindposes_from_armature(armature, ordered, swap_yz)

        out = bytearray((model_name + "\0").encode("utf-8"))
        _write_float_array(out, vertices, 3)
        _write_float_array(out, normals, 3)
        _write_float_array(out, tangents, 4)
        _write_float_array(out, uv1, 2)
        _write_float_array(out, uv2, 2)
        _write_float_array(out, uv3, 2)
        _write_float_array(out, colors, 4)
        out += struct.pack("<i", int(indices.size))
        out += indices.astype("<i4", copy=False).reshape(-1).tobytes(order="C")
        out += struct.pack("<i", len(bindposes))
        for bind in bindposes:
            # Sanctuary serializes Matrix4x4 column-by-column.
            out += np.asarray(bind, dtype="<f4").flatten(order="F").tobytes()

        stats = {
            "vertices": len(vertices),
            "triangles": len(indices),
            "bones": len(bindposes),
            "multi_weight_vertices": multi_weight,
            "unknown_vertex_groups": unknown_groups,
            "corner_vertex_export": True,
        }
        return bytes(out), stats
    finally:
        try:
            obj_eval.to_mesh_clear()
        except Exception:
            pass
        if armature is not None and old_pose_position is not None:
            armature.data.pose_position = old_pose_position
            try:
                context.view_layer.update()
            except Exception:
                pass


def export_sanmodel_file(context, obj, armature, path, model_name, swap_yz=True, mirror_uv=False):
    blob, stats = build_sanmodel_bytes(context, obj, armature, model_name, swap_yz, mirror_uv)
    atomic_overwrite_bytes(path, blob)
    return stats


def validate_model_against_armature(model: SanModelData, armature, swap_yz=True):
    mapping, ordered = get_bone_id_map(armature)
    report = {
        "model_bindposes": len(model.bindposes),
        "armature_bones": len(ordered),
        "bone_id_min": None,
        "bone_id_max": None,
        "bind_identity_max_error": None,
    }
    if len(model.uv2):
        ids = np.rint(model.uv2[:, 0]).astype(np.int32)
        report["bone_id_min"] = int(ids.min())
        report["bone_id_max"] = int(ids.max())
    if len(model.bindposes) == len(ordered):
        max_err = 0.0
        for i, b in enumerate(ordered):
            g = b.matrix_local.copy()
            if swap_yz:
                g = blender_bone_global_to_san(g)
            bp = Matrix(model.bindposes[i].tolist())
            err = np.max(np.abs(np.array(g @ bp) - np.eye(4)))
            max_err = max(max_err, float(err))
        report["bind_identity_max_error"] = max_err
    return report
