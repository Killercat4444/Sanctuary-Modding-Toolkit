"""Debounced native controls; primitive queues survive scene changes and undo safely."""
import json,time,math,struct
from contextlib import contextmanager
import bpy
from . import shield_authoring as native

_PENDING={};_FILLING=0;DEBOUNCE=.08
@contextmanager
def suppress():
 global _FILLING
 _FILLING+=1
 try:yield
 finally:_FILLING-=1

def value(item):return float(item.number) if item.group=='floats' else list(item.color if item.group=='colors' else item.vector)
def draft_scene(token):return next((s for s in bpy.data.scenes if s.get('studio_shield_draft') and s.sanctuary_shield.draft_token==token),None)
def queue(scene,item):
 state=scene.sanctuary_shield
 if not state.draft_token or not item.role or not item.group or not item.key:return
 _PENDING.setdefault(state.draft_token,{})[(item.role,item.group,item.key)]=(value(item),time.monotonic())
 if state.live_preview and not bpy.app.timers.is_registered(flush):bpy.app.timers.register(flush,first_interval=DEBOUNCE)

def parameter_changed(item,context):
 if _FILLING:return
 scene=item.id_data
 if isinstance(scene,bpy.types.Scene) and scene.get('studio_shield_draft'):
  if item.group=='colors':color_fields(item)
  queue(scene,item)

def color_fields(item):
 strength=max(float(v) for v in item.color[:3])
 with suppress():
  item.hdr_strength=strength
  item.hue=tuple(v/strength for v in item.color[:3]) if strength>0 else (1,1,1)

def hue_changed(item,context):
 if _FILLING:return
 with suppress():item.color=tuple(v*item.hdr_strength for v in item.hue)+(item.color[3],)
 parameter_changed(item,context)

def enabled_changed(state,context):
 if _FILLING:return
 scene=state.id_data
 if not isinstance(scene,bpy.types.Scene) or not scene.get('studio_shield_draft'):return
 if state.live_preview and _PENDING.get(state.draft_token) and not bpy.app.timers.is_registered(flush):bpy.app.timers.register(flush,first_interval=DEBOUNCE)

def activate(scene,role):
 state=scene.sanctuary_shield;obj=getattr(state,role+'_object')
 if not obj or obj.name not in scene.objects:raise ValueError('Shield surface no longer exists')
 layer=scene.view_layers[0]
 for other in scene.objects:other.select_set(False,view_layer=layer)
 obj.hide_select=False;obj.hide_set(False,view_layer=layer);obj.select_set(True,view_layer=layer);obj.active_material_index=0;layer.objects.active=obj
 for window in bpy.context.window_manager.windows:
  if window.scene!=scene:continue
  for area in window.screen.areas:
   if area.type=='VIEW_3D' and area.spaces.active.shading.type not in ('MATERIAL','RENDERED'):area.spaces.active.shading.type='MATERIAL'
   if area.type=='NODE_EDITOR' and area.ui_type=='ShaderNodeTree':area.spaces.active.shader_type='OBJECT';area.spaces.active.pin=False
 return obj

def layer_changed(state,context):
 if _FILLING:return
 scene=state.id_data
 if not isinstance(scene,bpy.types.Scene) or not scene.get('studio_shield_draft'):return
 with suppress():
  state.parameter_index=next((i for i,p in enumerate(state.parameters) if p.role==state.edit_layer),0)
  state.texture_index=next((i for i,p in enumerate(state.texture_slots) if json.loads(p.value)[0]==state.edit_layer),0)
 try:activate(scene,state.edit_layer);visibility_changed(state,context)
 except ValueError as exc:state.status=str(exc)

def parameter_selected(state,context):
 if _FILLING or not state.parameters or not 0<=state.parameter_index<len(state.parameters):return
 role=state.parameters[state.parameter_index].role
 if role!=state.edit_layer:state.edit_layer=role

def texture_selected(state,context):
 if _FILLING or not state.texture_slots or not 0<=state.texture_index<len(state.texture_slots):return
 role=json.loads(state.texture_slots[state.texture_index].value)[0]
 if role!=state.edit_layer:state.edit_layer=role

