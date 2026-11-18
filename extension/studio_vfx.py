"""Visual effect browser and explicit unit/projectile slot assignments."""
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup, UIList
from . import studio as st, mod_projects as mp
from .vfx_workflow import effect_slots, apply_slot, adjust_particles, EXTENSIONS
from .vfx_documents import display_name, scale_colors, encode_asset

_JOBS = {}
_ANIMATION = None
_SELECTION = None
_POPULATING = False
_ROWS_PENDING = None
_INDEX_IDENTITY = None

def redraw_browser():
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'VIEW_3D','PROPERTIES'}:area.tag_redraw()

def begin_rows(context,rows):
    global _ROWS_PENDING,_POPULATING,_INDEX_IDENTITY
    _POPULATING=True
    state=st.settings(context);state.effects.clear()
    _ROWS_PENDING=(context.scene,iter(rows),len(rows))
    _INDEX_IDENTITY=None
    if not bpy.app.timers.is_registered(populate_rows):bpy.app.timers.register(populate_rows,first_interval=.01)

def populate_rows():
    global _ROWS_PENDING,_POPULATING,_INDEX_IDENTITY
    if _ROWS_PENDING is None:return None
    scene,rows,total=_ROWS_PENDING
    if scene!=bpy.context.scene:
        _ROWS_PENDING=None;_POPULATING=False;return None
    state=st.settings(bpy.context)
    for _ in range(128):
        row=next(rows,None)
        if row is None:
            _ROWS_PENDING=None;_POPULATING=False
            _INDEX_IDENTITY=(str(st.engine(bpy.context)),str(mp.active_project(bpy.context,required=False)))
            if state.effects:
                state.effect_index=0
                select_asset(state,bpy.context)
            state.status=f'{total} effects available.';redraw_browser();return None
        item=state.effects.add();item.name=row['path'];item.value=json.dumps(row)
    redraw_browser();return .01

def ensure_index(context,refresh=False):
    identity=(str(st.engine(context)),str(mp.active_project(context,required=False)))
    if not refresh and _INDEX_IDENTITY==identity and st.settings(context).effects:
        if st.settings(context).effect_asset:play_preview(context)
        return
    from . import vfx_browser_jobs as jobs
    preview_root(context).mkdir(parents=True,exist_ok=True)
    jobs.submit(context,'INDEX',refresh=refresh)
    st.settings(context).status='Loading effect catalogue in the background…'

def close_browser():
    global _SELECTION,_ROWS_PENDING,_POPULATING
    _SELECTION=None;_ROWS_PENDING=None;_POPULATING=False
    stop_animation()
    from . import vfx_browser_jobs as jobs
    jobs.close()
    for timer in (flush_selection,populate_rows,animate_preview):
        if bpy.app.timers.is_registered(timer):bpy.app.timers.unregister(timer)


def preview_root(context):
    from .utils import cache_root
    return cache_root() / 'vfx-previews'


def included_gallery(context):
    """Bundled stock GIFs only; never contains a user's project or local paths."""
    import zipfile
    from .studio_core import safe_member
    from .vfx_catalogue import VERSION
    root=preview_root(context)/('included-stock-v'+str(VERSION))
    if not (root/'gallery.html').is_file():
        with zipfile.ZipFile(Path(__file__).with_name('preview_library.zip')) as archive:
            for entry in archive.infolist():
                if entry.is_dir():continue
                name=safe_member(entry.filename)
                if '/' in name or not name.endswith(('.gif','.png','.json','.html')):
                    raise ValueError('Invalid bundled preview-library member: '+name)
                root.mkdir(parents=True,exist_ok=True)
                destination=root/name
                if not destination.exists():destination.write_bytes(archive.read(entry))
    return root/'gallery.html'


def selected_row(context):
    state=st.settings(context)
    v=context.scene.sanctuary_vfx
    if state.effect_asset.startswith('projectile:') and v.projectile_document:
        return {'path':state.effect_asset,'id':v.projectile_id,'file':v.projectile_path,
                'name':v.projectile_id,'kind':'projectile','category':'projectiles','search':v.projectile_id}
    if not state.effects:
        return {'path':state.effect_asset,'name':display_name(state.effect_asset),'kind':'effect','category':'other','search':state.effect_asset}
    item=state.effects[state.effect_index]
    if item.name!=state.effect_asset:
        return {'path':state.effect_asset,'name':display_name(state.effect_asset),'kind':'effect','category':'other','search':state.effect_asset}
    if item.value:
        try:return json.loads(item.value)
        except ValueError:pass
    return {'path':item.name,'name':display_name(item.name),'kind':'effect','category':'other','search':item.name}


def stop_animation():
    global _ANIMATION,_SELECTION
    # Stop also cancels a click still waiting in the selection debounce timer.
    _SELECTION=None
    if bpy.app.timers.is_registered(flush_selection):bpy.app.timers.unregister(flush_selection)
    _ANIMATION=None
    from .vfx_browser_jobs import cancel_preview
    cancel_preview()
    if bpy.app.timers.is_registered(animate_preview):bpy.app.timers.unregister(animate_preview)


def play_preview(context):
    from . import vfx_browser_jobs as jobs
    stop_animation()
    row=selected_row(context); state=st.settings(context); v=context.scene.sanctuary_vfx
    row['preview_emitter_speed']=v.preview_emitter_speed
    overrides={'@projectile':v.projectile_document.as_string()} if row['kind']=='projectile' else {state.effect_asset.lower():edited_data(context)}
    preview_root(context).mkdir(parents=True,exist_ok=True)
    jobs.submit(context,'PLAY',row=row,overrides=overrides)
    state.status='Preparing animated preview…'

