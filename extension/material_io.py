from pathlib import Path
import hashlib
import struct
import zlib

import bpy
import numpy as np

from .dds_tools import decode_dds_top
from .utils import console, texture_cache_dir


def classify_texture_name(name):
    low = Path(name).name.lower()
    if "normal" in low or "normals" in low:
        return "normal"
    if "albedo" in low or "diffuse" in low or "basecolor" in low or "base_color" in low:
        return "albedo"
    if "mask" in low or "specteam" in low or "spec_team" in low:
        return "mask"
    return None


def find_companion_texture_paths(model_path):
    """Return best matching DDS paths beside a manually imported sanmodel."""
    model_path = Path(model_path)
    folder = model_path.parent
    uid = model_path.stem
    if uid.lower().endswith("_lod0"):
        uid = uid[:-5]
    candidates = sorted(folder.glob("*.dds")) + sorted(folder.glob("*.DDS"))
    result = {}
    for kind in ("albedo", "mask", "normal"):
        matches = [p for p in candidates if classify_texture_name(p.name) == kind]
        if not matches:
            continue
        # Prefer textures starting with this unit/model id, then the shortest name.
        matches.sort(key=lambda p: (0 if p.stem.lower().startswith(uid.lower()) else 1, len(p.name), p.name.lower()))
        result[kind] = matches[0]
    return result


def _write_preview_png(path, rgba):
    """Write RGBA uint8 to a small, dependency-free PNG cache file.

    Loading the decoded texture back as a FILE image is intentionally used
    instead of leaving it as a Blender GENERATED image. Blender 4.0 can keep a
    newly-written generated image's GPU display buffer black until an explicit
    refresh; a file-backed PNG makes both the Image Editor and material preview
    deterministic.
    """
    rgba = np.asarray(rgba, dtype=np.uint8)
    h, w, c = rgba.shape
    if c != 4:
        raise ValueError("Preview PNG expects RGBA")
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    # Level 1 is considerably faster for large 2K/4K preview textures while
    # remaining lossless. These are cache files only, not game assets.
    png += chunk(b"IDAT", zlib.compress(raw, 1))
    png += chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def _blender_image_from_rgba(name, rgba, colorspace="sRGB"):
    rgba = np.asarray(rgba, dtype=np.uint8)
    h, w, _ = rgba.shape

    # Hash the decoded pixel payload so a changed DDS cannot silently reuse a
    # stale cache image with the same unit/slot name.
    digest = hashlib.sha1(rgba.tobytes()).hexdigest()[:12]
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in name)
    cache = texture_cache_dir() / f"{safe}_{digest}.png"
    if not cache.is_file():
        _write_preview_png(cache, rgba)

    # Remove only preview images created by this toolkit. A normal user image
    # with the same display name is never destroyed.
    old = bpy.data.images.get(name)
    if old and old.get("san_preview_generated"):
        bpy.data.images.remove(old)

    img = bpy.data.images.load(str(cache), check_existing=False)
    img.name = name
    try:
        img.colorspace_settings.name = colorspace
    except Exception:
        pass
    try:
        img.alpha_mode = "CHANNEL_PACKED"
    except Exception:
        pass
    img["san_preview_generated"] = True
    img["san_preview_cache_png"] = str(cache)
    # update() is still called because Blender may have already created a GPU
    # texture for this datablock before the colorspace is assigned.
    try:
        img.update()
    except Exception:
        pass
    # Keep previews file-backed. Packing every generated 2K/4K PNG duplicates
    # the texture inside the .blend and substantially delays browser imports.
    return img

