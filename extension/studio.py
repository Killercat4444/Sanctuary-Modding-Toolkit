"""Persistent, project-centred authoring workspace; existing codecs stay in use."""
from datetime import datetime
import json
import math
from pathlib import Path
import shutil
import tempfile

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, FloatProperty,
                       FloatVectorProperty, IntProperty, PointerProperty, StringProperty)
from bpy.types import Operator, Panel, PropertyGroup, UIList
from mathutils import Matrix, Vector
from bpy_extras.io_utils import ImportHelper, ExportHelper

from . import mod_projects as mp, game_browser as gb, operators as legacy
from .studio_core import Blueprint, AssetLibrary, ensure_builder_skirt, json_leaves, json_set, lua_compile, path_label, safe_member
from .utils import get_addon_preferences, resolve_engine_root, safe_unit_id

_LIBRARY = None
_DONORS = []
_NAMED_CHOICES = []  # Keep enum strings alive for Blender's dynamic enum callback.
_NAMED_VALUES = {}
# Exact unit movement names from the shipped AI/UnitBlueprintValidator.lua.
MOVEMENT_TYPES = ('Gunship', 'Hover', 'LegsAmphibious', 'LegsLand', 'LegsSeabed', 'Plane',
                  'TracksAmphibious', 'TracksLand', 'TracksSeabed', 'TracksWater', 'UnderWater', 'WaterSurface')


def donor_items(self, context):
    return _DONORS or [('NONE', 'Build the unit index first', '')]


def named_choice_items(self, context):
    return _NAMED_CHOICES or [('__CUSTOM__', 'Custom value...', 'Enter your own value')]


class SANCTUARY_OT_studio_named_choice(Operator):
    bl_idname = 'sanctuary.studio_named_choice'
    bl_label = 'Choose Lua Value'
    bl_property = 'choice'
    choice: EnumProperty(items=named_choice_items)
    add_tag: BoolProperty(default=False, options={'HIDDEN'})
    field_path: StringProperty(options={'HIDDEN'})

    def invoke(self, context, event):
        global _NAMED_CHOICES, _NAMED_VALUES
        from .studio_choices import choices
        state = settings(context)
        try:
            path = ('tags', 1) if self.add_tag else tuple(json.loads(state.fields[state.field_index].path))
            self.field_path = json.dumps(path)
            values = choices(engine(context), mp.active_project(context), path,
                             '' if self.add_tag else state.string_value, Blueprint(source(context)))
            _NAMED_CHOICES = [('__CUSTOM__', 'Custom value...', 'Enter a new or mod-specific value')]
            records={}
            if path[-1]=='projectileTemplate':
                from .vfx_catalogue import projectile_records
                records=projectile_records(engine(context),mp.active_project(context))
                values=sorted(set(values)|set(records))
            _NAMED_VALUES={str(i):value for i,value in enumerate(values)}
            for i,value in enumerate(values):
                record=records.get(value.lower())
                label=(record['name']+' — '+value) if record else value
                if record and len(record['users'])>1:
                    label+=' · also '+', '.join(c['name'] for c in record['users'][1:])
                description=('Used by: '+', '.join(c['name'] for c in record['users'])+'; tags: '+', '.join(record['tags'])) if record else 'Observed in game/project Lua; review compatibility'
                _NAMED_CHOICES.append((str(i),label,description))
            context.window_manager.invoke_search_popup(self)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def execute(self, context):
        state = settings(context)
        try:
            if self.choice == '__CUSTOM__':
                if self.add_tag:
                    state.show_custom_tag = True
                else:
                    state.custom_string = True
                return {'FINISHED'}
            value = _NAMED_VALUES[self.choice]
            if self.add_tag:
                add_lua_tag(context, value)
            else:
                if not state.fields or state.fields[state.field_index].path != self.field_path:
                    raise ValueError('Selected field changed; reopen its choices')
                state.string_value = value
                state.custom_string = False
                state.status = 'Selected ' + value + '. Click Apply Field to update the draft.'
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def add_lua_tag(context, value):
    if not value.strip():
        raise ValueError('Enter a tag first')
    doc = Blueprint(source(context))
    if value in doc.strings(('tags',)):
        raise ValueError('This tag is already present')
    text = doc.append(('tags',), json.dumps(value))
    lua_compile(text, engine(context))
    set_source(context, text)
    settings(context).status = 'Tag added to draft: ' + value + '. Save Lua when ready.'