def show_pixels(context,rgba,info):
    import numpy as np
    image=bpy.data.images.get('Studio Browser VFX Preview')
    if image is None or not image.get('san_browser_preview'):
        image=bpy.data.images.new('Studio Browser VFX Preview',width=rgba.shape[1],height=rgba.shape[0],alpha=True)
        image['san_browser_preview']=True
    if tuple(image.size)!=tuple((rgba.shape[1],rgba.shape[0])):image.scale(rgba.shape[1],rgba.shape[0])
    pixels=(rgba[::-1].astype('float32')/255).ravel()
    image.pixels.foreach_set(pixels);image.update();image.preview_ensure()
    image.preview.image_size=(rgba.shape[1],rgba.shape[0]);image.preview.image_pixels_float=pixels
    context.scene.sanctuary_vfx.thumbnail=image;set_preview_messages(context.scene.sanctuary_vfx,info)
    return image

def start_animation(context,frames,info):
    global _ANIMATION
    set_preview_messages(context.scene.sanctuary_vfx,info)
    image=bpy.data.images.get('Studio Animated VFX Preview')
    if image is None:image=bpy.data.images.new('Studio Animated VFX Preview',width=frames.shape[2],height=frames.shape[1],alpha=True)
    if tuple(image.size)!=(frames.shape[2],frames.shape[1]):image.scale(frames.shape[2],frames.shape[1])
    context.scene.sanctuary_vfx.thumbnail=image
    fps=min(100,len(frames)/max(.01,info['seconds']))
    _ANIMATION=[context.scene,frames,image,time.monotonic(),fps,-1]
    # Publish the first frame now, without depending on a future timer tick.
    animate_preview()
    if _ANIMATION is None:raise RuntimeError(st.settings(context).status)
    st.settings(context).status='Animated preview ready.'
    if not bpy.app.timers.is_registered(animate_preview):bpy.app.timers.register(animate_preview,first_interval=.01)


def animate_preview():
    if _ANIMATION is None:return None
    scene,frames,image,started,fps,last_index=_ANIMATION
    try:
        from .vfx_browser_jobs import scene_exists
        if not scene_exists(scene) or bpy.context.scene != scene:
            stop_animation()
            if scene_exists(scene):scene.sanctuary_studio.status='Preview paused after scene changed. Press Play to resume.'
            return None
        index=int((time.monotonic()-started)*fps)%len(frames)
        if index==last_index:return 1/fps
        _ANIMATION[5]=index
        pixels=(frames[index][::-1].astype('float32')/255).ravel()
        image.pixels.foreach_set(pixels);image.update();image.preview_ensure()
        # Refresh the icon pixels as well as the underlying Image datablock.
        image.preview.image_size=(frames.shape[2],frames.shape[1])
        image.preview.image_pixels_float=pixels
        redraw_browser()
        return 1/fps
    except (ReferenceError,RuntimeError,ValueError) as exc:
        stop_animation()
        if scene_exists(scene):scene.sanctuary_studio.status='Animation display failed: '+str(exc)+'. Press Play to retry.'
        from .vfx_browser_jobs import _JOBS,record
        if 'preview' in _JOBS:record(_JOBS['preview'],'animation_display_failure',error=repr(exc))
        return None


def fields_selected(context):
    state=st.settings(context);v=context.scene.sanctuary_vfx
    if not state.effect_fields:return
    value=json.loads(state.effect_fields[state.effect_field_index].value)
    v.field_kind='BOOL' if isinstance(value,bool) else 'NUMBER' if isinstance(value,(int,float)) else 'STRING' if isinstance(value,str) else 'RAW'
    if v.field_kind=='BOOL':v.field_bool=value
    elif v.field_kind=='NUMBER':v.field_number=value
    elif v.field_kind=='STRING':v.field_string=value


def color_selected(self,context):
    if context is None:return
    state=st.settings(context)
    if not state.effect_document:return
    colors=json.loads(state.effect_document.as_string()).get('colors',[])
    if 0<=self.color_index<len(colors):
        value=colors[self.color_index].get('value',{})
        self.material_color=tuple(value.get(k,1) for k in 'rgba')


def projectile_field_selected(self,context):
    if not self.projectile_fields:return
    self.projectile_value=self.projectile_fields[self.projectile_field_index].value


def rebuild_projectile(v):
    from .studio_core import Blueprint
    v.projectile_fields.clear()
    v.projectile_effects.clear()
    for path,raw in Blueprint(v.projectile_document.as_string(),'ProjectileTemplate').leaves():
        item=v.projectile_fields.add();item.name='.'.join(map(str,path));item.path=json.dumps(path);item.value=raw
        value=Blueprint(v.projectile_document.as_string(),'ProjectileTemplate').scalar(path)
        if isinstance(value,str) and value.lower().endswith(EXTENSIONS):
            item=v.projectile_effects.add();item.name='.'.join(map(str,path));item.path=json.dumps(path);item.value=value
    v.projectile_field_index=min(v.projectile_field_index,max(0,len(v.projectile_fields)-1))
    projectile_field_selected(v,None)
    doc=Blueprint(v.projectile_document.as_string(),'ProjectileTemplate')
    v.projectile_speed=float(doc.scalar(('movement','speedMax'),0))
    v.projectile_acceleration=float(doc.scalar(('movement','acceleration'),0))
    v.projectile_turn=float(doc.scalar(('movement','rotationSpeed'),0))
    v.projectile_homing=doc.scalar(('movement','type')) not in (None,False)


def current_asset(context):
    state = st.settings(context)
    if not state.effects or not 0 <= state.effect_index < len(state.effects):
        raise ValueError('Select an effect first.')
    return state.effects[state.effect_index].name


def select_asset(self, context):
    global _SELECTION
    if context is None or _POPULATING:return
    stop_animation()
    _SELECTION=(context.scene,current_asset(context),time.monotonic())
    if not bpy.app.timers.is_registered(flush_selection):bpy.app.timers.register(flush_selection,first_interval=.12)

def flush_selection():
    global _SELECTION
    if _SELECTION is None:return None
    scene,path,started=_SELECTION
    if time.monotonic()-started<.12:return .04
    _SELECTION=None
    if scene!=bpy.context.scene:return None
    try:
        load_asset(bpy.context,path)
    except (ValueError, KeyError, OSError) as exc:
        st.settings(bpy.context).status = str(exc)
    return None


