"""Assign only enabled export clips. Never guess between competing behaviours."""
import json
from .studio_core import Blueprint

ROLES = ('walk', 'build', 'rest')


def assignments(items):
    enabled = [i for i in items if i.enabled and i.action]
    names = [i.clip_name.strip() for i in enabled]
    if any(not n for n in names) or len(names) != len(set(names)):
        raise ValueError('Export clips need unique, nonempty game clip names.')
    explicit, automatic = {}, {role: [] for role in ROLES}
    for item, name in zip(enabled, names):
        role = getattr(item, 'behaviour_role', 'AUTO')
        if role == 'NONE':
            continue
        if role != 'AUTO':
            if role in explicit:
                raise ValueError('Two clips are manually assigned to ' + role + '. Choose one in Lua > Animation assignment.')
            explicit[role] = name
        else:
            matches = [r for r in ROLES if r in name.casefold()]
            if len(matches) > 1:
                raise ValueError('Clip ' + name + ' matches multiple behaviours. Set its assignment manually.')
            if matches:
                automatic[matches[0]].append(name)
    result = dict(explicit)
    for role, candidates in automatic.items():
        if role in result:
            continue
        if len(candidates) > 1:
            raise ValueError('Multiple ' + role + ' clips: ' + ', '.join(candidates) + '. Assign one manually in Lua > Animation assignment.')
        if candidates:
            result[role] = candidates[0]
    return result, names


def assign_text(text, uid, items):
    chosen, names = assignments(items)
    if not names:
        return text  # No export list: do not disturb imported/external assignments.
    if Blueprint(text).scalar(('general', 'tpId')) != uid:
        raise ValueError('Animation assignment: Lua tpId does not match the export unit.')
    path = f'Units/{uid}/LOD0/{uid}.sananimation'
    for role, name in chosen.items():
        base = ('visuals', 'animations', role)
        text = Blueprint(text).set(base + ('clipName',), json.dumps(name))
        text = Blueprint(text).set(base + ('sanAnimationPath',), json.dumps(path))
        if role in ('rest', 'build') and Blueprint(text).span(base + ('authoredSpeed',)):
            text = Blueprint(text).remove(base + ('authoredSpeed',))
        elif role == 'walk' and Blueprint(text).scalar(base + ('authoredSpeed',)) is None:
            speed = Blueprint(text).scalar(('movement', 'walkAnimation', 'authoredSpeed'), 1.0)
            text = Blueprint(text).set(base + ('authoredSpeed',), str(speed))
    # Keep unrelated roles and custom options, but do not silently retain a dead
    # reference into the file that this export will replace.
    doc = Blueprint(text)
    for key, raw in doc.leaves():
        if len(key) == 4 and key[:2] == ('visuals', 'animations') and key[-1] == 'clipName':
            if doc.scalar(key[:-1] + ('sanAnimationPath',)) == path and doc.scalar(key) not in names:
                raise ValueError('Lua animation ' + str(key[2]) + ' refers to a clip not enabled for export. Enable it or remove that Lua assignment.')
    return text


def sync_saved(context, path):
    from . import studio
    path = __import__('pathlib').Path(path)
    old = path.read_text(encoding='utf-8-sig')
    state = studio.settings(context)
    if state.blueprint and state.blueprint.as_string() != old:
        raise ValueError('Save the Lua draft before synchronizing export animations.')
    s = context.scene.sanctuary_toolkit
    new = assign_text(old, s.unit_id, s.animations)
    if new != old:
        path.write_text(new, encoding='utf-8')
        if state.blueprint:
            studio.set_source(context, new)
            state.baseline = new
    return new


def draw(layout, context):
    from . import studio
    s = context.scene.sanctuary_toolkit
    box = layout.box()
    box.label(text='Animation assignment', icon='ACTION')
    box.label(text='Auto: names containing walk / build / rest')
    box.label(text='Assignments update on Save Unit and project export.')
    if not len(s.animations):
        box.label(text='Add clips in Assets > Animations first.')
    for item in s.animations:
        if item.enabled and item.action:
            row = box.row(align=True)
            row.label(text=item.clip_name or '(unnamed clip)')
            row.prop(item, 'behaviour_role', text='')
    try:
        chosen, _ = assignments(s.animations)
        for role in ROLES:
            box.label(text=role.title() + ': ' + chosen.get(role, '(keep existing Lua)'))
    except ValueError as exc:
        from .studio_workshop import note
        note(box, str(exc))
    box.label(text='Runtime class must support rest/build playback.')
