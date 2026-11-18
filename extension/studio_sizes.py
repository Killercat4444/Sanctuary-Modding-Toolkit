"""Viewport-only Lua size guides. Never creates exportable objects or writes files.

Coordinates follow sanmodel_io's rig-local export contract (no metre multiplier).
Game references: common/loading/unitTemplateLoader.lua and
AI/UnitBlueprintValidator.lua. Selection collider is 1.3 * bracket X/Z.
"""
import json
import math
from pathlib import Path
import hashlib

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatVectorProperty, PointerProperty, StringProperty
from bpy.types import Operator, PropertyGroup
from mathutils import Vector

from .studio_core import Blueprint, lua_compile

_HANDLES = []
_SIZE_SOURCES = []
_SIZE_SOURCE_PATHS = {}
# UI name, vector properties -> Lua paths, colour (RGBA).
GROUPS = {
    'COLLISION': ('Collision / hitbox', {
        'collision': ('collisionInfo', 'collisionSize'),
        'collision_offset': ('collisionInfo', 'centerOffset')}, (1.0, 0.25, 0.12, 1)),
    'FOOTPRINT': ('Movement footprint', {'footprint': ('footprint',)}, (0.25, 0.9, 0.4, 1)),
    'SELECTION': ('Selection brackets', {
        'selection': ('visuals', 'selection', 'size'),
        'selection_offset': ('visuals', 'selection', 'offset')}, (0.1, 0.7, 1.0, 1)),
    'SKIRT': ('Building skirt / tarmac', {'skirt': ('skirtSize',)}, (1.0, 0.7, 0.1, 1)),
}
DEFAULTS = {'collision': (1, 1, 1), 'collision_offset': (0, 0, 0),
            'footprint': (1, 1), 'selection': (1, 1),
            'selection_offset': (0, 0, 0), 'skirt': (2, 2)}


def redraw(self=None, context=None):
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


class SizeState(PropertyGroup):
    expanded: BoolProperty(name='Visual size editor', default=False)
    visible: BoolProperty(default=False, options={'SKIP_SAVE'})
    binding: StringProperty(options={'SKIP_SAVE'})
    baseline: StringProperty(options={'SKIP_SAVE'})
    initial: StringProperty(options={'SKIP_SAVE'})
    problems: StringProperty(options={'SKIP_SAVE'})
    present: StringProperty(options={'SKIP_SAVE'})
    kind: EnumProperty(name='Edit', items=[(k, v[0], '') for k, v in GROUPS.items()], update=redraw)
    collision: FloatVectorProperty(name='Full size (game XYZ)', size=3, min=0.001, default=(1,1,1), precision=3, update=redraw)
    collision_offset: FloatVectorProperty(name='Center (game XYZ)', size=3, precision=3, update=redraw)
    footprint: FloatVectorProperty(name='Width / length (Lua XY)', size=2, min=0.001, default=(1,1), precision=3, update=redraw)
    selection: FloatVectorProperty(name='Width / length (Lua XY)', size=2, min=0.001, default=(1,1), precision=3, update=redraw)
    selection_offset: FloatVectorProperty(name='Click-box center (game XYZ)', size=3, precision=3, update=redraw)
    skirt: FloatVectorProperty(
        name='Tarmac width / length (world Lua XY)', size=2, min=0.001,
        default=(2,2), precision=3, update=redraw,
        description='Writes skirtSize in game world-space dimensions; rendering depends on the faction’s tarmac loader')
    show_collision: BoolProperty(name='Hitbox', default=True, update=redraw)
    show_footprint: BoolProperty(name='Footprint', default=True, update=redraw)
    show_selection: BoolProperty(name='Brackets', default=True, update=redraw)
    show_skirt: BoolProperty(name='Skirt', default=True, update=redraw)
    show_click: BoolProperty(name='Click box (+30%)', default=False, update=redraw)
    show_ruler: BoolProperty(name='1-unit ruler', default=True, update=redraw)
    copy_source: StringProperty(name='Source Lua', subtype='FILE_PATH',
        description='Read-only source: use the largest upgrade tier\'s saved .santp or .lua file')
    copy_group: EnumProperty(name='Copy', items=[('ALL', 'All size settings', 'Hitbox and offset, footprint, selection and offset, skirt')]
        + [(k, v[0], '') for k, v in GROUPS.items()])


