"""Import already-baked Sanctuary curves without FPS resampling or SLERP.

The only conversion is the fixed coordinate/rest-basis mapping required for
Blender's matrix_basis channels. A linear combination of unweighted Hermite
curves is represented exactly on the union of their existing key times.
"""

import bisect
import base64
import hashlib
import json
import math
import struct
import zlib

from mathutils import Matrix, Quaternion, Vector


def serialize_clip_body(clip, name=None, blueprint_id=None):
    out = bytearray((str(clip.name if name is None else name) + "\0").encode("utf-8"))
    out += struct.pack("<iffii", int(clip.blueprint_id if blueprint_id is None else blueprint_id), float(clip.fps), float(clip.duration), int(clip.frame_count), len(clip.curves))
    for curve in clip.curves:
        out += struct.pack("<i", int(curve.curve_id))
        out += (str(curve.path) + "\0").encode("utf-8")
        out += struct.pack("<ii", int(curve.pre_wrap), int(curve.post_wrap))
        for channel in curve.channels:
            out += struct.pack("<i", len(channel))
            for key in channel:
                out += struct.pack("<6fi", float(key.time), float(key.value), float(key.in_tangent), float(key.out_tangent), float(key.in_weight), float(key.out_weight), int(key.weighted_mode))
    return bytes(out)


def _action_fingerprint(action):
    digest = hashlib.sha256()
    for curve in sorted(action.fcurves, key=lambda item: (item.data_path, item.array_index)):
        if curve.modifiers:
            return ""
        digest.update((curve.data_path + "\0" + curve.extrapolation).encode("utf-8"))
        digest.update(struct.pack("<i?", int(curve.array_index), bool(curve.mute)))
        for key in curve.keyframe_points:
            digest.update(struct.pack("<6f", *key.co, *key.handle_left, *key.handle_right))
            digest.update((key.interpolation + key.handle_left_type + key.handle_right_type).encode("ascii"))
    return digest.hexdigest()


def _rest_fingerprint(armature):
    digest = hashlib.sha256()
    for bone in sorted(armature.data.bones, key=lambda item: item.name):
        digest.update((bone.name + "\0" + (bone.parent.name if bone.parent else "")).encode("utf-8"))
        digest.update(struct.pack("<16f", *[float(bone.matrix_local[row][column]) for row in range(4) for column in range(4)]))
    return digest.hexdigest()


def native_clip_for_export(item, armature, manifest, swap_yz=True):
    """Pass through untouched imported native curves instead of sampling them."""
    action = item.action
    if not getattr(item, "preserve_native_curves", True) or not action.get("san_native_clip_data", ""):
        return None
    if getattr(item, "source_evaluation", "BAKED") == "AUTHORED":
        return None
    if bool(action.get("san_native_swap_yz", True)) != bool(swap_yz):
        return None
    if _action_fingerprint(action) != str(action.get("san_native_action_fingerprint", "")):
        return None
    if _rest_fingerprint(armature) != str(action.get("san_native_rest_fingerprint", "")):
        return None
    if abs(float(item.fps) - float(action.get("san_fps", item.fps))) > 1e-5:
        return None
    native_range = action.get("san_native_action_range", [])
    if len(native_range) != 2 or (int(item.frame_start), int(item.frame_end)) != tuple(int(round(float(value))) for value in native_range):
        return None
    from .sananimation_io import parse_sananimation_bytes
    blob = zlib.decompress(base64.b64decode(str(action["san_native_clip_data"])))
    clip = parse_sananimation_bytes(blob)[0]
    native_paths = {curve.path for curve in clip.curves if curve.path and curve.path != "Armature"}
    if native_paths != {entry["path"] for entry in manifest}:
        return None
    return clip


