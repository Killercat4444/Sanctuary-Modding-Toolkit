"""Persistent Lua forms. Closing a dialog never owns or discards the draft."""
from pathlib import Path
from datetime import datetime
import json
import re
import shutil
import time
import bpy
from bpy.props import StringProperty, EnumProperty
from bpy.types import Operator, UIList

from .lua_document import Document, string_table
from . import mod_projects as mp

FIELDS = {
    'lua_display_name': ('general','displayName'),
    'lua_unit_type': ('general','unitTypeName'),
    'lua_health': ('defence','health','max'),
    'lua_alloy_cost': ('economy','cost','alloys'),
    'lua_energy_cost': ('economy','cost','energy'),
    'lua_build_time': ('economy','buildTime'),
}
FORM = tuple(FIELDS) + ('lua_faction_prefix','lua_sequence','lua_domain','lua_tier',
    'lua_use_project_id','lua_faction_tag','lua_buildable_by','lua_extra_tags',
    'lua_target_layers','lua_donor_unit')
STATE = FORM + ('lua_source_path','lua_project_path','lua_source_text','lua_manual_text',
    'lua_baseline','lua_editor_mode')
TAG_FIELDS = ('lua_faction_tag','lua_buildable_by','lua_extra_tags','lua_target_layers')
_ENUM_CACHE = []  # Blender requires dynamic enum strings to stay alive.
_DONOR_CACHE = []


def snapshot(s): return {key:getattr(s,key) for key in FORM}


def stash(s):
    if s.lua_editor_mode == 'SOURCE' and s.lua_source_lines:
        s.lua_manual_text = '\n'.join(item.text for item in s.lua_source_lines) + '\n'
    drafts = json.loads(s.lua_drafts or '{}')
    drafts[s.lua_source_path or '@new'] = {key:getattr(s,key) for key in STATE}
    s.lua_drafts = json.dumps(drafts)


def weapon_paths(doc):
    span = doc.span(('weapons',))
    if not span: return []
    root = next(i for i,t in enumerate(doc.tokens) if t.start() == span[0])
    return [('weapons', key, 'layerTargetLimits') for key in doc.fields(root)
            if doc.span(('weapons',key,'layerTargetLimits'))]


def load_file(context, file, project, resume=True):
    s = context.scene.sanctuary_toolkit
    file = Path(file).resolve(); project = Path(project).resolve()
    if project not in file.parents: raise ValueError('Lua is outside this project')
    stash(s)
    drafts = json.loads(s.lua_drafts)
    if resume and str(file) in drafts:
        for key,value in drafts[str(file)].items(): setattr(s,key,value)
        _set_source_lines(s, s.lua_manual_text or s.lua_source_text, s.lua_source_text)
        s.mod_active_path = str(project)
        return
    text = file.read_text(encoding='utf-8-sig')
    s.lua_source_path = str(file); s.lua_project_path = str(project)
    s.lua_source_text = text; s.lua_manual_text = ''
    s.lua_source_lines.clear()
    _populate_form(s, text, project, file)
    s.mod_active_path = str(project)
    stash(s)


def _populate_form(s, text, project, file):
    """Populate the visual controls from complete Lua without discarding it."""
    doc = Document(text)
    uid = str(doc.scalar(('general','tpId'), file.stem)).lower()
    for key,path in FIELDS.items():
        value = doc.scalar(path)
        if value is not None: setattr(s,key,value)
    if re.fullmatch(r'u[a-z]{2}\d{4}',uid):
        s.lua_faction_prefix = uid[1].upper()
        if uid[2].upper() in ('L','S','A','N'): s.lua_domain = uid[2].upper()
        s.lua_sequence = uid[-3:]
        if uid[3] in '1234': s.lua_tier = uid[3]
    tags = doc.strings(('tags',))
    manifest = mp.read_manifest(project)
    factions = {'CHOSEN','GUARD','EDA',manifest.get('faction_tag','')}
    s.lua_faction_tag = ', '.join(t for t in tags if t in factions)
    s.lua_buildable_by = ', '.join(t for t in tags if t.startswith('BUILDABLE_BY_'))
    s.lua_extra_tags = ', '.join(t for t in tags if t not in factions and not t.startswith(('BUILDABLE_BY_', 'TECH')))
    for n in '1234':
        if 'TECH'+n in tags: s.lua_tier = n
    layers = list(dict.fromkeys(v for p in weapon_paths(doc) for v in doc.strings(p)))
    s.lua_target_layers = ', '.join(layers)
    s.lua_use_project_id = False
    s.lua_baseline = json.dumps(snapshot(s))


