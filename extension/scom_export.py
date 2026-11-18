"""Export an additive DYCOM sidecar for the qualified SCOM loader; never deploy."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile
from .hosted_export import (_collect_lua, _digest, _export_input_inventory,
    _snapshot_export_inputs, _validate_unit_assets, _wanted, normalize_version, ART_FOLDERS)
from .standalone_export import _build_patcher, _build_runtime_plugin
from .export_cache import pack_assets

IDENTITIES = {
    'supported_loader_sha256': 'BepInEx/plugins/ModLoader.dll',
    'supported_host_sha256': 'BepInEx/patchers/SanctuaryPatcherHost.dll',
    'supported_scom_sha256': 'SanctuaryMods/SCOM/Scom-patcher.dll',
    'supported_game_sha256': 'Sanctuary_Data/Managed/Trebuchet.dll',
}
EXCLUDED_SCOM_ASSETS = {'UI/Sprites/Icons/Units/ugl4002.dds', 'UI/Sprites/Icons/Units/ugl4002.sansprite'}

def validate_engine(engine: Path, config: dict) -> dict[str,str]:
    engine=Path(engine).expanduser().resolve()
    if not (engine/'Sanctuary.exe').is_file():
        raise ValueError('Select the SCOM engine folder containing Sanctuary.exe before exporting for SCOM')
    identities={}
    for key,relative in IDENTITIES.items():
        file=engine/relative
        if not file.is_file():raise ValueError('Selected engine is missing the SCOM dependency: '+relative)
        identities[relative]=_digest(file)
        if identities[relative]!=config.get(key):
            raise ValueError('Unsupported SCOM loader/assembly identity; requalification required: '+relative)
    for relative in ('BepInEx/core/BepInEx.dll','BepInEx/core/0Harmony.dll','BepInEx/core/Mono.Cecil.dll',
                     'Sanctuary_Data/Managed/netstandard.dll','Sanctuary_Data/Managed/System.IO.Compression.dll',
                     'Sanctuary_Data/Managed/UnityEngine.dll','Sanctuary_Data/Managed/UnityEngine.CoreModule.dll',
                     'Sanctuary_Data/Managed/Unity.Collections.dll'):
        file=engine/relative
        if not file.is_file():raise ValueError('SCOM compiler dependency is missing: '+relative)
        identities[relative]=_digest(file)
    if any((engine/'BepInEx/plugins/DycomStandalone').glob('*.dll')):
        raise ValueError('Remove the separate raw DYCOM standalone installation before using the SCOM sidecar')
    return identities

def _inputs(project):
    return sorted(set(_export_input_inventory(project)+[
        file.relative_to(project).as_posix() for file in (project/'SCOM').rglob('*')
        if file.is_file() and _wanted(file,project/'SCOM')]))

def _resources(project):
    files,skipped=_collect_lua(project,project/'Mod Manager Compatibility')
    _validate_unit_assets(project,files.values())
    output={('append/'+relative[7:] if relative.startswith('append/') else 'owned/'+relative):file.read_bytes()
            for relative,file in files.items()}
    for file in sorted((project/'SCOM/Lua/append').rglob('*.lua')):
        key='append/'+file.relative_to(project/'SCOM/Lua/append').as_posix()
        output[key]=output.get(key,b'')+b'\n'+file.read_bytes()
    units=sorted(relative.split('/')[3] for relative in files
                 if re.fullmatch(r'common/units/unitsTemplates/ud[ls]\d{4}/ud[ls]\d{4}\.santp',relative))
    if not units:raise ValueError('SCOM export has no owned DYCOM unit templates')
    for target,table in [('common/units/availableUnits.lua','AvailableUnits'),('common/units/quickLaunchUnits.lua','QuickLaunchUnits')]:
        key='append/'+target
        generated='\n-- Current canonical DYCOM roster only; preserve every SCOM entry.\n'
        generated+='\n'.join(table+'['+json.dumps(unit)+']=true' for unit in units)+'\n'
        output[key]=output.get(key,b'')+generated.encode()
    for key in output:
        if key.startswith('owned/') and key[6:].casefold() in ('common/systems/factions.lua','ai/aifunctions.lua'):
            raise ValueError('Whole-game Lua replacement is forbidden in the SCOM sidecar: '+key)
    # The selected SCOM copy contains legacy DYCOM brains. Keep those cache
    # entries intact and give this canonical profile a separate owned namespace.
    output={key.replace('AI-Dycom','AI-Dycom-SCOM'):blob.replace(b'AI-Dycom',b'AI-Dycom-SCOM')
            for key,blob in output.items()}
    if any(b'ugl4002' in blob.lower() for blob in output.values()):
        raise ValueError('DYCOM Lua now references the excluded SCOM-owned Guard icon; qualify a DYCOM icon alias first')
    return output,units,skipped

def _validate_archive(path):
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if len({name.casefold() for name in names})!=len(names):raise ValueError('Duplicate SCOM ZIP paths')
        required={'dycom/ZZZZZZZ-Dycom-SCOM-patcher.dll','dycom/DycomScomRuntime.dll',
                  'dycom/cache/DycomScomLua.zip','dycom/packs/Dycom.sanpack','dycom/scom-export-receipt.json'}
        if not required.issubset(names):raise ValueError('Incomplete DYCOM SCOM sidecar')
        if any(not name.startswith('dycom/') or name.endswith('/mod.json') or 'BepInEx/' in name for name in names):
            raise ValueError('SCOM export must contain only the dycom sidecar; no framework or Mod API faction registration')
        receipt=json.loads(archive.read('dycom/scom-export-receipt.json'))
        expected={'dycom/'+name for name in receipt['payloadHashes']}|{'dycom/scom-export-receipt.json'}
        if set(names)!=expected:raise ValueError('SCOM payload inventory differs from receipt')
        for relative,digest in receipt['payloadHashes'].items():
            if hashlib.sha256(archive.read('dycom/'+relative)).hexdigest().upper()!=digest:
                raise ValueError('SCOM payload checksum failed: '+relative)

def _check_target_collisions(engine,resources,asset_names):
    for key in resources:
        if not key.startswith('owned/'):continue
        relative=key[6:]
        for root in (engine/'LJ/lua',engine/'SanctuaryMods/SCOM/lua/shared'):
            if (root/relative).is_file():
                raise ValueError('SCOM target already contains an owned Lua path; qualify a DYCOM namespace first: '+relative)
    owned={name.casefold() for name in asset_names}
    for pack in (engine/'SanctuaryMods').rglob('*.sanpack'):
        if pack.relative_to(engine/'SanctuaryMods').parts[0].casefold()=='dycom':continue
        with zipfile.ZipFile(pack) as archive:
            collision=owned.intersection(name.casefold() for name in archive.namelist())
            if collision:raise ValueError('SCOM packed asset collision; qualify a DYCOM alias first: '+sorted(collision)[0])

def export_scom(project: Path, engine_root: Path, target: Path|None=None, version: str|None=None):
    project=Path(project).resolve();engine=Path(engine_root).expanduser().resolve()
    config_blob=(project/'SCOM/export.json').read_bytes()
    config=json.loads(config_blob.decode('utf-8-sig'))
    if config.get('format')!='dycom_scom_1' or config.get('folder')!='dycom':raise ValueError('Unsupported SCOM export configuration')
    if config.get('ai_namespace')!='AI-Dycom-SCOM' or set(config.get('exclude_assets',[]))!=EXCLUDED_SCOM_ASSETS:
        raise ValueError('Unsupported SCOM AI namespace or foreign-asset exclusion policy')
    engine_snapshot=validate_engine(engine,config)
    manifest=json.loads((project/'smt_project.json').read_text(encoding='utf-8-sig'))
    release=normalize_version(version or manifest.get('version','0.07'))
    inventory=_inputs(project)
    files=[project/relative for relative in inventory]+[project/relative for relative in config['sources']]
    source_hashes=_snapshot_export_inputs(project,files,release)
    if source_hashes['SCOM/export.json']!=hashlib.sha256(config_blob).hexdigest().upper():
        raise ValueError('SCOM configuration changed before the source snapshot; export again')
    resources,units,skipped=_resources(project)
    asset_names=[relative for relative in inventory if relative.split('/')[0] in ART_FOLDERS and relative not in EXCLUDED_SCOM_ASSETS]
    _check_target_collisions(engine,resources,asset_names)
    def manifest_hash():
        data=json.loads((project/'smt_project.json').read_text(encoding='utf-8-sig'));data.pop('updated_utc',None);data['version']=release
        return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest().upper()
    metadata=manifest_hash()
    def unchanged():
        if (_snapshot_export_inputs(project,files,release)!=source_hashes or _inputs(project)!=inventory
                or manifest_hash()!=metadata or validate_engine(engine,config)!=engine_snapshot):
            raise ValueError('Project or selected SCOM dependencies changed during export; no release committed')
        _check_target_collisions(engine,resources,asset_names)
    replace_existing=target is None
    target=Path(target or project/'Releases'/('Dycom-SCOM-'+release+'.zip')).resolve()
    if target.exists() and not replace_existing:raise FileExistsError('Refusing to overwrite an explicit export target: '+str(target))
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dycom-scom-') as directory:
        temp=Path(directory)
        patcher=_build_patcher(engine,temp/'ZZZZZZZ-Dycom-SCOM-patcher.dll',
            [project/'SCOM/DycomScomPatcher.cs',project/'SCOM/DycomScomCache.cs'],engine)
        # Reuse the full canonical logger; identify this independently built route.
        logger=(project/'Standalone/DycomStandaloneLogger.cs').read_text(encoding='utf-8-sig')
        for old,new in [('"com.dycom.standalone.logger"','"com.dycom.scom.logger"'),
                        ('"Dycom Standalone Logger"','"Dycom SCOM Logger"'),
                        ('new DycomAiLogContext("standalone",','new DycomAiLogContext("scom",')]:
            if logger.count(old)!=1:raise ValueError('Canonical logger route contract changed: '+old)
            logger=logger.replace(old,new,1)
        generated=temp/'DycomScomRuntime.cs';generated.write_text(logger,encoding='utf-8')
        runtime=_build_runtime_plugin(engine,temp/'DycomScomRuntime.dll',generated,engine)
        cache=temp/'DycomScomLua.zip'
        with zipfile.ZipFile(cache,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for relative,content in sorted(resources.items()):
                archive.writestr(relative,content)
        assets=sorted(project/relative for relative in inventory
            if relative.split('/')[0] in ART_FOLDERS and relative not in EXCLUDED_SCOM_ASSETS)
        for file in assets:
            if file.suffix.lower() not in {'.sansprite','.sanmaterial','.sandecal','.sanvfx'}:continue
            text=file.read_text(encoding='utf-8-sig').casefold()
            if any(excluded.casefold() in text for excluded in EXCLUDED_SCOM_ASSETS):
                raise ValueError('A retained DYCOM asset depends on an excluded SCOM icon: '+str(file.relative_to(project)))
        pack=temp/'Dycom.sanpack';pack_report={}
        pack_assets(project,assets,pack,'scom-art',source_hashes=source_hashes,
            exporter_version=_digest(Path(__file__)),report=pack_report)
        description=temp/'dycom-scom.json';description.write_text(json.dumps({
            'id':'com.dycom.scom','version':release,'folder':'dycom','alwaysEnabledWithPatcher':True,'aiModName':'AI-Dycom-SCOM',
            'nativeFactions':[{'value':4,'luaSlot':5,'name':'Dycom','unit':'udl0000','identity':'com.dycom.scom:dycom:udl0000'},
                              {'value':5,'luaSlot':6,'name':'DycomMech','unit':'udl0001','hidden':True,'identity':'com.dycom.scom:dycommech:udl0001'}]},indent=2),encoding='utf-8')
        readme=temp/'README.txt';readme.write_text('DYCOM for the qualified SCOM installation\n\nExtract only dycom beside SCOM in engine/SanctuaryMods. Uses the existing BepInEx, ModLoader and PatcherHost; contains no framework replacement. Restart the game after a manual installation. Choose AI-Dycom-SCOM in the native AI picker for the current canonical DYCOM brain; pre-existing AI-Dycom files remain untouched. This additive profile preserves SCOM Seraphim and uses the fifth visible faction with a separate commander selector. Exporting this ZIP does not install it. Offline qualification is not multiplayer testing.\n',encoding='utf-8')
        payload={'ZZZZZZZ-Dycom-SCOM-patcher.dll':patcher,'DycomScomRuntime.dll':runtime,
                 'cache/DycomScomLua.zip':cache,'packs/Dycom.sanpack':pack,'dycom-scom.json':description,'README.txt':readme}
        unchanged()
        receipt={'format':'DYCOM SCOM sidecar 1','version':release,'selectedEngine':str(engine),
                 'sourceHashes':source_hashes,'inputInventory':inventory,'projectManifestNormalizedSha256':metadata,
                 'engineReferences':engine_snapshot,'payloadHashes':{key:_digest(file) for key,file in payload.items()},
                 'generatedLoggerSha256':_digest(generated),'resourceHashes':{key:hashlib.sha256(value).hexdigest().upper() for key,value in resources.items()},
                 'units':units,'skippedWholeGameLua':skipped,'excludedUnusedForeignAssets':sorted(EXCLUDED_SCOM_ASSETS),
                 'aiNamespace':'AI-Dycom-SCOM','packCache':pack_report,'exporterSha256':_digest(Path(__file__))}
        result=temp/'Dycom-SCOM.zip'
        with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED,compresslevel=6,allowZip64=True) as archive:
            for relative,file in sorted(payload.items()):archive.write(file,'dycom/'+relative,compress_type=zipfile.ZIP_STORED if file.suffix in ('.zip','.sanpack') else zipfile.ZIP_DEFLATED)
            archive.writestr('dycom/scom-export-receipt.json',json.dumps(receipt,indent=2)+'\n')
        _validate_archive(result);unchanged();os.replace(result,target)
    return target,{'assets':len(assets),'lua':len(resources),'units':len(units),'plugins':1,'patchers':1}
