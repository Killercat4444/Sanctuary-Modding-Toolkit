import json
import time
import math
import struct
from dataclasses import dataclass
from pathlib import Path

import bpy
from mathutils import Matrix, Quaternion

from .utils import (
    blender_to_san_matrix,
    san_to_blender_matrix,
    blender_bone_global_to_san,
    san_bone_global_to_blender,
    full_bone_path,
    get_bone_id_map,
)

CHANNEL_COUNT = 10
WRAP_CLAMP_FOREVER = 8
_DEFAULT_CHANNELS = (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)


# -----------------------------------------------------------------------------
# Export
# -----------------------------------------------------------------------------

def _pack_cstr(text):
    return text.encode("utf-8") + b"\0"


def _pack_key(time, value, in_tangent, out_tangent):
    return struct.pack(
        "<6fi",
        float(time),
        float(value),
        float(in_tangent),
        float(out_tangent),
        1.0 / 3.0,
        1.0 / 3.0,
        0,
    )


def _linear_tangents(times, values, loop):
    """Return per-key in/out slopes that make every key interval exactly linear.

    Sanctuary/Unity stores scalar AnimationCurves for position/quaternion/scale
    components. Central-difference tangents can overshoot between dense sampled
    keys, which shows up as small limb twitches/jitter and is especially visible
    on quaternion components. Matching each segment's in/out slopes makes the
    Hermite segment exactly linear while preserving every sampled pose.
    """
    n = len(values)
    if n <= 1:
        return [0.0] * n, [0.0] * n

    slopes = []
    for i in range(n - 1):
        dt = float(times[i + 1] - times[i])
        slopes.append(0.0 if abs(dt) < 1e-12 else float(values[i + 1] - values[i]) / dt)

    ins = [0.0] * n
    outs = [0.0] * n
    for i in range(n):
        if i == 0:
            ins[i] = slopes[-1] if loop and len(slopes) > 1 else slopes[0]
            outs[i] = slopes[0]
        elif i == n - 1:
            ins[i] = slopes[-1]
            outs[i] = slopes[0] if loop else slopes[-1]
        else:
            ins[i] = slopes[i - 1]
            outs[i] = slopes[i]
    return ins, outs


def _channel_bytes(times, values, loop):
    if not values:
        return struct.pack("<i", 0)
    if max(values) - min(values) < 1e-8:
        if len(times) == 1:
            pairs = [(times[0], values[0])]
        else:
            pairs = [(times[0], values[0]), (times[-1], values[-1])]
        out = bytearray(struct.pack("<i", len(pairs)))
        for t, v in pairs:
            out += _pack_key(t, v, 0.0, 0.0)
        return bytes(out)

    in_tangents, out_tangents = _linear_tangents(times, values, loop)
    out = bytearray(struct.pack("<i", len(values)))
    for t, v, it, ot in zip(times, values, in_tangents, out_tangents):
        out += _pack_key(t, v, it, ot)
    return bytes(out)


def _pose_local_matrix(pb, exported_names):
    if pb.parent and pb.parent.name in exported_names:
        return pb.parent.matrix.inverted_safe() @ pb.matrix
    return pb.matrix.copy()


def _sample_action(context, armature_obj, action, ordered_bones, start, end, fps, loop, swap_yz=True):
    """Sample an evaluated Blender Action into Sanctuary bone-local transforms.

    Fresh/custom rigs are exported by converting their *evaluated global pose*
    directly into Sanctuary coordinates, then rebuilding parent-local transforms.
    Their .sanskeleton is generated from the same Blender rest globals, so this is
    the exact rigid transform mapping and does not introduce shear.

    Imported Sanctuary rigs are a different case: Blender may reconstruct an edit
    bone with a different roll/display basis than the original game skeleton.
    Those rigs therefore keep the deformation-delta path relative to the stored
    original Sanctuary rest pose.

    Evaluated PoseBone.matrix includes IK/constraints, so authoring controls are
    baked into the exported deform-bone motion.
    """
    if end < start:
        start, end = end, start
    frames = list(range(int(start), int(end) + 1))
    if not frames:
        frames = [int(start)]
    frame_count = len(frames)
    # Sanctuary's shipped looping clips use the header frameCount as the number
    # of authored intervals and can carry a closure key at `duration`.  For a
    # 16-frame Blender loop at 24 FPS this means 16/24 = 0.6667 s, not 15/24.
    # Non-looping one-shots still span first->last as (N-1)/FPS.
    duration = (frame_count / float(fps)) if loop else max(0.0, (frame_count - 1) / float(fps))
    sample_times = [i / float(fps) for i in range(frame_count)]

    hierarchy = _ordered_hierarchy(ordered_bones)
    imported_rig = bool(armature_obj.get("san_original_skeleton"))

    rest_local_san = _source_rest_san_matrices(armature_obj, ordered_bones, swap_yz)
    rest_global_san = {}
    for b in hierarchy:
        local = rest_local_san[b.name]
        if b.parent and b.parent.name in rest_global_san:
            rest_global_san[b.name] = rest_global_san[b.parent.name] @ local
        else:
            rest_global_san[b.name] = local.copy()

    samples = {b.name: [] for b in ordered_bones}
    scene = context.scene
    max_roundtrip_error = 0.0
    max_local_rebuild_error = 0.0

    for frame in frames:
        scene.frame_set(frame)
        context.view_layer.update()

        animated_global_san = {}
        for b in hierarchy:
            pb = armature_obj.pose.bones.get(b.name)
            if pb is None:
                raise ValueError(f"Pose bone '{b.name}' is missing")
            pose_global_bl = pb.matrix.copy()

            if imported_rig:
                rest_global_bl = b.matrix_local.copy()
                delta_bl = pose_global_bl @ rest_global_bl.inverted_safe()
                delta_san = blender_to_san_matrix(delta_bl) if swap_yz else delta_bl
                animated_global_san[b.name] = delta_san @ rest_global_san[b.name]

                generated_delta_san = (
                    animated_global_san[b.name] @ rest_global_san[b.name].inverted_safe()
                )
                generated_delta_bl = (
                    san_to_blender_matrix(generated_delta_san)
                    if swap_yz else generated_delta_san
                )
                expected_bl = delta_bl
            else:
                # Custom rig: skeleton/model export uses this exact same global
                # basis conversion. Preserve the posed rigid matrix directly.
                animated_global_san[b.name] = (
                    blender_to_san_matrix(pose_global_bl)
                    if swap_yz else pose_global_bl.copy()
                )
                expected_bl = pose_global_bl
                generated_bl = (
                    san_to_blender_matrix(animated_global_san[b.name])
                    if swap_yz else animated_global_san[b.name]
                )
                try:
                    err = max(
                        abs(float(generated_bl[r][c] - expected_bl[r][c]))
                        for r in range(4) for c in range(4)
                    )
                    max_roundtrip_error = max(max_roundtrip_error, err)
                except Exception:
                    pass
                continue

            try:
                err = max(
                    abs(float(generated_delta_bl[r][c] - expected_bl[r][c]))
                    for r in range(4) for c in range(4)
                )
                max_roundtrip_error = max(max_roundtrip_error, err)
            except Exception:
                pass

        for b in ordered_bones:
            g = animated_global_san[b.name]
            if b.parent and b.parent.name in animated_global_san:
                local_san = animated_global_san[b.parent.name].inverted_safe() @ g
            else:
                local_san = g.copy()

            loc, rot, scale = local_san.decompose()
            rot.normalize()

            # Sanctuary stores TRS, not shear. Rebuild the matrix now and reject
            # a conversion that would silently lose a large amount of motion.
            rebuilt = (
                Matrix.Translation(loc)
                @ rot.to_matrix().to_4x4()
                @ Matrix.Diagonal((float(scale.x), float(scale.y), float(scale.z), 1.0))
            )
            try:
                err = max(
                    abs(float(rebuilt[r][c] - local_san[r][c]))
                    for r in range(4) for c in range(4)
                )
                max_local_rebuild_error = max(max_local_rebuild_error, err)
            except Exception:
                pass

            samples[b.name].append((loc.copy(), rot.copy(), scale.copy()))

    if max_roundtrip_error > 1e-4:
        raise ValueError(
            f"Animation '{action.name}' failed coordinate round-trip validation "
            f"(max matrix error {max_roundtrip_error:.3e})"
        )
    if max_local_rebuild_error > 1e-3:
        raise ValueError(
            f"Animation '{action.name}' cannot be represented cleanly as Sanctuary TRS "
            f"(local shear/rebuild error {max_local_rebuild_error:.3e}). "
            "Apply armature/object scale and check constraints before exporting."
        )

    print(
        f"[Sanctuary Toolkit v1.4.6] Export sample {action.name!r}: "
        f"basis round-trip={max_roundtrip_error:.3e}, "
        f"TRS rebuild={max_local_rebuild_error:.3e}, "
        f"mode={'imported-delta' if imported_rig else 'custom-direct'}"
    )

    # Quaternion sign continuity prevents component curves from interpolating
    # through q -> -q even though both represent the same orientation.
    for b in ordered_bones:
        track = samples[b.name]
        prev = None
        for i, (loc, q, scale) in enumerate(track):
            if prev is not None and prev.dot(q) < 0.0:
                q = Quaternion((-q.w, -q.x, -q.y, -q.z))
                track[i] = (loc, q, scale)
            prev = q

    # Preserve every authored frame, then add a separate closure key one frame
    # later for looping clips.  This avoids both bad historical behaviors:
    # overwriting the artist's last frame, and snapping from the last frame back
    # to frame one with no interpolation interval.  Header frameCount remains the
    # authored count, matching stock Sanctuary loop timing.
    if loop and frame_count > 0:
        sample_times.append(float(duration))
        for b in ordered_bones:
            track = samples[b.name]
            loc0, q0, scale0 = track[0]
            q_close = q0.copy()
            if track and track[-1][1].dot(q_close) < 0.0:
                q_close = Quaternion((-q_close.w, -q_close.x, -q_close.y, -q_close.z))
            track.append((loc0.copy(), q_close, scale0.copy()))

    return frame_count, duration, sample_times, samples


def _curve_bytes(curve_id, path, channels, times, loop):
    out = bytearray()
    out += struct.pack("<i", int(curve_id))
    out += _pack_cstr(path)
    out += struct.pack("<ii", WRAP_CLAMP_FOREVER, WRAP_CLAMP_FOREVER)
    for values in channels:
        out += _channel_bytes(times, values, loop)
    return bytes(out)


def _identity_armature_curve(duration):
    times = [0.0] if duration <= 0 else [0.0, duration]
    channels = [
        [0.0] * len(times), [0.0] * len(times), [0.0] * len(times),
        [1.0] * len(times), [0.0] * len(times), [0.0] * len(times), [0.0] * len(times),
        [1.0] * len(times), [1.0] * len(times), [1.0] * len(times),
    ]
    return _curve_bytes(0, "Armature", channels, times, False)


def _bone_channels(track):
    return [
        [float(v[0].x) for v in track],
        [float(v[0].y) for v in track],
        [float(v[0].z) for v in track],
        [float(v[1].w) for v in track],
        [float(v[1].x) for v in track],
        [float(v[1].y) for v in track],
        [float(v[1].z) for v in track],
        [float(v[2].x) for v in track],
        [float(v[2].y) for v in track],
        [float(v[2].z) for v in track],
    ]


def _v141_reset_source_pose(context, arm):
    """Make Action evaluation deterministic by clearing stale unkeyed pose state.

    Assigning a Blender Action does not reset channels/bones that the Action does
    not key.  IK targets/feet could therefore inherit the pose of whichever
    armature/Action was active when the .blend was saved.  Reset to rest first;
    the Action then supplies every transform it actually owns.
    """
    ad = arm.animation_data_create()
    ad.action = None
    for pb in arm.pose.bones:
        try:
            pb.matrix_basis.identity()
        except Exception:
            pb.location = (0.0, 0.0, 0.0)
            if pb.rotation_mode == "QUATERNION":
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            else:
                pb.rotation_euler = (0.0, 0.0, 0.0)
            pb.scale = (1.0, 1.0, 1.0)
    context.view_layer.update()


def build_sananimation_bytes(context, armature_obj, items, swap_yz=True):
    mapping, ordered = get_bone_id_map(armature_obj)
    enabled = [item for item in items if item.enabled and item.action]
    if not enabled:
        raise ValueError("No enabled animation Actions were selected")
    names = [item.clip_name.strip() or item.action.name for item in enabled]
    if len(set(names)) != len(names):
        raise ValueError("Animation clip names must be unique")

    scene = context.scene
    old_frame = scene.frame_current
    old_pose_position = armature_obj.data.pose_position
    anim_data = armature_obj.animation_data_create()
    old_action = anim_data.action
    nla_states = [(track, track.mute) for track in anim_data.nla_tracks]

    out = bytearray(struct.pack("<i", len(enabled)))
    try:
        armature_obj.data.pose_position = "POSE"
        for track, _ in nla_states:
            track.mute = True

        for blueprint_id, item in enumerate(enabled):
            anim_data.action = item.action
            context.view_layer.update()
            clip_name = item.clip_name.strip() or item.action.name
            fps = float(item.fps)
            if fps <= 0:
                raise ValueError(f"Clip '{clip_name}' has invalid FPS")
            frame_count, duration, times, samples = _sample_action(
                context, armature_obj, item.action, ordered,
                item.frame_start, item.frame_end, fps, bool(item.loop), swap_yz,
            )

            out += _pack_cstr(clip_name)
            out += struct.pack("<i", blueprint_id)
            out += struct.pack("<ffii", fps, duration, frame_count, len(ordered) + 1)
            out += _identity_armature_curve(duration)
            for b in ordered:
                bid = mapping[b.name]
                path = full_bone_path(b)
                out += _curve_bytes(bid + 1, path, _bone_channels(samples[b.name]), times, bool(item.loop))
    finally:
        anim_data.action = old_action
        for track, state in nla_states:
            track.mute = state
        armature_obj.data.pose_position = old_pose_position
        scene.frame_set(old_frame)
        context.view_layer.update()

    return bytes(out), {"clips": len(enabled), "bones": len(ordered), "clip_names": names}


def export_sananimation_file(context, armature_obj, items, path, swap_yz=True):
    blob, stats = build_sananimation_bytes(context, armature_obj, items, swap_yz)
    Path(path).write_bytes(blob)
    return stats