def load_asset(context, path):
    state = st.settings(context)
    v = context.scene.sanctuary_vfx
    stop_animation()
    if path.startswith('projectile:'):
        uid=path.split(':',1)[1]
        from .studio_core import Blueprint
        roots=(mp.active_project(context,required=False),st.engine(context))
        candidates=[Path(root)/('Lua' if i==0 else 'LJ/lua')/'common/projectiles/projectilesTemplates'/uid/(uid+'.santp') for i,root in enumerate(roots) if root]
        file=next((f for f in candidates if f.is_file()),None)
        if file is None:
            from .vfx_catalogue import projectile_records
            record=projectile_records(st.engine(context),roots[0])[uid]
        else:
            text=file.read_text(encoding='utf-8-sig');record={'id':Blueprint(text,'ProjectileTemplate').scalar(('general','tpId')),'file':str(file),'text':text}
        if not v.projectile_document or v.projectile_document.as_string()!=v.projectile_baseline:
            if v.projectile_document:v.projectile_document.use_fake_user=True
            v.projectile_document=bpy.data.texts.new(record['id']+'.santp draft');v.projectile_document.use_fake_user=True
        else:v.projectile_document.clear()
        v.projectile_document.write(record['text']);v.projectile_path=record['file'];v.projectile_baseline=record['text']
        v.projectile_id=record['id'];v.projectile_new_id=''
        state.effect_asset=path;v.thumbnail=None;rebuild_projectile(v)
        state.status='Projectile loaded. Clone into your project before editing or assigning.'
        refresh_thumbnail(context)
        return
    data = st.library(context).json(path)
    state.effect_asset = path
    if not state.effect_document or state.effect_document.as_string() != state.effect_baseline:
        state.effect_document = bpy.data.texts.new('Studio Effect Draft')
        state.effect_document.use_fake_user = True
    st.write_effect_text(context, data)
    state.effect_baseline = json.dumps(data, indent=2)
    v.size = v.speed = v.lifetime = v.intensity = v.opacity = v.emission = 1
    v.use_tint = False
    v.thumbnail = None
    v.materials.clear();v.textures.clear();v.colors.clear()
    for index,color in enumerate(data.get('colors',[])):
        item=v.colors.add();item.name=color.get('key','Colour');item.path=str(index)
    v.color_index=0;color_selected(v,context)
    for index,texture in enumerate(data.get('textures',[])):
        item=v.textures.add();item.name=texture.get('key','Texture');item.path=str(index);item.value=texture.get('path','')
    refs=set()
    from .studio_core import json_leaves
    for _,value in json_leaves(data):
        if isinstance(value,str) and value.lower().endswith('.sanmaterial'):refs.add(value)
    for ref in sorted(refs):
        item=v.materials.add();item.name=display_name(ref);item.value=ref
    if state.effect_fields:fields_selected(context)
    refresh_thumbnail(context)


def refresh_thumbnail(context):
    """Selection automatically plays the full-quality preview off the UI thread."""
    play_preview(context)


def set_preview_messages(v,info):
    from .preview_diagnostics import messages
    v.preview_note=info['mode']+(' - moving emitter demo' if info.get('motion') else '')
    v.preview_details='\n'.join(messages(info))
    v.preview_is_moving=bool(info.get('motion'))


class VFXState(PropertyGroup):
    slots: CollectionProperty(type=st.StudioField)
    slot_index: IntProperty()
    category: EnumProperty(name='Category', items=[('ALL','All',''),('muzzle','Muzzle',''),('trails','Trails',''),('impact','Impact',''),('death','Death',''),('unit','Unit',''),('materials','Materials',''),('beams','Beams',''),('decals','Decals',''),('projectiles','Projectiles',''),('other','Other','')])
    size: FloatProperty(name='Size', default=1, min=0.01, max=10)
    speed: FloatProperty(name='Speed', default=1, min=0, max=10)
    lifetime: FloatProperty(name='Lifetime', default=1, min=0.01, max=10)
    use_tint: BoolProperty(name='Override colour')
    intensity: FloatProperty(name='Intensity',default=1,min=0,max=100)
    opacity: FloatProperty(name='Opacity',default=1,min=0,max=10)
    emission: FloatProperty(name='Emission density',default=1,min=0,max=100)
    thumbnail: PointerProperty(type=bpy.types.Image)
    preview_note: StringProperty()
    preview_details: StringProperty()
    preview_is_moving: BoolProperty()
    show_preview_details: BoolProperty(name='Preview limitations / next steps')
    preview_emitter_speed: FloatProperty(name='Demo emitter speed',description='Preview-only movement in source units per second; not game speed or exported unit settings',default=1,min=0,max=100)
    show_properties: BoolProperty(name='All effect properties')
    selection_scope: StringProperty(default='All game and project effects')
    materials: CollectionProperty(type=st.StudioField)
    material_index: IntProperty()
    textures: CollectionProperty(type=st.StudioField)
    texture_index: IntProperty()
    colors: CollectionProperty(type=st.StudioField)
    color_index: IntProperty(update=color_selected)
    material_color: FloatVectorProperty(name='HDR colour (RGBA)',size=4,subtype='COLOR',min=0,max=100000,default=(1,1,1,1))
    field_kind: StringProperty(default='RAW')
    field_number: FloatProperty(name='Value',precision=6)
    field_bool: BoolProperty(name='Enabled')
    field_string: StringProperty(name='Value')
    texture_kind: EnumProperty(name='Texture data',items=[('AUTO','Detect from shader slot',''),('NORMAL','Normal map',''),('COLOR','Colour / emission',''),('DATA','Linear mask / distortion','')],default='AUTO')
    normal_encoding: EnumProperty(name='Normal encoding',items=[('RGB','RGB tangent-space',''),('DXT5NM','DXT5nm (X in alpha, Y in green)','')],default='RGB')
    projectile_document: PointerProperty(type=bpy.types.Text)
    projectile_path: StringProperty()
    projectile_baseline: StringProperty()
    projectile_id: StringProperty()
    projectile_new_id: StringProperty(name='New projectile ID',description='Project-owned identifier, for example pd_my_cannon')
    projectile_fields: CollectionProperty(type=st.StudioField)
    projectile_field_index: IntProperty(update=projectile_field_selected)
    projectile_value: StringProperty(name='Lua value')
    projectile_search: StringProperty(name='Find projectile setting',options={'TEXTEDIT_UPDATE'})
    projectile_effects: CollectionProperty(type=st.StudioField)
    projectile_effect_index: IntProperty()
    projectile_speed: FloatProperty(name='Maximum speed',min=0)
    projectile_acceleration: FloatProperty(name='Acceleration',min=0)
    projectile_turn: FloatProperty(name='Turn speed',min=0)
    projectile_homing: BoolProperty(name='Homing movement')
    weapon_index: IntProperty(name='Target weapon',default=1,min=1)
    show_projectile_fields: BoolProperty(name='Projectile settings',default=True)
    gif_path: StringProperty()
    gallery_status: StringProperty()


