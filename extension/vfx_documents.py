"""Editable JSON assets and declarative Lua beams; never execute game source."""
import copy
import json
import math
import re


def lua_value(value):
    if value is None:
        return 'nil'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError('Lua values must be finite')
        return repr(value)
    if isinstance(value, list):
        return '{ ' + ', '.join(lua_value(child) for child in value) + ' }'
    if isinstance(value, dict):
        return '{ ' + ', '.join((str(key) if isinstance(key, str) and re.fullmatch(r'[A-Za-z_]\w*', key)
                                else '[' + lua_value(key) + ']') + ' = ' + lua_value(child)
                                for key, child in value.items()) + ' }'
    raise ValueError('Unsupported Lua value')


def decode_asset(blob, path):
    path=str(path)
    text = blob.decode('utf-8-sig') if isinstance(blob, bytes) else blob
    if not path.lower().endswith('.sanbeam') or text.lstrip().startswith('{'):
        return json.loads(text)
    from .studio_core import Blueprint
    doc = Blueprint(text, 'beamTemplate')

    def table(index):
        fields = doc.fields(index)
        values = {}
        for key, (start, end) in fields.items():
            if doc.tokens[start].group() == '{' and doc.pairs[start] == end - 1:
                value = table(start)
            else:
                raw = text[doc.tokens[start].start():doc.tokens[end - 1].end()]
                if raw in ('true', 'false', 'nil'):
                    value = {'true': True, 'false': False, 'nil': None}[raw]
                else:
                    try:
                        value = json.loads(raw)
                    except ValueError:
                        if raw.startswith("'") and raw.endswith("'"):
                            value = raw[1:-1]
                        else:
                            raise ValueError('Beam contains an expression; use the source editor: ' + raw)
            values[key] = value
        if values and all(isinstance(k, int) for k in values) and sorted(values) == list(range(1, len(values) + 1)):
            return [values[k] for k in sorted(values)]
        return values

    return table(doc.root)


def encode_asset(data, path):
    path=str(path)
    if path.lower().endswith('.sanbeam'):
        return ('-- Sanctuary Studio custom beam\nbeamTemplate = ' + lua_value(data) + '\n').encode('utf-8')
    return (json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')


def scale_colors(data, tint=None, intensity=1, opacity=1):
    result = copy.deepcopy(data)
    def visit(value):
        if isinstance(value, dict):
            if all(k in value for k in 'rgb'):
                # Preserve HDR intensity, including black and values below one.
                strength = max(0, *(float(value[k]) for k in 'rgb'))
                for i, key in enumerate('rgb'):
                    value[key] = (tint[i] * strength if tint is not None else value[key]) * intensity
                if isinstance(value.get('a'), (int, float)):
                    value['a'] *= opacity
            else:
                for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(result)
    return result


def display_name(path, data=None):
    from pathlib import PurePosixPath
    data = data or {}
    name = data.get('name') or data.get('general', {}).get('name')
    if not name and data.get('transforms'):
        name = data['transforms'][0].get('name')
    name = name or PurePosixPath(path).stem
    name = re.sub(r'^(?:v[a-z]{2}\d+|p[a-z]{2}\d+)\s*[_ -]*', '', name, flags=re.I)
    name = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', name).replace('_', ' ')
    name=re.sub(r'\s+', ' ', name).strip().title() or PurePosixPath(path).stem
    return re.sub(r'\b(Eda|Csg)\b',lambda m: {'Eda':'EDA','Csg':'Chosen'}[m.group(1)],name)
