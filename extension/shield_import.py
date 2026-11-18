"""Blueprint-driven, owned shield previews beside imported units; no game writes."""
import hashlib,json,math,re,uuid,zipfile
from pathlib import Path
import bpy
from bpy.types import Operator,Panel
from bpy.props import IntProperty,StringProperty
from mathutils import Matrix,Vector
from .studio_core import Blueprint,AssetLibrary,safe_member
from . import shield_authoring as native,shield_nodes as graph

PREVIEW_GAIN=2.0**-14

def _engine(context):
 from . import studio,game_browser
 try:root=studio.engine(context)
 except (ValueError,AttributeError):root=None
 if not root:root=(game_browser.get_database_payload() or {}).get('engine_root')
 return Path(root) if root else None

def _template_root(path):
 if path:
  for parent in Path(path).parents:
   if parent.name.casefold()=='lua':return parent.parent
 return None

def source_definition(context,record=None,model_path=None):
 """Read the indexed unit's own blueprint; never substitute the active donor."""
 record=record or {};engine=_engine(context);path=Path(record['template_path']) if record.get('template_path') else None
 root=Path(record['project_root']) if record.get('project_root') else _template_root(path)
 if model_path:
  model_path=Path(model_path).resolve();uid=model_path.stem.removesuffix('_lod0')
  model_root=next((p.parent for p in model_path.parents if p.name.casefold()=='units'),None)
  candidates=[]
  if model_root:
   candidates += [model_root/case/'common/units/unitsTemplates'/uid/(uid+'.santp') for case in ('Lua','lua')]
  path=next((p for p in candidates if p.is_file()),None);root=model_root if path else None
  if path is None:
   # An explicitly loaded blueprint may reference a different stock model ID.
   from . import studio,mod_projects
   state=studio.settings(context)
   if state.blueprint:
    doc=Blueprint(state.blueprint.as_string())
    if doc.scalar(('general','modelTpId'),doc.scalar(('general','tpId')))==uid:
     root=mod_projects.active_project(context,required=False)
     return {'text':doc.text,'engine':engine,'root':Path(root) if root else None,'template':'Loaded unit blueprint','key':'loaded:'+doc.scalar(('general','tpId'))}
   if engine:path=engine/'LJ/lua/common/units/unitsTemplates'/uid/(uid+'.santp')
 if not path or not path.is_file():return None
 return {'text':path.read_text(encoding='utf-8-sig'),'engine':engine,'root':root,'template':str(path),'key':record.get('record_key') or 'file:'+str(path.resolve())}

def definitions(text):
 doc=Blueprint(text);indices=sorted({p[2] for p,_ in doc.leaves() if len(p)>2 and p[:2]==('defence','shields') and isinstance(p[2],int)})
 result=[];diagnostics=[];seen_bones=set();tags=doc.strings(('tags',));suffix=next((value for tag,value in (('EDA','EDA'),('CHOSEN','Chosen'),('GUARD','Guard')) if tag in tags),None)
 for index in indices:
  prefix=('defence','shields',index)
  try:
   if index!=len(result)+1 and index>1 and not doc.span(('defence','shields',index-1)):raise ValueError('Non-contiguous shield indices are not traversed by the game ipairs loader')
   radii=[doc.scalar(prefix+('radii',axis)) for axis in 'xyz'];offset=[doc.scalar(prefix+('offset',axis),0) for axis in 'xyz']
   if any(type(v) not in (int,float) or not math.isfinite(v) or v<=0 for v in radii):raise ValueError('radii.x/y/z must be positive finite native radii, not a diameter/expression')
   if any(type(v) not in (int,float) or not math.isfinite(v) for v in offset):raise ValueError('offset.x/y/z must be finite numbers')
   bone=doc.scalar(prefix+('boneName',))
   if not isinstance(bone,str) or not bone:raise ValueError('Missing shield boneName')
   if bone in seen_bones:diagnostics.append('Shield '+str(index)+': repeated bone '+bone+'; the stock game loader names shield entities from the bone, so multiple shields there need in-game/custom Lua validation.')
   seen_bones.add(bone)
   refs=[]
   for field,stem in (('materialPath','Shield Forcefield '),('glowMaterialPath','Shield Edge Detail ')):
    explicit=doc.scalar(prefix+(field,))
    if explicit is None:
     if suffix is None:
      suffix='EDA';diagnostics.append('Shield '+str(index)+': no stock faction tag; native loader falls back to EDA. Set explicit materialPath/glowMaterialPath for custom visuals.')
     explicit='VFX/Shields/'+stem+suffix+'.sanmaterial'
    if not isinstance(explicit,str):raise ValueError(field+' is an unsupported expression')
    refs.append(safe_member(explicit))
   result.append({'index':index,'name':doc.scalar(prefix+('name',),'Shield '+str(index)),'bone':bone,'radii':radii,'offset':offset,'body':refs[0],'glow':refs[1],
                  'material_resolution':'Explicit unit references or exact native faction-tag defaults'})
  except (ValueError,TypeError) as exc:diagnostics.append('Shield '+str(index)+': '+str(exc))
 return doc,result,diagnostics