def render_form_source(s):
    """Render current visual controls over the last complete source draft."""
    source = s.lua_manual_text or s.lua_source_text
    if not source:
        return ''
    baseline = json.loads(s.lua_baseline or '{}')
    doc = Document(source)
    old_uid = str(doc.scalar(('general','tpId'), Path(s.lua_source_path).stem)).lower()
    new_uid = mp._unit_id(s)
    if new_uid != old_uid:
        source = re.sub(re.escape(old_uid), new_uid, source, flags=re.IGNORECASE)
        doc = Document(source)
    changes = {}
    for prop, path in FIELDS.items():
        value = getattr(s, prop)
        if baseline.get(prop) != value:
            changes[path] = json.dumps(value, ensure_ascii=False)
    if baseline.get('lua_health') != s.lua_health and doc.span(('defence','health','value')):
        changes[('defence','health','value')] = str(s.lua_health)
    if any(baseline.get(k) != getattr(s,k) for k in ('lua_faction_tag','lua_buildable_by','lua_extra_tags','lua_tier')):
        old_tags = doc.strings(('tags',))
        tags = mp._csv(s.lua_faction_tag) + mp._csv(s.lua_buildable_by) + mp._csv(s.lua_extra_tags)
        if baseline.get('lua_tier') != s.lua_tier or any(re.fullmatch(r'TECH[1-4]',t) for t in old_tags):
            tags.append('TECH'+s.lua_tier)
        changes[('tags',)] = string_table(list(dict.fromkeys(tags)))
    if baseline.get('lua_target_layers') != s.lua_target_layers:
        for path in weapon_paths(doc):
            changes[path] = string_table(mp._csv(s.lua_target_layers))
    result = doc.replace(changes)
    Document(result)
    return result


def _set_source_lines(s, current, original):
    current_lines = current.splitlines()
    original_lines = original.splitlines()
    s.lua_source_lines.clear()
    for index, value in enumerate(current_lines):
        item = s.lua_source_lines.add()
        item.line_number = index + 1
        item.original = original_lines[index] if index < len(original_lines) else ''
        item.text = value
        item.changed = item.text != item.original
    s.lua_source_line_index = min(s.lua_source_line_index, max(0, len(current_lines)-1))


def refresh_source_lines(s):
    _set_source_lines(s, render_form_source(s), s.lua_source_text)


def source_lines_text(s):
    return '\n'.join(item.text for item in s.lua_source_lines) + ('\n' if s.lua_source_lines else '')


def apply_source_lines(s):
    text = source_lines_text(s)
    Document(text)
    project = Path(s.lua_project_path).resolve()
    file = Path(s.lua_source_path).resolve()
    s.lua_manual_text = text
    _populate_form(s, text, project, file)
    # The manually edited source is now the form's baseline; the on-disk text
    # remains separate for external-change detection and diff highlighting.
    s.lua_manual_text = text
    stash(s)


