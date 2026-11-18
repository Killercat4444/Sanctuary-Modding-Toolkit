"""Bounded CPU preview of exported particles, not a Unity shader implementation.

Uses native birth times, curves, gradients, texture sheets and material blend
data. Unknown shader graphs and GPU-only modules remain approximations.
"""
import math
import numpy as np
import json
from pathlib import Path

SHADER_DEFAULTS=json.loads(Path(__file__).with_name('preview_shader_defaults.json').read_text(encoding='utf-8'))


def curve(value, t=0, seed=.5, default=1):
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) else default
    if not isinstance(value, dict): return default
    mode=value.get('mode',0)
    def sample(data):
        keys=sorted(data.get('keys',[]),key=lambda k:k.get('time',0))
        if not keys: return default
        if t<=keys[0].get('time',0): return keys[0].get('value',default)
        if t>=keys[-1].get('time',1): return keys[-1].get('value',default)
        for a,b in zip(keys,keys[1:]):
            if a['time']<=t<=b['time']:
                dt=b['time']-a['time']; u=(t-a['time'])/max(dt,1e-9)
                # Unweighted Hermite keys; weighted tangents are approximated.
                av=a.get('value',default); bv=b.get('value',default)
                at=a.get('outTangent',0); bt=b.get('inTangent',0)
                if not math.isfinite(at) or not math.isfinite(bt): return av
                return (2*u**3-3*u*u+1)*av+(u**3-2*u*u+u)*dt*at+(-2*u**3+3*u*u)*bv+(u**3-u*u)*dt*bt
        return default
    if mode==3:
        result=value.get('constantMin',default)*(1-seed)+value.get('constantMax',default)*seed
    elif mode in (1,2):
        hi=sample(value.get('curve',value.get('curveMax',{})))
        lo=sample(value.get('curveMin',{})) if mode==2 else hi
        result=(lo*(1-seed)+hi*seed)*value.get('curveMultiplier',1)
    else: result=value.get('constant',value.get('constantMax',default))
    return float(np.clip(result,-10000,10000)) if math.isfinite(result) else default


def gradient(value,t=0,seed=.5):
    if not isinstance(value,dict): return np.ones(4)
    def rgba(c): return np.array([c.get(k,1) for k in 'rgba'],dtype=float)
    def sample(g):
        ck=sorted(g.get('colorKeys',[]),key=lambda k:k['time'])
        ak=sorted(g.get('alphaKeys',[]),key=lambda k:k['time'])
        color=np.array([np.interp(t,[k['time'] for k in ck],[k['color'].get(c,1) for k in ck]) for c in 'rgb']) if ck else np.ones(3)
        alpha=np.interp(t,[k['time'] for k in ak],[k['alpha'] for k in ak]) if ak else 1
        return np.r_[color,alpha]
    mode=value.get('mode',0)
    if mode in (1,3):
        hi=sample(value.get('gradient',value.get('gradientMax',{})))
        result=sample(value.get('gradientMin',{}))*(1-seed)+hi*seed if mode==3 else hi
    elif mode==2: result=rgba(value.get('colorMin',{}))*(1-seed)+rgba(value.get('colorMax',{}))*seed
    elif mode==4: result=sample(value.get('gradient',value.get('gradientMax',{}))) if not value.get('gradient',{}).get('colorKeys') else rgba(value['gradient']['colorKeys'][min(len(value['gradient']['colorKeys'])-1,int(seed*len(value['gradient']['colorKeys'])))]['color'])
    else: result=rgba(value.get('color',value.get('colorMax',{})))
    return np.nan_to_num(np.clip(result,0,10000),nan=0,posinf=10000,neginf=0)


def parameters(material):
    values={k:v for k,v in SHADER_DEFAULTS.get(material.get('shader','').lower(),{}).items() if isinstance(v,(int,float))}
    values.update({x['key'].lower():x.get('value',0) for x in material.get('floats',[])})
    return values


