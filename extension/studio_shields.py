"""Shield authoring scene: native controls, strict edits and private game references."""
import copy,hashlib,json,uuid,shutil
from pathlib import Path
import bpy,numpy as np
from bpy.props import StringProperty,PointerProperty,CollectionProperty,IntProperty,FloatProperty,FloatVectorProperty,BoolProperty,EnumProperty
from bpy.types import PropertyGroup,Operator,Panel,UIList
from . import studio as st,mod_projects as mp,shield_authoring as native,shield_nodes as graph,shield_live as live,shield_texture_guard as texture_guard

def mesh_signature(obj):
 mesh=obj.data;h=hashlib.sha256()
 for collection,prop,size,dtype in ((mesh.vertices,'co',3,'float32'),(mesh.loops,'vertex_index',1,'int32'),(mesh.polygons,'loop_total',1,'int32'),(mesh.polygons,'material_index',1,'int32')):
  values=np.empty(len(collection)*size,dtype=dtype);collection.foreach_get(prop,values);h.update(values.tobytes())
 for layer in mesh.uv_layers:
  values=np.empty(len(layer.data)*2,dtype='float32');layer.data.foreach_get('uv',values);h.update(layer.name.encode());h.update(values.tobytes())
 for layer in mesh.color_attributes:
  values=np.empty(len(layer.data)*4,dtype='float32');layer.data.foreach_get('color',values);h.update(layer.name.encode());h.update(layer.domain.encode());h.update(values.tobytes())
 normals=np.empty(len(mesh.corner_normals)*3,dtype='float32');mesh.corner_normals.foreach_get('vector',normals);h.update(normals.tobytes())
 h.update(bytes(int(p.use_smooth) for p in mesh.polygons))
 return h.hexdigest()

def transform_signature(obj):return json.dumps([list(obj.location),list(obj.rotation_euler),list(obj.scale)])

def exposure_changed(self,context):
 if context and context.scene.get('studio_shield_draft'):context.scene.view_settings.exposure=self.exposure

class ShieldParameter(PropertyGroup):
 role:StringProperty();group:StringProperty();key:StringProperty()
 initial_value:StringProperty()
 number:FloatProperty(name='Native value',min=-1e9,max=1e9,update=live.parameter_changed)
 color:FloatVectorProperty(name='Native HDR RGBA',size=4,subtype='COLOR',min=0,max=1e9,update=live.parameter_changed)
 vector:FloatVectorProperty(name='Native vector / tiling',size=4,min=-1e9,max=1e9,update=live.parameter_changed)
 hue:FloatVectorProperty(name='Color',size=3,subtype='COLOR',min=0,max=1,default=(1,1,1),update=live.hue_changed)
 hdr_strength:FloatProperty(name='Native HDR brightness',min=0,max=1e9,default=1,update=live.hue_changed)

class ShieldTexture(PropertyGroup):
 role:StringProperty();key:StringProperty();value:StringProperty();path:StringProperty();description:StringProperty()
 image:PointerProperty(type=bpy.types.Image)

class ShieldState(PropertyGroup):
 source_scene:PointerProperty(type=bpy.types.Scene)
 body_document:PointerProperty(type=bpy.types.Text);glow_document:PointerProperty(type=bpy.types.Text)
 body_material:PointerProperty(type=bpy.types.Material);glow_material:PointerProperty(type=bpy.types.Material)
 original_body_material:PointerProperty(type=bpy.types.Material);original_glow_material:PointerProperty(type=bpy.types.Material)
 body_object:PointerProperty(type=bpy.types.Object);glow_object:PointerProperty(type=bpy.types.Object)
 body_baseline:StringProperty();glow_baseline:StringProperty()
 source_body:StringProperty();source_glow:StringProperty();binding_project:StringProperty();engine_root:StringProperty()
 draft_token:StringProperty()
 source_asset_root:StringProperty();preview_body_matrix:StringProperty();preview_gain:FloatProperty(default=1);preview_fps:FloatProperty(default=30)
 source_unit:PointerProperty(type=bpy.types.Object);generator_reference:PointerProperty(type=bpy.types.Object);preview_linear_matrix:StringProperty()
 asset_id:StringProperty(name='Unique shield ID');parameters:CollectionProperty(type=ShieldParameter);parameter_index:IntProperty(update=live.parameter_selected)
 texture_slots:CollectionProperty(type=ShieldTexture);texture_index:IntProperty(update=live.texture_selected)
 edit_layer:EnumProperty(name='Material layer',items=[('body','Body','Surface pattern and energy flow'),('glow','Rim / glow','Separate outer rim material')],default='body',update=live.layer_changed)
 live_preview:BoolProperty(name='Live native preview',default=True,update=live.enabled_changed)
 compare_original:BoolProperty(name='Compare original source',default=False,update=live.comparison_changed)
 solo_layer:BoolProperty(name='Solo selected layer (preview only)',default=False,update=live.visibility_changed)
 show_advanced:BoolProperty(name='Advanced native properties',default=False)
 preview_error:StringProperty();staged_sources:StringProperty(default='{}')
 png: StringProperty(name='Your PNG',subtype='FILE_PATH')
 texture_size:IntProperty(name='DDS size',default=1024,min=4,max=4096)
 normal_dxt5nm:BoolProperty(name='Encode normal as DXT5nm')
 override_hashes:StringProperty(default='{}')
 overrides:StringProperty(default='{}');exported_manifest:StringProperty(default='{}');exported_fingerprint:StringProperty()
 shield_index:IntProperty(name='Source unit shield',default=1,min=1)
 exposure:FloatProperty(name='Preview exposure (EV only)',default=-14,min=-24,max=10,update=exposure_changed)
 status:StringProperty()