def _channel_state(keys, time, default):
    """Return value and one-sided derivatives in source seconds."""
    if not keys:
        return float(default), 0.0, 0.0
    times = [float(key.time) for key in keys]
    index = bisect.bisect_left(times, float(time))
    if index < len(keys) and abs(times[index] - float(time)) <= 1e-9:
        key = keys[index]
        # A different component may add a union knot beyond this component's
        # range. Keep constant extrapolation on the outside of its endpoints.
        incoming = float(key.in_tangent) if index else 0.0
        outgoing = float(key.out_tangent) if index + 1 < len(keys) else 0.0
        return float(key.value), incoming, outgoing
    if index == 0:
        return float(keys[0].value), 0.0, 0.0
    if index >= len(keys):
        return float(keys[-1].value), 0.0, 0.0
    left, right = keys[index - 1], keys[index]
    duration = float(right.time - left.time)
    if duration <= 1e-12:
        return float(right.value), 0.0, 0.0
    u = (float(time) - float(left.time)) / duration
    u2 = u * u
    u3 = u2 * u
    value = (
        (2.0 * u3 - 3.0 * u2 + 1.0) * float(left.value)
        + (u3 - 2.0 * u2 + u) * duration * float(left.out_tangent)
        + (-2.0 * u3 + 3.0 * u2) * float(right.value)
        + (u3 - u2) * duration * float(right.in_tangent)
    )
    derivative = (
        (6.0 * u2 - 6.0 * u) * float(left.value)
        + (3.0 * u2 - 4.0 * u + 1.0) * duration * float(left.out_tangent)
        + (-6.0 * u2 + 6.0 * u) * float(right.value)
        + (3.0 * u2 - 2.0 * u) * duration * float(right.in_tangent)
    ) / duration
    return value, derivative, derivative


def _combine_channels(channels, defaults, coefficients, offset=0.0):
    active = [index for index, coefficient in enumerate(coefficients) if abs(float(coefficient)) > 1e-12]
    times = sorted({float(key.time) for index in active for key in channels[index]})
    if not times:
        times = [0.0]
    result = []
    for time in times:
        value = float(offset)
        incoming = 0.0
        outgoing = 0.0
        for index in active:
            coefficient = float(coefficients[index])
            source_value, source_in, source_out = _channel_state(channels[index], time, defaults[index])
            value += coefficient * source_value
            incoming += coefficient * source_in
            outgoing += coefficient * source_out
        result.append((time, value, incoming, outgoing))
    return result


def _write_native_fcurve(action, data_path, index, keys, fps, group_name):
    curve = action.fcurves.new(data_path=data_path, index=index, action_group=str(group_name))
    curve.extrapolation = "CONSTANT"
    curve.keyframe_points.add(len(keys))
    coordinates = []
    for time, value, _incoming, _outgoing in keys:
        coordinates.extend((1.0 + float(time) * fps, float(value)))
    curve.keyframe_points.foreach_set("co", coordinates)
    for key_index, (time, value, incoming, outgoing) in enumerate(keys):
        point = curve.keyframe_points[key_index]
        frame = 1.0 + float(time) * fps
        previous_dt = float(time - keys[key_index - 1][0]) if key_index else (
            float(keys[1][0] - time) if len(keys) > 1 else 1.0 / fps
        )
        next_dt = float(keys[key_index + 1][0] - time) if key_index + 1 < len(keys) else previous_dt
        point.interpolation = "BEZIER"
        point.handle_left_type = "FREE"
        point.handle_right_type = "FREE"
        point.handle_left = (frame - previous_dt * fps / 3.0, float(value) - incoming * previous_dt / 3.0)
        point.handle_right = (frame + next_dt * fps / 3.0, float(value) + outgoing * next_dt / 3.0)
    curve.update()
    return curve


def _quaternion_map(rest_inverse, swap_yz):
    columns = []
    for component in range(4):
        values = [0.0, 0.0, 0.0, 0.0]
        values[component] = 1.0
        if swap_yz:
            values = [values[0], -values[1], -values[3], -values[2]]
        converted = rest_inverse @ Quaternion(values)
        columns.append(tuple(float(value) for value in converted))
    return [[columns[column][row] for column in range(4)] for row in range(4)]


def _validate_native_keys(clip):
    for curve in clip.curves:
        for channel in curve.channels:
            for key in channel:
                if int(key.weighted_mode) != 0:
                    raise ValueError(
                        f"Clip '{clip.name}' contains weighted tangents. Native no-rebake import "
                        "does not silently resample weighted curves; export an unweighted baked clip first."
                    )
                if not all(math.isfinite(float(value)) for value in (key.time, key.value, key.in_tangent, key.out_tangent)):
                    raise ValueError(
                        f"Clip '{clip.name}' contains non-finite/step tangents. Native no-rebake import "
                        "requires finite baked curves and will not silently change their interpolation."
                    )


