import json
import re
import struct
import zipfile
from pathlib import Path

import bpy
import numpy as np

from .utils import console, get_addon_preferences, resolve_engine_root

DDS_MAGIC = b"DDS "
FOURCC_DXT5 = b"DXT5"


def _rgb_to_565(rgb):
    """Match Pillow's BCN encoder: truncate 8-bit RGB to 5:6:5."""
    rgb = np.asarray(rgb, dtype=np.uint16)
    return (
        ((rgb[..., 0] >> 3) << 11)
        | ((rgb[..., 1] >> 2) << 5)
        | (rgb[..., 2] >> 3)
    ).astype(np.uint16)


def _rgb_from_565(c):
    """Expand 5:6:5 exactly as Pillow's BCN encoder does."""
    c = np.asarray(c, dtype=np.uint16)
    r = ((c & 0xF800) >> 8).astype(np.uint16)
    r |= r >> 5
    g = ((c & 0x07E0) >> 3).astype(np.uint16)
    g |= g >> 6
    b = ((c & 0x001F) << 3).astype(np.uint16)
    b |= b >> 5
    return np.stack((r, g, b), axis=-1).astype(np.int16)


def _encode_bc3_blocks(blocks):
    """Encode Nx16 RGBA blocks byte-for-byte like Pillow's DXT5 writer."""
    blocks = np.asarray(blocks, dtype=np.uint8)
    n = len(blocks)

    # Pillow records the minimum endpoint first and maximum second. Its selector
    # thresholds are deliberately simple and are preserved here exactly; this is
    # what makes the toolkit output identical to the known-good DDS files.
    alpha = blocks[:, :, 3].astype(np.int16)
    a0 = alpha.min(axis=1).astype(np.uint8)
    a1 = alpha.max(axis=1).astype(np.uint8)
    denominator = (a1.astype(np.int16) - a0.astype(np.int16)).astype(np.float32)
    distance = np.zeros_like(alpha, dtype=np.float32)
    nonzero = denominator != 0
    distance[nonzero] = (
        np.abs(alpha[nonzero] - a0[nonzero, None]).astype(np.float32)
        / denominator[nonzero, None]
        * 10.0
    )
    aidx = np.where(
        alpha == 0,
        6,
        np.where(
            alpha == 255,
            7,
            np.where(distance < 3.0, 2, np.where(distance < 5.0, 3, np.where(distance < 7.0, 4, 5))),
        ),
    ).astype(np.uint64)
    packed_a = np.zeros(n, dtype=np.uint64)
    for i in range(16):
        packed_a |= (aidx[:, i] & 7) << np.uint64(3 * i)
    aidx_bytes = packed_a.view(np.uint8).reshape(n, 8)[:, :6]

    rgb = blocks[:, :, :3]
    encoded = _rgb_to_565(rgb)
    c1 = encoded.min(axis=1)
    c0 = encoded.max(axis=1)
    minimum_rgb = _rgb_from_565(c1)
    maximum_rgb = _rgb_from_565(c0)
    channel_denominator = np.abs(maximum_rgb - minimum_rgb).astype(np.float32)
    enabled = channel_denominator != 0
    enabled_count = enabled.sum(axis=1)
    component_distance = np.abs(rgb.astype(np.int16) - minimum_rgb[:, None, :]).astype(np.float32)
    component_distance = np.divide(
        component_distance,
        channel_denominator[:, None, :],
        out=np.zeros_like(component_distance),
        where=enabled[:, None, :],
    )
    color_distance = component_distance.sum(axis=2)
    usable = enabled_count != 0
    color_distance[usable] *= 6.0 / enabled_count[usable, None]
    cidx = np.zeros((n, 16), dtype=np.uint32)
    cidx[usable] = np.where(
        color_distance[usable] < 1.0,
        1,
        np.where(color_distance[usable] < 3.0, 3, np.where(color_distance[usable] < 5.0, 2, 0)),
    ).astype(np.uint32)
    packed_c = np.zeros(n, dtype=np.uint32)
    for i in range(16):
        packed_c |= (cidx[:, i] & 3) << np.uint32(2 * i)

    out = np.empty((n, 16), dtype=np.uint8)
    out[:, 0] = a0
    out[:, 1] = a1
    out[:, 2:8] = aidx_bytes
    out[:, 8:10] = c0.view(np.uint8).reshape(n, 2)
    out[:, 10:12] = c1.view(np.uint8).reshape(n, 2)
    out[:, 12:16] = packed_c.view(np.uint8).reshape(n, 4)
    return out.tobytes(order="C")


