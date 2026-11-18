"""Inspectable unlit shield material proxy on native UVs; exports use native data."""
import hashlib,json,math
import bpy,numpy as np
from .shield_authoring import BODY_SHADER,EDGE_SHADER

LIMITS='Native UV sphere, DDS slots/tiling, HDR colors, scrolling, normal/facing proxy and two material layers. HDRP scene-depth intersection, refraction, water fade, damage/hit callbacks and exact shader arithmetic are not reproduced. Arbitrary node or mesh edits cannot be exported.'

def image_digest(image):
 values=np.empty(len(image.pixels),dtype=np.float32);image.pixels.foreach_get(values)
 return hashlib.sha256(values.tobytes()).hexdigest()

def source_image(name,rgba,colorspace,owner):
 """Reuse this draft's unchanged pixels without deleting other material users."""
 from .utils import cache_root
 from .material_io import _write_preview_png
 digest=hashlib.sha256(rgba.tobytes()).hexdigest()
 image_name='Shield '+owner[:32]+' '+digest[:16]+' '+colorspace[:3]
 previous=bpy.data.images.get(image_name)
 if previous and previous.get('shield_owner')==owner and previous.colorspace_settings.name==colorspace:return previous
 folder=cache_root()/'shield-drafts'/owner/'preview-textures';folder.mkdir(parents=True,exist_ok=True)
 path=folder/(digest+'.png')
 if not path.is_file():_write_preview_png(path,rgba)
 image=bpy.data.images.load(str(path),check_existing=False);image.name=image_name
 image.colorspace_settings.name=colorspace;image.alpha_mode='CHANNEL_PACKED';image['shield_owner']=owner
 return image

def signature(material):
 tree=material.node_tree;nodes=[]
 for node in tree.nodes:
  entry={'name':node.name,'type':node.bl_idname}
  entry['mute']=node.mute
  entry['binding']=node.get('shield_binding');entry['initial']=node.get('shield_initial_value');entry['preview_time']=node.get('shield_preview_time')
  for key in ('operation','blend_type','clamp','clamp_type','use_clamp','data_type','vector_type','space','uv_map','interpolation','interpolation_type','extension','projection','projection_blend','invert'):
   if hasattr(node,key):entry[key]=getattr(node,key)
  mutable=node.get('shield_binding') or node.get('shield_preview_time')
  entry['inputs']=[(s.name,list(s.default_value) if hasattr(s.default_value,'__len__') else s.default_value) for s in node.inputs if hasattr(s,'default_value')]
  if not mutable:entry['outputs']=[(s.name,list(s.default_value) if hasattr(s.default_value,'__len__') else s.default_value) for s in node.outputs if hasattr(s,'default_value')]
  if node.type=='TEX_IMAGE':entry['image']=(node.image.name,image_digest(node.image),node.image.colorspace_settings.name,node.image.alpha_mode,node.image.filepath,node.image.source) if node.image else None
  nodes.append(entry)
 drivers=[]
 if tree.animation_data:
  if tree.animation_data.action:raise ValueError('Custom material animation cannot be exported; use native panner parameters')
  for f in tree.animation_data.drivers:
   drivers.append((f.data_path,f.array_index,f.driver.type,f.driver.expression,f.mute,[(v.name,v.type,[(str(t.id),t.data_path,t.bone_target,t.transform_type,t.transform_space) for t in v.targets]) for v in f.driver.variables]))
 payload={'nodes':sorted(nodes,key=lambda e:e['name']),'links':sorted((l.from_node.name,l.from_socket.identifier,l.to_node.name,l.to_socket.identifier) for l in tree.links),
  'drivers':drivers,'render_method':material.surface_render_method,'backface_culling':material.use_backface_culling}
 return hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()

def extract(material,data):
 if signature(material)!=material.get('shield_graph_signature'):raise ValueError('Unsupported node/image edit: use native parameter controls or Replace Texture. Added nodes, links, drivers and painted preview images cannot be exported.')
 import copy
 result=copy.deepcopy(data)
 for node in material.node_tree.nodes:
  binding=node.get('shield_binding')
  if not binding:continue
  group,key,component=json.loads(binding)
  if group=='tiling':item=next(t for t in result['textures'] if t['key']==key)['tilingOffset']
  else:item=next(i for i in result[group] if i['key']==key)
  value=node.outputs[0].default_value
  current=list(value) if hasattr(value,'__len__') else float(value)
  if current==json.loads(node['shield_initial_value']):continue
  if group=='colors':item['value']=dict(zip('rgba',map(float,value)))
  elif group=='floats':item['value']=float(value)
  elif group=='vectors':item['value'][component]=float(value)
  else:item[component]=float(value)
 return result

