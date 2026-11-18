"""Preserving standalone bootstrap, owned cache packages and direct deployment."""
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import zipfile
from .studio_core import Blueprint, lua_compile, safe_member
from .project_release import manifest, files_under
from .remmy_export import ASSET_FOLDERS, LUA_SUFFIXES

FORMAT='Sanctuary Studio standalone 1'
RUNTIME=Path(__file__).parent/'runtime/SanctuaryStudioStandalone.dll'
RUNTIME_REL='BepInEx/patchers/SanctuaryStudio/SanctuaryStudioStandalone.dll'
REQUIRED_BOOTSTRAP=('BepInEx/core/BepInEx.dll','BepInEx/core/BepInEx.Preloader.dll',
                    'BepInEx/core/Mono.Cecil.dll','BepInEx/core/0Harmony.dll','winhttp.dll','doorstop_config.ini')


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect_runtime(engine,mods):
    if not engine:raise ValueError('Set the Sanctuary game folder first.')
    engine=Path(engine).resolve();mods=Path(mods).resolve()
    if not (engine/'Sanctuary.exe').is_file():raise ValueError('Not a Sanctuary engine folder.')
    present=any((engine/p).exists() for p in REQUIRED_BOOTSTRAP) or (engine/'BepInEx').exists()
    missing=[p for p in REQUIRED_BOOTSTRAP if not (engine/p).is_file()]
    corrupt=[p for p in REQUIRED_BOOTSTRAP if p.endswith('.dll') and (engine/p).is_file() and (engine/p).read_bytes()[:2]!=b'MZ']
    catchers=[]
    for folder in ('patchers','plugins'):
        for dll in (engine/'BepInEx'/folder).rglob('*.dll'):
            blob=dll.read_bytes()
            if dll.name.lower()=='sanctuary.modapi.dll':kind='MM framework (use MM export; does not mount arbitrary Mods/Lua)'
            elif b'SanctuaryStudioStandalone' in blob:kind='Studio owned-cache loader'
            elif (b'pathToFileContents' in blob or b'CreateFileCache' in blob or b'TryGetFileContent' in blob) and any(t in blob for t in (b'Lua',b'Cache',b'Overlay')):
                kind='Existing cache/Lua integration; capabilities need explicit verification'
            else:continue
            catchers.append({'path':str(dll),'kind':kind,'sha256':digest(dll)})
    return {'engine':str(engine),'mods':str(mods),'bepinex':('absent' if not present else 'incomplete' if missing or corrupt else 'present'),
            'missing':missing if present else [],'corrupt':corrupt,'lua_integrations':catchers,
            'note':'Existing infrastructure is never replaced. Unknown/overlapping cache integrations stop standalone/direct install; MM is recommended.'}


def bootstrap(bundle):
    if not bundle or not Path(bundle).is_file():raise ValueError('Choose a trusted Remmy MM bootstrap ZIP in Project > Faction & Release. Existing game/SCOM binaries are not repackaged.')
    output={};seen=set()
    with zipfile.ZipFile(bundle) as z:
        for entry in z.infolist():
            if entry.is_dir():continue
            path=safe_member(entry.filename);key=path.casefold()
            if key in seen:raise ValueError('Duplicate bootstrap path: '+path)
            seen.add(key)
            if path.startswith('BepInEx/core/') and path.endswith(('.dll','.xml')) or path in ('winhttp.dll','doorstop_config.ini','.doorstop_version'):
                if entry.file_size>50*1024*1024:raise ValueError('Unexpected bootstrap file size: '+path)
                blob=z.read(entry)
                if path.endswith('.dll') and blob[:2]!=b'MZ':raise ValueError('Invalid bootstrap DLL: '+path)
                output[path]=blob
        for required in REQUIRED_BOOTSTRAP:
            if required not in output:raise ValueError('MM ZIP is missing bootstrap file: '+required)
    return output