def _rename_project_unit(project, old_uid, new_uid):
    if old_uid == new_uid:
        return
    project = Path(project)
    old_units = project/'Units'/old_uid
    new_units = project/'Units'/new_uid
    old_icon = project/'UI/Sprites/Icons/Units'/f'{old_uid}.dds'
    new_icon = old_icon.with_name(f'{new_uid}.dds')
    old_sprite = old_icon.with_suffix('.sansprite')
    new_sprite = new_icon.with_suffix('.sansprite')
    old_png = project/'Source/Icons'/f'{old_uid}.png'
    new_png = old_png.with_name(f'{new_uid}.png')
    if new_units.exists() or new_icon.exists() or new_sprite.exists() or new_png.exists():
        raise FileExistsError(f'Project assets already exist for {new_uid}')
    if old_units.exists():
        old_units.rename(new_units)
        for child in sorted(new_units.rglob('*'), key=lambda p: len(p.parts), reverse=True):
            if not child.is_file():
                continue
            data = child.read_bytes()
            data = data.replace(old_uid.encode(),new_uid.encode()).replace(old_uid.upper().encode(),new_uid.upper().encode())
            child.write_bytes(data)
            renamed = re.sub(old_uid,new_uid,child.name,flags=re.IGNORECASE)
            if renamed != child.name:
                child.rename(child.with_name(renamed))
    if old_icon.exists():
        old_icon.rename(new_icon)
    if old_png.exists():
        old_png.rename(new_png)
    if old_sprite.exists():
        text = re.sub(old_uid, new_uid, old_sprite.read_text(encoding='utf-8-sig'), flags=re.IGNORECASE)
        new_sprite.write_text(text, encoding='utf-8')
        old_sprite.unlink()
    from .unit_icons import repair_missing
    repair_missing(project)


def save_draft(context):
    s = context.scene.sanctuary_toolkit
    if not s.lua_source_path:
        file = mp.generate_lua(context)
        load_file(context,file,mp.active_project(context),resume=False)
        return file
    project = Path(s.lua_project_path).resolve()
    if project != mp.active_project(context).resolve():
        raise ValueError('This draft belongs to a different project. Reopen that project before saving.')
    file = Path(s.lua_source_path).resolve()
    if project not in file.parents: raise ValueError('Lua path escaped its project')
    source = s.lua_source_text
    if file.read_text(encoding='utf-8-sig') != source:
        raise ValueError('Lua changed on disk. Your draft is kept; reload it explicitly before overwriting external edits.')
    if s.lua_editor_mode == 'SOURCE' and s.lua_source_lines:
        apply_source_lines(s)
    disk_doc = Document(source)
    old_uid = str(disk_doc.scalar(('general','tpId'),file.stem)).lower()
    new_uid = mp._unit_id(s)
    result = render_form_source(s)
    if result != source:
        backup = project/'.smt_history'/datetime.now().strftime('%Y%m%d-%H%M%S-%f')/file.relative_to(project)
        backup.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(file,backup)
        destination = file
        if new_uid != old_uid:
            destination = project/'Lua/common/units/unitsTemplates'/new_uid/f'{new_uid}.santp'
            if destination.exists() and destination.resolve() != file.resolve():
                raise FileExistsError(f'Lua already exists for {new_uid}')
            _rename_project_unit(project,old_uid,new_uid)
            if s.mod_unit_icon_png and Path(bpy.path.abspath(s.mod_unit_icon_png)).resolve() == (project/'Source/Icons'/f'{old_uid}.png').resolve():
                s.mod_unit_icon_png = str(project/'Source/Icons'/f'{new_uid}.png')
        destination.parent.mkdir(parents=True,exist_ok=True)
        temporary = destination.with_suffix(destination.suffix+'.tmp')
        temporary.write_text(result,encoding='utf-8',newline='\n'); temporary.replace(destination)
        if destination.resolve() != file.resolve():
            file.unlink()
            try: file.parent.rmdir()
            except OSError: pass
            file = destination
    manifest = mp.read_manifest(project)
    uid = new_uid
    units = manifest.setdefault('units',[])
    if old_uid != new_uid:
        for existing in units:
            if existing.get('unit_id') == old_uid:
                existing['unit_id'] = new_uid
    unit = next((u for u in units if u.get('unit_id') == uid),None)
    if unit is None: unit={'unit_id':uid}; units.append(unit)
    unit.update(display_name=s.lua_display_name, template=file.relative_to(project).as_posix())
    manifest['known_tags'] = sorted(set(manifest.get('known_tags',[])) | set(mp._csv(s.lua_known_tags)))
    mp.write_manifest(project,manifest)
    if old_uid != new_uid:
        s.lua_source_path = str(file)
        s.unit_id = new_uid
    s.lua_source_text=result; s.lua_manual_text=''; s.lua_baseline=json.dumps(snapshot(s))
    refresh_source_lines(s); stash(s)
    return file


