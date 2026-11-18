"""Main-thread result handoff; bounded external processes, latest selection wins."""
import errno,json,os,subprocess,tempfile,time,shutil
from collections import deque
from pathlib import Path
import bpy
import numpy as np

_JOBS={};_TOKEN=0;_REAP=[];_EVENTS=deque(maxlen=64)
IO_RETRY_SECONDS=3.0

def record(job,event,**extra):
 _EVENTS.append(dict(event=event,token=job.get('token'),path=job.get('path'),phase=job.get('phase'),time=time.monotonic(),**extra))

def transient(exc):
 return isinstance(exc,OSError) and (exc.errno in (errno.EACCES,errno.EPERM,errno.ENOENT,errno.EBUSY,errno.EINTR) or getattr(exc,'winerror',None) in (5,32,33))

def scene_exists(scene):
 try:return scene.name in bpy.data.scenes
 except ReferenceError:return False
def abort_worker(channel,job):
 """Roll back this submission's new worker without touching other channels."""
 if _JOBS.get(channel) is job:_JOBS.pop(channel)
 job.update(pending=False,phase='startup_error')
 if job['process'].poll() is None:
  try:job['process'].terminate()
  except OSError:pass # Keep it tracked until its own bounded worker exits.
 job['log'].close();_REAP.append(job);reap()
 if not any(j.get('pending') for j in _JOBS.values()) and bpy.app.timers.is_registered(poll):bpy.app.timers.unregister(poll)

def submit(context,kind,**payload):
 global _TOKEN
 from . import studio as st,mod_projects as mp,studio_vfx as ui
 channel='index' if kind=='INDEX' else 'preview'
 created=False
 if channel in _JOBS and _JOBS[channel]['process'].poll() is not None:
  _REAP.append(_JOBS.pop(channel));reap()
 if channel not in _JOBS:
  parent=ui.preview_root(context);parent.mkdir(parents=True,exist_ok=True)
  from .vfx_browser_cleanup import cleanup_abandoned,write_owner
  cleanup_abandoned(parent)
  root=Path(tempfile.mkdtemp(prefix='browser-job-',dir=parent))
  log=None;process=None;job=None
  try:
   log=(root/'worker.log').open('w',encoding='utf-8')
   options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
   process=subprocess.Popen([bpy.app.binary_path,'--background','--factory-startup','--threads','2','--python-exit-code','1','--python',str(Path(__file__).with_name('vfx_browser_worker.py')),'--',str(root)],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,**options)
   job={'root':root,'parent':root.parent.resolve(),'process':process,'log':log}
   _JOBS[channel]=job;created=True
   write_owner(root,process.pid)
  except BaseException:
   if job is not None:abort_worker(channel,job)
   else:
    if log is not None:log.close()
    # No child was returned. Remove only our exact newly-created log/folder;
    # unexpected content, redirects and I/O failures preserve the directory.
    try:
     from .vfx_browser_cleanup import reparse
     if root.resolve().parent==parent.resolve() and not reparse(root):
      children=list(root.iterdir())
      if all(p.name=='worker.log' and p.is_file() and not reparse(p) for p in children):
       for p in children:p.unlink()
       root.rmdir()
    except OSError:pass
   raise
 _TOKEN+=1;job=_JOBS[channel];job.update(token=_TOKEN,kind=kind,scene=context.scene,path=st.settings(context).effect_asset,started=time.monotonic(),pending=True,phase='queued_request',request_ready=False)
 job.pop('io_started',None);job.pop('error',None)
 request=dict(token=_TOKEN,kind=kind,engine=str(st.engine(context) or ''),project=str(mp.active_project(context,required=False) or '') or None,**payload)
 # Worker reads request.json only; a newer queued request replaces this fixed temporary.
 try:
  (job['root']/'request.tmp').write_text(json.dumps(request));record(job,'submit')
  if not bpy.app.timers.is_registered(poll):bpy.app.timers.register(poll,first_interval=.01)
 except BaseException as exc:
  job.update(pending=False,phase='submission_error',error=repr(exc));record(job,'submission_failure',error=repr(exc))
  if created:abort_worker(channel,job)
  raise
 return _TOKEN

def cancel_preview():
 job=_JOBS.get('preview')
 if job and job.get('pending'):
  job.update(pending=False,phase='cancelled');record(job,'cancel')

def current(channel,job,token):
 from . import studio as st
 scene=job['scene']
 return (_JOBS.get(channel) is job and job.get('pending') and job['token']==token and scene_exists(scene) and scene==bpy.context.scene and (channel=='index' or st.settings(bpy.context).effect_asset==job['path']))