def encode_bc3(rgba, blocks_per_chunk=8192):
    """Encode an HxWx4 uint8 image as DXT5/BC3 blocks.

    The encoder is deterministic and standalone. It works in chunks so a 2K or
    4K texture does not allocate several hundred MB of temporary NumPy arrays in
    Blender.
    """
    rgba = np.asarray(rgba, dtype=np.uint8)
    h, w, c = rgba.shape
    if c != 4 or h % 4 or w % 4:
        raise ValueError("BC3 input must be RGBA and both dimensions multiples of 4")

    blocks = rgba.reshape(h // 4, 4, w // 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 4)
    payload = bytearray()
    for first in range(0, len(blocks), int(blocks_per_chunk)):
        payload += _encode_bc3_blocks(blocks[first:first + int(blocks_per_chunk)])
    return bytes(payload)


def _dds_header(width, height, mip_count, top_linear_size):
    full_mips = mip_count > 1
    flags = 0xA1007 if full_mips else 0x81007
    caps = 0x401008 if full_mips else 0x1000
    header = bytearray()
    header += DDS_MAGIC
    header += struct.pack(
        "<7I",
        124,
        flags,
        height,
        width,
        top_linear_size if full_mips else (width + 3) * 4,
        0,
        mip_count if full_mips else 0,
    )
    header += struct.pack("<11I", *([0] * 11))
    # DDS_PIXELFORMAT
    header += struct.pack("<II4sIIIII", 32, 0x4, FOURCC_DXT5, 32, 0, 0, 0, 0)
    header += struct.pack("<5I", caps, 0, 0, 0, 0)
    if len(header) != 128:
        raise AssertionError(f"DDS header is {len(header)} bytes, expected 128")
    return bytes(header)


def _srgb_to_linear(x):
    x = x.astype(np.float32) / 255.0
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(x):
    x = np.clip(x, 0.0, 1.0)
    y = np.where(x <= 0.0031308, x * 12.92, 1.055 * (x ** (1.0 / 2.4)) - 0.055)
    return np.clip(np.rint(y * 255.0), 0, 255).astype(np.uint8)


def _downsample_2x(img, kind):
    h, w, _ = img.shape
    if h == 1 and w == 1:
        return img
    nh = max(1, h // 2)
    nw = max(1, w // 2)
    # Mips below 4 are still encoded as one 4x4 BC block. Keep logical dimensions
    # here; padding occurs only at compression time.
    if h % 2 or w % 2:
        img = img[: nh * 2, : nw * 2]
    blocks = img.reshape(nh, 2, nw, 2, 4).transpose(0, 2, 1, 3, 4)

    if kind == "normal":
        vec = blocks[..., :3].astype(np.float32) / 127.5 - 1.0
        avg = vec.mean(axis=(2, 3))
        length = np.linalg.norm(avg, axis=2, keepdims=True)
        avg = avg / np.maximum(length, 1e-8)
        rgb = np.clip(np.rint((avg * 0.5 + 0.5) * 255.0), 0, 255).astype(np.uint8)
        alpha = np.full((nh, nw, 1), 255, dtype=np.uint8)
        return np.concatenate((rgb, alpha), axis=2)

    linear_rgb = _srgb_to_linear(blocks[..., :3])
    avg_linear = linear_rgb.mean(axis=(2, 3))
    rgb = _linear_to_srgb(avg_linear)
    if kind == "albedo":
        # Team selector: nearest preserves hard selector masks.
        alpha = blocks[:, :, 0, 0, 3:4].copy()
    else:  # mask
        alpha = np.clip(np.rint(blocks[..., 3].astype(np.float32).mean(axis=(2, 3))), 0, 255).astype(np.uint8)[..., None]
    return np.concatenate((rgb, alpha), axis=2)


def _pad_to_block(img):
    h, w, _ = img.shape
    ph = max(4, ((h + 3) // 4) * 4)
    pw = max(4, ((w + 3) // 4) * 4)
    if (ph, pw) == (h, w):
        return img
    # Repeat edge pixels for partial BC blocks.
    return np.pad(img, ((0, ph - h), (0, pw - w), (0, 0)), mode="edge")


def write_bc3_dds(path, rgba, kind="albedo", full_mips=False):
    rgba = np.asarray(rgba, dtype=np.uint8)
    h, w, _ = rgba.shape
    levels = [rgba]
    if full_mips:
        while levels[-1].shape[0] > 1 or levels[-1].shape[1] > 1:
            levels.append(_downsample_2x(levels[-1], kind))

    payloads = []
    for level in levels:
        payloads.append(encode_bc3(_pad_to_block(level)))
    header = _dds_header(w, h, len(levels), len(payloads[0]))
    Path(path).write_bytes(header + b"".join(payloads))
    return {
        "width": w,
        "height": h,
        "mips": len(levels),
        "bytes": 128 + sum(map(len, payloads)),
        "format": "BC3/DXT5",
    }


def _png_has_alpha(path):
    try:
        blob = Path(path).read_bytes()
        if blob[:8] == b"\x89PNG\r\n\x1a\n" and len(blob) >= 26:
            color_type = blob[25]
            return color_type in (4, 6) or b"tRNS" in blob
    except Exception:
        pass
    return False


def _lanczos(value):
    if value < -3.0 or value >= 3.0:
        return 0.0
    if value == 0.0:
        return 1.0
    first = np.sin(value * np.pi) / (value * np.pi)
    scaled = value / 3.0
    second = 1.0 if scaled == 0.0 else np.sin(scaled * np.pi) / (scaled * np.pi)
    return float(first * second)


def _pillow_lanczos_coefficients(input_size, output_size):
    """Build Pillow-compatible fixed-point Lanczos coefficients."""
    precision = 1 << 22
    scale = float(input_size) / float(output_size)
    filter_scale = max(scale, 1.0)
    support = 3.0 * filter_scale
    inverse_filter_scale = 1.0 / filter_scale
    bounds = []
    coefficients = []
    for output_index in range(output_size):
        center = (output_index + 0.5) * scale
        first = max(0, int(center - support + 0.5))
        last = min(input_size, int(center + support + 0.5))
        weights = np.array(
            [_lanczos((index + first - center + 0.5) * inverse_filter_scale) for index in range(last - first)],
            dtype=np.float64,
        )
        total = float(weights.sum())
        if total:
            weights /= total
        fixed = np.where(
            weights < 0.0,
            np.trunc(weights * precision - 0.5),
            np.trunc(weights * precision + 0.5),
        )
        bounds.append((first, last - first))
        coefficients.append(fixed.astype(np.int32))
    return bounds, coefficients


def _pillow_lanczos_resize_channel(channel, width, height):
    """Resize one uint8 channel using Pillow's fixed-point Lanczos behavior."""
    source = np.asarray(channel, dtype=np.uint8)
    if source.ndim != 2:
        raise ValueError("Expected one 8-bit image channel")
    if source.shape == (height, width):
        return source.copy()

    horizontal_bounds, horizontal_coefficients = _pillow_lanczos_coefficients(source.shape[1], width)
    temporary = np.empty((source.shape[0], width), dtype=np.uint8)
    for output_x, ((first, count), coefficient) in enumerate(zip(horizontal_bounds, horizontal_coefficients)):
        accumulated = (1 << 21) + np.sum(
            source[:, first:first + count].astype(np.int64) * coefficient[None, :].astype(np.int64),
            axis=1,
        )
        temporary[:, output_x] = np.clip(accumulated >> 22, 0, 255).astype(np.uint8)

    vertical_bounds, vertical_coefficients = _pillow_lanczos_coefficients(source.shape[0], height)
    output = np.empty((height, width), dtype=np.uint8)
    for output_y, ((first, count), coefficient) in enumerate(zip(vertical_bounds, vertical_coefficients)):
        accumulated = (1 << 21) + np.sum(
            temporary[first:first + count, :].astype(np.int64) * coefficient[:, None].astype(np.int64),
            axis=0,
        )
        output[output_y, :] = np.clip(accumulated >> 22, 0, 255).astype(np.uint8)
    return output


def _pillow_lanczos_resize_rgba(rgba, width, height):
    source = np.asarray(rgba, dtype=np.uint8)
    if source.shape[:2] == (height, width):
        return source.copy()
    return np.stack(
        [_pillow_lanczos_resize_channel(source[..., channel], width, height) for channel in range(4)],
        axis=2,
    )


def _load_png_rgba(path, target_size):
    path = str(Path(bpy.path.abspath(path)).expanduser())
    if not Path(path).is_file():
        raise FileNotFoundError(path)
    has_alpha = _png_has_alpha(path)
    img = bpy.data.images.load(path, check_existing=False)
    try:
        try:
            img.colorspace_settings.name = "Non-Color"
        except Exception:
            pass
        source_width, source_height = map(int, img.size)
        pixels = np.empty(source_width * source_height * 4, dtype=np.float32)
        try:
            img.pixels.foreach_get(pixels)
        except Exception:
            pixels[:] = np.asarray(img.pixels[:], dtype=np.float32)
        rgba = np.clip(
            np.rint(pixels.reshape(source_height, source_width, 4) * 255.0),
            0,
            255,
        ).astype(np.uint8)
        # Blender exposes image pixels bottom-up. Flip back so DDS preserves the
        # same visible orientation as the source PNG file.
        rgba = np.flipud(rgba).copy()
        if (source_width, source_height) != (target_size, target_size):
            rgba = _pillow_lanczos_resize_rgba(rgba, target_size, target_size)
        return rgba, has_alpha
    finally:
        bpy.data.images.remove(img)


def prepare_texture_rgba(path, kind, settings):
    rgba, has_alpha = _load_png_rgba(path, int(settings.texture_size))
    if kind == "albedo":
        mode = settings.albedo_alpha_mode
        if mode == "ZERO":
            rgba[..., 3] = 0
        elif mode == "OPAQUE":
            rgba[..., 3] = 255
        elif not has_alpha:
            # RGB PNGs must not accidentally make the whole unit team colored.
            rgba[..., 3] = 0
        return rgba

    if kind == "normal":
        rgba[..., 3] = 255
        return rgba

    if kind == "mask":
        if settings.mask_input_mode == "AO_ONLY":
            gray = rgba[..., 0].copy()
            out = np.zeros_like(rgba)
            out[..., 1] = gray
            out[..., 3] = int(round(float(settings.mask_default_smoothness) * 255.0))
            return out
        if not has_alpha:
            rgba[..., 3] = int(round(float(settings.mask_default_smoothness) * 255.0))
        return rgba

    raise ValueError(f"Unknown texture kind: {kind}")



def _png_signature_info(path):
    """Return (bit_depth, color_type) from PNG IHDR without Pillow."""
    try:
        data = Path(path).read_bytes()[:32]
        if len(data) >= 26 and data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
            return int(data[24]), int(data[25])
    except Exception:
        pass
    return None, None


def _looks_like_grayscale_png(path):
    _depth, ctype = _png_signature_info(path)
    return ctype in (0, 4)


def _looks_like_color_png(path):
    _depth, ctype = _png_signature_info(path)
    return ctype in (2, 3, 6)


def _validate_top_dxt5(path, expected_size=None):
    p = Path(path)
    try:
        blob = p.read_bytes()[:128]
    except Exception as exc:
        raise RuntimeError(f"DDS output was not created: {exc}")
    if len(blob) < 128 or blob[:4] != b"DDS ":
        raise RuntimeError("DDS writer did not produce a valid DDS header")
    width = struct.unpack_from("<I", blob, 16)[0]
    height = struct.unpack_from("<I", blob, 12)[0]
    pf_size, pf_flags, fourcc = struct.unpack_from("<II4s", blob, 76)
    if pf_size != 32 or not (pf_flags & 0x4) or fourcc != b"DXT5":
        raise RuntimeError(
            f"DDS writer produced FourCC={fourcc!r}, flags=0x{pf_flags:X} instead of DXT5/BC3"
        )
    expected_bytes = 128 + ((width + 3) // 4) * ((height + 3) // 4) * 16
    actual = p.stat().st_size
    if actual != expected_bytes:
        raise RuntimeError(f"Top-only DXT5 size mismatch: expected {expected_bytes}, got {actual}")
    if expected_size is not None and (width != int(expected_size) or height != int(expected_size)):
        raise RuntimeError(f"DDS dimensions are {width}x{height}, expected {expected_size}x{expected_size}")
    return {"width": width, "height": height, "bytes": actual}


def texture_output_names(output_dir, unit_id, engine_root=None):
    """Match the actual material references instead of guessing _albedo_team.

    A material in the export directory is authoritative. Otherwise use the
    selected installation's packed material. New units default to _Albedo.dds.
    Only unit-local references can be written into this LOD0 export directory.
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(unit_id)):
        raise ValueError("Texture export requires a safe unit name, such as ucl1501")
    names = {"albedo": f"{unit_id}_lod0_Albedo.dds", "mask": f"{unit_id}_lod0_mask.dds", "normal": f"{unit_id}_lod0_normal.dds"}
    material = None
    source = "default unit filenames"
    local_path = Path(output_dir) / f"{unit_id}_lod0.sanmaterial"
    if local_path.is_file():
        material = json.loads(local_path.read_text(encoding="utf-8-sig"))
        source = str(local_path)
    else:
        if engine_root is None:
            prefs = get_addon_preferences()
            engine_root = resolve_engine_root(prefs.sanctuary_root if prefs else "")
        if engine_root:
            pack = Path(engine_root) / "Sanctuary_Data/Gamedata/Units.sanpack"
            if pack.is_file():
                with zipfile.ZipFile(pack) as archive:
                    wanted = f"units/{unit_id}/lod0/{unit_id}_lod0.sanmaterial".lower()
                    member = next((name for name in archive.namelist() if name.lower() == wanted), None)
                    if member:
                        material = json.loads(archive.read(member).decode("utf-8-sig"))
                        source = f"{pack} > {member}"
    if material is not None:
        keys = {"_MainTex": "albedo", "_MaskMap": "mask", "_NormalMap": "normal"}
        prefix = f"units/{unit_id}/lod0/".lower()
        for texture in material.get("textures", []):
            kind = keys.get(texture.get("key"))
            if not kind:
                continue
            ref = str(texture.get("path", "")).replace("\\", "/")
            parts = ref.split("/")
            filename = parts[-1]
            unit_local = len(parts) == 1 or (len(parts) == 4 and ref.lower().startswith(prefix))
            if not unit_local or not re.fullmatch(r"[A-Za-z0-9_. -]+\.dds", filename, re.IGNORECASE) or filename.startswith("."):
                raise ValueError(f"Material references a shared/unsupported texture path: {ref!r}. Use a unit-local material before converting.")
            names[kind] = filename
        if len({name.casefold() for name in names.values()}) != len(names):
            raise ValueError("The material reuses one DDS filename for multiple texture slots")
    return names, source


def convert_texture_set(settings, output_dir, unit_id):
    outputs = {}
    full_mips = settings.texture_mips == "FULL"

    albedo_source = settings.texture_albedo_png
    mask_source = settings.texture_mask_png
    normal_source = settings.texture_normal_png

    # Strong auto-detection for the exact easy-to-make slot swap: an AO bake is
    # normally a grayscale PNG, while albedo is RGB/RGBA.  In the user's broken
    # export the 16-bit AO was in Albedo and the color albedo was in Mask/AO,
    # producing a white/grayscale albedo and albedo-red in mask G.
    if settings.mask_input_mode == "AO_ONLY" and albedo_source and mask_source:
        ap = Path(bpy.path.abspath(albedo_source)).expanduser()
        mp = Path(bpy.path.abspath(mask_source)).expanduser()
        if ap.is_file() and mp.is_file() and _looks_like_grayscale_png(ap) and _looks_like_color_png(mp):
            console(
                f"Detected swapped Albedo/AO slots ({ap.name} is grayscale; {mp.name} is color). Auto-correcting."
            )
            albedo_source, mask_source = mask_source, albedo_source
            settings.texture_albedo_png = albedo_source
            settings.texture_mask_png = mask_source

    names, material_source = texture_output_names(output_dir, unit_id)
    console(f"Texture filenames from {material_source}: {names}")
    slots = (("albedo", albedo_source, names["albedo"]),
             ("mask", mask_source, names["mask"]),
             ("normal", normal_source, names["normal"]))
    for kind, source, filename in slots:
        if not source:
            continue
        source_path = Path(bpy.path.abspath(source)).expanduser()
        if not source_path.is_file():
            raise FileNotFoundError(str(source_path))
        dest = Path(output_dir) / filename
        rgba = prepare_texture_rgba(source, kind, settings)
        stats = write_bc3_dds(dest, rgba, kind=kind, full_mips=full_mips)
        if full_mips:
            stats["encoder"] = "Toolkit Pillow-compatible BC3 (full-mip mode)"
        else:
            verified = _validate_top_dxt5(dest, int(settings.texture_size))
            stats.update(verified)
            stats["encoder"] = "Toolkit Pillow-compatible BC3"
        outputs[kind] = (dest, stats)
        stats["material_source"] = material_source
        console(f"Wrote {dest.name}: {stats}")
    return outputs

# -----------------------------------------------------------------------------
# DDS preview decoding (top mip only)
# -----------------------------------------------------------------------------

FOURCC_DXT1 = b"DXT1"


def _decode_565_array(c):
    c = np.asarray(c, dtype=np.uint16).astype(np.uint32)
    r = (((c >> 11) & 31) * 255 + 15) // 31
    g = (((c >> 5) & 63) * 255 + 31) // 63
    b = ((c & 31) * 255 + 15) // 31
    return np.stack((r, g, b), axis=-1).astype(np.uint8)


def _decode_bc_color_blocks(blocks, dxt1_alpha=False):
    """Decode the 8-byte DXT color half from Nx8 uint8 blocks.

    Returns Nx16x4 uint8. DXT5 always uses four-color interpolation. DXT1 can
    use the transparent 3-color mode when c0 <= c1.
    """
    blocks = np.asarray(blocks, dtype=np.uint8)
    n = len(blocks)
    c0 = blocks[:, 0].astype(np.uint16) | (blocks[:, 1].astype(np.uint16) << 8)
    c1 = blocks[:, 2].astype(np.uint16) | (blocks[:, 3].astype(np.uint16) << 8)
    p0 = _decode_565_array(c0)
    p1 = _decode_565_array(c1)
    pal = np.zeros((n, 4, 4), dtype=np.uint8)
    pal[:, 0, :3] = p0
    pal[:, 1, :3] = p1
    pal[:, 0:2, 3] = 255

    if dxt1_alpha:
        four = c0 > c1
        if np.any(four):
            pal[four, 2, :3] = ((2 * p0[four].astype(np.uint16) + p1[four].astype(np.uint16)) // 3).astype(np.uint8)
            pal[four, 3, :3] = ((p0[four].astype(np.uint16) + 2 * p1[four].astype(np.uint16)) // 3).astype(np.uint8)
            pal[four, 2:4, 3] = 255
        if np.any(~four):
            pal[~four, 2, :3] = ((p0[~four].astype(np.uint16) + p1[~four].astype(np.uint16)) // 2).astype(np.uint8)
            pal[~four, 2, 3] = 255
            pal[~four, 3, :] = 0
    else:
        pal[:, 2, :3] = ((2 * p0.astype(np.uint16) + p1.astype(np.uint16)) // 3).astype(np.uint8)
        pal[:, 3, :3] = ((p0.astype(np.uint16) + 2 * p1.astype(np.uint16)) // 3).astype(np.uint8)
        pal[:, 2:4, 3] = 255

    packed = (
        blocks[:, 4].astype(np.uint32)
        | (blocks[:, 5].astype(np.uint32) << 8)
        | (blocks[:, 6].astype(np.uint32) << 16)
        | (blocks[:, 7].astype(np.uint32) << 24)
    )
    shifts = (np.arange(16, dtype=np.uint32) * 2)[None, :]
    idx = ((packed[:, None] >> shifts) & 3).astype(np.intp)
    rows = np.arange(n, dtype=np.intp)[:, None]
    return pal[rows, idx]


def _decode_bc3_chunk(blocks):
    blocks = np.asarray(blocks, dtype=np.uint8)
    n = len(blocks)
    # DXT5 alpha palette.
    a0 = blocks[:, 0].astype(np.uint16)
    a1 = blocks[:, 1].astype(np.uint16)
    ap = np.zeros((n, 8), dtype=np.uint16)
    ap[:, 0] = a0
    ap[:, 1] = a1
    gt = a0 > a1
    for k in range(2, 8):
        if np.any(gt):
            ap[gt, k] = ((8 - k) * a0[gt] + (k - 1) * a1[gt]) // 7
    for k in range(2, 6):
        if np.any(~gt):
            ap[~gt, k] = ((6 - k) * a0[~gt] + (k - 1) * a1[~gt]) // 5
    ap[~gt, 6] = 0
    ap[~gt, 7] = 255

    packed_a = np.zeros(n, dtype=np.uint64)
    for i in range(6):
        packed_a |= blocks[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
    ashifts = (np.arange(16, dtype=np.uint64) * 3)[None, :]
    aidx = ((packed_a[:, None] >> ashifts) & 7).astype(np.intp)
    rows = np.arange(n, dtype=np.intp)[:, None]
    alpha = ap[rows, aidx].astype(np.uint8)

    rgba = _decode_bc_color_blocks(blocks[:, 8:16], dxt1_alpha=False)
    rgba[:, :, 3] = alpha
    return rgba


def decode_dds_top(blob, blocks_per_chunk=16384):
    """Decode the top mip of a DXT1 or DXT5 DDS into HxWx4 uint8 RGBA.

    This decoder is bundled so texture previews do not depend on Blender's DDS
    loader or on external Python packages. It intentionally decodes only mip 0,
    which is all the viewport preview needs.
    """
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        blob = bytes(blob)
    if len(blob) < 128 or blob[:4] != DDS_MAGIC:
        raise ValueError("Not a DDS file")
    height, width = struct.unpack_from("<II", blob, 12)
    if width <= 0 or height <= 0:
        raise ValueError("DDS has invalid dimensions")
    fourcc = bytes(blob[84:88])
    if fourcc not in (FOURCC_DXT5, FOURCC_DXT1):
        raise ValueError(f"Unsupported DDS format {fourcc!r}; preview supports DXT1 and DXT5")

    bw = (width + 3) // 4
    bh = (height + 3) // 4
    block_bytes = 16 if fourcc == FOURCC_DXT5 else 8
    count = bw * bh
    need = 128 + count * block_bytes
    if len(blob) < need:
        raise ValueError("DDS top mip is truncated")

    raw = np.frombuffer(blob, dtype=np.uint8, count=count * block_bytes, offset=128).reshape(count, block_bytes)
    decoded = np.empty((count, 16, 4), dtype=np.uint8)
    chunk = max(1, int(blocks_per_chunk))
    for first in range(0, count, chunk):
        part = raw[first:first + chunk]
        if fourcc == FOURCC_DXT5:
            decoded[first:first + len(part)] = _decode_bc3_chunk(part)
        else:
            decoded[first:first + len(part)] = _decode_bc_color_blocks(part, dxt1_alpha=True)

    # Blocks are ordered left-to-right, top-to-bottom. Convert the block raster
    # into an ordinary top-down HxW image.
    image = decoded.reshape(bh, bw, 4, 4, 4).transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return image[:height, :width].copy()
