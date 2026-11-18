"""Search metadata, straight-line projectile previews and bounded preview cache.

Previews interpret exported particle/material data; they are not captures of
Unity shaders, collisions or GPU simulation. The gallery labels this explicitly.
"""
from collections import OrderedDict
import hashlib
import html
import json
import math
from pathlib import Path
import re
import time
import numpy as np
from .studio_core import Blueprint, AssetLibrary
from .vfx_documents import display_name
from .vfx_workflow import EXTENSIONS
from .vfx_gif import write_gif, write_png

VERSION = 10
FRAME_COUNT = 30
RESOLUTION = 256
PREVIEW_SIGNATURE=hashlib.sha256(b''.join(Path(__file__).with_name(name).read_bytes() for name in ('vfx_simulation.py','preview_shader_defaults.json','preview_diagnostics.py'))).hexdigest()


def unit_usage(engine,project):
    """Link IDs to their real consumers; never infer a tank from ID digits."""
    projectiles, effects = {}, {}
    custom_tag=''
    if project and (Path(project)/'smt_project.json').is_file():
        metadata=json.loads((Path(project)/'smt_project.json').read_text(encoding='utf-8-sig'))
        custom_tag=metadata.get('faction_tag','')
    for root in (Path(engine)/'LJ/lua' if engine else None,Path(project)/'Lua' if project else None):
        if not root:continue
        for file in (root/'common/units/unitsTemplates').rglob('*.santp'):
            try:
                doc=Blueprint(file.read_text(encoding='utf-8-sig'))
                uid=doc.scalar(('general','tpId'),file.stem)
                tags=doc.strings(('tags',))
                faction=custom_tag if root==Path(project or '')/'Lua' and custom_tag in tags else next((t for t in tags if t in ('EDA','CHOSEN','GUARD')),'')
                if not faction:
                    faction=next((t for t in tags if t not in ('LAND','AIR','MOBILE','STRUCTURE','NAVAL','COMMAND','COMMANDER') and
                                  not t.startswith(('TECH','BUILDABLE_'))),'Custom')
                tier=next((t.replace('TECH','T') for t in tags if re.fullmatch(r'TECH\d+',t)),'')
                title=str(doc.scalar(('general','displayName'),doc.scalar(('general','name'),uid)))
                title=re.sub(r'(?i)^(?:Tier\s*\d+:?\s*|T\d+\s*)','',title)
                label=' '.join(v for v in (tier,faction,title) if v)
                consumer={'unit':uid,'name':label,'tags':sorted(set(tags+[faction,tier]))}
                for path,_ in doc.leaves():
                    value=doc.scalar(path)
                    if not isinstance(value,str):continue
                    if path[-1]=='projectileTemplate':projectiles.setdefault(value.lower(),{})[uid]=consumer
                    elif value.lower().endswith(EXTENSIONS):effects.setdefault(value.lower(),{})[uid]=consumer
            except (OSError,ValueError):continue
    return ({k:list(v.values()) for k,v in projectiles.items()},{k:list(v.values()) for k,v in effects.items()})


def projectile_records(engine, project):
    records = {}
    consumers,_=unit_usage(engine,project)
    for root in (Path(engine) / 'LJ/lua' if engine else None, Path(project) / 'Lua' if project else None):
        if not root: continue
        for file in (root / 'common/projectiles/projectilesTemplates').rglob('*.santp'):
            try:
                text = file.read_text(encoding='utf-8-sig')
                doc = Blueprint(text, 'ProjectileTemplate')
                uid = doc.scalar(('general', 'tpId'))
                if not isinstance(uid, str): continue
                slots = [value for path, _ in doc.leaves() if isinstance((value := doc.scalar(path)), str)
                         and value.lower().endswith(EXTENSIONS)]
                users=consumers.get(uid.lower(),[])
                title=(users[0]['name']+' Projectile') if users else display_name(uid,{'general':{'name':doc.scalar(('general','name'),uid)}})+' Projectile'
                tags={t for c in users for t in c['tags'] if t}
                prefix=re.match(r'p([ecg])',uid)
                if prefix:tags.add({'e':'EDA','c':'CHOSEN','g':'GUARD'}[prefix.group(1)])
                records[uid.lower()] = {'id': uid, 'name': title,'engine_name':str(doc.scalar(('general','name'),uid)),
                                       'users':users,'tags':sorted(tags),
                                       'file': str(file), 'effects': slots, 'text': text}
            except (ValueError, OSError): continue
    return records