def known_tags(context, field):
    from .game_browser import load_database
    s = context.scene.sanctuary_toolkit
    values = set(mp._csv(s.lua_known_tags)) | set(mp._csv(getattr(s,field)))
    for record in load_database():
        file = Path(record.get('template_path',''))
        if not file.is_file(): continue
        try:
            doc = Document(file.read_text(encoding='utf-8-sig'))
            if field == 'lua_target_layers':
                for path in weapon_paths(doc): values.update(doc.strings(path))
            else: values.update(doc.strings(('tags',)))
        except (OSError,ValueError): continue
    project = mp.active_project(context,required=False)
    if project and (project/mp.MANIFEST).is_file():
        manifest = mp.read_manifest(project)
        values.update(manifest.get('known_tags',[]))
        if field != 'lua_target_layers': values.add(manifest.get('faction_tag',''))
    return sorted(v for v in values if v)


def _tag_items(self,context): return _ENUM_CACHE


def _donor_items(self,context): return _DONOR_CACHE


class SANCTUARY_OT_choose_lua_template(Operator):
    bl_idname='sanctuary.choose_lua_template'; bl_label='Choose Stock Unit Template'
    bl_property='choice'
    choice: EnumProperty(items=_donor_items)
    def invoke(self,context,event):
        global _DONOR_CACHE
        from .game_browser import load_database
        _DONOR_CACHE=[(r['unit_id'],r['unit_id'].upper()+' — '+r.get('display_name',r['unit_id']),r.get('unit_type',''))
            for r in load_database() if r.get('source_kind','BASE')=='BASE' and Path(r.get('template_path','')).is_file()]
        if not _DONOR_CACHE:
            self.report({'ERROR'},'Rebuild the unit index first');return {'CANCELLED'}
        context.window_manager.invoke_search_popup(self);return {'RUNNING_MODAL'}
    def execute(self,context):
        from .game_browser import load_database
        s=context.scene.sanctuary_toolkit
        if s.lua_source_path: return {'CANCELLED'}
        try:
            record=next(r for r in load_database() if r['unit_id']==self.choice and r.get('source_kind','BASE')=='BASE')
            doc=Document(Path(record['template_path']).read_text(encoding='utf-8-sig'))
            tags=doc.strings(('tags',))
            s.lua_donor_unit=self.choice
            for prop,path in FIELDS.items():
                value=doc.scalar(path)
                if value is not None:setattr(s,prop,value)
            for tier in '1234':
                if 'TECH'+tier in tags:s.lua_tier=tier
            domain=self.choice[2].upper()
            if domain in 'LSAN':s.lua_domain=domain
            s.lua_buildable_by=', '.join(t for t in tags if t.startswith('BUILDABLE_BY_'))
            s.lua_extra_tags=', '.join(t for t in tags if t not in {'CHOSEN','GUARD','EDA'} and not t.startswith(('BUILDABLE_BY_','TECH')))
            s.lua_target_layers=', '.join(dict.fromkeys(v for p in weapon_paths(doc) for v in doc.strings(p)))
            project=mp.active_project(context,required=False)
            faction=mp.read_manifest(project).get('faction_tag','') if project and (project/mp.MANIFEST).is_file() else ''
            s.lua_faction_tag=faction
            stash(s);return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}


class SANCTUARY_OT_add_lua_tag(Operator):
    bl_idname='sanctuary.add_lua_tag'; bl_label='Choose a Known Tag'
    bl_property='choice'
    field: StringProperty()
    choice: EnumProperty(items=_tag_items)
    def invoke(self,context,event):
        global _ENUM_CACHE
        if self.field not in TAG_FIELDS: return {'CANCELLED'}
        _ENUM_CACHE=[(v,v,'Add '+v) for v in known_tags(context,self.field)]
        if not _ENUM_CACHE: self.report({'INFO'},'No known tags; register a custom tag below'); return {'CANCELLED'}
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}
    def execute(self,context):
        if self.field not in TAG_FIELDS: return {'CANCELLED'}
        s=context.scene.sanctuary_toolkit
        setattr(s,self.field,', '.join(dict.fromkeys(mp._csv(getattr(s,self.field))+[self.choice])))
        stash(s); return {'FINISHED'}