def poll():
 from . import studio_vfx as ui,studio as st
 for channel,job in list(_JOBS.items()):
  if not job.get('pending'):continue
  token=job['token'];scene=job['scene']
  try:
   if not current(channel,job,token):
    job.update(pending=False,phase='cancelled');record(job,'context_cancel')
    if scene_exists(scene):scene.sanctuary_studio.status='Preview cancelled after scene or selection changed. Press Play to resume.'
    continue
   if not job.get('request_ready'):
    os.replace(job['root']/'request.tmp',job['root']/'request.json')
    job.update(request_ready=True,phase='awaiting_worker');record(job,'request_published')
   result=job['root']/'result.json'
   response=json.loads(result.read_text()) if result.is_file() else {}
   if response.get('token')!=token:
    if job['process'].poll() is not None:raise RuntimeError('Preview worker stopped; see '+str(job['root']/'worker.log'))
    if time.monotonic()-job['started']>120:raise RuntimeError('Preview worker timed out; choose another effect or reopen the browser.')
    continue
   if response.get('kind')!=job['kind']:raise ValueError('Preview response kind does not match its request')
   if response.get('error'):raise RuntimeError(response['error'])
   job['phase']='publishing';record(job,'response_ready')
   if channel=='index':
    if not current(channel,job,token):continue
    ui.begin_rows(bpy.context,response['rows'])
    job.update(pending=False,phase='ready');record(job,'rows_published')
    _JOBS.pop(channel);_REAP.append(job);reap()
   else:
    frames=np.load(job['root']/'frames.npy',allow_pickle=False)
    if frames.dtype!=np.uint8 or frames.ndim!=4 or frames.shape[1:]!=(256,256,4) or len(frames)!=(30 if job['kind']=='PLAY' else 1):raise ValueError('Invalid preview frame payload')
    # A superseded/cancelled generation cannot publish after file I/O.
    if not current(channel,job,token):continue
    if job['kind']=='PLAY':ui.start_animation(bpy.context,frames,response['info'])
    else:ui.show_pixels(bpy.context,frames[0],response['info'])
    if job['token']==token and _JOBS.get(channel) is job:
     job.update(pending=False,phase='ready');job.pop('io_started',None);record(job,'pixels_published')
   ui.redraw_browser()
  except (OSError,ValueError,RuntimeError,ReferenceError,AssertionError,KeyError,TypeError) as exc:
   if job.get('token')!=token or _JOBS.get(channel) is not job:continue
   retry_start=job.setdefault('io_started',time.monotonic()) if transient(exc) else None
   if retry_start is not None and time.monotonic()-retry_start<IO_RETRY_SECONDS and current(channel,job,token):
    record(job,'cache_retry',error=repr(exc));st.settings(bpy.context).status='Preview cache busy; retrying. Stop or choose another effect to cancel.'
    continue
   job.update(pending=False,phase='error',error=repr(exc));record(job,'failure',error=repr(exc))
   if channel=='preview' and ui._ANIMATION is not None and ui._ANIMATION[0]==scene:ui.stop_animation()
   if scene_exists(scene):scene.sanctuary_studio.status='Preview unavailable: '+str(exc)+'. Press Play to retry or reopen the browser. Worker log: '+str(job['root']/'worker.log')
 # Include requests queued while publishing so the real timer cannot stop early.
 return .05 if any(j.get('pending') for j in _JOBS.values()) else None

def close():
 for job in list(_JOBS.values()):
  job.update(pending=False,phase='closed');record(job,'close')
  if job['process'].poll() is None:job['process'].terminate()
  job['log'].close()
  _REAP.append(job)
 _JOBS.clear()
 if bpy.app.timers.is_registered(poll):bpy.app.timers.unregister(poll)
 reap()

def reap():
 for job in list(_REAP):
  if job['process'].poll() is None:continue
  job['log'].close();root=job['root'].resolve()
  # Only this process's exact generated directory, with no reparse targets.
  assert root.parent==job['parent'] and root.name.startswith('browser-job-')
  try:
   if root.exists():
    if any(p.is_symlink() or (getattr(p.lstat(),'st_file_attributes',0)&1024) for p in (root,*root.rglob('*'))):raise RuntimeError('Unsafe generated cache reparse point')
    shutil.rmtree(root)
   _REAP.remove(job)
  except OSError:pass
 if _REAP:
  if not bpy.app.timers.is_registered(reap):bpy.app.timers.register(reap,first_interval=.1)
  return .1
 return None
