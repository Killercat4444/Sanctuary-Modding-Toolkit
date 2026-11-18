"""Stage a game-format asset pack as a separate, editable project."""
from pathlib import Path, PurePosixPath
import shutil
import stat
import tempfile
import zipfile
import re

from . import mod_projects as mp


def safe_relative(name):
    path = PurePosixPath(name.replace('\\', '/'))
    if path.is_absolute() or not path.parts or any(
        p in ('.', '..') or ':' in p or p.endswith((' ', '.')) or
        re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p)
        for p in path.parts
    ): raise ValueError('Unsafe archive path: ' + name)
    return path


def copy_pack(source, destination, default_root='Units'):
    seen = set()
    def target_for(name):
        path = safe_relative(name)
        parts = list(path.parts)
        roots = {v.lower():v for v in ('Units','Lua','UI','VFX','Environment','Audio','Projectiles')}
        if parts[0].lower() in roots: parts[0] = roots[parts[0].lower()]
        else: parts.insert(0, default_root)
        # Windows paths are case insensitive; do not silently overwrite collisions.
        key = '/'.join(parts).casefold()
        if key in seen: raise ValueError('Duplicate archive path: ' + name)
        seen.add(key)
        target = destination.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        return target
    if source.is_dir():
        for file in source.rglob('*'):
            if file.is_symlink(): raise ValueError('Linked files are not supported in imported packs')
            if file.is_file(): shutil.copy2(file, target_for(file.relative_to(source).as_posix()))
    else:
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                safe_relative(info.filename)
                if stat.S_ISLNK(info.external_attr >> 16): raise ValueError('Archive links are not supported')
                if info.is_dir(): continue
                target = target_for(info.filename)
                with archive.open(info) as src, target.open('wb') as dst: shutil.copyfileobj(src, dst)


def import_sanpack(context, source):
    source = Path(source).resolve()
    s = context.scene.sanctuary_toolkit
    library = mp.library_root(context); library.mkdir(parents=True, exist_ok=True)
    name = mp._clean_name(s.mod_project_name or source.stem)
    destination = library / name
    number = 2
    while destination.exists():
        destination = library / f'{name} Imported {number}'; number += 1
    # Nothing becomes an active project until every file has been checked/copied.
    with tempfile.TemporaryDirectory(prefix='.smt_import_', dir=library) as temporary:
        stage = Path(temporary) / 'project'; stage.mkdir()
        copy_pack(source, stage)
        if s.mod_import_companions:
            siblings = {p.name.lower():p for p in source.parent.iterdir()}
            lua = siblings.get('lua')
            if lua and lua.is_dir(): copy_pack(lua, stage, 'Lua')
            ui = siblings.get('ui.sanpack')
            if ui and ui != source: copy_pack(ui, stage, 'UI')
        for folder in ('Units', 'Lua', 'UI'): (stage/folder).mkdir(exist_ok=True)
        units = {}
        for file in (stage/'Lua').rglob('*.santp'):
            uid = file.stem.lower()
            if re.fullmatch(r'u[a-z]{2}\d{4}', uid):
                units[uid] = {'unit_id':uid, 'template':file.relative_to(stage).as_posix()}
        for folder in (stage/'Units').iterdir():
            if folder.is_dir() and re.fullmatch(r'u[a-z]{2}\d{4}', folder.name.lower()):
                units.setdefault(folder.name.lower(), {'unit_id':folder.name.lower()})
        mp.write_manifest(stage, {'name':destination.name, 'tagged_faction':s.mod_tagged,
            'faction_name':s.mod_faction_name, 'faction_tag':s.mod_faction_tag,
            'units':list(units.values()), 'files':{'units':'Units','lua':'Lua','ui':'UI'},
            'imported_from':str(source)})
        from .project_blueprints import ensure_project_templates, game_root
        ensure_project_templates(stage, game_root(context))
        from .unit_icons import repair_missing
        repair_missing(stage)
        stage.rename(destination)
    s.mod_active_path = str(destination)
    return destination