def build(assets,data,name,overrides=None,owner='draft',preview_gain=1.0,preview_fps=30.0,unit_context=False):
 from .shield_textures import decode as decode_dds_top
 material=bpy.data.materials.new(name);material['shield_owner']=owner;material.use_nodes=True;tree=material.node_tree;tree.nodes.clear()
 material.surface_render_method='BLENDED';material.use_backface_culling=False
 material['shield_preview_boundary']=LIMITS;material['shield_native_shader']=data['shader']
 nodes=tree.nodes;links=tree.links;bound={};overrides=overrides or {}
 def new(kind,label):
  node=nodes.new(kind);node.name=label;node.label=label;return node
 def value(key,default=0):return bound.get(key) or fixed(default,key)
 def fixed(number,label='Constant'):
  node=new('ShaderNodeValue',label);node.outputs[0].default_value=number;return node.outputs[0]
 def mathnode(op,a,b=None,label=None):
  node=new('ShaderNodeMath',label or op);node.operation=op
  links.new(a,node.inputs[0])
  if b is not None:
   if isinstance(b,(int,float)):node.inputs[1].default_value=b
   else:links.new(b,node.inputs[1])
  return node.outputs[0]
 for group,components in (('floats',None),('colors','rgba'),('vectors','xyzw')):
  for item in data.get(group,[]):
   key=item['key'];v=item['value']
   if group=='colors':
    node=new('ShaderNodeRGB','Native '+key);node.outputs[0].default_value=tuple(v.get(c,0) for c in 'rgba')
    node['shield_binding']=json.dumps([group,key,None]);bound[key]=node.outputs[0]
   else:
    for c in components or (None,):
     node=new('ShaderNodeValue','Native '+key+('.'+c if c else ''));node.outputs[0].default_value=v.get(c,0) if c else v
     node['shield_binding']=json.dumps([group,key,c]);bound[key+('.'+c if c else '')]=node.outputs[0]
 clock=new('ShaderNodeValue','Preview seconds');clock['shield_preview_time']=True
 clock.outputs[0].driver_add('default_value').driver.expression='frame / '+repr(float(preview_fps))
 uv=new('ShaderNodeTexCoord','Native sphere UV');tex={}
 for slot in data.get('textures',[]):
  key=slot['key'];blob=Path(overrides[key]).read_bytes() if key in overrides else assets.read(slot['path'])
  rgba=decode_dds_top(blob);rgba=rgba[0] if isinstance(rgba,tuple) else rgba
  # Recognize the exported DXT5nm packing only when its constant channels prove it.
  if 'normal' in key.casefold() and np.all(rgba[:,:,0]>=250) and np.all(rgba[:,:,2]<=5):
   rgba=rgba.copy();x=rgba[:,:,3].astype('float32')/127.5-1;y=rgba[:,:,1].astype('float32')/127.5-1
   rgba[:,:,0]=rgba[:,:,3];rgba[:,:,2]=np.rint((np.sqrt(np.maximum(0,1-x*x-y*y))+1)*127.5).astype('uint8');rgba[:,:,3]=255
  image=source_image(name+' '+key,rgba,'Non-Color' if slot.get('linear') else 'sRGB',owner)
  texture=new('ShaderNodeTexImage','Native texture '+key);texture.image=image;texture.extension='REPEAT'
  coords=new('ShaderNodeVectorMath','UV tiling '+key);coords.operation='MULTIPLY'
  scale=new('ShaderNodeCombineXYZ','Tiling '+key);offset=new('ShaderNodeCombineXYZ','Offset '+key)
  for c,default in (('x',1),('y',1),('z',0),('w',0)):
   node=new('ShaderNodeValue','Native '+key+' tiling.'+c);node.outputs[0].default_value=slot.get('tilingOffset',{}).get(c,default)
   node['shield_binding']=json.dumps(['tiling',key,c])
   links.new(node.outputs[0],(scale if c in 'xy' else offset).inputs[0 if c in 'xz' else 1])
  links.new(uv.outputs['UV'],coords.inputs[0]);links.new(scale.outputs[0],coords.inputs[1])
  add=new('ShaderNodeVectorMath','UV offset '+key);add.operation='ADD';links.new(coords.outputs[0],add.inputs[0]);links.new(offset.outputs[0],add.inputs[1])
  if key in ('_Energy','_Energy_Normals','_Energy_Distortion','_Edge_Texture'):
   pan=new('ShaderNodeCombineXYZ','Panner '+key)
   for i,c in enumerate('xy'):links.new(mathnode('MULTIPLY',value('_Pan_Speed.'+c),clock.outputs[0]),pan.inputs[i])
   moving=new('ShaderNodeVectorMath','UV motion '+key);moving.operation='ADD';links.new(add.outputs[0],moving.inputs[0]);links.new(pan.outputs[0],moving.inputs[1]);coords_out=moving.outputs[0]
  else:coords_out=add.outputs[0]
  links.new(coords_out,texture.inputs['Vector']);tex[key]=texture
 def multiply_color(color,texture,label):
  node=new('ShaderNodeMixRGB',label);node.blend_type='MULTIPLY';node.inputs[0].default_value=1
  links.new(color,node.inputs[1]);links.new(texture,node.inputs[2]);return node.outputs[0]
 def remap(texture,key,default):
  node=new('ShaderNodeMapRange','Native remap '+key);node.clamp=False
  links.new(texture.outputs['Color'],node.inputs['Value'])
  for c,socket,number in zip('xyzw',('From Min','From Max','To Min','To Max'),default):links.new(value(key+'.'+c,number),node.inputs[socket])
  return node.outputs[0]
 facing=new('ShaderNodeLayerWeight','Facing proxy');facing.inputs['Blend'].default_value=.5
 geometry=new('ShaderNodeNewGeometry','Two-sided shield')
 if data['shader']==BODY_SHADER:
  emission=multiply_color(value('_Emissive_Color',1),tex['_Emissive'].outputs['Color'],'Source emissive pattern')
  energy=multiply_color(value('_Energy_Intensity',1),tex['_Energy'].outputs['Color'],'Source energy flow')
  add=new('ShaderNodeMixRGB','Emission plus energy proxy');add.blend_type='ADD';add.inputs[0].default_value=1
  links.new(emission,add.inputs[1]);links.new(energy,add.inputs[2]);color=add.outputs[0]
  alpha=remap(tex['_Alpha'],'_Alpha_Remap',(0,1,0,.15));lines=remap(tex['_Alpha_Lines'],'_Alpha_Lines_Remap',(0,1,0,1))
  if not unit_context:alpha=mathnode('ADD',alpha,lines,'Native masks proxy')
  else:material['shield_alpha_proxy']='Generator context uses native base alpha remap/facing/backface only. HDR line-remap composition is unavailable; treating its 50-range intensity as opacity hides the generator. Native line values remain exact on export.'
  alpha=mathnode('MAXIMUM',alpha,value('_ShieldPassOpacityMin',0))
  normal=new('ShaderNodeNormalMap','Source tangent normal');normal.space='TANGENT';normal.uv_map='UV1Map';links.new(tex['_Energy_Normals'].outputs['Color'],normal.inputs['Color']);links.new(value('_Energy_Normal_Intensity',1),normal.inputs['Strength']);links.new(normal.outputs[0],facing.inputs['Normal'])
  # Facing profile deliberately marked proxy: HDRP distance/depth math unavailable.
  edge=mathnode('POWER',facing.outputs['Fresnel'],value('_Fresnel_Alpha_Power',1))
  edge=mathnode('MULTIPLY',edge,value('_Fresnel_Alpha_Intensity',1));alpha=mathnode('MULTIPLY',alpha,mathnode('ADD',edge,1))
  back=new('ShaderNodeMixRGB','Native backface opacity');back.inputs[1].default_value=(1,1,1,1)
  links.new(geometry.outputs['Backfacing'],back.inputs[0]);links.new(value('_BackFaceOpacity',1),back.inputs[2]);alpha=mathnode('MULTIPLY',alpha,back.outputs[0])
 else:
  color=multiply_color(value('_Color',1),tex['_Edge_Texture'].outputs['Color'],'Source glow texture')
  alpha=mathnode('MULTIPLY',facing.outputs['Fresnel'],tex['_Edge_Texture'].outputs['Color'],'Glow rim proxy')
 clamp=new('ShaderNodeClamp','Alpha range');links.new(alpha,clamp.inputs['Value'])
 emission=new('ShaderNodeEmission','Native unlit emission proxy');links.new(color,emission.inputs['Color'])
 emission.inputs['Strength'].default_value=preview_gain
 material['shield_preview_gain']=preview_gain
 transparent=new('ShaderNodeBsdfTransparent','Transparent shell')
 additive=data['shader']==EDGE_SHADER and next((i['value'] for i in data.get('floats',[]) if i['key']=='_DstBlend'),1)==1
 if additive:
  links.new(mathnode('MULTIPLY',clamp.outputs[0],preview_gain,'Source additive glow opacity'),emission.inputs['Strength'])
  mix=new('ShaderNodeAddShader','Native additive glow proxy');links.new(transparent.outputs[0],mix.inputs[0]);links.new(emission.outputs[0],mix.inputs[1])
 else:
  mix=new('ShaderNodeMixShader','Shield surface');links.new(clamp.outputs[0],mix.inputs[0]);links.new(transparent.outputs[0],mix.inputs[1]);links.new(emission.outputs[0],mix.inputs[2])
 output=new('ShaderNodeOutputMaterial','Shield output');links.new(mix.outputs[0],output.inputs['Surface'])
 for i,node in enumerate(nodes):node.location=(-1200+(i%7)*300,400-(i//7)*180)
 for node in nodes:
  if node.get('shield_binding'):
   output=node.outputs[0].default_value
   node['shield_initial_value']=json.dumps(list(output) if hasattr(output,'__len__') else float(output))
 material['shield_graph_signature']=signature(material);return material

from pathlib import Path
