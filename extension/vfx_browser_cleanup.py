"""Conservative recovery of marked, inactive browser job folders in their own cache."""
import json,os,time,shutil
from pathlib import Path

PRODUCER='Sanctuary Studio VFX browser';_SCANS={}
FILES={'owner.json','request.json','request.tmp','result.json','result.json.tmp','frames.npy','frames.tmp','worker.log','stop'}

def alive(pid):
 if not isinstance(pid,int) or pid<=0:return True
 if os.name=='nt':
  import ctypes
  kernel=ctypes.WinDLL('kernel32',use_last_error=True)
  kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32];kernel.OpenProcess.restype=ctypes.c_void_p
  kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)];kernel.CloseHandle.argtypes=[ctypes.c_void_p]
  handle=kernel.OpenProcess(0x1000,False,pid)
  if not handle:return ctypes.get_last_error()!=87 # Access denied/unknown: preserve.
  try:
   code=ctypes.c_uint32()
   return not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value==259
  finally:kernel.CloseHandle(handle)
 try:os.kill(pid,0);return True
 except ProcessLookupError:return False
 except OSError:return True

def reparse(path):return path.is_symlink() or bool(getattr(path.lstat(),'st_file_attributes',0)&1024)

def write_owner(root,worker_pid):
 data={'producer':PRODUCER,'schema':1,'directory':root.name,'parent':str(root.parent.resolve()),'owner_pid':os.getpid(),'worker_pid':worker_pid,'created':time.time()}
 (root/'owner.json').write_text(json.dumps(data))

def cleanup_abandoned(parent):
 parent=Path(parent)
 if not parent.is_dir() or reparse(parent):return []
 parent=parent.resolve();now=time.monotonic();key=str(parent)
 if now-_SCANS.get(key,-1000)<60:return []
 _SCANS[key]=now
 if len(_SCANS)>8:_SCANS.pop(next(iter(_SCANS)))
 removed=[]
 # Bound startup work; unknown/legacy directories are never interpreted as owned.
 for n,root in enumerate(parent.iterdir()):
  if n>=64:break
  if not root.name.startswith('browser-job-'):continue
  try:
   if reparse(root) or not root.is_dir():continue
   marker=root/'owner.json'
   if not marker.is_file() or reparse(marker):continue
   data=json.loads(marker.read_text())
   if data.get('producer')!=PRODUCER or data.get('schema')!=1 or data.get('directory')!=root.name or data.get('parent')!=str(parent):continue
   age=time.time()-float(data['created'])
   if age<120 or alive(data.get('owner_pid')) or alive(data.get('worker_pid')):continue
   children=list(root.iterdir())
   if len(children)>len(FILES) or any(p.name not in FILES or reparse(p) or not p.is_file() for p in children):continue
   # Validate final absolute location and marker again immediately before deletion.
   if root.resolve().parent!=parent or json.loads(marker.read_text())!=data:continue
   shutil.rmtree(root);removed.append(root.name)
  except (OSError,ValueError,TypeError,KeyError):continue
 return removed