def binding(context):
    s = context.scene.sanctuary_toolkit
    st = context.scene.sanctuary_studio
    rig = s.animation_target_armature or (s.export_model_object.find_armature() if s.export_model_object else None)
    return json.dumps([s.unit_id, s.mod_active_path, st.blueprint.as_pointer() if st.blueprint else 0,
                       s.export_model_object.as_pointer() if s.export_model_object else 0,
                       rig.as_pointer() if rig else 0,
                       bool(s.swap_yz_axis)])


def current(context, require_source=True):
    st = context.scene.sanctuary_sizes
    if st.binding != binding(context):
        raise ValueError('Unit, Lua or pinned model changed. Reload sizes from Lua.')
    text = context.scene.sanctuary_studio.blueprint
    if require_source and (not text or text.as_string() != st.baseline):
        raise ValueError('Lua draft changed. Reload sizes before editing or previewing.')
    return st


def read_vectors(text):
    doc = Blueprint(text)
    values, problems, present = {}, {}, {}
    for kind, (_, properties, _) in GROUPS.items():
        present[kind] = bool(doc.span(next(iter(properties.values()))))
        for prop, path in properties.items():
            v = []
            for axis, default in zip('xyz', DEFAULTS[prop]):
                raw = doc.raw(path + (axis,))
                value = doc.scalar(path + (axis,)) if raw is not None else default
                if raw is None and present[kind] and 'offset' not in prop:
                    problems[kind] = '.'.join(path + (axis,)) + ' is missing; edit it in Lua.'
                if not isinstance(value, (int, float)) or not math.isfinite(value):
                    problems[kind] = '.'.join(path + (axis,)) + ' uses an expression; edit it in Lua.'
                    value = default
                if 'offset' not in prop and value <= 0:
                    problems[kind] = '.'.join(path + (axis,)) + ' must be positive.'
                    value = default
                v.append(value)
            # Refuse replacing an opaque table expression with a generated table.
            for n in range(1, len(path) + 1):
                raw = doc.raw(path[:n])
                if raw is not None and not raw.lstrip().startswith('{'):
                    problems[kind] = '.'.join(path[:n]) + ' is an expression; edit it in Lua.'
            values[prop] = v
    return values, problems, present


def load(context):
    from . import studio
    if not context.scene.sanctuary_toolkit.export_model_object:
        raise ValueError('Pin your model first in Model & rig.')
    text = studio.source(context)
    uid = context.scene.sanctuary_toolkit.unit_id.casefold()
    if Blueprint(text).scalar(('general', 'tpId'), '').casefold() != uid:
        raise ValueError('The Lua draft belongs to another unit. Load the correct unit Lua first.')
    values, problems, present = read_vectors(text)
    st = context.scene.sanctuary_sizes
    for prop, value in values.items():
        setattr(st, prop, value)
    st.initial = json.dumps({prop: list(getattr(st, prop)) for prop in values})
    st.problems, st.present = json.dumps(problems), json.dumps(present)
    st.baseline, st.binding, st.visible, st.expanded = text, binding(context), True, True
    ensure_handlers()
    redraw()


def apply(context):
    from . import studio
    st = current(context)
    original = json.loads(st.initial)
    problems, present = json.loads(st.problems), json.loads(st.present)
    text = st.baseline
    changes = 0
    for kind, (_, properties, _) in GROUPS.items():
        for prop, path in properties.items():
            for i, value in enumerate(getattr(st, prop)):
                # An active absent size may be explicitly added, but don't add absent offsets.
                add = kind == st.kind and not present[kind] and 'offset' not in prop
                if not add and abs(value - original[prop][i]) < 1e-6:
                    continue
                if kind in problems:
                    raise ValueError(problems[kind])
                text = Blueprint(text).set(path + ('xyz'[i],), format(value, '.7g'))
                changes += 1
    if not changes:
        raise ValueError('No preview changes to apply.')
    lua_compile(text, studio.engine(context))
    studio.set_source(context, text)
    load(context)
    context.scene.sanctuary_studio.status = 'Sizes applied to Lua draft. Save Lua / Export Complete Unit when ready.'


