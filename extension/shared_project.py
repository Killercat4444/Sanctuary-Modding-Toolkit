"""One project selection for all .blend files; preserve per-file unit assets."""
import json
import os
from pathlib import Path
import shutil
import tempfile

import bpy
from bpy.types import Operator
from .utils import get_addon_preferences

_LOADED = False
_SHARED = False
_VALUE = ''
_ERROR = ''


def settings_path():
    return Path(bpy.utils.user_resource('CONFIG')) / 'sanctuary_modding_toolkit' / 'project_defaults.json'


def reset():
    global _LOADED, _SHARED, _VALUE, _ERROR
    _LOADED, _SHARED, _VALUE, _ERROR = False, False, '', ''


def ensure_loaded():
    global _LOADED, _SHARED, _VALUE, _ERROR
    if _LOADED:
        return
    _LOADED = True
    path = settings_path()
    if not path.is_file():
        return
    _SHARED = True
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict) or data.get('version') != 1 or not isinstance(data.get('project'), str):
            raise ValueError('Invalid project-defaults file.')
        value = data['project']
        if value and not Path(value).is_absolute():
            raise ValueError('Saved project must have an absolute path.')
        _VALUE = value
    except (OSError, ValueError, TypeError) as exc:
        _ERROR = 'Saved global project could not be read. Choose your project again. ' + str(exc)


def get(settings):
    if get_addon_preferences() is None:
        return settings.get('mod_active_path', '')  # Script-only registration.
    ensure_loaded()
    return _VALUE if _SHARED else settings.get('mod_active_path', '')


def validate_destination(value, prefs):
    from .mod_projects import read_manifest
    path = Path(value)
    read_manifest(path)
    for name in ('mods_root', 'sanctuary_root'):
        raw = getattr(prefs, name, '')
        if not raw:
            continue
        protected = Path(bpy.path.abspath(raw)).expanduser().resolve()
        if path == protected or protected in path.parents:
            raise ValueError('Select an authoring project outside live Mods and the game installation.')
    return path


def write(value):
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='project_defaults_', suffix='.tmp', delete=False) as f:
            temporary = Path(f.name)
            json.dump({'version': 1, 'project': value}, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        if path.is_file():
            shutil.copy2(path, path.with_suffix('.json.bak'))
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def set(settings, value):
    global _LOADED, _SHARED, _VALUE, _ERROR
    # Keep a compatibility copy; opening an old .blend does NOT call this setter
    # and therefore cannot replace the global project with its older saved copy.
    prefs = get_addon_preferences()
    if prefs is None:
        settings['mod_active_path'] = value
        return
    resolved = str(Path(bpy.path.abspath(value)).expanduser().resolve()) if value.strip() else ''
    ensure_loaded()
    current = _VALUE if _SHARED else settings.get('mod_active_path', '')
    settings['mod_active_path'] = resolved
    # Export scripts and Blender UI redraws can reassign the already-selected
    # project. Revalidating and fsyncing the same global setting is unnecessary
    # and, on large mod libraries, can stall an otherwise quick unit export.
    if resolved == current and not _ERROR:
        _LOADED, _SHARED, _VALUE = True, True, resolved
        return
    _LOADED, _SHARED, _VALUE, _ERROR = True, True, resolved, ''
    try:
        if resolved:
            validate_destination(resolved, prefs)
        write(resolved)
    except (OSError, ValueError, TypeError) as exc:
        # Keep invalid/incomplete input visible; never fall back to a different
        # project silently. Export is blocked until this selection is repaired.
        _ERROR = 'Project selection was not saved: ' + str(exc)


def check():
    if get_addon_preferences() is None:
        return
    ensure_loaded()
    if _ERROR:
        raise ValueError(_ERROR)


class SANCTUARY_OT_remember_project(Operator):
    bl_idname = 'sanctuary.remember_project'
    bl_label = 'Use This Project for All Files'
    bl_description = 'Remember the selected authoring project across .blend files and Blender restarts'

    def execute(self, context):
        try:
            settings = context.scene.sanctuary_toolkit
            if not settings.mod_active_path:
                raise ValueError('Choose an authoring project first.')
            set(settings, settings.mod_active_path)
            check()
            self.report({'INFO'}, 'Project remembered for all .blend files. Unit IDs, meshes and armatures remain per file.')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def draw_note(layout, context):
    if get_addon_preferences(context) is None:
        return
    ensure_loaded()
    if not _SHARED:
        layout.operator('sanctuary.remember_project', icon='FILE_TICK')
    elif _ERROR:
        layout.label(text='Project not saved. Check the selected folder.', icon='ERROR')
        layout.operator('sanctuary.remember_project', text='Retry Saving Project', icon='FILE_REFRESH')
    else:
        layout.label(text='Shared across .blend files • saved automatically', icon='CHECKMARK')


def register():
    reset()
    bpy.utils.register_class(SANCTUARY_OT_remember_project)


def unregister():
    bpy.utils.unregister_class(SANCTUARY_OT_remember_project)
    reset()