def tint(material):
    colors={k:dict(zip('rgba',v)) for k,v in SHADER_DEFAULTS.get(material.get('shader','').lower(),{}).items() if isinstance(v,list)}
    colors.update({x['key'].lower():x.get('value',{}) for x in material.get('colors',[])})
    # Exported composite/trail materials carry the recognizable hue here, often
    # with HDR values well above 1. _EmissionColor is frequently plain white.
    selected=next((colors[k] for k in ('_flamecolor','_flame_color','_flare_color','_diffusecolor','_diffuse_color','_basecolor','_color','_tintcolor','_emissioncolor') if k in colors),{})
    return np.clip([selected.get(k,1) for k in 'rgb'],0,10000)


def sample_texture(texture,u,v):
    h,w=texture.shape[:2];x=np.clip(u,0,1)*(w-1);y=np.clip(v,0,1)*(h-1)
    ix=x.astype(int);iy=y.astype(int);fx=(x-ix)[...,None];fy=(y-iy)[...,None]
    a=texture[iy,ix];b=texture[iy,np.minimum(ix+1,w-1)]
    c=texture[np.minimum(iy+1,h-1),ix];d=texture[np.minimum(iy+1,h-1),np.minimum(ix+1,w-1)]
    result=(a*(1-fx)+b*fx)*(1-fy)+(c*(1-fx)+d*fx)*fy
    return result/255 if texture.dtype==np.uint8 else result


def mesh_uv(blob,path):
    """Rasterize geometry into UV/coverage, not a pre-shaded texture atlas."""
    from .sanmodel_io import parse_sanmodel_bytes
    model=parse_sanmodel_bytes(blob);vertices=model.vertices
    if path.lower().startswith('vfx/'):
        axes=np.argsort(np.ptp(vertices,axis=0))[-2:]
        xy=np.stack((vertices[:,axes[1]],-vertices[:,axes[0]]),axis=1)
    else:
        axes=np.array((1,2))
        xy=np.stack((vertices[:,2]*.94+vertices[:,0]*.34,-vertices[:,1]+vertices[:,0]*.12),axis=1)
    xy-=(xy.max(axis=0)+xy.min(axis=0))/2
    xy=xy/max(float(np.abs(xy).max()),1e-6)*59+64
    result=np.zeros((128,128,3),dtype=np.float32)
    for tri in model.indices[-4000:]:
        p=xy[tri];low=np.maximum(np.floor(p.min(axis=0)).astype(int),0);high=np.minimum(np.ceil(p.max(axis=0)).astype(int),127)
        if np.any(high<low):continue
        yy,xx=np.mgrid[low[1]:high[1]+1,low[0]:high[0]+1]
        denom=(p[1,1]-p[2,1])*(p[0,0]-p[2,0])+(p[2,0]-p[1,0])*(p[0,1]-p[2,1])
        if abs(denom)<1e-8:continue
        a=((p[1,1]-p[2,1])*(xx-p[2,0])+(p[2,0]-p[1,0])*(yy-p[2,1]))/denom
        b=((p[2,1]-p[0,1])*(xx-p[2,0])+(p[0,0]-p[2,0])*(yy-p[2,1]))/denom
        inside=(a>=0)&(b>=0)&(a+b<=1)
        uv=a[:,:,None]*model.uv1[tri[0]]+b[:,:,None]*model.uv1[tri[1]]+(1-a-b)[:,:,None]*model.uv1[tri[2]] if len(model.uv1)==len(vertices) else np.stack((xx/127,1-yy/127),axis=-1)
        view=result[low[1]:high[1]+1,low[0]:high[0]+1]
        view[:,:,0][inside]=uv[:,:,0][inside];view[:,:,1][inside]=1-uv[:,:,1][inside];view[:,:,2][inside]=1
    return result,(int(axes[1]),int(axes[0]))