def read_draft(state,replace_texture=None):
 live.flush_scene(state.id_data,force=True)
 if state.preview_error:raise ValueError('Unapplied native control: '+state.preview_error)
 pending_hashes=texture_guard.staged_hashes(state,replace_texture)
 texture_guard.restore_empty_images(state)
 result=[]
 for role in ('body','glow'):
  data=json.loads(getattr(state,role+'_document').as_string());baseline=json.loads(getattr(state,role+'_baseline'))
  if set(data)!=set(baseline) or any(data.get(k)!=baseline.get(k) for k in ('shader','keywords','renderQueue','name')):
   raise ValueError('Unsupported native schema/render-state edit; use the source shield shader and supported property controls')
  for group in ('floats','colors','vectors','textures'):
   if [i['key'] for i in data.get(group,[])]!=[i['key'] for i in baseline.get(group,[])]:raise ValueError('Adding/removing native shader slots is unsupported: '+group)
   for item,original in zip(data.get(group,[]),baseline.get(group,[])):
    if set(item)!=set(original):raise ValueError('Native shader slot schema changed: '+item['key'])
    if group=='textures':
     if item['path']!=original['path'] or item.get('linear')!=original.get('linear'):raise ValueError('Use Stage Your Texture to change texture content while keeping preview and game data consistent')
     if set(item.get('tilingOffset',{}))!=set(original.get('tilingOffset',{})):raise ValueError('Texture transform schema changed')
    elif isinstance(item['value'],dict) and set(item['value'])!=set(original['value']):raise ValueError('Native vector/color component schema changed')
  obj=getattr(state,role+'_object');material=getattr(state,role+'_material')
  expected=getattr(state,'original_'+role+'_material') if state.compare_original else material
  if not obj or not material or list(obj.data.materials)!=[expected]:raise ValueError('Shield material assignment was changed; restore the authored shell')
  original=getattr(state,'original_'+role+'_material')
  if original and graph.extract(original,baseline)!=baseline:raise ValueError('The original comparison material was edited. Use the private native editor for exportable changes.')
  if mesh_signature(obj)!=obj.get('shield_mesh_signature') or transform_signature(obj)!=obj.get('shield_transform_signature') or obj.modifiers or obj.animation_data or obj.constraints or obj.parent or obj.data.shape_keys or obj.data.animation_data:
   raise ValueError('Edited sphere geometry, UVs, transforms, modifiers or animation cannot be exported as a shield material. Native shield radii/offset belong in unit Lua.')
  if getattr(state,role+'_baseline') is None:raise ValueError('Missing shield baseline')
  data=graph.extract(material,data);native.validate_material(data,'surface' if role=='body' else 'glow');result.append(data)
 texture_guard.commit_hashes(state,pending_hashes)
 return result

def write_document(text,data):text.clear();text.write(json.dumps(data,indent=2))

def parameter_rows(state,body,glow):
 selected=(state.parameters[state.parameter_index].role,state.parameters[state.parameter_index].group,state.parameters[state.parameter_index].key) if state.parameters and 0<=state.parameter_index<len(state.parameters) else None
 with live.suppress():
  _parameter_rows(state,body,glow)
  state.parameter_index=next((i for i,p in enumerate(state.parameters) if (p.role,p.group,p.key)==selected),next((i for i,p in enumerate(state.parameters) if p.role==state.edit_layer),0))
 refresh_texture_images(state)

def _parameter_rows(state,body,glow):
 state.parameters.clear();state.texture_slots.clear()
 for role,data in (('body',body),('glow',glow)):
  for group in ('colors','floats','vectors'):
   for entry in data.get(group,[]):
    item=state.parameters.add();item.name=role+' '+entry['key'];item.role=role;item.group=group;item.key=entry['key']
    if group=='floats':item.number=entry['value']
    elif group=='colors':item.color=tuple(entry['value'].get(c,0) for c in 'rgba');live.color_fields(item)
    else:item.vector=tuple(entry['value'].get(c,0) for c in 'xyzw')
    item.initial_value=json.dumps(float(item.number) if group=='floats' else list(item.color if group=='colors' else item.vector))
  for slot in data.get('textures',[]):
   item=state.texture_slots.add();item.role=role;item.key=slot['key'];item.path=slot['path'];item.name=texture_label(role,slot['key']);item.value=json.dumps([role,slot['key']])
   item.description='Tangent normal; use RGB PNG unless the source shader expects DXT5nm AG packing.' if 'normal' in slot['key'].casefold() else 'Native texture slot; its tiling/offset exports to the game.'
   parameter=state.parameters.add();parameter.name=role+' '+slot['key']+' tiling/offset';parameter.role=role;parameter.group='tiling';parameter.key=slot['key'];parameter.vector=tuple(slot.get('tilingOffset',{}).get(c,1 if c in 'xy' else 0) for c in 'xyzw')
   parameter.initial_value=json.dumps(list(parameter.vector))
 state.parameter_index=min(state.parameter_index,max(0,len(state.parameters)-1))

def texture_label(role,key):
 return {'_Emissive':'Body pattern / detail','_Energy':'Energy flow pattern','_Energy_Normals':'Scrolling normal map','_Alpha':'Body opacity mask','_Alpha_Lines':'Energy line mask','_Edge_Texture':'Rim / glow pattern'}.get(key,role+' '+key)

def refresh_texture_images(state):
 for slot in state.texture_slots:
  node=getattr(state,slot.role+'_material').node_tree.nodes.get('Native texture '+slot.key)
  slot.image=node.image if node else None

