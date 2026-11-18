"""Global icon-camera defaults, independent of .blend files and mod projects."""
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
from copy import deepcopy

import bpy
from bpy.props import EnumProperty
from bpy.types import Operator
from mathutils import Euler, Matrix, Vector
from .utils import get_addon_preferences

# Keep the 2.0.5 default until the user saves their preferred angle.
DEFAULT_ROTATION = tuple(Vector((1.4, -1.8, -1.25)).to_track_quat('-Z', 'Y').to_euler('XYZ'))
_LOADED = set()
_LOAD_ERRORS = {}
_PRESETS = {}

# Capture writable camera controls, not linked datablocks, custom code, or aliases
# of lens/sensor values which would overwrite each other on restore.
_SKIP = set(bpy.types.ID.bl_rna.properties.keys()) | {'angle', 'angle_x', 'angle_y'}
_SCALAR_TYPES = {'BOOLEAN', 'INT', 'FLOAT', 'ENUM'}


def properties(obj):
    result = {}
    for prop in obj.bl_rna.properties:
        key = prop.identifier
        if key in _SKIP or prop.is_readonly or prop.type not in _SCALAR_TYPES:
            continue
        value = getattr(obj, key)
        if prop.type == 'ENUM' and prop.is_enum_flag:
            value = sorted(value)
        elif getattr(prop, 'is_array', False):
            value = list(value)
        result[key] = value
    return result


def check_properties(values):
    if not isinstance(values, dict):
        raise ValueError('Invalid saved camera properties.')
    for key, value in values.items():
        if not isinstance(key, str) or key in _SKIP:
            raise ValueError('Invalid saved camera property name.')
        parts = value if isinstance(value, list) else [value]
        if not all(isinstance(v, (str, bool, int, float)) for v in parts):
            raise ValueError('Invalid saved camera value: ' + key)
        if any(isinstance(v, (int, float)) and not math.isfinite(v) for v in parts):
            raise ValueError('Nonfinite saved camera value: ' + key)


def apply_properties(obj, values):
    # Fail visibly rather than silently drop a camera setting on a different
    # Blender version. All captured values are ordinary writable RNA controls.
    for key, value in values.items():
        prop = obj.bl_rna.properties.get(key)
        if not prop or prop.is_readonly or prop.type not in _SCALAR_TYPES or key in _SKIP:
            raise ValueError('This Blender version cannot restore camera setting: ' + key)
        if prop.type == 'ENUM' and prop.is_enum_flag:
            value = set(value)
        setattr(obj, key, value)


def validate_preset(data):
    if not isinstance(data, dict) or data.get('version') not in (1, 2):
        raise ValueError('Unrecognised camera-defaults file format.')
    validate_rotation(data.get('rotation_degrees'))
    if data['version'] == 1 or data.get('mode') == 'ANGLE':
        return
    if data.get('mode') != 'FULL':
        raise ValueError('Unrecognised camera preset mode.')
    matrix = data.get('matrix_world')
    if not isinstance(matrix, list) or len(matrix) != 4:
        raise ValueError('Invalid saved camera transform.')
    for row in matrix:
        if not isinstance(row, list) or len(row) != 4 or any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in row):
            raise ValueError('Invalid saved camera transform.')
    if abs(Matrix(matrix).determinant()) < 1e-12:
        raise ValueError('Saved camera transform has zero scale.')
    for key in ('camera', 'dof', 'stereo'):
        check_properties(data.get(key))
    if 'cycles' in data:
        check_properties(data['cycles'])
    if data.get('focus_target') is not None:
        validate_rotation(data['focus_target'])  # Same finite XYZ vector contract.
    viewport = data.get('viewport')
    if viewport is not None:
        if not isinstance(viewport, dict) or not isinstance(viewport.get('offset'), list) or len(viewport['offset']) != 2:
            raise ValueError('Invalid saved camera-view framing.')
        validate_rotation([viewport.get('zoom'), *viewport['offset']])
    aspect = data.get('pixel_aspect', [1, 1])
    if not isinstance(aspect, list) or len(aspect) != 2:
        raise ValueError('Invalid saved pixel aspect.')
    validate_rotation([*aspect, 0])
    if min(aspect) <= 0:
        raise ValueError('Pixel aspect must be positive.')


def settings_path():
    # Merely drawing the panel/creating a scene must not write preferences.
    return Path(bpy.utils.user_resource('CONFIG')) / 'sanctuary_modding_toolkit' / 'icon_camera_defaults.json'


def validate_rotation(value):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError('Camera rotation requires three X/Y/Z angles.')
    if any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in value):
        raise ValueError('Camera angles must be finite numbers.')
    return tuple(float(v) for v in value)