class SANCTUARY_OT_studio_donor(Operator):
    bl_idname = 'sanctuary.studio_donor'
    bl_label = 'Choose Existing Unit Lua'
    bl_property = 'donor'
    donor: EnumProperty(items=donor_items)
    apply_to_unit: BoolProperty(default=True, options={'HIDDEN'})

    def invoke(self, context, event):
        global _DONORS
        records = gb.load_database()
        if not records:
            root = engine(context)
            if root:
                gb.build_database(root)
                records = gb.load_database()
        from .studio_donors import choices
        _DONORS = choices(records, mp.active_project(context, required=False))
        context.window_manager.invoke_search_popup(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if self.donor == 'NONE':
            return {'CANCELLED'}
        try:
            settings(context).donor = self.donor
            if self.apply_to_unit:
                label = next((label for key, label, _ in _DONORS if key == self.donor), self.donor)
                use_existing_lua(context, donor_document(context).text, label)
            self.report({'INFO'}, settings(context).status)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SANCTUARY_OT_studio_mesh_export(Operator, ExportHelper):
    bl_idname = 'sanctuary.studio_mesh_export'
    bl_label = 'Export Mesh As'
    filename_ext = '.sanmodel'
    filter_glob: StringProperty(default='*.sanmodel', options={'HIDDEN'})

    def invoke(self, context, event):
        uid = safe_unit_id(context.scene.sanctuary_toolkit.unit_id)
        self.filepath = (uid or 'unit') + '_lod0.sanmodel'
        return ExportHelper.invoke(self, context, event)

    def execute(self, context):
        try:
            from .sanmodel_io import export_sanmodel_file
            s = context.scene.sanctuary_toolkit
            obj, rig = s.export_model_object, s.animation_target_armature
            if not obj:
                raise ValueError('Pin your mesh first')
            rig = rig or obj.find_armature()
            if obj.vertex_groups and not rig:
                raise ValueError('This mesh has vertex groups; pin its armature first')
            path = Path(self.filepath)
            stats = export_sanmodel_file(context, obj, rig, path, path.stem, s.swap_yz_axis)
            state = settings(context)
            state.status = f'Mesh exported: {path} ({stats["bones"]} bones). Skeleton/Lua not included in mesh-only export.'
            self.report({'WARNING'} if stats.get('unknown_vertex_groups') else {'INFO'}, state.status)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SANCTUARY_OT_studio_lua_file(Operator, ImportHelper):
    bl_idname = 'sanctuary.studio_lua_file'
    bl_label = 'Use Existing Lua File'
    filter_glob: StringProperty(default='*.santp;*.lua', options={'HIDDEN'})

    def execute(self, context):
        try:
            path = Path(self.filepath)
            use_existing_lua(context, path.read_text(encoding='utf-8-sig'), path.name)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SANCTUARY_OT_studio_record_lua(Operator):
    bl_idname = 'sanctuary.studio_record_lua'
    bl_label = 'Use This Lua for Current Unit'
    record_key: StringProperty()

    def execute(self, context):
        try:
            settings(context).donor = self.record_key
            use_existing_lua(context, donor_document(context).text, self.record_key)
            self.report({'INFO'}, 'Lua loaded for your current unit. Close the browser to review it in Studio.')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def use_existing_lua(context, value, label):
    """Load a chosen unit Lua immediately; keep the current mesh, rig and ID."""
    state = settings(context)
    uid = safe_unit_id(context.scene.sanctuary_toolkit.unit_id)
    if not uid:
        raise ValueError('Set the target Unit ID first')
    project = mp.active_project(context)
    doc = Blueprint(value)
    donor = doc.scalar(('general', 'tpId'))
    if not isinstance(donor, str) or not safe_unit_id(donor):
        raise ValueError('Choose a unit blueprint containing general.tpId, not a behavior script')
    value = value.replace(donor, uid)
    lua_compile(value, engine(context))
    from .project_blueprints import ensure_template
    path = ensure_template(project, uid, engine(context), source_text=value)
    # Old drafts remain retrievable in Blender Text datablocks, never discarded.
    previous = state.blueprint
    if previous:
        previous.use_fake_user = True
    state.blueprint = bpy.data.texts.new(uid + '.santp')
    state.blueprint.use_fake_user = True
    state.blueprint_path = str(path)
    state.binding_project = str(project)
    state.baseline = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    state.filter = ''
    state.field_group = 'general'
    state.lua_origin = label
    set_source(context, value)
    state.stage = 'BLUEPRINT'
    state.status = f'Loaded {label} for {uid}. Mesh and rig retained. Review and save; previous draft kept in Texts.'


def open_text_window(context, text):
    if not text:
        raise ValueError('No report or Lua source to open')
    wm = context.window_manager
    state = settings(context)
    state.viewer_text = text
    state.viewer_visible = True
    state.viewer_page = 1
    before = {w.as_pointer() for w in wm.windows}
    try:
        # area_dupli depends on mouse coordinates; window_new does not.
        bpy.ops.wm.window_new('EXEC_DEFAULT')
    except RuntimeError:
        state.status = 'New window unavailable. Read the text below or use Copy Full Text.'
        return
    attempts = [0]
    def finish():
        created = [w for w in wm.windows if w.as_pointer() not in before]
        if created:
            area = max(created[0].screen.areas, key=lambda a: a.width * a.height)
            area.type = 'TEXT_EDITOR'
            area.spaces.active.text = text
            area.spaces.active.show_word_wrap = True
            state.status = 'Opened ' + text.name + ' in a separate window.'
            return None
        attempts[0] += 1
        if attempts[0] >= 30:
            state.status = 'New window unavailable. Read the text below or use Copy Full Text.'
            return None
        return 0.1
    if finish() is not None:
        bpy.app.timers.register(finish, first_interval=0.1)


class SANCTUARY_OT_studio_deploy(Operator):
    bl_idname = 'sanctuary.studio_deploy'
    bl_label = 'Deploy Project to SSS'
    summary: StringProperty()

    def invoke(self, context, event):
        try:
            from .studio_validation import project_preflight
            counts = project_preflight(context)
            self.summary = ', '.join(f'{k}: {v}' for k, v in counts.items() if v)
            return context.window_manager.invoke_props_dialog(self, width=550)
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def draw(self, context):
        self.layout.label(text=self.summary)
        prefs = get_addon_preferences(context)
        self.layout.label(text='Destination: ' + (prefs.mods_root if prefs else 'Not set'))
        self.layout.label(text='Matching installed files will be updated.', icon='INFO')
        self.layout.label(text='Restart the game after deployment to test the updated files.')

    def execute(self, context):
        try:
            from .studio_validation import project_preflight
            project_preflight(context)
            counts, _ = mp.deploy_project(context)
            settings(context).status = 'Project installed. Restart Sanctuary to use the updated files.'
            self.report({'INFO'}, 'Deployment complete. Restart SSS before testing.')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SANCTUARY_OT_studio_new_unit(Operator):
    bl_idname = 'sanctuary.studio_new_unit'
    bl_label = 'New Unit from Template'
    prefix: StringProperty(name='Faction letter', maxlen=1)
    family: EnumProperty(name='Category', items=[(k, v, '') for k, v in (('l','Land'),('s','Structure'),('a','Air'),('n','Naval'))], default='s')
    tier: IntProperty(name='Tier', default=1, min=0, max=4)
    serial: IntProperty(name='Number', default=1, min=0, max=999)
    title: StringProperty(name='Display name', default='New Unit')
    replace_existing: BoolProperty(name='Replace the donor unit', default=False)
    keep_assets: BoolProperty(name='Use currently pinned mesh and rig', default=True)

    def invoke(self, context, event):
        project=mp.active_project(context,required=False)
        if project:self.prefix=mp.read_manifest(project).get('faction_prefix','')
        return context.window_manager.invoke_props_dialog(self, width=450)

    def draw(self, context):
        self.layout.label(text='Template: ' + settings(context).donor)
        self.layout.prop(self, 'replace_existing')
        if not self.replace_existing:
            row = self.layout.row(align=True)
            row.prop(self, 'prefix'); row.prop(self, 'family')
            row = self.layout.row(align=True)
            row.prop(self, 'tier'); row.prop(self, 'serial')
            self.layout.label(text=f'Unit ID: u{self.prefix.lower()}{self.family}{self.tier}{self.serial:03}')
        self.layout.prop(self, 'title')
        self.layout.prop(self, 'keep_assets')

    def execute(self, context):
        try:
            project = mp.active_project(context)
            doc = donor_document(context)
            donor = str(doc.scalar(('general', 'tpId'), ''))
            uid = donor if self.replace_existing else f'u{self.prefix.lower()}{self.family}{self.tier}{self.serial:03}'
            if not safe_unit_id(uid):
                raise ValueError('The generated Unit ID is invalid')
            target = project / 'Lua/common/units/unitsTemplates' / uid / (uid + '.santp')
            if target.exists():
                raise ValueError('That unit already exists in the project; open it instead')
            state = settings(context)
            s = context.scene.sanctuary_toolkit
            model, rig = s.export_model_object, s.animation_target_armature
            texture_values = {p: getattr(s, p) for p in UNIT_SETTINGS}
            if self.replace_existing:
                value = doc.set(('general', 'displayName'), json.dumps(self.title))
            else:
                from .lua_authoring import generate_unit
                faction_tag=mp.read_manifest(project).get('faction_tag')
                if not faction_tag:
                    stock_tags=set(doc.strings(('tags',))).intersection({'EDA','CHOSEN','GUARD'})
                    faction_tag=next(iter(stock_tags)) if len(stock_tags)==1 else ''
                value=generate_unit(doc.text,uid,self.title,faction_tag,
                                    own_model=bool(self.keep_assets and model and rig))
            lua_compile(value, engine(context))
            switch_unit(context, uid)
            if self.keep_assets:
                s.export_model_object, s.animation_target_armature = model, rig
                for p, v in texture_values.items():
                    setattr(s, p, v)
            use_existing_lua(context, value, donor)
            # Retain the donor shader settings; subsequent texture conversion
            # rewrites the three texture slots using the established converter.
            from .dds_tools import _read_game_material
            mat, _ = _read_game_material(donor, engine(context))
            if mat:
                destination = project / 'Units' / uid / 'LOD0' / (uid + '_lod0.sanmaterial')
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    mat['name'] = uid + '_lod0'
                    destination.write_text(json.dumps(mat, indent=2), encoding='utf-8')
            state.stage = 'BLUEPRINT'
            state.status = 'New unit draft ready. Review model, bones, faction tags and build filters, then save.'
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def settings(context):
    return context.scene.sanctuary_studio


def engine(context):
    prefs = get_addon_preferences(context)
    return resolve_engine_root(prefs.sanctuary_root) if prefs else None


def library(context, refresh=False):
    global _LIBRARY
    project = mp.active_project(context, required=False)
    root = engine(context)
    if refresh or _LIBRARY is None or _LIBRARY.project != project or _LIBRARY.engine != root:
        if _LIBRARY is not None:
            _LIBRARY.close()
        _LIBRARY = AssetLibrary(root, project).scan()
    return _LIBRARY


def source(context):
    text = settings(context).blueprint
    if not text:
        raise ValueError('Open a project blueprint or choose a template first')
    return text.as_string()


def set_source(context, value):
    Blueprint(value)
    state = settings(context)
    if not state.blueprint:
        state.blueprint = bpy.data.texts.new('Sanctuary Blueprint')
    state.blueprint.clear()
    state.blueprint.write(value)
    rebuild_fields(context)


def rebuild_fields(context):
    state = settings(context)
    state.fields.clear()
    for path, value in Blueprint(source(context)).leaves():
        item = state.fields.add()
        item.name = path_label(path)
        item.path = json.dumps(path)
        item.value = value
    state.field_index = min(state.field_index, max(0, len(state.fields) - 1))
    select_field_group(state, context)
    select_field(state, context)


def select_field(self, context):
    if not self.fields or self.field_index >= len(self.fields):
        return
    item = self.fields[self.field_index]
    self.field_value = item.value
    try:
        value = json.loads(item.value)
    except ValueError:
        value = Blueprint(source(context)).scalar(tuple(json.loads(item.path)))
    self.field_kind = ('BOOL' if isinstance(value, bool) else 'NUMBER' if isinstance(value, (int, float))
                       else 'STRING' if isinstance(value, str) else 'RAW')
    if self.field_kind == 'STRING':
        self.string_value = value
        self.custom_string = False
        if item.name == 'movement.type':
            self.movement_type = value if value in MOVEMENT_TYPES else '__CUSTOM__'
    elif self.field_kind == 'NUMBER':
        self.number_value = value
    elif self.field_kind == 'BOOL':
        self.bool_value = value
    if item.name.lower().endswith(('bonename', 'bone')) and isinstance(value, str):
        self.bone = value


def select_effect_field(self, context):
    if self.effect_fields and self.effect_field_index < len(self.effect_fields):
        self.effect_value = self.effect_fields[self.effect_field_index].value
        if context and hasattr(context.scene,'sanctuary_vfx'):
            from .studio_vfx import fields_selected
            fields_selected(context)


class StudioField(PropertyGroup):
    path: StringProperty()
    value: StringProperty()


class StudioUnit(PropertyGroup):
    model: PointerProperty(type=bpy.types.Object)
    armature: PointerProperty(type=bpy.types.Object)
    blueprint: PointerProperty(type=bpy.types.Text)
    blueprint_path: StringProperty()
    baseline: StringProperty()
    snapshot: StringProperty(default='{}')
    icon_scene: PointerProperty(type=bpy.types.Scene)


UNIT_SETTINGS = ('texture_albedo_png', 'texture_mask_png', 'texture_normal_png', 'texture_team_mask_png',
                 'texture_size', 'texture_mips', 'mask_input_mode', 'mask_default_smoothness',
                 'mask_ao_strength', 'albedo_alpha_mode', 'mod_unit_icon_png', 'swap_yz_axis')

ANIMATION_SETTINGS = ('enabled', 'clip_name', 'frame_start', 'frame_end', 'fps', 'loop',
                      'behaviour_role',
                      'source_evaluation', 'preserve_native_curves',
                      'source_pose_snapshot', 'source_constraint_snapshot')


def remember_unit(context):
    state = settings(context)
    s = context.scene.sanctuary_toolkit
    project = state.binding_project or str(mp.active_project(context, required=False) or '')
    key = project.casefold() + '::' + s.unit_id
    entry = state.unit_bindings.get(key)
    if entry is None:
        entry = state.unit_bindings.add()
        entry.name = key
    entry.model, entry.armature = s.export_model_object, s.animation_target_armature
    entry.blueprint, entry.blueprint_path, entry.baseline = state.blueprint, state.blueprint_path, state.baseline
    entry.icon_scene = state.icon_scene
    values = {p: getattr(s, p) for p in UNIT_SETTINGS}
    values['animations'] = [{p: getattr(item, p) for p in ANIMATION_SETTINGS}
                            | {'action': item.action.name if item.action else '',
                               'source_armature': item.source_armature.name if item.source_armature else ''}
                            for item in s.animations]
    entry.snapshot = json.dumps(values)


def switch_unit(context, uid):
    state = settings(context)
    s = context.scene.sanctuary_toolkit
    remember_unit(context)
    s.unit_id = uid
    state.binding_project = str(mp.active_project(context))
    entry = state.unit_bindings.get(state.binding_project.casefold() + '::' + uid)
    s.export_model_object = entry.model if entry else None
    s.animation_target_armature = entry.armature if entry else None
    state.blueprint = entry.blueprint if entry else None
    state.blueprint_path = entry.blueprint_path if entry else ''
    state.baseline = entry.baseline if entry else ''
    state.icon_scene = entry.icon_scene if entry else None
    s.animations.clear()
    for prop in UNIT_SETTINGS:
        s.property_unset(prop)
    if entry:
        values = json.loads(entry.snapshot)
        for prop in UNIT_SETTINGS:
            if prop in values:
                setattr(s, prop, values[prop])
        for data in values.get('animations', []):
            item = s.animations.add()
            item.action = bpy.data.actions.get(data.get('action', ''))
            item.source_armature = bpy.data.objects.get(data.get('source_armature', ''))
            for prop, value in data.items():
                if prop not in ('action', 'source_armature'):
                    setattr(item, prop, value)
    if state.blueprint:
        rebuild_fields(context)
    else:
        state.fields.clear()
        project = mp.active_project(context)
        path = project / 'Lua/common/units/unitsTemplates' / uid / (uid + '.santp')
        if path.is_file():
            load_blueprint(context, path)
    from .unit_icon_preview import bind_saved
    bind_saved(context)
    state.status = 'Active unit: ' + uid + '. Assets and animation list restored.'


def select_browser_effect(self, context):
    from .studio_vfx import select_asset
    select_asset(self, context)


def select_field_group(self, context):
    for i, item in enumerate(self.fields):
        if (self.field_group == 'ALL' or item.name.split('.')[0] == self.field_group) and self.filter.casefold() in item.name.casefold():
            self.field_index = i
            break


def sync_navigation(self, context):
    self.destination = self.stage if self.stage in ('PROJECT','BROWSER') else 'WORKSHOP'
    if self.stage in ('BLUEPRINT','EFFECTS'):
        self.workshop = 'BEHAVIOUR'
        self.behaviour_page = 'EFFECTS' if self.stage == 'EFFECTS' else 'FIELDS'
    elif self.stage == 'ICON': self.workshop = 'ICON'
    elif self.stage in ('PUBLISH','VALIDATE'): self.workshop = 'EXPORT'
    else: self.workshop = 'MODEL'


class StudioState(PropertyGroup):
    destination: EnumProperty(items=[('WORKSHOP', 'Workshop', 'Prepare the current unit'), ('BROWSER', 'Unit Browser', 'Browse vanilla and mod units'), ('PROJECT', 'Project', 'Faction and installation settings')], default='WORKSHOP')
    workshop: EnumProperty(items=[('MODEL', 'Model', ''), ('BEHAVIOUR', 'Behaviour', ''), ('ICON', 'Icon', ''), ('EXPORT', 'Export', '')])
    model_page: EnumProperty(items=[('ASSETS', 'Mesh & Textures', ''), ('ANIMATION', 'Animations', ''), ('SIZES', 'Size Guides', '')])
    behaviour_page: EnumProperty(items=[('FIELDS', 'Settings', ''), ('EFFECTS', 'Effects', ''), ('AUDIO', 'Sound', '')])
    show_camera: BoolProperty(name='Camera & lighting')
    show_references: BoolProperty(name='Vanilla references')
    show_source: BoolProperty(name='Change starting template')
    show_details: BoolProperty(name='Technical details')
    show_components: BoolProperty(name='Add a component')
    animation_role: EnumProperty(name='Play when', items=[('rest','Idle',''),('walk','Walking',''),('build','Building','')])
    custom_string: BoolProperty(name='Enter custom value', default=False)
    show_custom_tag: BoolProperty(name='Custom tag', default=False)
    new_tag: StringProperty(name='New tag')
    viewer_text: PointerProperty(type=bpy.types.Text)
    viewer_visible: BoolProperty(name='Show text here', default=False)
    viewer_page: IntProperty(name='Text page', default=1, min=1)
    last_saved_lua: StringProperty(subtype='FILE_PATH')
    movement_type: EnumProperty(name='Movement type', items=[(v, v, 'Game movement type: ' + v) for v in MOVEMENT_TYPES]
                               + [('__CUSTOM__', 'Keep custom value', 'Preserve the existing nonstandard movement name')])
    advanced: BoolProperty(name='Advanced options', default=False)
    show_animations: BoolProperty(name='Animations', default=False)
    lua_origin: StringProperty()
    binding_project: StringProperty()
    unit_bindings: CollectionProperty(type=StudioUnit)
    project_units: CollectionProperty(type=StudioField)
    project_unit_index: IntProperty()
    stage: EnumProperty(name='Workspace', items=[(v, label, '', number) for number, (v, label) in enumerate((
        ('PROJECT', 'Project'), ('BROWSER', 'Units'), ('ASSETS', 'Assets'), ('BLUEPRINT', 'Blueprint'),
        ('EFFECTS', 'Effects'), ('ICON', 'Icon'), ('VALIDATE', 'Validate'), ('PUBLISH', 'Publish'),
        ('HOME', 'Build Unit')))], default='HOME', update=sync_navigation)
    blueprint: PointerProperty(type=bpy.types.Text)
    blueprint_path: StringProperty(subtype='FILE_PATH')
    baseline: StringProperty()
    fields: CollectionProperty(type=StudioField)
    field_index: IntProperty(update=select_field)
    filter: StringProperty(name='Find field', options={'TEXTEDIT_UPDATE'}, update=select_field_group)
    field_group: EnumProperty(name='Section', items=[(v, n, '') for v, n in (
        ('ALL','All fields'),('general','Identity & classes'),('tags','Tags & build access'),
        ('economy','Economy'),('defence','Health & shields'),('weapons','Weapons'),
        ('visuals','Visuals & effects'),('audio','Sound'),('additions','Rotators & additions'),
        ('intel','Intel'),('movement','Movement'),('collisionInfo','Collision'),('construction','Construction'),('footprint','Footprint'),('skirtSize','Building skirt'))], default='general', update=select_field_group)
    field_value: StringProperty(name='Lua expression')
    field_kind: StringProperty(default='RAW')
    string_value: StringProperty(name='Value')
    number_value: FloatProperty(name='Value', precision=5)
    bool_value: BoolProperty(name='Enabled')
    bone: StringProperty(name='Bone')
    new_field: StringProperty(name='Field path', description='Dot-separated path; array indices start at 1')
    donor: StringProperty(name='Template Unit ID', default='ucs3401')
    section: EnumProperty(name='Component', items=[(p, n, '') for p, n in (
        ('weapons', 'Weapon'), ('defence.shields', 'Shield'), ('additions.rotators', 'Rotator'),
        ('visuals.effectsPersistent', 'Persistent VFX'), ('audio', 'Audio'),
        ('economy', 'Economy'), ('intel', 'Intel'), ('collisionInfo', 'Collision'))])
    donor_index: IntProperty(name='Source item', default=1, min=1)
    rotator_speed: FloatVectorProperty(name='Degrees / second (game XYZ)', size=3, default=(0, 15, 0))
    effect_search: StringProperty(name='Find effect / material', options={'TEXTEDIT_UPDATE'})
    effects: CollectionProperty(type=StudioField)
    effect_index: IntProperty(update=select_browser_effect)
    effect_asset: StringProperty(name='Asset path')
    effect_name: StringProperty(name='Custom effect name', default='my_effect')
    effect_document: PointerProperty(type=bpy.types.Text)
    effect_baseline: StringProperty()
    effect_fields: CollectionProperty(type=StudioField)
    effect_field_index: IntProperty(update=select_effect_field)
    effect_filter: StringProperty(name='Find property', options={'TEXTEDIT_UPDATE'})
    effect_value: StringProperty(name='JSON value')
    effect_color: FloatVectorProperty(name='Particle tint', subtype='COLOR', size=3, min=0, max=1, default=(0.1, 0.6, 1))
    effect_scale: FloatProperty(name='Size multiplier', default=1, min=0.01, max=100)
    effect_texture: StringProperty(name='Replacement PNG', subtype='FILE_PATH')
    effect_texture_size: EnumProperty(name='Texture size', items=[(str(n), str(n), '') for n in (256, 512, 1024, 2048)], default='1024')
    shield_index: IntProperty(name='Shield number', default=1, min=1)
    bind_path: StringProperty(name='Effect list path', default='visuals.effectsPersistent')
    preview_offset: FloatVectorProperty(name='Preview offset (game XYZ)', size=3)
    audio_file: StringProperty(name='Audio file', subtype='FILE_PATH')
    audio_event: StringProperty(name='Game audio event')
    audio_path: StringProperty(name='Audio field', default='audio.onDestroyed')
    audio_loop: BoolProperty(name='Loop')
    icon_scene: PointerProperty(type=bpy.types.Scene)
    icon_size: EnumProperty(name='Resolution', items=[(str(n), str(n) + ' px', '') for n in (128, 256, 512)], default='128')
    report: PointerProperty(type=bpy.types.Text)
    status: StringProperty(default='Create or open a project to begin')
    faction_prefix: StringProperty(name='New faction letter', maxlen=1)
    faction_tag: StringProperty(name='Faction tag')
    faction_name: StringProperty(name='Faction name')
    faction_project: StringProperty(name='New project name', default='My Faction')


class SANCTUARY_UL_studio_fields(UIList):
    def filter_items(self, context, data, propname):
        state = settings(context)
        query = state.filter.casefold()
        return [self.bitflag_filter_item if query in item.name.casefold()
                and (state.field_group == 'ALL' or item.name.split('.')[0] == state.field_group) else 0
                for item in getattr(data, propname)], []

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.label(text=item.name, icon='BONE_DATA' if 'bone' in item.name.lower() else 'DOT')
        row.label(text=item.value[:65])


class SANCTUARY_UL_studio_effects(UIList):
    def filter_items(self, context, data, propname):
        query = settings(context).effect_search.casefold()
        category = getattr(getattr(context.scene, 'sanctuary_vfx', None), 'category', 'ALL')
        from .vfx_browser_cache import filter_rows
        return filter_rows(getattr(data,propname),query,category,self.bitflag_filter_item)

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        from .vfx_browser_cache import row
        title=row(item).get('name',item.name)
        layout.label(text=title, icon='PARTICLES' if item.name.endswith('.sanvfx') else 'TRACKING_FORWARDS' if item.name.startswith('projectile:') else 'MATERIAL')


class SANCTUARY_UL_studio_units(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.label(text=item.name.upper(), icon='OBJECT_DATA')
        row.label(text=item.value)


class SANCTUARY_UL_studio_effect_fields(UIList):
    def filter_items(self, context, data, propname):
        query = settings(context).effect_filter.casefold()
        return [self.bitflag_filter_item if query in item.name.casefold() else 0
                for item in getattr(data, propname)], []

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.label(text=item.name)
        row.label(text=item.value[:45])


def split_path(value):
    return tuple(int(p) if p.isdigit() else p for p in value.strip().split('.') if p)


def load_blueprint(context, path):
    state = settings(context)
    if state.blueprint and state.blueprint.as_string() != state.baseline:
        # Preserve the actual Text datablock and keep a retrievable fake user.
        state.blueprint.use_fake_user = True
    text = Path(path).read_text(encoding='utf-8-sig')
    state.blueprint = bpy.data.texts.new(Path(path).name)
    state.blueprint.use_fake_user = True
    state.blueprint_path = str(path)
    state.baseline = text
    state.binding_project = str(mp.active_project(context, required=False) or '')
    set_source(context, text)


def donor_document(context):
    from .studio_donors import resolve
    record = resolve(gb.load_database(), settings(context).donor, mp.active_project(context, required=False))
    return Blueprint(Path(record['template_path']).read_text(encoding='utf-8-sig'))


def effect_fields(context):
    state = settings(context)
    state.effect_fields.clear()
    value = json.loads(state.effect_document.as_string())
    for path, leaf in json_leaves(value):
        item = state.effect_fields.add()
        item.name, item.path, item.value = path_label(path), json.dumps(path), json.dumps(leaf)
    state.effect_field_index = min(state.effect_field_index, max(0, len(state.effect_fields) - 1))
    select_effect_field(state, context)


def write_effect_text(context, value):
    state = settings(context)
    state.effect_document.clear()
    state.effect_document.write(json.dumps(value, indent=2))
    effect_fields(context)


def _preview_attachment(context):
    state = settings(context)
    rig = context.scene.sanctuary_toolkit.animation_target_armature
    if not rig or state.bone not in rig.data.bones:
        raise ValueError('Pin an armature and choose an existing bone')
    collection = bpy.data.collections.new('Studio • VFX attachment preview')
    context.scene.collection.children.link(collection)
    marker = bpy.data.objects.new('VFX • ' + Path(state.effect_asset).stem, None)
    collection.objects.link(marker)
    marker.empty_display_type = 'ARROWS'
    marker.empty_display_size = 2
    marker.show_in_front = True
    # Constraint tracks the bone origin; ordinary bone parenting adds tail offset.
    constraint = marker.constraints.new('COPY_TRANSFORMS')
    constraint.target, constraint.subtarget = rig, state.bone
    marker['studio_preview'] = True
    marker['particleName'] = state.effect_asset
    offset = bpy.data.objects.new('Effect origin (approximation)', None)
    collection.objects.link(offset)
    offset.parent = marker
    x, y, z = state.preview_offset
    offset.location = (x, z, y) if context.scene.sanctuary_toolkit.swap_yz_axis else (x, y, z)
    offset.empty_display_type = 'SPHERE'
    offset.empty_display_size = state.effect_scale
    offset.show_in_front = True
    state.status = 'Attachment follows the bone. Sphere indicates size, not the game shader.'


class SANCTUARY_OT_studio_action(Operator):
    bl_idname = 'sanctuary.studio_action'
    bl_label = 'Studio Action'
    bl_description = 'Perform the selected Sanctuary Studio workflow step'
    action: StringProperty()

    def execute(self, context):
        state = settings(context)
        s = context.scene.sanctuary_toolkit
        try:
            self.run(context, state, s)
            if context.area:
                context.area.tag_redraw()
            return {'FINISHED'}
        except Exception as exc:
            state.status = str(exc)
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def run(self, context, state, s):
        action = self.action
        if action == 'OPEN':
            existing = {w.as_pointer() for w in context.window_manager.windows}
            bpy.ops.screen.area_dupli('EXEC_DEFAULT')
            added = [w for w in context.window_manager.windows if w.as_pointer() not in existing]
            if not added:
                raise ValueError('Blender could not open a detached Studio window')
            for area in added[0].screen.areas:
                area.type = 'PROPERTIES'
                area.spaces.active.context = 'SCENE'
            state.status = 'Detached Studio window ready; resize it to suit your workspace'
        elif action == 'USE_SELECTED':
            obj = context.active_object
            if obj and obj.type == 'MESH':
                s.export_model_object = obj
                if obj.find_armature():
                    s.animation_target_armature = obj.find_armature()
            elif obj and obj.type == 'ARMATURE':
                s.animation_target_armature = obj
            else:
                raise ValueError('Select the final mesh or armature')
            state.status = 'Pinned ' + obj.name + ' for export'
        elif action == 'PROJECT_UNITS':
            manifest = mp.read_manifest(mp.active_project(context))
            state.project_units.clear()
            for record in manifest.get('units', []):
                if record.get('unit_id'):
                    item = state.project_units.add()
                    item.name = record['unit_id']
                    item.value = record.get('display_name', '')
            state.status = f'{len(state.project_units)} units in this project'
        elif action == 'PROJECT_FOLDER':
            bpy.ops.wm.path_open(filepath=str(mp.active_project(context)))
        elif action == 'REPAIR_LUA':
            from .project_blueprints import ensure_project_templates
            count = ensure_project_templates(mp.active_project(context), engine(context))
            mp._refresh_browser(context)
            state.status = f'Created {count} project Lua copies. Existing Lua edits were kept unchanged.'
        elif action == 'SWITCH_UNIT':
            if not state.project_units:
                raise ValueError('Refresh project units first')
            switch_unit(context, state.project_units[state.project_unit_index].name)
        elif action == 'LOAD':
            project = mp.active_project(context)
            uid = safe_unit_id(s.unit_id)
            path = project / 'Lua/common/units/unitsTemplates' / uid / (uid + '.santp')
            if not path.is_file():
                from .project_blueprints import ensure_template
                path = ensure_template(project, uid, engine(context))
            load_blueprint(context, path)
            state.stage = 'BLUEPRINT'
        elif action == 'DONOR_SELECTED':
            uid = s.browser_selected_unit
            if not uid:
                raise ValueError('Select a unit in the Unit Browser')
            state.donor = uid
            use_existing_lua(context, donor_document(context).text, uid)
        elif action == 'TEMPLATE':
            project = mp.active_project(context)
            uid = safe_unit_id(s.unit_id)
            if not uid:
                raise ValueError('Enter a valid target Unit ID')
            doc = donor_document(context)
            donor = str(doc.scalar(('general', 'tpId'), state.donor))
            value = doc.text.replace(donor, uid)
            path = project / 'Lua/common/units/unitsTemplates' / uid / (uid + '.santp')
            if path.exists():
                raise ValueError('Blueprint already exists; open it instead')
            use_existing_lua(context, value, donor)
        elif action == 'REFRESH':
            rebuild_fields(context)
        elif action == 'SOURCE':
            if not state.blueprint:
                raise ValueError('Open a blueprint first')
            open_text_window(context, state.blueprint)
        elif action == 'FIELD':
            if not state.fields:
                raise ValueError('Choose a blueprint field')
            item = state.fields[state.field_index]
            raw = state.field_value
            if item.name == 'movement.type' and state.movement_type != '__CUSTOM__':
                raw = json.dumps(state.movement_type)
            elif item.name.lower().endswith(('bonename', 'bone')):
                raw = json.dumps(state.bone)
            elif state.field_kind == 'STRING':
                raw = json.dumps(state.string_value)
            elif state.field_kind == 'NUMBER':
                raw = str(int(state.number_value)) if state.number_value.is_integer() else repr(state.number_value)
            elif state.field_kind == 'BOOL':
                raw = 'true' if state.bool_value else 'false'
            value = Blueprint(source(context)).set(tuple(json.loads(item.path)), raw)
            lua_compile(value, engine(context))
            set_source(context, value)
        elif action == 'ADD_FIELD':
            value = Blueprint(source(context)).set(split_path(state.new_field), state.field_value)
            lua_compile(value, engine(context))
            set_source(context, value)
        elif action == 'ADD_TAG':
            add_lua_tag(context, state.new_tag)
        elif action == 'REMOVE_FIELD':
            item = state.fields[state.field_index]
            set_source(context, Blueprint(source(context)).remove(tuple(json.loads(item.path))))
        elif action == 'COMPONENT':
            donor = donor_document(context)
            path = split_path(state.section)
            list_component = state.section in ('weapons', 'defence.shields', 'additions.rotators', 'visuals.effectsPersistent')
            raw = donor.raw(path + (state.donor_index,)) if list_component else donor.raw(path)
            if raw is None:
                raise ValueError('That template does not contain this component/item')
            doc = Blueprint(source(context))
            value = doc.append(path, raw) if list_component else doc.set(path, raw)
            lua_compile(value, engine(context))
            set_source(context, value)
            state.status = 'Component copied with its original settings; review bone names and asset references'
        elif action == 'ROTATOR':
            rig = s.animation_target_armature
            if not rig or state.bone not in rig.data.bones:
                raise ValueError('Choose a bone on the pinned armature')
            x, y, z = state.rotator_speed
            raw = '{ boneName = ' + json.dumps(state.bone) + f', speed = {{ x = {x}, y = {y}, z = {z} }} }}'
            set_source(context, Blueprint(source(context)).append(('additions', 'rotators'), raw))
        elif action == 'SAVE':
            from .animation_assignment import assign_text
            value = ensure_builder_skirt(assign_text(source(context), s.unit_id, s.animations))
            doc = Blueprint(value)
            compiled, message = lua_compile(value, engine(context))
            project = mp.active_project(context).resolve()
            path = Path(state.blueprint_path).resolve()
            from .project_blueprints import checked_path
            checked_path(project, path, engine(context))
            if project not in path.parents or path.suffix not in ('.santp', '.lua'):
                raise ValueError('Blueprint destination must be inside the active project')
            uid = str(doc.scalar(('general', 'tpId'), ''))
            if uid != s.unit_id or uid != path.stem:
                raise ValueError('Blueprint tpId, export Unit ID and filename must match')
            if path.exists():
                disk = path.read_text(encoding='utf-8-sig')
                if disk != state.baseline:
                    raise ValueError('Blueprint changed on disk; reopen it before saving. Your Text draft is preserved.')
                backup = project / '.smt_history' / datetime.now().strftime('%Y%m%d-%H%M%S-%f') / path.name
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding='utf-8')
            set_source(context, value)
            state.baseline = value
            state.last_saved_lua = str(path)
            manifest = mp.read_manifest(project)
            entry = next((u for u in manifest.setdefault('units', []) if u.get('unit_id') == uid), None)
            if entry is None:
                entry = {'unit_id': uid}
                manifest['units'].append(entry)
            entry.update(display_name=doc.scalar(('general', 'displayName'), uid), template=path.relative_to(project).as_posix())
            mp.write_manifest(project, manifest)
            remember_unit(context)
            state.status = 'Blueprint saved • ' + message
        elif action == 'INDEX_EFFECTS':
            assets = library(context, True)
            state.effects.clear()
            for key in sorted(assets.records):
                if key.endswith(('.sanvfx', '.sanbeam', '.sanmaterial', '.sandecal')):
                    item = state.effects.add()
                    item.name = key
            state.status = f'{len(state.effects)} effects and materials indexed'
        elif action == 'LOAD_EFFECT':
            if not state.effects:
                raise ValueError('Index effects first')
            state.effect_asset = state.effects[state.effect_index].name
            data = library(context).json(state.effect_asset)
            state.effect_document = bpy.data.texts.new(Path(state.effect_asset).name)
            state.effect_document.use_fake_user = True
            write_effect_text(context, data)
            state.effect_baseline = state.effect_document.as_string()
        elif action == 'CLONE_EFFECT':
            dest, count = library(context).clone(state.effect_asset, state.effect_name)
            state.effect_asset = dest
            state.effect_document = bpy.data.texts.new(Path(dest).name)
            state.effect_document.use_fake_user = True
            write_effect_text(context, library(context).json(dest))
            state.effect_baseline = state.effect_document.as_string()
            state.status = f'Custom effect created with {count} dependency files'
        elif action == 'EFFECT_FIELD':
            item = state.effect_fields[state.effect_field_index]
            data = json.loads(state.effect_document.as_string())
            replacement = json.loads(state.effect_value)
            old = json.loads(item.value)
            if type(old) is not type(replacement) and not (type(old) in (int, float) and type(replacement) in (int, float)):
                raise ValueError('Keep the original property type')
            write_effect_text(context, json_set(data, json.loads(item.path), replacement))
        elif action == 'TINT_EFFECT':
            data = json.loads(state.effect_document.as_string())
            count = 0
            strengths = {}
            for path, value in list(json_leaves(data)):
                if path[-1] in ('r', 'g', 'b') and any('color' in str(k).lower() for k in path):
                    parent = path[:-1]
                    if parent not in strengths:
                        target = data
                        for key in parent:
                            target = target[key]
                        strengths[parent] = max(1, *(abs(target.get(k, 0)) for k in 'rgb'))
                    data = json_set(data, path, state.effect_color['rgb'.index(path[-1])] * strengths[parent])
                    count += 1
            if not count:
                raise ValueError('No particle RGB fields in this asset. Inspect its material color properties.')
            write_effect_text(context, data)
            state.status = f'Tinted {count // 3} RGB values; texture color and shader behavior still affect appearance'
        elif action == 'SAVE_EFFECT':
            project = mp.active_project(context)
            path = project / safe_member(state.effect_asset)
            if not path.is_file():
                raise ValueError('Make a custom copy before saving stock effects')
            data = json.loads(state.effect_document.as_string())
            disk = json.dumps(json.loads(path.read_text(encoding='utf-8-sig')), indent=2)
            if disk != state.effect_baseline:
                raise ValueError('Effect changed on disk; reload it before saving')
            backup = project / '.smt_history' / datetime.now().strftime('%Y%m%d-%H%M%S-%f') / path.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup)
            path.write_text(json.dumps(data, indent=2), encoding='utf-8')
            state.effect_baseline = json.dumps(data, indent=2)
            state.status = 'Custom effect saved'
        elif action == 'BIND_EFFECT':
            if not state.effect_asset.lower().endswith('.sanvfx'):
                raise ValueError('Select a .sanvfx effect for a particle binding')
            rig = s.animation_target_armature
            if not rig or state.bone not in rig.data.bones:
                raise ValueError('Choose an existing bone on the pinned armature')
            library(context).read(state.effect_asset)
            raw = '{ boneName = ' + json.dumps(state.bone) + ', particleName = ' + json.dumps(state.effect_asset) + ' }'
            set_source(context, Blueprint(source(context)).append(split_path(state.bind_path), raw))
            state.status = 'Effect added to blueprint draft. Save the blueprint to export the binding.'
        elif action == 'PREVIEW_EFFECT':
            _preview_attachment(context)
        elif action == 'PLAY_EFFECT':
            from .studio_preview import create_preview
            if not state.effect_document:
                raise ValueError('Inspect an effect first')
            create_preview(context, state.effect_asset, json.loads(state.effect_document.as_string()), library(context))
        elif action == 'RETURN_SCENE':
            name = context.scene.get('studio_source_scene')
            if not name or name not in bpy.data.scenes:
                raise ValueError('Source scene is no longer available')
            context.window.scene = bpy.data.scenes[name]
        elif action == 'REPLACE_EFFECT_TEXTURE':
            from .dds_tools import _load_png_rgba, write_bc3_dds
            if not state.effect_asset.endswith('.sanmaterial'):
                raise ValueError('Inspect a custom .sanmaterial first')
            item = state.effect_fields[state.effect_field_index]
            path = json.loads(item.path)
            if not path or path[-1] != 'path' or path[0] != 'textures':
                raise ValueError('Select a textures.N.path field first')
            project = mp.active_project(context)
            material_path = project / safe_member(state.effect_asset)
            if not material_path.is_file():
                raise ValueError('Create a custom material copy first')
            png = Path(bpy.path.abspath(state.effect_texture))
            if not png.is_file():
                raise ValueError('Choose a replacement PNG texture')
            rgba, _ = _load_png_rgba(str(png), int(state.effect_texture_size))
            target = material_path.parent / (png.stem + '_studio.dds')
            if target.exists():
                raise ValueError('That texture filename already exists; choose a uniquely named PNG')
            write_bc3_dds(target, rgba, kind='albedo', full_mips=False)
            data = json.loads(state.effect_document.as_string())
            write_effect_text(context, json_set(data, path, target.relative_to(project).as_posix()))
            library(context, True)
            state.status = 'Replacement texture converted. Save the material to commit its reference.'
        elif action == 'BIND_SHIELD_MATERIAL':
            bind_shield_material(context)
        elif action == 'BIND_AUDIO':
            if not state.audio_event.strip():
                raise ValueError('Enter a game audio event name')
            raw = '{ name = ' + json.dumps(state.audio_event) + ', looping = ' + ('true' if state.audio_loop else 'false') + ' }'
            set_source(context, Blueprint(source(context)).set(split_path(state.audio_path), raw))
        elif action == 'PLAY_AUDIO':
            import aud
            path = Path(bpy.path.abspath(state.audio_file))
            if not path.is_file():
                raise ValueError('Choose a playable WAV, OGG or MP3 file')
            global _AUDIO_DEVICE, _AUDIO_HANDLE
            _AUDIO_DEVICE = aud.Device()
            _AUDIO_HANDLE = _AUDIO_DEVICE.play(aud.Sound(str(path)))
        elif action == 'ICON_SETUP':
            create_icon_scene(context)
        elif action == 'ICON_EDIT':
            if not state.icon_scene:
                raise ValueError('Create the Icon Studio scene first')
            context.window.scene = state.icon_scene
            from .icon_camera import restore_view
            restore_view(context, context.window.scene)
        elif action == 'ICON_RENDER':
            render_icon(context)
        elif action == 'ICON_SAVE':
            _, _, sprite = mp.save_unit_icon(context)
            state.status = 'Icon pair saved to project: ' + str(sprite.parent) + '. Deploy separately when ready.'
        elif action == 'ICON_REPAIR':
            from .unit_icons import repair_missing
            count = repair_missing(mp.icon_project(context))
            state.status = f'Created {count} missing sprite descriptors in the project. Live game unchanged; deploy when ready.'
        elif action == 'VALIDATE':
            from .studio_validation import validate
            validate(context, roundtrip=False)
        elif action == 'ROUNDTRIP':
            from .studio_validation import validate
            validate(context, roundtrip=True)
        elif action == 'REPORT':
            open_text_window(context, state.report)
        elif action == 'COPY_VIEWER':
            if not state.viewer_text:
                raise ValueError('No text is open')
            context.window_manager.clipboard = state.viewer_text.as_string()
            state.status = 'Full text copied to clipboard'
        elif action == 'VIEW_SAVED_LUA':
            path = saved_lua_path(context)
            saved = bpy.data.texts.new('Saved on disk • ' + path.name)
            saved.write(path.read_text(encoding='utf-8-sig'))
            saved['studio_disk_path'] = str(path)
            open_text_window(context, saved)
        elif action == 'LUA_FOLDER':
            bpy.ops.wm.path_open(filepath=str(saved_lua_path(context).parent))
        elif action == 'PROJECT_TEXTURES':
            from .dds_tools import convert_texture_set
            project = mp.active_project(context)
            uid = safe_unit_id(s.unit_id)
            if not uid:
                raise ValueError('Set a valid Unit ID')
            destination = project / 'Units' / uid / 'LOD0'
            destination.mkdir(parents=True, exist_ok=True)
            outputs = convert_texture_set(s, destination, uid)
            state.status = f'Converted {len(outputs)} textures into {destination}'
        elif action == 'BUILD':
            if state.blueprint:
                result = bpy.ops.sanctuary.studio_action(action='SAVE')
                if result != {'FINISHED'}:
                    raise ValueError('Blueprint could not be saved; resolve the reported error')
            self.run_export(context, state)
        elif action == 'EXPORT':
            self.run_export(context, state)
        elif action == 'CONVERT_FACTION':
            from .studio_faction import convert
            destination = convert(context)
            state.status = 'Converted project: ' + str(destination)
        else:
            raise ValueError('Unknown Studio action: ' + action)

    def run_export(self, context, state):
        from .project_blueprints import prepare_active_unit
        path = prepare_active_unit(context)
        if not state.blueprint:
            load_blueprint(context, path)
        from .animation_assignment import sync_saved
        sync_saved(context, path)
        from .studio_validation import validate
        if not validate(context, roundtrip=False):
            raise ValueError('Export blocked: open the validation report for the exact errors')
        destination = mp.export_active_unit(context)
        state.status = 'Unit exported to ' + str(destination) + '. Existing icon preserved. Deploy separately when ready.'