class SANCTUARY_OT_remove_lua_tag(Operator):
    bl_idname='sanctuary.remove_lua_tag'; bl_label='Remove Tag'
    field: StringProperty(); tag: StringProperty()
    @classmethod
    def description(cls,context,properties): return 'Remove '+properties.tag
    def execute(self,context):
        if self.field not in TAG_FIELDS: return {'CANCELLED'}
        s=context.scene.sanctuary_toolkit
        setattr(s,self.field,', '.join(t for t in mp._csv(getattr(s,self.field)) if t != self.tag))
        stash(s); return {'FINISHED'}


class SANCTUARY_OT_custom_lua_tag(Operator):
    bl_idname='sanctuary.custom_lua_tag'; bl_label='Register Custom Tag'
    tag: StringProperty(name='Tag')
    def invoke(self,context,event): return context.window_manager.invoke_props_dialog(self)
    def execute(self,context):
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',self.tag):
            self.report({'ERROR'},'Use letters, numbers and underscores; start with a letter'); return {'CANCELLED'}
        s=context.scene.sanctuary_toolkit
        s.lua_known_tags=', '.join(dict.fromkeys(mp._csv(s.lua_known_tags)+[self.tag]))
        project=mp.active_project(context,required=False)
        if project and (project/mp.MANIFEST).is_file():
            manifest=mp.read_manifest(project)
            manifest['known_tags']=sorted(set(manifest.get('known_tags',[])) | {self.tag})
            mp.write_manifest(project,manifest)
        return {'FINISHED'}


def draw_tags(layout,s,field,label):
    box=layout.box(); box.label(text=label)
    # Flow lays out real tag buttons and wraps them in a narrow dialog.
    flow=box.grid_flow(row_major=True,columns=1 if field=='lua_buildable_by' else 2,even_columns=False,align=True)
    for tag in mp._csv(getattr(s,field)):
        chip=flow.row(align=True)
        chip.label(text=tag)
        op=chip.operator('sanctuary.remove_lua_tag',text='',icon='X')
        op.field=field; op.tag=tag
    flow.operator('sanctuary.add_lua_tag',text='',icon='ADD').field=field


class SANCTUARY_UL_lua_source(UIList):
    bl_idname = 'SANCTUARY_UL_lua_source'
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        row=layout.row(align=True)
        row.alert=bool(item.changed)
        if item.changed:
            op=row.operator('sanctuary.reset_lua_line',text='',icon='LOOP_BACK')
            op.line_index=index
        else:
            row.label(text='',icon='BLANK1')
        number=row.row();number.alignment='RIGHT';number.label(text=str(item.line_number))
        row.prop(item,'text',text='',emboss=False)


class SANCTUARY_OT_refresh_lua_source(Operator):
    bl_idname='sanctuary.refresh_lua_source'; bl_label='Refresh Source from Visual Changes'
    def execute(self,context):
        try: refresh_source_lines(context.scene.sanctuary_toolkit);return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}


class SANCTUARY_OT_apply_lua_source(Operator):
    bl_idname='sanctuary.apply_lua_source'; bl_label='Apply Source to Visual Fields'
    def execute(self,context):
        try: apply_source_lines(context.scene.sanctuary_toolkit);return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}


