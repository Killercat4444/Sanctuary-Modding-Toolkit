"""Project-owned unit portrait pairs. Never deploys or opens a live archive."""
import json
from pathlib import Path
import re
import struct
import tempfile
import os

UNIT = re.compile(r'u[a-z]{2}\d{4}')


def owned(project, relative):
    root = Path(project).resolve()
    target = root / relative
    if root not in target.resolve().parents:
        raise ValueError('Icon path escapes the mod project: ' + str(target))
    return target


def paths(project, uid):
    uid = str(uid).lower()
    if not UNIT.fullmatch(uid):
        raise ValueError('Choose a valid unit ID before saving its icon')
    dds = owned(project, 'UI/Sprites/Icons/Units/' + uid + '.dds')
    return dds, dds.with_suffix('.sansprite')


def dimensions(blob):
    if len(blob) < 128 or blob[:4] != b'DDS ' or struct.unpack_from('<I', blob, 4)[0] != 124:
        raise ValueError('Invalid DDS icon header')
    height, width = struct.unpack_from('<II', blob, 12)
    if not (0 < width <= 8192 and 0 < height <= 8192):
        raise ValueError('Invalid DDS icon dimensions')
    codec = blob[84:88]
    block = {b'DXT1': 8, b'DXT3': 16, b'DXT5': 16}.get(codec)
    if block and len(blob) < 128 + ((width+3)//4)*((height+3)//4)*block:
        raise ValueError('Truncated DDS icon')
    return width, height


def descriptor(uid, width, height):
    return {'texture': {'path': 'UI/Sprites/Icons/Units/' + uid + '.dds'},
            'wrapMode': 'Clamp', 'rect': {'x': 0.0, 'y': 0.0, 'width': float(width), 'height': float(height)},
            'pivot': {'x': width/2.0, 'y': height/2.0}, 'pixelsPerUnit': 100.0,
            'extrude': 1, 'meshType': 'Tight',
            'border': {'x': 0.0, 'y': 0.0, 'z': 0.0, 'w': 0.0}, 'generateFallbackPhysicsShape': False}


def atomic(path, blob):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.icon-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(blob)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def save_png(project, uid, source):
    """Reuse the existing DDS codec; keep RGBA and native square render size."""
    from .dds_tools import _load_png_rgba, write_bc3_dds
    source = Path(source)
    blob = source.read_bytes()
    if blob[:8] != b'\x89PNG\r\n\x1a\n' or blob[12:16] != b'IHDR':
        raise ValueError('Choose a valid PNG image')
    width, height = struct.unpack_from('>II', blob, 16)
    if width != height or width not in (64, 128, 256, 512, 1024, 2048):
        raise ValueError('Unit icons must be square PNGs: 64, 128, 256, 512, 1024 or 2048 pixels')
    dds, sprite = paths(project, uid)
    owned(project, sprite.relative_to(Path(project).resolve()))
    png = owned(project, 'Source/Icons/' + dds.stem + '.png')
    rgba, _ = _load_png_rgba(str(source), width)
    # Complete conversion before replacing any existing project icon.
    with tempfile.TemporaryDirectory(prefix='sanctuary-icon-') as tmp:
        output = Path(tmp)/'icon.dds'
        write_bc3_dds(output, rgba, kind='albedo', full_mips=False)
        encoded = output.read_bytes()
        assert dimensions(encoded) == (width, height)
    atomic(png, blob)
    atomic(dds, encoded)
    atomic(sprite, json.dumps(descriptor(dds.stem, width, height), indent=2).encode('utf-8'))
    validate_pair(project, dds.stem)
    return png, dds, sprite


def validate_pair(project, uid):
    dds, sprite = paths(project, uid)
    owned(project, sprite.relative_to(Path(project).resolve()))
    if not dds.is_file() or not sprite.is_file():
        raise ValueError(f'{uid}: icon needs both DDS and .sansprite. Use Save Unit Icon or Repair Missing Icon Sprites.')
    width, height = dimensions(dds.read_bytes())
    data = json.loads(sprite.read_text(encoding='utf-8-sig'))
    if data.get('texture', {}).get('path', '').lower() != f'ui/sprites/icons/units/{dds.stem}.dds':
        raise ValueError(f'{uid}: sprite points to a different icon')
    rect = data.get('rect', {})
    if (rect.get('x'), rect.get('y'), rect.get('width'), rect.get('height')) != (0, 0, width, height):
        raise ValueError(f'{uid}: sprite rectangle must match DDS size {width} x {height}')
    return width, height


def validate_project(project):
    folder = owned(project, 'UI/Sprites/Icons/Units')
    ids = {p.stem for p in folder.glob('*') if p.suffix.lower() in ('.dds', '.sansprite') and UNIT.fullmatch(p.stem)}
    for uid in sorted(ids):
        validate_pair(project, uid)
    for png in owned(project, 'Source/Icons').glob('*.png'):
        if UNIT.fullmatch(png.stem) and png.stem not in ids:
            raise ValueError(png.stem + ': PNG has not been saved as a game icon. Use Save Unit Icon.')
    return len(ids)


def repair_missing(project):
    """Migrate DDS-only projects without re-rendering or recompressing images."""
    plan = []
    for dds in owned(project, 'UI/Sprites/Icons/Units').glob('*.dds'):
        if not UNIT.fullmatch(dds.stem):
            continue
        _, sprite = paths(project, dds.stem)
        owned(project, sprite.relative_to(Path(project).resolve()))
        if sprite.exists():
            validate_pair(project, dds.stem)
        else:
            width, height = dimensions(dds.read_bytes())
            plan.append((sprite, descriptor(dds.stem, width, height)))
    for path, data in plan:
        atomic(path, json.dumps(data, indent=2).encode('utf-8'))
    return len(plan)
