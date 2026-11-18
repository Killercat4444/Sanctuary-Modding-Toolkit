"""Task-oriented Studio presentation. All exports use the established codecs."""
from pathlib import Path
import json
import textwrap
import bpy
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty
from . import studio as st, mod_projects as mp
from .studio_core import Blueprint
from .utils import get_addon_preferences

_ACTION_PREVIEWS = {}


class SANCTUARY_OT_workshop_animation(Operator):
    bl_idname = 'sanctuary.workshop_animation'
    bl_label = 'Use Animation Clip'
    action: StringProperty()

    def execute(self, context):
        try:
            s = context.scene.sanctuary_toolkit
            state = st.settings(context)
            if self.action == 'RESTORE':
                previous = _ACTION_PREVIEWS.pop(context.scene.as_pointer(), None)
                if previous:
                    rig, action, start, end, fps = previous
                    if context.screen and context.screen.is_animation_playing: bpy.ops.screen.animation_cancel(restore_frame=False)
                    rig.animation_data.action = action
                    context.scene.frame_start, context.scene.frame_end, context.scene.render.fps = start,end,fps
                return {'FINISHED'}
            if not 0 <= s.animation_index < len(s.animations): raise ValueError('Add and select a clip first.')
            item = s.animations[s.animation_index]
            if not item.action: raise ValueError('Choose a Blender action for this clip.')
            if self.action == 'ASSIGN':
                if not item.enabled:
                    raise ValueError('Enable this clip for export before assigning it.')
                item.behaviour_role = state.animation_role
                uid = s.unit_id
                path = ('visuals','animations',state.animation_role)
                doc = Blueprint(st.source(context))
                text = doc.set(path + ('clipName',), json.dumps(item.clip_name))
                text = Blueprint(text).set(path + ('sanAnimationPath',), json.dumps(f'Units/{uid}/LOD0/{uid}.sananimation'))
                st.set_source(context,text)
                state.status = 'Animation assigned. Save Unit to commit the Lua.'
            else:
                rig = item.source_armature or s.animation_target_armature
                if not rig: raise ValueError('Choose the source rig first.')
                rig.animation_data_create()
                _ACTION_PREVIEWS.setdefault(context.scene.as_pointer(), (rig,rig.animation_data.action,context.scene.frame_start,context.scene.frame_end,context.scene.render.fps))
                rig.animation_data.action = item.action
                context.scene.frame_start = item.frame_start
                context.scene.frame_end = item.frame_end
                context.scene.render.fps = int(item.fps)
                context.scene.frame_set(item.frame_start)
                if context.screen and not context.screen.is_animation_playing: bpy.ops.screen.animation_play()
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'},str(exc)); return {'CANCELLED'}


def note(layout, text):
    width = max(24, min(85, int((getattr(bpy.context.region, 'width', 350) - 50) / 7)))
    for line in textwrap.wrap(text, width):
        layout.label(text=line)


def fold(layout, state, prop):
    layout.prop(state, prop, emboss=False, icon='TRIA_DOWN' if getattr(state, prop) else 'TRIA_RIGHT')
    return getattr(state, prop)


class SANCTUARY_OT_workshop_icon(Operator, ImportHelper):
    bl_idname = 'sanctuary.workshop_icon'
    bl_label = 'Use Existing Icon Image'
    filename_ext = '.png'
    filter_glob: StringProperty(default='*.png', options={'HIDDEN'})

    def execute(self, context):
        original = context.window.scene
        try:
            from .unit_icon_preview import source_scene
            source = source_scene(context)
            if source != original: context.window.scene = source
            context.scene.sanctuary_toolkit.mod_unit_icon_png = self.filepath
            mp.save_unit_icon(context, allow_other_unit_source=True)
            st.settings(context).status = 'Icon saved to project.'
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            context.window.scene = original


class SANCTUARY_OT_workshop_icon_scene(Operator):
    bl_idname = 'sanctuary.workshop_icon_scene'
    bl_label = 'Edit Icon Scene'

    def execute(self, context):
        try:
            state = st.settings(context)
            if not state.icon_scene or state.icon_scene.get('studio_icon_unit') != context.scene.sanctuary_toolkit.unit_id:
                st.create_icon_scene(context)
            context.window.scene = state.icon_scene
            from .icon_camera import restore_view
            restore_view(context, context.scene)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SANCTUARY_OT_workshop_render(Operator):
    bl_idname = 'sanctuary.workshop_render'
    bl_label = 'Render & Save Icon'

    def execute(self, context):
        original = context.window.scene
        try:
            from .unit_icon_preview import source_scene
            source = source_scene(context)
            if source != original: context.window.scene = source
            st.render_icon(context)
            self.report({'INFO'}, 'PNG, DDS and sprite saved in the mod project.')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        finally:
            context.window.scene = original


