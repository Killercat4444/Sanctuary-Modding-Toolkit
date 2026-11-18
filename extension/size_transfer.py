"""Copy only explicit size vectors between Lua documents; never execute Lua."""
import math
from .studio_core import Blueprint

SIZE_GROUPS = {
    'COLLISION': [(('collisionInfo', 'collisionSize'), 3, False),
                  (('collisionInfo', 'centerOffset'), 3, True)],
    'FOOTPRINT': [(('footprint',), 2, False)],
    'SELECTION': [(('visuals', 'selection', 'size'), 2, False),
                  (('visuals', 'selection', 'offset'), 3, True)],
    'SKIRT': [(('skirtSize',), 2, False)],
}


def literal_table(doc, path):
    span = doc.span(path)
    if not span:
        return False
    start = next(i for i, token in enumerate(doc.tokens) if token.start() == span[0])
    return doc.tokens[start].group() == '{' and doc.tokens[doc.pairs[start]].end() == span[1]


def editable_parents(doc, path):
    for n in range(1, len(path)):
        raw = doc.raw(path[:n])
        if raw is not None and not literal_table(doc, path[:n]):
            raise ValueError('.'.join(path[:n]) + ' is an expression; cannot safely copy sizes into it.')


def transfer_sizes(source, destination, group='ALL'):
    """Keep raw precision and all destination source outside selected vectors.

    Required dimensions must exist as finite positive literal numbers. Missing
    optional offsets mirror the donor's absence instead of retaining a different
    destination offset or inventing a zero/default from the preview UI.
    """
    if group not in ('ALL', *SIZE_GROUPS):
        raise ValueError('Choose a valid size group.')
    donor, target = Blueprint(source), Blueprint(destination)
    fields = [field for name, members in SIZE_GROUPS.items()
              if group == 'ALL' or name == group for field in members]
    edits = []
    for path, length, optional in fields:
        editable_parents(donor, path)
        editable_parents(target, path)
        raw = donor.raw(path)
        if raw is None:
            if not optional:
                raise ValueError('Source Lua is missing ' + '.'.join(path) + '. Save the source sizes first, or choose another group.')
        else:
            if not literal_table(donor, path):
                raise ValueError('Source ' + '.'.join(path) + ' must be a literal vector table.')
            for axis in 'xyz'[:length]:
                value = donor.scalar(path + (axis,))
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError('Source ' + '.'.join(path + (axis,)) + ' must be a finite literal number.')
                if not optional and value <= 0:
                    raise ValueError('Source ' + '.'.join(path + (axis,)) + ' must be positive.')
        edits.append((path, raw))
    result = destination
    changed = []
    for path, raw in edits:
        doc = Blueprint(result)
        old = doc.raw(path)
        if raw is None:
            if old is not None:
                result = doc.remove(path)
                changed.append('.'.join(path))
        elif raw != old:
            result = doc.set(path, raw)
            changed.append('.'.join(path))
    return result, changed
