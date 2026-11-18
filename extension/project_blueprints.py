"""Project-owned unit blueprints. Game and deployed files are sources, never edit targets."""
from pathlib import Path
import re

from . import mod_projects as mp
from .studio_core import Blueprint, lua_compile
from .utils import get_addon_preferences, resolve_engine_root


def game_root(context):
    prefs = get_addon_preferences(context)
    return resolve_engine_root(prefs.sanctuary_root) if prefs else None


def checked_path(project, path, engine=None):
    project, path = Path(project).resolve(), Path(path).resolve()
    if project not in path.parents:
        raise ValueError('Lua path escapes its project: ' + str(path))
    if engine:
        engine = Path(engine).resolve()
        for protected in (engine / 'LJ', engine / 'Sanctuary_Data/Gamedata'):
            if path == protected or protected in path.parents:
                raise ValueError('The game installation cannot be used as a Lua editing destination')
    return path


def plan_template(project, uid, engine=None, source_text=None, source_path=None):
    if not re.fullmatch(r'u[a-z]{2}\d{4}', uid):
        raise ValueError('Invalid Unit ID: ' + str(uid))
    project = Path(project).resolve()
    mp.read_manifest(project)
    target = checked_path(project, project / 'Lua/common/units/unitsTemplates' / uid / (uid + '.santp'), engine)
    matches = [p for p in (project / 'Lua').rglob('*')
               if p.is_file() and p.stem.lower() == uid and p.suffix.lower() in ('.santp', '.lua')]
    if len(matches) > 1:
        raise ValueError('Multiple project Lua files exist for ' + uid + '; resolve that ambiguity first')
    existing = matches[0] if matches else None
    if existing:
        target = checked_path(project, existing, engine)
        blob = target.read_bytes()
    elif source_text is not None:
        blob = source_text.encode('utf-8')
    else:
        candidates = [Path(source_path)] if source_path else []
        if engine:
            base = Path(engine) / 'LJ/lua/common/units/unitsTemplates' / uid
            candidates += [base / (uid + '.santp'), base / (uid + '.lua')]
        donor = next((p for p in candidates if p.is_file()), None)
        if donor is None:
            raise ValueError('No project Lua or matching base-game Lua for ' + uid
                             + '. Choose an existing unit Lua in Studio before exporting this custom ID.')
        blob = donor.read_bytes()
    text = blob.decode('utf-8-sig')
    doc = Blueprint(text)
    if doc.scalar(('general', 'tpId')) != uid:
        raise ValueError('Lua tpId does not match ' + uid + '; existing files have not been overwritten')
    lua_compile(text, engine)
    return target, blob, existing is None, doc.scalar(('general', 'displayName'), uid)


def commit_plans(project, plans):
    manifest = mp.read_manifest(project)
    for uid, (path, blob, create, display_name) in plans.items():
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Exclusive creation: an edited file must never be overwritten.
            with path.open('xb') as stream:
                stream.write(blob)
        entry = next((u for u in manifest.setdefault('units', []) if u.get('unit_id') == uid), None)
        if entry is None:
            entry = {'unit_id': uid}; manifest['units'].append(entry)
        entry.update(template=path.relative_to(Path(project).resolve()).as_posix(), display_name=display_name)
    mp.write_manifest(project, manifest)


def ensure_template(project, uid, engine=None, source_text=None, source_path=None):
    plan = plan_template(project, uid, engine, source_text, source_path)
    commit_plans(project, {uid: plan})
    return plan[0]


def project_unit_ids(project):
    """Registered units and real exported content, never empty authoring placeholders."""
    project = Path(project).resolve()
    manifest = mp.read_manifest(project)
    ids = {u['unit_id'] for u in manifest.get('units', []) if u.get('unit_id')}
    for folder in (project / 'Units').glob('*'):
        if not folder.is_dir() or not re.fullmatch(r'u[a-z]{2}\d{4}', folder.name.lower()):
            continue
        # Match release art selection: dot metadata and bytecode caches do not ship.
        content = any(file.is_file() and not any(part.startswith('.') or part == '__pycache__'
            for part in file.relative_to(folder).parts) for file in folder.rglob('*'))
        if content:
            ids.add(folder.name.lower())
    return ids


def ensure_project_templates(project, engine=None):
    """Plan all missing copies first; an unresolved custom ID leaves every file untouched."""
    project = Path(project).resolve()
    ids = project_unit_ids(project)
    plans = {uid: plan_template(project, uid, engine) for uid in sorted(ids)}
    commit_plans(project, plans)
    return sum(plan[2] for plan in plans.values())


def require_project_templates(project, engine=None):
    """Read-only deployment gate: never silently ship a mesh without its own Lua."""
    project = Path(project).resolve()
    ids = project_unit_ids(project)
    for uid in sorted(ids):
        plan = plan_template(project, uid, engine)
        if plan[2]:
            raise ValueError('Missing project Lua for ' + uid
                             + '. Use Project > Create Missing Unit Lua Copies before deploying.')


def prepare_active_unit(context):
    s = context.scene.sanctuary_toolkit
    state = context.scene.sanctuary_studio
    project = mp.active_project(context)
    # Never export another unit's stale draft or silently replace a saved edit.
    if state.blueprint:
        if state.blueprint_path and project.resolve() not in Path(state.blueprint_path).resolve().parents:
            raise ValueError('The Lua draft belongs to another project. Load this unit\'s saved Lua from the selected project first; your old draft is preserved.')
        doc = Blueprint(state.blueprint.as_string())
        if doc.scalar(('general', 'tpId')) != s.unit_id:
            raise ValueError('The open Lua draft belongs to another unit; choose the correct Lua first')
        existing = plan_template(project, s.unit_id, game_root(context), source_text=state.blueprint.as_string())[0]
        if existing.is_file() and existing.read_text(encoding='utf-8-sig') != state.blueprint.as_string():
            raise ValueError('Save the edited Lua draft before exporting')
    value = state.blueprint.as_string() if state.blueprint else None
    path = ensure_template(project, s.unit_id, game_root(context), source_text=value)
    return path


def editable_record_path(context, record, payload):
    """Explicit browser action stages a copy, including for old mesh-only projects."""
    uid = record['unit_id']
    if record.get('record_key', '').startswith('project:') and not record.get('project_root'):
        # A cache from an older preview has no trustworthy owning project path.
        # Never substitute whatever unrelated project happens to be active.
        raise ValueError('Rebuild the Unit Browser index once to update this old project record')
    project = Path(record['project_root']) if record.get('project_root') else mp.active_project(context)
    source = record.get('template_path')
    root = game_root(context) or resolve_engine_root(payload.get('engine_root', ''))
    return ensure_template(project, uid, root, source_path=source)