# -----------------------------------------------------------------------------
# Import / preview
# -----------------------------------------------------------------------------

@dataclass
class SanKey:
    time: float
    value: float
    in_tangent: float
    out_tangent: float
    in_weight: float
    out_weight: float
    weighted_mode: int


@dataclass
class SanCurve:
    curve_id: int
    path: str
    pre_wrap: int
    post_wrap: int
    channels: list


@dataclass
class SanClip:
    name: str
    blueprint_id: int
    fps: float
    duration: float
    frame_count: int
    curves: list


def _read_cstr(blob, off):
    end = blob.find(b"\0", off)
    if end < 0:
        raise ValueError("sananimation contains an unterminated string")
    return blob[off:end].decode("utf-8", "replace"), end + 1


def parse_sananimation_bytes(blob):
    off = 0
    if len(blob) < 4:
        raise ValueError("sananimation is too short")
    clip_count = struct.unpack_from("<i", blob, off)[0]
    off += 4
    if clip_count < 0 or clip_count > 10000:
        raise ValueError("sananimation clip count is invalid")
    clips = []
    for _ in range(clip_count):
        name, off = _read_cstr(blob, off)
        if off + 20 > len(blob):
            raise ValueError("sananimation ended in a clip header")
        blueprint_id = struct.unpack_from("<i", blob, off)[0]; off += 4
        fps, duration, frame_count, curve_count = struct.unpack_from("<ffii", blob, off); off += 16
        if curve_count < 0 or curve_count > 100000:
            raise ValueError(f"Clip '{name}' has an invalid curve count")
        curves = []
        for _curve in range(curve_count):
            if off + 4 > len(blob):
                raise ValueError("sananimation ended before a curve")
            curve_id = struct.unpack_from("<i", blob, off)[0]; off += 4
            path, off = _read_cstr(blob, off)
            if off + 8 > len(blob):
                raise ValueError("sananimation ended in curve wrap modes")
            pre_wrap, post_wrap = struct.unpack_from("<ii", blob, off); off += 8
            channels = []
            for _ch in range(CHANNEL_COUNT):
                if off + 4 > len(blob):
                    raise ValueError("sananimation ended before channel key count")
                key_count = struct.unpack_from("<i", blob, off)[0]; off += 4
                if key_count < 0 or key_count > 10000000:
                    raise ValueError("sananimation channel key count is invalid")
                keys = []
                need = key_count * 28
                if off + need > len(blob):
                    raise ValueError("sananimation ended inside keyframes")
                for _key in range(key_count):
                    t, v, it, ot, iw, ow, wm = struct.unpack_from("<6fi", blob, off)
                    off += 28
                    keys.append(SanKey(t, v, it, ot, iw, ow, wm))
                channels.append(keys)
            curves.append(SanCurve(curve_id, path, pre_wrap, post_wrap, channels))
        clips.append(SanClip(name, blueprint_id, fps, duration, frame_count, curves))
    if off != len(blob):
        # Some game revisions may append metadata. Do not reject valid clips,
        # but leave a trace for debugging.
        print(f"[Sanctuary Toolkit] sananimation has {len(blob)-off} trailing bytes")
    return clips


def parse_sananimation_file(path):
    return parse_sananimation_bytes(Path(path).read_bytes())


def parse_sananimation_header(blob):
    clips = parse_sananimation_bytes(blob)
    return {"clip_count": len(clips), "size": len(blob), "clips": [c.name for c in clips]}


def _eval_channel(keys, t, default):
    if not keys:
        return float(default)
    if len(keys) == 1 or t <= keys[0].time:
        return float(keys[0].value)
    if t >= keys[-1].time:
        return float(keys[-1].value)
    lo = 0
    hi = len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid].time <= t:
            lo = mid
        else:
            hi = mid
    a = keys[lo]
    b = keys[hi]
    dt = float(b.time - a.time)
    if abs(dt) < 1e-12:
        return float(b.value)
    u = max(0.0, min(1.0, (t - a.time) / dt))
    # Unity AnimationCurve's unweighted case is cubic Hermite with stored in/out
    # slopes. This accurately reproduces the toolkit's own exported clips and is
    # a close viewport preview for shipped clips that use ordinary tangents.
    if not all(math.isfinite(x) for x in (a.out_tangent, b.in_tangent)):
        return float(a.value if u < 1.0 else b.value)
    h00 = 2*u*u*u - 3*u*u + 1
    h10 = u*u*u - 2*u*u + u
    h01 = -2*u*u*u + 3*u*u
    h11 = u*u*u - u*u
    return float(h00*a.value + h10*(a.out_tangent*dt) + h01*b.value + h11*(b.in_tangent*dt))


def _rest_local_matrix(bone):
    if bone.parent:
        return bone.parent.matrix_local.inverted_safe() @ bone.matrix_local
    return bone.matrix_local.copy()


def _rest_local_san_matrix(bone, swap_yz=True):
    """Return this Blender bone's rest-local matrix in Sanctuary coordinates."""
    local_bl = _rest_local_matrix(bone)
    return blender_to_san_matrix(local_bl) if swap_yz else local_bl.copy()


def _matrix_defaults(local_san):
    loc, rot, scale = local_san.decompose()
    rot.normalize()
    return (
        float(loc.x), float(loc.y), float(loc.z),
        float(rot.w), float(rot.x), float(rot.y), float(rot.z),
        float(scale.x), float(scale.y), float(scale.z),
    )


def _sample_times_for_clip(clip):
    """Bake the Sanctuary curve on its real time grid, including duration.

    Shipped clips commonly store a key exactly at duration even when their
    frameCount field describes the authored intervals.  Sampling 0..duration
    inclusive at clip FPS avoids dropping that final closure/hold key.
    """
    fps = float(clip.fps) if float(clip.fps) > 1e-8 else 30.0
    duration = max(0.0, float(clip.duration))
    if int(clip.frame_count) <= 1 or duration <= 1e-9:
        return [0.0]
    intervals = max(1, int(round(duration * fps)))
    times = [i / fps for i in range(intervals + 1)]
    # Preserve the serialized endpoint even when duration*fps is fractionally off.
    times[-1] = duration
    return times


def _new_action_for_clip(armature_obj, clip, source_label, baked_frames=None):
    action_name = f"SAN::{armature_obj.name}::{clip.name}"
    old = bpy.data.actions.get(action_name)
    if old:
        bpy.data.actions.remove(old)
    action = bpy.data.actions.new(action_name)
    action["san_clip_name"] = clip.name
    action["san_source_animation"] = str(source_label)
    action["san_armature_name"] = armature_obj.name
    action["san_fps"] = float(clip.fps)
    action["san_duration"] = float(clip.duration)
    action["san_serialized_frame_count"] = int(clip.frame_count)
    baked = max(1, int(baked_frames if baked_frames is not None else clip.frame_count))
    action["san_frame_count"] = baked
    action["san_frame_start"] = 0
    action["san_frame_end"] = baked - 1
    return action


def _create_curve(action, data_path, index, values):
    fc = action.fcurves.new(data_path=data_path, index=index)
    fc.keyframe_points.add(len(values))
    flat = []
    for frame, value in values:
        flat.extend((float(frame), float(value)))
    try:
        fc.keyframe_points.foreach_set("co", flat)
    except Exception:
        for kp, (frame, value) in zip(fc.keyframe_points, values):
            kp.co = (frame, value)
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"
    fc.update()
    return fc


def _animation_path_maps(armature_obj):
    """Return exact Sanctuary path -> Blender bone plus safe name fallbacks."""
    exact = {}
    by_original_name = {}
    by_blender_name = {}
    for bone in armature_obj.data.bones:
        path = str(bone.get("san_path", "")) or full_bone_path(bone)
        exact[path] = bone
        original = str(bone.get("san_original_name", "")) or path.rsplit("/", 1)[-1]
        by_original_name.setdefault(original, []).append(bone)
        by_blender_name[bone.name] = bone
    return exact, by_original_name, by_blender_name


def _curve_map_for_clip(armature_obj, clip):
    exact, by_original_name, by_blender_name = _animation_path_maps(armature_obj)
    curve_by_bone = {}
    unmatched = []
    ambiguous = []
    for curve in clip.curves:
        if not curve.path or curve.path == "Armature":
            continue
        bone = exact.get(curve.path)
        if bone is None:
            leaf = curve.path.rsplit("/", 1)[-1]
            candidates = by_original_name.get(leaf, [])
            if len(candidates) == 1:
                bone = candidates[0]
            elif leaf in by_blender_name:
                bone = by_blender_name[leaf]
            elif len(candidates) > 1:
                ambiguous.append(curve.path)
        if bone is None:
            unmatched.append(curve.path)
            continue
        curve_by_bone[bone.name] = curve
    return curve_by_bone, unmatched, ambiguous


def _desired_pose_globals(ordered, local_bl_by_name):
    globals_by_name = {}
    for bone in ordered:
        local = local_bl_by_name[bone.name]
        if bone.parent:
            globals_by_name[bone.name] = globals_by_name[bone.parent.name] @ local
        else:
            globals_by_name[bone.name] = local
    return globals_by_name


def _basis_from_pose_global(bone, desired_global, desired_parent_global=None):
    """Use Blender's own bone-space conversion instead of approximating it.

    Bone.convert_local_to_pose accounts for inherit-scale/local-location rules
    and is the API Blender documents for assigning an entire pose recursively.
    """
    if bone.parent:
        return bone.convert_local_to_pose(
            desired_global,
            bone.matrix_local,
            parent_matrix=desired_parent_global,
            parent_matrix_local=bone.parent.matrix_local,
            invert=True,
        )
    return bone.convert_local_to_pose(desired_global, bone.matrix_local, invert=True)


def _solve_basis_from_pose_globals(armature_obj, ordered, desired_globals):
    """Return exact matrix_basis values for desired armature-space matrices.

    This follows Blender's documented recursive Bone.convert_local_to_pose()
    pattern. Crucially, parent_matrix is the *desired parent pose matrix* for
    the same sample, not whatever the dependency graph happened to evaluate
    while we were assigning PoseBone.matrix one bone at a time.
    """
    id_rank = {b.name: i for i, b in enumerate(ordered)}
    ordered_names = set(id_rank)
    roots = sorted(
        [b for b in ordered if b.parent is None or b.parent.name not in ordered_names],
        key=lambda b: id_rank[b.name],
    )
    solve_order = []

    def visit(b):
        solve_order.append(b)
        children = [c for c in b.children if c.name in ordered_names]
        children.sort(key=lambda c: id_rank[c.name])
        for c in children:
            visit(c)

    for root in roots:
        visit(root)
    seen = {b.name for b in solve_order}
    solve_order.extend(b for b in ordered if b.name not in seen)

    basis_by_name = {}
    max_roundtrip_error = 0.0
    for bone in solve_order:
        target = desired_globals[bone.name]
        if bone.parent and bone.parent.name in desired_globals:
            parent_pose = desired_globals[bone.parent.name]
            basis = bone.convert_local_to_pose(
                target,
                bone.matrix_local,
                parent_matrix=parent_pose,
                parent_matrix_local=bone.parent.matrix_local,
                invert=True,
            )
            roundtrip = bone.convert_local_to_pose(
                basis,
                bone.matrix_local,
                parent_matrix=parent_pose,
                parent_matrix_local=bone.parent.matrix_local,
            )
        else:
            basis = bone.convert_local_to_pose(
                target,
                bone.matrix_local,
                invert=True,
            )
            roundtrip = bone.convert_local_to_pose(
                basis,
                bone.matrix_local,
            )
        basis_by_name[bone.name] = basis
        try:
            err = max(abs(float(roundtrip[r][c] - target[r][c])) for r in range(4) for c in range(4))
            max_roundtrip_error = max(max_roundtrip_error, err)
        except Exception:
            pass
    return basis_by_name, max_roundtrip_error


def _source_rest_san_matrices(armature_obj, ordered, swap_yz=True):
    """Return exact Sanctuary rest-local matrices for every imported bone.

    Imported Sanctuary armatures keep the original JSON skeleton on the object.
    Prefer that source data rather than reconstructing the rest locals from
    Blender's EditBone matrices, because Blender is free to normalize bone roll /
    display-axis details when an edit bone is created.  Animation curves are
    authored against the Sanctuary skeleton locals, so those exact source locals
    are the correct reference pose for animation deltas.
    """
    result = {}
    raw = armature_obj.get("san_original_skeleton")
    if raw:
        try:
            data = json.loads(raw)
            entries = {int(e.get("boneId", -1)): e for e in data.get("bonesHierarchy", [])}
            for bone in ordered:
                bid = int(bone.get("san_bone_id", -1))
                entry = entries.get(bid)
                if entry is None:
                    continue
                p = entry.get("localPosition", {})
                r = entry.get("localRotation", {})
                loc = (float(p.get("x", 0.0)), float(p.get("y", 0.0)), float(p.get("z", 0.0)))
                q = Quaternion((
                    float(r.get("w", 1.0)),
                    float(r.get("x", 0.0)),
                    float(r.get("y", 0.0)),
                    float(r.get("z", 0.0)),
                ))
                qn2 = float(q.w*q.w + q.x*q.x + q.y*q.y + q.z*q.z)
                if qn2 < 1e-12 or not math.isfinite(qn2):
                    q = Quaternion((1.0, 0.0, 0.0, 0.0))
                else:
                    q.normalize()
                result[bone.name] = Matrix.Translation(loc) @ q.to_matrix().to_4x4()
        except Exception as exc:
            print(f"[Sanctuary Toolkit] Could not use original skeleton rest transforms: {exc}")

    # Fresh/custom armatures do not have source JSON. Their current Blender rest
    # matrices are authoritative. Convert *global* bone matrices with the same
    # coordinate conversion used by skeleton export, then rebuild Sanctuary locals.
    missing = [b for b in ordered if b.name not in result]
    if missing:
        globals_san = {}
        for bone in _ordered_hierarchy(ordered):
            g_bl = bone.matrix_local.copy()
            globals_san[bone.name] = blender_bone_global_to_san(g_bl) if swap_yz else g_bl
        for bone in ordered:
            if bone.name in result:
                continue
            g = globals_san[bone.name]
            if bone.parent and bone.parent.name in globals_san:
                result[bone.name] = globals_san[bone.parent.name].inverted_safe() @ g
            else:
                result[bone.name] = g.copy()
    return result


