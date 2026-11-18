"""Copy a project and remap unit identities without touching the source mod."""
import json
from pathlib import Path
import re
import shutil

from . import mod_projects as mp
from .studio_core import Blueprint


def convert(context):
    from .studio import settings
    state = settings(context)
    source = mp.active_project(context).resolve()
    manifest = mp.read_manifest(source)
    prefix = state.faction_prefix.lower()
    tag = state.faction_tag.upper().strip()
    if not re.fullmatch('[a-z]', prefix) or prefix in ('c', 'e', 'g'):
        raise ValueError('Choose one custom faction letter; c, e and g are stock factions')
    if not re.fullmatch('[A-Z][A-Z0-9_]*', tag):
        raise ValueError('Faction tag must use uppercase letters, numbers and underscores')
    destination = (mp.library_root(context) / mp._clean_name(state.faction_project)).resolve()
    if destination == source or source in destination.parents or destination.exists():
        raise ValueError('Choose a new project name outside the source project')
    ids = {u['unit_id'] for u in manifest.get('units', []) if u.get('unit_id')}
    ids.update(p.stem for p in (source / 'Lua').rglob('*.santp') if re.fullmatch(r'u[a-z]{2}\d{4}', p.stem))
    mapping = {uid: 'u' + prefix + uid[2:] for uid in sorted(ids)}
    if not mapping:
        raise ValueError('The source project contains no unit IDs')
    if len(set(mapping.values())) != len(mapping):
        raise ValueError('Two source factions share the same unit suffix; resolve ID collisions first')
    pattern = re.compile('|'.join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)), re.I)
    def remap(text):
        return pattern.sub(lambda m: mapping[m.group().lower()], text)
    plan = []
    for file in source.rglob('*'):
        parts=file.relative_to(source).parts
        if not file.is_file() or any(p.startswith('.') for p in parts) or parts[0] in ('Releases','Mod Manager Compatibility','Standalone','Remmy','Plugins'):
            continue
        if file.is_symlink():
            raise ValueError('Project contains a symbolic link: ' + str(file))
        relative = remap(file.relative_to(source).as_posix())
        blob = file.read_bytes()
        if file.suffix.lower() in ('.santp', '.lua', '.json', '.sanmaterial', '.sanskeleton', '.sanvfx', '.sanbeam', '.sandecal', '.sansprite', '.md'):
            text = remap(blob.decode('utf-8-sig'))
            if file.suffix.lower() == '.santp' and 'unitsTemplates' in file.parts:
                doc = Blueprint(text)
                tags = doc.strings(('tags',))
                previous_tags = {'CHOSEN', 'GUARD', 'EDA', manifest.get('faction_tag', '')}
                from .lua_document import string_table
                text = doc.set(('tags',), string_table([tag] + [t for t in tags if t not in previous_tags and t != tag]))
                expression=Blueprint(text).raw(('construction','canBuild'))
                if expression:
                    for old_tag in previous_tags:
                        if old_tag:expression=re.sub(r'\bTags\.'+re.escape(old_tag)+r'\b','Tags.'+tag,expression)
                    text=Blueprint(text).set(('construction','canBuild'),expression)
                if tag=='DYCOM' and Blueprint(text).span(('visuals','holes')):
                    text=Blueprint(text).remove(('visuals','holes'))
            blob = text.encode('utf-8')
        elif file.suffix.lower() == '.sanmodel':
            # Only the NUL-terminated header name is text. Preserve geometry bytes.
            end = blob.index(b'\0')
            blob = remap(blob[:end].decode('utf-8')).encode('utf-8') + blob[end:]
        # Animation binary references need the codec; never blindly replace bytes.
        elif file.suffix.lower() == '.sananimation':
            from .sananimation_io import parse_sananimation_bytes
            from .native_animation import serialize_clip_body
            import struct
            clips = parse_sananimation_bytes(blob)
            if not isinstance(clips, list):
                raise ValueError('Animation container requires explicit remapping support: ' + file.name)
            original = struct.pack('<i', len(clips)) + b''.join(serialize_clip_body(c) for c in clips)
            if not blob.startswith(original):
                raise ValueError('Animation cannot be losslessly rewritten: ' + file.name)
            trailer = blob[len(original):]
            bodies = [serialize_clip_body(c, name=remap(c.name)) for c in clips]
            blob = struct.pack('<i', len(bodies)) + b''.join(bodies) + trailer
        plan.append((relative, blob))
    for relative, blob in plan:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
    converted = json.loads(remap(json.dumps(manifest)))
    converted.pop('release',None)  # A copied mod ID/faction key would collide with its source.
    converted.update(name=state.faction_project, faction_name=state.faction_name, faction_tag=tag,
                     tagged_faction=True, faction_prefix=prefix, source_project=str(source))
    converted['faction_registration'] = 'pending_runtime_integration'
    mp.write_manifest(destination, converted)
    from .unit_icons import repair_missing, validate_project
    repair_missing(destination)
    validate_project(destination)
    (destination / 'FACTION_INTEGRATION.md').write_text(
        '# Faction conversion\n\nUnit IDs, local references and faction tags have been remapped.\n'
        'Original behavior classes are retained. This is a faction asset project, not yet a registered selectable faction.\n'
        'Set up this project in Project > Faction & Release with a NEW mod ID/key/tag and commander.\n'
        'Publish through MM for dynamic faction slots. Check all class/bone/build references and test a new match.\n'
        'Compiled Plugins and old release/loader adapters were not copied; rebuild project-specific DLLs if needed.\n\n'
        + json.dumps(mapping, indent=2), encoding='utf-8')
    context.scene.sanctuary_toolkit.mod_active_path = str(destination)
    old = context.scene.sanctuary_toolkit.unit_id
    context.scene.sanctuary_toolkit.unit_id = mapping.get(old, next(iter(mapping.values())))
    return destination