def refresh_slots(context):
    v = context.scene.sanctuary_vfx
    v.slots.clear()
    if not st.settings(context).blueprint: return
    for slot in effect_slots(st.source(context), st.engine(context), mp.active_project(context, required=False)):
        item = v.slots.add(); item.name = slot['label']; item.path = json.dumps(slot); item.value = slot['asset']
    v.slot_index = min(v.slot_index, max(0, len(v.slots)-1))


class SANCTUARY_UL_effect_slots(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon='PARTICLES')


class SANCTUARY_OT_vfx_browser(Operator):
    bl_idname = 'sanctuary.vfx_browser'
    bl_label = 'Sanctuary Effect Browser'
    target_path: StringProperty(options={'HIDDEN'})

    def invoke(self, context, event):
        try:
            ensure_index(context)
            refresh_slots(context)
            v = context.scene.sanctuary_vfx
            if self.target_path:
                for i, item in enumerate(v.slots):
                    if '.'.join(map(str, json.loads(item.path)['path'])) == self.target_path:
                        v.slot_index = i; break
            return context.window_manager.invoke_props_dialog(self, width=1050)
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}

    def execute(self, context):close_browser();return {'FINISHED'}
    def cancel(self,context):close_browser()

    def draw(self, context):
        state = st.settings(context); v = context.scene.sanctuary_vfx
        from .studio_workshop import note
        split = self.layout.split(factor=0.48)
        left, right = split.column(), split.column()
        left.prop(state, 'effect_search', text='', icon='VIEWZOOM')
        left.prop(v, 'category')
        row = left.row(align=True)
        row.operator('sanctuary.vfx_action', text='All Effects', icon='FILE_REFRESH').action = 'INDEX'
        left.operator('sanctuary.vfx_action',text='Build All GIF Previews',icon='RENDER_ANIMATION').action='BUILD_GALLERY'
        left.operator('sanctuary.vfx_action',text='Open Animated GIF Gallery',icon='URL').action='GALLERY'
        if v.gallery_status:note(left,v.gallery_status)
        row.operator('sanctuary.studio_donor', text='Choose Source Unit…').apply_to_unit = False
        left.operator('sanctuary.vfx_action', text='Show Effects Used by Source Unit').action = 'DONOR'
        note(left, v.selection_scope)
        left.template_list('SANCTUARY_UL_studio_effects', 'browser', state, 'effects', state, 'effect_index', rows=13)
        if state.effect_asset.startswith('projectile:'):
            preview_controls(right,context)
            projectile_editor(right,context)
            return
        if not state.effect_document:
            note(right, 'Select an effect to inspect its texture and preview its animation.')
            return
        note(right, Path(state.effect_asset).stem.replace('_',' '))
        if v.thumbnail:
            v.thumbnail.preview_ensure()
            right.template_icon(icon_value=v.thumbnail.preview.icon_id, scale=12)
            note(right,v.preview_note or 'Particle approximation')
        preview_controls(right,context,show_image=False)
        right.operator('sanctuary.vfx_action', text='Preview in Blender', icon='PLAY').action = 'PREVIEW'
        note(right, 'Approximate particles and materials. Game shaders and simulation can differ.')
        editor(right, context)
        if v.slots:
            right.label(text='Replace a visual on this unit:')
            right.template_list('SANCTUARY_UL_effect_slots', 'target', v, 'slots', v, 'slot_index', rows=3)
            right.operator('sanctuary.vfx_action', text='Use Selected Effect', icon='CHECKMARK').action = 'APPLY'
        note(right, state.status)


def edited_data(context):
    state = st.settings(context); v = context.scene.sanctuary_vfx
    data = json.loads(state.effect_document.as_string())
    if (v.size,v.speed,v.lifetime,v.emission) != (1,1,1,1):
        if data.get('particleSystems'):
            data = adjust_particles(data, None, v.size,v.speed,v.lifetime,v.emission)
        else:
            raise ValueError('This asset does not support particle size/speed/lifetime controls.')
    if v.use_tint or v.intensity!=1 or v.opacity!=1:
        data=scale_colors(data,state.effect_color if v.use_tint else None,v.intensity,v.opacity)
    return data


def has_adjustments(v):
    return v.use_tint or (v.size,v.speed,v.lifetime,v.intensity,v.opacity,v.emission)!=(1,1,1,1,1,1)