def source_library(source):
 assets=AssetLibrary(source['engine'],source['root']).scan()
 # Only the imported source's declared package root is considered; no mod discovery.
 root=source['root']
 if root:
  packs=list((root/'packs').glob('*.sanpack'))
  for pack in sorted(packs):
   with zipfile.ZipFile(pack) as archive:
    for item in archive.infolist():
     if not item.is_dir():assets.records[safe_member(item.filename).casefold()]=(str(pack),item.filename)
  # Source-local loose files take precedence over that source's packs.
  for category in ('VFX','Units','Environment'):
   for file in (root/category).rglob('*'):
    if file.is_file():assets.records[file.relative_to(root).as_posix().casefold()]=(str(file),'')
 return assets

def _xyz(values,swap):return (values[0],values[2],values[1]) if swap else tuple(values)

def _rest_bone(assets,doc,name,swap):
 from .sanskeleton_io import parse_sanskeleton_bytes,_san_local_matrix
 from .utils import san_bone_global_to_blender
 uid=doc.scalar(('general','modelTpId'),doc.scalar(('general','tpId')))
 path='Units/'+str(uid)+'/LOD0/'+str(uid)+'.sanskeleton'
 if path.casefold() not in assets.records:raise ValueError('Missing native skeleton for shield bone '+name+': '+path)
 entries=sorted(parse_sanskeleton_bytes(assets.read(path))['bonesHierarchy'],key=lambda e:int(e['boneId']));matrices=[];matches=[]
 for entry in entries:
  parent=int(entry.get('parentBoneId',-1));local=_san_local_matrix(entry);world=matrices[parent]@local if parent>=0 else local;matrices.append(world)
  if entry.get('name')==name:matches.append(world)
 if len(matches)!=1:raise ValueError('Shield bone '+name+' has '+str(len(matches))+' native matches; cannot infer its center')
 return san_bone_global_to_blender(matches[0]) if swap else matches[0]

def _anchor(context,obj,rig,assets,doc,definition,owner,swap):
 anchor=bpy.data.objects.new('Shield '+str(definition['index'])+' bone anchor',None);context.collection.objects.link(anchor);anchor['shield_owner']=owner;anchor.hide_select=True
 anchor['san_shield_source']=obj;anchor['san_shield_index']=definition['index'];anchor['san_shield_role']='anchor'
 actual_rig=rig or obj.find_armature();root=actual_rig or obj
 bone=None
 if actual_rig:
  matches=[b for b in actual_rig.data.bones if b.get('san_original_name',b.name)==definition['bone']]
  if len(matches)==1:bone=matches[0].name
 if bone:
  constraint=anchor.constraints.new('COPY_TRANSFORMS');constraint.target=actual_rig;constraint.subtarget=bone;constraint.target_space=constraint.owner_space='WORLD'
 elif definition['bone']=='__Head':anchor.parent=root;anchor.matrix_parent_inverse=Matrix.Identity(4);anchor.matrix_basis=Matrix.Identity(4)
 else:
  anchor.parent=root;anchor.matrix_parent_inverse=Matrix.Identity(4);anchor.matrix_basis=_rest_bone(assets,doc,definition['bone'],swap)
  definition['pose_note']='Native rest-bone transform used; import the matching skeleton for animated bone attachment.'
 entity=bpy.data.objects.new('Shield '+str(definition['index'])+' offset',None);context.collection.objects.link(entity);entity.parent=anchor;entity.location=_xyz(definition['offset'],swap);entity['shield_owner']=owner;entity.hide_select=True
 entity['san_shield_source']=obj;entity['san_shield_index']=definition['index'];entity['san_shield_role']='offset'
 return entity