def token(prefs):
    return prefs.as_pointer() if hasattr(prefs, 'as_pointer') else id(prefs)


def ensure_loaded(prefs):
    if prefs is None or token(prefs) in _LOADED:
        return
    key = token(prefs)
    _LOADED.add(key)
    path = settings_path()
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        validate_preset(data)
        angles = validate_rotation(data.get('rotation_degrees'))
        prefs.icon_camera_rotation = tuple(math.radians(v) for v in angles)
        _PRESETS[key] = data
    except (OSError, ValueError, TypeError) as exc:
        _LOAD_ERRORS[key] = str(exc)


def rotation(context):
    prefs = get_addon_preferences(context)
    if prefs is None:
        return DEFAULT_ROTATION
    ensure_loaded(prefs)
    if token(prefs) in _LOAD_ERRORS:
        raise ValueError('Saved camera setup could not be read. Remember a camera again, or save an angle with automatic framing.')
    return validate_rotation(tuple(getattr(prefs, 'icon_camera_rotation', DEFAULT_ROTATION)))


def write_rotation(value):
    value = validate_rotation(tuple(value))
    data = {'version': 2, 'mode': 'ANGLE', 'rotation_degrees': [math.degrees(v) for v in value]}
    return write_preset(data)


def write_preset(data):
    validate_preset(data)
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Save only our tiny camera-settings file, not all Blender preferences.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='icon_camera_', suffix='.tmp', delete=False) as f:
            temporary = Path(f.name)
            json.dump(data, f, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        if path.is_file():
            shutil.copy2(path, path.with_suffix('.json.bak'))
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()
    return path


def preset(context):
    prefs = get_addon_preferences(context)
    angles = rotation(context)  # Also checks corrupt defaults before creating data.
    data = _PRESETS.get(token(prefs)) if prefs is not None else None
    if data and data.get('mode') == 'FULL':
        return deepcopy(data)
    return {'version': 2, 'mode': 'ANGLE', 'rotation_degrees': [math.degrees(v) for v in angles]}


def camera_view(context, scene):
    if context.scene != scene:
        return None
    area = getattr(context, 'area', None)
    areas = [area] if area and area.type == 'VIEW_3D' else []
    screen = getattr(context, 'screen', None)
    if screen:
        areas += [a for a in screen.areas if a.type == 'VIEW_3D' and a not in areas]
    for area in areas:
        space = area.spaces.active
        region = space.region_3d
        if region and region.view_perspective == 'CAMERA' and (not space.use_local_camera or space.camera == scene.camera):
            return {'zoom': region.view_camera_zoom, 'offset': list(region.view_camera_offset)}
    return None


def capture_setup(context, scene):
    layer = scene.view_layers[0]
    layer.update()
    graph = layer.depsgraph
    camera = scene.camera.evaluated_get(graph)
    data = {'version': 2, 'mode': 'FULL',
            'rotation_degrees': [math.degrees(v) for v in camera.matrix_world.to_euler('XYZ')],
            'matrix_world': [list(row) for row in camera.matrix_world],
            'camera': properties(camera.data), 'dof': properties(camera.data.dof),
            'stereo': properties(camera.data.stereo), 'viewport': camera_view(context, scene),
            'pixel_aspect': [scene.render.pixel_aspect_x, scene.render.pixel_aspect_y],
            'focus_target': None}
    if hasattr(camera.data, 'cycles'):
        data['cycles'] = properties(camera.data.cycles)
    focus = camera.data.dof.focus_object
    if focus:
        focus = focus.evaluated_get(graph)
        subtarget = getattr(camera.data.dof, 'focus_subtarget', '')
        if subtarget and focus.type == 'ARMATURE' and subtarget in focus.pose.bones:
            point = focus.matrix_world @ focus.pose.bones[subtarget].matrix.translation
        else:
            point = focus.matrix_world.translation
        data['focus_target'] = list(point)
    validate_preset(data)
    return data


def configure(scene, center, radius, data):
    """Apply the remembered camera exactly, or use the explicitly chosen auto-fit mode."""
    camera = scene.camera
    if data.get('mode') != 'FULL':
        place(camera, center, max(radius * 4, 1), [math.radians(v) for v in data['rotation_degrees']])
        camera.data.type = 'ORTHO'
        camera.data.ortho_scale = max(radius * 2.25, 1)
        return
    apply_properties(camera.data, data['camera'])
    apply_properties(camera.data.dof, data['dof'])
    apply_properties(camera.data.stereo, data['stereo'])
    if data.get('cycles'):
        if not hasattr(camera.data, 'cycles'):
            raise ValueError('The saved camera needs Cycles camera settings unavailable in this Blender.')
        apply_properties(camera.data.cycles, data['cycles'])
    camera.rotation_mode = 'XYZ'
    camera.matrix_world = Matrix(data['matrix_world'])
    if data.get('focus_target') is not None:
        # Standalone target: never depend on an object/bone in another .blend.
        focus = bpy.data.objects.new('Icon Camera Focus', None)
        scene.collection.objects.link(focus)
        focus.location = data['focus_target']
        focus.hide_render = True
        camera.data.dof.focus_object = focus
    scene.render.pixel_aspect_x, scene.render.pixel_aspect_y = data.get('pixel_aspect', (1, 1))
    scene['studio_camera_view'] = json.dumps(data.get('viewport'))
    scene['studio_camera_preset'] = 'FULL'


def restore_view(context, scene):
    # Viewport zoom/pan changes only the on-screen camera border, not the lens.
    view = json.loads(scene.get('studio_camera_view', 'null'))
    screen = getattr(context, 'screen', None)
    if not screen:
        return
    for area in screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            space.camera = scene.camera
            region = space.region_3d
            if region:
                region.view_perspective = 'CAMERA'
                if view:
                    region.view_camera_zoom = view['zoom']
                    region.view_camera_offset = view['offset']


def icon_scene(context):
    if context.scene.get('studio_icon_unit'):
        return context.scene
    state = getattr(context.scene, 'sanctuary_studio', None)
    scene = state.icon_scene if state else None
    return scene if scene and scene.get('studio_icon_unit') else None


def place(camera, center, distance, angles):
    """Fixed Euler angle, auto-fit distance, camera aimed at the unit centre."""
    orientation = Euler(validate_rotation(tuple(angles)), 'XYZ')
    camera.rotation_mode = 'XYZ'
    camera.rotation_euler = orientation
    camera.location = Vector(center) + orientation.to_matrix() @ Vector((0, 0, distance))


def fit_subject(scene, margin=.035):
    """Fit isolated render meshes to the camera without resizing source assets."""
    camera=scene.camera
    if not camera or camera.data.type not in ('ORTHO','PERSP'):
        raise ValueError('Auto-fit requires a perspective or orthographic icon camera.')
    scene.view_layers[0].update()
    points=[obj.matrix_world @ vertex.co for obj in scene.objects
            if obj.type=='MESH' and not obj.hide_render for vertex in obj.data.vertices]
    if not points:raise ValueError('No visible icon mesh to frame.')
    orientation=camera.matrix_world.to_quaternion().to_matrix()
    inverse=orientation.transposed()
    local=[inverse @ p for p in points]
    center=Vector(tuple((min(p[i] for p in local)+max(p[i] for p in local))/2 for i in range(3)))
    local=[p-center for p in local]
    frame=camera.data.view_frame(scene=scene)
    if camera.data.type=='ORTHO':
        left,right=min(p.x for p in frame),max(p.x for p in frame)
        bottom,top=min(p.y for p in frame),max(p.y for p in frame)
        width=max(p.x for p in local)-min(p.x for p in local)
        height=max(p.y for p in local)-min(p.y for p in local)
        factor=max(width/(right-left),height/(top-bottom),1e-6)/(1-2*margin)
        camera.data.ortho_scale*=factor
        depth=max(1,max(p.z for p in local)+camera.data.clip_start+1)
        offset=Vector(((left+right)*factor/2,(bottom+top)*factor/2,-depth))
    else:
        left,right=min(p.x/-p.z for p in frame),max(p.x/-p.z for p in frame)
        bottom,top=min(p.y/-p.z for p in frame),max(p.y/-p.z for p in frame)
        midx,midy=(left+right)/2,(bottom+top)/2
        left+=(right-left)*margin;right-=(right-left)*margin/(1-margin)
        bottom+=(top-bottom)*margin;top-=(top-bottom)*margin/(1-margin)
        depth=max(camera.data.clip_start+.01+p.z for p in local)
        for p in local:
            depth=max(depth,(p.x+left*p.z)/(left-midx),(p.x+right*p.z)/(right-midx),
                      (p.y+bottom*p.z)/(bottom-midy),(p.y+top*p.z)/(top-midy))
        offset=Vector((midx*depth,midy*depth,-depth))
    camera.location=orientation @ (center-offset)
    camera.data.clip_end=max(camera.data.clip_end,depth+max(abs(p.z) for p in local)+1)
    if camera.data.dof.use_dof:
        camera.data.dof.focus_distance=depth
        if camera.data.dof.focus_object:camera.data.dof.focus_object.location=orientation @ center
    scene.view_layers[0].update()


def refresh_icon_image(path):
    """Invalidate both Blender's file Image and its separate thumbnail cache."""
    image=bpy.data.images.load(str(path),check_existing=True)
    image.reload();image.preview_ensure()
    image.preview.image_size=(image.size[0],image.size[1])
    image.preview.image_pixels_float=list(image.pixels)
    image['studio_icon_mtime']=str(Path(path).stat().st_mtime_ns)
    return image


class SANCTUARY_OT_icon_fit(Operator):
    bl_idname='sanctuary.icon_fit'
    bl_label='Fit Mesh Inside Camera'
    def execute(self,context):
        try:
            scene=icon_scene(context)
            if not scene:raise ValueError('Create an icon scene first.')
            fit_subject(scene)
            self.report({'INFO'},'Mesh tightly framed with a safe margin. Source model is unchanged.')
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}