def sprite(canvas,x,y,rx,ry,color,texture,alpha=1,angle=0,additive=True,uv=(0,0,1,1),material=None):
    if alpha<=0 or rx<=.05 or ry<=.05:return
    resolution=len(canvas);reach=min(resolution*2,math.hypot(rx,ry))
    x0=max(0,int(x-reach));x1=min(resolution,int(x+reach+1));y0=max(0,int(y-reach));y1=min(resolution,int(y+reach+1))
    if x1<=x0 or y1<=y0:return
    yy,xx=np.mgrid[y0:y1,x0:x1];c=math.cos(angle);s=math.sin(angle)
    dx=((xx-x)*c+(yy-y)*s)/rx;dy=(-(xx-x)*s+(yy-y)*c)/ry
    inside=(abs(dx)<=1)&(abs(dy)<=1)
    surface=texture if isinstance(texture,dict) else {'image':texture}
    texture=surface.get('image')
    u=(dx+1)*.5;v=(dy+1)*.5
    if surface.get('uv_map') is not None:
        geometry=sample_texture(surface['uv_map'],u,v)
        u,v=geometry[:,:,0],geometry[:,:,1]
        inside=inside*geometry[:,:,2]
    if 'shield' in (material or {}).get('shader','').lower():
        r2=dx*dx+dy*dy;z=np.sqrt(np.maximum(0,1-r2))
        su=.5+np.arctan2(dx,z)/(2*math.pi);sv=.5+np.arcsin(np.clip(dy,-1,1))/math.pi
        props=parameters(material);edge=(1-z)**max(.1,props.get('_fresnel_alpha_power',1))
        mask=(.15+.7*edge)*(r2<1)
        if surface.get('_alpha') is not None:mask*=sample_texture(surface['_alpha'],su,sv)[:,:,0]
        detail=np.ones_like(z)
        if surface.get('_energy_normals') is not None:
            normals=sample_texture(surface['_energy_normals'],su,sv)
            detail=.5+normals[:,:,1]*.5
        rgb=np.asarray(color)*detail[:,:,None]*(.35+edge[:,:,None]*2)
    elif texture is None:
        mask=np.maximum(0,1-dx*dx-dy*dy)**3*inside;rgb=np.asarray(color)
    else:
        sampled=sample_texture(texture,uv[0]+u*uv[2],uv[1]+v*uv[3])
        mask=sampled[:,:,3]*inside;rgb=sampled[:,:,:3]
        composite='composite' in (material or {}).get('shader','').lower()
        if composite:
            # Composite textures pack flame and diffuse channels rather than
            # ordinary RGB. This approximation retains their actual silhouette.
            props=parameters(material);flame=rgb[:,:,2:3]*props.get('_flameboost',2)
            colors={c['key'].lower():c.get('value',{}) for c in material.get('colors',[])}
            diffuse=np.array([colors.get('_diffusecolor',{}).get(k,0) for k in 'rgb'])
            rgb=flame*np.asarray(color)+rgb[:,:,1:2]*diffuse
        else:
            # DDS color slots are normally display-encoded; blend in linear light.
            rgb=np.where(rgb<=.04045,rgb/12.92,((rgb+.055)/1.055)**2.4)*np.asarray(color)
        if surface.get('mask') is not None:
            props=parameters(material or {});offset=surface.get('mask_slot',{}).get('tilingOffset',{})
            mu=np.mod(u*offset.get('x',1)+offset.get('z',0),1);mv=np.mod(v*offset.get('y',1)+offset.get('w',0),1)
            masked=sample_texture(surface['mask'],mu,mv)[:,:,0]**max(.01,props.get('_mask_power',1))
            contribution=props.get('_mask_diffuse_contribution',1)
            rgb=rgb*(1-contribution+contribution*masked[:,:,None])
            contribution=props.get('_mask_alpha_contribution',0)
            mask*=1-contribution+contribution*masked
    mask=np.clip(mask*alpha,0,1)
    view=canvas[y0:y1,x0:x1]
    if additive:view+=rgb*mask[:,:,None]
    else:view[:]=view*(1-mask[:,:,None])+rgb*mask[:,:,None]