def remove_generated(owner):
 """Owned previews only: preserve source models/rigs and other imported units."""
 if not owner:return
 for scene in list(bpy.data.scenes):
  if scene.get('shield_owner')==owner:
   for window in bpy.context.window_manager.windows:
    if window.scene==scene:
     source=bpy.data.scenes.get(scene.get('studio_source_scene',''))
     if source:window.scene=source
   bpy.data.scenes.remove(scene)
 for name in ('objects','meshes','materials','images','worlds','cameras','lights'):
  collection=getattr(bpy.data,name)
  for item in list(collection):
   if item.get('shield_owner')==owner and (name=='objects' or item.users==0):collection.remove(item,do_unlink=True) if name=='objects' else collection.remove(item)
 from .studio_shields import discard_cache
 discard_cache(owner)

def fit_camera(scene,objects,camera,swap=True):
 points=[obj.matrix_world@Vector(corner) for obj in objects if obj.type=='MESH' for corner in obj.bound_box]
 if not points:raise ValueError('No shield/model bounds for render camera')
 minimum=Vector(tuple(min(p[i] for p in points) for i in range(3)));maximum=Vector(tuple(max(p[i] for p in points) for i in range(3)));center=(minimum+maximum)*.5;extent=max((maximum-minimum).length,1)
 direction=Vector((.8,-1.4,.9) if swap else (.8,.9,-1.4));camera.location=center+direction.normalized()*extent*2
 forward=(center-camera.location).normalized();up=Vector((0,0,1) if swap else (0,1,0));right=forward.cross(up).normalized();camera_up=right.cross(forward).normalized()
 camera.rotation_euler=Matrix((right,camera_up,-forward)).transposed().to_euler();camera.data.type='ORTHO';camera.data.ortho_scale=extent*1.1;camera.data.clip_end=max(1000,extent*10);scene.camera=camera

def render_scene(context,obj,rig,shells,owner):
 scene=bpy.data.scenes.new(obj.name+' Shield Render');scene['shield_owner']=owner;scene['studio_source_scene']=context.scene.name;scene['san_shield_source']=obj
 linked=set(shells+[obj]);linked.update(o for o in context.scene.objects if o.get('shield_owner')==owner)
 for item in list(linked):
  parent=item.parent
  while parent:linked.add(parent);parent=parent.parent
 if rig:linked.add(rig)
 for item in linked:scene.collection.objects.link(item)
 scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=32;scene.render.resolution_x=scene.render.resolution_y=768;scene.render.resolution_percentage=100;scene.render.fps=context.scene.render.fps
 scene.frame_start=context.scene.frame_start;scene.frame_end=context.scene.frame_end;scene.frame_set(context.scene.frame_current)
 world=bpy.data.worlds.new('Shield unit render background');world['shield_owner']=owner;world.use_nodes=True;world.node_tree.nodes['Background'].inputs[0].default_value=(.035,.045,.06,1);world.node_tree.nodes['Background'].inputs[1].default_value=.5;scene.world=world
 camera=bpy.data.objects.new('Shield unit render camera',bpy.data.cameras.new('Shield unit render camera'));camera['shield_owner']=camera.data['shield_owner']=owner;scene.collection.objects.link(camera)
 fit_camera(scene,[obj]+shells,camera,bool(obj.get('san_shield_swap_yz',True)))
 light=bpy.data.objects.new('Shield unit sunlight',bpy.data.lights.new('Shield unit sunlight','SUN'));light['shield_owner']=light.data['shield_owner']=owner;light.data.energy=3;light.rotation_euler=(.45,-.6,-.5);scene.collection.objects.link(light)
 scene['shield_preview_brightness']='Native HDR emission scaled by 2^-14 only for Blender scene visibility; native material/game values stay exact. Body opacity/facing remains a proxy.'
 return scene

