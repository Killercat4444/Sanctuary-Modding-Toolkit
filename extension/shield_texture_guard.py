"""Editor-only checks that staged game bytes still belong to the shown preview."""
import hashlib,json,re
from pathlib import Path
import bpy,numpy as np

def preview_rgba(blob,key):
 from .shield_textures import decode
 rgba=decode(blob);rgba=rgba[0] if isinstance(rgba,tuple) else rgba
 # Match the existing preview's proven DXT5nm channel conversion exactly.
 if 'normal' in key.casefold() and np.all(rgba[:,:,0]>=250) and np.all(rgba[:,:,2]<=5):
  rgba=rgba.copy();x=rgba[:,:,3].astype('float32')/127.5-1;y=rgba[:,:,1].astype('float32')/127.5-1
  rgba[:,:,0]=rgba[:,:,3];rgba[:,:,2]=np.rint((np.sqrt(np.maximum(0,1-x*x-y*y))+1)*127.5).astype('uint8');rgba[:,:,3]=255
 return rgba

def preview_hash(image,owner):
 path=Path(bpy.path.abspath(image.filepath))
 if image.get('shield_owner')!=owner or image.source!='FILE' or not re.fullmatch(r'[0-9a-f]{64}\.png',path.name):
  raise ValueError('Unsupported shield preview image identity; restore the authored material')
 return path.stem

def staged_hashes(state,ignore=None):
 overrides=json.loads(state.overrides);known=json.loads(state.override_hashes);pending={}
 for slot,path in overrides.items():
  if slot==ignore:continue
  try:blob=Path(path).read_bytes()
  except OSError as exc:raise ValueError('Staged texture is missing or unreadable: '+slot+'. Restore its DDS or replace this slot with your PNG; edits are retained.') from exc
  digest=hashlib.sha256(blob).hexdigest()
  if slot in known:
   if known[slot]!=digest:raise ValueError('Staged texture changed since its preview: '+slot+'. Restore its DDS or replace this slot with your PNG; edits are retained.')
  else:
   # Pre229 drafts have no byte receipt. Their content-addressed preview proves
   # visible native pixels before adopting a current byte receipt.
   role,key=slot.split(':',1);material=state.body_material if role=='surface' else state.glow_material
   node=material.node_tree.nodes.get('Native texture '+key) if material else None
   if not node or not node.image:raise ValueError('Missing native preview for staged texture: '+slot)
   actual=hashlib.sha256(preview_rgba(blob,key).tobytes()).hexdigest()
   if actual!=preview_hash(node.image,state.draft_token):raise ValueError('Staged texture differs from the saved preview: '+slot+'. Replace this slot with your PNG; edits are retained.')
   pending[slot]=digest
 return pending

def restore_empty_images(state):
 """Reload restored cache files; preserve nonempty pixels for strict paint checks."""
 seen=set()
 for prop in ('body_material','glow_material','original_body_material','original_glow_material'):
  material=getattr(state,prop)
  if not material:continue
  for node in material.node_tree.nodes:
   image=node.image if node.type=='TEX_IMAGE' else None
   if not image or image in seen:continue
   seen.add(image)
   if len(image.pixels):continue
   preview_hash(image,state.draft_token)
   path=Path(bpy.path.abspath(image.filepath))
   if not path.is_file():raise ValueError('Shield preview cache image is missing: '+str(path)+'. Restore that PNG and validate again; native edits and staged DDS are retained.')
   image.reload()
   if not len(image.pixels):raise ValueError('Shield preview cache image could not reload: '+str(path)+'. Native edits and staged DDS are retained.')

def commit_hashes(state,pending):
 if pending:
  known=json.loads(state.override_hashes);known.update(pending);state.override_hashes=json.dumps(known)
