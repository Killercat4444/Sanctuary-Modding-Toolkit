"""Persistent factory Blender worker. No UI/RNA use and no source writes."""
import sys,json,time,os,importlib,errno
from pathlib import Path
from collections import OrderedDict
import numpy as np

def replace(temporary,path):
 # Windows readers/AV can briefly hold the old fixed cache file. Only the worker waits.
 deadline=time.monotonic()+3
 while True:
  try:os.replace(temporary,path);return
  except OSError as exc:
   if (exc.errno not in (errno.EACCES,errno.EPERM,errno.EBUSY) and getattr(exc,'winerror',None) not in (5,32,33)) or time.monotonic()>=deadline:raise
   time.sleep(.02)

def atomic(path,data):
 temporary=path.with_suffix(path.suffix+'.tmp');temporary.write_text(json.dumps(data));replace(temporary,path)

def run(root):
 folder=Path(__file__).resolve().parent;sys.path.insert(0,str(folder.parent))
 cat=importlib.import_module(folder.name+'.vfx_catalogue')
 core=importlib.import_module(folder.name+'.studio_core')
 assets=None;identity=None;last=None;cache=OrderedDict();cache_bytes=0;renderer=None;last_used=time.monotonic()
 while not (root/'stop').exists():
  try:request=json.loads((root/'request.json').read_text())
  except (OSError,ValueError):time.sleep(.04);continue
  token=request['token']
  if token==last:
   if time.monotonic()-last_used>60:break
   time.sleep(.04);continue
  last=token;result={'token':token,'kind':request['kind']}
  try:
   new_identity=(request['engine'],request.get('project'))
   if identity!=new_identity or request.get('refresh'):
    if assets:assets.close()
    assets=core.AssetLibrary(*new_identity).scan();identity=new_identity
    renderer=cat.Renderer(assets);cache.clear();cache_bytes=0
   if request['kind']=='INDEX':result['rows']=cat.catalogue(assets)
   else:
    row=request['row'];overrides=request.get('overrides') or None
    key=(cat.fingerprint(assets,row,overrides),request['kind'])
    if key not in cache:
     renderer.overrides=overrides or {}
     # Texture/mesh caches must not hide loose project edits between jobs.
     renderer.textures.clear();renderer.mesh_stamps.clear()
     frames=renderer.frames(row,frame_count=30 if request['kind']=='PLAY' else 6)
     if request['kind']=='POSTER':frames=frames[int(frames[:,:,:,:3].astype('float32').mean(axis=(1,2,3)).argmax())][None]
     cache[key]=(frames,renderer.last_info);cache_bytes+=frames.nbytes
     while cache_bytes>32*1024*1024 and len(cache)>1:
      _,(old,_) =cache.popitem(last=False);cache_bytes-=old.nbytes
    cache.move_to_end(key);frames,info=cache[key]
    result.update(info=info,cache_bytes=cache_bytes)
    # Never publish superseded pixels; one fixed result file bounds disk use.
    if json.loads((root/'request.json').read_text())['token']!=token:continue
    with (root/'frames.tmp').open('wb') as handle:np.save(handle,frames,allow_pickle=False)
    replace(root/'frames.tmp',root/'frames.npy')
   if json.loads((root/'request.json').read_text())['token']!=token:continue
  except Exception as exc:result['error']=str(exc)
  atomic(root/'result.json',result);last_used=time.monotonic()
  if request['kind']=='INDEX':break # Metadata lives in the UI; no idle Blender needed.
 if assets:assets.close()

if __name__=='__main__':run(Path(sys.argv[sys.argv.index('--')+1]))