def apply_glow_scale(state):
 data=json.loads(state.glow_baseline if state.compare_original else state.glow_document.as_string())
 scale=next((e['value'] for e in data.get('floats',[]) if e['key']=='_VertexPositionScale'),1);obj=state.glow_object
 if not obj:return
 if state.preview_body_matrix:
  from mathutils import Matrix
  obj.matrix_world=Matrix(json.loads(state.preview_body_matrix))@Matrix.Diagonal((scale,scale,scale,1))
 else:obj.scale=(scale,)*3
 obj['shield_transform_signature']=transform_signature(obj)

def rebuild_materials(state,assets,body,glow):
 overrides=json.loads(state.overrides)
 created={}
 before_materials=set(bpy.data.materials);before_images=set(bpy.data.images)
 try:
  for role,data in (('body',body),('glow',glow)):
   texture_overrides={k.split(':',1)[1]:v for k,v in overrides.items() if k.startswith(('surface' if role=='body' else 'glow')+':')}
   created[role]=graph.build(assets,data,'Shield '+state.draft_token+' '+role,texture_overrides,state.draft_token,preview_gain=state.preview_gain,preview_fps=state.preview_fps,unit_context=bool(state.preview_body_matrix))
 except BaseException:
  for material in set(bpy.data.materials)-before_materials:
   if material.users==0:bpy.data.materials.remove(material)
  for image in set(bpy.data.images)-before_images:
   if image.users==0:bpy.data.images.remove(image)
  raise
 for role,data in (('body',body),('glow',glow)):
  previous=getattr(state,role+'_material')
  material=created[role]
  setattr(state,role+'_material',material);obj=getattr(state,role+'_object')
  obj.data.materials.clear();obj.data.materials.append(getattr(state,'original_'+role+'_material') if state.compare_original else material)
  scale=next((e['value'] for e in data.get('floats',[]) if e['key']=='_VertexPositionScale'),1) if role=='glow' else 1
  if state.preview_body_matrix:
   from mathutils import Matrix
   obj.matrix_world=Matrix(json.loads(state.preview_body_matrix))@Matrix.Diagonal((scale,scale,scale,1))
  else:obj.scale=(scale,)*3
  obj['shield_transform_signature']=transform_signature(obj)
  if previous and previous.users==0:bpy.data.materials.remove(previous)
 write_document(state.body_document,body);write_document(state.glow_document,glow)
 apply_glow_scale(state)
 refresh_texture_images(state)

def _open_scene(context,path,pair=None,unit_preview=None,assets_override=None):
 from .sanmodel_io import import_sanmodel,parse_sanmodel_bytes
 source=context.scene;assets=assets_override or st.library(context)
 if path.casefold() not in assets.records:assets.scan()
 body_path,glow_path=pair or native.source_pair(assets,path)
 body=assets.json(body_path);glow=assets.json(glow_path);native.validate_material(body,'surface');native.validate_material(glow,'glow')
 from .studio_vfx import close_browser
 close_browser()
 scene=bpy.data.scenes.new('Shield Authoring');scene['studio_shield_draft']=True;scene['studio_source_scene']=source.name
 state=scene.sanctuary_shield;state.source_scene=source;state.source_body=body_path;state.source_glow=glow_path
 state.binding_project=str(mp.active_project(context,required=False) or '');state.engine_root=str(st.engine(context));state.draft_token=uuid.uuid4().hex;state.asset_id='shield_'+state.draft_token[:12]
 if unit_preview:
  unit,item=unit_preview
  state.source_unit=unit;state.shield_index=item['index']
  shell=next(o for o in source.objects if o.get('san_shield_source')==unit and o.get('san_shield_index')==item['index'] and o.get('san_shield_role')=='body')
  placement=shell.matrix_world.copy()
  from mathutils import Matrix
  linear=placement.copy();linear.translation=(0,0,0);state.preview_linear_matrix=json.dumps([list(row) for row in linear])
  state.preview_body_matrix=json.dumps([list(row) for row in Matrix.Translation(placement.translation)]);state.preview_gain=2**-14;state.preview_fps=source.render.fps/source.render.fps_base;state.exposure=0
  state.source_asset_root=unit.get('san_shield_source_root','');state.engine_root=unit.get('san_shield_engine','') or state.engine_root
 for role,data in (('body',body),('glow',glow)):
  text=bpy.data.texts.new('Shield '+role+' native draft');text.use_fake_user=True;write_document(text,data)
  setattr(state,role+'_document',text);setattr(state,role+'_baseline',json.dumps(data,sort_keys=True))
 if context.window:context.window.scene=scene
 with bpy.context.temp_override(scene=scene,view_layer=scene.view_layers[0]):
  for role in ('body','glow'):
   obj=import_sanmodel(bpy.context,parse_sanmodel_bytes(assets.read(native.MODEL)),None,bool(unit_preview[0].get('san_shield_swap_yz',True)) if unit_preview else True,False)
   if isinstance(obj,tuple):obj=obj[0]
   obj.name='Shield '+role+' (native UV sphere)';obj.visible_shadow=False;setattr(state,role+'_object',obj)
   if unit_preview:
    linear=placement.copy();linear.translation=(0,0,0);obj.data.transform(linear)
   for polygon in obj.data.polygons:polygon.use_smooth=True
  rebuild_materials(state,assets,body,glow);parameter_rows(state,body,glow)
  for role,data in (('body',body),('glow',glow)):
   original=graph.build(assets,data,'Original '+role+' comparison (render-only)',owner=state.draft_token,preview_gain=state.preview_gain,preview_fps=state.preview_fps,unit_context=bool(state.preview_body_matrix))
   setattr(state,'original_'+role+'_material',original)
  bpy.context.view_layer.update()
  for obj in (state.body_object,state.glow_object):obj['shield_mesh_signature']=mesh_signature(obj)
  bpy.context.view_layer.objects.active=state.body_object
  if unit_preview:
   unit=unit_preview[0];depsgraph=source.view_layers[0].depsgraph
   mesh=bpy.data.meshes.new_from_object(unit.evaluated_get(depsgraph),preserve_all_data_layers=True,depsgraph=depsgraph)
   reference=bpy.data.objects.new('Generator reference (not exported)',mesh);scene.collection.objects.link(reference);reference.matrix_world=unit.matrix_world.copy();reference.hide_select=True
   state.generator_reference=reference
 scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=24
 scene.render.fps=30;scene.frame_start=1;scene.frame_end=180;scene.render.resolution_x=scene.render.resolution_y=512;scene.render.resolution_percentage=100
 if unit_preview:
  scene.render.fps=source.render.fps;scene.render.fps_base=source.render.fps_base;scene.frame_start=source.frame_start;scene.frame_end=source.frame_end;scene.frame_set(source.frame_current)
 scene.world=bpy.data.worlds.new('Shield preview background');scene.world.use_nodes=True
 scene.world.node_tree.nodes['Background'].inputs[0].default_value=(.005,.008,.015,1)
 scene.view_settings.exposure=state.exposure
 camera=bpy.data.objects.new('Shield camera',bpy.data.cameras.new('Shield camera'));scene.collection.objects.link(camera)
 from mathutils import Vector
 camera.location=(0,-3.5,1.5);camera.rotation_euler=(Vector((0,0,0))-camera.location).to_track_quat('-Z','Y').to_euler()
 camera.data.type='ORTHO';camera.data.ortho_scale=2.8;scene.camera=camera
 if unit_preview:
  from .shield_import import fit_camera
  scene.view_layers[0].update();fit_camera(scene,[o for o in scene.objects if o.type=='MESH'],camera,bool(unit_preview[0].get('san_shield_swap_yz',True)))
  light=bpy.data.objects.new('Generator reference sunlight',bpy.data.lights.new('Generator reference sunlight','SUN'));light.data.energy=3;light.rotation_euler=(.45,-.6,-.5);scene.collection.objects.link(light)
 state.status='Native sphere/material draft. Exposure is preview-only. Edit native controls, export a unique copy, then bind and save the source unit.'
 scene['shield_editor_version']=227
 return scene