def source_items(self, context):
    return _SIZE_SOURCES or [('NONE', 'No saved project unit Lua found', '')]


class SANCTUARY_OT_size_source(Operator):
    bl_idname = 'sanctuary.size_source'
    bl_label = 'Choose Size Source Unit'
    bl_description = 'Choose a saved unit Lua from the active project without loading or replacing your current unit'
    bl_property = 'source'
    source: EnumProperty(items=source_items)

    def invoke(self, context, event):
        from .mod_projects import active_project
        try:
            project = active_project(context)
            _SIZE_SOURCES.clear()
            _SIZE_SOURCE_PATHS.clear()
            root = project / 'Lua/common/units/unitsTemplates'
            for path in sorted(root.glob('*/*.santp')):
                try:
                    doc = Blueprint(path.read_text(encoding='utf-8-sig'))
                    uid = str(doc.scalar(('general', 'tpId'), path.stem))
                    label = str(doc.scalar(('general', 'displayName'), ''))
                    key = hashlib.sha256(str(path.resolve()).casefold().encode()).hexdigest()
                    _SIZE_SOURCES.append((key, uid.upper() + ' • ' + label, str(path)))
                    _SIZE_SOURCE_PATHS[key] = str(path)
                except (OSError, ValueError):
                    continue
            context.window_manager.invoke_search_popup(self)
            return {'RUNNING_MODAL'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

    def execute(self, context):
        if self.source not in _SIZE_SOURCE_PATHS:
            self.report({'ERROR'}, 'Reopen the source picker and select a saved unit Lua.')
            return {'CANCELLED'}
        context.scene.sanctuary_sizes.copy_source = _SIZE_SOURCE_PATHS[self.source]
        return {'FINISHED'}


def copy_from_lua(context):
    from . import studio
    from .size_transfer import transfer_sizes
    state = context.scene.sanctuary_sizes
    original = studio.source(context)
    uid = context.scene.sanctuary_toolkit.unit_id.casefold()
    if not uid or str(Blueprint(original).scalar(('general', 'tpId'), '')).casefold() != uid:
        raise ValueError('Load the target unit\'s own Lua first. The current draft has a different Unit ID.')
    if state.binding == binding(context) and state.initial:
        initial = json.loads(state.initial)
        if any(any(abs(v - initial[p][i]) > 1e-6 for i, v in enumerate(getattr(state, p))) for p in initial):
            raise ValueError('There are unapplied size-preview edits. Apply them or use Reload from Lua before copying.')
    if not state.copy_source:
        raise ValueError('Choose the source unit Lua first.')
    path = Path(bpy.path.abspath(state.copy_source)).expanduser().resolve()
    if path.suffix.casefold() not in ('.santp', '.lua') or not path.is_file():
        raise ValueError('Choose an existing .santp or .lua source file.')
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError('Source Lua is unexpectedly large; choose a unit template.')
    donor = path.read_text(encoding='utf-8-sig')
    updated, changes = transfer_sizes(donor, original, state.copy_group)
    if not changes:
        raise ValueError('Those size settings already match the saved source Lua.')
    lua_compile(updated, studio.engine(context))
    # Exactly one text mutation after every check. No source/destination disk writes.
    studio.set_source(context, updated)
    state.visible = False
    state.binding = ''
    if context.scene.sanctuary_toolkit.export_model_object:
        load(context)
    state.expanded = True
    source_uid = str(Blueprint(donor).scalar(('general', 'tpId'), path.stem))
    message = 'Sizes copied from ' + source_uid.upper() + ' to ' + uid.upper() + ' draft. Save Lua, then deploy separately.'
    context.scene.sanctuary_studio.status = message
    return message


class SANCTUARY_OT_copy_sizes(Operator):
    bl_idname = 'sanctuary.copy_sizes'
    bl_label = 'Copy Sizes to Current Lua Draft'
    bl_description = 'Replace only the chosen size fields from the saved source Lua; never changes unit ID, model, stats or files on disk'
    bl_options = {'REGISTER', 'UNDO'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event,
            message='Replace the selected size fields in this unit\'s Lua draft? Other settings are kept; nothing is saved to disk.')

    def execute(self, context):
        try:
            self.report({'INFO'}, copy_from_lua(context))
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def draw_copy(layout, context):
    state = context.scene.sanctuary_sizes
    box = layout.box()
    box.label(text='Copy sizes from another Lua', icon='DUPLICATE')
    box.operator('sanctuary.size_source', text='Choose Source Project Unit...', icon='VIEWZOOM')
    box.prop(state, 'copy_source')
    box.prop(state, 'copy_group')
    box.operator('sanctuary.copy_sizes', icon='PASTEDOWN')
    box.label(text='Use the largest upgrade tier as the source.')
    box.label(text='Reads saved source Lua; copies to draft only. Save afterwards.')


def export_bounds(context):
    """Use the existing exporter's exact evaluated REST mesh and relative transform."""
    from .sanmodel_io import _prepare_evaluated_mesh, _mesh_to_armature_matrix, _transform_position
    s = context.scene.sanctuary_toolkit
    obj = s.export_model_object
    if not obj or obj.type != 'MESH':
        raise ValueError('Pin a mesh first.')
    if obj.mode != 'OBJECT':
        raise ValueError('Switch to Object Mode before measuring the export mesh.')
    rig = s.animation_target_armature or obj.find_armature()
    evaluated, mesh, old_pose, _ = _prepare_evaluated_mesh(context, obj, rig)
    try:
        transform = _mesh_to_armature_matrix(obj, rig)
        vertices = [_transform_position(v.co, transform, s.swap_yz_axis) for v in mesh.vertices]
        if not vertices:
            raise ValueError('The mesh has no vertices.')
        low = Vector(tuple(min(v[i] for v in vertices) for i in range(3)))
        high = Vector(tuple(max(v[i] for v in vertices) for i in range(3)))
        return low, high
    finally:
        evaluated.to_mesh_clear()
        if rig and old_pose is not None:
            rig.data.pose_position = old_pose
            context.view_layer.update()


def fit(context):
    st = current(context)
    if st.kind in json.loads(st.problems):
        raise ValueError(json.loads(st.problems)[st.kind])
    low, high = export_bounds(context)
    if st.kind == 'COLLISION':
        st.collision = tuple(max(0.001, v) for v in high-low)
        st.collision_offset = (high+low)/2
    else:
        # Ground rectangles are origin-centred in the loader. Use symmetric extents.
        size = [max(0.001, 2*max(abs(low[i]), abs(high[i]))) for i in (0, 2)]
        prop = {'FOOTPRINT':'footprint', 'SELECTION':'selection', 'SKIRT':'skirt'}[st.kind]
        setattr(st, prop, size)
    redraw()


def edges_box(size, center=(0, 0, 0)):
    points = [tuple(center[i] + signs[i]*size[i]/2 for i in range(3))
              for signs in ((-1,-1,-1),(1,-1,-1),(1,-1,1),(-1,-1,1),
                            (-1,1,-1),(1,1,-1),(1,1,1),(-1,1,1))]
    edges = ((0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),(0,4),(1,5),(2,6),(3,7))
    return [points[i] for edge in edges for i in edge]


def rectangle(size):
    x, z = size[0]/2, size[1]/2
    points = [(-x,0,-z),(x,0,-z),(x,0,z),(-x,0,z)]
    return [points[i] for edge in ((0,1),(1,2),(2,3),(3,0)) for i in edge]


def geometry(st):
    present, problems = json.loads(st.present), json.loads(st.problems)
    result = []
    for kind, (label, properties, color) in GROUPS.items():
        if kind in problems or not (present[kind] or kind == st.kind):
            continue
        prop = next(iter(properties))
        if not getattr(st, 'show_'+prop):
            continue
        coords = edges_box(st.collision, st.collision_offset) if kind == 'COLLISION' else rectangle(getattr(st, prop))
        dims = ' × '.join(f'{v:.3g}' for v in getattr(st, prop))
        result.append((label+'  '+dims, coords, color))
    if st.show_click and present['SELECTION'] and present['COLLISION'] and not any(k in problems for k in ('SELECTION','COLLISION')):
        size = (math.floor(st.selection[0]*1.3*1000+0.5)/1000, st.collision[1],
                math.floor(st.selection[1]*1.3*1000+0.5)/1000)
        result.append(('Click box  '+' × '.join(f'{v:.3g}' for v in size),
                       edges_box(size, st.selection_offset), (0.85,0.35,1.0,1)))
    if st.show_ruler:
        ruler = [(0,0,0),(1,0,0)]
        for i in range(11):
            ruler.extend([(i/10,0,-0.025),(i/10,0,0.025)])
        result.append(('1 Lua unit (ticks = 0.1)', ruler, (1,1,1,1)))
    return result


def to_world(context, points):
    s = context.scene.sanctuary_toolkit
    obj = s.export_model_object
    rig = s.animation_target_armature or obj.find_armature()
    anchor = (rig or obj).matrix_world
    return [anchor @ Vector((p[0],p[2],p[1]) if s.swap_yz_axis else p) for p in points]


def preview_context():
    ctx = bpy.context
    if not ctx.area or ctx.area.type != 'VIEW_3D' or not hasattr(ctx.scene, 'sanctuary_sizes'):
        return None
    st = ctx.scene.sanctuary_sizes
    if not st.visible or not ctx.space_data.overlay.show_overlays:
        return None
    current(ctx)
    return ctx


def draw_world():
    import gpu
    from gpu_extras.batch import batch_for_shader
    try:
        ctx = preview_context()
        if not ctx:
            return
        shader = gpu.shader.from_builtin('POLYLINE_UNIFORM_COLOR')
        depth, blend = gpu.state.depth_test_get(), gpu.state.blend_get()
        try:
            gpu.state.depth_test_set('NONE')
            gpu.state.blend_set('ALPHA')
            for _, points, color in geometry(ctx.scene.sanctuary_sizes):
                batch = batch_for_shader(shader, 'LINES', {'pos': to_world(ctx, points)})
                shader.bind()
                shader.uniform_float('viewportSize', (ctx.region.width, ctx.region.height))
                shader.uniform_float('lineWidth', 2.0)
                shader.uniform_float('color', color)
                batch.draw(shader)
        finally:
            gpu.state.depth_test_set(depth)
            gpu.state.blend_set(blend)
    except (ValueError, ReferenceError):
        return  # Changed unit/draft: hide old guides, offer Reload in the panel.


def draw_labels():
    import blf
    try:
        ctx = preview_context()
        if not ctx:
            return
        ui = ctx.preferences.system.ui_scale
        y = ctx.region.height-100*ui
        lines = [('SIZE PREVIEW • Lua units • not exported', (1,1,1,1))]
        lines += [(label, color) for label, _, color in geometry(ctx.scene.sanctuary_sizes)]
        for text, color in lines:
            blf.size(0, 13*ui)
            blf.color(0, *color)
            blf.position(0, 22*ui, y, 0)
            blf.draw(0, text)
            y -= 21*ui
    except (ValueError, ReferenceError):
        return


def ensure_handlers():
    if not _HANDLES and not bpy.app.background:
        _HANDLES.extend([bpy.types.SpaceView3D.draw_handler_add(draw_world, (), 'WINDOW', 'POST_VIEW'),
                         bpy.types.SpaceView3D.draw_handler_add(draw_labels, (), 'WINDOW', 'POST_PIXEL')])


class SANCTUARY_OT_studio_sizes(Operator):
    bl_idname = 'sanctuary.studio_sizes'
    bl_label = 'Visual Unit Sizes'
    bl_options = {'REGISTER', 'UNDO'}
    action: EnumProperty(items=[(x,x,'') for x in ('SHOW','HIDE','RELOAD','FIT','APPLY')])

    def invoke(self, context, event):
        if self.action == 'RELOAD':
            return context.window_manager.invoke_confirm(self, event, message='Discard size-preview edits and reload from the Lua draft?')
        return self.execute(context)

    def execute(self, context):
        try:
            if self.action == 'HIDE':
                context.scene.sanctuary_sizes.visible = False
            elif self.action == 'SHOW':
                try:
                    current(context)
                    context.scene.sanctuary_sizes.visible = True
                    ensure_handlers()
                except ValueError:
                    load(context)
            elif self.action == 'RELOAD':
                load(context)
            elif self.action == 'FIT':
                fit(context)
            elif self.action == 'APPLY':
                apply(context)
            redraw()
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class SANCTUARY_OT_match_tarmac_selection(Operator):
    bl_idname = 'sanctuary.match_tarmac_selection'
    bl_label = 'Match Tarmac to Selection'
    bl_description = 'Copy the current selection width and length into the building skirt/tarmac preview'
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            st = current(context)
            problems = json.loads(st.problems)
            if 'SELECTION' in problems:
                raise ValueError(problems['SELECTION'])
            if 'SKIRT' in problems:
                raise ValueError(problems['SKIRT'])
            st.skirt = tuple(st.selection)
            st.kind = 'SKIRT'
            redraw()
            self.report({'INFO'}, 'Tarmac preview now matches selection. Apply Size Edits to update the Lua draft.')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def draw_panel(layout, context, force=False):
    st = context.scene.sanctuary_sizes
    box = layout.box()
    box.prop(st, 'expanded', text='Visual size editor', icon='TRIA_DOWN' if st.expanded else 'TRIA_RIGHT', emboss=False)
    if not st.expanded and not force:
        return
    draw_copy(box, context)
    if not st.visible:
        box.operator('sanctuary.studio_sizes', text='Show Sizes Around Model', icon='HIDE_OFF').action = 'SHOW'
        box.label(text='Pin a mesh and load its Lua first.')
        return
    row = box.row(align=True)
    row.operator('sanctuary.studio_sizes', text='Reload from Lua', icon='FILE_REFRESH').action = 'RELOAD'
    row.operator('sanctuary.studio_sizes', text='Hide', icon='HIDE_ON').action = 'HIDE'
    try:
        current(context)
    except ValueError as exc:
        box.label(text='Unit or Lua changed. Reload sizes.', icon='ERROR')
        return
    box.prop(st, 'kind')
    problems = json.loads(st.problems)
    if st.kind in problems:
        box.label(text='This shape uses invalid / nonliteral values.', icon='ERROR')
        box.label(text='Edit its values in Lua, then Reload.')
    else:
        for prop, path in GROUPS[st.kind][1].items():
            box.label(text='.'.join(path))
            box.prop(st, prop)
        if not json.loads(st.present)[st.kind]:
            box.label(text='Not in Lua yet. Apply will add this shape.', icon='INFO')
        box.operator('sanctuary.studio_sizes', text='Fit This Shape to Export Mesh', icon='FULLSCREEN_ENTER').action = 'FIT'
        if st.kind == 'SKIRT':
            box.operator('sanctuary.match_tarmac_selection', icon='FULLSCREEN_ENTER')
        box.operator('sanctuary.studio_sizes', text='Apply Size Edits to Lua Draft', icon='CHECKMARK').action = 'APPLY'
        box.label(text='Preview only until Apply. Save Lua afterwards.')
    if st.kind == 'SELECTION':
        box.label(text='Offset moves the click box, not the brackets.')
    if st.kind == 'SKIRT':
        box.label(text='Non-square factory rotation preserves these world axes.')
    box.label(text='Show guides (viewport only):')
    grid = box.grid_flow(row_major=True, columns=2, even_columns=True)
    for prop in ('show_collision','show_footprint','show_selection','show_skirt','show_click','show_ruler'):
        grid.prop(st, prop)
    box.label(text='Full dimensions, not half-sizes or percentages.')
    if context.scene.sanctuary_toolkit.swap_yz_axis:
        box.label(text='3D game Y = Blender Z (height).')
        box.label(text='2D Lua X/Y = Blender X/Y (ground).')
    else:
        box.label(text='Axis swap OFF: game Y is height in Blender Y.')
    box.label(text='1 unit = 1 exported coordinate unit; no metre conversion.')
    box.label(text='Pose / animation can extend beyond a fitted REST box.')
    box.label(text='Game validation may recalculate brackets/footprint/skirt.', icon='INFO')


CLASSES = (SizeState, SANCTUARY_OT_studio_sizes, SANCTUARY_OT_match_tarmac_selection,
           SANCTUARY_OT_size_source, SANCTUARY_OT_copy_sizes)

def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.sanctuary_sizes = PointerProperty(type=SizeState)

def unregister():
    _SIZE_SOURCES.clear()
    _SIZE_SOURCE_PATHS.clear()
    for handle in _HANDLES:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _HANDLES.clear()
    del bpy.types.Scene.sanctuary_sizes
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