def clips_to_blender_actions(armature_obj, clips, source_label="", swap_yz=True):
    """Bake Sanctuary local transforms as deltas from the Sanctuary rest pose.

    A .sananimation curve stores the bone's *absolute local transform relative to
    its Sanctuary parent*.  Blender Actions, however, animate matrix_basis: the
    change from the bone's rest local transform.  Therefore the stable mapping is

        deltaSAN = inverse(restLocalSAN) @ animatedLocalSAN
        matrix_basis = SAN->Blender(deltaSAN)

    This has an important invariant: a Sanctuary Rest clip that equals the
    skeleton rest locals becomes identity matrix_basis on every bone.  It also
    avoids solving absolute Blender pose matrices through EditBone roll/display
    conventions, which was the source of detached limbs in toolkit v1.2.4-1.2.6.
    """
    if not armature_obj or armature_obj.type != "ARMATURE":
        raise ValueError("Select/import an armature before loading Sanctuary animations")

    _mapping, ordered = get_bone_id_map(armature_obj)
    if not ordered:
        raise ValueError("The selected armature contains no bones")

    rest_san = _source_rest_san_matrices(armature_obj, ordered, swap_yz)
    rest_defaults = {name: _matrix_defaults(mat) for name, mat in rest_san.items()}

    for pb in armature_obj.pose.bones:
        pb.rotation_mode = "QUATERNION"

    actions = []
    for clip in clips:
        curve_by_bone, unmatched, ambiguous = _curve_map_for_clip(armature_obj, clip)
        times = _sample_times_for_clip(clip)
        action = _new_action_for_clip(armature_obj, clip, source_label, len(times))

        values_by_bone = {
            b.name: {
                "loc": [[], [], []],
                "rot": [[], [], [], []],
                "scale": [[], [], []],
            }
            for b in ordered
        }
        previous_quat = {b.name: None for b in ordered}
        extreme = []
        max_rest_delta = 0.0

        for frame, t in enumerate(times):
            for bone in ordered:
                curve = curve_by_bone.get(bone.name)
                defaults = rest_defaults[bone.name]

                if curve is None:
                    anim_local_san = rest_san[bone.name].copy()
                else:
                    vals = [_eval_channel(curve.channels[i], t, defaults[i]) for i in range(CHANNEL_COUNT)]
                    loc = (vals[0], vals[1], vals[2])
                    q = Quaternion((vals[3], vals[4], vals[5], vals[6]))
                    qn2 = float(q.w*q.w + q.x*q.x + q.y*q.y + q.z*q.z)
                    if qn2 < 1e-12 or not math.isfinite(qn2):
                        q = Quaternion((defaults[3], defaults[4], defaults[5], defaults[6]))
                    else:
                        q.normalize()
                    scale = (vals[7], vals[8], vals[9])
                    anim_local_san = (
                        Matrix.Translation(loc)
                        @ q.to_matrix().to_4x4()
                        @ Matrix.Diagonal((float(scale[0]), float(scale[1]), float(scale[2]), 1.0))
                    )

                # The key fix: Sanctuary curves are absolute LOCAL transforms,
                # while Blender F-curves need the delta from the bone's rest local.
                delta_san = rest_san[bone.name].inverted_safe() @ anim_local_san
                basis = san_to_blender_matrix(delta_san) if swap_yz else delta_san

                b_loc, b_rot, b_scale = basis.decompose()
                bn2 = float(b_rot.w*b_rot.w + b_rot.x*b_rot.x + b_rot.y*b_rot.y + b_rot.z*b_rot.z)
                if bn2 < 1e-12 or not math.isfinite(bn2):
                    b_rot = Quaternion((1.0, 0.0, 0.0, 0.0))
                else:
                    b_rot.normalize()
                prev = previous_quat[bone.name]
                if prev is not None and prev.dot(b_rot) < 0.0:
                    b_rot = Quaternion((-b_rot.w, -b_rot.x, -b_rot.y, -b_rot.z))
                previous_quat[bone.name] = b_rot.copy()

                if clip.name.lower() == "rest":
                    try:
                        err = max(abs(float(basis[r][c] - (1.0 if r == c else 0.0))) for r in range(4) for c in range(4))
                        max_rest_delta = max(max_rest_delta, err)
                    except Exception:
                        pass

                packed = (*b_loc, b_rot.w, b_rot.x, b_rot.y, b_rot.z, *b_scale)
                if (not all(math.isfinite(float(v)) for v in packed)
                        or max(abs(float(v)) for v in b_loc) > 1e4
                        or max(abs(float(v)) for v in b_scale) > 1e3):
                    extreme.append((frame, bone.name))

                out = values_by_bone[bone.name]
                for i in range(3):
                    out["loc"][i].append((frame, b_loc[i]))
                    out["scale"][i].append((frame, b_scale[i]))
                out["rot"][0].append((frame, b_rot.w))
                out["rot"][1].append((frame, b_rot.x))
                out["rot"][2].append((frame, b_rot.y))
                out["rot"][3].append((frame, b_rot.z))

        for bone in ordered:
            pb = armature_obj.pose.bones.get(bone.name)
            if pb is None:
                continue
            out = values_by_bone[bone.name]
            for i in range(3):
                _create_curve(action, pb.path_from_id("location"), i, out["loc"][i])
            for i in range(4):
                _create_curve(action, pb.path_from_id("rotation_quaternion"), i, out["rot"][i])
            for i in range(3):
                _create_curve(action, pb.path_from_id("scale"), i, out["scale"][i])

        msg = (
            f"[Sanctuary Toolkit] Imported clip {clip.name!r}: "
            f"{len(curve_by_bone)}/{len(ordered)} animated bone paths matched, "
            f"{len(times)} baked preview frames"
        )
        if clip.name.lower() == "rest":
            msg += f"; rest->identity max error={max_rest_delta:.3e}"
        print(msg)
        if unmatched:
            print(f"[Sanctuary Toolkit]   Unmatched animation paths ({len(unmatched)}): " + ", ".join(unmatched[:20]))
        if ambiguous:
            print(f"[Sanctuary Toolkit]   Ambiguous duplicate-name paths ({len(ambiguous)}): " + ", ".join(ambiguous[:20]))
        if extreme:
            print(f"[Sanctuary Toolkit] WARNING: {len(extreme)} extreme/non-finite imported delta samples; first: {extreme[:10]}")
        actions.append(action)

    armature_obj["san_animation_actions_json"] = json.dumps([a.name for a in actions])
    armature_obj["san_animation_source"] = str(source_label)
    return actions

def import_sananimation_bytes(armature_obj, blob, source_label="<memory>", swap_yz=True):
    clips = parse_sananimation_bytes(blob)
    actions = clips_to_blender_actions(armature_obj, clips, source_label, swap_yz)
    return clips, actions


def import_sananimation_file(armature_obj, path, swap_yz=True):
    path = Path(path)
    return import_sananimation_bytes(armature_obj, path.read_bytes(), str(path), swap_yz)

# -----------------------------------------------------------------------------
# v1.3 direct Sanctuary pose preview
# -----------------------------------------------------------------------------
# Imported .sananimation clips are previewed directly from their serialized
# Sanctuary bone-local transforms.  We intentionally do NOT convert them into
# Blender matrix_basis F-curves for playback: Blender bone roll/rest-space rules
# can make a mathematically simple local-delta Action diverge from the exact
# armature-space pose Sanctuary requested.  The frame handler reconstructs the
# Sanctuary hierarchy and writes final PoseBone.matrix values instead.

_PREVIEW_CLIP_CACHE = {}
_ACTIVE_PREVIEW = None
_PREVIEW_TIMER_INTERVAL = 1.0 / 60.0


def _preview_cache_key(armature_obj, create=True):
    key = armature_obj.get("san_preview_cache_key") if armature_obj else None
    if not key and create and armature_obj:
        import uuid
        key = uuid.uuid4().hex
        armature_obj["san_preview_cache_key"] = key
    return str(key) if key else ""


def _store_preview_clips(armature_obj, clips, source_label, swap_yz):
    key = _preview_cache_key(armature_obj, True)
    _PREVIEW_CLIP_CACHE[key] = {
        "armature_name": armature_obj.name,
        "clips": {clip.name: clip for clip in clips},
        "swap_yz": bool(swap_yz),
        "source": str(source_label),
    }
    metadata = [
        {
            "name": clip.name,
            "fps": float(clip.fps),
            "duration": float(clip.duration),
            "frame_count": int(clip.frame_count),
        }
        for clip in clips
    ]
    armature_obj["san_animation_clips_json"] = json.dumps(metadata, separators=(",", ":"))
    armature_obj["san_animation_actions_json"] = "[]"
    armature_obj["san_animation_source"] = str(source_label)
    return clips


def get_cached_preview_clip(armature_obj, clip_name):
    if not armature_obj:
        return None
    key = _preview_cache_key(armature_obj, False)
    record = _PREVIEW_CLIP_CACHE.get(key)
    if not record:
        return None
    return record.get("clips", {}).get(str(clip_name))


def _ordered_hierarchy(ordered):
    rank = {b.name: i for i, b in enumerate(ordered)}
    names = set(rank)
    roots = [b for b in ordered if b.parent is None or b.parent.name not in names]
    roots.sort(key=lambda b: rank[b.name])
    out = []
    seen = set()

    def visit(b):
        if b.name in seen:
            return
        seen.add(b.name)
        out.append(b)
        children = [c for c in b.children if c.name in names]
        children.sort(key=lambda c: rank[c.name])
        for child in children:
            visit(child)

    for root in roots:
        visit(root)
    for bone in ordered:
        visit(bone)
    return out


def _curve_local_san(curve, t, rest_matrix):
    defaults = _matrix_defaults(rest_matrix)
    if curve is None:
        return rest_matrix.copy()
    vals = [_eval_channel(curve.channels[i], t, defaults[i]) for i in range(CHANNEL_COUNT)]
    q = Quaternion((vals[3], vals[4], vals[5], vals[6]))
    qn2 = float(q.w*q.w + q.x*q.x + q.y*q.y + q.z*q.z)
    if qn2 < 1e-12 or not math.isfinite(qn2):
        q = Quaternion((defaults[3], defaults[4], defaults[5], defaults[6]))
    else:
        q.normalize()
    sx = float(vals[7]) if math.isfinite(float(vals[7])) else 1.0
    sy = float(vals[8]) if math.isfinite(float(vals[8])) else 1.0
    sz = float(vals[9]) if math.isfinite(float(vals[9])) else 1.0
    return (
        Matrix.Translation((float(vals[0]), float(vals[1]), float(vals[2])))
        @ q.to_matrix().to_4x4()
        @ Matrix.Diagonal((sx, sy, sz, 1.0))
    )


def _target_pose_globals(armature_obj, clip, t, swap_yz):
    """Preview Sanctuary animation by preserving its skinning deformation."""
    _mapping, ordered_raw = get_bone_id_map(armature_obj)
    ordered = _ordered_hierarchy(ordered_raw)
    rest_local_san = _source_rest_san_matrices(armature_obj, ordered_raw, swap_yz)
    curve_by_bone, unmatched, ambiguous = _curve_map_for_clip(armature_obj, clip)

    rest_global_san = {}
    anim_global_san = {}
    targets_bl = {}
    for bone in ordered:
        rest_local = rest_local_san[bone.name]
        if bone.parent and bone.parent.name in rest_global_san:
            rglobal = rest_global_san[bone.parent.name] @ rest_local
        else:
            rglobal = rest_local.copy()
        rest_global_san[bone.name] = rglobal

        anim_local = _curve_local_san(curve_by_bone.get(bone.name), t, rest_local)
        if bone.parent and bone.parent.name in anim_global_san:
            aglobal = anim_global_san[bone.parent.name] @ anim_local
        else:
            aglobal = anim_local
        anim_global_san[bone.name] = aglobal

        delta_san = aglobal @ rglobal.inverted_safe()
        delta_bl = san_to_blender_matrix(delta_san) if swap_yz else delta_san
        targets_bl[bone.name] = delta_bl @ bone.matrix_local

    return ordered, targets_bl, unmatched, ambiguous


def apply_direct_preview_pose(armature_obj, clip, t, swap_yz=True):
    """Apply one .sananimation sample as final armature-space matrices."""
    if not armature_obj or armature_obj.type != "ARMATURE":
        return 0.0
    t = max(0.0, min(float(clip.duration), float(t)))
    ordered, targets, _unmatched, _ambiguous = _target_pose_globals(
        armature_obj, clip, t, bool(swap_yz)
    )
    armature_obj.data.pose_position = "POSE"
    for bone in ordered:
        pb = armature_obj.pose.bones.get(bone.name)
        if pb is not None:
            pb.matrix = targets[bone.name]
    armature_obj.update_tag(refresh={"OBJECT"})
    return 0.0


def _find_armature_for_active_preview(state):
    if not state:
        return None
    name = state.get("armature_name", "")
    arm = bpy.data.objects.get(name)
    key = state.get("cache_key", "")
    if arm and _preview_cache_key(arm, False) == key:
        return arm
    for obj in bpy.data.objects:
        if obj.type == "ARMATURE" and _preview_cache_key(obj, False) == key:
            state["armature_name"] = obj.name
            return obj
    return None


def _tag_preview_redraw():
    try:
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in {"VIEW_3D", "PROPERTIES", "DOPESHEET_EDITOR", "TIMELINE"}:
                    area.tag_redraw()
    except Exception:
        pass


def _direct_preview_timer():
    """Clock imported animation preview independently of Blender timeline playback.

    Previous builds relied on screen.animation_play + frame_change_post. On some
    Blender 4.0 layouts that advanced the timeline but did not reliably drive the
    handler, leaving the clip frozen on a single pose. A main-thread app timer is
    window/context independent and samples the serialized clip continuously.
    """
    state = _ACTIVE_PREVIEW
    if not state:
        return None
    arm = _find_armature_for_active_preview(state)
    if arm is None:
        return None
    clip = get_cached_preview_clip(arm, state.get("clip_name", ""))
    if clip is None:
        return None

    duration = max(float(clip.duration), 1e-6)
    elapsed = max(0.0, time.perf_counter() - float(state.get("started_at", time.perf_counter())))
    t = elapsed % duration if state.get("loop", True) else min(elapsed, duration)
    apply_direct_preview_pose(arm, clip, t, bool(state.get("swap_yz", True)))

    try:
        bpy.context.view_layer.update()
    except Exception:
        pass
    _tag_preview_redraw()

    if not state.get("loop", True) and elapsed >= duration:
        return None
    return _PREVIEW_TIMER_INTERVAL


