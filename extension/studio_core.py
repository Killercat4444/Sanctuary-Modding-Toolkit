"""Non-executing document operations and project asset resolution for Studio."""
import copy
import ctypes
import json
import math
from pathlib import Path, PurePosixPath
import re
import zipfile
from collections import OrderedDict

from .lua_document import Document


def table_fields(doc, table):
    """Read table entries including scalar arrays and bracketed keys.

    Function bodies are left opaque. Commas inside nested calls/tables do not
    terminate an entry. No Lua source is executed.
    """
    tokens = doc.tokens
    end = doc.pairs[table]
    i, ordinal = table + 1, 1
    result = {}
    while i < end:
        if tokens[i].group() in (',', ';'):
            i += 1
            continue
        key = ordinal
        start = i
        if i + 1 < end and tokens[i + 1].group() == '=':
            key, start = tokens[i].group(), i + 2
        elif tokens[i].group() == '[' and i in doc.pairs:
            close = doc.pairs[i]
            if close + 1 < end and tokens[close + 1].group() == '=':
                raw = doc.text[tokens[i + 1].start():tokens[close - 1].end()]
                try:
                    key = json.loads(raw)
                except ValueError:
                    key = raw.strip("'")
                start = close + 2
        if start == i:
            ordinal += 1
        j = start
        blocks = []
        while j < end:
            value = tokens[j].group()
            if value in ('function', 'if', 'for', 'while', 'repeat'):
                blocks.append(value)
            elif value == 'end' and blocks:
                blocks.pop()
            elif value == 'until' and blocks and blocks[-1] == 'repeat':
                blocks.pop()
            if not blocks and value in (',', ';'):
                break
            j = doc.pairs.get(j, j) + 1
        if j > start:
            result[key] = (start, j)
        i = j + 1
    return result


class Blueprint(Document):
    fields = table_fields

    def leaves(self, path=(), table=None):
        table = self.root if table is None else table
        for key, (start, end) in self.fields(table).items():
            child = path + (key,)
            if self.tokens[start].group() == '{' and self.pairs[start] == end - 1:
                if self.fields(start):
                    yield from self.leaves(child, start)
                else:
                    yield child, self.text[self.tokens[start].start():self.tokens[end - 1].end()]
            else:
                yield child, self.text[self.tokens[start].start():self.tokens[end - 1].end()]

    def set(self, path, raw):
        if self.span(path):
            return self.replace({path: raw})
        if not path:
            raise ValueError('Choose a field')
        parent = path[:-1]
        table = self.root
        if parent:
            span = self.span(parent)
            if not span:
                key = path[-1]
                member = (str(key) if isinstance(key, str) and re.fullmatch(r'[A-Za-z_]\w*', key)
                          else '[' + json.dumps(key) + ']')
                return self.set(parent, '{ ' + member + ' = ' + raw + ' }')
            table = next(i for i, t in enumerate(self.tokens) if t.start() == span[0])
            if self.tokens[table].group() != '{':
                raise ValueError('The parent field is an expression, not a table')
        close = self.tokens[self.pairs[table]].start()
        previous = self.tokens[self.pairs[table] - 1]
        last = previous.group()
        separator = '' if last in ('{', ',', ';') else ','
        key = path[-1]
        member = (key if isinstance(key, str) and re.fullmatch(r'[A-Za-z_]\w*', key)
                  else '[' + json.dumps(key) + ']')
        before = self.text[:previous.end()] + separator + self.text[previous.end():close]
        return before + '\n    ' + member + ' = ' + raw + ',\n' + self.text[close:]

    def append(self, path, raw):
        span = self.span(path)
        if not span:
            return self.set(path, '{\n' + raw + ',\n}')
        table = next(i for i, t in enumerate(self.tokens) if t.start() == span[0])
        if self.tokens[table].group() != '{':
            raise ValueError('Choose a list table')
        close_token = self.pairs[table]
        close = self.tokens[close_token].start()
        previous = self.tokens[close_token - 1]
        sep = '' if previous.group() in ('{', ',', ';') else ','
        before = self.text[:previous.end()] + sep + self.text[previous.end():close]
        return before + '\n' + raw + ',\n' + self.text[close:]

    def remove(self, path):
        span = self.span(path)
        if not span:
            raise ValueError('Field no longer exists')
        table = self.root
        for key in path[:-1]:
            table = self.fields(table)[key][0]
        start, end = self.fields(table)[path[-1]]
        value_start = self.tokens[start].start()
        entry_start = value_start
        # Locate key syntax immediately before the value.
        if start >= 2 and self.tokens[start - 1].group() == '=':
            entry_start = self.tokens[start - 2].start()
            if self.tokens[start - 2].group() == ']':
                entry_start = self.tokens[next(k for k, v in self.pairs.items() if v == start - 2)].start()
        entry_end = self.tokens[end - 1].end()
        if end < len(self.tokens) and self.tokens[end].group() in (',', ';'):
            entry_end = self.tokens[end].end()
        return self.text[:entry_start] + self.text[entry_end:]