def animations(layout, context):
    s = context.scene.sanctuary_toolkit
    box = st.card(layout, 'Animation clips', 'ACTION')
    note(box, 'Add your Blender actions here. Enabled clips are included when you export the unit.')
    row = box.row(align=True)
    row.operator('sanctuary.animation_add_current', text='Add Current Action', icon='ADD')
    row.operator('sanctuary.animation_remove', text='', icon='REMOVE')
    box.template_list('SANCTUARY_UL_animations', '', s, 'animations', s, 'animation_index', rows=4)
    if 0 <= s.animation_index < len(s.animations):
        item = s.animations[s.animation_index]
        box.prop(item, 'enabled', text='Include in unit export')
        box.prop(item, 'action')
        box.prop(item, 'clip_name', text='Game clip name')
        box.prop(item, 'behaviour_role')
        row = box.row(align=True)
        row.prop(item, 'frame_start', text='Start'); row.prop(item, 'frame_end', text='End')
        row = box.row(align=True)
        row.prop(item, 'fps'); row.prop(item, 'loop')
        box.prop(item, 'source_armature', text='Source rig')
        row = box.row(align=True)
        row.operator('sanctuary.workshop_animation', text='Play Action', icon='PLAY').action = 'PLAY'
        row.operator('sanctuary.workshop_animation', text='Restore', icon='LOOP_BACK').action = 'RESTORE'
        box.prop(st.settings(context), 'animation_role')
        box.operator('sanctuary.workshop_animation', text='Assign Clip to Behaviour', icon='CHECKMARK').action = 'ASSIGN'
        if st.settings(context).advanced:
            box.prop(item, 'source_evaluation')
            box.prop(item, 'preserve_native_curves')
    box.prop(s, 'animation_target_armature', text='Unit rig')
    preview = st.card(layout, 'Play or import an animation', 'PLAY')
    preview.prop(s, 'preview_animation', text='Clip')
    row = preview.row(align=True)
    row.operator('sanctuary.preview_animation_play', text='Play', icon='PLAY')
    row.operator('sanctuary.preview_animation_stop', text='Pause', icon='PAUSE')
    row.operator('sanctuary.preview_animation_restore', text='Reset', icon='LOOP_BACK')
    preview.operator('sanctuary.load_sananimation', text='Import Game Animationâ€¦', icon='IMPORT')
    if st.settings(context).advanced:
        preview.operator('sanctuary.export_animations', text='Export Animation File Onlyâ€¦', icon='EXPORT')
        preview.operator('sanctuary.animation_add_all', text='Add All Selected Actions')