def import_native_actions(armature, clips, source_label, swap_yz=True):
    from .sananimation_io import (
        _curve_map_for_clip,
        _matrix_defaults,
        _new_action_for_clip,
        _ordered_hierarchy,
        _source_rest_san_matrices,
    )
    from .utils import get_bone_id_map

    _mapping, raw_bones = get_bone_id_map(armature)
    bones = _ordered_hierarchy(raw_bones)
    rest_san = _source_rest_san_matrices(armature, raw_bones, bool(swap_yz))
    axis = Matrix(((1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 1.0, 0.0))) if swap_yz else Matrix.Identity(3)
    scale_indices = (0, 2, 1) if swap_yz else (0, 1, 2)
    actions = []
    action_map = {}

    # Validate all clips before creating Actions, so unsupported curves cannot
    # leave an import half-created or replace an existing Action first.
    for clip in clips:
        _validate_native_keys(clip)
    for clip in clips:
        fps = float(clip.fps) if float(clip.fps) > 1e-8 else 30.0
        action = _new_action_for_clip(armature, clip, source_label, clip.frame_count)
        # Clip lists store Action names, not ID references. Retain even clips
        # that have never been selected/assigned when the .blend is saved.
        action.use_fake_user = True
        action["san_frame_start"] = 1.0
        action["san_frame_end"] = 1.0 + float(clip.duration) * fps
        action["san_preview_fps"] = fps
        action["san_bake_mode"] = "native-keys-no-resampling-v146"
        action["san_native_key_import"] = True
        action["san_native_swap_yz"] = bool(swap_yz)
        curve_map, unmatched, ambiguous = _curve_map_for_clip(armature, clip)
        imported_key_count = 0

        for bone in bones:
            pose_bone = armature.pose.bones.get(bone.name)
            if pose_bone is None:
                continue
            if not bone.use_local_location or not bone.use_inherit_rotation or bone.inherit_scale != "FULL":
                raise ValueError(
                    f"Bone '{bone.name}' uses nonstandard inheritance. Native no-rebake import "
                    "requires ordinary local-location/full-inheritance bones."
                )
            rest_bl = bone.parent.matrix_local.inverted_safe() @ bone.matrix_local if bone.parent else bone.matrix_local.copy()
            rest_position, rest_rotation, rest_scale = rest_bl.decompose()
            if max(abs(float(value) - 1.0) for value in rest_scale) > 1e-4:
                raise ValueError(f"Bone '{bone.name}' has non-unit rest scale; native import will not rebake it")
            rest_rotation.normalize()
            inverse_rotation = rest_rotation.inverted()
            inverse_rotation_matrix = inverse_rotation.to_matrix()
            position_map = inverse_rotation_matrix @ axis
            position_offset = -(inverse_rotation_matrix @ rest_position)
            rotation_map = _quaternion_map(inverse_rotation, bool(swap_yz))
            defaults = _matrix_defaults(rest_san[bone.name])
            source_curve = curve_map.get(bone.name)
            channels = source_curve.channels if source_curve else [[] for _ in range(10)]

            for component in range(3):
                keys = _combine_channels(channels[:3], defaults[:3], position_map[component], position_offset[component])
                _write_native_fcurve(action, pose_bone.path_from_id("location"), component, keys, fps, bone.name)
                imported_key_count += len(keys)
            for component in range(4):
                keys = _combine_channels(channels[3:7], defaults[3:7], rotation_map[component])
                _write_native_fcurve(action, pose_bone.path_from_id("rotation_quaternion"), component, keys, fps, bone.name)
                imported_key_count += len(keys)
            for component, source_component in enumerate(scale_indices):
                coefficients = [0.0, 0.0, 0.0]
                coefficients[source_component] = 1.0
                keys = _combine_channels(channels[7:10], defaults[7:10], coefficients)
                _write_native_fcurve(action, pose_bone.path_from_id("scale"), component, keys, fps, bone.name)
                imported_key_count += len(keys)

        action["san_native_import_key_count"] = imported_key_count
        native_blob = struct.pack("<i", 1) + serialize_clip_body(clip)
        action["san_native_clip_data"] = base64.b64encode(zlib.compress(native_blob, 9)).decode("ascii")
        action["san_native_action_range"] = list(action.frame_range)
        action["san_native_action_fingerprint"] = _action_fingerprint(action)
        action["san_native_rest_fingerprint"] = _rest_fingerprint(armature)
        actions.append(action)
        action_map[clip.name] = action.name
        print(
            f"[Sanctuary Toolkit v1.4.6] Imported '{clip.name}' at native {fps:g} FPS: "
            f"{imported_key_count} coordinate-mapped keys, no frame resampling, no SLERP, "
            f"unmatched={len(unmatched)}, ambiguous={len(ambiguous)}"
        )

    armature["san_animation_actions_json"] = json.dumps([action.name for action in actions])
    armature["san_animation_action_map_json"] = json.dumps(action_map, separators=(",", ":"))
    armature["san_animation_source"] = str(source_label)
    return actions
