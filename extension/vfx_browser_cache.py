"""Bounded, Blender-independent metadata caches; RNA mutations invalidate rows."""
from collections import OrderedDict
import json

_ROWS=OrderedDict();_FILTERS=OrderedDict()
def row(item):
 key=(item.name,item.value)
 if key not in _ROWS:
  try:value=json.loads(item.value) if item.value else {}
  except ValueError:value={}
  _ROWS[key]=value
  if len(_ROWS)>4096:_ROWS.popitem(last=False)
 _ROWS.move_to_end(key);return _ROWS[key]

def filter_rows(items,query,category,bit):
 # RNA collections can be edited by legacy/donor paths; include exact values
 # so same-length replacements and restored .blend content cannot go stale.
 signature=tuple((item.name,item.value) for item in items)
 key=(signature,query.casefold(),category,bit)
 if key not in _FILTERS:
  words=query.casefold().split();flags=[]
  for item in items:
   data=row(item);haystack=(item.name+' '+data.get('search','')+' '+data.get('name','')).casefold()
   match=all(word in haystack for word in words) and (category=='ALL' or data.get('category')==category)
   flags.append(bit if match else 0)
  _FILTERS[key]=tuple(flags)
  if len(_FILTERS)>8:_FILTERS.popitem(last=False)
 _FILTERS.move_to_end(key);return list(_FILTERS[key]),[]

def clear():_ROWS.clear();_FILTERS.clear()