def begin_direct_preview(context, armature_obj, clip_name):
    global _ACTIVE_PREVIEW
    key = _preview_cache_key(armature_obj, False)
    record = _PREVIEW_CLIP_CACHE.get(key)
    if not record:
        raise ValueError("This clip is not in the live preview cache. Reload its .sananimation file.")
    clip = record.get("clips", {}).get(str(clip_name))
    if clip is None:
        raise ValueError(f"Animation clip {clip_name!r} is not loaded for this armature")

    # Stop any previous toolkit preview first, but keep this rig's current pose
    # until the first new clip sample is assigned below.
    if _ACTIVE_PREVIEW:
        stop_direct_preview(context, reset_pose=False)

    anim_data = armature_obj.animation_data_create()
    old_action_name = anim_data.action.name if anim_data.action else ""
    nla_state = [(track.name, bool(track.mute)) for track in anim_data.nla_tracks]
    anim_data.action = None
    for track in anim_data.nla_tracks:
        track.mute = True
    for pb in armature_obj.pose.bones:
        pb.matrix_basis.identity()

    _ACTIVE_PREVIEW = {
        "cache_key": key,
        "armature_name": armature_obj.name,
        "clip_name": clip.name,
        "swap_yz": bool(record.get("swap_yz", True)),
        "old_action_name": old_action_name,
        "nla_state": nla_state,
        "started_at": time.perf_counter(),
        "loop": True,
    }

    apply_direct_preview_pose(armature_obj, clip, 0.0, bool(record.get("swap_yz", True)))
    _tag_preview_redraw()

    if not bpy.app.timers.is_registered(_direct_preview_timer):
        bpy.app.timers.register(_direct_preview_timer, first_interval=0.0, persistent=False)

    print(
        f"[Sanctuary Toolkit v1.3.8] Timer preview {clip.name!r}: "
        f"{len(clip.curves)} curves, source={clip.fps:g} FPS, "
        f"duration={clip.duration:g}s, preview clock=60 Hz"
    )
    return clip


def stop_direct_preview(context=None, reset_pose=True):
    global _ACTIVE_PREVIEW
    state = _ACTIVE_PREVIEW
    _ACTIVE_PREVIEW = None
    if not state:
        return
    arm = _find_armature_for_active_preview(state)
    if arm is None:
        return
    if arm.animation_data:
        states = dict(state.get("nla_state", []))
        for track in arm.animation_data.nla_tracks:
            if track.name in states:
                track.mute = states[track.name]
        old_name = state.get("old_action_name", "")
        arm.animation_data.action = bpy.data.actions.get(old_name) if old_name else None
    if reset_pose:
        for pb in arm.pose.bones:
            pb.matrix_basis.identity()
        arm.update_tag(refresh={"OBJECT"})
    if context is not None:
        try:
            context.view_layer.update()
        except Exception:
            pass
    _tag_preview_redraw()


def register_preview_handler():
    # v1.3.8 preview is timer-driven and intentionally does not register a
    # frame_change_post handler. Registration is kept as a compatibility entry
    # point for operators.py.
    if _direct_preview_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_direct_preview_frame_change)
    print("[Sanctuary Toolkit v1.3.8] timer animation preview ready")


def unregister_preview_handler():
    global _ACTIVE_PREVIEW
    _ACTIVE_PREVIEW = None
    if _direct_preview_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_direct_preview_frame_change)
    _PREVIEW_CLIP_CACHE.clear()


# Legacy handler retained only so old registrations can be removed safely.
def _direct_preview_frame_change(scene, depsgraph=None):
    return


# Override the earlier Action-baking import entry points. Export remains based on
# ordinary Blender Actions; import/preview is now direct and lossless with
# respect to the Sanctuary hierarchy.
def import_sananimation_bytes(armature_obj, blob, source_label="<memory>", swap_yz=True):
    clips = parse_sananimation_bytes(blob)
    _store_preview_clips(armature_obj, clips, source_label, swap_yz)
    # Keep the historical two-value return contract. Callers previously called
    # the second value `actions`; SanClip also exposes `.name`, so old callers can
    # select the first clip without requiring generated Blender Actions.
    return clips, clips


def import_sananimation_file(armature_obj, path, swap_yz=True):
    path = Path(path)
    return import_sananimation_bytes(armature_obj, path.read_bytes(), str(path), swap_yz)

# -----------------------------------------------------------------------------
# v1.3.8 editable Action preview
# -----------------------------------------------------------------------------
# v1.3.7's real-time timer was useful diagnostically, but it was a poor authoring
# workflow and exposed the scalar Hermite quaternion curves to sub-frame
# overshoot.  v1.3.8 bakes the *same deformation-preserving target matrices* used
# by the successful v1.3.4 preview into ordinary Blender Actions on the clip's
# authored frame grid.  Quaternion samples are normalized and hemisphere-fixed,
# then keyed linearly.  This makes the clip editable in the Dope Sheet and avoids
# live per-component Hermite jitter.

_ACTION_PREVIEW_STATE = None


def _clip_bake_grid(clip):
    fps = float(clip.fps) if float(clip.fps) > 1e-8 else 30.0
    duration = max(0.0, float(clip.duration))

    # Duration is the pacing authority.  Stock Sanctuary clips can report
    # frameCount=60, FPS=60, duration=1.0 and contain keys at both t=0 and t=1.
    # Baking only 60 Blender frames would shorten that to 59/60 s.  Build one
    # sample per authored frame interval *including both endpoints* instead.
    intervals = max(0, int(round(duration * fps)))
    if intervals == 0:
        return fps, [1.0], [0.0]
    frames = [float(i + 1) for i in range(intervals + 1)]
    times = [i / fps for i in range(intervals + 1)]
    times[-1] = duration
    return fps, frames, times


def _clean_scalar(value, reference=0.0, eps=1e-9):
    value = float(value)
    return float(reference) if abs(value - float(reference)) <= eps else value




def _create_grouped_curve(action, data_path, index, values, group_name):
    fc = action.fcurves.new(data_path=data_path, index=index, action_group=str(group_name))
    fc.keyframe_points.add(len(values))
    flat = []
    for frame, value in values:
        flat.extend((float(frame), float(value)))
    try:
        fc.keyframe_points.foreach_set("co", flat)
    except Exception:
        for kp, (frame, value) in zip(fc.keyframe_points, values):
            kp.co = (float(frame), float(value))
    for kp in fc.keyframe_points:
        kp.interpolation = "LINEAR"
        # Unweighted linear keys avoid handle/tangent overshoot entirely.
        try:
            kp.handle_left_type = "VECTOR"
            kp.handle_right_type = "VECTOR"
        except Exception:
            pass
    fc.update()
    return fc


def _bake_clip_to_editable_action(armature_obj, clip, source_label, swap_yz=True):
    """Bake one Sanctuary clip into a normal editable Blender Action.

    The pose source is `_target_pose_globals`, i.e. the same deformation-preserving
    transform path that gave the first stable Sanctuary previews.  We temporarily
    disable Action/NLA/constraints only while deriving matrix_basis, assign each
    target as PoseBone.matrix in parent-first order, then read Blender's own
    matrix_basis back.  This avoids hand-deriving Blender bone-roll space.
    """
    if not armature_obj or armature_obj.type != "ARMATURE":
        raise ValueError("Select/import an armature before loading Sanctuary animations")

    _mapping, ordered_raw = get_bone_id_map(armature_obj)
    ordered = _ordered_hierarchy(ordered_raw)
    if not ordered:
        raise ValueError("The selected armature contains no exportable bones")

    fps, frames, times = _clip_bake_grid(clip)
    action = _new_action_for_clip(armature_obj, clip, source_label, len(frames))
    action["san_frame_start"] = float(frames[0])
    action["san_frame_end"] = float(frames[-1])
    action["san_bake_mode"] = "deformation-global-visual-bake-v138"

    anim_data = armature_obj.animation_data_create()
    old_action = anim_data.action
    old_nla = [(track, bool(track.mute)) for track in anim_data.nla_tracks]
    old_frame = int(bpy.context.scene.frame_current)
    old_pose_position = armature_obj.data.pose_position
    old_basis = {pb.name: pb.matrix_basis.copy() for pb in armature_obj.pose.bones}
    old_modes = {pb.name: pb.rotation_mode for pb in armature_obj.pose.bones}
    constraint_state = []
    for pb in armature_obj.pose.bones:
        for con in pb.constraints:
            constraint_state.append((con, bool(con.mute)))

    # Values are stored as complete per-frame TRS tuples first, so quaternion
    # normalization/sign continuity is performed before any F-curves are made.
    samples = {b.name: [] for b in ordered}
    max_pose_assignment_error = 0.0

    try:
        anim_data.action = None
        for track, _mute in old_nla:
            track.mute = True
        for con, _mute in constraint_state:
            con.mute = True
        armature_obj.data.pose_position = "POSE"
        for pb in armature_obj.pose.bones:
            pb.rotation_mode = "QUATERNION"
            pb.matrix_basis.identity()

        for frame, t in zip(frames, times):
            _ord, targets, _unmatched, _ambiguous = _target_pose_globals(
                armature_obj, clip, t, bool(swap_yz)
            )

            # Assign the exact armature-space targets in parent-before-child order.
            for bone in ordered:
                pb = armature_obj.pose.bones.get(bone.name)
                if pb is not None:
                    pb.matrix = targets[bone.name]

            try:
                bpy.context.view_layer.update()
            except Exception:
                pass

            for bone in ordered:
                pb = armature_obj.pose.bones.get(bone.name)
                if pb is None:
                    continue
                try:
                    target = targets[bone.name]
                    err = max(
                        abs(float(pb.matrix[r][c] - target[r][c]))
                        for r in range(4) for c in range(4)
                    )
                    max_pose_assignment_error = max(max_pose_assignment_error, err)
                except Exception:
                    pass

                loc, rot, scale = pb.matrix_basis.decompose()
                qn2 = float(rot.w*rot.w + rot.x*rot.x + rot.y*rot.y + rot.z*rot.z)
                if qn2 < 1e-14 or not math.isfinite(qn2):
                    rot = Quaternion((1.0, 0.0, 0.0, 0.0))
                else:
                    rot.normalize()

                # Remove only machine-noise-sized values.  This is deliberately
                # not a motion smoothing filter; authored small motions survive.
                loc = (
                    _clean_scalar(loc.x, 0.0, 1e-9),
                    _clean_scalar(loc.y, 0.0, 1e-9),
                    _clean_scalar(loc.z, 0.0, 1e-9),
                )
                scale = (
                    _clean_scalar(scale.x, 1.0, 1e-8),
                    _clean_scalar(scale.y, 1.0, 1e-8),
                    _clean_scalar(scale.z, 1.0, 1e-8),
                )
                samples[bone.name].append((float(frame), loc, rot.copy(), scale))

        # Quaternion q and -q are the same orientation.  Keep every bone on one
        # hemisphere so Blender never interpolates through a 360-degree flip.
        for bone in ordered:
            prev = None
            fixed = []
            for frame, loc, q, scale in samples[bone.name]:
                q.normalize()
                if prev is not None and prev.dot(q) < 0.0:
                    q = Quaternion((-q.w, -q.x, -q.y, -q.z))
                fixed.append((frame, loc, q.copy(), scale))
                prev = q.copy()
            samples[bone.name] = fixed

    finally:
        # Restore the author's rig exactly after baking.  The imported Action is
        # only assigned when the user presses Play/selects the clip.
        anim_data.action = old_action
        for track, mute in old_nla:
            track.mute = mute
        for con, mute in constraint_state:
            try:
                con.mute = mute
            except Exception:
                pass
        for pb in armature_obj.pose.bones:
            if pb.name in old_modes:
                pb.rotation_mode = old_modes[pb.name]
            basis = old_basis.get(pb.name)
            if basis is not None:
                pb.matrix_basis = basis
        armature_obj.data.pose_position = old_pose_position
        try:
            bpy.context.scene.frame_set(old_frame)
            bpy.context.view_layer.update()
        except Exception:
            pass

    # Build grouped, editable F-curves.
    for bone in ordered:
        pb = armature_obj.pose.bones.get(bone.name)
        if pb is None:
            continue
        loc_channels = [[], [], []]
        rot_channels = [[], [], [], []]
        scale_channels = [[], [], []]
        for frame, loc, q, scale in samples[bone.name]:
            for i in range(3):
                loc_channels[i].append((frame, loc[i]))
                scale_channels[i].append((frame, scale[i]))
            rot_channels[0].append((frame, q.w))
            rot_channels[1].append((frame, q.x))
            rot_channels[2].append((frame, q.y))
            rot_channels[3].append((frame, q.z))

        for i in range(3):
            _create_grouped_curve(action, pb.path_from_id("location"), i, loc_channels[i], bone.name)
        for i in range(4):
            _create_grouped_curve(action, pb.path_from_id("rotation_quaternion"), i, rot_channels[i], bone.name)
        for i in range(3):
            _create_grouped_curve(action, pb.path_from_id("scale"), i, scale_channels[i], bone.name)

    action["san_pose_assignment_max_error"] = float(max_pose_assignment_error)
    print(
        f"[Sanctuary Toolkit v1.3.8] Baked editable Action {clip.name!r}: "
        f"{len(frames)} keys @ {fps:g} FPS, duration={clip.duration:g}s, "
        f"quaternions normalized/sign-continuous, pose error={max_pose_assignment_error:.3e}"
    )
    return action


def _bake_clips_to_editable_actions(armature_obj, clips, source_label, swap_yz=True):
    actions = []
    action_map = {}
    for clip in clips:
        action = _bake_clip_to_editable_action(
            armature_obj, clip, source_label, bool(swap_yz)
        )
        actions.append(action)
        action_map[clip.name] = action.name
    armature_obj["san_animation_actions_json"] = json.dumps([a.name for a in actions])
    armature_obj["san_animation_action_map_json"] = json.dumps(action_map, separators=(",", ":"))
    armature_obj["san_animation_source"] = str(source_label)
    return actions


