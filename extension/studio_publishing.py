"""Project-independent faction setup, publishing, validation and help UI."""
import json
from pathlib import Path
import re
import bpy
from bpy.props import BoolProperty, EnumProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup
from . import mod_projects as mp


class PublishingState(PropertyGroup):
    mod_id: StringProperty(name='Unique mod ID',description='Lowercase identity, e.g. com.yourname.faction')
    author: StringProperty(name='Author')
    description: StringProperty(name='Description')
    faction: BoolProperty(name='Add a playable faction',default=True)
    faction_name: StringProperty(name='Faction name')
    faction_key: StringProperty(name='Faction key')
    faction_tag: StringProperty(name='Faction tag')
    prefix: StringProperty(name='Unit prefix',description='Two letters, e.g. um; do not use ue, uc or ug',maxlen=2)
    looks_like: EnumProperty(name='Base visuals',items=[(v,v.title(),'Stock visuals to inherit where not overridden') for v in ('EDA','CHOSEN','GUARD')])
    commander: StringProperty(name='Commander Unit ID',description='Saved project commander, e.g. uml0000')
    commander_name: StringProperty(name='Commander label',default='Commander')
    faction_icon: StringProperty(name='Faction PNG',subtype='FILE_PATH')
    ai_folder: StringProperty(name='Optional AI folder',description='Relative to project Lua, e.g. AI/mods/AI-MyFaction; leave blank for no custom AI')
    mm_bundle: StringProperty(name='MM bootstrap ZIP',subtype='FILE_PATH',description='Trusted Remmy Mod Manager ZIP supplying BepInEx if absent; no framework plugins are copied')
    report: PointerProperty(type=bpy.types.Text)
    help_topic: EnumProperty(name='Help topic',items=[(k,v,'') for k,v in (
        ('start','Start a faction'),('lua','Unit Lua & checks'),('vfx','VFX, projectiles & shields'),
        ('icons','Icons & models'),('mm','Recommended: MM export'),('standalone','Standalone / direct install'),('test','Test & troubleshoot'))])
    loaded_project: StringProperty()


def load(context):
    project=mp.active_project(context);m=mp.read_manifest(project);data=m.get('release',{})
    state=context.scene.sanctuary_publishing
    state.mod_id=data.get('id','');state.author=data.get('author','');state.description=data.get('description','')
    f=(data.get('factions') or [{}])[0]
    state.faction=bool(f) if data else m.get('tagged_faction',True)
    state.faction_name=f.get('name',m.get('faction_name',''));state.faction_tag=f.get('tag',m.get('faction_tag',''))
    state.faction_key=f.get('key','');state.prefix=f.get('unitPrefix','')
    state.looks_like=f.get('looksLike','EDA');state.faction_icon=str(project/f['icon']) if f.get('icon') else ''
    c=(f.get('commanders') or [{}])[0];state.commander=c.get('unit','');state.commander_name=c.get('name','Commander')
    state.ai_folder=f.get('ai',{}).get('folder','');state.loaded_project=str(project)


def save(context):
    project=mp.active_project(context).resolve();m=mp.read_manifest(project);v=context.scene.sanctuary_publishing
    if v.loaded_project!=str(project):raise ValueError('Load this project’s publishing settings before saving; another project’s settings will not be copied.')
    data={'id':v.mod_id.strip(),'name':m['name'],'author':v.author.strip(),'description':v.description.strip(),'kind':'gameplay','apiVersion':1,'luaRoot':'lua'}
    icon_blob=None;icon_target=None
    if v.faction:
        f={'key':v.faction_key.strip(),'name':v.faction_name.strip(),'tag':v.faction_tag.strip().upper(),
           'unitPrefix':v.prefix.strip().lower(),'looksLike':v.looks_like,
           'commanders':[{'name':v.commander_name.strip(),'unit':v.commander.strip().lower()}]}
        from .studio_core import safe_member
        icon_target=project/'Icons'/'faction.png'
        if v.faction_icon:
            source=Path(bpy.path.abspath(v.faction_icon)).resolve()
            if source.suffix.lower()!='.png' or not source.is_file():raise ValueError('Choose an existing faction PNG.')
            icon_blob=source.read_bytes()
            if not icon_blob.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Faction icon is not a PNG.')
        elif not icon_target.is_file():raise ValueError('Choose a faction PNG icon.')
        f['icon']='Icons/faction.png'
        if v.ai_folder:
            folder=safe_member(v.ai_folder)
            if not (project/'Lua'/folder).is_dir():raise ValueError('AI folder must exist inside project Lua.')
            f['ai']={'folder':folder,'name':v.faction_name,'land':True,'air':True,'naval':False}
        data['factions']=[f]
        # Validate identity and commander without modifying the existing manifest.
        if not re.fullmatch(r'[a-z0-9_-]{1,32}',f['key']):raise ValueError('Faction key: use lowercase letters, numbers, underscores or dashes.')
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{0,31}',f['tag']) or f['tag'] in ('EDA','CHOSEN','GUARD'):raise ValueError('Choose a unique custom faction tag.')
        if not re.fullmatch(r'u[a-z]',f['unitPrefix']) or f['unitPrefix'] in ('ue','uc','ug'):raise ValueError('Choose a custom prefix such as um, not a stock prefix.')
        if not re.fullmatch(f['unitPrefix']+r'[a-z]\d{4}',f['commanders'][0]['unit']):raise ValueError('Commander ID must use the configured prefix.')
        path=project/'Lua/common/units/unitsTemplates'/v.commander/(v.commander+'.santp')
        from .studio_core import Blueprint
        if not path.is_file() or not set(Blueprint(path.read_text(encoding='utf-8-sig')).strings(('tags',))).intersection({'COMMAND','COMMANDER'}):
            raise ValueError('Generate and save a Commander-role Lua template, then select its ID here.')
        m.update(tagged_faction=True,faction_name=f['name'],faction_tag=f['tag'],faction_prefix=f['unitPrefix'][1])
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,63}',data['id']) or not all(data[k] for k in ('name','author','description')):
        raise ValueError('Enter a valid unique mod ID, author and description.')
    if icon_blob:
        from .unit_icons import atomic, owned
        atomic(owned(project,'Icons/faction.png'),icon_blob)
    m['release']=data;mp.write_manifest(project,m)
    context.scene.sanctuary_lua_author.faction_tag=m.get('faction_tag','')