def catalogue(assets):
    rows = []
    _,usage=unit_usage(assets.engine,assets.project)
    for path in sorted(assets.records):
        if not path.endswith(EXTENSIONS): continue
        try:
            data = assets.json(path)
            title = display_name(path, data)
            category = ('materials' if path.endswith('.sanmaterial') else 'beams' if path.endswith('.sanbeam')
                        else 'decals' if path.endswith('.sandecal') else next((kind for kind in ('muzzle','trails','impact','death','unit')
                                                                        if '/' + kind + '/' in path), 'other'))
            users=usage.get(path,[])
            tags={t for c in users for t in c['tags'] if t}
            for tag in ('EDA','CHOSEN','GUARD'):
                if tag.lower() in (title+' '+path).lower():tags.add(tag)
            prefix=re.match(r'v([ecg])',Path(path).stem)
            if prefix:tags.add({'e':'EDA','c':'CHOSEN','g':'GUARD'}[prefix.group(1)])
            rows.append({'path': path, 'name': title, 'category': category,'tags':sorted(tags),'users':users,
                         'search': (title + ' ' + path + ' ' + str(data.get('shader', ''))+' '+' '.join(sorted(tags))+' '+' '.join(c['name'] for c in users)).casefold(), 'kind': 'effect'})
        except (ValueError, KeyError, OSError) as exc:
            rows.append({'path': path, 'name': display_name(path), 'category': 'other', 'kind': 'effect',
                         'search': path.casefold(), 'error': str(exc)})
    for uid, record in projectile_records(assets.engine, assets.project).items():
        prefix=re.match(r'p([ecg])',uid)
        if prefix:record['tags']=sorted(set(record['tags'])|{{'e':'EDA','c':'CHOSEN','g':'GUARD'}[prefix.group(1)]})
        rows.append({'path': 'projectile:' + uid, 'name': record['name'],'tags':record['tags'],'users':record['users'],
                     'category': 'projectiles', 'search': (uid + ' ' + record['name'] + ' '+record['engine_name']+' '+' '.join(record['tags'])+' '+' '.join(c['name'] for c in record['users'])+' ' + ' '.join(record['effects'])).casefold(),
                     'kind': 'projectile', 'file': record['file']})
    return rows


def constant(value, default=1):
    if isinstance(value, (int, float)): result = float(value)
    elif isinstance(value, dict):
        mode = value.get('mode', 0)
        if mode == 3: result = (value.get('constantMin', default) + value.get('constantMax', default)) / 2
        elif mode in (1, 2):
            keys = value.get('curve', value.get('curveMax', {})).get('keys', [])
            result = (sum(k.get('value', 0) for k in keys) / max(1, len(keys))) * value.get('curveMultiplier', 1)
        else: result = value.get('constant', value.get('constantMax', default))
    else: result = default
    return max(-1000, min(1000, result)) if math.isfinite(result) else default


def dependencies(assets, path, overrides=None, visited=None):
    from .preview_diagnostics import resolve_path
    overrides = overrides or {}; visited = visited if visited is not None else set()
    path,_=resolve_path(assets,path)
    key = path.lower()
    if key in visited or key not in assets.records: return []
    visited.add(key)
    if key in overrides:stamp=hashlib.sha256(json.dumps(overrides[key],sort_keys=True).encode()).hexdigest()
    else:
        location,member=assets.records[key]
        stat=Path(location).stat()
        if member:
            if location not in assets._archives:
                import zipfile
                assets._archives[location]=zipfile.ZipFile(location)
            info=assets._archives[location].getinfo(member)
            stamp=str((info.CRC,info.file_size,stat.st_mtime_ns))
        else:stamp=str((stat.st_mtime_ns,stat.st_size))
    result = [(key,stamp)]
    if key.endswith(EXTENSIONS):
        data = overrides.get(key) or assets.json(path)
        def walk(value):
            if isinstance(value, dict):
                for child in value.values(): yield from walk(child)
            elif isinstance(value, list):
                for child in value: yield from walk(child)
            elif isinstance(value, str) and resolve_path(assets,value)[0] in assets.records:
                yield value
        for child in walk(data): result += dependencies(assets, child, overrides, visited)
    return result