def open_scene(context,path,pair=None,unit_preview=None,assets_override=None):
 source=context.scene
 if unit_preview:
  unit,item=unit_preview
  for existing in bpy.data.scenes:
   state=existing.sanctuary_shield
   if not existing.get('studio_shield_draft') or state.source_unit!=unit or state.shield_index!=item['index']:continue
   if (state.source_body,state.source_glow)!=(item['body'],item['glow']):raise ValueError('This unit has an existing edited draft with changed source references. Keep or discard it explicitly before creating another.')
   live.flush_scene(existing,force=True)
   try:refresh_context(existing,source,unit,item)
   except (ValueError,RuntimeError) as exc:state.status='Existing edits retained; generator context not refreshed: '+str(exc)
   if context.window:context.window.scene=existing
   live.activate(existing,state.edit_layer)
   return existing
 before={name:set(getattr(bpy.data,name)) for name in ('scenes','objects','meshes','materials','images','texts','worlds','cameras','lights')}
 try:
  scene=_open_scene(context,path,pair,unit_preview,assets_override);token=scene.sanctuary_shield.draft_token
  for name in before:
   for item in set(getattr(bpy.data,name))-before[name]:item['shield_owner']=token
  views=[]
  if context.screen:
   for area in context.screen.areas:
    if area.type=='VIEW_3D':
     space=area.spaces.active;views.append([space.shading.type,space.region_3d.view_perspective]);space.shading.type='MATERIAL';space.region_3d.view_perspective='CAMERA'
  scene['shield_source_views']=json.dumps(views)
  live.activate(scene,scene.sanctuary_shield.edit_layer)
  return scene
 except BaseException:
  if context.window:context.window.scene=source
  tokens=[s.sanctuary_shield.draft_token for s in set(bpy.data.scenes)-before['scenes'] if s.get('studio_shield_draft')]
  for name in before:
   collection=getattr(bpy.data,name)
   for item in set(collection)-before[name]:
    if name=='texts':item.use_fake_user=False
    if name in ('scenes','objects') or item.users==0:collection.remove(item)
  for token in tokens:discard_cache(token)
  raise

def refresh_context(scene,source,unit,item):
 """Refresh only owned context geometry after placement changes; retain material edits."""
 from mathutils import Matrix
 state=scene.sanctuary_shield
 shell=next((o for o in source.objects if o.get('san_shield_source')==unit and o.get('san_shield_index')==item['index'] and o.get('san_shield_role')=='body'),None)
 if not shell:raise ValueError('Current imported shield surface is missing')
 source.view_layers[0].update();placement=shell.matrix_world.copy();linear=placement.copy();linear.translation=(0,0,0)
 previous=Matrix(json.loads(state.preview_linear_matrix)) if state.preview_linear_matrix else None
 translation=Matrix.Translation(placement.translation)
 changed=previous is None or any(abs(linear[i][j]-previous[i][j])>1e-6 for i in range(4) for j in range(4)) or any(abs(translation[i][j]-json.loads(state.preview_body_matrix)[i][j])>1e-6 for i in range(4) for j in range(4))
 if changed:
  read_draft(state) # Refuse to overwrite unsupported user geometry/graph edits.
  if previous is None:raise ValueError('Older draft lacks placement metadata; its snapshot is retained')
  change=linear@previous.inverted()
  for role in ('body','glow'):
   obj=getattr(state,role+'_object');obj.data.transform(change);obj.matrix_world=translation
   obj['shield_mesh_signature']=mesh_signature(obj);obj['shield_transform_signature']=transform_signature(obj)
  state.preview_linear_matrix=json.dumps([list(row) for row in linear]);state.preview_body_matrix=json.dumps([list(row) for row in translation]);apply_glow_scale(state)
 if state.generator_reference:
  reference=state.generator_reference;old=reference.data
  reference.data=bpy.data.meshes.new_from_object(unit.evaluated_get(source.view_layers[0].depsgraph),preserve_all_data_layers=True,depsgraph=source.view_layers[0].depsgraph)
  reference.data['shield_owner']=state.draft_token;reference.matrix_world=unit.matrix_world.copy()
  if old.users==0:bpy.data.meshes.remove(old)
 if changed:
  from .shield_import import fit_camera
  scene.view_layers[0].update();fit_camera(scene,[o for o in scene.objects if o.type=='MESH'],scene.camera,bool(unit.get('san_shield_swap_yz',True)))
 state.status='Reopened the existing private draft; native edits and staged textures retained.'

