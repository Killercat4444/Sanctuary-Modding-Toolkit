"""Generate complete unit blueprints from game-compatible archetypes.

The generator preserves expressions, classes and comments; it does not invent
an engine schema or execute Lua. Generated source uses the normal Save/Export.
"""
import json
from pathlib import Path
import re
from .studio_core import Blueprint, ensure_builder_skirt
from .lua_document import string_table

ROLES = (('MOBILE','Ground combat'),('ENGINEER','Engineer'),('FACTORY','Factory'),('DEFENSE','Defense'),
         ('RADAR','Radar'),('STORAGE','Resource storage'),('SHIELD','Shield generator'),('COMMANDER','Commander'),
         ('AIR','Aircraft'),('NAVAL','Naval unit'),('GENERATOR','Energy generator'),('EXTRACTOR','Alloy extractor'))


def role_matches(text, role):
    doc = Blueprint(text)
    tags = set(doc.strings(('tags',)))
    name = str(doc.scalar(('general','displayName'),'')).lower()
    tests = {
        'MOBILE': 'LAND' in tags and 'MOBILE' in tags and not tags.intersection({'ENGINEER','COMMAND','COMMANDER'}),
        'ENGINEER': 'ENGINEER' in tags,
        'FACTORY': 'FACTORY' in tags or 'factory' in name,
        'DEFENSE': 'STRUCTURE' in tags and bool(tags.intersection({'ANTI_AIR','ANTI_SURFACE','DIRECT_FIRE','INDIRECT_FIRE'})),
        'RADAR': 'radar' in name,
        'STORAGE': 'storage' in name,
        'SHIELD': bool(doc.span(('defence','shields'))),
        'COMMANDER': bool(tags.intersection({'COMMAND','COMMANDER'})) or 'commander' in name,
        'AIR': 'AIR' in tags and 'MOBILE' in tags,
        'NAVAL': bool(tags.intersection({'NAVY','NAVAL'})) and 'MOBILE' in tags,
        'GENERATOR': 'generator' in name,
        'EXTRACTOR': 'extractor' in name,
    }
    return tests.get(role, True)


def generate_unit(source, uid, title, faction_tag, values=None, own_model=False, root_bone=None, muzzle_bone=None):
    if not re.fullmatch(r'u[a-z]{2}\d{4}',uid): raise ValueError('Use a unit ID such as uml1001; no ID is filled in automatically.')
    if not re.fullmatch(r'[A-Z][A-Z0-9_]*',faction_tag): raise ValueError('Use an uppercase faction tag.')
    doc = Blueprint(source)
    tags = doc.strings(('tags',))
    donor_tags=set(tag for tag in tags if tag in ('EDA','CHOSEN','GUARD'))
    tags = [tag for tag in tags if tag not in donor_tags]
    if faction_tag not in tags: tags.append(faction_tag)
    if uid[3] in '1234':
        tags = [tag for tag in tags if not re.fullmatch(r'TECH[1-4]', tag)] + ['TECH'+uid[3]]
    text = doc.set(('general','tpId'),json.dumps(uid))
    changes = {('general','displayName'): title, ('general','name'): title,
               ('general','unitTypeName'): re.sub(r'\W+','',faction_tag.title()+title) or uid}
    changes.update(values or {})
    for path,value in changes.items(): text = Blueprint(text).set(path,json.dumps(value))
    text = Blueprint(text).set(('tags',),string_table(tags))
    # Constructors must build the new faction, not the copied donor faction.
    expression=Blueprint(text).raw(('construction','canBuild'))
    if expression:
        for tag in donor_tags:
            expression=re.sub(r'\bTags\.'+re.escape(tag)+r'\b','Tags.'+faction_tag,expression)
        text=Blueprint(text).set(('construction','canBuild'),expression)
    if faction_tag == 'DYCOM' and Blueprint(text).span(('visuals','holes')):
        text = Blueprint(text).remove(('visuals','holes'))
    if own_model:
        text = Blueprint(text).set(('general','modelTpId'),json.dumps(uid))
        text = Blueprint(text).set(('visuals','mesh','modelTpId'),json.dumps(uid))
        # Retain animation references until the artist assigns actual clips.
        # Never manufacture nonexistent animation or texture filenames.
    else:
        model=doc.scalar(('general','modelTpId'),doc.scalar(('general','tpId')))
        text = Blueprint(text).set(('general','modelTpId'),json.dumps(model))
    for path,_ in list(Blueprint(text).leaves()):
        value = Blueprint(text).scalar(path)
        if not isinstance(value,str): continue
        key = str(path[-1]).lower()
        if root_bone and key in ('bonename','bone') and value.lower() == 'root':
            text = Blueprint(text).set(path,json.dumps(root_bone))
        if muzzle_bone and key in ('muzzlebonename','muzzle'):
            text = Blueprint(text).set(path,json.dumps(muzzle_bone))
        # Some templates express a muzzle list directly as bone strings.
        if muzzle_bone and 'muzzles' in path and isinstance(path[-1],int):
            text = Blueprint(text).set(path,json.dumps(muzzle_bone))
    return ensure_builder_skirt(text)


def bone_issues(text, bones):
    available = set(bones); issues = []
    doc = Blueprint(text)
    for path,_ in doc.leaves():
        key = str(path[-1]).lower(); value = doc.scalar(path)
        if isinstance(value,str) and (key in ('bone','bonename','muzzlebonename','aimbonename','yawbone','pitchbone')
                                     or 'muzzles' in path):
            if value and value not in available: issues.append('.'.join(map(str,path))+': '+value)
    return issues


def component_source(target, donor, section, index=1):
    destination = Blueprint(target); source = Blueprint(donor)
    path = tuple(int(k) if k.isdigit() else k for k in section.split('.'))
    if path[0] in ('weapons',) or path == ('defence','shields'):
        raw = source.raw(path+(index,))
        if raw is None: raise ValueError('The source has no component at that index.')
        return destination.append(path,raw)
    raw = source.raw(path)
    if raw is None: raise ValueError('The source has no '+section+' section.')
    return destination.set(path,raw)


def class_sources(text,uid,faction):
    """Paired engine-native subclasses; inherit behavior instead of copying stock."""
    doc=Blueprint(text);base=doc.strings(('general','class'))
    if len(base)!=2 or not all(re.fullmatch(r'[A-Za-z_]\w*',part) for part in base):
        raise ValueError('Starting template must have a simple module/class pair.')
    name=faction.title().replace('_','')+'Studio_'+uid
    module=faction.lower()+'_studio_'+uid
    sources={}
    for side in ('host','client'):
        prefix=side.title()
        path=side+'/units/unitsClasses/'+module+'.lua'
        sources[path]=(f'-- Project-owned behavior for {uid}; stock classes remain untouched.\n'
            f'local Base = Import("{side}/units/unitsClasses/{base[0]}.lua")\n'
            f'{prefix}{name} = Class("{name}", Base.{prefix}{base[1]})\n\n'
            '-- Add overrides below. Call the inherited method when its setup is required.\n'
            '-- Host scripts control simulation; client scripts control presentation.\n')
    return doc.set(('general','class'),string_table([module,name])),sources