def behaviour(layout, context):
    state = st.settings(context)
    layout.row(align=True).prop(state, 'behaviour_page', expand=True)
    if state.behaviour_page == 'EFFECTS':
        from .studio_vfx import draw_effects
        draw_effects(layout, context)
        if state.blueprint:st.button(layout,'SAVE','Save Unit','FILE_TICK')
        return
    if state.behaviour_page == 'FIELDS':
        from .studio_lua import draw as draw_lua_author
        draw_lua_author(layout,context)
    if not state.blueprint:
        note(layout, 'Start with an existing unit. Studio makes an editable copy in your project.')
        st.draw_lua_choice(layout, context)
        return
    row = layout.row(align=True)
    st.button(row, 'SAVE', 'Save Unit', 'FILE_TICK')
    st.button(row, 'SOURCE', 'View Lua', 'TEXT')
    if state.behaviour_page == 'EFFECTS':
        from .studio_vfx import draw_effects
        draw_effects(layout, context)
        return
    if state.behaviour_page == 'AUDIO':
        box = st.card(layout, 'Unit sounds', 'SOUND')
        box.prop(state, 'audio_event', text='Game event')
        box.prop(state, 'audio_path', text='Lua field')
        box.prop(state, 'audio_loop')
        st.button(box, 'BIND_AUDIO', 'Assign Sound', 'CHECKMARK')
        box.prop(state, 'audio_file', text='Preview audio file')
        st.button(box, 'PLAY_AUDIO', 'Listen', 'PLAY')
        note(box, 'Audio files can be previewed here. Game sound events must already exist in a sound bank.')
        return
    if fold(layout, state, 'show_source'):
        st.draw_lua_choice(layout, context)
    from .animation_assignment import draw as draw_animation_assignment
    draw_animation_assignment(layout, context)
    layout.prop(state, 'field_group', text='Section')
    layout.prop(state, 'filter', text='', icon='VIEWZOOM')
    layout.template_list('SANCTUARY_UL_studio_fields', '', state, 'fields', state, 'field_index', rows=7)
    selected = state.fields[state.field_index] if 0 <= state.field_index < len(state.fields) else None
    if selected and (state.field_group == 'ALL' or selected.name.split('.')[0] == state.field_group) and state.filter.casefold() in selected.name.casefold():
        item = state.fields[state.field_index]
        box = st.card(layout, item.name.split('.')[-1], 'OPTIONS')
        if item.name.lower().endswith(('bone', 'bonename')):
            st.bone_control(box, context)
        elif state.field_kind == 'STRING':
            if item.name == 'movement.type':
                box.prop(state, 'movement_type')
                if state.movement_type == '__CUSTOM__': box.prop(state, 'string_value')
            else:
                box.operator('sanctuary.studio_named_choice', text=state.string_value or 'Choose valueâ€¦', icon='DOWNARROW_HLT')
                box.prop(state, 'custom_string')
                if state.custom_string: box.prop(state, 'string_value')
            if state.string_value.lower().endswith(('.sanvfx', '.sanmaterial', '.sanbeam')):
                op = box.operator('sanctuary.vfx_browser', text='Browse & Replace Effectâ€¦', icon='PARTICLES')
                op.target_path = item.name
        else:
            box.prop(state, {'NUMBER':'number_value','BOOL':'bool_value'}.get(state.field_kind, 'field_value'))
        row = box.row(align=True)
        st.button(row, 'FIELD', 'Apply Change', 'CHECKMARK')
        if state.advanced: st.button(row, 'REMOVE_FIELD', 'Remove', 'REMOVE')
        if state.advanced: note(box, item.name)
    if state.field_group == 'tags':
        layout.operator('sanctuary.studio_named_choice', text='Add Tagâ€¦', icon='ADD').add_tag = True
    if fold(layout, state, 'show_components'):
        box = layout.box()
        box.operator('sanctuary.studio_donor', text='Choose Source Unitâ€¦').apply_to_unit = False
        box.prop(state, 'section'); box.prop(state, 'donor_index')
        st.button(box, 'COMPONENT', 'Copy Component', 'DUPLICATE')
    if state.field_group == 'additions':
        box = st.card(layout, 'Rotating bone', 'BONE_DATA')
        st.bone_control(box, context)
        box.prop(state, 'rotator_speed')
        st.button(box, 'ROTATOR', 'Add Rotator', 'ADD')
    if state.advanced:
        box = st.card(layout, 'Custom Lua field', 'CONSOLE')
        box.prop(state, 'new_field'); box.prop(state, 'field_value')
        st.button(box, 'ADD_FIELD', 'Set Field', 'ADD')


def icon(layout, context):
    state = st.settings(context)
    source = bpy.data.scenes.get(context.scene.get('studio_source_scene', ''))
    if context.scene.get('studio_icon_unit') and source:
        state = source.sanctuary_studio
        s = source.sanctuary_toolkit
    else:
        s = context.scene.sanctuary_toolkit
    box = st.card(layout, 'Unit icon', 'IMAGE_DATA')
    from .unit_icon_preview import request
    preview = request(context)
    if preview['icon']: box.template_icon(icon_value=preview['icon'], scale=7)
    elif preview['message']: note(box, preview['message'])
    box.operator('sanctuary.unit_icon_reload', text='Reload Saved Icon', icon='FILE_REFRESH')
    box.operator('sanctuary.workshop_icon_scene', text='Edit Icon Scene', icon='CAMERA_DATA') if not context.scene.get('studio_icon_unit') else note(box, 'Adjust the camera, then render below.')
    box.operator('sanctuary.icon_fit',text='Fit Mesh Inside Camera',icon='FULLSCREEN_ENTER')
    row = box.row(); row.scale_y = 1.3
    row.operator('sanctuary.workshop_render', icon='RENDER_STILL')
    box.operator('sanctuary.workshop_icon', text='Use Existing Imageâ€¦', icon='IMPORT')
    note(box, 'Saves the icon and game files to your project.')
    if fold(layout, state, 'show_camera'):
        from .icon_camera import draw_defaults
        layout.prop(state, 'icon_size')
        draw_defaults(layout, context)
        note(layout, 'Lighting: forest.exr, rotated 180Â°.')
    if fold(layout, state, 'show_references'):
        from .icon_references import draw
        draw(layout, st.engine(context))
    if state.advanced:
        st.button(layout, 'ICON_REPAIR', 'Repair Project Icon Files', 'FILE_REFRESH')


