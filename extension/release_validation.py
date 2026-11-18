"""Read-only, non-executing whole-project release checks. Never run author Lua."""
from pathlib import Path
import math
import re
from .studio_core import AssetLibrary, Blueprint, lua_compile, safe_member


class ReleaseReport:
    def __init__(self):
        self.errors, self.warnings, self.passes = [], [], []
    def text(self):
        return ('Sanctuary Studio project release check\n'
                f'{len(self.errors)} errors, {len(self.warnings)} warnings\n\n' +
                '\n'.join('ERROR  '+s for s in self.errors) + '\n' +
                '\n'.join('WARN   '+s for s in self.warnings) + '\n' +
                '\n'.join('PASS   '+s for s in self.passes) +
                '\n\nStatic checks are not a simulation. Test a new match before distribution. '
                'Dynamic imports, engine APIs, custom behavior, AI, shader rendering and balance '
                'still require in-game tests. Restart after standalone/direct updates.\n')
    def require(self):
        if self.errors:
            raise ValueError(f'Export blocked: {len(self.errors)} validation errors. '+ '; '.join(self.errors[:4]))
        return self


def check(project, engine, faction=None):
    project, engine = Path(project).resolve(), Path(engine).resolve() if engine else None
    report = ReleaseReport()
    if not engine or not (engine/'LJ/lua').is_dir():
        report.errors.append('Set the installed Sanctuary engine folder to validate game references.')
        return report
    assets = AssetLibrary(engine, project).scan()
    root = project/'Lua'; stock=engine/'LJ/lua'
    files = sorted(p for p in root.rglob('*') if p.is_file() and p.suffix.lower() in ('.lua','.santp'))
    known, units, projectiles, seen = {}, {}, {}, set()
    for base in (stock,root):
        for p in base.rglob('*'):
            if p.is_file() and p.suffix.lower() in ('.lua','.santp'):
                known[p.relative_to(base).as_posix().casefold()] = p
    for p in files:
        relative=p.relative_to(root).as_posix(); label='Lua/'+relative
        if p.is_symlink() or project not in p.resolve().parents:
            report.errors.append(label+': path escapes project'); continue
        if relative.casefold() in seen: report.errors.append(label+': duplicate case-insensitive filename')
        seen.add(relative.casefold())
        try:
            source=p.read_text(encoding='utf-8-sig')
            passed,message=lua_compile(source,engine)
            if not passed: raise ValueError(message)
            if relative.lower().startswith('append/'):
                target=relative[7:]; safe_member(target)
                if target.casefold() not in known: raise ValueError('missing append target '+target)
                from .standalone_export import _merge_lua_append
                merged=_merge_lua_append(known[target.casefold()].read_bytes(),p.read_bytes()).decode('utf-8-sig')
                lua_compile(merged,engine)
            # Literal Import calls are checked; expressions are not evaluated.
            for match in re.finditer(r'\bImport\s*\(\s*(["\'])(.*?)\1\s*\)',source):
                ref=match.group(2); safe_member(ref)
                if ref.casefold() not in known: report.errors.append(label+': missing Import '+ref)
            if p.suffix.lower()=='.santp' and ('unitsTemplates' in p.parts or 'projectilesTemplates' in p.parts):
                kind='UnitTemplate' if 'unitsTemplates' in p.parts else 'ProjectileTemplate'
                doc=Blueprint(source,kind); uid=doc.scalar(('general','tpId'))
                if uid!=p.stem or p.parent.name!=p.stem: raise ValueError('folder, filename and general.tpId must agree')
                target=units if kind=='UnitTemplate' else projectiles
                if uid in target: raise ValueError('duplicate template ID '+uid)
                target[uid]=(p,doc)
        except Exception as exc: report.errors.append(label+': '+str(exc))
    all_projectiles={p.stem for p in (stock/'common/projectiles/projectilesTemplates').rglob('*.santp')} | set(projectiles)
    all_units={p.stem for p in (stock/'common/units/unitsTemplates').rglob('*.santp')} | set(units)
    for uid,(p,doc) in units.items():
        label=uid; model=doc.scalar(('general','modelTpId'),uid)
        bones=set()
        try:
            if not isinstance(model,str) or not re.fullmatch(r'u[a-z]{2}\d{4}',model): raise ValueError('invalid modelTpId')
            assetroot=f'Units/{model}/LOD0/{model}'
            for suffix in ('.sanskeleton','_lod0.sanmodel','_lod0.sanmaterial'):
                if (assetroot+suffix).lower() not in assets.records: report.errors.append(label+': missing '+assetroot+suffix)
            if (assetroot+'.sanskeleton').lower() in assets.records:
                from .sanskeleton_io import parse_sanskeleton_bytes
                skeleton=parse_sanskeleton_bytes(assets.read(assetroot+'.sanskeleton'))
                bones={b.get('boneName',b.get('name')) for b in skeleton['bonesHierarchy']}
                bones.add('__Head')
            pair=doc.strings(('general','class'))
            if len(pair)!=2: report.errors.append(label+': general.class needs a module and class name')
            else:
                for side in ('host','client'):
                    ref=f'{side}/units/unitsClasses/{pair[0]}.lua'
                    path=known.get(ref.casefold())
                    if not path: report.errors.append(label+': missing behavior '+ref)
                    elif side.title()+pair[1] not in path.read_text(encoding='utf-8-sig'):
                        report.errors.append(label+': '+ref+' does not declare '+side.title()+pair[1])
            for field in (('defence','health','max'),('footprint','x'),('footprint','y')):
                value=doc.scalar(field)
                if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value) or value<=0:
                    report.errors.append(label+': '+'.'.join(field)+' must be a positive number')
            for shield_path,_ in doc.leaves():
                if shield_path[:2]==('defence','shields') and shield_path[-1]=='radii':
                    report.errors.append(label+': shield radii must be an x/y/z table, not a scalar')
                if shield_path[:2]==('defence','shields') and len(shield_path)>=5 and shield_path[-2]=='radii':
                    radius=doc.scalar(shield_path)
                    if not isinstance(radius,(int,float)) or isinstance(radius,bool) or not math.isfinite(radius) or radius<=0:
                        report.errors.append(label+': shield radius must be positive at '+'.'.join(map(str,shield_path)))
            for field,_ in doc.leaves():
                value=doc.scalar(field); key=str(field[-1]).lower()
                if not isinstance(value,str): continue
                if bones and (key in ('bone','bonename','yawbone','pitchbone','muzzlebonename','aimbonename','platformbonename') or
                              any(str(k).lower() in ('muzzles','buildbeambones') for k in field)):
                    if value and value not in bones: report.errors.append(label+': missing bone '+value+' at '+'.'.join(map(str,field)))
                if key=='projectiletemplate' and value not in all_projectiles:
                    report.errors.append(label+': missing projectile '+value)
                if key in ('upgradesto','upgradeunit','upgradetpId'.lower()) and value not in all_units:
                    report.errors.append(label+': missing upgrade unit '+value)
                if value.lower().startswith(('units/','vfx/','environment/','projectiles/','audio/')) and '/' in value and '.' in value:
                    safe_member(value)
                    if value.lower() not in assets.records: report.errors.append(label+': missing asset '+value)
            tags=doc.strings(('tags',))
            if not tags: report.errors.append(label+': no unit tags')
            if faction and uid.startswith(faction['unitPrefix']):
                if faction['tag'] not in tags: report.errors.append(label+': missing faction tag '+faction['tag'])
                expression=doc.scalar(('construction','canBuild'),'')
                if isinstance(expression,str) and re.search(r'Tags\.(EDA|CHOSEN|GUARD)\b',expression):
                    report.errors.append(label+': build menu still targets a stock faction; use Tags.'+faction['tag'])
        except Exception as exc: report.errors.append(label+': '+str(exc))
    for uid,(p,doc) in projectiles.items():
        for field,_ in doc.leaves():
            value=doc.scalar(field)
            if isinstance(value,str) and value.lower().startswith(('units/','vfx/','environment/','projectiles/','audio/')) and '/' in value and '.' in value:
                try:
                    safe_member(value)
                    if value.lower() not in assets.records:report.errors.append(uid+': missing projectile asset '+value)
                except ValueError as exc:report.errors.append(uid+': '+str(exc))
    for folder in ('Units','UI','VFX','Environment','Audio','Projectiles'):
        for p in (project/folder).rglob('*'):
            if not p.is_file(): continue
            if p.is_symlink() or project not in p.resolve().parents:
                report.errors.append(str(p)+': path escapes project'); continue
            if p.suffix.lower() in ('.sanmaterial','.sanvfx','.sanbeam','.sandecal','.sansprite'):
                try:
                    from .vfx_documents import decode_asset
                    from .studio_core import json_leaves
                    data=decode_asset(p.read_bytes(),p)
                    for field,value in json_leaves(data):
                        parent=data; disabled=False
                        for part in field:
                            if isinstance(parent,dict) and parent.get('enabled') is False:disabled=True;break
                            parent=parent[part]
                        if disabled:continue
                        if isinstance(value,str) and value.lower().startswith(('units/','ui/','vfx/','environment/','projectiles/','audio/')):
                            safe_member(value)
                            if value.lower() not in assets.records and not (project/value).is_file():
                                report.errors.append(p.name+': missing dependency '+value)
                except Exception as exc: report.errors.append(p.name+': '+str(exc))
    if faction:
        commander=faction['commanders'][0]['unit']
        if commander not in units: report.errors.append('Faction commander must have a saved project template: '+commander)
        elif not set(units[commander][1].strings(('tags',))).intersection({'COMMAND','COMMANDER'}):
            report.errors.append(commander+': selected starter is not tagged as a commander')
        if not units: report.errors.append('Faction has no saved unit templates')
    if not files: report.warnings.append('Project has no Lua. Art-only mods cannot register a playable faction.')
    if any(re.search(r'\bImport\s*\(\s*[^"\'\s]',p.read_text(encoding='utf-8-sig')) for p in files):
        report.warnings.append('Computed Import paths require in-game testing; only literal imports were resolved.')
    report.errors=list(dict.fromkeys(report.errors));report.warnings=list(dict.fromkeys(report.warnings))
    report.passes.append(f'Compiled {len(files)} Lua/template files without executing them; inspected {len(units)} units and {len(projectiles)} projectiles.')
    assets.close()
    return report
