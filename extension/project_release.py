"""Generic native Mod API releases, separate from legacy project adapters."""
import json
from pathlib import Path
import re
import tempfile
import zipfile
from .hosted_export import normalize_version, _wanted
from .remmy_export import ASSET_FOLDERS, LUA_SUFFIXES
from .studio_core import safe_member


def legacy_dycom(project):
    file=Path(project)/'Mod Manager Compatibility/export.json'
    if not file.is_file(): return False
    data=json.loads(file.read_text(encoding='utf-8-sig'))
    if data.get('profile')=='generic': return False
    # Existing release adapter is opt-in by its actual manifest, never by folder name.
    manifest=Path(project)/'Mod Manager Compatibility/mod.json'
    if not manifest.is_file():return False
    factions=json.loads(manifest.read_text(encoding='utf-8-sig')).get('factions',[])
    return any(f.get('tag')=='DYCOM' and len(f.get('commanders',[]))>1 for f in factions)


def manifest(project,version=None):
    from .mod_projects import read_manifest
    source=read_manifest(project)
    data=dict(source.get('release',{}))
    if not data:
        raise ValueError('Set up project publishing in Project > Faction & Release first.')
    data['version']=normalize_version(version or source.get('version','0.1'))
    required=('id','name','author','description')
    if any(not isinstance(data.get(k),str) or not data[k].strip() for k in required):
        raise ValueError('Publishing needs a mod ID, name, author and description.')
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}',data['id']): raise ValueError('Mod ID must start with a lowercase letter/number and use 1–64 letters, numbers, dots, underscores or dashes.')
    from .project_import import safe_relative
    safe_relative(data['id'])
    data.update(apiVersion=1,kind='gameplay',luaRoot='lua')
    faction=data.get('factions',[])
    if faction:
        if len(faction)!=1:raise ValueError('The guided faction setup supports one faction and one commander.')
        f=faction[0]
        if not re.fullmatch(r'[a-z0-9_-]{1,32}',f.get('key','')):raise ValueError('Faction key must be a lowercase ID (1–32 characters).')
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,31}',f.get('tag','')) or f['tag'] in ('EDA','CHOSEN','GUARD'):raise ValueError('Choose a unique custom faction tag, not a stock tag.')
        if not re.fullmatch(r'u[a-z]',f.get('unitPrefix','')) or f['unitPrefix'] in ('ue','uc','ug'):raise ValueError('Use a custom unit prefix such as um; ue/uc/ug belong to the game.')
        if f.get('looksLike') not in ('EDA','CHOSEN','GUARD'):raise ValueError('Choose a valid base visual faction.')
        if not f.get('name','').strip():raise ValueError('Faction display name is required.')
        commanders=f.get('commanders',[])
        if len(commanders)!=1 or not re.fullmatch(f['unitPrefix']+r'[a-z]\d{4}',commanders[0].get('unit','')):raise ValueError('Choose one saved commander using your faction prefix.')
        if not commanders[0].get('name','').strip():raise ValueError('Commander display name is required.')
        icon=safe_member(f.get('icon',''))
        iconpath=(Path(project)/icon).resolve()
        if Path(project).resolve() not in iconpath.parents or not icon.lower().endswith('.png') or not iconpath.is_file():raise ValueError('Save a faction PNG icon inside the project.')
        if f.get('ai'):
            folder=safe_member(f['ai'].get('folder',''))
            if not (Path(project)/'Lua'/folder).is_dir():raise ValueError('Faction AI folder does not exist under project Lua: '+folder)
    return data


def files_under(project,folder,suffixes=None):
    project=Path(project).resolve();root=project/folder
    files=[];seen=set()
    for file in sorted(root.rglob('*')):
        if not file.is_file() or not _wanted(file,root,suffixes):continue
        relative=file.relative_to(project).as_posix();safe_member(relative)
        if file.is_symlink() or project not in file.resolve().parents:raise ValueError('Linked/escaping project file: '+relative)
        key=relative.casefold()
        if key in seen:raise ValueError('Case-insensitive project filename collision: '+relative)
        seen.add(key);files.append(file)
    return files


def export_mm(project,engine,version=None,destination=None):
    project=Path(project).resolve();data=manifest(project,version)
    from .release_validation import check
    check(project,engine,(data.get('factions') or [None])[0]).require()
    slug=data['id'];target=Path(destination) if destination else project/'Releases'/f'{slug}-RemmyMM-{data["version"]}.zip'
    target.parent.mkdir(parents=True,exist_ok=True)
    lua=files_under(project,'Lua',LUA_SUFFIXES)
    art=[p for category in ASSET_FOLDERS for p in files_under(project,category)]
    plugins=files_under(project,'Plugins',{'.dll'})
    if len({p.name.casefold() for p in plugins})!=len(plugins):
        raise ValueError('Project plugin DLL names collide; use unique assembly filenames.')
    with tempfile.TemporaryDirectory(prefix='studio_release_') as directory:
        stage=Path(directory);pack=stage/(slug+'.sanpack')
        from .export_cache import pack_assets
        if art:pack_assets(project,art,pack,'generic-mm')
        built=stage/'release.zip'
        with zipfile.ZipFile(built,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
            z.writestr(slug+'/mod.json',json.dumps(data,indent=2))
            for p in lua:z.write(p,slug+'/lua/'+p.relative_to(project/'Lua').as_posix())
            if art:z.write(pack,slug+'/packs/'+pack.name,compress_type=zipfile.ZIP_STORED)
            for f in data.get('factions',[]):z.write(project/f['icon'],slug+'/'+f['icon'])
            # Declared project plugins only. Never discover or bundle arbitrary engine DLLs.
            for p in plugins:z.write(p,slug+'/'+p.name)
            z.writestr('README.txt',f'{data["name"]} {data["version"]}\n\nRecommended: install current Remmy Mod Manager with Mod API 1.3 or newer.\n'
                        f'Copy the {slug} folder into engine/SanctuaryMods, then pick it in the lobby Mods panel.\n'
                        'All players need the same release. This archive does not replace BepInEx or include a loose-Lua catcher.\n')
            z.writestr('installer.bat','@echo off\r\nsetlocal\r\n'
                        'echo Use Remmy Mod Manager for this package. Enter the Sanctuary engine folder.\r\n'
                        'set /p "STUDIO_ENGINE=Engine folder: "\r\n'
                        'if not exist "%STUDIO_ENGINE%\\Sanctuary.exe" (echo Invalid engine folder. & pause & exit /b 1)\r\n'
                        'if not exist "%STUDIO_ENGINE%\\BepInEx\\plugins\\Sanctuary.ModApi.dll" (echo Install Mod Manager first. & pause & exit /b 1)\r\n'
                        f'if exist "%STUDIO_ENGINE%\\SanctuaryMods\\{slug}" (echo Existing mod found. Update it through MM; this installer will not overwrite it. & pause & exit /b 1)\r\n'
                        f'xcopy "%~dp0{slug}" "%STUDIO_ENGINE%\\SanctuaryMods\\{slug}\\" /E /I\r\npause\r\n')
        with zipfile.ZipFile(built) as z:
            if z.testzip():raise ValueError('Release archive CRC verification failed')
        temporary=target.with_suffix(target.suffix+'.tmp');temporary.write_bytes(built.read_bytes());temporary.replace(target)
    return target,{'assets':len(art),'lua':len(lua),'plugins':len(plugins)}