def return_to_source(context,scene):
 live.flush_scene(scene,force=True)
 source=scene.sanctuary_shield.source_scene
 if not source:raise ValueError('Source scene no longer exists')
 if context.window:context.window.scene=source
 views=iter(json.loads(scene.get('shield_source_views','[]')))
 if context.screen:
  for area in context.screen.areas:
   if area.type=='VIEW_3D':
    saved=next(views,None)
    if saved:area.spaces.active.shading.type=saved[0];area.spaces.active.region_3d.view_perspective=saved[1]

def discard_scene(context,scene):
 """Discard only this generated draft; exported project assets remain independent."""
 token=scene.sanctuary_shield.draft_token
 if not scene.get('studio_shield_draft') or not token or len(token)!=32 or any(c not in '0123456789abcdef' for c in token):raise ValueError('Invalid generated shield draft identity')
 return_to_source(context,scene)
 live.cancel_scene(scene)
 bpy.data.scenes.remove(scene)
 for name in ('objects','meshes','materials','images','texts','worlds','cameras','lights'):
  collection=getattr(bpy.data,name)
  for item in list(collection):
   if item.get('shield_owner')!=token:continue
   if name=='texts':item.use_fake_user=False
   if item.users==0:collection.remove(item)
 discard_cache(token)

def discard_cache(token):
 from .utils import cache_root
 if len(token)!=32 or any(c not in '0123456789abcdef' for c in token):raise ValueError('Invalid shield cache identity')
 root=(cache_root()/'shield-drafts').resolve();target=(root/token).resolve()
 if target.parent!=root or target.name!=token:raise ValueError('Invalid shield cache destination')
 if target.exists():
  if target.is_symlink() or any(p.is_symlink() for p in target.rglob('*')):raise ValueError('Refusing redirected draft cache')
  shutil.rmtree(target)

def draft_fingerprint(body,glow,overrides):
 hashes={key:hashlib.sha256(Path(path).read_bytes()).hexdigest() for key,path in overrides.items()}
 return hashlib.sha256(json.dumps([body,glow,hashes],sort_keys=True).encode()).hexdigest()