def poll_gallery():
    running=False
    for key,(process,root,scene,log) in list(_JOBS.items()):
        try:
            progress=json.loads((root/'progress.json').read_text()) if (root/'progress.json').is_file() else {'done':0,'total':0}
            if scene.name in bpy.data.scenes:
                scene.sanctuary_vfx.gallery_status=f"GIF previews: {progress['done']} / {progress['total']}"
            code=process.poll()
            if code is not None:
                log.close();del _JOBS[key]
                if scene.name in bpy.data.scenes:
                    scene.sanctuary_vfx.gallery_status=((f"GIF gallery ready; {progress.get('errors',0)} preview failures. See catalogue.json." if progress.get('errors') else 'All GIF previews ready. Open the gallery.') if code==0 else 'Preview generation failed; see '+str(root/'worker.log'))
            else:running=True
        except (OSError,ValueError,ReferenceError):running=True
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:area.tag_redraw()
    return 1 if running else None


def save_projectile(context,clone=False):
    from .studio_core import Blueprint,lua_compile
    from .unit_icons import atomic
    v=context.scene.sanctuary_vfx;project=mp.active_project(context).resolve()
    text=v.projectile_document.as_string();path=Path(v.projectile_path).resolve()
    if clone:
        uid=v.projectile_new_id.strip().lower()
        if not re.fullmatch(r'p[a-z0-9_]+',uid):raise ValueError('Enter a new projectile ID starting with p.')
        # Hosted Dycom has a reviewed namespace boundary; make that visible now.
        from .project_release import legacy_dycom
        if legacy_dycom(project) and not uid.startswith('pd'):
            raise ValueError('Dycom Mod API projectiles must start with pd so both release formats include them.')
        path=project/'Lua/common/projectiles/projectilesTemplates'/uid/(uid+'.santp')
        if path.exists():raise FileExistsError('Projectile exists; open its project entry to edit it.')
        text=Blueprint(text,'ProjectileTemplate').set(('general','tpId'),json.dumps(uid))
    else:
        if project not in path.parents:raise ValueError('Clone this stock projectile into the project first.')
        if path.read_text(encoding='utf-8-sig')!=v.projectile_baseline:raise ValueError('Projectile changed on disk. Reload it; the draft remains in Texts.')
        backup=project/'.smt_history'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')/path.name
        backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,backup)
    lua_compile(text,st.engine(context))
    uid=Blueprint(text,'ProjectileTemplate').scalar(('general','tpId'))
    if uid!=path.stem:raise ValueError('Projectile tpId must match its filename.')
    atomic(path,text.encode('utf-8'))
    v.projectile_path=str(path);v.projectile_baseline=text;v.projectile_id=uid
    v.projectile_document.clear();v.projectile_document.write(text);rebuild_projectile(v)
    st.settings(context).effect_asset='projectile:'+uid
    return path


def replace_material_texture(context):
    from .dds_tools import _load_png_rgba,write_bc3_dds
    from .studio_core import json_set,safe_member
    state=st.settings(context);v=context.scene.sanctuary_vfx
    if not state.effect_asset.lower().endswith('.sanmaterial') or not v.textures:
        raise ValueError('Open a material and select its shader texture slot first.')
    project=mp.active_project(context).resolve()
    material=(project/safe_member(state.effect_asset)).resolve()
    if project not in material.parents:raise ValueError('Texture destination must stay within this project.')
    if not material.is_file():raise ValueError('Save a custom material copy before replacing textures.')
    source=Path(bpy.path.abspath(state.effect_texture)).resolve()
    if not source.is_file() or source.suffix.lower()!='.png':raise ValueError('Choose your own PNG texture.')
    data=json.loads(state.effect_document.as_string());index=int(v.textures[v.texture_index].path)
    slot=data['textures'][index];key=slot.get('key','').lower()
    normal=v.texture_kind=='NORMAL' or (v.texture_kind=='AUTO' and ('normal' in key or 'bump' in key))
    linear=normal or v.texture_kind=='DATA' or (v.texture_kind=='AUTO' and slot.get('linear',False))
    rgba,_=_load_png_rgba(str(source),int(state.effect_texture_size))
    if normal and v.normal_encoding=='DXT5NM':
        rgba=rgba.copy();rgba[:,:,3]=rgba[:,:,0];rgba[:,:,0]=255;rgba[:,:,2]=0
    suffix=re.sub(r'[^A-Za-z0-9_-]+','_',slot.get('key','texture')).strip('_') or 'texture'
    target=material.parent/(source.stem+'_'+suffix+'_studio.dds')
    if target.is_file():
        backup=project/'.smt_history'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')/target.name
        backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
    write_bc3_dds(target,rgba,kind='normal' if normal and v.normal_encoding=='RGB' else 'mask' if linear else 'albedo',full_mips=False)
    slot['path']=target.relative_to(project).as_posix();slot['linear']=linear
    st.write_effect_text(context,data);v.textures[v.texture_index].value=slot['path']
    st.library(context,True)
    state.status='Texture replaced in the draft. Save Custom Asset to commit the material. Normal maps use linear data.'


