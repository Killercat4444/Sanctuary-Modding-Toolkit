"""Saved unit portraits: read-only draws, scoped identities, deferred image refresh."""
from collections import OrderedDict
from pathlib import Path
import bpy
from . import unit_icons

_CACHE=OrderedDict();_PENDING=OrderedDict();_CREATED={};MAX_CACHE=32

def identity(project,uid):
 root=Path(project).resolve();uid=str(uid).lower()
 if not unit_icons.UNIT.fullmatch(uid):raise ValueError('Choose a valid Unit ID to display its saved icon.')
 path=unit_icons.owned(root,'Source/Icons/'+uid+'.png')
 return (str(root).casefold(),uid),path

def source_scene(context):
 scene=context.scene;uid=scene.get('studio_icon_unit')
 if not uid:return scene
 source=bpy.data.scenes.get(scene.get('studio_source_scene',''))
 if not source:raise ValueError('Icon source unit scene is missing; return to your unit and reopen its icon scene.')
 if source.sanctuary_toolkit.unit_id.lower()!=str(uid).lower():raise ValueError('This icon scene belongs to '+str(uid)+'. Return to that unit before saving its icon.')
 return source

def target(context):
 from . import mod_projects as mp
 source=source_scene(context)
 with bpy.context.temp_override(scene=source,view_layer=source.view_layers[0]):project=mp.active_project(bpy.context,required=False)
 if not project:raise ValueError('Choose your authoring project to display its saved unit icon.')
 return identity(project,source.sanctuary_toolkit.unit_id)

def stamp(path):
 info=path.stat();return (info.st_mtime_ns,info.st_size)

def safe_stamp(path):
 try:return stamp(path)
 except OSError:return None

def _remove_created(name):
 image=bpy.data.images.get(name);pointer=_CREATED.get(name)
 if image and image.as_pointer()==pointer and image.users==0 and not image.use_fake_user:
  # Image editor references can be weak: retain any visibly selected image.
  if not any(space.type=='IMAGE_EDITOR' and space.image==image for screen in bpy.data.screens for area in screen.areas for space in area.spaces):bpy.data.images.remove(image)
 _CREATED.pop(name,None)

def prepare(project,uid):
 key,path=identity(project,uid)
 try:
  version=stamp(path);before=set(bpy.data.images)
  from .icon_camera import refresh_icon_image
  image=refresh_icon_image(path)
  if not len(image.pixels):raise ValueError('Saved icon PNG has no readable pixels.')
  if image not in before:_CREATED[image.name]=image.as_pointer()
  if stamp(path)!=version:raise OSError('Saved icon changed while loading; retry Reload Saved Icon.')
  entry={'stamp':version,'image':image.name,'error':'','path':str(path)}
 except Exception as exc:
  entry={'stamp':safe_stamp(path),'image':'','error':'Saved icon preview could not load: '+str(exc),'path':str(path)}
 _CACHE[key]=entry;_CACHE.move_to_end(key)
 while len(_CACHE)>MAX_CACHE:
  _,old=_CACHE.popitem(last=False)
  if old['image'] and not any(e['image']==old['image'] for e in _CACHE.values()):_remove_created(old['image'])
 return entry

def invalidate(project,uid):
 key,_=identity(project,uid);_CACHE.pop(key,None);_PENDING.pop(key,None)

def request(context):
 """Only file stats, Python bookkeeping and timer scheduling during draw."""
 try:
  key,path=target(context)
  if not path.is_file():return {'icon':0,'message':'No saved icon for '+key[1]+'. Render it or use an existing PNG.'}
  version=stamp(path);entry=_CACHE.get(key)
  if entry and entry['stamp']==version:
   if entry['error']:return {'icon':0,'message':entry['error']}
   image=bpy.data.images.get(entry['image'])
   if image and image.preview:
    _CACHE.move_to_end(key);return {'icon':image.preview.icon_id,'message':''}
  _PENDING[key]=(str(path.parents[2]),key[1])
  # This queue contains primitive paths/IDs, never stale scene/RNA pointers.
  while len(_PENDING)>MAX_CACHE:_PENDING.popitem(last=False)
  if not bpy.app.timers.is_registered(flush):bpy.app.timers.register(flush,first_interval=.01)
  return {'icon':0,'message':'Preparing saved icon preview...'}
 except Exception as exc:return {'icon':0,'message':str(exc)}

def flush():
 if not _PENDING:return None
 _,(project,uid)=_PENDING.popitem(last=False);prepare(project,uid)
 for screen in bpy.data.screens:
  for area in screen.areas:
   if area.type in ('VIEW_3D','PROPERTIES'):area.tag_redraw()
 return .01 if _PENDING else None

def bind_saved(context):
 from . import mod_projects as mp
 project=mp.active_project(context,required=False)
 if project:
  _,path=identity(project,context.scene.sanctuary_toolkit.unit_id)
  if path.is_file():context.scene.sanctuary_toolkit.mod_unit_icon_png=str(path)
  invalidate(project,context.scene.sanctuary_toolkit.unit_id)

@bpy.app.handlers.persistent
def clear(*args):
 if bpy.app.timers.is_registered(flush):bpy.app.timers.unregister(flush)
 _CACHE.clear();_PENDING.clear()

class SANCTUARY_OT_unit_icon_reload(bpy.types.Operator):
 bl_idname='sanctuary.unit_icon_reload';bl_label='Reload Saved Unit Icon'
 def execute(self,context):
  try:
   from . import mod_projects as mp
   source=source_scene(context)
   with bpy.context.temp_override(scene=source,view_layer=source.view_layers[0]):
    project=mp.icon_project(bpy.context);uid=source.sanctuary_toolkit.unit_id;_,path=identity(project,uid)
    if not path.is_file():raise ValueError('No saved PNG for '+uid+'. Render or choose your image first.')
    invalidate(project,uid);entry=prepare(project,uid)
    if entry['error']:raise ValueError(entry['error'])
    source.sanctuary_toolkit.mod_unit_icon_png=str(path)
   self.report({'INFO'},'Saved icon reloaded for '+uid+'.');return {'FINISHED'}
  except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}

def register():
 clear()
 bpy.utils.register_class(SANCTUARY_OT_unit_icon_reload)
 for handler in (bpy.app.handlers.load_post,bpy.app.handlers.undo_post,bpy.app.handlers.redo_post):
  if clear not in handler:handler.append(clear)

def unregister():
 clear()
 for handler in (bpy.app.handlers.load_post,bpy.app.handlers.undo_post,bpy.app.handlers.redo_post):
  if clear in handler:handler.remove(clear)
 for name in list(_CREATED):_remove_created(name)
 bpy.utils.unregister_class(SANCTUARY_OT_unit_icon_reload)