def ensure_builder_skirt(source):
    """Give engineer-built mobile units the reservation required by build orders.

    The game reads skirtSize even for mobile units placed by an engineer. Keep
    an artist's explicit value; only infer one when the field is absent.
    """
    doc = Blueprint(source)
    if doc.span(('skirtSize',)):
        return source
    try:
        tags = set(doc.strings(('tags',)))
    except ValueError:
        return source
    if 'MOBILE' not in tags or not any(tag.startswith('BUILDABLE_BY_') and tag.endswith('_ENGINEER') for tag in tags):
        return source

    def even_reservation(values):
        numbers = [value for value in values if isinstance(value, (int, float)) and math.isfinite(value) and value > 0]
        return max(2, 2 * math.ceil(max(numbers, default=2) / 2))

    x = even_reservation((doc.scalar(('footprint', 'x')),
                          doc.scalar(('collisionInfo', 'collisionSize', 'x'))))
    y = even_reservation((doc.scalar(('footprint', 'y')),
                          doc.scalar(('collisionInfo', 'collisionSize', 'z'))))
    return doc.set(('skirtSize',), f'{{ x = {x}, y = {y} }}')


def path_label(path):
    return '.'.join(map(str, path))


def lua_compile(source, engine):
    """Use the installed game compiler, load only: never call lua_pcall."""
    dll = Path(engine) / 'Sanctuary_Data/Plugins/x86_64/lua51.dll' if engine else None
    if not dll or not dll.is_file():
        return False, 'Game Lua compiler unavailable; delimiter validation only'
    lib = ctypes.CDLL(str(dll))
    lib.luaL_newstate.restype = ctypes.c_void_p
    lib.luaL_loadbuffer.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p]
    lib.lua_tolstring.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    lib.lua_tolstring.restype = ctypes.c_char_p
    lib.lua_close.argtypes = [ctypes.c_void_p]
    state = lib.luaL_newstate()
    if not state:
        raise RuntimeError('Could not allocate Lua compiler')
    try:
        data = source.encode('utf-8')
        if lib.luaL_loadbuffer(state, data, len(data), b'@studio_blueprint'):
            raise ValueError(lib.lua_tolstring(state, -1, None).decode('utf-8', 'replace'))
    finally:
        lib.lua_close(state)
    return True, 'Game Lua syntax compiler passed (source not executed)'


def safe_member(value):
    p = PurePosixPath(str(value).replace('\\', '/'))
    if p.is_absolute() or not p.parts or any(s in ('..', '.') or ':' in s for s in p.parts):
        raise ValueError('Unsafe asset path: ' + str(value))
    return p.as_posix()