def remember(context, capture=False):
    prefs = get_addon_preferences(context)
    if prefs is None:
        raise ValueError('Enable the installed add-on to save global camera defaults.')
    ensure_loaded(prefs)
    if capture:
        scene = icon_scene(context)
        if not scene or not scene.camera:
            raise ValueError('Create or choose an Icon Studio scene first.')
        data = capture_setup(context, scene)
        angles = tuple(math.radians(v) for v in data['rotation_degrees'])
        path = write_preset(data)
    else:
        angles = tuple(prefs.icon_camera_rotation)
        path = write_rotation(angles)
        data = {'version': 2, 'mode': 'ANGLE', 'rotation_degrees': [math.degrees(v) for v in angles]}
    prefs.icon_camera_rotation = angles
    _PRESETS[token(prefs)] = data
    _LOAD_ERRORS.pop(token(prefs), None)
    return path


class SANCTUARY_OT_icon_camera_default(Operator):
    bl_idname = 'sanctuary.icon_camera_default'
    bl_label = 'Remember Global Icon Camera'
    bl_description = 'Remember the complete icon camera, including position and lens/zoom, across .blend files'
    action: EnumProperty(items=[('SAVE', 'Use angle with automatic framing', ''), ('CAPTURE', 'Remember full camera setup', '')])

    def execute(self, context):
        try:
            remember(context, capture=self.action == 'CAPTURE')
            message = ('Full camera setup saved. New icon scenes keep its composition settings and fit the current subject.'
                       if self.action == 'CAPTURE' else 'Angle saved for all files. New icon scenes use automatic framing.')
            state = getattr(context.scene, 'sanctuary_studio', None)
            if state:
                state.status = message
            self.report({'INFO'}, message)
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