def fingerprint(assets, row, overrides=None):
    from .preview_diagnostics import resolve_path
    if row['kind'] == 'projectile':
        blob = (overrides or {}).get('@projectile') or Path(row['file']).read_text(encoding='utf-8-sig')
        doc = Blueprint(blob, 'ProjectileTemplate')
        refs = [doc.scalar(p) for p, _ in doc.leaves() if isinstance(doc.scalar(p), str) and resolve_path(assets,doc.scalar(p))[0] in assets.records]
        parts = [('@projectile', hashlib.sha256(blob.encode()).hexdigest())]
        seen = set()
        for ref in refs: parts += dependencies(assets, ref, overrides, seen)
    else: parts = dependencies(assets, row['path'], overrides)
    return hashlib.sha256(json.dumps([VERSION, PREVIEW_SIGNATURE, FRAME_COUNT, RESOLUTION, row.get('preview_emitter_speed',1), sorted(parts)], sort_keys=True).encode()).hexdigest()[:24]


class Renderer:
    def __init__(self, assets, overrides=None):
        self.assets = assets; self.overrides = overrides or {}; self.textures = OrderedDict()
        self.warnings = [];self.mesh_stamps=OrderedDict()

    def data(self, path):
        return self.overrides.get(path.lower()) or self.assets.json(path)

    def texture(self, path):
        key=path.lower()
        if key not in self.assets.records:return None
        if key not in self.textures:
            from .dds_tools import decode_dds_top
            try:
                pixels=decode_dds_top(self.assets.read(key))
                if isinstance(pixels,tuple):pixels=pixels[0]
                h,w=pixels.shape[:2]
                pixels=pixels[np.linspace(0,h-1,min(h,1024)).astype(int)[:,None],np.linspace(0,w-1,min(w,1024)).astype(int)[None,:]].copy()
            except (ValueError,KeyError,RuntimeError) as exc:
                pixels=None;self.warnings.append(str(exc))
            self.textures[key]=pixels
            if len(self.textures)>16:self.textures.popitem(last=False)
        return self.textures[key]

    def material(self, path):
        if not path or path.lower() not in self.assets.records:return {},None
        data=self.data(path)
        choices=sorted(data.get('textures',[]),key=lambda t:0 if t.get('key','').lower() in
                       ('_comptexture','_maintex','_diffuse','_basemap','_emissive','_basecolormap') else 1)
        chosen=next((t for t in choices if t.get('path','').lower() in self.assets.records and
                     not any(k in t.get('key','').lower() for k in ('normal','mask','distort','motion','gradient','damage','break'))),None)
        return data,self.texture(chosen['path']) if chosen else None

    def surface(self, material, texture, mesh=''):
        surface={'image':texture}
        if 'shield' in material.get('shader','').lower():
            for slot in material.get('textures',[]):
                key=slot.get('key','').lower()
                if key in ('_energy_normals','_alpha','_energy'):
                    surface[key]=self.texture(slot.get('path',''))
        mask=next((t for t in material.get('textures',[]) if t.get('key','').lower()=='_mask'),None)
        if mask:
            surface['mask']=self.texture(mask.get('path',''))
            surface['mask_slot']=mask
        if mesh:
            key=('uv',mesh)
            if key not in self.mesh_stamps:
                from .vfx_simulation import mesh_uv
                self.mesh_stamps[key]=mesh_uv(self.assets.read(mesh),mesh)
                if len(self.mesh_stamps)>32:self.mesh_stamps.popitem(last=False)
            surface['uv_map'],surface['axes']=self.mesh_stamps[key]
        return surface

    def color(self, material, particle=None):
        color = (particle or {}).get('main', {}).get('startColor', {}).get('color', {})
        tint = np.array([max(0, min(10000, color.get(k, 1))) for k in 'rgb'], dtype=float)
        preferred = sorted(material.get('colors', []), key=lambda c: 0 if any(k in c.get('key','').lower() for k in ('emission','emissive','color')) else 1)
        if preferred:
            values = preferred[0].get('value', {})
            tint *= [max(0, min(10000, values.get(k, 1))) for k in 'rgb']
        # Tone-map HDR values, retaining the relative tint.
        largest = max(1, float(tint.max()))
        return tint / largest * (1 - math.exp(-largest))

    def mesh_stamp(self,path,texture):
        """Small textured side-profile of the actual particle projectile mesh."""
        key=(path,id(texture))
        if key in self.mesh_stamps:return self.mesh_stamps[key]
        from .sanmodel_io import parse_sanmodel_bytes
        model=parse_sanmodel_bytes(self.assets.read(path))
        vertices=model.vertices
        if path.lower().startswith('vfx/'):
            axes=np.argsort(np.ptp(vertices,axis=0))[-2:]
            xy=np.stack((vertices[:,axes[1]],-vertices[:,axes[0]]),axis=1)
        else:
            xy=np.stack((vertices[:,2]*.94+vertices[:,0]*.34,-vertices[:,1]+vertices[:,0]*.12),axis=1)
        xy-= (xy.max(axis=0)+xy.min(axis=0))/2
        extent=max(float(np.abs(xy).max()),1e-6);xy=xy/extent*27+32
        stamp=np.zeros((64,64,4),dtype=np.uint8)
        order=np.argsort(vertices[model.indices].mean(axis=1)[:,0])
        # Bounded mesh rasterisation, not a replacement for the import pipeline.
        for tri in model.indices[order][-4000:]:
            p=xy[tri];low=np.maximum(np.floor(p.min(axis=0)).astype(int),0);high=np.minimum(np.ceil(p.max(axis=0)).astype(int),63)
            if np.any(high<low):continue
            yy,xx=np.mgrid[low[1]:high[1]+1,low[0]:high[0]+1]
            denom=(p[1,1]-p[2,1])*(p[0,0]-p[2,0])+(p[2,0]-p[1,0])*(p[0,1]-p[2,1])
            if abs(denom)<1e-8:continue
            a=((p[1,1]-p[2,1])*(xx-p[2,0])+(p[2,0]-p[1,0])*(yy-p[2,1]))/denom
            b=((p[2,1]-p[0,1])*(xx-p[2,0])+(p[0,0]-p[2,0])*(yy-p[2,1]))/denom
            inside=(a>=0)&(b>=0)&(a+b<=1)
            shade=np.full((*xx.shape,4),255,dtype=np.uint8)
            if texture is not None and len(model.uv1)==len(vertices):
                uv=a[:,:,None]*model.uv1[tri[0]]+b[:,:,None]*model.uv1[tri[1]]+(1-a-b)[:,:,None]*model.uv1[tri[2]]
                th,tw=texture.shape[:2]
                shade=texture[np.clip(((1-uv[:,:,1])*(th-1)).astype(int),0,th-1),np.clip((uv[:,:,0]*(tw-1)).astype(int),0,tw-1)].copy()
            if len(model.normals)==len(vertices):
                light=1 if path.lower().startswith('vfx/') else .4+.6*abs(float(model.normals[tri].mean(axis=0)[0]))
                shade[:,:,:3]=(shade[:,:,:3]*light).astype(np.uint8)
            view=stamp[low[1]:high[1]+1,low[0]:high[0]+1];view[inside]=shade[inside]
        self.mesh_stamps[key]=stamp
        if len(self.mesh_stamps)>32:self.mesh_stamps.popitem(last=False)
        return stamp

    @staticmethod
    def sprite(canvas, x, y, radius, color, texture=None, alpha=1):
        radius = max(1, min(70, float(radius)))
        x0, y0 = max(0, int(x-radius)), max(0, int(y-radius))
        x1, y1 = min(RESOLUTION, int(x+radius+1)), min(RESOLUTION, int(y+radius+1))
        if x1 <= x0 or y1 <= y0: return
        yy, xx = np.mgrid[y0:y1, x0:x1]
        dx, dy = (xx-x)/radius, (yy-y)/radius
        mask = np.maximum(0, 1-dx*dx-dy*dy)**2 * alpha
        rgb = np.asarray(color)
        if texture is not None:
            h,w = texture.shape[:2]
            sample = texture[np.clip(((dy+1)/2*(h-1)).astype(int),0,h-1), np.clip(((dx+1)/2*(w-1)).astype(int),0,w-1)] / 255
            rgb = sample[:,:,:3] * rgb
            mask *= sample[:,:,3]
        canvas[y0:y1,x0:x1] += rgb * mask[:,:,None]

    def frames(self, row, resolution=None, frame_count=None):
        from .vfx_simulation import render
        self.warnings.clear()
        return render(self, row, resolution or RESOLUTION, frame_count or FRAME_COUNT)