def unit_appends(units,models):
    """Unit discovery and stock model-marker fallback, never faction slots."""
    appends={}
    from .vfx_documents import lua_value
    appends['common/units/availableUnits.lua']='\n-- Studio project unit access\n'+'\n'.join('AvailableUnits['+lua_value(uid)+'] = true' for uid in sorted(units))+'\n'
    appends['common/units/QuickLaunchUnits.lua']='\n-- Studio project unit access\n'+'\n'.join('QuickLaunchUnits['+lua_value(uid)+'] = true' for uid in sorted(units))+'\n'
    if models:appends['common/loading/constructionSpawnPositionLoader.lua']=f'''
-- Stock factory marker inference follows the MODEL's faction naming convention.
-- Explicit project platform bones are preserved. No faction slots are changed.
local StudioBorrowedModels = {lua_value(models)}
local StudioOriginalSpawn = GetConstructorSpawnBones
function GetConstructorSpawnBones(tp, skeletonBones)
    local model = StudioBorrowedModels[tp.general.tpId]
    if not model or tp.construction.platformBoneName then
        return StudioOriginalSpawn(tp, skeletonBones)
    end
    local id = tp.general.tpId
    tp.general.tpId = model
    local out = {{pcall(StudioOriginalSpawn, tp, skeletonBones)}}
    tp.general.tpId = id
    if not out[1] then error(out[2]) end
    return unpack(out, 2)
end
'''
    return appends


def stage_project(project,engine,version,stage):
    project=Path(project).resolve();engine=Path(engine).resolve();stage=Path(stage)
    data=manifest(project,version);f=(data.get('factions') or [None])[0]
    if f:
        raise ValueError('New factions must use MM export: the game has hard-coded standalone faction slots, and overlapping slots can hide factions. MM assigns them dynamically.')
    if files_under(project,'Plugins',{'.dll'}):
        raise ValueError('This project declares plugin DLLs. Use MM export; standalone cannot assume a Mod API plugin is a BepInEx plugin or silently omit it.')
    from .release_validation import check
    check(project,engine,f).require()
    if not RUNTIME.is_file() or RUNTIME.read_bytes()[:2]!=b'MZ':raise ValueError('Bundled Studio runtime is missing. Reinstall the complete addon ZIP.')
    slug=data['id'];owned=stage/'engine/StudioStandalone'/slug;owned.mkdir(parents=True)
    lua=files_under(project,'Lua',LUA_SUFFIXES)
    for source in lua:
        relative=source.relative_to(project/'Lua').as_posix().casefold()
        if relative in ('common/systems/factions.lua','append/common/systems/factions.lua'):
            raise ValueError('Standalone faction-slot overrides are not supported. Register new factions through MM instead.')
        if 'unitstemplates/' in relative and source.suffix.lower()=='.santp':
            tags=Blueprint(source.read_text(encoding='utf-8-sig')).strings(('tags',))
            if not set(tags).intersection({'EDA','CHOSEN','GUARD'}):
                raise ValueError(source.stem+': standalone units must use an existing game faction tag. Use MM for a custom faction.')
    units={p.stem for p in lua if 'unitsTemplates' in p.parts and p.suffix.lower()=='.santp'}
    models={}
    for p in lua:
        if p.stem not in units or p.suffix.lower()!='.santp':continue
        doc=Blueprint(p.read_text(encoding='utf-8-sig'));model=doc.scalar(('general','modelTpId'))
        if isinstance(model,str) and model[:2] in ('ue','uc','ug') and p.stem[:2] not in ('ue','uc','ug'):
            models[p.stem]=model
    appends=unit_appends(units,models) if units else {}
    with zipfile.ZipFile(owned/'cache.zip','w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
        for p in lua:
            relative=p.relative_to(project/'Lua').as_posix()
            if relative.startswith('append/') and relative[7:] in appends:
                # Combine project and generated appends before writing one cache record.
                appends[relative[7:]]=p.read_text(encoding='utf-8-sig')+'\n'+appends[relative[7:]]
            else:z.write(p,'files/LJ/lua/'+relative)
        from .standalone_export import _merge_lua_append
        for relative,source in sorted(appends.items()):
            target=engine/'LJ/lua'/relative
            if not target.is_file():raise ValueError('Game lacks required standalone append target: '+relative)
            lua_compile(_merge_lua_append(target.read_bytes(),source.encode()).decode('utf-8-sig'),engine)
            z.writestr('files/LJ/lua/append/'+relative,source)
    descriptor={'format':FORMAT,'id':slug,'version':data['version'],'gameAssemblySha256':digest(engine/'Sanctuary_Data/Managed/Trebuchet.dll'),
                'factions':data.get('factions',[])}
    (owned/'runtime.json').write_text(json.dumps(descriptor,indent=2),encoding='utf-8')
    catcher=stage/'engine'/RUNTIME_REL;catcher.parent.mkdir(parents=True);shutil.copy2(RUNTIME,catcher)
    art=[p for folder in ASSET_FOLDERS for p in files_under(project,folder)]
    if art:
        from .export_cache import pack_assets
        pack=stage/'Mods'/(slug+'.sanpack');pack.parent.mkdir(parents=True,exist_ok=True)
        pack_assets(project,art,pack,'generic-standalone')
    # Keep inspectable loose source under the project's own folder; active cache is explicit.
    for p in lua:
        target=stage/'Mods'/slug/'Lua'/p.relative_to(project/'Lua');target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
    ai=0
    if f and f.get('ai'):
        relative=f['ai']['folder'];root=project/'Lua'/relative
        for p in files_under(project,'Lua/'+relative,LUA_SUFFIXES):
            target=stage/'engine/LJ/lua'/relative/p.relative_to(root);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target);ai+=1
    return data,{'packs':int(bool(art)),'lua':len(lua)+len(appends),'ai':ai}