def _action_for_clip(armature_obj, clip_name):
    if not armature_obj:
        return None
    try:
        mapping = json.loads(armature_obj.get("san_animation_action_map_json", "{}"))
        name = mapping.get(str(clip_name), "")
        if name:
            return bpy.data.actions.get(name)
    except Exception:
        pass
    # Compatibility fallback for older generated Actions.
    for action in bpy.data.actions:
        if action.get("san_armature_name") == armature_obj.name and action.get("san_clip_name") == str(clip_name):
            return action
    return None


def _set_scene_fps(scene, fps):
    fps = float(fps) if float(fps) > 1e-8 else 30.0
    nominal = max(1, int(round(fps)))
    scene.render.fps = nominal
    scene.render.fps_base = float(nominal) / fps


def _screen_animation_play(context):
    # Normal Blender playback is intentionally used so the Action is visible and
    # editable in Dope Sheet/Graph Editor while it runs.
    try:
        if context.screen and context.screen.is_animation_playing:
            return
    except Exception:
        pass
    try:
        bpy.ops.screen.animation_play()
        return
    except Exception:
        pass
    # Defensive fallback for unusual editor contexts.
    wm = context.window_manager
    for window in wm.windows:
        try:
            with context.temp_override(window=window, screen=window.screen):
                bpy.ops.screen.animation_play()
                return
        except Exception:
            continue
    raise RuntimeError("Blender could not start timeline playback from the current window")


def _screen_animation_pause(context):
    try:
        if context.screen and context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
            return
    except Exception:
        pass
    for window in context.window_manager.windows:
        try:
            if window.screen.is_animation_playing:
                with context.temp_override(window=window, screen=window.screen):
                    bpy.ops.screen.animation_cancel(restore_frame=False)
                return
        except Exception:
            continue


def begin_direct_preview(context, armature_obj, clip_name):
    """Assign the baked Action and start ordinary Blender timeline playback."""
    global _ACTION_PREVIEW_STATE, _ACTIVE_PREVIEW

    clip = get_cached_preview_clip(armature_obj, clip_name)
    action = _action_for_clip(armature_obj, clip_name)
    if clip is None or action is None:
        raise ValueError("This clip has no editable Blender Action. Reload its .sananimation file with v1.3.8.")

    # Finish any previous v1.3.8 preview session first.
    if _ACTION_PREVIEW_STATE and _ACTION_PREVIEW_STATE.get("armature_name") != armature_obj.name:
        restore_action_preview(context)

    anim_data = armature_obj.animation_data_create()
    if not _ACTION_PREVIEW_STATE:
        constraint_state = []
        for pb in armature_obj.pose.bones:
            for con in pb.constraints:
                constraint_state.append((pb.name, con.name, bool(con.mute)))
        _ACTION_PREVIEW_STATE = {
            "armature_name": armature_obj.name,
            "old_action_name": anim_data.action.name if anim_data.action else "",
            "nla_state": [(track.name, bool(track.mute)) for track in anim_data.nla_tracks],
            "constraint_state": constraint_state,
            "old_rotation_modes": {pb.name: pb.rotation_mode for pb in armature_obj.pose.bones},
            "old_fps": int(context.scene.render.fps),
            "old_fps_base": float(context.scene.render.fps_base),
            "old_use_preview_range": bool(context.scene.use_preview_range),
            "old_preview_start": int(context.scene.frame_preview_start),
            "old_preview_end": int(context.scene.frame_preview_end),
            "old_frame": int(context.scene.frame_current),
        }

    _screen_animation_pause(context)

    # Imported Sanctuary Actions are already visually baked.  Authoring IK/pole
    # constraints must not solve them a second time during playback.
    for pb in armature_obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
        for con in pb.constraints:
            con.mute = True
    for track in anim_data.nla_tracks:
        track.mute = True
    anim_data.action = action
    try:
        armature_obj.select_set(True)
        context.view_layer.objects.active = armature_obj
    except Exception:
        pass

    start = int(round(float(action.get("san_frame_start", 1.0))))
    end = int(round(float(action.get("san_frame_end", max(start, start + int(clip.frame_count) - 1)))))
    _set_scene_fps(context.scene, float(clip.fps))
    context.scene.use_preview_range = True
    context.scene.frame_preview_start = start
    context.scene.frame_preview_end = max(start, end)
    context.scene.frame_set(start)
    try:
        context.view_layer.update()
    except Exception:
        pass

    _ACTIVE_PREVIEW = None  # explicitly disable legacy timer/handler state
    _screen_animation_play(context)
    print(
        f"[Sanctuary Toolkit v1.3.8] Blender Action preview {clip.name!r}: "
        f"frames {start}-{end}, source={clip.fps:g} FPS, duration={clip.duration:g}s"
    )
    return clip


def stop_direct_preview(context=None, reset_pose=False):
    """Pause playback but intentionally keep the imported Action active/editable."""
    if context is None:
        context = bpy.context
    _screen_animation_pause(context)
    # Do not clear the Action and do not restore constraints here.  Keeping the
    # visually baked Action active is what makes the Dope Sheet useful after Pause.


def restore_action_preview(context=None):
    """Exit imported-Action preview mode and restore the user's prior rig state."""
    global _ACTION_PREVIEW_STATE, _ACTIVE_PREVIEW
    if context is None:
        context = bpy.context
    _screen_animation_pause(context)
    state = _ACTION_PREVIEW_STATE
    _ACTION_PREVIEW_STATE = None
    _ACTIVE_PREVIEW = None
    if not state:
        return

    arm = bpy.data.objects.get(state.get("armature_name", ""))
    if arm is not None and arm.type == "ARMATURE":
        anim_data = arm.animation_data_create()
        nla = dict(state.get("nla_state", []))
        for track in anim_data.nla_tracks:
            if track.name in nla:
                track.mute = nla[track.name]
        cstate = {(pb, con): mute for pb, con, mute in state.get("constraint_state", [])}
        for pb in arm.pose.bones:
            old_mode = state.get("old_rotation_modes", {}).get(pb.name)
            if old_mode:
                pb.rotation_mode = old_mode
            for con in pb.constraints:
                key = (pb.name, con.name)
                if key in cstate:
                    con.mute = bool(cstate[key])
        old_name = state.get("old_action_name", "")
        anim_data.action = bpy.data.actions.get(old_name) if old_name else None
        if anim_data.action is None:
            for pb in arm.pose.bones:
                pb.matrix_basis.identity()

    scene = context.scene
    scene.render.fps = int(state.get("old_fps", scene.render.fps))
    scene.render.fps_base = float(state.get("old_fps_base", scene.render.fps_base))
    scene.use_preview_range = bool(state.get("old_use_preview_range", False))
    scene.frame_preview_start = int(state.get("old_preview_start", scene.frame_preview_start))
    scene.frame_preview_end = int(state.get("old_preview_end", scene.frame_preview_end))
    try:
        scene.frame_set(int(state.get("old_frame", scene.frame_current)))
        context.view_layer.update()
    except Exception:
        pass


def register_preview_handler():
    # Remove handlers left by older toolkit packages if this exact function is
    # somehow present, then use only normal Action evaluation in v1.3.8.
    if _direct_preview_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_direct_preview_frame_change)
    print("[Sanctuary Toolkit v1.3.8] editable Action animation preview ready")


def unregister_preview_handler():
    global _ACTION_PREVIEW_STATE, _ACTIVE_PREVIEW
    try:
        restore_action_preview(bpy.context)
    except Exception:
        pass
    _ACTION_PREVIEW_STATE = None
    _ACTIVE_PREVIEW = None
    if _direct_preview_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_direct_preview_frame_change)
    _PREVIEW_CLIP_CACHE.clear()


def import_sananimation_bytes(armature_obj, blob, source_label="<memory>", swap_yz=True):
    clips = parse_sananimation_bytes(blob)
    _store_preview_clips(armature_obj, clips, source_label, swap_yz)
    from .native_animation import import_native_actions
    actions = import_native_actions(armature_obj, clips, source_label, swap_yz)
    return clips, actions


def import_sananimation_file(armature_obj, path, swap_yz=True):
    path = Path(path)
    return import_sananimation_bytes(armature_obj, path.read_bytes(), str(path), swap_yz)

# -----------------------------------------------------------------------------
# v1.3.9 quaternion-stable editable Action import / preview
# -----------------------------------------------------------------------------
# v1.3.8 proved that making imported clips ordinary Blender Actions is the right
# authoring workflow, but it baked poses by assigning PoseBone.matrix and sampled
# Sanctuary quaternion components as four independent Hermite curves.  Unity's
# quaternion animation semantics keep rotation keys linked and interpolate the
# shortest rotational path.  Treating W/X/Y/Z as independent scalar splines can
# create visible twitching even after normalizing each sampled quaternion.
#
# v1.3.9 therefore:
#   * evaluates rotation as one quaternion track with hemisphere continuity + SLERP;
#   * bakes imported clips to at least 60 FPS while preserving serialized duration;
#   * derives Blender matrix_basis with Bone.convert_local_to_pose(), without
#     mutating the live pose/dependency graph while the Action is being built;
#   * keeps the normal Blender timeline / Dope Sheet workflow;
#   * leaves the v1.3.8 model/skeleton/material/browser pipeline untouched.

_V139_QUAT_CACHE = {}


def _v139_curve_quat_keys(curve, default_quat):
    """Return linked, normalized Sanctuary quaternion keys for one bone curve."""
    if curve is None:
        q = default_quat.copy()
        q.normalize()
        return [(0.0, q)]

    channels = curve.channels[3:7]
    times = sorted({float(k.time) for ch in channels for k in ch})
    if not times:
        q = default_quat.copy()
        q.normalize()
        return [(0.0, q)]

    # Cache by curve identity + the default quaternion. Curves live in the parsed
    # clip cache for the lifetime of the import session.
    cache_key = (
        id(curve),
        round(float(default_quat.w), 9), round(float(default_quat.x), 9),
        round(float(default_quat.y), 9), round(float(default_quat.z), 9),
    )
    cached = _V139_QUAT_CACHE.get(cache_key)
    if cached is not None:
        return cached

    defaults = (float(default_quat.w), float(default_quat.x), float(default_quat.y), float(default_quat.z))
    result = []
    prev = None
    for t in times:
        vals = [
            _eval_channel(channels[i], t, defaults[i]) if i < len(channels) else defaults[i]
            for i in range(4)
        ]
        q = Quaternion((vals[0], vals[1], vals[2], vals[3]))
        n2 = float(q.w*q.w + q.x*q.x + q.y*q.y + q.z*q.z)
        if n2 < 1e-14 or not math.isfinite(n2):
            q = default_quat.copy()
        else:
            q.normalize()
        if prev is not None and prev.dot(q) < 0.0:
            q = Quaternion((-q.w, -q.x, -q.y, -q.z))
        result.append((float(t), q.copy()))
        prev = q.copy()

    _V139_QUAT_CACHE[cache_key] = result
    return result


def _v139_eval_quaternion(curve, t, default_quat):
    """Evaluate a Sanctuary rotation using shortest-path quaternion interpolation."""
    keys = _v139_curve_quat_keys(curve, default_quat)
    if len(keys) == 1 or t <= keys[0][0]:
        return keys[0][1].copy()
    if t >= keys[-1][0]:
        return keys[-1][1].copy()

    lo = 0
    hi = len(keys) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if keys[mid][0] <= t:
            lo = mid
        else:
            hi = mid
    ta, qa = keys[lo]
    tb, qb = keys[hi]
    dt = float(tb - ta)
    if abs(dt) < 1e-12:
        return qb.copy()
    u = max(0.0, min(1.0, (float(t) - ta) / dt))
    q = qa.slerp(qb, u)
    q.normalize()
    return q


def _curve_local_san(curve, t, rest_matrix):
    """v1.3.9: scalar Hermite for translation/scale, linked quaternion SLERP for rotation."""
    defaults = _matrix_defaults(rest_matrix)
    if curve is None:
        return rest_matrix.copy()

    # Position and scale remain genuine scalar AnimationCurves.
    px = _eval_channel(curve.channels[0], t, defaults[0])
    py = _eval_channel(curve.channels[1], t, defaults[1])
    pz = _eval_channel(curve.channels[2], t, defaults[2])

    default_q = Quaternion((defaults[3], defaults[4], defaults[5], defaults[6]))
    n2 = float(default_q.w*default_q.w + default_q.x*default_q.x + default_q.y*default_q.y + default_q.z*default_q.z)
    if n2 < 1e-14 or not math.isfinite(n2):
        default_q = Quaternion((1.0, 0.0, 0.0, 0.0))
    else:
        default_q.normalize()
    q = _v139_eval_quaternion(curve, float(t), default_q)

    sx = _eval_channel(curve.channels[7], t, defaults[7])
    sy = _eval_channel(curve.channels[8], t, defaults[8])
    sz = _eval_channel(curve.channels[9], t, defaults[9])
    sx = float(sx) if math.isfinite(float(sx)) else 1.0
    sy = float(sy) if math.isfinite(float(sy)) else 1.0
    sz = float(sz) if math.isfinite(float(sz)) else 1.0

    return (
        Matrix.Translation((float(px), float(py), float(pz)))
        @ q.to_matrix().to_4x4()
        @ Matrix.Diagonal((sx, sy, sz, 1.0))
    )


def _v139_clip_bake_grid(clip):
    """Return a dense editable Blender timeline while preserving exact clip duration."""
    source_fps = float(clip.fps) if float(clip.fps) > 1e-8 else 30.0
    duration = max(0.0, float(clip.duration))
    # Dense preview keys prevent Blender's component F-curves from having to span
    # long quaternion arcs.  60 Hz is enough for 24/30 FPS authored clips and
    # leaves native 60 FPS game clips unchanged.
    bake_fps = max(60.0, source_fps)
    if duration <= 1e-9:
        return bake_fps, [1.0], [0.0]
    intervals = max(1, int(round(duration * bake_fps)))
    times = [duration * (i / intervals) for i in range(intervals + 1)]
    frames = [1.0 + i for i in range(intervals + 1)]
    return bake_fps, frames, times