class SANCTUARY_OT_vfx_action(Operator):
    bl_idname = 'sanctuary.vfx_action'
    bl_label = 'Update Effect'
    action: StringProperty()

    def execute(self, context):
        try:
            state, v = st.settings(context), context.scene.sanctuary_vfx
            if self.action in ('INDEX', 'DONOR'):
                if self.action=='INDEX':
                    ensure_index(context,refresh=True)
                    return {'FINISHED'}
                assets = st.library(context)
                from .vfx_catalogue import catalogue
                rows=catalogue(assets)
                paths = [row['path'] for row in rows]
                v.selection_scope = 'All game and project effects'
                if self.action == 'DONOR':
                    donor = st.donor_document(context)
                    paths = sorted({s['asset'].lower() for s in effect_slots(donor.text, st.engine(context), mp.active_project(context, required=False))})
                    v.selection_scope = 'Used by ' + str(donor.scalar(('general','displayName'), state.donor))
                    state.effect_search = ''; v.category = 'ALL'
                state.effects.clear()
                lookup={row['path']:row for row in rows}
                for path in paths:
                    item = state.effects.add(); item.name = path
                    row=lookup.get(path)
                    if row:item.value=json.dumps(row)
                if paths:
                    state.effect_index = 0
                    load_asset(context, paths[0])
                state.status = f'{len(paths)} effects available.'
            elif self.action == 'SLOTS': refresh_slots(context)
            elif self.action == 'INSPECT_SLOT':
                item = v.slots[v.slot_index]
                load_asset(context, item.value)
            elif self.action=='PLAY_GIF':play_preview(context)
            elif self.action=='STOP_GIF':stop_animation();state.status='Preview stopped.'
            elif self.action=='GIF':
                from .vfx_catalogue import build_preview
                row=selected_row(context)
                row['preview_emitter_speed']=v.preview_emitter_speed
                overrides={'@projectile':v.projectile_document.as_string()} if row['kind']=='projectile' else {state.effect_asset.lower():edited_data(context)}
                gif,png=build_preview(st.library(context),row,preview_root(context),overrides)
                set_preview_messages(v,row['preview'])
                v.gif_path=str(gif);bpy.ops.wm.path_open(filepath=str(gif))
            elif self.action=='BUILD_GALLERY':
                from .vfx_catalogue import catalogue
                root=preview_root(context);root.mkdir(parents=True,exist_ok=True)
                if str(root) in _JOBS:raise ValueError('GIF generation is already running; its progress appears here.')
                rows=catalogue(st.library(context))
                job=root/'job.json';job.write_text(json.dumps({'engine':str(st.engine(context) or ''),'project':str(mp.active_project(context,required=False) or ''),'root':str(root),'rows':rows}),encoding='utf-8')
                log=(root/'worker.log').open('w',encoding='utf-8')
                options={}
                if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
                process=subprocess.Popen([bpy.app.binary_path,'--background','--factory-startup','--threads','2','--python',str(Path(__file__).with_name('vfx_preview_worker.py')),'--',str(job)],stdout=log,stderr=subprocess.STDOUT,**options)
                _JOBS[str(root)]=(process,root,context.scene,log)
                if not bpy.app.timers.is_registered(poll_gallery):bpy.app.timers.register(poll_gallery,first_interval=1)
                v.gallery_status=f'Generating {len(rows)} GIF previews in the background.'
                state.status=v.gallery_status
            elif self.action=='GALLERY':
                target=preview_root(context)/'gallery.html'
                if not target.is_file():raise ValueError('Build All GIF Previews first.')
                bpy.ops.wm.url_open(url=target.as_uri())
            elif self.action=='INCLUDED_GALLERY':
                bpy.ops.wm.url_open(url=included_gallery(context).as_uri())
            elif self.action=='MATERIAL':
                if not v.materials:raise ValueError('This effect has no material dependencies.')
                load_asset(context,v.materials[v.material_index].value)
            elif self.action=='COLOR':
                data=json.loads(state.effect_document.as_string())
                data['colors'][v.color_index]['value']=dict(zip('rgba',v.material_color))
                st.write_effect_text(context,data)
            elif self.action=='EFFECT_SOURCE':st.open_text_window(context,state.effect_document)
            elif self.action=='EFFECT_VALIDATE':
                data=json.loads(state.effect_document.as_string())
                if not isinstance(data,dict):raise ValueError('Effect document must be a JSON object.')
                st.write_effect_text(context,data);state.status='Effect draft parses correctly. All properties are available below.'
            elif self.action=='PROPERTY':
                from .studio_core import json_set
                item=state.effect_fields[state.effect_field_index]
                value=(v.field_bool if v.field_kind=='BOOL' else v.field_number if v.field_kind=='NUMBER'
                       else v.field_string if v.field_kind=='STRING' else json.loads(state.effect_value))
                if isinstance(json.loads(item.value),int) and not isinstance(json.loads(item.value),bool):
                    if v.field_number!=int(v.field_number):raise ValueError('This field needs an integer.')
                    value=int(v.field_number)
                st.write_effect_text(context,json_set(json.loads(state.effect_document.as_string()),json.loads(item.path),value))
            elif self.action=='TEXTURE':replace_material_texture(context)
            elif self.action=='SAVE_ASSET':
                from .unit_icons import atomic
                project=mp.active_project(context).resolve();target=(project/st.safe_member(state.effect_asset)).resolve()
                if project not in target.parents or not target.is_file():raise ValueError('Save as Custom Copy first.')
                disk=json.dumps(st.library(context).json(state.effect_asset),indent=2)
                if disk!=state.effect_baseline:raise ValueError('Asset changed on disk. Reload it first; your draft is preserved.')
                backup=project/'.smt_history'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')/target.name
                backup.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(target,backup)
                data=edited_data(context);atomic(target,encode_asset(data,state.effect_asset));load_asset(context,state.effect_asset)
                state.status='Custom asset saved with its original file format and a recoverable backup.'
            elif self.action=='PROJECTILE_CLONE':
                path=save_projectile(context,True);state.status='Private projectile saved: '+str(path)
            elif self.action=='PROJECTILE_SAVE':
                path=save_projectile(context);state.status='Projectile saved: '+str(path)
            elif self.action=='PROJECTILE_FIELD':
                from .studio_core import Blueprint,lua_compile
                item=v.projectile_fields[v.projectile_field_index]
                text=Blueprint(v.projectile_document.as_string(),'ProjectileTemplate').set(tuple(json.loads(item.path)),v.projectile_value)
                lua_compile(text,st.engine(context));v.projectile_document.clear();v.projectile_document.write(text);rebuild_projectile(v)
            elif self.action=='PROJECTILE_SOURCE':st.open_text_window(context,v.projectile_document)
            elif self.action=='PROJECTILE_MOVEMENT':
                from .studio_core import Blueprint,lua_compile
                text=v.projectile_document.as_string()
                for key,value in [('speedMax',v.projectile_speed),('acceleration',v.projectile_acceleration),('rotationSpeed',v.projectile_turn),('type',v.projectile_homing)]:
                    from .vfx_documents import lua_value
                    text=Blueprint(text,'ProjectileTemplate').set(('movement',key),lua_value(value))
                lua_compile(text,st.engine(context));v.projectile_document.clear();v.projectile_document.write(text);rebuild_projectile(v)
                state.status='Movement settings applied to projectile draft. Save to commit them.'
            elif self.action=='PROJECTILE_EFFECT':
                load_asset(context,v.projectile_effects[v.projectile_effect_index].value)
            elif self.action=='PROJECTILE_ASSIGN':
                from .studio_core import Blueprint
                if v.projectile_document.as_string()!=v.projectile_baseline:raise ValueError('Save the projectile draft first.')
                doc=Blueprint(st.source(context));path=('weapons',v.weapon_index,'projectileTemplate')
                if not doc.span(path):raise ValueError('Choose a weapon with a projectileTemplate field.')
                st.set_source(context,doc.set(path,json.dumps(v.projectile_id)))
                state.status='Projectile assigned to the unit draft. Save Unit to commit it.'
            elif self.action == 'PREVIEW':
                from .studio_preview import create_preview
                create_preview(context, state.effect_asset, edited_data(context), st.library(context))
            elif self.action == 'SAVE_COPY':
                assets = st.library(context)
                data = edited_data(context)
                dest, count = assets.clone(state.effect_asset, state.effect_name, data_override=data)
                load_asset(context, dest)
                state.status = f'Custom effect saved to project with {count} dependencies. Use it on a slot below.'
            elif self.action == 'APPLY':
                if not v.slots: raise ValueError('Load a unit and refresh its effect slots first.')
                if state.effect_document.as_string() != state.effect_baseline or has_adjustments(v):
                    raise ValueError('Save your edited effect as a custom copy before assigning it.')
                st.library(context).read(state.effect_asset)
                slot = json.loads(v.slots[v.slot_index].path)
                text, file = apply_slot(st.source(context), slot, state.effect_asset, st.engine(context), mp.active_project(context))
                st.set_source(context, text)
                refresh_slots(context)
                state.status = 'Effect assigned. Save Unit to commit the Lua.' + (' A private projectile copy was created in the project.' if file else '')
            self.report({'INFO'}, state.status)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc)); return {'CANCELLED'}