class SANCTUARY_OT_shield_action(Operator):
 bl_idname='sanctuary.shield_action';bl_label='Shield Authoring';bl_options={'REGISTER','UNDO'};action:StringProperty()
 def execute(self,context):
  assets=None
  try:
   if self.action=='OPEN':open_scene(context,st.settings(context).effect_asset);return {'FINISHED'}
   state=context.scene.sanctuary_shield
   if self.action=='RETURN':return_to_source(context,context.scene);return {'FINISHED'}
   if self.action=='CANCEL':discard_scene(context,context.scene);return {'FINISHED'}
   from .studio_core import AssetLibrary
   assets=AssetLibrary(state.engine_root,state.binding_project or None).scan()
   if state.source_asset_root:
    from .shield_import import source_library
    original_assets=source_library({'engine':state.engine_root,'root':Path(state.source_asset_root)})
    assets.records.update(original_assets.records);original_assets.close()
   if self.action=='SELECT_LAYER':live.activate(context.scene,state.edit_layer);return {'FINISHED'}
   if self.action=='RESET_ALL':
    live.cancel_scene(context.scene);body=json.loads(state.body_baseline);glow=json.loads(state.glow_baseline)
    state.overrides='{}';state.override_hashes='{}';state.staged_sources='{}';state.preview_error=''
    rebuild_materials(state,assets,body,glow);parameter_rows(state,body,glow);state.status='Private materials reset to source. Exported files and generator placement were preserved.';return {'FINISHED'}
   if self.action=='RESET_PARAMETER':
    body,glow=read_draft(state)
    item=state.parameters[state.parameter_index];baseline=json.loads(getattr(state,item.role+'_baseline'))
    original=next(e for e in baseline['textures' if item.group=='tiling' else item.group] if e['key']==item.key)
    if item.group=='floats':item.number=original['value']
    elif item.group=='colors':item.color=tuple(original['value'].get(c,0) for c in 'rgba')
    else:item.vector=tuple((original['tilingOffset'] if item.group=='tiling' else original['value']).get(c,0) for c in 'xyzw')
    live.flush_scene(context.scene,force=True)
    # Restore exact source JSON rather than exporting the UI's float32 rounding.
    data=body if item.role=='body' else glow
    target=next(e for e in data['textures' if item.group=='tiling' else item.group] if e['key']==item.key)
    target['tilingOffset' if item.group=='tiling' else 'value']=copy.deepcopy(original['tilingOffset' if item.group=='tiling' else 'value'])
    material=getattr(state,item.role+'_material')
    for node in material.node_tree.nodes:
     binding=node.get('shield_binding')
     if binding and json.loads(binding)[:2]==[item.group,item.key]:
      output=node.outputs[0].default_value;node['shield_initial_value']=json.dumps(list(output) if hasattr(output,'__len__') else float(output))
    material['shield_graph_signature']=graph.signature(material)
    write_document(getattr(state,item.role+'_document'),data);apply_glow_scale(state);return {'FINISHED'}
   if self.action=='MATCH_COLORS':
    pattern=next((p for p in state.parameters if p.role=='body' and p.group=='colors' and p.key=='_Emissive_Color'),None)
    if pattern is None:raise ValueError('This source has no native pattern color')
    for item in state.parameters:
     if item.group=='colors' and (item.role,item.key) in (('body','_Energy_Intensity'),('glow','_Color')):item.hue=pattern.hue
    live.flush_scene(context.scene,force=True);state.status='Energy and rim use the pattern hue; each native HDR brightness was retained.';return {'FINISHED'}
   replacement=None
   if self.action=='TEXTURE':
    role,key=json.loads(state.texture_slots[state.texture_index].value);replacement=('surface' if role=='body' else 'glow')+':'+key
   body,glow=read_draft(state,replace_texture=replacement)
   if self.action=='PARAMETER':
    item=state.parameters[state.parameter_index];data=body if item.role=='body' else glow
    current=float(item.number) if item.group=='floats' else list(item.color if item.group=='colors' else item.vector)
    if current==json.loads(item.initial_value):pass
    elif item.group=='tiling':next(t for t in data['textures'] if t['key']==item.key)['tilingOffset']=dict(zip('xyzw',map(float,item.vector)))
    else:
     value=float(item.number) if item.group=='floats' else dict(zip('rgba' if item.group=='colors' else 'xyzw',map(float,item.color if item.group=='colors' else item.vector)))
     next(e for e in data[item.group] if e['key']==item.key)['value']=value
    rebuild_materials(state,assets,body,glow);parameter_rows(state,body,glow);state.status='Native property applied; game-only depth/hit fields may have no Blender preview.'
   elif self.action=='TEXTURE':
    role,key=json.loads(state.texture_slots[state.texture_index].value);data=body if role=='body' else glow
    source=Path(bpy.path.abspath(state.png)).resolve()
    if source.suffix.casefold()!='.png' or not source.is_file():raise ValueError('Choose your own PNG')
    from .dds_tools import _load_png_rgba,write_bc3_dds
    from .utils import cache_root
    size=((state.texture_size+3)//4)*4;rgba,_=_load_png_rgba(str(source),size)
    slot=next(s for s in data['textures'] if s['key']==key);normal='normal' in key.casefold()
    if normal and state.normal_dxt5nm:
     rgba=rgba.copy();rgba[:,:,3]=rgba[:,:,0];rgba[:,:,0]=255;rgba[:,:,2]=0
    root=cache_root()/'shield-drafts'/state.draft_token;root.mkdir(parents=True,exist_ok=True)
    target=root/(role+'_'+key.strip('_')+'_'+uuid.uuid4().hex+'.dds')
    write_bc3_dds(target,rgba,kind='normal' if normal and not state.normal_dxt5nm else 'mask' if normal or slot.get('linear') else 'albedo',full_mips=False)
    previous_overrides=state.overrides;previous_hashes=state.override_hashes;overrides=json.loads(previous_overrides);overrides[replacement]=str(target);state.overrides=json.dumps(overrides)
    hashes=json.loads(previous_hashes);hashes[replacement]=hashlib.sha256(target.read_bytes()).hexdigest();state.override_hashes=json.dumps(hashes)
    try:rebuild_materials(state,assets,body,glow)
    except BaseException:
     state.overrides=previous_overrides;state.override_hashes=previous_hashes;target.unlink(missing_ok=True);raise
    staged=json.loads(state.staged_sources);staged[role+':'+key]=str(source);state.staged_sources=json.dumps(staged)
    refresh_texture_images(state)
    state.status='Your texture staged as native DDS; no project or stock asset replaced.'
   elif self.action in ('VALIDATE','EXPORT','BIND'):
    if self.action=='VALIDATE':state.status='Native data, graph and sphere checks passed. HDRP/hit parity still requires a game test.'
    else:
     if not state.source_scene or not state.binding_project:raise ValueError('Choose a writable project in the source scene, then reopen shield authoring')
     with bpy.context.temp_override(scene=state.source_scene,view_layer=state.source_scene.view_layers[0]):
      if str(mp.active_project(bpy.context) or '')!=state.binding_project:raise ValueError('Source project changed; reopen this shield draft in the intended project')
      if self.action=='EXPORT':
       manifest,count=native.export_assets(assets,state.binding_project,state.engine_root,state.asset_id,body,glow,json.loads(state.overrides));state.exported_manifest=json.dumps(manifest)
       state.exported_fingerprint=draft_fingerprint(body,glow,json.loads(state.overrides))
       st.library(bpy.context,refresh=True)
       state.status=f'Exported {count} private assets as {manifest["id"]}. Bind to source unit, Save Unit, then Check Entire Project and Export MM ZIP.'
      else:
       manifest=json.loads(state.exported_manifest)
       if manifest.get('id')!=state.asset_id:raise ValueError('Export this unique shield ID before binding it')
       if draft_fingerprint(body,glow,json.loads(state.overrides))!=state.exported_fingerprint:raise ValueError('Shield draft changed after export; export a new unique ID before binding it')
       for key in ('surface','glow'):
        if not (Path(state.binding_project)/manifest[key]).is_file():raise ValueError('Exported shield asset is missing')
       st.set_source(bpy.context,native.bind_source(st.source(bpy.context),state.shield_index,manifest))
       state.status='Both private material references assigned to source unit draft. Return, Save Unit, Check Entire Project and export MM together.'
   else:raise ValueError('Unknown shield action')
   return {'FINISHED'}
  except Exception as exc:
   self.report({'ERROR'},str(exc));return {'CANCELLED'}
  finally:
   if assets:assets.close()

class SANCTUARY_UL_shield_parameters(UIList):
 def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):layout.label(text=item.name)
 def filter_items(self,context,data,property):return [self.bitflag_filter_item if p.role==data.edit_layer else 0 for p in getattr(data,property)],[]