def attach_imported_unit(context,obj,rig=None,record=None,model_path=None,options=None):
 """Nonfatal companion import; actionable errors never replace a missing source."""
 try:
  source=source_definition(context,record,model_path)
  parsed=definitions(source['text']) if source else None
 except (ValueError,OSError,UnicodeError) as exc:
  obj['san_shield_import_diagnostics']='Cannot read native unit shield definitions: '+str(exc)
  return []
 if not source:
  if record or model_path and re.fullmatch(r'u[a-z]{2}\d{4}',Path(model_path).stem.removesuffix('_lod0')):obj['san_shield_import_diagnostics']='No unit blueprint resolved; automatic shield references cannot be determined.'
  return []
 doc,items,diagnostics=parsed
 if not doc.span(('defence','shields')):return []
 owner=obj.get('san_shield_import_owner') or uuid.uuid4().hex;remove_generated(owner)
 obj['san_shield_import_owner']=owner;obj['san_shield_import_key']=source['key'];obj['san_shield_import_options']=json.dumps(list(options) if options else [])
 obj['san_shield_source_template']=source['template'];obj['san_shield_source_text']=source['text'];obj['san_shield_source_root']=str(source['root'] or '');obj['san_shield_engine']=str(source['engine'] or '')
 if model_path:obj['san_shield_model_path']=str(Path(model_path).resolve())
 swap=context.scene.sanctuary_toolkit.swap_yz_axis;obj['san_shield_swap_yz']=swap
 assets=None;shells=[];completed=[]
 try:
  assets=source_library(source)
  from .sanmodel_io import import_sanmodel,parse_sanmodel_bytes
  sphere=parse_sanmodel_bytes(assets.read(native.MODEL))
  for item in items:
   before={name:set(getattr(bpy.data,name)) for name in ('objects','meshes','materials','images')}
   try:
    body=assets.json(item['body']);glow=assets.json(item['glow']);native.validate_material(body,'surface');native.validate_material(glow,'glow')
    entity=_anchor(context,obj,rig,assets,doc,item,owner,swap);pair=[]
    for role,data in (('body',body),('glow',glow)):
     shell=import_sanmodel(context,sphere,None,swap,False);shell.name=obj.name+' Shield '+str(item['index'])+' '+role;shell.parent=entity;shell.hide_select=True;shell.visible_shadow=False;shell['shield_owner']=shell.data['shield_owner']=owner;shell['san_shield_source']=obj;shell['san_shield_index']=item['index'];shell['san_shield_role']=role;shell['san_preview_only']=True
     for polygon in shell.data.polygons:polygon.use_smooth=True
     material=graph.build(assets,data,'Unit shield '+role,owner=owner,preview_gain=PREVIEW_GAIN,preview_fps=context.scene.render.fps/context.scene.render.fps_base,unit_context=True);shell.data.materials.append(material)
     dilation=next((v['value'] for v in data.get('floats',[]) if v['key']=='_VertexPositionScale'),1) if role=='glow' else 1
     shell.scale=tuple(v*dilation for v in _xyz(item['radii'],swap));pair.append(shell)
    shells+=pair;completed.append(item)
    if item.get('pose_note'):diagnostics.append('Shield '+str(item['index'])+': '+item['pose_note'])
   except (ValueError,KeyError,OSError) as exc:
    for name in ('objects','meshes','materials','images'):
     collection=getattr(bpy.data,name)
     for data in set(collection)-before[name]:
      if name=='objects':collection.remove(data,do_unlink=True)
      elif data.users==0:collection.remove(data)
    diagnostics.append('Shield '+str(item['index'])+': '+str(exc)+'. Body: '+item['body']+'; glow: '+item['glow']+'. Source: '+source['template']+'. Verify referenced bone/material/DDS in this unit source; no preset substituted.')
  context.view_layer.update()
  if shells:
   scene=render_scene(context,obj,rig,shells,owner);obj['san_shield_preview_scene']=scene.name
   if context.screen:
    for area in context.screen.areas:
     if area.type=='VIEW_3D':area.spaces.active.shading.type='MATERIAL'
 except (ValueError,KeyError,OSError,zipfile.BadZipFile) as exc:diagnostics.append('Shield import: '+str(exc))
 finally:
  if assets:assets.close()
  obj['san_shield_import_definitions']=json.dumps(completed);obj['san_shield_import_diagnostics']='\n'.join(diagnostics)
  obj['san_shield_import_status']=str(len(completed))+' native shield definition(s) imported; render scene uses preview-only HDR brightness conversion.'
  bpy.ops.object.select_all(action='DESELECT');obj.select_set(True);context.view_layer.objects.active=obj
 return shells