def visibility_changed(state,context):
 scene=state.id_data
 if not isinstance(scene,bpy.types.Scene) or not scene.get('studio_shield_draft'):return
 for role in ('body','glow'):
  obj=getattr(state,role+'_object')
  if obj:
   hidden=state.solo_layer and state.edit_layer!=role;obj.hide_render=hidden;obj.hide_set(hidden,view_layer=scene.view_layers[0])

def comparison_changed(state,context):
 if _FILLING:return
 scene=state.id_data
 if not isinstance(scene,bpy.types.Scene) or not scene.get('studio_shield_draft'):return
 if state.compare_original and not (state.original_body_material and state.original_glow_material):
  with suppress():state.compare_original=False
  state.status='This older draft has no source comparison snapshot. Your private edits are retained; reopen from the source for a new comparison.'
  return
 for role in ('body','glow'):
  obj=getattr(state,role+'_object');material=getattr(state,('original_' if state.compare_original else '')+role+'_material')
  if obj and material:obj.data.materials.clear();obj.data.materials.append(material)
 from . import studio_shields as ui
 ui.apply_glow_scale(state)
 state.status='Viewing original source comparison; your edits are retained and exported.' if state.compare_original else 'Viewing your private editable native material.'

def preview_supported(material,group,key):
 if key=='_VertexPositionScale':return True
 roots=[n for n in material.node_tree.nodes if n.type=='OUTPUT_MATERIAL'];reachable=set(roots)
 while roots:
  node=roots.pop()
  for link in material.node_tree.links:
   if link.to_node==node and link.from_node not in reachable:reachable.add(link.from_node);roots.append(link.from_node)
 return any(n in reachable and n.get('shield_binding') and json.loads(n['shield_binding'])[:2]==[group,key] for n in material.node_tree.nodes)

def flush_scene(scene,force=False):
 from . import studio_shields as ui
 state=scene.sanctuary_shield;pending=_PENDING.get(state.draft_token)
 if not pending or not force and not state.live_preview:return
 selected={k:v[0] for k,v in pending.items() if force or time.monotonic()-v[1]>=DEBOUNCE}
 if not selected:return
 previous=[];documents={};original_documents={};transforms={};initials={}
 try:
  for role in ('body','glow'):
   text=getattr(state,role+'_document');obj=getattr(state,role+'_object')
   if not text or not obj:raise ValueError('Native '+role+' document or surface is missing; reopen this draft from its source')
   original_documents[role]=text.as_string();documents[role]=json.loads(original_documents[role])
   transforms[role]=dict(signature=obj.get('shield_transform_signature'),rotation_mode=obj.rotation_mode,
    location=tuple(obj.location),rotation_euler=tuple(obj.rotation_euler),rotation_quaternion=tuple(obj.rotation_quaternion),rotation_axis_angle=tuple(obj.rotation_axis_angle),scale=tuple(obj.scale))
  initials={(p.role,p.group,p.key):p.initial_value for p in state.parameters if (p.role,p.group,p.key) in selected}
  for (role,group,key),current in selected.items():
   if not all(math.isfinite(float(v)) for v in (current if isinstance(current,list) else [current])):raise ValueError('Native control must be finite: '+key)
   data=documents[role]
   if group=='tiling':next(t for t in data['textures'] if t['key']==key)['tilingOffset']=dict(zip('xyzw',current))
   else:next(t for t in data[group] if t['key']==key)['value']=current if group=='floats' else dict(zip('rgba' if group=='colors' else 'xyzw',current))
  for role in ('body','glow'):native.validate_material(documents[role],'surface' if role=='body' else 'glow')
  for (role,group,key),current in selected.items():
   matches=[]
   for node in getattr(state,role+'_material').node_tree.nodes:
    binding=node.get('shield_binding')
    if not binding:continue
    node_group,node_key,component=json.loads(binding)
    if (node_group,node_key)!=(group,key):continue
    previous.append((node,list(node.outputs[0].default_value) if hasattr(node.outputs[0].default_value,'__len__') else float(node.outputs[0].default_value)))
    node.outputs[0].default_value=current if group in ('floats','colors') else current['xyzw'.index(component)];matches.append(node)
   if not matches:raise ValueError('Native binding missing: '+role+' '+key)
  for role in ('body','glow'):ui.write_document(getattr(state,role+'_document'),documents[role]);getattr(state,role+'_material').node_tree.update_tag()
  ui.apply_glow_scale(state)
  with suppress():
   for item in state.parameters:
    if (item.role,item.group,item.key) in selected:item.initial_value=json.dumps(value(item))
  state.preview_error='';state.status='Native controls updated in the visible draft. Game-only fields export but may have no Blender effect.'
  scene.view_layers[0].update()
 except Exception as exc:
  for node,old in previous:node.outputs[0].default_value=old
  for role,old in original_documents.items():
   text=getattr(state,role+'_document')
   if text:text.clear();text.write(old)
  for role,transform in transforms.items():
   obj=getattr(state,role+'_object')
   for key in ('rotation_mode','location','rotation_euler','rotation_quaternion','rotation_axis_angle','scale'):setattr(obj,key,transform[key])
   if transform['signature'] is not None:obj['shield_transform_signature']=transform['signature']
  with suppress():
   for item in state.parameters:
    previous_initial=initials.get((item.role,item.group,item.key))
    if previous_initial is not None:item.initial_value=previous_initial
  state.preview_error=str(exc);state.status='Native preview change was not applied: '+str(exc)+'. Correct the control before export.'
 finally:
  for key in selected:pending.pop(key,None)
  if not pending:_PENDING.pop(state.draft_token,None)