class SANCTUARY_UL_shield_textures(UIList):
 def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
  if item.image:item.image.preview_ensure();layout.template_icon(icon_value=item.image.preview.icon_id,scale=2)
  layout.label(text=item.name)
 def filter_items(self,context,data,property):return [self.bitflag_filter_item if p.role==data.edit_layer else 0 for p in getattr(data,property)],[]

class SANCTUARY_OT_shield_texture_file(Operator):
 bl_idname='sanctuary.shield_texture_file';bl_label='Load Your Pattern / Normal PNG';bl_options={'REGISTER','UNDO'}
 filepath:StringProperty(subtype='FILE_PATH');filter_glob:StringProperty(default='*.png',options={'HIDDEN'})
 draft_token:StringProperty(options={'HIDDEN'});role:StringProperty(options={'HIDDEN'});key:StringProperty(options={'HIDDEN'})
 def invoke(self,context,event):
  state=context.scene.sanctuary_shield;self.draft_token=state.draft_token
  if not state.texture_slots:self.report({'ERROR'},'This material has no native texture slots');return {'CANCELLED'}
  slot=state.texture_slots[state.texture_index];self.role=slot.role;self.key=slot.key
  context.window_manager.fileselect_add(self);return {'RUNNING_MODAL'}
 def execute(self,context):
  state=context.scene.sanctuary_shield
  if state.draft_token!=self.draft_token:self.report({'ERROR'},'The shield draft changed while choosing a file. Reopen its texture picker.');return {'CANCELLED'}
  index=next((i for i,s in enumerate(state.texture_slots) if (s.role,s.key)==(self.role,self.key)),None)
  if index is None:self.report({'ERROR'},'Native texture slot is missing');return {'CANCELLED'}
  state.texture_index=index;state.png=self.filepath
  return bpy.ops.sanctuary.shield_action(action='TEXTURE')

class SANCTUARY_PT_shield_authoring(Panel):
 bl_label='Shield Authoring';bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='Sanctuary'
 @classmethod
 def poll(cls,context):return bool(context.scene.get('studio_shield_draft'))
 def draw(self,context):
  layout=self.layout;state=context.scene.sanctuary_shield
  from .studio_workshop import note
  note(layout,'Your private native shield materials. Controls export to the game; Blender still approximates the shader.')
  layout.prop(state,'edit_layer',expand=True);layout.operator('sanctuary.shield_action',text='Select Editable Surface').action='SELECT_LAYER'
  preview=layout.box();preview.label(text='Preview only');preview.prop(state,'exposure');preview.prop(state,'solo_layer')
  comparison=preview.row();comparison.enabled=bool(state.original_body_material and state.original_glow_material);comparison.prop(state,'compare_original')
  if not comparison.enabled:note(preview,'Older draft: private edits retained; reopen from the source for an original comparison snapshot.')
  if state.compare_original:note(layout,'Viewing source comparison. Your material edits are retained; export uses your edited private materials.')
  controls=layout.column();controls.enabled=not state.compare_original;controls.prop(state,'live_preview')
  def parameter(key,group=None):return next((p for p in state.parameters if p.role==state.edit_layer and p.key==key and (group is None or p.group==group)),None)
  colors=controls.box();colors.label(text='Native color and brightness')
  for key,label in (('_Emissive_Color','Body pattern color'),('_Energy_Intensity','Energy flow color'),('_Color','Rim / glow color')):
   item=parameter(key,'colors')
   if item:colors.label(text=label);colors.prop(item,'hue',text='Color');colors.prop(item,'hdr_strength',text='HDR brightness')
  if state.edit_layer=='body':
   colors.operator('sanctuary.shield_action',text='Match Energy and Rim to Pattern Hue').action='MATCH_COLORS'
   note(colors,'Pattern, energy and rim are separate color branches. HDR brightness and color management affect the displayed hue; color is not opacity.')
  textures=controls.box();textures.label(text='Native pattern and normal textures')
  textures.template_list('SANCTUARY_UL_shield_textures','texture',state,'texture_slots',state,'texture_index',rows=4)
  if state.texture_slots:
   slot=state.texture_slots[state.texture_index]
   if slot.role==state.edit_layer:
    if slot.image:slot.image.preview_ensure();textures.template_icon(icon_value=slot.image.preview.icon_id,scale=5)
    note(textures,slot.path);note(textures,slot.description)
    staged=json.loads(state.staged_sources).get(slot.role+':'+slot.key)
    if staged:note(textures,'Your PNG: '+Path(staged).name)
    item=parameter(slot.key,'tiling')
    if item:
     row=textures.row(align=True);row.prop(item,'vector',index=0,text='Tiling U');row.prop(item,'vector',index=1,text='Tiling V')
     row=textures.row(align=True);row.prop(item,'vector',index=2,text='Offset U');row.prop(item,'vector',index=3,text='Offset V')
    textures.operator('sanctuary.shield_texture_file',text='Browse PNG and Replace This Texture',icon='FILE_FOLDER')
    textures.prop(state,'png');textures.prop(state,'texture_size')
    if 'normal' in slot.key.casefold():textures.prop(state,'normal_dxt5nm',text='Pack normal as DXT5nm (AG)');note(textures,'RGB normals are the default. Enable AG packing only when required by the native source shader.')
    textures.operator('sanctuary.shield_action',text='Load PNG into This Texture').action='TEXTURE'
  opacity=parameter('_Alpha_Remap','vectors');minimum=parameter('_ShieldPassOpacityMin','floats')
  if opacity or minimum:
   box=controls.box();box.label(text='Native body opacity')
   if opacity:box.prop(opacity,'vector',index=3,text='Mask maximum opacity')
   if minimum:box.prop(minimum,'number',text='Minimum opacity')
   note(box,'The source opacity mask, facing and backface settings also affect transparency.')
  motion=parameter('_Pan_Speed','vectors');normal=parameter('_Energy_Normal_Intensity','floats')
  if motion or normal:
   box=controls.box();box.label(text='Native scrolling and normals')
   if motion:box.prop(motion,'vector',index=0,text='Energy scroll U / second');box.prop(motion,'vector',index=1,text='Energy scroll V / second');note(box,'Play the timeline to see native scrolling. The static body pattern has its own UVs.')
   if normal:box.prop(normal,'number',text='Normal strength (native)')
  controls.prop(state,'show_advanced')
  if state.show_advanced:
   controls.template_list('SANCTUARY_UL_shield_parameters','',state,'parameters',state,'parameter_index',rows=7)
   if state.parameters:
    item=state.parameters[state.parameter_index];controls.prop(item,'number' if item.group=='floats' else 'color' if item.group=='colors' else 'vector')
    note(controls,'Live Blender effect; native value exports.' if live.preview_supported(getattr(state,item.role+'_material'),item.group,item.key) else 'Game-only native field: exports, but has no live Blender effect.')
    controls.operator('sanctuary.shield_action',text='Reset Selected Native Property').action='RESET_PARAMETER'
  controls.operator('sanctuary.shield_action',text='Apply / Rebuild Native Preview').action='PARAMETER'
  controls.operator('sanctuary.shield_action',text='Reset Private Materials to Source').action='RESET_ALL'
  note(layout,'Depth/refraction/water/damage remain game-only. Added nodes, arbitrary links, painted preview images and geometry changes cannot export; use native controls and PNG replacement.')
  layout.prop(state,'asset_id')
  layout.operator('sanctuary.shield_action',text='Validate Shield Draft').action='VALIDATE'
  layout.operator('sanctuary.shield_action',text='Export New Unique Shield Assets').action='EXPORT'
  layout.prop(state,'shield_index');layout.operator('sanctuary.shield_action',text='Bind Both Materials to Source Unit').action='BIND'
  layout.operator('sanctuary.shield_action',text='Return to Source Unit').action='RETURN'
  layout.operator('sanctuary.shield_action',text='Discard This Draft').action='CANCEL'
  note(layout,graph.LIMITS)
  if state.status:note(layout,state.status)