def _v139_basis_from_targets(ordered, targets):
    """Solve matrix_basis directly from desired armature-space matrices."""
    basis = {}
    max_err = 0.0
    for bone in ordered:
        target = targets[bone.name]
        if bone.parent and bone.parent.name in targets:
            parent_target = targets[bone.parent.name]
            bmat = bone.convert_local_to_pose(
                target,
                bone.matrix_local,
                parent_matrix=parent_target,
                parent_matrix_local=bone.parent.matrix_local,
                invert=True,
            )
            roundtrip = bone.convert_local_to_pose(
                bmat,
                bone.matrix_local,
                parent_matrix=parent_target,
                parent_matrix_local=bone.parent.matrix_local,
            )
        else:
            bmat = bone.convert_local_to_pose(target, bone.matrix_local, invert=True)
            roundtrip = bone.convert_local_to_pose(bmat, bone.matrix_local)
        basis[bone.name] = bmat
        try:
            err = max(
                abs(float(roundtrip[r][c] - target[r][c]))
                for r in range(4) for c in range(4)
            )
            max_err = max(max_err, err)
        except Exception:
            pass
    return basis, max_err


def _bake_clip_to_editable_action(armature_obj, clip, source_label, swap_yz=True):
    """Bake one clip to an ordinary Blender Action without touching the live pose."""
    if not armature_obj or armature_obj.type != "ARMATURE":
        raise ValueError("Select/import an armature before loading Sanctuary animations")

    _mapping, ordered_raw = get_bone_id_map(armature_obj)
    ordered = _ordered_hierarchy(ordered_raw)
    if not ordered:
        raise ValueError("The selected armature contains no exportable bones")

    bake_fps, frames, times = _v139_clip_bake_grid(clip)
    action = _new_action_for_clip(armature_obj, clip, source_label, len(frames))
    action["san_frame_start"] = float(frames[0])
    action["san_frame_end"] = float(frames[-1])
    action["san_preview_fps"] = float(bake_fps)
    action["san_bake_mode"] = "quat-slerp-convert-local-to-pose-v139"

    samples = {b.name: [] for b in ordered}
    previous = {b.name: None for b in ordered}
    max_roundtrip = 0.0

    for frame, t in zip(frames, times):
        _ord, targets, _unmatched, _ambiguous = _target_pose_globals(
            armature_obj, clip, float(t), bool(swap_yz)
        )
        basis_by_name, err = _v139_basis_from_targets(ordered, targets)
        max_roundtrip = max(max_roundtrip, float(err))

        for bone in ordered:
            bmat = basis_by_name[bone.name]
            loc, rot, scale = bmat.decompose()
            n2 = float(rot.w*rot.w + rot.x*rot.x + rot.y*rot.y + rot.z*rot.z)
            if n2 < 1e-14 or not math.isfinite(n2):
                rot = Quaternion((1.0, 0.0, 0.0, 0.0))
            else:
                rot.normalize()
            prev = previous[bone.name]
            if prev is not None and prev.dot(rot) < 0.0:
                rot = Quaternion((-rot.w, -rot.x, -rot.y, -rot.z))
            previous[bone.name] = rot.copy()

            # Only eliminate true float noise; never smooth authored movement.
            loc_t = tuple(0.0 if abs(float(v)) < 1e-10 else float(v) for v in loc)
            scale_t = tuple(1.0 if abs(float(v) - 1.0) < 1e-9 else float(v) for v in scale)
            samples[bone.name].append((float(frame), loc_t, rot.copy(), scale_t))

    for bone in ordered:
        pb = armature_obj.pose.bones.get(bone.name)
        if pb is None:
            continue
        loc_channels = [[], [], []]
        rot_channels = [[], [], [], []]
        scale_channels = [[], [], []]
        for frame, loc, q, scale in samples[bone.name]:
            for i in range(3):
                loc_channels[i].append((frame, loc[i]))
                scale_channels[i].append((frame, scale[i]))
            rot_channels[0].append((frame, q.w))
            rot_channels[1].append((frame, q.x))
            rot_channels[2].append((frame, q.y))
            rot_channels[3].append((frame, q.z))
        for i in range(3):
            _create_grouped_curve(action, pb.path_from_id("location"), i, loc_channels[i], bone.name)
        for i in range(4):
            _create_grouped_curve(action, pb.path_from_id("rotation_quaternion"), i, rot_channels[i], bone.name)
        for i in range(3):
            _create_grouped_curve(action, pb.path_from_id("scale"), i, scale_channels[i], bone.name)

    action["san_pose_roundtrip_max_error"] = float(max_roundtrip)
    print(
        f"[Sanctuary Toolkit v1.4.2] Baked {clip.name!r}: "
        f"{len(frames)} editable keys @ {bake_fps:g} FPS, duration={clip.duration:g}s, "
        f"quaternion=SLERP/normalized/continuous, pose round-trip={max_roundtrip:.3e}"
    )
    return action


def _bake_clips_to_editable_actions(armature_obj, clips, source_label, swap_yz=True):
    actions = []
    action_map = {}
    for clip in clips:
        action = _bake_clip_to_editable_action(armature_obj, clip, source_label, bool(swap_yz))
        actions.append(action)
        action_map[clip.name] = action.name
    armature_obj["san_animation_actions_json"] = json.dumps([a.name for a in actions])
    armature_obj["san_animation_action_map_json"] = json.dumps(action_map, separators=(",", ":"))
    armature_obj["san_animation_source"] = str(source_label)
    return actions


def begin_direct_preview(context, armature_obj, clip_name):
    """v1.3.9: play the editable Action on Blender's normal timeline."""
    global _ACTION_PREVIEW_STATE, _ACTIVE_PREVIEW
    clip = get_cached_preview_clip(armature_obj, clip_name)
    action = _action_for_clip(armature_obj, clip_name)
    if clip is None or action is None:
        raise ValueError("This clip has no editable Action. Reload its .sananimation file.")

    if _ACTION_PREVIEW_STATE and _ACTION_PREVIEW_STATE.get("armature_name") != armature_obj.name:
        restore_action_preview(context)

    _screen_animation_pause(context)
    anim_data = armature_obj.animation_data_create()
    if not _ACTION_PREVIEW_STATE:
        constraint_state = []
        for pb in armature_obj.pose.bones:
            for con in pb.constraints:
                constraint_state.append((pb.name, con.name, bool(con.mute)))
        _ACTION_PREVIEW_STATE = {
            "armature_name": armature_obj.name,
            "old_action_name": anim_data.action.name if anim_data.action else "",
            "nla_state": [(track.name, bool(track.mute)) for track in anim_data.nla_tracks],
            "constraint_state": constraint_state,
            "old_rotation_modes": {pb.name: pb.rotation_mode for pb in armature_obj.pose.bones},
            "old_fps": int(context.scene.render.fps),
            "old_fps_base": float(context.scene.render.fps_base),
            "old_use_preview_range": bool(context.scene.use_preview_range),
            "old_preview_start": int(context.scene.frame_preview_start),
            "old_preview_end": int(context.scene.frame_preview_end),
            "old_frame": int(context.scene.frame_current),
        }

    # The Action contains final baked deform-bone motion.  Disable only pose-bone
    # constraints while preview/edit mode is active so IK cannot solve it twice.
    for pb in armature_obj.pose.bones:
        pb.rotation_mode = "QUATERNION"
        for con in pb.constraints:
            con.mute = True
    for track in anim_data.nla_tracks:
        track.mute = True
    anim_data.action = action

    start = int(round(float(action.get("san_frame_start", 1.0))))
    end = int(round(float(action.get("san_frame_end", start))))
    preview_fps = float(action.get("san_preview_fps", max(60.0, float(clip.fps))))
    _set_scene_fps(context.scene, preview_fps)
    context.scene.use_preview_range = True
    context.scene.frame_preview_start = start
    context.scene.frame_preview_end = max(start, end)
    context.scene.frame_set(start)
    try:
        context.view_layer.update()
        armature_obj.select_set(True)
        context.view_layer.objects.active = armature_obj
    except Exception:
        pass

    _ACTIVE_PREVIEW = None
    _screen_animation_play(context)
    print(
        f"[Sanctuary Toolkit v1.4.2] Blender Action preview {clip.name!r}: "
        f"frames {start}-{end}, timeline={preview_fps:g} FPS, source={clip.fps:g} FPS, "
        f"duration={clip.duration:g}s"
    )
    return clip


def stop_direct_preview(context=None, reset_pose=False):
    if context is None:
        context = bpy.context
    _screen_animation_pause(context)
    # Keep Action and muted authoring constraints active so the user can edit the
    # exact preview pose in the Dope Sheet immediately after Pause.


def restore_action_preview(context=None):
    global _ACTION_PREVIEW_STATE, _ACTIVE_PREVIEW
    if context is None:
        context = bpy.context
    _screen_animation_pause(context)
    state = _ACTION_PREVIEW_STATE
    _ACTION_PREVIEW_STATE = None
    _ACTIVE_PREVIEW = None
    if not state:
        return
    arm = bpy.data.objects.get(state.get("armature_name", ""))
    if arm is not None and arm.type == "ARMATURE":
        anim_data = arm.animation_data_create()
        nla = dict(state.get("nla_state", []))
        for track in anim_data.nla_tracks:
            if track.name in nla:
                track.mute = nla[track.name]
        cstate = {(pb, con): mute for pb, con, mute in state.get("constraint_state", [])}
        for pb in arm.pose.bones:
            old_mode = state.get("old_rotation_modes", {}).get(pb.name)
            if old_mode:
                pb.rotation_mode = old_mode
            for con in pb.constraints:
                key = (pb.name, con.name)
                if key in cstate:
                    con.mute = bool(cstate[key])
        old_name = state.get("old_action_name", "")
        anim_data.action = bpy.data.actions.get(old_name) if old_name else None
        if anim_data.action is None:
            for pb in arm.pose.bones:
                pb.matrix_basis.identity()

    scene = context.scene
    scene.render.fps = int(state.get("old_fps", scene.render.fps))
    scene.render.fps_base = float(state.get("old_fps_base", scene.render.fps_base))
    scene.use_preview_range = bool(state.get("old_use_preview_range", False))
    scene.frame_preview_start = int(state.get("old_preview_start", scene.frame_preview_start))
    scene.frame_preview_end = int(state.get("old_preview_end", scene.frame_preview_end))
    try:
        scene.frame_set(int(state.get("old_frame", scene.frame_current)))
        context.view_layer.update()
    except Exception:
        pass


def register_preview_handler():
    # v1.3.9 uses only ordinary Blender Action evaluation. Remove any stale direct
    # preview handler left in this package instance.
    if _direct_preview_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_direct_preview_frame_change)
    print("[Sanctuary Toolkit v1.4.7] deformation-preserving editable preview ready; v1.4.6 export retained")


def unregister_preview_handler():
    global _ACTION_PREVIEW_STATE, _ACTIVE_PREVIEW
    try:
        restore_action_preview(bpy.context)
    except Exception:
        pass
    _ACTION_PREVIEW_STATE = None
    _ACTIVE_PREVIEW = None
    if _direct_preview_frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(_direct_preview_frame_change)
    _PREVIEW_CLIP_CACHE.clear()
    _V139_QUAT_CACHE.clear()


def import_sananimation_bytes(armature_obj, blob, source_label="<memory>", swap_yz=True):
    clips = parse_sananimation_bytes(blob)
    _store_preview_clips(armature_obj, clips, source_label, swap_yz)
    # Restore the proven deformation-preserving preview. This is intentionally
    # independent of v1.4.6's direct baked-source export, which stays unchanged.
    # Raw component Hermite import made stock quaternion preview unstable.
    actions = _bake_clips_to_editable_actions(armature_obj, clips, source_label, swap_yz)
    for action in actions:
        action.use_fake_user = True
        action["san_import_preview"] = "deformation-preserving-slerp-v147"
    return clips, actions


def import_sananimation_file(armature_obj, path, swap_yz=True):
    path = Path(path)
    return import_sananimation_bytes(armature_obj, path.read_bytes(), str(path), swap_yz)

# -----------------------------------------------------------------------------
# v1.4.0 multi-rig animation export
# -----------------------------------------------------------------------------
#
# A Blender Action is not a complete pose by itself: it is evaluated against an
# armature's rest matrices, constraints and IK controls.  v1.3.x applied every
# Action to whichever armature happened to be active when Export was pressed.
# In scenes with one imported armature per clip (Rest / Walk / Build), selecting
# a different rig therefore changed *all* exported clips.
#
# v1.4.0 binds every export item to its own Source Rig and converts the evaluated
# deformation from that source rest pose onto one fixed Target/Skeleton Rig.
# Selection no longer participates in the animation math.

from .utils import atomic_overwrite_bytes


def _v140_action_owners(action):
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


def _v140_resolve_source_armature(item, target_armature):
    src = getattr(item, "source_armature", None)
    if src and getattr(src, "type", None) == "ARMATURE":
        return src
    owners = _v140_action_owners(getattr(item, "action", None))
    if len(owners) == 1:
        return owners[0]
    if target_armature in owners:
        return target_armature
    return target_armature


def _v140_original_bone_name(bone):
    try:
        return str(bone.get("san_original_name", bone.name))
    except Exception:
        return str(bone.name)


def _v140_source_bones_for_target(source_armature, target_ordered):
    """Resolve only canonical target bones; ignore IK/helper/source-only bones."""
    src_bones = list(source_armature.data.bones)
    by_name = {b.name: b for b in src_bones}
    by_original = {}
    by_path = {}
    by_id = {}
    for b in src_bones:
        by_original.setdefault(_v140_original_bone_name(b), []).append(b)
        try:
            path = str(b.get("san_path", ""))
        except Exception:
            path = ""
        if path:
            by_path[path] = b
        try:
            if "san_bone_id" in b:
                bid = int(b["san_bone_id"])
                by_id.setdefault(bid, []).append(b)
        except Exception:
            pass

    resolved = {}
    missing = []
    for tb in target_ordered:
        sb = None
        # Imported/copied Sanctuary rigs: numeric ID + original name is the
        # strongest identity and avoids matching a Blender .001 helper copy.
        try:
            tbid = int(tb.get("san_bone_id")) if "san_bone_id" in tb else None
        except Exception:
            tbid = None
        if tbid is not None:
            candidates = by_id.get(tbid, [])
            same = [b for b in candidates if _v140_original_bone_name(b) == _v140_original_bone_name(tb)]
            if len(same) == 1:
                sb = same[0]
            elif len(candidates) == 1:
                sb = candidates[0]

        if sb is None:
            try:
                tpath = str(tb.get("san_path", ""))
            except Exception:
                tpath = ""
            if tpath:
                sb = by_path.get(tpath)

        if sb is None:
            candidates = by_original.get(_v140_original_bone_name(tb), [])
            if len(candidates) == 1:
                sb = candidates[0]

        if sb is None:
            sb = by_name.get(tb.name)

        if sb is None:
            missing.append(tb.name)
        resolved[tb.name] = sb
    return resolved, missing


