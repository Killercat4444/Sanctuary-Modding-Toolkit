"""Resolve and edit effect slots without executing Lua or replacing weapon stats."""
import copy
import json
import re
from pathlib import Path
from .studio_core import Blueprint, safe_member

EXTENSIONS = ('.sanvfx', '.sanbeam', '.sandecal', '.sanmaterial')

def projectile_path(engine, project, uid):
    if not re.fullmatch(r'[A-Za-z0-9_]+', uid):
        raise ValueError('Invalid projectile ID: ' + str(uid))
    rel = Path('common/projectiles/projectilesTemplates') / uid / (uid + '.santp')
    for base in (Path(project) / 'Lua' if project else None, Path(engine) / 'LJ/lua' if engine else None):
        if base and (base / rel).is_file(): return base / rel
    raise ValueError('Projectile template not found: ' + uid)

def effect_slots(text, engine, project):
    unit = Blueprint(text)
    slots = []
    for path, raw in unit.leaves():
        value = unit.scalar(path)
        if isinstance(value, str) and value.lower().endswith(EXTENSIONS):
            slots.append({'owner': 'unit', 'path': list(path), 'asset': value,
                          'label': '.'.join(map(str, path))})
        if path[-1] == 'projectileTemplate' and isinstance(value, str):
            try:
                file = projectile_path(engine, project, value)
                projectile = Blueprint(file.read_text(encoding='utf-8-sig'), 'ProjectileTemplate')
            except (ValueError, OSError):
                continue
            for effect_path, _ in projectile.leaves():
                asset = projectile.scalar(effect_path)
                if isinstance(asset, str) and asset.lower().endswith(EXTENSIONS):
                    kind = 'Impact' if 'onImpactEffects' in effect_path else 'Trail' if 'persistantEffects' in effect_path else 'Visual'
                    slots.append({'owner':'projectile', 'projectile':value, 'weapon_path':list(path),
                                  'path':list(effect_path), 'asset':asset,
                                  'label':f'Weapon {path[1]} · {kind} · ' + '.'.join(map(str, effect_path[2:]))})
    return slots

def apply_slot(text, slot, asset, engine, project):
    """Always isolate projectile edits to a unit-owned project copy."""
    asset = safe_member(asset)
    if Path(asset).suffix.lower() != Path(slot['asset']).suffix.lower():
        raise ValueError('Choose the same asset type as the existing effect slot.')
    unit = Blueprint(text)
    if slot['owner'] == 'unit':
        if unit.scalar(slot['path']) != slot['asset']:
            raise ValueError('This slot changed. Refresh effects before applying.')
        return unit.set(tuple(slot['path']), json.dumps(asset)), None
    if unit.scalar(slot['weapon_path']) != slot['projectile']:
        raise ValueError('The weapon projectile changed. Refresh effects before applying.')
    original = projectile_path(engine, project, slot['projectile'])
    doc = Blueprint(original.read_text(encoding='utf-8-sig'), 'ProjectileTemplate')
    if doc.scalar(slot['path']) != slot['asset']:
        raise ValueError('The projectile effect changed on disk. Refresh effects first.')
    uid = unit.scalar(('general', 'tpId'))
    if not uid or not re.fullmatch(r'[A-Za-z0-9_]+', uid): raise ValueError('Save a valid unit ID first.')
    base = 'p' + uid.lower()[1:] + 'w' + str(slot['weapon_path'][1]) + 'vfx'
    pid, number = base, 1
    destination = Path(project) / 'Lua/common/projectiles/projectilesTemplates' / pid / (pid + '.santp')
    while destination.exists():
        number += 1; pid = base + str(number)
        destination = Path(project) / 'Lua/common/projectiles/projectilesTemplates' / pid / (pid + '.santp')
    edited = doc.set(tuple(slot['path']), json.dumps(asset))
    edited = Blueprint(edited, 'ProjectileTemplate').set(('general','tpId'), json.dumps(pid))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(edited, encoding='utf-8')
    return unit.set(tuple(slot['weapon_path']), json.dumps(pid)), destination

def adjust_particles(data, tint=None, size=1.0, speed=1.0, lifetime=1.0, emission=1.0):
    result = copy.deepcopy(data)
    systems = result.get('particleSystems', [])
    if not systems: raise ValueError('This asset has no particle systems. Inspect its material or a particle effect.')
    for system in systems:
        main = system.get('main', {})
        for key, factor in [('startSize',size),('startSizeX',size),('startSizeY',size),('startSizeZ',size),('startSpeed',speed),('startLifetime',lifetime)]:
            curve = main.get(key)
            if isinstance(curve, dict):
                for name in ('constant','constantMin','constantMax','curveMultiplier'):
                    if isinstance(curve.get(name), (int,float)): curve[name] *= factor
            if isinstance(main.get(key+'Multiplier'), (int,float)): main[key+'Multiplier'] *= factor
        for key in ('emission_rateOverTime', 'emission_rateOverDistance'):
            curve = main.get(key)
            if isinstance(curve, dict):
                for name in ('constant','constantMin','constantMax','curveMultiplier'):
                    if isinstance(curve.get(name),(int,float)): curve[name] *= emission
            if isinstance(main.get(key+'Multiplier'),(int,float)): main[key+'Multiplier'] *= emission
        for burst in main.get('emission_bursts',[]):
            curve=burst.get('count')
            if isinstance(curve,dict):
                for name in ('constant','constantMin','constantMax','curveMultiplier'):
                    if isinstance(curve.get(name),(int,float)):curve[name] *= emission
        if tint is not None:
            def recolor(value):
                if isinstance(value, dict):
                    if all(k in value for k in 'rgb'):
                        intensity = max(1, *(value[k] for k in 'rgb'))
                        for i,k in enumerate('rgb'): value[k] = tint[i] * intensity
                    else:
                        for child in value.values(): recolor(child)
                elif isinstance(value,list):
                    for child in value: recolor(child)
            recolor(main.get('startColor', {}))
            recolor(system.get('colorOverLifetime', {}))
    return result
