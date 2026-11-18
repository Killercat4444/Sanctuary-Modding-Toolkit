"""Native shield drafts and collision-safe private assets, independent of Blender UI."""
import copy,hashlib,json,math,re
from pathlib import Path
from .vfx_documents import encode_asset,decode_asset
from .studio_core import safe_member,Blueprint,lua_compile
from . import shield_support

BODY_SHADER='RTS/Shield/Shied Forcefield CSG V3'
EDGE_SHADER='RTS/Shield/Shield Edge Detail'
MODEL='VFX/Shields/Models/Sphere_hipol.sanmodel'

def validate_material(data,role):
 expected=BODY_SHADER if role=='surface' else EDGE_SHADER
 if data.get('shader')!=expected:raise ValueError('Unsupported shield shader: '+str(data.get('shader')))
 for group,components in (('floats',None),('colors','rgba'),('vectors','xyzw')):
  seen=set()
  for item in data.get(group,[]):
   key=item['key']
   if key in seen:raise ValueError('Duplicate shader property: '+key)
   seen.add(key);values=[item['value']] if components is None else [item['value'].get(c,0) for c in components]
   if not all(isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v) for v in values):raise ValueError('Shader property must be finite: '+key)
 if type(data.get('renderQueue')) is not int:raise ValueError('Shield renderQueue must remain a native integer')
 texture_keys=set()
 for texture in data.get('textures',[]):
  if texture['key'] in texture_keys:raise ValueError('Duplicate texture slot: '+texture['key'])
  texture_keys.add(texture['key'])
  safe_member(texture['path'])
  if not texture['path'].lower().endswith('.dds'):raise ValueError('Shield texture must be a DDS: '+texture['path'])
  for value in texture.get('tilingOffset',{}).values():
   if not isinstance(value,(int,float)) or isinstance(value,bool) or not math.isfinite(value):raise ValueError('Invalid texture tiling/offset')
 # Existing native fields, keywords and render state are preserved verbatim.
 return data

def source_pair(assets,path):
 data=assets.json(path)
 # Prefer the private manifest for either shell, including IDs containing faction names.
 location,member=assets.records[path.lower()]
 if not member:
  manifest_path=Path(location).parent/'studio_shield.json'
  if manifest_path.is_file():
   manifest=json.loads(manifest_path.read_text());pair=(safe_member(manifest['surface']),safe_member(manifest['glow']))
   if path.casefold() not in [p.casefold() for p in pair]:raise ValueError('Shield manifest does not identify this material')
   for reference in pair:
    if reference.casefold() not in assets.records:raise ValueError('Private shield pair is incomplete')
   return pair
 if data.get('shader')==EDGE_SHADER:
  suffix=Path(path).stem.rsplit(' ',1)[-1]
  body='VFX/Shields/Shield Forcefield '+suffix+'.sanmaterial'
  if body.lower() not in assets.records:raise ValueError('Select the paired body material for this custom shield')
  return body,path
 if data.get('shader')!=BODY_SHADER:raise ValueError('Select a native shield Forcefield material')
 for suffix in ('EDA','Chosen','Guard'):
  if suffix.casefold() in Path(path).stem.casefold():return path,'VFX/Shields/Shield Edge Detail '+suffix+'.sanmaterial'
 raise ValueError('The body has no unambiguous paired glow material; select a stock faction source')

def export_assets(assets,project,engine,uid,body,glow,overrides=None):
 project=Path(project).resolve();engine=Path(engine).resolve()
 if project==engine or engine in project.parents:raise ValueError('Shield assets require a project outside the game installation')
 if not re.fullmatch(r'[a-z][a-z0-9_]{2,63}',uid):raise ValueError('Use a unique lowercase ID: 3–64 letters, digits or underscores, starting with a letter')
 prefix='VFX/Studio/Shields/'+uid
 target=(project/prefix).resolve()
 if project not in target.parents:raise ValueError('Shield destination escapes project')
 if target.exists() or any(p.startswith(prefix.casefold()+'/') for p in assets.records):raise FileExistsError('Shield ID already exists; choose a new ID')
 validate_material(body,'surface');validate_material(glow,'glow')
 support=project/'Lua'/shield_support.RELATIVE
 if support.exists() and not shield_support.is_generated_support(shield_support.RELATIVE,support):raise ValueError('Existing shield loader has custom edits; it will not be overwritten')
 lua_compile(shield_support.SUPPORT_LUA,engine)
 blobs={};materials={};overrides=overrides or {};textures={}
 known_overrides={role+':'+s['key'] for role,data in (('surface',body),('glow',glow)) for s in data.get('textures',[])}
 if set(overrides)-known_overrides:raise ValueError('Unknown shield texture override slot')
 for role,original in (('surface',body),('glow',glow)):
  material=copy.deepcopy(original);material['name']=uid+'_'+role
  for i,slot in enumerate(material.get('textures',[])):
   replacement=overrides.get(role+':'+slot['key'])
   source=slot['path'];blob=Path(replacement).read_bytes() if replacement else assets.read(source)
   # Keep encoded texture bytes exact; validate before accepting replacements.
   from .shield_textures import decode as decode_dds_top
   decoded=decode_dds_top(blob)
   token=hashlib.sha256(blob).hexdigest()[:12]
   filename=re.sub(r'[^a-z0-9_]+','_',slot['key'].casefold()).strip('_')+'_'+token+'.dds'
   path=prefix+'/Textures/'+filename
   blobs[path]=blob;slot['path']=path;textures[role+':'+slot['key']]=path
  material_path=prefix+'/'+role+'.sanmaterial';blob=encode_asset(material,material_path)
  assert decode_asset(blob,material_path)==material
  blobs[material_path]=blob;materials[role]=material_path
 manifest={'schema':1,'id':uid,'surface':materials['surface'],'glow':materials['glow'],'model':MODEL,
  'shader_surface':BODY_SHADER,'shader_glow':EDGE_SHADER,'textures':textures,
  'export_boundary':'Native materials/textures and opt-in shield references. Blender node graphs and edited sphere geometry are not game shader assets.'}
 blobs[prefix+'/studio_shield.json']=(json.dumps(manifest,indent=2)+'\n').encode()
 # Exclusive directory and files: neither existing art nor another mod is overwritten.
 target.mkdir(parents=True,exist_ok=False);written=[]
 try:
  for relative,blob in blobs.items():
   file=(project/relative).resolve();assert target in file.parents
   file.parent.mkdir(parents=True,exist_ok=True)
   with file.open('xb') as handle:handle.write(blob)
   written.append((file,hashlib.sha256(blob).hexdigest()))
  if not support.exists():
   support.parent.mkdir(parents=True,exist_ok=True)
   with support.open('xb') as handle:handle.write(shield_support.SUPPORT_LUA.encode('utf-8'))
 except BaseException:
  # Roll back only bytes created by this attempt; preserve unexpected edits.
  for file,digest in reversed(written):
   if file.is_file() and hashlib.sha256(file.read_bytes()).hexdigest()==digest:file.unlink()
  for folder in sorted((p for p in target.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
   try:folder.rmdir()
   except OSError:pass
  try:target.rmdir()
  except OSError:pass
  raise
 assets.scan();return manifest,len(blobs)

def bind_source(text,index,manifest):
 doc=Blueprint(text);path=('defence','shields',index)
 if not doc.span(path):raise ValueError('The source unit has no shield at this index')
 text=doc.set(path+('materialPath',),json.dumps(manifest['surface']))
 return Blueprint(text).set(path+('glowMaterialPath',),json.dumps(manifest['glow']))