def saved_lua_path(context):
    uid = safe_unit_id(context.scene.sanctuary_toolkit.unit_id)
    if not uid:
        raise ValueError('Set a valid Unit ID first')
    from .project_blueprints import plan_template
    plan = plan_template(mp.active_project(context), uid, engine(context))
    path = plan[0]
    if plan[2] or not path.is_file():
        raise ValueError('No saved Lua for this unit. Use Save first.')
    return path


def forest_environment_path():
    candidates = [Path(bpy.utils.system_resource('DATAFILES')) / 'studiolights/world/forest.exr']
    for light in bpy.context.preferences.studio_lights:
        if light.name.lower() == 'forest.exr':
            candidates.append(Path(light.path))
    for path in candidates:
        if path.is_file():
            return path
    raise FileNotFoundError('Blender forest.exr was not found. Restore its bundled studio light first.')


def create_icon_scene(context):
    state = settings(context)
    obj = context.scene.sanctuary_toolkit.export_model_object
    if not obj:
        raise ValueError('Pin the final model on the Assets stage')
    forest_path = forest_environment_path()
    from .icon_camera import preset, configure
    camera_preset = preset(context)  # Check saved settings before creating data.
    scene = bpy.data.scenes.new('Sanctuary • Icon Studio')
    state.icon_scene = scene
    scene['studio_source_scene'] = context.scene.name
    scene['studio_icon_unit'] = context.scene.sanctuary_toolkit.unit_id
    scene.sanctuary_studio.stage = 'ICON'
    # Bake the evaluated pose into a separate object, preserving materials.
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(context.evaluated_depsgraph_get()))
    clone = bpy.data.objects.new('Icon Subject', mesh)
    scene.collection.objects.link(clone)
    clone.matrix_world = obj.matrix_world.copy()
    scene.view_layers[0].update()
    corners = [clone.matrix_world @ Vector(c) for c in clone.bound_box]
    center = sum(corners, Vector()) / 8
    radius = max((c - center).length for c in corners)
    # Keep the isolated subject at world X/Y zero so the 180-degree orbit is
    # literally around world Z, without moving the artist's original object.
    transform = clone.matrix_world.copy()
    transform.translation.x -= center.x
    transform.translation.y -= center.y
    clone.matrix_world = transform
    center.x = center.y = 0
    camera_data = bpy.data.cameras.new('Icon Camera')
    camera = bpy.data.objects.new('Icon Camera', camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    configure(scene, center, radius, camera_preset)
    scene.world = bpy.data.worlds.new('Icon • forest.exr only')
    scene.world.use_nodes = True
    nodes = scene.world.node_tree.nodes
    environment = nodes.new('ShaderNodeTexEnvironment')
    environment.image = bpy.data.images.load(str(forest_path), check_existing=True)
    coordinates = nodes.new('ShaderNodeTexCoord')
    mapping = nodes.new('ShaderNodeMapping')
    mapping.name = 'Forest Rotation'
    mapping.label = 'forest.exr • 180° around Z'
    mapping.inputs['Rotation'].default_value.z = math.pi
    scene.world.node_tree.links.new(coordinates.outputs['Generated'], mapping.inputs['Vector'])
    scene.world.node_tree.links.new(mapping.outputs['Vector'], environment.inputs['Vector'])
    scene.world.node_tree.links.new(environment.outputs['Color'], nodes['Background'].inputs['Color'])
    nodes['Background'].inputs['Strength'].default_value = 1.0
    # Cycles samples HDR illumination without added lights or Eevee light probes.
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 24
    scene.cycles.use_denoising = True
    scene['studio_icon_environment'] = str(forest_path)
    scene.render.film_transparent = True
    scene.render.resolution_percentage = 100
    scene.render.resolution_x = scene.render.resolution_y = int(state.icon_size)
    from .icon_camera import fit_subject
    fit_subject(scene)
    scene.view_layers[0].update()
    state.status = 'Icon scene ready: forest.exr only, no lights. Adjust the camera, then return to render.'


def bind_shield_material(context):
    """Generate a narrow opt-in append supported by both export formats."""
    state = settings(context)
    if not state.effect_asset.lower().endswith('.sanmaterial'):
        raise ValueError('Select a shield .sanmaterial, not a particle effect')
    project = mp.active_project(context)
    library(context).read(state.effect_asset)
    doc = Blueprint(source(context))
    path = ('defence', 'shields', state.shield_index)
    if not doc.span(path):
        raise ValueError('The blueprint has no shield at that index')
    root = engine(context)
    if not root:
        raise ValueError('Set the game folder first')
    from .shield_support import ensure_support
    ensure_support(project,root)
    set_source(context, doc.set(path + ('materialPath',), json.dumps(state.effect_asset)))
    state.status = 'Shield material assigned. Both release formats include the opt-in support; save the unit and export together.'


def render_icon(context):
    state = settings(context)
    s = context.scene.sanctuary_toolkit
    if not state.icon_scene:
        raise ValueError('Set up the Icon Studio first')
    project = mp.icon_project(context)
    scene = state.icon_scene
    uid = safe_unit_id(s.unit_id)
    if not uid or scene.get('studio_icon_unit', '').lower() != uid:
        raise ValueError('This icon scene belongs to another unit. Select its unit or create a new icon scene.')
    if not scene.get('studio_icon_environment'):
        raise ValueError('Create a new Forest-Lit Icon Scene first. This scene uses the old lighting setup and has not been changed.')
    scene.render.resolution_x = scene.render.resolution_y = int(state.icon_size)
    from .unit_icons import owned
    path = owned(project, 'Source/Icons/' + uid + '.png')
    path.parent.mkdir(parents=True, exist_ok=True)
    scene.render.image_settings.file_format = 'PNG'
    scene.render.image_settings.color_mode = 'RGBA'
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True, scene=scene.name)
    s.mod_unit_icon_png = str(path)
    _, _, sprite = mp.save_unit_icon(context)
    state.status = 'PNG, DDS and sprite saved to project: ' + str(sprite.parent) + '. Deploy separately when ready.'