def _normal_preview_rgba(rgba):
    """Turn either conventional RGB or DXT5nm-style A/G data into RGB normals."""
    rgba = np.asarray(rgba, dtype=np.uint8)
    alpha = rgba[..., 3].astype(np.float32)
    red = rgba[..., 0].astype(np.float32)
    # DXT5nm normally has useful X in alpha. If alpha is essentially opaque,
    # preserve the RGB normal as-authored (the toolkit's own exported normals use this).
    use_dxt5nm = float(alpha.std()) > 2.0 and float(np.mean(np.abs(alpha - red))) > 6.0
    if not use_dxt5nm:
        out = rgba.copy()
        out[..., 3] = 255
        return out

    x = alpha / 127.5 - 1.0
    y = rgba[..., 1].astype(np.float32) / 127.5 - 1.0
    z = np.sqrt(np.maximum(0.0, 1.0 - x * x - y * y))
    vec = np.stack((x, y, z), axis=-1)
    length = np.linalg.norm(vec, axis=-1, keepdims=True)
    vec /= np.maximum(length, 1e-8)
    out = np.empty_like(rgba)
    out[..., :3] = np.clip(np.rint((vec * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
    out[..., 3] = 255
    return out


def _input(node, *names):
    for name in names:
        sock = node.inputs.get(name)
        if sock is not None:
            return sock
    return None


def _opaque_copy(rgba):
    out = np.asarray(rgba, dtype=np.uint8).copy()
    out[..., 3] = 255
    return out


def _grayscale_rgba(channel):
    c = np.asarray(channel, dtype=np.uint8)
    return np.stack((c, c, c, np.full_like(c, 255)), axis=-1)


def apply_sanctuary_material(obj, unit_id, texture_blobs, source_labels=None):
    """Create a Blender material approximating Sanctuary's RTS/Unit shader.

    The toolkit deliberately keeps two kinds of Blender images:
      * *_data images preserve packed alpha/channels for the material nodes.
      * *_preview images are opaque, human-readable views for the Image Editor.

    This prevents Sanctuary's sparse team selector in albedo alpha from making a
    perfectly valid albedo look almost transparent/blank inside Blender.
    """
    if not obj or obj.type != "MESH":
        raise ValueError("A mesh object is required for texture preview")
    source_labels = source_labels or {}
    decoded = {}
    for kind, blob in texture_blobs.items():
        if not blob:
            continue
        try:
            raw = decode_dds_top(blob)
            decoded[kind] = raw
            rgb = raw[..., :3]
            console(
                f"Decoded {kind}: {raw.shape[1]}x{raw.shape[0]}, "
                f"RGB min/max={int(rgb.min())}/{int(rgb.max())}, mean={float(rgb.mean()):.1f}, "
                f"A min/max={int(raw[...,3].min())}/{int(raw[...,3].max())}"
            )
        except Exception as exc:
            console(f"Could not decode {kind} DDS for viewport preview: {exc}")

    if not decoded:
        return None, {}

    mat_name = f"SAN_{unit_id}_Preview"
    mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(mat_name)
    mat.use_nodes = True
    mat["san_preview_material"] = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    out.location = (760, 0)
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (470, 0)
    links.new(bsdf.outputs.get("BSDF"), out.inputs.get("Surface"))

    uv_node = None
    if obj.data.uv_layers:
        uv_node = nodes.new("ShaderNodeUVMap")
        uv_node.uv_map = obj.data.uv_layers[0].name
        uv_node.label = f"Sanctuary UV: {uv_node.uv_map}"
        uv_node.location = (-1050, 20)

    images = {}
    albedo_node = None
    if "albedo" in decoded:
        raw = decoded["albedo"]
        # One image contains both the visible RGB and the team selector alpha.
        # The old importer generated three full-resolution copies here.
        data_img = _blender_image_from_rgba(f"{unit_id}_albedo_data", raw, "sRGB")
        data_img["san_source"] = str(source_labels.get("albedo", ""))
        images["albedo"] = data_img
        images["albedo_data"] = data_img

        tex = nodes.new("ShaderNodeTexImage")
        tex.image = data_img
        tex.label = "Sanctuary Albedo RGB + Team Selector A"
        if uv_node is not None:
            links.new(uv_node.outputs["UV"], tex.inputs["Vector"])
        tex.location = (-760, 220)
        team_color=nodes.new("ShaderNodeRGB");team_color.label="SMT Team Color";team_color.name="SMT Team Color"
        try:team_color.outputs[0].default_value=bpy.context.scene.sanctuary_toolkit.preview_team_color
        except Exception:team_color.outputs[0].default_value=(1.0,.18,.02,1.0)
        team_color.location=(-520,360)
        team_mix=nodes.new("ShaderNodeMixRGB");team_mix.label="Faction Color Preview";team_mix.blend_type="MIX";team_mix.location=(-250,240)
        links.new(tex.outputs["Alpha"],team_mix.inputs[0]);links.new(tex.outputs["Color"],team_mix.inputs[1]);links.new(team_color.outputs[0],team_mix.inputs[2])
        albedo_node = team_mix
        base = _input(bsdf, "Base Color")
        if base:
            links.new(team_mix.outputs["Color"], base)

    if "mask" in decoded:
        raw = decoded["mask"]
        # Packed metallic/AO/emission/smoothness channels are numeric data.
        # Keep one channel-packed image and expose its channels through nodes;
        # generating five additional 4K preview images made imports needlessly
        # slow and memory-heavy.
        data_img = _blender_image_from_rgba(f"{unit_id}_mask_data", raw, "Non-Color")
        data_img["san_source"] = str(source_labels.get("mask", ""))
        images["mask"] = data_img
        images["mask_data"] = data_img

        tex = nodes.new("ShaderNodeTexImage")
        tex.image = data_img
        tex.label = "Sanctuary Mask: R Metal / G AO / B Emit / A Smooth"
        if uv_node is not None:
            links.new(uv_node.outputs["UV"], tex.inputs["Vector"])
        tex.location = (-760, -80)
        sep = nodes.new("ShaderNodeSeparateColor")
        sep.mode = "RGB"
        sep.location = (-490, -60)
        links.new(tex.outputs["Color"], sep.inputs["Color"])

        metallic = _input(bsdf, "Metallic")
        if metallic:
            links.new(sep.outputs["Red"], metallic)

        rough = _input(bsdf, "Roughness")
        if rough:
            inv = nodes.new("ShaderNodeMath")
            inv.operation = "SUBTRACT"
            inv.inputs[0].default_value = 1.0
            inv.location = (-220, -170)
            links.new(tex.outputs["Alpha"], inv.inputs[1])
            links.new(inv.outputs[0], rough)

        # Blender has no direct Principled AO input. Multiply base color by a
        # restrained AO factor so the viewport communicates the packed G channel
        # without turning the preview into a different material model.
        if albedo_node:
            ao_mix = nodes.new("ShaderNodeMixRGB")
            ao_mix.blend_type = "MULTIPLY"
            ao_mix.inputs[0].default_value = 1.0
            ao_mix.location = (20, 175)
            links.new(albedo_node.outputs["Color"], ao_mix.inputs[1])
            links.new(sep.outputs["Green"], ao_mix.inputs[2])
            base = _input(bsdf, "Base Color")
            if base:
                # Replace the earlier direct albedo link.
                for link in list(base.links):
                    links.remove(link)
                links.new(ao_mix.outputs["Color"], base)

            emit_mix = nodes.new("ShaderNodeMixRGB")
            emit_mix.blend_type = "MULTIPLY"
            emit_mix.inputs[0].default_value = 1.0
            emit_mix.location = (10, 50)
            links.new(albedo_node.outputs["Color"], emit_mix.inputs[1])
            links.new(sep.outputs["Blue"], emit_mix.inputs[2])
            emission = _input(bsdf, "Emission Color", "Emission")
            if emission:
                links.new(emit_mix.outputs["Color"], emission)
            strength = _input(bsdf, "Emission Strength")
            if strength:
                strength.default_value = 1.0

    if "normal" in decoded:
        normal_rgba = _normal_preview_rgba(decoded["normal"])
        img = _blender_image_from_rgba(f"{unit_id}_normal_preview", normal_rgba, "Non-Color")
        img["san_source"] = str(source_labels.get("normal", ""))
        images["normal"] = img
        tex = nodes.new("ShaderNodeTexImage")
        tex.image = img
        tex.label = "Sanctuary Normal"
        if uv_node is not None:
            links.new(uv_node.outputs["UV"], tex.inputs["Vector"])
        tex.location = (-760, -430)
        normal = nodes.new("ShaderNodeNormalMap")
        normal.location = (-220, -370)
        links.new(tex.outputs["Color"], normal.inputs["Color"])
        normal_in = _input(bsdf, "Normal")
        if normal_in:
            links.new(normal.outputs["Normal"], normal_in)

    if len(obj.data.materials):
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    obj["san_preview_material"] = mat.name
    return mat, images


def apply_companion_textures_from_paths(obj, model_path, unit_id=None):
    paths = find_companion_texture_paths(model_path)
    if not paths:
        return None, {}, {}
    blobs = {kind: path.read_bytes() for kind, path in paths.items()}
    labels = {kind: str(path) for kind, path in paths.items()}
    unit_id = unit_id or Path(model_path).stem.replace("_lod0", "")
    mat, images = apply_sanctuary_material(obj, unit_id, blobs, labels)
    return mat, images, paths