def draw_defaults(layout, context):
    prefs = get_addon_preferences(context)
    if prefs is None:
        return
    ensure_loaded(prefs)
    box = layout.box()
    box.label(text='Camera defaults • all .blend files', icon='CAMERA_DATA')
    scene = icon_scene(context)
    if scene and scene.camera:
        box.operator('sanctuary.icon_camera_default', text='Remember Full Camera Setup', icon='FILE_TICK').action = 'CAPTURE'
        box.label(text='Saves position, rotation, lens/zoom and camera settings.')
    else:
        box.label(text='Create an icon scene, frame it, then remember its camera.')
    full = _PRESETS.get(token(prefs), {}).get('mode') == 'FULL'
    if full:
        box.label(text='Full camera saved • new scenes fit the subject', icon='CHECKMARK')
        box.label(text='Manual changes to existing scenes are respected.')
        box.operator('sanctuary.icon_camera_default', text='Use Angle Only + Automatic Framing', icon='LOOP_BACK').action = 'SAVE'
    else:
        box.label(text='No full preset yet • angle with automatic framing')
        box.prop(prefs, 'icon_camera_rotation', text='Rotation XYZ')
        box.operator('sanctuary.icon_camera_default', text='Save Angle + Automatic Framing', icon='FILE_TICK').action = 'SAVE'
    box.label(text='Existing icon scenes are not changed.')
    if token(prefs) in _LOAD_ERRORS:
        box.label(text='Saved settings unreadable. Remember a camera again.', icon='ERROR')


def register():
    _LOADED.clear()
    _LOAD_ERRORS.clear()
    _PRESETS.clear()
    bpy.utils.register_class(SANCTUARY_OT_icon_camera_default)
    bpy.utils.register_class(SANCTUARY_OT_icon_fit)


def unregister():
    bpy.utils.unregister_class(SANCTUARY_OT_icon_fit)
    bpy.utils.unregister_class(SANCTUARY_OT_icon_camera_default)
    _LOADED.clear()
    _LOAD_ERRORS.clear()
    _PRESETS.clear()