def button(layout, action, label, icon='NONE'):
    layout.operator('sanctuary.studio_action', text=label, icon=icon).action = action


def card(layout, title, icon='NONE'):
    box = layout.box()
    box.label(text=title, icon=icon)
    return box


def bone_control(layout, context):
    state = settings(context)
    rig = context.scene.sanctuary_toolkit.animation_target_armature
    if rig:
        layout.prop_search(state, 'bone', rig.data, 'bones', icon='BONE_DATA')
    else:
        layout.label(text='Pin an armature in Assets to choose bones', icon='INFO')


def draw_lua_choice(layout, context):
    state = settings(context)
    box = card(layout, 'Use an existing unit Lua', 'TEXT')
    box.operator('sanctuary.studio_donor', text='Choose Game / Mod Unit Lua...', icon='VIEWZOOM')
    box.operator('sanctuary.studio_lua_file', text='Browse Lua File...', icon='FILE_FOLDER')
    button(box, 'LOAD', 'Load This Unit’s Saved Project Lua', 'FILE_REFRESH')
    box.label(text='Selection loads the Lua immediately for your Unit ID.')
    if state.lua_origin:
        box.label(text='Using: ' + state.lua_origin)
    if state.blueprint:
        uid = context.scene.sanctuary_toolkit.unit_id
        try:
            doc_id = Blueprint(state.blueprint.as_string()).scalar(('general', 'tpId'))
        except (ValueError, IndexError, KeyError):
            doc_id = None
        if doc_id != uid or Path(state.blueprint_path).stem != uid:
            row = box.row(); row.alert = True
            row.label(text='Lua belongs to another unit. Choose the correct Lua above.', icon='ERROR')
        else:
            box.label(text='Loaded for ' + uid, icon='CHECKMARK')