def build_preview(assets, row, root, overrides=None, renderer=None):
    root = Path(root); root.mkdir(parents=True,exist_ok=True)
    key = fingerprint(assets,row,overrides)
    gif, png = root/(key+'.gif'), root/(key+'.png')
    info=root/(key+'.preview.json')
    if not overrides and (not gif.is_file() or not png.is_file() or not info.is_file()):
        # Exact current source/signature/speed fingerprints only; custom or
        # updated source gets its own key and cannot reuse stale stock pixels.
        import zipfile
        bundle=Path(__file__).with_name('preview_library.zip')
        if bundle.is_file():
            with zipfile.ZipFile(bundle) as archive:
                names=set(archive.namelist())
                if all(file.name in names for file in (gif,png,info)):
                    for file in (gif,png,info):file.write_bytes(archive.read(file.name))
    if not gif.is_file() or not png.is_file() or not info.is_file():
        renderer = renderer or Renderer(assets,overrides)
        frames = renderer.frames(row)
        write_gif(gif,frames,fps=FRAME_COUNT/max(.01,renderer.last_info['seconds']))
        poster=int(frames[:,:,:,:3].astype('float32').mean(axis=(1,2,3)).argmax())
        write_png(png,frames[poster])
        info.write_text(json.dumps(renderer.last_info,indent=2),encoding='utf-8')
    row['preview']=json.loads(info.read_text(encoding='utf-8'))
    return gif, png