class SANCTUARY_OT_reset_lua_line(Operator):
    bl_idname='sanctuary.reset_lua_line'; bl_label='Reset Changed Lua Line'
    line_index: bpy.props.IntProperty()
    @classmethod
    def description(cls,context,properties):
        return 'Click again within two seconds to reset this line to the loaded file'
    def execute(self,context):
        s=context.scene.sanctuary_toolkit; now=time.monotonic()
        if s.lua_reset_armed_line != self.line_index or now-s.lua_reset_armed_time>2.0:
            s.lua_reset_armed_line=self.line_index;s.lua_reset_armed_time=now
            self.report({'INFO'},'Click the reset arrow again to reset this changed line')
            return {'FINISHED'}
        if 0<=self.line_index<len(s.lua_source_lines):
            item=s.lua_source_lines[self.line_index];item.text=item.original
        s.lua_reset_armed_line=-1;s.lua_reset_armed_time=0.0
        return {'FINISHED'}


class SANCTUARY_OT_lua_window(Operator):
    bl_idname='sanctuary.lua_window'; bl_label='Unit Lua Editor'
    def invoke(self,context,event):
        return context.window_manager.invoke_props_dialog(self,width=860,confirm_text='Save Lua to Project')
    def draw(self,context):
        s=context.scene.sanctuary_toolkit; layout=self.layout
        layout.label(text='Draft survives closing this window. Save the .blend to retain it between sessions.',icon='INFO')
        if s.lua_source_path: layout.label(text='Editing: '+Path(s.lua_source_path).name,icon='FILE_SCRIPT')
        layout.prop(s,'lua_editor_mode',expand=True)
        if s.lua_editor_mode=='SOURCE':
            if s.lua_source_path and not s.lua_source_lines:
                try: refresh_source_lines(s)
                except Exception: pass
            toolbar=layout.row(align=True)
            toolbar.operator('sanctuary.refresh_lua_source',text='Refresh from Visual',icon='FILE_REFRESH')
            toolbar.operator('sanctuary.apply_lua_source',text='Apply Source to Visual',icon='CHECKMARK')
            layout.template_list('SANCTUARY_UL_lua_source','',s,'lua_source_lines',s,'lua_source_line_index',rows=24)
            layout.label(text='Orange-highlighted lines differ from the loaded file. Reset arrows require two clicks.',icon='INFO')
            layout.label(text='The complete Lua is editable; Save writes it to the active mod project.',icon='FILE_SCRIPT')
            return
        split=layout.split(factor=0.48); left=split.column(); right=split.column()
        identity=left.box(); identity.label(text='Identity & Template')
        if not s.lua_source_path:
            identity.prop(s,'lua_donor_unit')
            identity.operator('sanctuary.choose_lua_template',text='Choose Template...',icon='VIEWZOOM')
        identity.prop(s,'lua_display_name'); identity.prop(s,'lua_unit_type')
        lock=identity.column()
        lock.prop(s,'lua_use_project_id')
        if s.lua_use_project_id: lock.prop(s,'unit_id',text='Project Unit ID')
        else:
            lock.prop(s,'lua_faction_prefix'); lock.prop(s,'lua_domain'); lock.prop(s,'lua_sequence')
        identity.prop(s,'lua_tier')
        try: uid=mp._unit_id(s)
        except ValueError: uid='Enter exactly three digits'
        identity.label(text='Unit ID: '+uid.upper())
        stats=left.box(); stats.label(text='Economy & Durability')
        for prop in FIELDS:
            if prop not in ('lua_display_name','lua_unit_type'): stats.prop(s,prop)
        left.prop(s,'mod_unit_icon_png')
        left.label(text='Unit export preserves icons. Save them separately in Studio > Icon.',icon='INFO')
        draw_tags(right,s,'lua_faction_tag','Faction Tags')
        draw_tags(right,s,'lua_buildable_by','Built By Tags')
        draw_tags(right,s,'lua_extra_tags','Extra Tags')
        draw_tags(right,s,'lua_target_layers','Weapon Target Layers')
        right.label(text='Changing target layers applies this list to all weapons.',icon='INFO')
        right.operator('sanctuary.custom_lua_tag',icon='ADD')
        row=layout.row(); row.operator('sanctuary.new_lua_draft',text='New Draft',icon='ADD')
        if s.lua_source_path:
            row.operator('sanctuary.reload_lua_draft',text='Reload from Disk',icon='FILE_REFRESH')
        layout.label(text='Custom fields, class names, bones and animations are preserved when editing.',icon='CHECKMARK')
    def cancel(self,context): stash(context.scene.sanctuary_toolkit)
    def execute(self,context): return bpy.ops.sanctuary.mod_generate_lua()