def draw_asset_inputs(layout, context):
    s = context.scene.sanctuary_toolkit
    box = card(layout, 'Model & rig', 'OUTLINER_OB_ARMATURE')
    box.prop(s, 'export_model_object', text='Mesh')
    box.prop(s, 'animation_target_armature', text='Armature')
    button(box, 'USE_SELECTED', 'Use Selected Mesh / Armature', 'EYEDROPPER')
    box = card(layout, 'Textures', 'TEXTURE')
    for prop, title in (('texture_albedo_png','Albedo'),('texture_mask_png','Mask / AO'),('texture_normal_png','Normal')):
        from .ui import _draw_texture_input
        _draw_texture_input(box, s, prop, title, {'texture_albedo_png':'ALBEDO','texture_mask_png':'MASK','texture_normal_png':'NORMAL'}[prop])
    box.prop(s, 'mask_input_mode')
    box.prop(s, 'albedo_alpha_mode', text='Player colour source')
    if s.albedo_alpha_mode == 'TEAM_MASK':
        box.prop(s, 'texture_team_mask_png', text='Player colour mask')
    row = box.row(align=True)
    row.operator('sanctuary.texture_size_step', text='', icon='REMOVE').direction = -1
    row.label(text=f'Resolution: {s.texture_size} × {s.texture_size}')
    row.operator('sanctuary.texture_size_step', text='', icon='ADD').direction = 1
    button(box, 'PROJECT_TEXTURES', 'Convert Textures Into Project', 'IMAGE_DATA')
    box.label(text='Unit export also converts these inputs automatically.')


