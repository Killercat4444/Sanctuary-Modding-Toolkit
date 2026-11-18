"""Three read-only vanilla framing references; never part of an export scene."""
from pathlib import Path
import tempfile
import zipfile
import bpy.utils.previews
from .dds_tools import decode_dds_top
from .game_browser import _write_png

REFERENCES = (('Land', 'ucl1001'), ('Air', 'uca1001'), ('Building', 'ucs1511'))
_previews = None
_temporary = None
_key = None
_error = ''


def clear():
    global _previews, _temporary, _key, _error
    if _previews is not None:
        bpy.utils.previews.remove(_previews)
    if _temporary is not None:
        _temporary.cleanup()
    _previews = _temporary = _key = None
    _error = ''


def load(engine):
    global _previews, _temporary, _key, _error
    archive = Path(engine)/'Sanctuary_Data/Gamedata/UI.sanpack' if engine else None
    key = (str(archive), archive.stat().st_mtime_ns) if archive and archive.is_file() else (str(engine), None)
    if key == _key:
        return [(label, _previews[uid].icon_id) for label, uid in REFERENCES] if _previews else []
    clear()
    _key = key
    if not archive or not archive.is_file():
        _error = 'Set the game folder to load vanilla references.'
        return []
    try:
        _temporary = tempfile.TemporaryDirectory(prefix='sanctuary-vanilla-icons-')
        _previews = bpy.utils.previews.new()
        with zipfile.ZipFile(archive) as z:
            members = {n.lower(): n for n in z.namelist()}
            for label, uid in REFERENCES:
                member = members['ui/sprites/icons/units/' + uid + '.dds']
                path = Path(_temporary.name)/(uid + '.png')
                rgba = decode_dds_top(z.read(member))
                # Stock DDS files retain unrelated RGB in fully transparent texels.
                # Clear only that invisible color for clean thumbnail filtering.
                rgba[rgba[..., 3] == 0, :3] = 0
                _write_png(path, rgba)
                _previews.load(uid, str(path), 'IMAGE')
        return [(label, _previews[uid].icon_id) for label, uid in REFERENCES]
    except Exception:
        clear()
        _key = key
        _error = 'Vanilla icon references unavailable in this game build.'
        return []


def draw(layout, engine):
    box = layout.box()
    box.label(text='Vanilla framing references', icon='IMAGE_REFERENCE')
    refs = load(engine)
    if not refs:
        box.label(text=_error, icon='INFO')
        return
    row = box.row(align=True)
    for label, icon in refs:
        column = row.column(align=True)
        column.template_icon(icon_value=icon, scale=4.0)
        column.label(text=label)
    box.label(text='Compare angle, framing and silhouette.')
    box.label(text='Reference only • excluded from renders and mods')