def require_runtime(report):
    if report['bepinex']=='incomplete':raise ValueError('Existing BepInEx is incomplete or corrupt; it will NOT be replaced. Repair it with its own installer, then recheck.')
    for integration in report['lua_integrations']:
        if integration['kind']!='Studio owned-cache loader':
            raise ValueError('Existing Lua integration preserved: '+integration['path']+'. This generic standalone package cannot verify its faction/cache capabilities. Use MM export or an explicitly compatible standalone adapter.')
        if integration['sha256']!=digest(RUNTIME):raise ValueError('A different Studio Lua catcher is already installed. It will not be replaced automatically; update it explicitly or use MM.')


def deploy_project(project,engine,mods,version,bundle):
    engine=Path(engine).resolve();mods=Path(mods).resolve()
    audit=inspect_runtime(engine,mods);require_runtime(audit)
    with tempfile.TemporaryDirectory(prefix='studio_install_') as directory:
        stage=Path(directory);data,counts=stage_project(project,engine,version,stage)
        payload={};shared_bootstrap=set()
        if audit['bepinex']=='absent':
            for relative,blob in bootstrap(bundle).items():
                payload[engine/relative]=blob;shared_bootstrap.add(engine/relative)
        for root,destination in ((stage/'engine',engine),(stage/'Mods',mods)):
            for source in root.rglob('*'):
                if source.is_file():payload[destination/source.relative_to(root)]=source.read_bytes()
        ledger=engine/'StudioStandalone'/data['id']/'installed-files.json'
        previous=json.loads(ledger.read_text(encoding='utf-8')) if ledger.is_file() else {}
        for target,blob in payload.items():
            resolved=target.resolve()
            if not (engine in resolved.parents or mods in resolved.parents):raise ValueError('Installation path escapes its configured root: '+str(target))
            if target.is_file() and target.read_bytes()!=blob:
                if str(target) not in previous or digest(target)!=previous[str(target)]:
                    raise ValueError('Existing file is not an unchanged project-owned deployment; refusing to overwrite: '+str(target))
            elif target.exists() and not target.is_file():raise ValueError('Installation target is not a file: '+str(target))
        # Retire only hash-verified files owned by the last deployment. Keep recoverable copies.
        stale=[p for p in previous if Path(p) not in payload and Path(p).exists()]
        for value in stale:
            path=Path(value).resolve()
            if not (engine in path.parents or mods in path.parents) or not path.is_file() or digest(path)!=previous[value]:
                raise ValueError('Stale file was edited or is not safely owned; refusing to remove: '+value)
        from .unit_icons import atomic
        from datetime import datetime
        backup=engine/'StudioStandalone'/data['id']/'backups'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        def backup_path(target):
            base=engine if engine in target.resolve().parents else mods
            destination=backup/('engine' if base==engine else 'Mods')/target.relative_to(base)
            destination.parent.mkdir(parents=True,exist_ok=True)
            return destination
        for value in stale:
            path=Path(value);path.replace(backup_path(path))
        updated={}
        for target,blob in payload.items():
            if not target.is_file() or target.read_bytes()!=blob:
                if target.is_file():shutil.copy2(target,backup_path(target))
                atomic(target,blob)
            if target not in shared_bootstrap:updated[str(target)]=hashlib.sha256(blob).hexdigest()
        atomic(ledger,json.dumps(updated,indent=2).encode())
    return counts,str(backup) if backup.is_dir() else None