def draw_export_actions(layout, context):
    s = context.scene.sanctuary_toolkit
    box = card(layout, 'Export & deploy', 'EXPORT')
    row = box.row(); row.scale_y = 1.35
    button(row, 'BUILD', 'Save Lua & Export Complete Unit', 'EXPORT')
    box.label(text='Exports mesh, rig, listed animations and textures.')
    box.label(text='Preserves the existing icon. Use Icon > Render & Save Unit Icon to replace it.', icon='INFO')
    box.operator('sanctuary.studio_deploy', text='Deploy Exported Project to SSS...', icon='PLAY')
    box.label(text='Deployment is separate and asks for confirmation.')
    project = mp.active_project(context, required=False)
    if project:
        box.label(text='Project: ' + project.name)
        button(box, 'PROJECT_FOLDER', 'Open Export Folder', 'FILE_FOLDER')
    box = card(layout, 'Only need the mesh?', 'MESH_DATA')
    box.operator('sanctuary.studio_mesh_export', text='Export Mesh As .sanmodel...', icon='EXPORT')
    box.label(text='No Lua or mod project required for mesh-only export.')


def draw_saved_lua(layout, context):
    s = context.scene.sanctuary_toolkit
    project = mp.active_project(context, required=False)
    uid = safe_unit_id(s.unit_id)
    if not project or not uid:
        return
    path = project / 'Lua/common/units/unitsTemplates' / uid / (uid + '.santp')
    box = card(layout, 'Saved Lua on disk', 'FILE_TICK')
    import textwrap
    for line in textwrap.wrap(str(path), 52):
        box.label(text=line)
    if path.is_file():
        button(box, 'VIEW_SAVED_LUA', 'View Saved Lua', 'TEXT')
        button(box, 'LUA_FOLDER', 'Open Containing Folder', 'FILE_FOLDER')
        box.label(text='Reads the disk file, not your unsaved draft.')
    else:
        box.label(text='Not saved here yet. Use Save Lua first.', icon='INFO')