def editor(layout, context):
    state, v = st.settings(context), context.scene.sanctuary_vfx
    from .studio_workshop import fold
    if not state.effect_document: return
    box = st.card(layout, 'Customise this effect', 'MODIFIER')
    data = json.loads(state.effect_document.as_string())
    if data.get('shader') in ('RTS/Shield/Shied Forcefield CSG V3','RTS/Shield/Shield Edge Detail'):
        box.operator('sanctuary.shield_action',text='Open Native Shield Authoring',icon='SHADING_RENDERED').action='OPEN'
    if data.get('particleSystems') or data.get('colors'):
        box.prop(v, 'use_tint')
        if v.use_tint: box.prop(state, 'effect_color', text='Colour')
        row=box.row(align=True);row.prop(v,'intensity');row.prop(v,'opacity')
    if data.get('particleSystems'):
        row = box.row(align=True); row.prop(v,'size'); row.prop(v,'speed'); row.prop(v,'lifetime')
        box.prop(v,'emission')
    box.prop(state, 'effect_name', text='Custom name')
    box.operator('sanctuary.vfx_action', text='Save as Custom Effect', icon='DUPLICATE').action = 'SAVE_COPY'
    box.operator('sanctuary.vfx_action',text='Save Custom Asset',icon='FILE_TICK').action='SAVE_ASSET'
    row=box.row(align=True)
    row.operator('sanctuary.vfx_action',text='Full Effect Data',icon='TEXT').action='EFFECT_SOURCE'
    row.operator('sanctuary.vfx_action',text='Validate Draft',icon='CHECKMARK').action='EFFECT_VALIDATE'
    if v.materials:
        box.template_list('SANCTUARY_UL_vfx_values','materials',v,'materials',v,'material_index',rows=3)
        box.operator('sanctuary.vfx_action',text='Edit Selected Material',icon='MATERIAL').action='MATERIAL'
    if v.colors:
        box.template_list('SANCTUARY_UL_vfx_values','colors',v,'colors',v,'color_index',rows=3)
        box.prop(v,'material_color')
        box.operator('sanctuary.vfx_action',text='Apply Shader Colour',icon='CHECKMARK').action='COLOR'
    if v.textures:
        tex=box.box();tex.label(text='Shader textures / shield normal maps',icon='TEXTURE')
        tex.template_list('SANCTUARY_UL_vfx_values','textures',v,'textures',v,'texture_index',rows=3)
        tex.prop(state,'effect_texture',text='Your PNG');tex.prop(state,'effect_texture_size')
        tex.prop(v,'texture_kind');tex.prop(v,'normal_encoding')
        tex.operator('sanctuary.vfx_action',text='Replace Selected Shader Texture',icon='IMAGE_DATA').action='TEXTURE'
        tex.label(text='Import your own pattern into the normal slot; no pattern is generated.')
        if st.settings(context).blueprint:
            tex.prop(state,'shield_index');st.button(tex,'BIND_SHIELD_MATERIAL','Assign Material to Unit Shield','CHECKMARK')
    if fold(box, v, 'show_properties'):
        box.prop(state, 'effect_filter', text='', icon='VIEWZOOM')
        box.template_list('SANCTUARY_UL_studio_effect_fields', '', state, 'effect_fields', state, 'effect_field_index', rows=4)
        prop={'BOOL':'field_bool','NUMBER':'field_number','STRING':'field_string'}.get(v.field_kind)
        box.prop(v,prop) if prop else box.prop(state,'effect_value')
        box.operator('sanctuary.vfx_action',text='Apply Property',icon='CHECKMARK').action='PROPERTY'


