"""Named Lua choices discovered from data, never by executing Lua."""
from pathlib import Path

from .studio_core import Blueprint

_CACHE = {}


def field_key(path):
    return tuple('*' if isinstance(part, int) else part for part in path)


def catalog(engine, project):
    roots = [Path(engine) / 'LJ/lua/common/units/unitsTemplates'] if engine else []
    if project:
        roots.append(Path(project) / 'Lua')
    files = sorted({p for root in roots for p in root.rglob('*')
                    if p.is_file() and p.suffix.lower() in ('.santp', '.lua')})
    signature = tuple((str(p), p.stat().st_mtime_ns, p.stat().st_size) for p in files)
    cache_key = tuple(map(str, roots))
    cached = _CACHE.get(cache_key)
    if cached and cached[0] == signature:
        return cached[1]
    values = {}
    for path in files:
        try:
            doc = Blueprint(path.read_text(encoding='utf-8-sig'))
            for field, raw in doc.leaves():
                value = doc.scalar(field)
                if isinstance(value, str) and raw.strip().startswith(('"', "'", '[[')):
                    values.setdefault(field_key(field), set()).add(value)
        except (OSError, UnicodeError, ValueError):
            continue  # Not all project Lua files are unit tables.
    _CACHE[cache_key] = (signature, values)
    return values


def choices(engine, project, path, current='', document=None):
    key = field_key(path)
    values = set(catalog(engine, project).get(key, ()))
    if document:
        for field, raw in document.leaves():
            if field_key(field) == key:
                value = document.scalar(field)
                if isinstance(value, str):
                    values.add(value)
    if current:
        values.add(current)
    return sorted(values, key=lambda value: (value.casefold(), value))