def draw_legacy_studio(layout, context):
    state = settings(context)
    s = context.scene.sanctuary_toolkit
    layout.use_property_split = False
    header = layout.column(align=True)
    header.label(text='SANCTUARY  /  STUDIO', icon='WORKSPACE')
    tagline = header.row(align=True)
    tagline.label(text='2.0.8 Preview • Build → Export → Deploy')
    if context.area and context.area.type == 'VIEW_3D':
        button(tagline, 'OPEN', '', 'WINDOW')
    nav = layout.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
    for key, title in (('HOME','Build Unit'),('PROJECT','Project'),('BROWSER','Units'),('ASSETS','Assets'),('BLUEPRINT','Lua'),
                       ('EFFECTS','Effects'),('ICON','Icon'),('VALIDATE','Test'),('PUBLISH','Export')):
        nav.prop_enum(state, 'stage', key, text=title)
    if context.scene.get('studio_source_scene'):
        button(layout, 'RETURN_SCENE', 'Return to Source Scene', 'BACK')
    identity = layout.row(align=True)
    identity.prop(s, 'unit_id', text='Unit')
    button(identity, 'USE_SELECTED', '', 'EYEDROPPER')
    layout.prop(state, 'advanced', text='Show Advanced Options')
    if state.stage == 'HOME':
        project = mp.active_project(context, required=False)
        if not project or not (project / mp.MANIFEST).exists():
            box = card(layout, 'First: create or choose a project', 'FILE_FOLDER')
            box.prop(s, 'mod_active_path', text='Existing project')
            from .shared_project import draw_note
            draw_note(box, context)
            box.prop(s, 'mod_project_name', text='New project')
            prefs = get_addon_preferences(context)
            if prefs:
                box.prop(prefs, 'mod_library_root', text='Projects folder')
            box.operator('sanctuary.mod_create', text='Create Project', icon='ADD')
        else:
            layout.label(text='Project: ' + project.name, icon='FILE_FOLDER')
        draw_asset_inputs(layout, context)
        draw_lua_choice(layout, context)
        draw_export_actions(layout, context)
    elif state.stage == 'PROJECT':
        box = card(layout, 'Your mod project', 'FILE_FOLDER')
        box.prop(s, 'mod_active_path', text='Project')
        from .shared_project import draw_note
        draw_note(box, context)
        row = box.row(align=True)
        row.operator('sanctuary.mod_open', text='Open', icon='FILE_FOLDER')
        row.operator('sanctuary.mod_import', text='Import archive', icon='IMPORT')
        box.prop(s, 'mod_archive_path', text='Archive')
        units = card(layout, 'Units in this project', 'OUTLINER_COLLECTION')
        button(units, 'PROJECT_UNITS', 'Refresh Units', 'FILE_REFRESH')
        button(units, 'REPAIR_LUA', 'Create Missing Unit Lua Copies', 'DUPLICATE')
        units.template_list('SANCTUARY_UL_studio_units', '', state, 'project_units', state, 'project_unit_index', rows=4)
        button(units, 'SWITCH_UNIT', 'Work on Selected Unit', 'CHECKMARK')
        box = card(layout, 'Create a project', 'ADD')
        box.prop(s, 'mod_project_name')
        box.prop(s, 'mod_tagged')
        if s.mod_tagged:
            box.prop(s, 'mod_faction_name')
            box.prop(s, 'mod_faction_tag')
        prefs = get_addon_preferences(context)
        if prefs:
            box.prop(prefs, 'mod_library_root', text='Library folder')
            box.prop(prefs, 'sanctuary_root', text='Game folder')
        box.operator('sanctuary.mod_create', text='Create Project', icon='ADD')
        if state.advanced:
            box = card(layout, 'Convert a mod into a faction project', 'DUPLICATE')
            for prop in ('faction_project', 'faction_name', 'faction_prefix', 'faction_tag'):
                box.prop(state, prop)
            box.label(text='Creates a separate copy with remapped unit IDs.')
            button(box, 'CONVERT_FACTION', 'Create Converted Copy', 'DUPLICATE')
    elif state.stage == 'BROWSER':
        box = card(layout, 'Game & mod library', 'ASSET_MANAGER')
        box.operator('sanctuary.open_unit_browser', text='Open Unit Browser', icon='VIEWZOOM')
        box.label(text='Select a donor, then use it as your blueprint template.')
        button(box, 'DONOR_SELECTED', 'Use Selected Unit as Template', 'IMPORT')
        box.prop(s, 'mod_unit_icon_png', text='Add / replace unit icon')
        button(box, 'ICON_SAVE', 'Save Unit Icon to Project', 'FILE_TICK')
    elif state.stage == 'ASSETS':
        draw_asset_inputs(layout, context)
        from . import ui
        holder = type('PanelLayout', (), {'layout': layout})()
        layout.prop(state, 'show_animations', icon='TRIA_DOWN' if state.show_animations else 'TRIA_RIGHT')
        if state.show_animations:
            ui.SANCTUARY_PT_animation.draw(holder, context)
        if state.advanced:
            layout.prop(s, 'swap_yz_axis')
            ui.SANCTUARY_PT_textures.draw(holder, context)
        draw_export_actions(layout, context)
    elif state.stage == 'BLUEPRINT':
        row = layout.row(align=True)
        button(row, 'LOAD', 'Open Unit Lua', 'FILE_FOLDER')
        button(row, 'SAVE', 'Save', 'FILE_TICK')
        draw_lua_choice(layout, context)
        from .studio_sizes import draw_panel
        draw_panel(layout, context)
        if state.advanced:
            box = card(layout, 'Create a new unit identity (optional)', 'PRESET')
            box.operator('sanctuary.studio_donor', text='Choose Component / Wizard Donor').apply_to_unit = False
            box.prop(state, 'donor')
            box.operator('sanctuary.studio_new_unit', text='New Unit ID Wizard', icon='ADD')
        if state.blueprint:
            row = layout.row(align=True)
            row.prop(state, 'blueprint', text='Draft')
            button(row, 'SOURCE', '', 'TEXT')
            row = layout.row(align=True)
            row.prop(state, 'filter', text='', icon='VIEWZOOM')
            button(row, 'REFRESH', '', 'FILE_REFRESH')
            layout.prop(state, 'field_group', text='')
            layout.template_list('SANCTUARY_UL_studio_fields', '', state, 'fields', state, 'field_index', rows=10)
            if state.fields:
                box = card(layout, state.fields[state.field_index].name, 'OPTIONS')
                item = state.fields[state.field_index]
                if item.name == 'movement.type':
                    box.prop(state, 'movement_type')
                    if state.movement_type == '__CUSTOM__':
                        box.prop(state, 'string_value', text='Custom type')
                    box.label(text='Movement changes may also require matching tags.')
                elif item.name.lower().endswith(('bonename', 'bone')):
                    bone_control(box, context)
                elif state.field_kind == 'STRING':
                    box.operator('sanctuary.studio_named_choice', text=state.string_value or 'Choose value...', icon='DOWNARROW_HLT')
                    box.prop(state, 'custom_string')
                    if state.custom_string:
                        box.prop(state, 'string_value', text='Custom value')
                    box.label(text='Choices from game/project Lua; custom values allowed.')
                else:
                    box.prop(state, {'STRING': 'string_value', 'NUMBER': 'number_value', 'BOOL': 'bool_value'}.get(state.field_kind, 'field_value'))
                row = box.row(align=True)
                button(row, 'FIELD', 'Apply Field', 'CHECKMARK')
                button(row, 'REMOVE_FIELD', 'Remove', 'REMOVE')
            tags = card(layout, 'Unit tags', 'BOOKMARKS')
            tags.operator('sanctuary.studio_named_choice', text='Add Tag...', icon='ADD').add_tag = True
            tags.prop(state, 'show_custom_tag')
            if state.show_custom_tag:
                tags.prop(state, 'new_tag')
                button(tags, 'ADD_TAG', 'Add Custom Tag', 'ADD')
            box = card(layout, 'Components from template', 'MODIFIER')
            box.prop(state, 'section')
            box.prop(state, 'donor_index')
            button(box, 'COMPONENT', 'Copy Component', 'DUPLICATE')
            box = card(layout, 'Add a rotating bone', 'BONE_DATA')
            bone_control(box, context)
            box.prop(state, 'rotator_speed')
            button(box, 'ROTATOR', 'Add Rotator', 'ADD')
            if state.advanced:
                box = card(layout, 'Custom field / Lua expression', 'CONSOLE')
                box.prop(state, 'new_field')
                box.prop(state, 'field_value')
                button(box, 'ADD_FIELD', 'Add or Set Field', 'ADD')
    elif state.stage == 'EFFECTS':
        box = card(layout, 'Effect & material library', 'PARTICLES')
        button(box, 'INDEX_EFFECTS', 'Index Game + Project Effects', 'FILE_REFRESH')
        box.prop(state, 'effect_search', text='', icon='VIEWZOOM')
        box.template_list('SANCTUARY_UL_studio_effects', '', state, 'effects', state, 'effect_index', rows=7)
        button(box, 'LOAD_EFFECT', 'Inspect Selected Asset', 'VIEWZOOM')
        if state.effect_document:
            box = card(layout, 'Custom effect editor', 'SHADERFX')
            box.prop(state, 'effect_asset')
            box.prop(state, 'effect_name')
            button(box, 'CLONE_EFFECT', 'Make Custom Copy + Dependencies', 'DUPLICATE')
            box.prop(state, 'effect_filter', text='', icon='VIEWZOOM')
            box.template_list('SANCTUARY_UL_studio_effect_fields', '', state, 'effect_fields', state, 'effect_field_index', rows=7)
            box.prop(state, 'effect_value')
            button(box, 'EFFECT_FIELD', 'Apply Property', 'CHECKMARK')
            box.prop(state, 'effect_color')
            button(box, 'TINT_EFFECT', 'Apply Particle Tint', 'COLOR')
            button(box, 'SAVE_EFFECT', 'Save Custom Asset', 'FILE_TICK')
            button(box, 'PLAY_EFFECT', 'Open Animated Preview Scene', 'PLAY')
            box.label(text='Preview approximates particles; Unity shaders differ.', icon='INFO')
            if state.effect_asset.lower().endswith('.sanmaterial'):
                texture = card(layout, 'Replace a material texture', 'TEXTURE')
                texture.label(text='Select textures.N.path above, then choose a PNG.')
                texture.prop(state, 'effect_texture')
                texture.prop(state, 'effect_texture_size')
                button(texture, 'REPLACE_EFFECT_TEXTURE', 'Convert & Assign Texture', 'IMAGE_DATA')
                texture.prop(state, 'shield_index')
                button(texture, 'BIND_SHIELD_MATERIAL', 'Use as Shield Surface Material', 'SHADING_RENDERED')
            box = card(layout, 'Attach to a bone', 'CONSTRAINT_BONE')
            bone_control(box, context)
            box.prop(state, 'bind_path')
            button(box, 'BIND_EFFECT', 'Add to Blueprint', 'ADD')
            box.prop(state, 'preview_offset')
            box.prop(state, 'effect_scale')
            button(box, 'PREVIEW_EFFECT', 'Preview Attachment', 'HIDE_OFF')
            box.label(text='Attachment guide only; game shader is not simulated.', icon='INFO')
        box = card(layout, 'Audio', 'SOUND')
        box.prop(state, 'audio_file')
        button(box, 'PLAY_AUDIO', 'Listen to Audio File', 'PLAY')
        box.prop(state, 'audio_event')
        box.prop(state, 'audio_path')
        box.prop(state, 'audio_loop')
        button(box, 'BIND_AUDIO', 'Assign Game Audio Event', 'ADD')
        box.label(text='Wwise bank creation is not provided by this editor.', icon='INFO')
    elif state.stage == 'ICON':
        from .icon_references import draw as draw_icon_references
        from .icon_camera import draw_defaults
        if context.scene.get('studio_icon_unit'):
            box = card(layout, 'Icon scene', 'CAMERA_DATA')
            box.label(text='Adjust camera. Lighting uses forest.exr only.')
            box.label(text='Return to Source Scene to render and assign.')
            draw_defaults(layout, context)
            draw_icon_references(layout, engine(context))
            return
        box = card(layout, 'Unit portrait studio', 'RENDER_STILL')
        box.prop(state, 'icon_size')
        button(box, 'ICON_SETUP', 'Create Forest-Lit Icon Scene', 'SCENE_DATA')
        if state.icon_scene:
            button(box, 'ICON_EDIT', 'Edit Icon Camera', 'CAMERA_DATA')
        box.prop(state, 'icon_scene')
        box.label(text='forest.exr illumination only • no light objects')
        box.label(text='New scenes use your remembered camera setup.')
        draw_defaults(layout, context)
        button(box, 'ICON_RENDER', 'Render & Save Unit Icon', 'RENDER_STILL')
        box.prop(s, 'mod_unit_icon_png', text='Assigned PNG')
        button(box, 'ICON_SAVE', 'Save Unit Icon from PNG', 'FILE_TICK')
        button(box, 'ICON_REPAIR', 'Repair Missing Icon Sprites', 'FILE_REFRESH')
        box.label(text='Saves PNG + DDS + sprite to the mod project only.')
        box.label(text='Use Export → Deploy to update the live game.')
        draw_icon_references(layout, engine(context))
    elif state.stage == 'VALIDATE':
        box = card(layout, 'Check before exporting', 'CHECKMARK')
        button(box, 'VALIDATE', 'Check Unit & Blueprint', 'CHECKMARK')
        button(box, 'ROUNDTRIP', 'Validate / Test: Export & Reimport', 'FILE_REFRESH')
        box.label(text='Test output opens in a separate scene.')
        box.label(text='Your original objects stay in the source scene.')
        button(box, 'REPORT', 'Open Validation Report', 'TEXT')
        box.label(text='Game visuals and behavior still need an in-game check.', icon='INFO')
    elif state.stage == 'PUBLISH':
        draw_export_actions(layout, context)
        box = card(layout, 'Install in Sanctuary', 'PLAY')
        prefs = get_addon_preferences(context)
        if prefs:
            box.prop(prefs, 'mods_root', text='Live Mods folder')
        box.operator('sanctuary.studio_deploy', text='Check & Deploy Project to SSS', icon='PLAY')
        box = card(layout, 'Shareable project archive', 'PACKAGE')
        box.prop(s, 'mod_archive_path', text='Archive')
        box.operator('sanctuary.mod_pack', text='Package Mod Project', icon='PACKAGE')
    if state.stage in ('HOME', 'BLUEPRINT', 'PUBLISH'):
        draw_saved_lua(layout, context)
    if state.viewer_text:
        viewer = layout.box()
        viewer.prop(state, 'viewer_visible', text='Read Report / Saved Lua Here')
        if state.viewer_visible:
            viewer.label(text=state.viewer_text.name)
            button(viewer, 'COPY_VIEWER', 'Copy Full Text', 'COPYDOWN')
            rows = state.viewer_text.as_string().splitlines()
            pages = max(1, (len(rows) + 24) // 25)
            viewer.prop(state, 'viewer_page', text=f'Page (1–{pages})')
            page = min(state.viewer_page, pages)
            import textwrap
            for line in rows[(page - 1) * 25:page * 25]:
                for wrapped in textwrap.wrap(line, 52) or [' ']:
                    viewer.label(text=wrapped)
    footer = layout.box()
    # Keep status readable in narrow sidebars.
    import textwrap
    for line in textwrap.wrap(state.status, 48):
        footer.label(text=line)


def draw_studio(layout, context):
    from .studio_workshop import draw
    draw(layout, context)


class SANCTUARY_PT_studio(Panel):
    bl_idname = 'SANCTUARY_PT_studio'
    bl_label = 'Sanctuary Studio'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Sanctuary'
    bl_order = -10

    def draw(self, context):
        draw_studio(self.layout, context)


class SANCTUARY_PT_studio_text(Panel):
    bl_label = 'Sanctuary Studio'
    bl_space_type = 'TEXT_EDITOR'
    bl_region_type = 'UI'
    bl_category = 'Sanctuary'

    def draw(self, context):
        self.layout.prop(settings(context), 'blueprint', text='Blueprint')
        button(self.layout, 'REFRESH', 'Read Source Changes', 'FILE_REFRESH')
        button(self.layout, 'SAVE', 'Validate & Save Blueprint', 'FILE_TICK')


class SANCTUARY_PT_studio_wide(Panel):
    bl_idname = 'SANCTUARY_PT_studio_wide'
    bl_label = 'Sanctuary Studio'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'scene'
    bl_order = -1000

    def draw(self, context):
        draw_studio(self.layout, context)


CLASSES = (StudioField, StudioUnit, StudioState, SANCTUARY_UL_studio_fields, SANCTUARY_UL_studio_effects, SANCTUARY_UL_studio_units,
           SANCTUARY_UL_studio_effect_fields, SANCTUARY_OT_studio_named_choice, SANCTUARY_OT_studio_donor, SANCTUARY_OT_studio_lua_file, SANCTUARY_OT_studio_record_lua, SANCTUARY_OT_studio_mesh_export, SANCTUARY_OT_studio_deploy, SANCTUARY_OT_studio_new_unit, SANCTUARY_OT_studio_action,
           SANCTUARY_PT_studio, SANCTUARY_PT_studio_text, SANCTUARY_PT_studio_wide)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.sanctuary_studio = PointerProperty(type=StudioState)


def unregister():
    global _LIBRARY
    _LIBRARY = None
    from .icon_references import clear
    clear()
    del bpy.types.Scene.sanctuary_studio
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