CLASSES=(ShieldParameter,ShieldTexture,ShieldState,SANCTUARY_OT_shield_action,SANCTUARY_UL_shield_parameters,SANCTUARY_UL_shield_textures,SANCTUARY_OT_shield_texture_file,SANCTUARY_PT_shield_authoring)
@bpy.app.handlers.persistent
def initialize_loaded_drafts(*args):
 """Upgrade saved controls only; retain existing graphs, DDS staging and geometry."""
 live.before_undo()
 for scene in bpy.data.scenes:
  if not scene.get('studio_shield_draft'):continue
  state=scene.sanctuary_shield
  try:
   # Blender remaps file paths when saving .blend. Restore only this draft's
   # relative image paths to the same absolute location before strict validation.
   for prop in ('body_material','glow_material','original_body_material','original_glow_material'):
    material=getattr(state,prop)
    if not material:continue
    for node in material.node_tree.nodes:
     image=node.image if node.type=='TEX_IMAGE' else None
     if image and image.get('shield_owner')==state.draft_token and image.filepath.startswith('//'):image.filepath=bpy.path.abspath(image.filepath)
   if scene.get('shield_editor_version',0)>=227:continue
   retained={(p.role,p.group,p.key):live.value(p) for p in state.parameters if p.initial_value and live.value(p)!=json.loads(p.initial_value)}
   body,glow=read_draft(state)
   write_document(state.body_document,body);write_document(state.glow_document,glow)
   parameter_rows(state,body,glow)
   with live.suppress():
    for item in state.parameters:
     saved=retained.get((item.role,item.group,item.key))
     if saved is None:continue
     setattr(item,'number' if item.group=='floats' else 'color' if item.group=='colors' else 'vector',saved)
     if item.group=='colors':live.color_fields(item)
     live.queue(scene,item)
   scene['shield_editor_version']=227
   state.status='Older shield draft controls upgraded; material edits and staged textures retained. Source comparison is available in newly opened drafts.'
  except Exception as exc:state.status='Older draft retained; controls could not upgrade: '+str(exc)
 # Current drafts can be saved before debounce, or with Live Preview disabled.
 # Reconstruct their saved pending native controls after RNA/file replacement.
 live.after_undo()
def register():
 for cls in CLASSES:bpy.utils.register_class(cls)
 bpy.types.Scene.sanctuary_shield=PointerProperty(type=ShieldState)
 live.register()
 if initialize_loaded_drafts not in bpy.app.handlers.load_post:bpy.app.handlers.load_post.append(initialize_loaded_drafts)
 initialize_loaded_drafts()
def unregister():
 if initialize_loaded_drafts in bpy.app.handlers.load_post:bpy.app.handlers.load_post.remove(initialize_loaded_drafts)
 live.unregister()
 del bpy.types.Scene.sanctuary_shield
 for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