def flush():
 for token in list(_PENDING):
  scene=draft_scene(token)
  if scene is None:_PENDING.pop(token,None)
  else:flush_scene(scene)
 return DEBOUNCE if any(draft_scene(t) is not None and draft_scene(t).sanctuary_shield.live_preview for t in _PENDING) else None

def cancel_scene(scene):
 _PENDING.pop(scene.sanctuary_shield.draft_token,None)
 if not _PENDING and bpy.app.timers.is_registered(flush):bpy.app.timers.unregister(flush)

def before_undo(*args):
 _PENDING.clear()
 if bpy.app.timers.is_registered(flush):bpy.app.timers.unregister(flush)

def after_undo(*args):
 # RNA handles can be replaced by undo; rebuild primitive requests from current scene data.
 for scene in bpy.data.scenes:
  if not scene.get('studio_shield_draft'):continue
  state=scene.sanctuary_shield
  try:
   documents={r:json.loads(getattr(state,r+'_document').as_string()) for r in ('body','glow')}
   for item in state.parameters:
    data=documents[item.role]
    original=next(t for t in data['textures' if item.group=='tiling' else item.group] if t['key']==item.key)
    expected=original['tilingOffset'] if item.group=='tiling' else original['value']
    expected=list(expected.get(c,1 if item.group=='tiling' and c in 'xy' else 0) for c in ('rgba' if item.group=='colors' else 'xyzw')) if isinstance(expected,dict) else expected
    rounded=lambda n:struct.unpack('<f',struct.pack('<f',float(n)))[0]
    expected=[rounded(n) for n in expected] if isinstance(expected,list) else rounded(expected)
    if value(item)!=expected:queue(scene,item)
    if item.group=='colors':color_fields(item)
  except (AttributeError,KeyError,TypeError,ValueError,StopIteration,OverflowError,ReferenceError):state.preview_error='Native draft data unavailable after file load or undo; reopen or correct this draft before export.'

def register():
 for handlers,fn in ((bpy.app.handlers.undo_pre,before_undo),(bpy.app.handlers.redo_pre,before_undo),(bpy.app.handlers.undo_post,after_undo),(bpy.app.handlers.redo_post,after_undo)):
  if fn not in handlers:handlers.append(fn)

def unregister():
 before_undo()
 for handlers,fn in ((bpy.app.handlers.undo_pre,before_undo),(bpy.app.handlers.redo_pre,before_undo),(bpy.app.handlers.undo_post,after_undo),(bpy.app.handlers.redo_post,after_undo)):
  if fn in handlers:handlers.remove(fn)