def export_standalone_project(project,engine,version,bundle):
    # Export always includes a trusted bootstrap for recipients who have no BepInEx.
    boot=bootstrap(bundle);project=Path(project).resolve()
    with tempfile.TemporaryDirectory(prefix='studio_standalone_') as directory:
        stage=Path(directory);data,counts=stage_project(project,engine,version,stage)
        target=project/'Releases'/f'{data["id"]}-Standalone-{data["version"]}.zip';target.parent.mkdir(parents=True,exist_ok=True)
        built=stage/'output.zip'
        with zipfile.ZipFile(built,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as z:
            for p in sorted(stage.rglob('*')):
                if p.is_file() and p!=built:z.write(p,p.relative_to(stage).as_posix(),compress_type=zipfile.ZIP_STORED if p.suffix in ('.zip','.sanpack') else zipfile.ZIP_DEFLATED)
            for relative,blob in boot.items():z.writestr('bootstrap/'+relative,blob)
            z.write(Path(__file__).parent/'runtime/Install-Standalone.ps1','Install-Standalone.ps1')
            z.writestr('installer.bat','@echo off\r\nsetlocal\r\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Standalone.ps1"\r\npause\r\n')
            z.writestr('README.txt',f'{data["name"]} {data["version"]} — ADVANCED STANDALONE\n\nMM EXPORT IS RECOMMENDED. NEW FACTIONS ARE MM-ONLY.\n'
                'Extract this ZIP and run installer.bat for a checked FIRST installation. It validates the game build, existing BepInEx and Lua catchers, then refuses conflicting files. For updates, use the addon direct installer or remove only your own package using its recorded file list.\n'
                'Close Sanctuary fully. Do NOT combine this package with MM or another Lua/faction catcher.\n'
                'This ZIP targets the exact game assembly recorded in engine/StudioStandalone/*/runtime.json. Re-export after game updates.\n'
                'If BepInEx is absent, install the bootstrap contents into the engine folder. If any BepInEx/doorstop infrastructure already exists, do NOT copy bootstrap over it; validate/repair using its original installer.\n'
                'Do not manually copy over an existing setup without checking compatibility. Mods normally resides in Documents/My Games/Enhearten Media/Sanctuary/Mods.\n'
                'Do NOT replace an existing Lua catcher. Existing unknown catchers need explicit compatibility verification; use MM instead.\n'
                'The Studio catcher may be skipped only when the identical DLL is already installed. Preserve all unrelated files.\n'
                'Standalone files activate at startup. All players require identical files. Restart, then test a new small match before sharing.\n'
                'Direct deployment from the addon automates these checks and refuses unowned overwrites.\n')
        with zipfile.ZipFile(built) as z:
            if z.testzip():raise ValueError('Standalone archive CRC verification failed')
        temp=target.with_suffix('.zip.tmp');shutil.copy2(built,temp);temp.replace(target)
    return target,counts