def export(layout, context):
    box = st.card(layout, 'Export this unit', 'EXPORT')
    note(box, 'Save the settings, then generate the model, textures and animation files in your project.')
    row = box.row(); row.scale_y = 1.4
    st.button(row, 'BUILD', 'Export Unit to Project', 'EXPORT')
    st.button(box, 'PROJECT_FOLDER', 'Open Project Folder', 'FILE_FOLDER')
    box = st.card(layout, 'Check your unit', 'CHECKMARK')
    st.button(box, 'VALIDATE', 'Check Settings & Files', 'CHECKMARK')
    st.button(box, 'ROUNDTRIP', 'Validate & Preview in Blender', 'PLAY')
    if st.settings(context).report: st.button(box, 'REPORT', 'View Report', 'TEXT')
    box = st.card(layout, 'Install your project', 'PLAY')
    note(box, 'Updates the installed game files for every exported unit in this project.')
    box.operator('sanctuary.studio_deploy', text='Install Project in Gameâ€¦', icon='PLAY')
    if st.settings(context).advanced:
        layout.operator('sanctuary.studio_mesh_export', text='Export Mesh File Onlyâ€¦', icon='EXPORT')


def project(layout, context):
    s = context.scene.sanctuary_toolkit
    state = st.settings(context)
    box = st.card(layout, 'Current project', 'FILE_FOLDER')
    box.prop(s, 'mod_active_path', text='Folder')
    note(box, 'This project is remembered across Blender files.')
    row = box.row(align=True)
    row.operator('sanctuary.mod_open', text='Open Project')
    row.operator('sanctuary.mod_import', text='Import Archive')
    current = mp.active_project(context, required=False)
    from .studio_publishing import draw as draw_publishing
    if current: draw_publishing(layout,context)
    if not current or not mp._manifest_path(current).is_file() or state.advanced:
        box = st.card(layout, 'Create a project', 'ADD')
        box.prop(s, 'mod_project_name', text='Name')
        box.prop(s, 'mod_tagged', text='Custom faction')
        if s.mod_tagged:
            box.prop(s, 'mod_faction_name'); box.prop(s, 'mod_faction_tag')
        box.operator('sanctuary.mod_create', text='Create Project', icon='ADD')
    box = st.card(layout, 'Share or install', 'PACKAGE')
    box.operator('sanctuary.studio_deploy', text='Install Project in Gameâ€¦', icon='PLAY')
    if current:
        box.prop(s, 'mod_export_version', text='Version')
    box.prop(s, 'mod_archive_path', text='ZIP destination')
    box.operator('sanctuary.mod_pack', text='Create Project ZIP', icon='PACKAGE')
    if current and not (current / 'Remmy' / 'submod.json').is_file():
        box.separator()
        box.operator('sanctuary.mod_export_remmy', text='Export MM ZIP (Recommended)', icon='EXPORT')
        note(box, 'MM manages Lua and assigns custom faction slots dynamically.')
        box.operator('sanctuary.mod_export_standalone', text='Export Standalone ZIP', icon='PACKAGE')
        box.operator('sanctuary.mod_export_scom', text='Export DYCOM for SCOM', icon='PACKAGE')
        note(box, 'Unit/art mods only. New factions must use MM; standalone slots can conflict.')
        note(box, 'Advanced: validates bootstrap and Lua support. Full game restart required.')
    elif current and (current / 'Remmy' / 'submod.json').is_file():
        box.separator()
        box.operator('sanctuary.mod_export_uber', text='Export Uber Tester ZIP', icon='EXPORT')
        note(box, 'Creates the separate optional Uber Engineer installer for Dycom testers.')
    prefs = get_addon_preferences(context)
    if prefs:
        box = st.card(layout, 'Locations', 'PREFERENCES')
        box.prop(prefs, 'sanctuary_root', text='Game folder')
        box.prop(prefs, 'mods_root', text='Installed mods')
        box.prop(prefs, 'mod_library_root', text='Project library')
    if state.advanced:
        box = st.card(layout, 'Convert a faction', 'DUPLICATE')
        for prop in ('faction_project','faction_name','faction_prefix','faction_tag'): box.prop(state, prop)
        st.button(box, 'CONVERT_FACTION', 'Create Faction Copy', 'DUPLICATE')


