"""Unambiguous Lua donor choices, independent of case-sensitive legacy index keys."""
import hashlib
from pathlib import Path


def path_key(value):
    return str(Path(value).resolve()).replace('\\', '/').casefold() if value else ''


def choice_key(record):
    identity = record['unit_id'].casefold() + '\n' + path_key(record['template_path'])
    return 'lua:' + hashlib.sha256(identity.encode('utf-8')).hexdigest()


def source_label(record):
    if record.get('project_root'):
        return 'Project: ' + Path(record['project_root']).name
    if record.get('record_key', '').casefold().startswith('project:'):
        return 'Project: ' + record['record_key'].split(':')[1]
    if record.get('record_key', '').casefold().startswith('deployed:') or record.get('source_label') == 'Live Mods':
        return 'Installed Mods'
    if record.get('source_kind', 'BASE') == 'BASE':
        return 'Vanilla'
    return record.get('source_label') or 'Mod'


def ranked(records, project=None):
    active = path_key(project)
    def rank(r):
        label = source_label(r)
        order = (0 if active and path_key(r.get('project_root')) == active else
                 1 if label.startswith('Project:') else 3 if label == 'Installed Mods' else 2)
        return order, r['unit_id'].casefold(), path_key(r['template_path'])
    return sorted((r for r in records if r.get('template_path') and Path(r['template_path']).is_file()), key=rank)


def choices(records, project=None):
    """Collapse duplicate file paths and identical Lua for the SAME unit ID only.

    Different edited versions remain individually selectable with source labels.
    Prefer the active authoring project to a byte-identical live deployment.
    """
    seen_files, seen_content, result = set(), set(), []
    for record in ranked(records, project):
        key = choice_key(record)
        try:
            fingerprint = (record['unit_id'].casefold(), hashlib.sha256(Path(record['template_path']).read_bytes()).digest())
        except OSError:
            continue
        if key in seen_files or fingerprint in seen_content:
            continue
        seen_files.add(key)
        seen_content.add(fingerprint)
        label = (record['unit_id'].upper() + ' • ' + record.get('display_name', '')
                 + ' • ' + record.get('faction', '') + '  [' + source_label(record) + ']')
        result.append((key, label, record['template_path']))
    return result


def resolve(records, key, project=None):
    records = ranked(records, project)
    # Never lowercase a key and compare it to an unnormalised project key.
    folded = key.strip().casefold()
    for record in records:
        if choice_key(record) == folded or record.get('record_key', '').casefold() == folded:
            return record
    for record in records:
        if record['unit_id'].casefold() == folded:
            return record
    raise ValueError('That Lua source is no longer available. Reopen the picker or rebuild the unit index.')
