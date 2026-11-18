"""Non-executing, span-based editor for Sanctuary's declarative Lua templates.

Only explicit field paths are changed. All other source, including custom
classes, bones, expressions and comments, stays byte-for-byte unchanged.
"""
import json
import re

TOKEN = re.compile(r'''--\[(=*)\[.*?\]\1\]|--[^\n]*|\[(=*)\[.*?\]\2\]|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[A-Za-z_]\w*|[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|\S''', re.S)


class Document:
    def __init__(self, text, root_name='UnitTemplate'):
        self.text = text
        self.tokens = [m for m in TOKEN.finditer(text) if not m.group().startswith('--')]
        self.pairs = {}
        stack = []
        for i, token in enumerate(self.tokens):
            v = token.group()
            if v in ('{', '[', '('): stack.append((v, i))
            elif v in ('}', ']', ')'):
                if not stack or stack[-1][0] != {'}':'{', ']':'[', ')':'('}[v]:
                    raise ValueError('Unbalanced Lua delimiters; edit the source before using the form')
                _, start = stack.pop(); self.pairs[start] = i
        if stack: raise ValueError('Unbalanced Lua delimiters')
        self.root = next((i+2 for i in range(len(self.tokens)-2)
                          if self.tokens[i].group() == root_name
                          and self.tokens[i+1].group() == '=' and self.tokens[i+2].group() == '{'), None)
        if self.root is None: raise ValueError('No ' + root_name + ' table found')

    def fields(self, table):
        i = table + 1
        end = self.pairs[table]
        result = {}
        array_index = 1
        while i < end:
            name = None
            start = None
            if i+2 < end and re.fullmatch(r'[A-Za-z_]\w*', self.tokens[i].group()) and self.tokens[i+1].group() == '=':
                name = self.tokens[i].group()
                start = i+2
            elif (i+4 < end and self.tokens[i].group() == '['
                  and self.pairs.get(i) == i+2
                  and self.tokens[i+1].group().startswith(('"', "'"))
                  and self.tokens[i+3].group() == '='):
                key = self.tokens[i+1].group()
                if key.startswith('"'):
                    try: name = json.loads(key)
                    except ValueError: pass
                else:
                    name = key[1:-1].replace("\\'", "'").replace('\\\\', '\\')
                start = i+4
            if name is not None:
                j = start
                while j < end and self.tokens[j].group() not in (',', ';'):
                    j = self.pairs.get(j, j) + 1
                result[name] = (start, j)
                i = j+1
            elif self.tokens[i].group() == '{':
                result[array_index] = (i, self.pairs[i]+1)
                array_index += 1
                i = self.pairs[i]+1
            else: i = self.pairs.get(i, i)+1
        return result

    def span(self, path):
        table = self.root
        for index, field in enumerate(path):
            found = self.fields(table).get(field)
            if found is None: return None
            start, end = found
            if index == len(path)-1:
                return self.tokens[start].start(), self.tokens[end-1].end()
            if self.tokens[start].group() != '{': return None
            table = start

    def raw(self, path):
        span = self.span(path)
        return self.text[slice(*span)] if span else None

    def scalar(self, path, default=None):
        raw = self.raw(path)
        if raw is None: return default
        if raw.startswith('"'):
            try: return json.loads(raw)
            except ValueError: return default
        if raw.startswith("'") and raw.endswith("'"):
            return raw[1:-1].replace("\\'", "'").replace('\\\\', '\\')
        try: return float(raw) if any(c in raw.lower() for c in '.e') else int(raw)
        except ValueError: return default

    def strings(self, path):
        raw = self.raw(path)
        if raw is None: return []
        values = [m.group() for m in TOKEN.finditer(raw) if not m.group().startswith('--')]
        if any(v not in ('{','}',',',';') and not v.startswith(('"', "'")) for v in values):
            raise ValueError('The tag list uses an expression; edit it in the raw Lua source')
        return [json.loads(v) if v.startswith('"') else v[1:-1] for v in values if v.startswith(('"', "'"))]

    def replace(self, changes):
        edits = []
        for path, value in changes.items():
            span = self.span(path)
            if span is None: raise ValueError('Template has no editable field: ' + '.'.join(map(str,path)))
            edits.append((*span, value))
        result = self.text
        for start, end, value in sorted(edits, reverse=True): result = result[:start] + value + result[end:]
        return result


def string_table(values):
    return '{ ' + ', '.join(json.dumps(v, ensure_ascii=False) for v in dict.fromkeys(values)) + ' }'