class AssetLibrary:
    def __init__(self, engine=None, project=None):
        self.engine = Path(engine) if engine else None
        self.project = Path(project) if project else None
        self.records = {}
        self._archives = {}
        self._json_cache = OrderedDict()

    def close(self):
        for archive in self._archives.values():
            archive.close()
        self._archives.clear()
        self._json_cache.clear()

    def __del__(self):
        self.close()

    def scan(self):
        self.close()
        self.records.clear()
        if self.engine:
            root = self.engine / 'Sanctuary_Data/Gamedata'
            for name in ('VFX', 'Environment', 'Audio', 'Units', 'Projectiles', 'UI'):
                archive = root / (name + '.sanpack')
                if archive.is_file():
                    with zipfile.ZipFile(archive) as z:
                        for item in z.infolist():
                            if not item.is_dir():
                                self.records[item.filename.lower()] = (str(archive), item.filename)
                folder = root / name
                if folder.is_dir():
                    for file in folder.rglob('*'):
                        if file.is_file():
                            self.records[file.relative_to(root).as_posix().lower()] = (str(file), '')
        if self.project:
            for name in ('VFX', 'Environment', 'Audio', 'Units', 'Projectiles', 'UI'):
                for file in (self.project / name).rglob('*'):
                    if file.is_file():
                        self.records[file.relative_to(self.project).as_posix().lower()] = (str(file), '')
        return self

    def read(self, name):
        location, member = self.records[safe_member(name).lower()]
        if member:
            if location not in self._archives:
                self._archives[location] = zipfile.ZipFile(location)
            return self._archives[location].read(member)
        return Path(location).read_bytes()

    def json(self, name):
        from .vfx_documents import decode_asset
        key = safe_member(name).lower()
        location, member = self.records[key]
        # Loose project files can change between reads. Archive reads are cached
        # until the explicit Refresh operation builds a fresh library.
        stamp = None if member else (Path(location).stat().st_mtime_ns, Path(location).stat().st_size)
        cache_key = (key, stamp)
        if cache_key not in self._json_cache:
            self._json_cache[cache_key] = decode_asset(self.read(name), name)
            if len(self._json_cache) > 128:
                self._json_cache.popitem(last=False)
        self._json_cache.move_to_end(cache_key)
        return copy.deepcopy(self._json_cache[cache_key])

    def clone(self, name, namespace, data_override=None):
        if not self.project:
            raise ValueError('Create a project first')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', namespace):
            raise ValueError('Effect name may contain letters, numbers, underscores and hyphens')
        mapping, blobs = {}, {}
        def visit(asset):
            key = asset.lower()
            if key in mapping:
                return mapping[key]
            asset = safe_member(asset)
            # Windows folds directory casing, while archive member names may not.
            # Use one canonical spelling for every cloned dependency and reference.
            destination = 'VFX/Studio/' + namespace + '/' + asset.lower()
            mapping[key] = destination
            blob = self.read(asset)
            if Path(asset).suffix.lower() in ('.sanvfx', '.sanmaterial', '.sanbeam', '.sandecal'):
                from .vfx_documents import decode_asset, encode_asset
                data = copy.deepcopy(data_override) if key == name.lower() and data_override is not None else decode_asset(blob, asset)
                def rewrite(value):
                    if isinstance(value, dict):
                        return {k: rewrite(v) for k, v in value.items()}
                    if isinstance(value, list):
                        return [rewrite(v) for v in value]
                    if isinstance(value, str) and value.lower() in self.records:
                        return visit(value)
                    return value
                blob = encode_asset(rewrite(data), asset)
            blobs[destination] = blob
            return destination
        result = visit(name)
        for dest in blobs:
            resolved=(self.project/dest).resolve()
            if self.project.resolve() not in resolved.parents:
                raise ValueError('Effect destination escapes project: '+dest)
            if resolved.exists():
                raise FileExistsError('Effect name already exists; choose a new name: ' + namespace)
        for dest, blob in blobs.items():
            path = (self.project / dest).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(blob)
        self.scan()
        return result, len(blobs)


def json_leaves(value, path=()):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from json_leaves(v, path + (k,))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from json_leaves(v, path + (i,))
    else:
        yield path, value


def json_set(value, path, replacement):
    result = copy.deepcopy(value)
    target = result
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = replacement
    return result
