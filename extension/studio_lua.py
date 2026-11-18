"""Guided Lua generation and component authoring for the Workshop."""
from pathlib import Path
import json
import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup, UIList
from . import studio as st, mod_projects as mp
from .lua_authoring import ROLES, generate_unit, role_matches, bone_issues, component_source, class_sources
from .studio_core import Blueprint, lua_compile


class LuaAuthorState(PropertyGroup):
    role: EnumProperty(name='Unit role',items=[(k,v,'Use a complete compatible game template') for k,v in ROLES])
    templates: CollectionProperty(type=st.StudioField)
    template_index: IntProperty()
    search: StringProperty(name='Find starting template', options={'TEXTEDIT_UPDATE'})
    title: StringProperty(name='Unit name',default='New Unit')
    faction_tag: StringProperty(name='Faction tag',description='Use the custom tag saved in project publishing')
    use_stats: BoolProperty(name='Set initial health and costs',default=False)
    health: FloatProperty(name='Health',default=1000,min=1)
    alloys: FloatProperty(name='Alloy cost',default=100,min=0)
    energy: FloatProperty(name='Energy cost',default=1000,min=0)
    build_time: FloatProperty(name='Build work',default=100,min=.01)
    root_bone: StringProperty(name='Root bone')
    muzzle_bone: StringProperty(name='Primary muzzle bone')
    own_model: BoolProperty(name='Use current Unit ID for exported model',default=True)
    create_classes: BoolProperty(name='Create paired host / client behavior Lua',default=False,description='Project-owned subclasses of the chosen template; existing engine behavior is inherited')
    report: StringProperty()
    component: EnumProperty(name='Component',items=[(k,v,'') for k,v in (
        ('weapons','Weapon'),('defence.shields','Shield'),('intel','Intel'),('economy','Economy'),
        ('construction','Construction / build menu'),('general.orders','Orders'),('visuals.animations','Animation bindings'),
        ('additions.rotators','Rotators'),('visuals.effectsPersistent','Persistent effects'),('audio','Sounds'))])
    component_index: IntProperty(name='Source item',default=1,min=1)
    show_generator: BoolProperty(name='Generate unit Lua',default=False)
    show_components: BoolProperty(name='Add settings from a template',default=False)


class SANCTUARY_UL_lua_archetypes(UIList):
    def filter_items(self,context,data,propname):
        query = context.scene.sanctuary_lua_author.search.casefold().split()
        return [self.bitflag_filter_item if all(q in (item.name+' '+item.value).casefold() for q in query) else 0
                for item in getattr(data,propname)],[]
    def draw_item(self,context,layout,data,item,icon,active_data,active_propname,index):
        layout.label(text=item.value,icon='TEXT'); layout.label(text=item.name.upper())


def index_templates(context):
    v = context.scene.sanctuary_lua_author; v.templates.clear()
    roots = [st.engine(context)/'LJ/lua' if st.engine(context) else None]
    project = mp.active_project(context,required=False)
    if project:
        roots.append(project/'Lua')
        v.faction_tag=mp.read_manifest(project).get('faction_tag','') or v.faction_tag
    paths = {}
    for root in roots:
        if root:
            for file in (root/'common/units/unitsTemplates').rglob('*.santp'): paths[file.stem.lower()] = file
    for uid,file in sorted(paths.items()):
        try:
            text = file.read_text(encoding='utf-8-sig')
            if not role_matches(text,v.role): continue
            doc = Blueprint(text)
            item = v.templates.add(); item.name=uid; item.path=str(file)
            item.value=str(doc.scalar(('general','displayName'),uid))
        except (ValueError,OSError): continue
    v.template_index=0