class SANCTUARY_OT_new_lua_draft(Operator):
    bl_idname='sanctuary.new_lua_draft'; bl_label='New Lua Draft'
    def execute(self,context):
        s=context.scene.sanctuary_toolkit; stash(s)
        draft=json.loads(s.lua_drafts).get('@new')
        if draft:
            for key,value in draft.items(): setattr(s,key,value)
        s.lua_source_path=''; s.lua_source_text=''; s.lua_manual_text=''; s.lua_baseline=''; s.lua_project_path=''
        s.lua_source_lines.clear();s.lua_editor_mode='VISUAL'
        return {'FINISHED'}


class SANCTUARY_OT_reload_lua_draft(Operator):
    bl_idname='sanctuary.reload_lua_draft'; bl_label='Discard This Draft and Reload Disk File?'
    def invoke(self,context,event): return context.window_manager.invoke_confirm(self,event)
    def execute(self,context):
        s=context.scene.sanctuary_toolkit
        try: load_file(context,s.lua_source_path,s.lua_project_path,resume=False); return {'FINISHED'}
        except Exception as exc: self.report({'ERROR'},str(exc)); return {'CANCELLED'}


class SANCTUARY_OT_edit_unit_lua(Operator):
    bl_idname='sanctuary.edit_unit_lua'; bl_label='Edit Unit Lua'
    record_key: StringProperty()
    def execute(self,context):
        from .game_browser import load_database
        try:
            record=next(r for r in load_database() if r.get('record_key')==self.record_key)
            file=Path(record['template_path']).resolve()
            if not file.is_file(): raise FileNotFoundError(f'No Lua file exists for {record["unit_id"]}')
            project=next((p for p in file.parents if (p/mp.MANIFEST).is_file()),None)
            if project is None:
                # Stage a live/loose Lua file into the active project. Editing is
                # always project-safe; deployment remains an explicit action.
                project=mp.active_project(context,required=False)
                if project is None or not (project/mp.MANIFEST).is_file():
                    root=mp.library_root(context); project=root/'Live Mod Edits'
                    if not (project/mp.MANIFEST).is_file():
                        s=context.scene.sanctuary_toolkit
                        old=(s.mod_project_name,s.mod_tagged,s.mod_faction_name,s.mod_faction_tag)
                        s.mod_project_name='Live Mod Edits';s.mod_tagged=False;s.mod_faction_name='';s.mod_faction_tag=''
                        try: project=mp.create_project(context)
                        finally: s.mod_project_name,s.mod_tagged,s.mod_faction_name,s.mod_faction_tag=old
                    context.scene.sanctuary_toolkit.mod_active_path=str(project)
                uid=record['unit_id']
                staged=project/'Lua/common/units/unitsTemplates'/uid/f'{uid}.santp'
                staged.parent.mkdir(parents=True,exist_ok=True)
                if not staged.exists(): shutil.copy2(file,staged)
                file=staged
            load_file(context,file,project)
            return bpy.ops.sanctuary.lua_window('INVOKE_DEFAULT')
        except Exception as exc: self.report({'ERROR'},str(exc)); return {'CANCELLED'}


CLASSES=(SANCTUARY_UL_lua_source,SANCTUARY_OT_choose_lua_template,SANCTUARY_OT_add_lua_tag,
    SANCTUARY_OT_remove_lua_tag,SANCTUARY_OT_custom_lua_tag,SANCTUARY_OT_refresh_lua_source,
    SANCTUARY_OT_apply_lua_source,SANCTUARY_OT_reset_lua_line,SANCTUARY_OT_lua_window,
    SANCTUARY_OT_new_lua_draft,SANCTUARY_OT_reload_lua_draft,SANCTUARY_OT_edit_unit_lua)

def register():
    for cls in CLASSES: bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