def _reuse(context,source,key,options,location):
 if not source or not Blueprint(source['text']).span(('defence','shields')):return None
 for obj in context.scene.objects:
  if obj.type!='MESH' or obj.get('san_shield_import_key')!=key:continue
  if obj.get('san_shield_import_options','[]')!=json.dumps(list(options) if options else []):continue
  root=obj.find_armature() or obj
  if (root.matrix_world.translation-Vector(location)).length>1e-5:continue
  if bool(obj.get('san_shield_swap_yz'))!=context.scene.sanctuary_toolkit.swap_yz_axis:continue
  return obj,root if root.type=='ARMATURE' else None
 return None

def reuse_database_import(context,record,options):
 try:source=source_definition(context,record=record);reused=_reuse(context,source,source['key'] if source else '',options,context.scene.cursor.location)
 except (ValueError,OSError,UnicodeError):return None
 if reused:attach_imported_unit(context,*reused,record=record,options=options)
 return reused

def reuse_model_import(context,path):
 try:source=source_definition(context,model_path=path);reused=_reuse(context,source,source['key'] if source else '',None,(0,0,0))
 except (ValueError,OSError,UnicodeError):return None
 if reused:attach_imported_unit(context,*reused,model_path=path)
 return reused

def selected_unit(context):
 obj=context.active_object
 if not obj:return None
 if obj.get('san_shield_source'):return obj['san_shield_source']
 if obj.get('san_shield_import_owner'):return obj
 # Older imported anchors had ownership only; support saved2.2.5/2.2.6 scenes.
 owner=obj.get('shield_owner')
 return next((o for o in context.scene.objects if owner and o.get('san_shield_import_owner')==owner),None)

def select_surface(context,unit,index,role):
 surface=next((o for o in context.scene.objects if o.type=='MESH' and o.get('san_shield_source')==unit and o.get('san_shield_index')==index and o.get('san_shield_role')==role),None)
 if not surface:raise ValueError('Imported '+role+' surface is missing for shield '+str(index))
 for obj in context.scene.objects:obj.select_set(False)
 surface.hide_select=False;surface.hide_set(False);surface.select_set(True);context.view_layer.objects.active=surface;surface.active_material_index=0
 if context.screen:
  for area in context.screen.areas:
   if area.type=='VIEW_3D' and area.spaces.active.shading.type not in ('MATERIAL','RENDERED'):area.spaces.active.shading.type='MATERIAL'
   if area.type=='NODE_EDITOR' and area.ui_type=='ShaderNodeTree':area.spaces.active.pin=False;area.spaces.active.shader_type='OBJECT'
 return surface