def _v140_target_rest_globals_san(target_armature, ordered_bones, swap_yz):
    local = _source_rest_san_matrices(target_armature, ordered_bones, swap_yz)
    globals_san = {}
    for b in _ordered_hierarchy(ordered_bones):
        lm = local[b.name]
        if b.parent and b.parent.name in globals_san:
            globals_san[b.name] = globals_san[b.parent.name] @ lm
        else:
            globals_san[b.name] = lm.copy()
    return globals_san


def _v140_sample_source_action_to_target(
    context,
    target_armature,
    source_armature,
    action,
    target_ordered_bones,
    start,
    end,
    fps,
    loop,
    swap_yz=True,
):
    if end < start:
        start, end = end, start
    frames = list(range(int(start), int(end) + 1)) or [int(start)]
    frame_count = len(frames)
    duration = (frame_count / float(fps)) if loop else max(0.0, (frame_count - 1) / float(fps))
    sample_times = [i / float(fps) for i in range(frame_count)]

    target_hierarchy = _ordered_hierarchy(target_ordered_bones)
    target_rest_global_san = _v140_target_rest_globals_san(
        target_armature, target_ordered_bones, bool(swap_yz)
    )
    source_by_target, missing = _v140_source_bones_for_target(
        source_armature, target_ordered_bones
    )
    if missing:
        print(
            f"[Sanctuary Toolkit v1.4.2] {action.name!r}: "
            f"{len(missing)} target bone(s) are absent from source rig "
            f"{source_armature.name!r}; they will remain at target rest: "
            + ", ".join(missing[:12])
            + (" ..." if len(missing) > 12 else "")
        )

    samples = {b.name: [] for b in target_ordered_bones}
    scene = context.scene
    max_delta_roundtrip = 0.0
    max_local_rebuild_error = 0.0

    # Source Action is evaluated on *its own* armature. The deformation is then
    # transferred to the canonical target rest pose in armature/global space.
    for frame in frames:
        scene.frame_set(frame)
        context.view_layer.update()

        animated_target_global_san = {}
        for tb in target_hierarchy:
            sb = source_by_target.get(tb.name)
            if sb is None:
                animated_target_global_san[tb.name] = target_rest_global_san[tb.name].copy()
                continue

            pb = source_armature.pose.bones.get(sb.name)
            if pb is None:
                animated_target_global_san[tb.name] = target_rest_global_san[tb.name].copy()
                continue

            source_rest_global_bl = sb.matrix_local.copy()
            source_pose_global_bl = pb.matrix.copy()
            delta_bl = source_pose_global_bl @ source_rest_global_bl.inverted_safe()
            delta_san = blender_to_san_matrix(delta_bl) if swap_yz else delta_bl.copy()
            animated_target_global_san[tb.name] = delta_san @ target_rest_global_san[tb.name]

            # Coordinate transform must be an exact involution to float error.
            check_bl = san_to_blender_matrix(delta_san) if swap_yz else delta_san
            try:
                err = max(
                    abs(float(check_bl[r][c] - delta_bl[r][c]))
                    for r in range(4) for c in range(4)
                )
                max_delta_roundtrip = max(max_delta_roundtrip, err)
            except Exception:
                pass

        for tb in target_ordered_bones:
            g = animated_target_global_san[tb.name]
            if tb.parent and tb.parent.name in animated_target_global_san:
                local_san = animated_target_global_san[tb.parent.name].inverted_safe() @ g
            else:
                local_san = g.copy()

            loc, rot, scale = local_san.decompose()
            rot.normalize()
            rebuilt = (
                Matrix.Translation(loc)
                @ rot.to_matrix().to_4x4()
                @ Matrix.Diagonal((float(scale.x), float(scale.y), float(scale.z), 1.0))
            )
            try:
                err = max(
                    abs(float(rebuilt[r][c] - local_san[r][c]))
                    for r in range(4) for c in range(4)
                )
                max_local_rebuild_error = max(max_local_rebuild_error, err)
            except Exception:
                pass
            samples[tb.name].append((loc.copy(), rot.copy(), scale.copy()))

    if max_delta_roundtrip > 1e-4:
        raise ValueError(
            f"Animation '{action.name}' failed coordinate round-trip validation "
            f"(max matrix error {max_delta_roundtrip:.3e})"
        )
    if max_local_rebuild_error > 1e-3:
        raise ValueError(
            f"Animation '{action.name}' cannot be represented cleanly as Sanctuary TRS "
            f"(local shear/rebuild error {max_local_rebuild_error:.3e})."
        )

    # Quaternion sign continuity is required because q and -q represent the same
    # orientation but independent scalar curves would otherwise interpolate the
    # long way around.
    for tb in target_ordered_bones:
        track = samples[tb.name]
        prev = None
        for i, (loc, q, scale) in enumerate(track):
            if prev is not None and prev.dot(q) < 0.0:
                q = Quaternion((-q.w, -q.x, -q.y, -q.z))
                track[i] = (loc, q, scale)
            prev = q

    if loop and frame_count > 0:
        sample_times.append(float(duration))
        for tb in target_ordered_bones:
            track = samples[tb.name]
            loc0, q0, scale0 = track[0]
            q_close = q0.copy()
            if track[-1][1].dot(q_close) < 0.0:
                q_close = Quaternion((-q_close.w, -q_close.x, -q_close.y, -q_close.z))
            track.append((loc0.copy(), q_close, scale0.copy()))

    print(
        f"[Sanctuary Toolkit v1.4.6] Export {action.name!r}: "
        f"source={source_armature.name!r} -> target={target_armature.name!r}, "
        f"delta round-trip={max_delta_roundtrip:.3e}, "
        f"TRS rebuild={max_local_rebuild_error:.3e}"
    )
    return frame_count, duration, sample_times, samples


def _v140_save_armature_state(arm):
    ad = arm.animation_data_create()
    return {
        "action": ad.action,
        "nla": [(track, track.mute) for track in ad.nla_tracks],
        "pose_position": arm.data.pose_position,
        "pose_basis": {pb.name: pb.matrix_basis.copy() for pb in arm.pose.bones},
        "constraints": {
            (pb.name, constraint.name): bool(constraint.mute)
            for pb in arm.pose.bones
            for constraint in pb.constraints
        },
    }


def _v140_restore_armature_state(arm, state):
    try:
        ad = arm.animation_data_create()
        ad.action = state["action"]
        for track, mute in state["nla"]:
            try:
                track.mute = mute
            except Exception:
                pass
        arm.data.pose_position = state["pose_position"]
        for name, basis in state.get("pose_basis", {}).items():
            pb = arm.pose.bones.get(name)
            if pb is not None:
                pb.matrix_basis = basis
        for pb in arm.pose.bones:
            for constraint in pb.constraints:
                key = (pb.name, constraint.name)
                if key in state.get("constraints", {}):
                    constraint.mute = bool(state["constraints"][key])
    except Exception:
        pass


def _v141_reset_source_pose(context, arm):
    """Make Action evaluation deterministic by clearing stale unkeyed pose state.

    Assigning a Blender Action does not reset channels/bones that the Action does
    not key.  IK targets/feet could therefore inherit the pose of whichever
    armature/Action was active when the .blend was saved.  Reset to rest first;
    the Action then supplies every transform it actually owns.
    """
    ad = arm.animation_data_create()
    ad.action = None
    for pb in arm.pose.bones:
        try:
            pb.matrix_basis.identity()
        except Exception:
            pb.location = (0.0, 0.0, 0.0)
            if pb.rotation_mode == "QUATERNION":
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            else:
                pb.rotation_euler = (0.0, 0.0, 0.0)
            pb.scale = (1.0, 1.0, 1.0)
    context.view_layer.update()


def build_sananimation_bytes(context, armature_obj, items, swap_yz=True):
    """v1.4.2 exporter: deterministic per-clip source rigs -> one canonical target rig."""
    target_armature = armature_obj
    if not target_armature or getattr(target_armature, "type", None) != "ARMATURE":
        raise ValueError("Choose a Target / Skeleton Rig before exporting animations")

    mapping, ordered = get_bone_id_map(target_armature)
    enabled = [item for item in items if item.enabled and item.action]
    if not enabled:
        raise ValueError("No enabled animation Actions were selected")
    names = [item.clip_name.strip() or item.action.name for item in enabled]
    if len(set(names)) != len(names):
        raise ValueError("Animation clip names must be unique")

    sources = []
    for item in enabled:
        src = _v140_resolve_source_armature(item, target_armature)
        if src is None:
            raise ValueError(
                f"Clip '{item.clip_name or item.action.name}' has no Source Rig. "
                "Select its armature and re-add the Action, or choose Source Rig in the list."
            )
        sources.append(src)

    scene = context.scene
    old_frame = scene.frame_current
    states = {}
    out = bytearray(struct.pack("<i", len(enabled)))
    source_names = []
    try:
        # Save every touched source once.  Target need not be touched unless it is
        # also one of the source rigs.
        for src in dict.fromkeys(sources):
            states[src] = _v140_save_armature_state(src)
            src.data.pose_position = "POSE"
            ad = src.animation_data_create()
            for track in ad.nla_tracks:
                track.mute = True

        for blueprint_id, (item, src) in enumerate(zip(enabled, sources)):
            _v144_restore_source_snapshot(context, src, getattr(item, "source_pose_snapshot", ""))
            ad = src.animation_data_create()
            ad.action = item.action
            scene.frame_set(int(item.frame_start))
            context.view_layer.update()

            clip_name = item.clip_name.strip() or item.action.name
            fps = float(item.fps)
            if fps <= 0:
                raise ValueError(f"Clip '{clip_name}' has invalid FPS")

            frame_count, duration, times, samples = _v140_sample_source_action_to_target(
                context,
                target_armature,
                src,
                item.action,
                ordered,
                item.frame_start,
                item.frame_end,
                fps,
                bool(item.loop),
                bool(swap_yz),
            )

            out += _pack_cstr(clip_name)
            out += struct.pack("<i", blueprint_id)
            out += struct.pack("<ffii", fps, duration, frame_count, len(ordered) + 1)
            out += _identity_armature_curve(duration)
            for b in ordered:
                bid = mapping[b.name]
                path = full_bone_path(b)
                out += _curve_bytes(
                    bid + 1,
                    path,
                    _bone_channels(samples[b.name]),
                    times,
                    bool(item.loop),
                )
            source_names.append((clip_name, src.name))
    finally:
        for arm, state in states.items():
            _v140_restore_armature_state(arm, state)
        scene.frame_set(old_frame)
        context.view_layer.update()

    return bytes(out), {
        "clips": len(enabled),
        "bones": len(ordered),
        "clip_names": names,
        "target_rig": target_armature.name,
        "source_rigs": source_names,
    }


def export_sananimation_file(context, armature_obj, items, path, swap_yz=True):
    blob, stats = build_sananimation_bytes(context, armature_obj, items, swap_yz)
    atomic_overwrite_bytes(path, blob)
    return stats

# =============================================================================
# v1.4.2 deterministic animation export
# =============================================================================
# The sibling .sanskeleton file is now the authoritative animation target.
# Previously a live Blender armature supplied the target rest matrices. In scenes
# containing separate Rest/Walk/Build armatures, repinning/selecting a different
# armature could therefore change every exported clip even though the Actions
# themselves were unchanged. v1.4.2 serializes against the exact skeleton that
# Sanctuary will load, so UI selection cannot alter the target rest pose.


def _v142_san_local_from_entry(entry):
    p = entry.get("localPosition", {})
    r = entry.get("localRotation", {})
    loc = Matrix.Translation((
        float(p.get("x", 0.0)),
        float(p.get("y", 0.0)),
        float(p.get("z", 0.0)),
    ))
    q = Quaternion((
        float(r.get("w", 1.0)),
        float(r.get("x", 0.0)),
        float(r.get("y", 0.0)),
        float(r.get("z", 0.0)),
    ))
    if abs(q.w) + abs(q.x) + abs(q.y) + abs(q.z) <= 1e-12:
        q = Quaternion((1.0, 0.0, 0.0, 0.0))
    else:
        q.normalize()
    return loc @ q.to_matrix().to_4x4()


def _v142_load_target_manifest(animation_path):
    from .sanskeleton_io import parse_sanskeleton_file

    animation_path = Path(animation_path)
    sk_path = animation_path.with_suffix(".sanskeleton")
    if not sk_path.is_file():
        raise ValueError(
            f"Animation export requires the canonical sibling skeleton {sk_path.name}. "
            "Export the final model armature as .sanskeleton first."
        )
    data = parse_sanskeleton_file(sk_path)
    entries = sorted(data.get("bonesHierarchy", []), key=lambda e: int(e.get("boneId", 0)))
    ids = [int(e.get("boneId", -1)) for e in entries]
    if ids != list(range(len(entries))):
        raise ValueError(f"{sk_path.name} bone IDs are not contiguous from 0")

    globals_san = []
    paths = []
    manifest = []
    for i, e in enumerate(entries):
        parent = int(e.get("parentBoneId", -1))
        if parent >= i:
            raise ValueError(f"{sk_path.name}: bone {i} has invalid parent {parent}")
        lm = _v142_san_local_from_entry(e)
        gm = globals_san[parent] @ lm if parent >= 0 else lm
        globals_san.append(gm)
        name = str(e.get("name", f"Bone_{i}"))
        path = (paths[parent] + "/" + name) if parent >= 0 else name
        paths.append(path)
        manifest.append({
            "id": i,
            "name": name,
            "parent": parent,
            "path": path,
            "rest_global_san": gm,
        })
    return sk_path, manifest