def gallery(rows, root):
    root = Path(root)
    cards = []
    for row in rows:
        if 'gif' not in row: continue
        cards.append('<article data-search="'+html.escape(row['search'],quote=True)+'" data-category="'+row['category']+'">'
                     '<img loading="lazy" src="'+html.escape(Path(row['gif']).name,quote=True)+'">'
                     '<h3>'+html.escape(row['name'])+'</h3><code>'+html.escape(row['path'])+'</code>'
                     +'<p>'+html.escape(row.get('preview',{}).get('mode','Particle approximation'))+'</p>'
                     +(''.join('<p class="error">'+html.escape(limit)+'</p>' for limit in row.get('preview',{}).get('limitations',[])))
                     +(''.join('<p>'+html.escape(d['message']+' '+d['action'])+'</p>' for d in row.get('preview',{}).get('diagnostics',[])))
                     +('<p class="error">'+html.escape(row['error'])+'</p>' if row.get('error') else '')+'</article>')
    document = '''<!doctype html><html><meta charset="utf-8"><title>Sanctuary VFX & Projectiles</title>
<style>body{background:#101925;color:#dce8f5;font:15px system-ui;margin:24px}header{position:sticky;top:0;background:#101925;padding:12px;z-index:1}input,select{font:inherit;padding:10px;background:#1b2b3d;color:white;border:1px solid #4b6b8d}main{display:grid;grid-template-columns:repeat(auto-fill,minmax(245px,1fr));gap:15px}article{background:#1b2b3d;padding:14px;border-radius:8px}img{width:100%;image-rendering:auto}code{font-size:11px;overflow-wrap:anywhere}.error{color:#ffba8a}</style>
<header><h1>Sanctuary VFX & Projectiles</h1><p>Animated data previews · approximate exported particles/materials. Unity shaders, physics and collisions can differ. Projectile flight is a straight-line demonstration.</p>
<input id="search" placeholder="Name, ID, faction, path or shader…" size="40"><select id="category"><option value="">All</option>'''
    for category in sorted({row['category'] for row in rows}): document += '<option>'+category+'</option>'
    document += '</select> <span id="count"></span></header><main>'+''.join(cards)+'''</main>
<script>function filter(){let n=0,q=document.getElementById('search').value.toLowerCase().split(/\s+/).filter(Boolean),c=document.getElementById('category').value;document.querySelectorAll('article').forEach(a=>{let yes=q.every(w=>a.dataset.search.includes(w))&&(!c||a.dataset.category===c);a.hidden=!yes;a.style.display=yes?'':'none';n+=yes;});document.getElementById('count').textContent=n+' previews';}document.getElementById('search').oninput=filter;document.getElementById('category').onchange=filter;filter();</script></html>'''
    target = root/'gallery.html'; target.write_text(document,encoding='utf-8'); return target


def run_job(job_path):
    job_path = Path(job_path); job = json.loads(job_path.read_text())
    assets = AssetLibrary(job.get('engine'),job.get('project')).scan()
    rows = job['rows']; root = Path(job['root']); root.mkdir(parents=True,exist_ok=True)
    renderer = Renderer(assets)
    started = time.monotonic()
    for index,row in enumerate(rows):
        try:
            gif,png = build_preview(assets,row,root,renderer=renderer)
            row.update(gif=str(gif),poster=str(png))
        except Exception as exc:
            row['error'] = str(exc)
        if index % 10 == 0 or index == len(rows)-1:
            (root/'progress.json').write_text(json.dumps({'done':index+1,'total':len(rows),'errors':sum(bool(r.get('error')) for r in rows),'seconds':time.monotonic()-started}),encoding='utf-8')
    gallery(rows,root)
    (root/'catalogue.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    assets.close()