class SANCTUARY_OT_lua_author(Operator):
    bl_idname='sanctuary.lua_author'
    bl_label='Author Unit Lua'
    action: StringProperty()

    def execute(self,context):
        try:
            v = context.scene.sanctuary_lua_author; state=st.settings(context); s=context.scene.sanctuary_toolkit
            if self.action=='INDEX':
                index_templates(context); state.status=f'{len(v.templates)} compatible starting templates.'
            elif self.action in ('GENERATE','COMPONENT'):
                if not v.templates: raise ValueError('Find templates for a unit role first.')
                item=v.templates[v.template_index]; donor=Path(item.path).read_text(encoding='utf-8-sig')
                if self.action=='GENERATE':
                    project=mp.active_project(context)
                    uid=s.unit_id.strip().lower()
                    from .project_release import legacy_dycom
                    if legacy_dycom(project) and not uid.startswith('ud'):
                        raise ValueError('Dycom MM release units must use the ud namespace so both formats include them.')
                    destination=project/'Lua/common/units/unitsTemplates'/uid/(uid+'.santp')
                    if destination.exists(): raise ValueError('This unit already exists. Open its saved Lua and add components instead.')
                    values = {('defence','health','max'):v.health,('defence','health','value'):v.health,
                              ('economy','cost','alloys'):v.alloys,('economy','cost','energy'):v.energy,
                              ('economy','buildTime'):v.build_time} if v.use_stats else {}
                    text=generate_unit(donor,uid,v.title,v.faction_tag,values,v.own_model,v.root_bone,v.muzzle_bone)
                    sources={}
                    if v.create_classes:
                        text,sources=class_sources(text,uid,v.faction_tag)
                        for relative,code in sources.items():
                            path=(project/'Lua'/relative).resolve()
                            if project.resolve() not in path.parents or path.exists():raise ValueError('Behavior script already exists or escapes the project: '+str(path))
                            if not lua_compile(code,st.engine(context))[0]:raise ValueError('Set the game folder to validate behavior scripts.')
                    lua_compile(text,st.engine(context))
                    from .unit_icons import atomic
                    for relative,code in sources.items():atomic(project/'Lua'/relative,code.encode('utf-8'))
                    st.use_existing_lua(context,text,'Generated from '+item.name)
                    state.status='Complete unit template generated in your project. Review bones, weapons and build access before exporting.'
                else:
                    text=component_source(st.source(context),donor,v.component,v.component_index)
                    lua_compile(text,st.engine(context)); st.set_source(context,text)
                    state.status='Component added to draft. Review its bones and save the unit.'
                if s.animation_target_armature:
                    issues=bone_issues(st.source(context),s.animation_target_armature.data.bones.keys())
                    v.report='Unmatched bones:\n'+'\n'.join(issues[:30]) if issues else 'All explicit bone references match the pinned rig.'
                else: v.report='Pin a rig to check the source template’s bone bindings.'
            elif self.action=='VALIDATE':
                from .studio_validation import validate
                validate(context)
            elif self.action=='SOURCE':
                st.open_text_window(context,state.blueprint)
            elif self.action in ('HOST_SOURCE','CLIENT_SOURCE'):
                pair=Blueprint(st.source(context)).strings(('general','class'))
                if len(pair)!=2:raise ValueError('Unit has no behavior class pair.')
                relative=self.action.split('_')[0].lower()+'/units/unitsClasses/'+pair[0]+'.lua'
                project=mp.active_project(context).resolve();path=(project/'Lua'/relative).resolve()
                if project not in path.parents or not path.is_file():raise ValueError('This unit uses an inherited game class. Generate owned subclasses for a new unit to edit behavior.')
                draft=bpy.data.texts.new(path.name);draft.from_string(path.read_text(encoding='utf-8-sig'));draft.filepath=str(path);draft.use_fake_user=True
                st.open_text_window(context,draft)
                state.status='Project behavior script opened. Use Text Editor Save after editing; exports compile its syntax.'
            self.report({'INFO'},state.status); return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}


def draw(layout,context):
    from .studio_workshop import note,fold
    v=context.scene.sanctuary_lua_author; s=context.scene.sanctuary_toolkit
    # Panel drawing is read-only in Blender. Empty units always show the
    # generator without assigning a scene property during draw.
    empty_unit = not st.settings(context).blueprint
    if empty_unit or fold(layout,v,'show_generator'):
        box=layout.box(); box.prop(v,'role')
        box.operator('sanctuary.lua_author',text='Find Compatible Templates',icon='VIEWZOOM').action='INDEX'
        box.prop(v,'search',text='',icon='VIEWZOOM')
        box.template_list('SANCTUARY_UL_lua_archetypes','',v,'templates',v,'template_index',rows=4)
        box.prop(v,'title'); box.prop(v,'faction_tag');box.prop(v,'own_model');box.prop(v,'create_classes'); box.prop(v,'use_stats')
        if v.use_stats:
            row=box.row(align=True);row.prop(v,'health');row.prop(v,'build_time')
            row=box.row(align=True);row.prop(v,'alloys');row.prop(v,'energy')
        if s.animation_target_armature:
            box.prop_search(v,'root_bone',s.animation_target_armature.data,'bones')
            box.prop_search(v,'muzzle_bone',s.animation_target_armature.data,'bones')
        note(box,'Uses the installed game’s classes and complete schema. Existing unit Lua is preserved. No default Unit ID is supplied.')
        box.operator('sanctuary.lua_author',text='Generate Complete Unit Lua',icon='ADD').action='GENERATE'
    if st.settings(context).blueprint and fold(layout,v,'show_components'):
        box=layout.box();box.prop(v,'role')
        box.operator('sanctuary.lua_author',text='Find Source Templates').action='INDEX'
        box.prop(v,'search',text='',icon='VIEWZOOM')
        box.template_list('SANCTUARY_UL_lua_archetypes','components',v,'templates',v,'template_index',rows=3)
        box.prop(v,'component');box.prop(v,'component_index')
        box.operator('sanctuary.lua_author',text='Add Component to Draft',icon='ADD').action='COMPONENT'
    if v.report: note(layout,v.report)
    if st.settings(context).blueprint:
        row=layout.row(align=True)
        row.operator('sanctuary.lua_author',text='Host Behavior Lua',icon='TEXT').action='HOST_SOURCE'
        row.operator('sanctuary.lua_author',text='Client Behavior Lua',icon='TEXT').action='CLIENT_SOURCE'


CLASSES=(LuaAuthorState,SANCTUARY_UL_lua_archetypes,SANCTUARY_OT_lua_author)
def register():
    for cls in CLASSES:bpy.utils.register_class(cls)
    bpy.types.Scene.sanctuary_lua_author=PointerProperty(type=LuaAuthorState)
def unregister():
    del bpy.types.Scene.sanctuary_lua_author
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