def _v142_source_lookup(source_armature):
    by_id = {}
    by_path = {}
    by_original = {}
    by_name = {}
    for b in source_armature.data.bones:
        by_name[b.name] = b
        try:
            if "san_bone_id" in b:
                by_id.setdefault(int(b["san_bone_id"]), []).append(b)
        except Exception:
            pass
        try:
            path = str(b.get("san_path", ""))
        except Exception:
            path = ""
        if path:
            by_path[path] = b
        try:
            original = str(b.get("san_original_name", b.name))
        except Exception:
            original = b.name
        by_original.setdefault(original, []).append(b)
    return by_id, by_path, by_original, by_name


def _v142_map_source_bones(source_armature, manifest):
    by_id, by_path, by_original, by_name = _v142_source_lookup(source_armature)
    result = {}
    missing = []
    for t in manifest:
        sb = None
        candidates = by_id.get(t["id"], [])
        if candidates:
            exact = []
            for b in candidates:
                try:
                    original = str(b.get("san_original_name", b.name))
                except Exception:
                    original = b.name
                if original == t["name"]:
                    exact.append(b)
            if len(exact) == 1:
                sb = exact[0]
            elif len(candidates) == 1:
                sb = candidates[0]
        if sb is None:
            sb = by_path.get(t["path"])
        if sb is None:
            candidates = by_original.get(t["name"], [])
            if len(candidates) == 1:
                sb = candidates[0]
        if sb is None:
            sb = by_name.get(t["name"])
        if sb is None:
            missing.append(t["path"])
        result[t["id"]] = sb
    return result, missing


def _v144_restore_source_snapshot(context, arm, raw_snapshot):
    """Restore the pose baseline captured when the animation item was added.

    Actions only replace channels they key.  This snapshot makes all remaining
    IK/control values clip-local and prevents the .blend's last active/selected
    armature from changing another clip at export time.
    """
    ad = arm.animation_data_create()
    ad.action = None
    data = None
    if raw_snapshot:
        try:
            data = json.loads(raw_snapshot)
        except Exception:
            data = None
    for pb in arm.pose.bones:
        vals = data.get(pb.name) if isinstance(data, dict) else None
        if isinstance(vals, list) and len(vals) == 16:
            try:
                pb.matrix_basis = Matrix((
                    vals[0:4], vals[4:8], vals[8:12], vals[12:16]
                ))
                continue
            except Exception:
                pass
        try:
            pb.matrix_basis.identity()
        except Exception:
            pb.location = (0.0, 0.0, 0.0)
            if pb.rotation_mode == "QUATERNION":
                pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
            else:
                pb.rotation_euler = (0.0, 0.0, 0.0)
            pb.scale = (1.0, 1.0, 1.0)
    context.view_layer.update()


def _v146_restore_saved_constraints(arm, state):
    saved = state.get("constraints", {}) if state else {}
    for pb in arm.pose.bones:
        for constraint in pb.constraints:
            key = (pb.name, constraint.name)
            if key in saved:
                constraint.mute = bool(saved[key])


def _v146_configure_clip_constraints(context, arm, action, raw_snapshot, saved_state, evaluation="BAKED"):
    """Apply deterministic constraint state for one animation source.

    Sanctuary files imported by this toolkit are baked to every deform bone, so
    running IK/Copy Transform constraints again corrupts the feet. Authored
    Actions still need their constraints evaluated; those use the mute state
    captured when the export-list item was added.
    """
    _v146_restore_saved_constraints(arm, saved_state)
    baked = evaluation != "AUTHORED"
    if baked:
        for pb in arm.pose.bones:
            for constraint in pb.constraints:
                constraint.mute = True
        mode = "baked Action: constraints disabled"
    else:
        data = None
        if raw_snapshot:
            try:
                data = json.loads(raw_snapshot)
            except Exception as exc:
                raise ValueError(
                    f"Action '{action.name}' has an invalid frozen constraint state. "
                    "Remove it from Animation Export and Add Current again."
                ) from exc
        for pb in arm.pose.bones:
            for constraint in pb.constraints:
                bone_state = data.get(pb.name) if isinstance(data, dict) else None
                if not isinstance(bone_state, dict) or constraint.name not in bone_state:
                    raise ValueError(
                        f"Action '{action.name}' has no frozen state for {pb.name}/{constraint.name}. "
                        "Set the intended authoring constraint state, then remove this clip and Add Current again in v1.4.6."
                    )
                constraint.mute = bool(bone_state[constraint.name])
        mode = "authored Action: frozen constraint state"
    context.view_layer.update()
    return mode


def _v142_resolve_source_strict(item):
    src = getattr(item, "source_armature", None)
    if src is None or getattr(src, "type", None) != "ARMATURE":
        raise ValueError(
            f"Clip '{item.clip_name or getattr(item.action, 'name', 'Animation')}' has no fixed Source Rig. "
            "Select the correct armature, Add Current again, and verify Source Rig in the list."
        )
    return src


def _v142_sample_source_to_manifest(
    context, source_armature, action, manifest, start, end, fps, loop, swap_yz=True
):
    if end < start:
        start, end = end, start
    frames = list(range(int(start), int(end) + 1)) or [int(start)]
    frame_count = len(frames)
    native_range = action.get("san_native_action_range", [])
    native_timing = bool(
        action.get("san_native_key_import", False)
        and len(native_range) == 2
        and (int(start), int(end)) == tuple(int(round(float(value))) for value in native_range)
        and abs(float(fps) - float(action.get("san_preview_fps", fps))) <= 1e-5
    )
    if native_timing:
        frame_count = int(action.get("san_serialized_frame_count", frame_count))
        duration = float(action.get("san_duration", (len(frames)-1)/float(fps)))
    else:
        duration = (frame_count / float(fps)) if loop else max(0.0, (frame_count - 1) / float(fps))
    times = [i / float(fps) for i in range(len(frames))]

    source_map, missing = _v142_map_source_bones(source_armature, manifest)
    if missing:
        print(
            f"[Sanctuary Toolkit v1.4.2] {action.name!r}: {len(missing)} canonical bone(s) "
            f"missing from source rig {source_armature.name!r}; holding target rest: "
            + ", ".join(missing[:10]) + (" ..." if len(missing) > 10 else "")
        )

    samples = {t["id"]: [] for t in manifest}
    scene = context.scene
    max_roundtrip = 0.0
    max_rebuild = 0.0

    for frame in frames:
        scene.frame_set(frame)
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        try:
            depsgraph.update()
        except Exception:
            pass
        evaluated_armature = source_armature.evaluated_get(depsgraph)

        animated_global = {}
        for t in manifest:
            sb = source_map[t["id"]]
            if sb is None:
                animated_global[t["id"]] = t["rest_global_san"].copy()
                continue
            pb = evaluated_armature.pose.bones.get(sb.name)
            if pb is None:
                animated_global[t["id"]] = t["rest_global_san"].copy()
                continue

            pose_bl = pb.matrix.copy()
            # Baked source rigs can have different edit/rest matrices even when
            # their authored poses share the canonical armature coordinate
            # space. Subtracting source rest and applying target rest changes
            # those already-baked poses (notably the foot arcs). Serialize the
            # actual pose directly; only rebuild the canonical parent locals.
            pose_san = blender_to_san_matrix(pose_bl) if swap_yz else pose_bl.copy()
            animated_global[t["id"]] = pose_san

            check = san_to_blender_matrix(pose_san) if swap_yz else pose_san
            try:
                err = max(abs(float(check[r][c] - pose_bl[r][c])) for r in range(4) for c in range(4))
                max_roundtrip = max(max_roundtrip, err)
            except Exception:
                pass

        for t in manifest:
            g = animated_global[t["id"]]
            if t["parent"] >= 0:
                local = animated_global[t["parent"]].inverted_safe() @ g
            else:
                local = g.copy()
            loc, rot, scale = local.decompose()
            rot.normalize()
            rebuilt = (
                Matrix.Translation(loc)
                @ rot.to_matrix().to_4x4()
                @ Matrix.Diagonal((float(scale.x), float(scale.y), float(scale.z), 1.0))
            )
            try:
                err = max(abs(float(rebuilt[r][c] - local[r][c])) for r in range(4) for c in range(4))
                max_rebuild = max(max_rebuild, err)
            except Exception:
                pass
            samples[t["id"]].append((loc.copy(), rot.copy(), scale.copy()))

    if max_roundtrip > 1e-4:
        raise ValueError(
            f"Animation '{action.name}' failed coordinate round-trip validation ({max_roundtrip:.3e})"
        )
    if max_rebuild > 1e-3:
        raise ValueError(
            f"Animation '{action.name}' has unsupported local shear/TRS loss ({max_rebuild:.3e})"
        )

    # Normalize and keep quaternion hemisphere continuous.
    for t in manifest:
        track = samples[t["id"]]
        prev = None
        for i, (loc, q, scale) in enumerate(track):
            q.normalize()
            if prev is not None and prev.dot(q) < 0.0:
                q = Quaternion((-q.w, -q.x, -q.y, -q.z))
                track[i] = (loc, q, scale)
            prev = q

    if loop and frame_count and not native_timing:
        times.append(float(duration))
        for t in manifest:
            track = samples[t["id"]]
            loc0, q0, sc0 = track[0]
            qclose = q0.copy()
            if track[-1][1].dot(qclose) < 0.0:
                qclose = Quaternion((-qclose.w, -qclose.x, -qclose.y, -qclose.z))
            track.append((loc0.copy(), qclose, sc0.copy()))

    print(
        f"[Sanctuary Toolkit v1.4.6] Export {action.name!r}: "
        f"source={source_armature.name!r}, disk-target=sanskeleton, "
        f"round-trip={max_roundtrip:.3e}, TRS={max_rebuild:.3e}"
    )
    return frame_count, duration, times, samples


def build_sananimation_bytes(context, armature_obj, items, swap_yz=True, target_skeleton_path=None):
    """Preserve baked poses using the on-disk skeleton's IDs and hierarchy."""
    enabled = [item for item in items if item.enabled and item.action]
    if not enabled:
        raise ValueError("No enabled animation Actions were selected")
    names = [item.clip_name.strip() or item.action.name for item in enabled]
    if len(set(names)) != len(names):
        raise ValueError("Animation clip names must be unique")

    # target_skeleton_path is accepted for testing/internal callers; normal export
    # derives it from the .sananimation destination in export_sananimation_file.
    if target_skeleton_path is None:
        raise ValueError("Internal error: canonical skeleton path was not supplied")
    sk_path, manifest = _v142_load_target_manifest(Path(target_skeleton_path).with_suffix('.sananimation'))

    for item in enabled:
        if not getattr(item, "source_pose_snapshot", ""):
            raise ValueError(
                f"Clip '{item.clip_name or item.action.name}' was added by an older toolkit build and has no frozen Source Pose. "
                "Clear the Animation Export list and Add Current for each clip once in v1.4.6."
            )
    sources = [_v142_resolve_source_strict(item) for item in enabled]
    scene = context.scene
    old_frame = scene.frame_current
    states = {}
    out = bytearray(struct.pack("<i", len(enabled)))
    native_clip_count = 0
    try:
        for src in dict.fromkeys(sources):
            states[src] = _v140_save_armature_state(src)
            src.data.pose_position = "POSE"
            ad = src.animation_data_create()
            for track in ad.nla_tracks:
                track.mute = True

        for blueprint_id, (item, src) in enumerate(zip(enabled, sources)):
            from .native_animation import native_clip_for_export, serialize_clip_body
            native_clip = native_clip_for_export(item, src, manifest, swap_yz=swap_yz)
            if native_clip is not None:
                clip_name = item.clip_name.strip() or item.action.name
                out += serialize_clip_body(native_clip, name=clip_name, blueprint_id=blueprint_id)
                native_clip_count += 1
                print(f"[Sanctuary Toolkit v1.4.6] '{clip_name}': preserved original native curves without resampling")
                continue
            _v141_reset_source_pose(context, src)
            _v144_restore_source_snapshot(context, src, item.source_pose_snapshot)
            constraint_mode = _v146_configure_clip_constraints(
                context,
                src,
                item.action,
                getattr(item, "source_constraint_snapshot", ""),
                states[src],
                getattr(item, "source_evaluation", "BAKED"),
            )
            ad = src.animation_data_create()
            ad.action = item.action
            scene.frame_set(int(item.frame_start))
            context.view_layer.update()

            clip_name = item.clip_name.strip() or item.action.name
            fps = float(item.fps)
            if fps <= 0.0:
                raise ValueError(f"Clip '{clip_name}' has invalid FPS")

            frame_count, duration, times, samples = _v142_sample_source_to_manifest(
                context, src, item.action, manifest,
                item.frame_start, item.frame_end, fps, bool(item.loop), bool(swap_yz)
            )
            print(
                f"[Sanctuary Toolkit v1.4.6] {clip_name!r}: {constraint_mode}"
            )

            out += _pack_cstr(clip_name)
            out += struct.pack("<i", blueprint_id)
            out += struct.pack("<ffii", fps, duration, frame_count, len(manifest) + 1)
            out += _identity_armature_curve(duration)
            for t in manifest:
                out += _curve_bytes(
                    t["id"] + 1,
                    t["path"],
                    _bone_channels(samples[t["id"]]),
                    times,
                    bool(item.loop),
                )
    finally:
        for arm, state in states.items():
            _v140_restore_armature_state(arm, state)
        scene.frame_set(old_frame)
        context.view_layer.update()

    return bytes(out), {
        "clips": len(enabled),
        "bones": len(manifest),
        "clip_names": names,
        "canonical_skeleton": sk_path.name,
        "source_rigs": [(item.clip_name or item.action.name, src.name) for item, src in zip(enabled, sources)],
        "native_clips_preserved": native_clip_count,
    }


def export_sananimation_file(context, armature_obj, items, path, swap_yz=True):
    path = Path(path)
    sk_path = path.with_suffix(".sanskeleton")
    blob, stats = build_sananimation_bytes(
        context, armature_obj, items, swap_yz,
        target_skeleton_path=sk_path,
    )
    atomic_overwrite_bytes(path, blob)
    return stats
