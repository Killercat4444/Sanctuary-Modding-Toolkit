"""High-quality Sanctuary BC3/DXT5 writer using Pillow.

This module deliberately contains no Blender imports so dds_tools.py can run it
with either Blender's Python (when Pillow is installed there) or an ordinary
system Python that has Pillow installed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def _png_has_alpha(im: Image.Image) -> bool:
    return "A" in im.getbands() or "transparency" in im.info


def _resize_rgba(im: Image.Image, size: int) -> Image.Image:
    if im.size != (size, size):
        # This matches the conversion used for the known-good engineer DDS set.
        im = im.resize((size, size), Image.Resampling.LANCZOS)
    return im


def _gray8_from_image(im: Image.Image) -> Image.Image:
    """Convert grayscale/RGB PNG data to an 8-bit AO image without clipping I;16.

    Pillow's plain I;16 -> L conversion clips values >255. Sanctuary AO bakes are
    commonly 16-bit, so scale the full numeric range to 0..255 explicitly.
    """
    arr = np.asarray(im)
    if arr.ndim == 3:
        arr = arr[..., 0]
    if arr.dtype == np.uint16 or im.mode.startswith("I;16"):
        out = np.rint(arr.astype(np.float32) * (255.0 / 65535.0)).clip(0, 255).astype(np.uint8)
    elif np.issubdtype(arr.dtype, np.integer) and arr.dtype.itemsize > 1:
        info = np.iinfo(arr.dtype)
        out = np.rint(arr.astype(np.float32) * (255.0 / float(info.max))).clip(0, 255).astype(np.uint8)
    else:
        out = np.asarray(im.convert("L"), dtype=np.uint8)
    return Image.fromarray(out, "L")


def prepare_rgba(source: str | Path, kind: str, size: int, *,
                 albedo_alpha_mode: str = "ZERO",
                 mask_input_mode: str = "AO_ONLY",
                 mask_smoothness: int = 118) -> Image.Image:
    src = Image.open(source)
    had_alpha = _png_has_alpha(src)

    if kind == "mask" and mask_input_mode == "AO_ONLY":
        gray = _gray8_from_image(src)
        if gray.size != (size, size):
            gray = gray.resize((size, size), Image.Resampling.LANCZOS)
        g = np.asarray(gray, dtype=np.uint8)
        rgba = np.zeros((size, size, 4), dtype=np.uint8)
        rgba[..., 1] = g
        rgba[..., 3] = int(mask_smoothness)
        return Image.fromarray(rgba, "RGBA")

    im = _resize_rgba(src.convert("RGBA"), size)
    rgba = np.asarray(im, dtype=np.uint8).copy()

    if kind == "albedo":
        if albedo_alpha_mode == "ZERO":
            rgba[..., 3] = 0
        elif albedo_alpha_mode == "OPAQUE":
            rgba[..., 3] = 255
        elif not had_alpha:
            rgba[..., 3] = 0
    elif kind == "normal":
        rgba[..., 3] = 255
    elif kind == "mask":
        if not had_alpha:
            rgba[..., 3] = int(mask_smoothness)
    else:
        raise ValueError(f"Unknown texture kind: {kind}")

    return Image.fromarray(rgba, "RGBA")


def convert_one(source: str | Path, dest: str | Path, kind: str, size: int,
                *, albedo_alpha_mode: str = "ZERO",
                mask_input_mode: str = "AO_ONLY",
                mask_smoothness: int = 118) -> dict:
    im = prepare_rgba(
        source, kind, int(size),
        albedo_alpha_mode=albedo_alpha_mode,
        mask_input_mode=mask_input_mode,
        mask_smoothness=int(mask_smoothness),
    )
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Pillow's BCN encoder is the same DXT5 path used for the known-good GPT
    # engineer textures. It writes one top mip by default.
    im.save(dest, format="DDS", pixel_format="DXT5")
    header = dest.read_bytes()[:128]
    if len(header) < 128 or header[:4] != b"DDS " or header[84:88] != b"DXT5":
        raise RuntimeError(
            f"This Pillow build did not emit DXT5/BC3 (FourCC={header[84:88]!r}); refusing uncompressed DDS"
        )
    expected_bytes = 128 + ((im.width + 3) // 4) * ((im.height + 3) // 4) * 16
    if dest.stat().st_size != expected_bytes:
        raise RuntimeError(
            f"DXT5 payload size mismatch: expected {expected_bytes}, got {dest.stat().st_size}"
        )
    return {
        "width": im.width,
        "height": im.height,
        "mips": 1,
        "bytes": dest.stat().st_size,
        "format": "BC3/DXT5",
        "encoder": "Pillow BCN",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--kind", choices=("albedo", "mask", "normal"), required=True)
    ap.add_argument("--size", type=int, required=True)
    ap.add_argument("--albedo-alpha", default="ZERO")
    ap.add_argument("--mask-mode", default="AO_ONLY")
    ap.add_argument("--smoothness", type=int, default=118)
    ns = ap.parse_args()
    stats = convert_one(
        ns.source, ns.dest, ns.kind, ns.size,
        albedo_alpha_mode=ns.albedo_alpha,
        mask_input_mode=ns.mask_mode,
        mask_smoothness=ns.smoothness,
    )
    print(json.dumps(stats, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