class SANCTUARY_UL_vfx_values(UIList):
    def filter_items(self,context,data,propname):
        query=context.scene.sanctuary_vfx.projectile_search.casefold() if propname=='projectile_fields' else ''
        return [self.bitflag_filter_item if query in item.name.casefold() else 0 for item in getattr(data,propname)],[]
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        layout.label(text=item.name)
        if item.value:layout.label(text=item.value[:65])


def preview_controls(layout,context,show_image=True):
    from .studio_workshop import note
    v=context.scene.sanctuary_vfx
    if show_image and v.thumbnail:
        v.thumbnail.preview_ensure();layout.template_icon(icon_value=v.thumbnail.preview.icon_id,scale=12)
        if v.preview_note:note(layout,v.preview_note)
    if v.preview_is_moving:
        layout.prop(v,'preview_emitter_speed')
        note(layout,'Demonstration speed only; source distance emission and lifetimes are retained. Press Play to refresh.')
    if v.preview_details:
        layout.prop(v,'show_preview_details')
        if v.show_preview_details or v.preview_note.startswith('No supported visible emission'):
            for message in v.preview_details.splitlines():note(layout,message)
    row=layout.row(align=True)
    row.operator('sanctuary.vfx_action',text='Play Preview',icon='PLAY').action='PLAY_GIF'
    row.operator('sanctuary.vfx_action',text='Stop',icon='PAUSE').action='STOP_GIF'
    layout.operator('sanctuary.vfx_action',text='View / Export GIF',icon='RENDER_ANIMATION').action='GIF'
    note(layout,'Data-driven preview, not a game capture. Shader, physics and mesh appearance can differ.')


def projectile_editor(layout,context):
    from .studio_workshop import note
    v=context.scene.sanctuary_vfx
    box=st.card(layout,'Projectile Lua','TRACKING_FORWARDS')
    note(box,v.projectile_id+' • '+v.projectile_path)
    box.prop(v,'projectile_new_id')
    box.operator('sanctuary.vfx_action',text='Clone Projectile into Project',icon='DUPLICATE').action='PROJECTILE_CLONE'
    box.operator('sanctuary.vfx_action',text='Save Custom Projectile',icon='FILE_TICK').action='PROJECTILE_SAVE'
    row=box.row(align=True);row.prop(v,'projectile_speed');row.prop(v,'projectile_acceleration')
    row=box.row(align=True);row.prop(v,'projectile_turn');row.prop(v,'projectile_homing')
    box.operator('sanctuary.vfx_action',text='Apply Movement Settings',icon='CHECKMARK').action='PROJECTILE_MOVEMENT'
    if v.projectile_effects:
        box.template_list('SANCTUARY_UL_vfx_values','projectile-effects',v,'projectile_effects',v,'projectile_effect_index',rows=3)
        box.operator('sanctuary.vfx_action',text='Edit Trail / Impact Effect',icon='PARTICLES').action='PROJECTILE_EFFECT'
    box.prop(v,'projectile_search',text='',icon='VIEWZOOM')
    box.template_list('SANCTUARY_UL_vfx_values','projectiles',v,'projectile_fields',v,'projectile_field_index',rows=6)
    box.prop(v,'projectile_value')
    box.operator('sanctuary.vfx_action',text='Apply Projectile Setting',icon='CHECKMARK').action='PROJECTILE_FIELD'
    box.operator('sanctuary.vfx_action',text='Edit Full Projectile Lua',icon='TEXT').action='PROJECTILE_SOURCE'
    note(box,'Strings need Lua quotes. Damage, rate of fire and launch speed belong to the unit weapon.')
    if st.settings(context).blueprint:
        box.prop(v,'weapon_index')
        box.operator('sanctuary.vfx_action',text='Assign to Unit Weapon',icon='CHECKMARK').action='PROJECTILE_ASSIGN'


def draw_effects(layout, context):
    state, v = st.settings(context), context.scene.sanctuary_vfx
    from .studio_workshop import note
    layout.operator('sanctuary.vfx_browser', text='Open Effect Browser…', icon='PARTICLES')
    layout.operator('sanctuary.vfx_action',text='Browse Included Stock GIFs',icon='RENDER_ANIMATION').action='INCLUDED_GALLERY'
    if state.blueprint:layout.operator('sanctuary.vfx_action', text='Read This Unit’s Effects', icon='FILE_REFRESH').action = 'SLOTS'
    if state.effect_asset.startswith('projectile:'):
        preview_controls(layout,context);projectile_editor(layout,context);return
    if v.slots:
        layout.template_list('SANCTUARY_UL_effect_slots', '', v, 'slots', v, 'slot_index', rows=5)
        note(layout, 'Weapon trails and impacts are edited through a private projectile copy.')
        layout.operator('sanctuary.vfx_action', text='Inspect Current Effect', icon='VIEWZOOM').action = 'INSPECT_SLOT'
    if state.effect_document:
        note(layout, Path(state.effect_asset).stem)
        layout.operator('sanctuary.vfx_action', text='Preview Changes in Blender', icon='PLAY').action = 'PREVIEW'
        editor(layout, context)
        layout.operator('sanctuary.vfx_action', text='Replace Selected Slot', icon='CHECKMARK').action = 'APPLY'
        box = st.card(layout, 'Add an effect to a bone', 'BONE_DATA')
        st.bone_control(box, context)
        st.button(box, 'BIND_EFFECT', 'Attach Effect', 'ADD')
        if state.advanced: box.prop(state, 'bind_path')


CLASSES = (VFXState, SANCTUARY_UL_effect_slots, SANCTUARY_UL_vfx_values, SANCTUARY_OT_vfx_browser, SANCTUARY_OT_vfx_action)
def register():
    for cls in CLASSES: bpy.utils.register_class(cls)
    bpy.types.Scene.sanctuary_vfx = PointerProperty(type=VFXState)
def unregister():
    close_browser()
    from .vfx_browser_cache import clear
    clear()
    del bpy.types.Scene.sanctuary_vfx
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