class SANCTUARY_OT_publishing(Operator):
    bl_idname='sanctuary.publishing';bl_label='Project Publishing'
    action: StringProperty()
    def execute(self,context):
        try:
            if self.action=='LOAD':load(context)
            elif self.action=='SAVE':save(context)
            elif self.action=='CHECK':
                from .studio import engine
                from .release_validation import check
                project=mp.active_project(context);m=mp.read_manifest(project)
                f=(m.get('release',{}).get('factions') or [None])[0]
                result=check(project,engine(context),f)
                report=bpy.data.texts.new('Studio Project Release Check');report.write(result.text());report.use_fake_user=True
                context.scene.sanctuary_publishing.report=report
                from .studio import open_text_window
                open_text_window(context,report)
                self.report({'ERROR'} if result.errors else {'INFO'},f'{len(result.errors)} errors, {len(result.warnings)} warnings. See release report.')
            elif self.action=='REPORT':
                from .studio import open_text_window
                if context.scene.sanctuary_publishing.report:open_text_window(context,context.scene.sanctuary_publishing.report)
            elif self.action=='HELP':
                from .studio_help import open_help
                open_help(context)
            elif self.action=='RUNTIME':
                from .studio_runtime import inspect_runtime
                from .studio import engine
                from .utils import get_addon_preferences
                p=get_addon_preferences(context);report=inspect_runtime(engine(context),Path(bpy.path.abspath(p.mods_root)))
                text=bpy.data.texts.new('Studio Runtime Check');text.write(json.dumps(report,indent=2));context.scene.sanctuary_publishing.report=text
                from .studio import open_text_window
                open_text_window(context,text)
            return {'FINISHED'}
        except Exception as exc:self.report({'ERROR'},str(exc));return {'CANCELLED'}


def draw(layout,context):
    v=context.scene.sanctuary_publishing
    box=layout.box();box.label(text='Faction & Release',icon='COMMUNITY')
    box.operator('sanctuary.publishing',text='Load Publishing Settings',icon='FILE_REFRESH').action='LOAD'
    if v.loaded_project==str(mp.active_project(context,required=False)):
        for field in ('mod_id','author','description','faction'):box.prop(v,field)
        if v.faction:
            for field in ('faction_name','faction_key','faction_tag','prefix','looks_like','commander','commander_name','faction_icon','ai_folder'):box.prop(v,field)
            box.label(text='One faction, one saved Commander-role unit.',icon='INFO')
            box.label(text='New factions: use MM (dynamic faction slots).',icon='INFO')
            box.label(text='Standalone faction slot conflicts can hide mods.')
        box.operator('sanctuary.publishing',text='Save Faction & Release Setup',icon='FILE_TICK').action='SAVE'
    box.operator('sanctuary.publishing',text='Check Entire Project',icon='CHECKMARK').action='CHECK'
    box.prop(v,'mm_bundle')
    box.operator('sanctuary.publishing',text='Check BepInEx & Lua Loader',icon='CHECKMARK').action='RUNTIME'


def help_draw(layout,context):
    box=layout.box();box.label(text='Help & Getting Started',icon='HELP')
    box.prop(context.scene.sanctuary_publishing,'help_topic',text='')
    box.operator('sanctuary.publishing',text='Open Help Guide',icon='TEXT').action='HELP'


def register():
    for cls in (PublishingState,SANCTUARY_OT_publishing):bpy.utils.register_class(cls)
    bpy.types.Scene.sanctuary_publishing=PointerProperty(type=PublishingState)
def unregister():
    del bpy.types.Scene.sanctuary_publishing
    for cls in (SANCTUARY_OT_publishing,PublishingState):bpy.utils.unregister_class(cls)