def draw(layout, context):
    state, s = st.settings(context), context.scene.sanctuary_toolkit
    layout.use_property_split = False
    if context.scene.get('studio_approximation'):
        note(layout, 'Effect preview Â· approximate game appearance')
        st.button(layout, 'RETURN_SCENE', 'Return to Unit', 'BACK')
        layout.operator('screen.animation_play', text='Play / Pause', icon='PLAY')
        return
    if context.scene.get('studio_icon_unit'):
        st.button(layout, 'RETURN_SCENE', 'Return to Unit', 'BACK')
        icon(layout, context)
        return
    header = layout.row(align=True)
    header.label(text='SANCTUARY STUDIO', icon='WORKSPACE')
    st.button(header, 'OPEN', '', 'WINDOW')
    project_path = mp.active_project(context, required=False)
    layout.label(text=project_path.name if project_path else 'Choose a project to begin', icon='FILE_FOLDER')
    layout.row(align=True).prop(state, 'destination', expand=True)
    from .studio_publishing import help_draw
    help_draw(layout,context)
    if state.destination == 'PROJECT':
        project(layout, context)
    elif state.destination == 'BROWSER':
        layout.operator('sanctuary.open_unit_browser', text='Browse All Unitsâ€¦', icon='ASSET_MANAGER')
        layout.operator('sanctuary.studio_new_unit', text='Create Unit from Templateâ€¦', icon='ADD')
        st.button(layout, 'PROJECT_UNITS', 'Refresh Project Units', 'FILE_REFRESH')
        layout.template_list('SANCTUARY_UL_studio_units', '', state, 'project_units', state, 'project_unit_index', rows=7)
        st.button(layout, 'SWITCH_UNIT', 'Work on Selected Unit', 'CHECKMARK')
    else:
        title = s.unit_id.upper() or 'New unit'
        if state.blueprint:
            try: title = Blueprint(state.blueprint.as_string()).scalar(('general','displayName'), title)
            except ValueError: pass
        layout.label(text=str(title), icon='OBJECT_DATA')
        layout.prop(s, 'unit_id', text='Unit ID')
        layout.row(align=True).prop(state, 'workshop', expand=True)
        if not project_path:
            note(layout, 'First choose or create a project in Project. Then select your model and armature below.')
        if state.workshop == 'MODEL':
            layout.row(align=True).prop(state, 'model_page', expand=True)
            if state.model_page == 'ANIMATION': animations(layout, context)
            elif state.model_page == 'SIZES':
                from .studio_sizes import draw_panel
                draw_panel(layout, context, force=True)
            else: st.draw_asset_inputs(layout, context)
        elif state.workshop == 'BEHAVIOUR': behaviour(layout, context)
        elif state.workshop == 'ICON': icon(layout, context)
        else: export(layout, context)
    layout.separator()
    layout.prop(state, 'advanced', text='Advanced tools')
    if state.status:
        note(layout, state.status.splitlines()[0][:100])
        if fold(layout, state, 'show_details'):
            note(layout, state.status)
            if state.report: st.button(layout, 'REPORT', 'Open Report', 'TEXT')
            if state.viewer_text:
                st.button(layout, 'COPY_VIEWER', 'Copy Report / Lua', 'COPYDOWN')
                layout.prop(state, 'viewer_visible', text='Read Here')
                if state.viewer_visible:
                    rows = state.viewer_text.as_string().splitlines()
                    layout.prop(state, 'viewer_page', text='Page')
                    start = (state.viewer_page-1)*25
                    for line in rows[start:start+25]: note(layout, line)


CLASSES = (SANCTUARY_OT_workshop_animation, SANCTUARY_OT_workshop_icon, SANCTUARY_OT_workshop_icon_scene, SANCTUARY_OT_workshop_render)
def register():
    for cls in CLASSES: bpy.utils.register_class(cls)
def unregister():
    for cls in reversed(CLASSES): bpy.utils.unregister_class(cls)