def blur(image,radius):
    # Separable box blur without scipy or GPU requirements, with fixed edges.
    result=image
    for axis in (0,1):
        pads=[(0,0)]*3;pads[axis]=(radius,radius)
        padded=np.pad(result,pads,mode='edge')
        zeros=list(padded.shape);zeros[axis]=1
        integral=np.concatenate((np.zeros(zeros,dtype=result.dtype),np.cumsum(padded,axis=axis)),axis=axis)
        hi=[slice(None)]*3;lo=hi.copy();hi[axis]=slice(2*radius+1,None);lo[axis]=slice(None,-(2*radius+1))
        result=(integral[tuple(hi)]-integral[tuple(lo)])/(2*radius+1)
    return result


def texture_uv(system,material,age,seed):
    sheet=system.get('textureSheetAnimation') or {}
    if sheet.get('enabled'):
        cols=max(1,min(32,int(sheet.get('numTilesX',1))));rows=max(1,min(32,int(sheet.get('numTilesY',1))))
        phase=curve(sheet.get('frameOverTime'),age,seed,age)*sheet.get('cycleCount',1)
    else:
        props=parameters(material)
        cols=max(1,min(32,int(props.get('_x',1))));rows=max(1,min(32,int(props.get('_y',1))))
        custom=system.get('customData') or {}
        phase=curve(custom.get('vector0x'),age,seed,age) if custom.get('enabled') else age
    index=min(cols*rows-1,max(0,int(phase*cols*rows)))
    return ((index%cols)/cols,(index//cols)/rows,1/cols,1/rows)


def emission_rate(main,emitter_speed):
    """Particles/s = native particles/s + native particles/unit * demo units/s."""
    return max(0,curve(main.get('emission_rateOverTime'),default=0))+max(0,curve(main.get('emission_rateOverDistance'),default=0))*max(0,emitter_speed)


def render(renderer,row,resolution,frame_count):
    from pathlib import Path
    from .studio_core import Blueprint
    from .vfx_workflow import EXTENSIONS
    from .preview_diagnostics import resolve_path,enrich
    projectile=row['kind']=='projectile';paths=[row['path']];impacts=[]
    if projectile:
        doc=Blueprint(renderer.overrides.get('@projectile') or Path(row['file']).read_text(encoding='utf-8-sig'),'ProjectileTemplate')
        refs=[(p,doc.scalar(p)) for p,_ in doc.leaves() if isinstance(doc.scalar(p),str) and doc.scalar(p).lower().endswith(EXTENSIONS)]
        paths=[v for p,v in refs if 'persistantEffects' in p] or [v for _,v in refs[:1]]
        impacts=[v for p,v in refs if 'onImpactEffects' in p]
    layers=[]
    omitted=[]
    disabled=enabled=0
    for original_path in paths+impacts:
        path,resolution_notice=resolve_path(renderer.assets,original_path)
        impact_path=original_path in impacts
        if resolution_notice:omitted.append(resolution_notice)
        if path.lower() not in renderer.assets.records:
            omitted.append('Missing reference: '+original_path);continue
        data=renderer.data(path)
        systems=data.get('particleSystems') or []
        if not systems:
            refs=[m.get('material','') for m in data.get('subMesh',[])] if path.endswith('.sanbeam') else [path if path.endswith('.sanmaterial') else data.get('material','')]
            for ref in refs:
                material,texture=renderer.material(ref)
                texture=renderer.surface(material,texture)
                layers.append((path,data,{},material,texture,impact_path))
        for system in systems[:32]:
            rendering=system.get('rendering') or {}
            if rendering.get('enabled') is False:
                disabled+=1;continue
            enabled+=1
            ref=rendering.get('material') or rendering.get('trailMaterial','')
            material,texture=renderer.material(ref)
            shader=material.get('shader','').lower()
            if 'solid_frame' in shader:
                omitted.append('Solid-frame shader layer: '+ref)
                continue
            if 'distortion' in shader:
                omitted.append('Screen-space distortion: '+ref)
                continue
            comp_slot=next((t for t in material.get('textures',[]) if t.get('key','').lower()=='_comptexture'),{})
            noise_texture='/noise/' in comp_slot.get('path','').lower()
            if 'composite' in shader and noise_texture:
                # Opaque packed noise relies on mesh-specific Shader Graph masks,
                # dissolve/custom streams and distortion. Showing it as a sprite
                # invents solid squares. Keep the other source-backed layers and
                # disclose this unsupported layer instead.
                omitted.append('Mesh/noise shader layer: '+ref)
                continue
            if 'procedural flare' in shader:
                # Its texture is a shader lookup profile, not a billboard image.
                texture=None
                omitted.append('Procedural flare profile approximated: '+ref)
            # Quad particle meshes are billboards; a side profile would collapse
            # them. Actual solid projectile meshes retain the existing rasterizer.
            mesh=next((m for m in rendering.get('meshes',[]) if m.lower() in renderer.assets.records),'')
            try:texture=renderer.surface(material,texture,mesh if rendering.get('renderMode')==4 else '')
            except (ValueError,KeyError,IndexError) as exc:renderer.warnings.append(str(exc))
            layers.append((path,data,system,material,texture,impact_path))
    lifetimes=[curve((s.get('main') or {}).get('startLifetime'),default=1) for _,_,s,_,_,_ in layers if s]
    seconds=max(.25,min(6,max(lifetimes or [2])))
    moving=not projectile and any(curve((s.get('main') or {}).get('emission_rateOverDistance'),default=0)>0 for _,_,s,_,_,_ in layers)
    # Source rateOverDistance is retained; movement speed is explicitly a demo
    # control, never guessed from absent game locomotion/launch data.
    demo_speed=max(0,min(100,float(row.get('preview_emitter_speed',1)))) if moving else (6/seconds if projectile else 0)
    span=seconds*demo_speed;emitter_moves=projectile or moving
    # Commands retain native units until the complete clip has been framed.
    commands=[[] for _ in range(frame_count)];rng=np.random.default_rng(0)
    particle_budget=max(4,min(64,256//max(1,len(layers))))
    for path,data,system,material,texture,impact in layers:
        color=tint(material);props=parameters(material);additive=props.get('_dstblend',1)==1
        if 'procedural flare' in material.get('shader','').lower():color*=max(1,props.get('_focus',1))
        main=system.get('main') or {};shape=system.get('shape') or {}
        life=max(.02,curve(main.get('startLifetime'),default=seconds))
        delay=max(0,curve(main.get('startDelay'),default=0))
        rate=emission_rate(main,demo_speed if emitter_moves else 0)
        births=[]
        if rate:
            begin=-min(life,seconds) if main.get('looping') or main.get('prewarm') else delay
            all_births=np.arange(begin,seconds,1/max(rate,1e-9))
            births=list(all_births[np.linspace(0,len(all_births)-1,min(256,len(all_births))).astype(int)]) if len(all_births) else []
        for burst in main.get('emission_bursts',[]):
            count=min(96,max(0,int(curve(burst.get('count'),default=burst.get('maxCount',1)))))
            for repeat in range(min(16,max(1,burst.get('cycleCount',1)))):
                births.extend([delay+burst.get('time',0)+repeat*burst.get('repeatInterval',0)]*count)
        if main.get('maxParticles',192)<=0:births=[]
        if not system:births=[0]
        if len(births)>particle_budget:
            births=np.asarray(births)[np.linspace(0,len(births)-1,particle_budget).astype(int)]
            renderer.warnings.append('Preview particle budget reduced: '+path)
        randoms=rng.random((len(births),5))
        transforms=data.get('transforms',[]);owner=system.get('ownerId',0)
        offset=transforms[owner].get('localPosition',{}) if 0<owner<len(transforms) else {}
        for frame in range(frame_count):
            time=(frame+.5)/frame_count*seconds
            if impact:time-=(seconds*.75)
            head=-span/2+span*(frame+.5)/frame_count if emitter_moves else 0
            drawn=0
            for n,birth in enumerate(births):
                seed=randoms[n];particle_life=max(.02,curve(main.get('startLifetime'),seed=seed[0],default=seconds))
                age=time-birth
                if system and (age<0 or age>=particle_life):continue
                if drawn>=min(particle_budget,max(0,int(main.get('maxParticles',particle_budget)))):continue
                drawn+=1
                normalized=min(1,max(0,age/particle_life));start=gradient(main.get('startColor',{}),seed=seed[1])
                lifetime=system.get('colorOverLifetime') or {}
                if lifetime.get('enabled'):start*=gradient(lifetime.get('color',{}),normalized,seed[1])
                alpha=start[3]*props.get('_opacity',1)
                size=abs(curve(main.get('startSize'),seed=seed[2],default=2))
                dimensions=[size]*3
                if main.get('startSize3D'):
                    dimensions=[abs(curve(main.get('startSize'+axis),seed=seed[2],default=size)) for axis in 'XYZ']
                scaling=system.get('sizeOverLifetime') or {}
                if scaling.get('enabled'):
                    dimensions=[value*abs(curve(scaling.get(axis) if scaling.get('separateAxes') else scaling.get('size'),normalized,seed[2])) for value,axis in zip(dimensions,'xyz')]
                axes=texture.get('axes',(0,1)) if isinstance(texture,dict) else (0,1)
                sx,sy=dimensions[axes[0]],dimensions[axes[1]]
                angle=seed[3]*math.tau;radius=max(0,shape.get('radius',0)) if shape.get('enabled') else 0
                speed=curve(main.get('startSpeed'),seed=seed[0],default=0)
                distance=radius+speed*age
                x=math.cos(angle)*distance if shape.get('enabled') else 0
                y=math.sin(angle)*distance*.6 if shape.get('enabled') else -speed*age
                if shape.get('enabled') and shape.get('shapeType') in (4,7,8,9):
                    cone=math.tan(math.radians(min(80,abs(shape.get('angle',0)))))
                    x=(seed[3]-.5)*2*(radius+abs(speed*age)*cone);y=-speed*age
                if shape.get('enabled') and shape.get('shapeType') in (5,15,16,18):
                    x=(seed[3]-.5)*shape.get('scale',{}).get('x',1)
                    y=(seed[4]-.5)*shape.get('scale',{}).get('y',1)-speed*age
                velocity=system.get('velocityOverLifetime') or {}
                if velocity.get('enabled'):
                    x+=curve(velocity.get('x'),normalized,seed[0],0)*age;y-=curve(velocity.get('y'),normalized,seed[0],0)*age
                y+=4.905*curve(main.get('gravityModifier'),default=0)*age*age
                x+=offset.get('x',0);y-=offset.get('y',0)+offset.get('z',0)*.45
                if emitter_moves:
                    attached=bool(system.get('rendering',{}).get('meshes')) or main.get('simulationSpace',1)==0
                    if moving:attached=main.get('simulationSpace',1)==0
                    x+=head if attached else -span/2+demo_speed*max(0,birth)
                rotation=curve(main.get('startRotation'),seed=seed[3],default=0)
                rotate=system.get('rotationOverLifetime') or {}
                if rotate.get('enabled'):rotation+=curve(rotate.get('z'),normalized,seed[3],0)*age
                rendering=system.get('rendering') or {}
                if rendering.get('renderMode')==1:
                    sx*=max(1,rendering.get('lengthScale',1)+abs(speed)*rendering.get('velocityScale',0));rotation=angle
                if path.endswith('.sanbeam'):sx=6;sy=.12
                uv=texture_uv(system,material,normalized,seed[4])
                if rendering.get('renderMode')!=5:
                    commands[frame].append((x,y,max(.005,sx/2),max(.005,sy/2),color*start[:3],texture,alpha,rotation,additive,uv,material))
                trails=system.get('trails') or {}
                if trails.get('enabled'):
                    trail_material,trail_texture=renderer.material(rendering.get('trailMaterial',''))
                    trail_life=gradient(trails.get('colorOverLifetime',{}),normalized,seed[1])
                    tc=tint(trail_material)*start[:3]*trail_life[:3]
                    width=size*.5*max(0,curve(trails.get('widthOverTrail'),normalized,seed[2],1))
                    if width<=0:
                        omitted.append('Zero exported trail width (runtime override unverified): '+path)
                        continue
                    for j in range(1,9):
                        f=j/9;trail_color=gradient(trails.get('colorOverTrail',{}),1-f)
                        tx=x-f*(2 if projectile else max(.1,abs(speed)*min(age,.5)))
                        commands[frame].append((tx,y,width*3,width,tc*trail_color[:3],trail_texture,alpha*(1-f)*trail_color[3]*trail_life[3],0,True,(0,0,1,1),trail_material))
    visible=[c for frame in commands for c in frame if c[6]>.01]
    bounds=[(-4,4,-2,2)] if projectile else []
    bounds.extend((c[0]-math.hypot(c[2],c[3]),c[0]+math.hypot(c[2],c[3]),c[1]-math.hypot(c[2],c[3]),c[1]+math.hypot(c[2],c[3])) for c in visible)
    if bounds:
        xmin=min(b[0] for b in bounds);xmax=max(b[1] for b in bounds);ymin=min(b[2] for b in bounds);ymax=max(b[3] for b in bounds)
    else:xmin=ymin=-1;xmax=ymax=1
    scale=resolution*.8/max(xmax-xmin,ymax-ymin,.05);cx=(xmin+xmax)/2;cy=(ymin+ymax)/2
    frames=[]
    for frame in commands:
        canvas=np.zeros((resolution,resolution,3),dtype=np.float32);canvas[:]=(.006,.009,.014)
        for x,y,rx,ry,color,texture,alpha,angle,additive,uv,material in frame:
            sprite(canvas,resolution/2+(x-cx)*scale,resolution/2+(y-cy)*scale,rx*scale,ry*scale,color,texture,alpha,angle,additive,uv,material)
        # Bloom occurs before tone mapping; keeping HDR avoids flattening every
        # emissive material into the same dim tint.
        bright=np.maximum(canvas-1,0)
        canvas+=blur(bright,max(1,resolution//64))*.22+blur(bright,max(2,resolution//24))*.12
        linear=canvas/(1+canvas);srgb=np.where(linear<=.0031308,linear*12.92,1.055*linear**(1/2.4)-.055)
        pixels=np.empty((resolution,resolution,4),dtype=np.uint8);pixels[:,:,:3]=np.clip(srgb*255,0,255);pixels[:,:,3]=255
        frames.append(pixels)
    renderer.last_info={'mode':'Partial particle approximation' if omitted else 'Particle approximation',
                        'limitations':sorted(set(omitted)), 'seconds':seconds,
                        'resolution':resolution,'frames':frame_count}
    if not any(commands):renderer.last_info['mode']='No supported visible emission'
    if not any(commands) and disabled and not enabled:
        omitted.append('Source renderer disabled: '+row['path'])
    elif not any(commands) and not omitted:
        omitted.append('No emitted particles in sampled clip: '+row['path'])
    if layers and not any(s for _,_,s,_,_,_ in layers):renderer.last_info['mode']='Shield surface swatch (no hit response)' if any('shield' in m.get('shader','').lower() for _,_,_,m,_,_ in layers) else 'Source material / beam swatch'
    if moving:renderer.last_info['motion']={'kind':'moving_emitter_demo','speed_units_per_second':demo_speed,'distance_units':span,'not_game_speed':True}
    renderer.last_info=enrich(renderer.last_info,omitted)
    renderer.warnings.extend(omitted)
    return np.asarray(frames)