class SANCTUARY_OT_imported_shield(Operator):
 bl_idname='sanctuary.imported_shield';bl_label='Imported Unit Shield';action:StringProperty();index:IntProperty(default=1);role:StringProperty(default='body')
 def execute(self,context):
  try:
   obj=selected_unit(context) or context.scene.get('san_shield_source')
   if not obj:raise ValueError('Select an imported shield generator')
   if self.action=='SELECT':select_surface(context,obj,self.index,getattr(self,'role','body'))
   elif self.action=='RENDER':
    scene=bpy.data.scenes.get(obj.get('san_shield_preview_scene',''))
    if not scene:raise ValueError('No resolved shield render scene; inspect import diagnostics')
    context.window.scene=scene
    for area in context.screen.areas:
     if area.type=='VIEW_3D':area.spaces.active.shading.type='MATERIAL';area.spaces.active.region_3d.view_perspective='CAMERA'
   elif self.action=='RETURN':
    source=bpy.data.scenes.get(context.scene.get('studio_source_scene',''))
    if not source:raise ValueError('Source scene is missing')
    context.window.scene=source
   elif self.action=='EDIT':
    item=next(i for i in json.loads(obj['san_shield_import_definitions']) if i['index']==self.index)
    from . import studio_shields
    assets=source_library({'engine':Path(obj['san_shield_engine']) if obj.get('san_shield_engine') else None,'root':Path(obj['san_shield_source_root']) if obj.get('san_shield_source_root') else None})
    source=bpy.data.scenes.get(context.scene.get('studio_source_scene','')) if context.scene.get('san_shield_source') else context.scene
    if not source:raise ValueError('Source import scene is missing')
    try:
     with bpy.context.temp_override(scene=source,view_layer=source.view_layers[0]):scene=studio_shields.open_scene(bpy.context,item['body'],pair=(item['body'],item['glow']),unit_preview=(obj,item),assets_override=assets)
    finally:assets.close()
    scene.sanctuary_shield.shield_index=self.index
    role=getattr(self,'role','body');scene.sanctuary_shield.edit_layer=role
    studio_shields.live.activate(scene,role)
   else:raise ValueError('Unknown imported shield action')
   return {'FINISHED'}
  except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}

class SANCTUARY_PT_imported_shields(Panel):
 bl_label='Imported Unit Shields';bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='Sanctuary'
 @classmethod
 def poll(cls,context):return bool(selected_unit(context) or context.scene.get('san_shield_source')) and not context.scene.get('studio_shield_draft')
 def draw(self,context):
  obj=selected_unit(context) or context.scene.get('san_shield_source');layout=self.layout
  from .studio_workshop import note
  note(layout,obj.get('san_shield_import_status',''))
  layout.prop(context.scene.sanctuary_shield,'edit_layer',expand=True)
  for item in json.loads(obj.get('san_shield_import_definitions','[]')):
   note(layout,'Shield '+str(item['index'])+': radii '+str(tuple(item['radii']))+', native bone '+item['bone']+', offset '+str(tuple(item['offset'])))
   row=layout.row(align=True)
   button=row.operator('sanctuary.imported_shield',text='Select Shield Surface '+str(item['index']));button.action='SELECT';button.index=item['index'];button.role=context.scene.sanctuary_shield.edit_layer
   button=row.operator('sanctuary.imported_shield',text='Edit Shield Material '+str(item['index']));button.action='EDIT';button.index=item['index'];button.role=context.scene.sanctuary_shield.edit_layer
  if obj.get('san_shield_preview_scene'):layout.operator('sanctuary.imported_shield',text='Open Generator & Shield Render Scene').action='RENDER'
  if context.scene.get('san_shield_source'):layout.operator('sanctuary.imported_shield',text='Return to Imported Unit').action='RETURN'
  if obj.get('san_shield_import_diagnostics'):note(layout,obj['san_shield_import_diagnostics'])
  note(layout,'Native dimensions/UV/DDS references; HDRP shader/depth/hit behavior remains a Blender proxy. Generated shells are preview-only. Edit a private shield to export materials; unit radii/offset stay in Lua.')
  note(layout,'Select Surface activates the reference mesh. Edit Shield Material opens/reuses your private exportable editor. Reference-node tweaks are preview-only and reset on unit reimport.')

CLASSES=(SANCTUARY_OT_imported_shield,SANCTUARY_PT_imported_shields)
def register():
 for cls in CLASSES:bpy.utils.register_class(cls)
def unregister():
 for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
