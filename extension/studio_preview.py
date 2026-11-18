"""Bounded visual approximations of exported particle systems in Blender.

This is deliberately labelled as an approximation: Unity Shader Graph, GPU
simulation and collision modules are not reproduced by Blender.
"""
import hashlib
import json
import math
from pathlib import Path
import random

import bpy
from mathutils import Matrix, Quaternion, Vector


def constant(value, default=1):
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, dict):
        return default
    mode = value.get('mode', 0)
    if mode == 3:
        return (value.get('constantMin', default) + value.get('constantMax', default)) / 2
    return float(value.get('constant', value.get('constantMax', default)))


def vec(value, default=(0, 0, 0)):
    if not isinstance(value, dict):
        return Vector(default)
    return Vector(tuple(float(value.get(k, default[i])) for i, k in enumerate('xyz')))


def preview_material(assets, path, tint, override=None):
    from .dds_tools import decode_dds_top
    from .material_io import _blender_image_from_rgba
    material = bpy.data.materials.new('Effect Preview • ' + Path(path).stem)
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new('ShaderNodeOutputMaterial')
    emission = tree.nodes.new('ShaderNodeEmission')
    emission.inputs['Color'].default_value = (*tint[:3], 1)
    emission.inputs['Strength'].default_value = 2
    transparent = tree.nodes.new('ShaderNodeBsdfTransparent')
    mix = tree.nodes.new('ShaderNodeMixShader')
    mix.inputs[0].default_value = 0.8
    tree.links.new(transparent.outputs[0], mix.inputs[1])
    tree.links.new(emission.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], output.inputs[0])
    material.surface_render_method = 'BLENDED'
    data = override if override is not None else assets.json(path) if path and path.lower() in assets.records else {}
    # Shader profiles/noise are not finished sprites. Give unsupported procedural
    # shaders a soft particle silhouette instead of rendering an opaque quad.
    coordinates = tree.nodes.new('ShaderNodeTexCoord')
    distance = tree.nodes.new('ShaderNodeVectorMath')
    distance.operation = 'DISTANCE'
    distance.inputs[1].default_value = (0.5, 0.5, 0)
    tree.links.new(coordinates.outputs['UV'], distance.inputs[0])
    falloff = tree.nodes.new('ShaderNodeMapRange')
    falloff.inputs['From Min'].default_value = 0
    falloff.inputs['From Max'].default_value = 0.5
    falloff.inputs['To Min'].default_value = 1
    falloff.inputs['To Max'].default_value = 0
    tree.links.new(distance.outputs['Value'], falloff.inputs['Value'])
    soften = tree.nodes.new('ShaderNodeMath'); soften.operation = 'POWER'
    soften.inputs[1].default_value = 2
    tree.links.new(falloff.outputs['Result'], soften.inputs[0])
    tree.links.new(soften.outputs[0], mix.inputs[0])
    color = next((c.get('value', {}) for c in data.get('colors', [])
                  if c.get('key', '').lower() in ('_basecolor', '_color', '_tintcolor', '_flare_color', '_emissioncolor')), {})
    tint = tuple(tint[i] * max(0, color.get(k, 1)) for i,k in enumerate('rgb'))
    emission.inputs['Color'].default_value = (*tint, 1)
    textures = data.get('textures', [])
    choices = sorted(textures, key=lambda item: 0 if item.get('key', '').lower() in ('_maintex', '_basemap', '_basecolormap', '_emissive') else 1)
    texture = next((t for t in choices if t.get('path', '').lower() in assets.records
                    and not any(w in t.get('key', '').lower() for w in ('normal', 'distort', 'mask'))), None)
    if texture and 'procedural' not in data.get('shader', '').lower():
        try:
            pixels = decode_dds_top(assets.read(texture['path']))
            if isinstance(pixels, tuple):
                pixels = pixels[0]
            image = _blender_image_from_rgba('VFX • ' + Path(texture['path']).stem, pixels)
            node = tree.nodes.new('ShaderNodeTexImage')
            node.image = image
            multiply = tree.nodes.new('ShaderNodeMixRGB')
            multiply.blend_type = 'MULTIPLY'
            multiply.inputs[0].default_value = 1
            multiply.inputs[2].default_value = (*tint[:3], 1)
            tree.links.new(node.outputs['Color'], multiply.inputs[1])
            tree.links.new(multiply.outputs[0], emission.inputs['Color'])
            alpha = tree.nodes.new('ShaderNodeMath'); alpha.operation = 'MULTIPLY'
            tree.links.new(node.outputs['Alpha'], alpha.inputs[0])
            tree.links.new(soften.outputs[0], alpha.inputs[1])
            tree.links.new(alpha.outputs[0], mix.inputs[0])
        except (ValueError, KeyError, TypeError) as exc:
            material['preview_texture_error'] = str(exc)
    material['source_material'] = path
    material['preview_approximation'] = True
    return material


def create_preview(context, asset, data, assets):
    from .sanmodel_io import import_sanmodel, parse_sanmodel_bytes
    from .utils import san_to_blender_matrix
    original = context.scene
    scene = bpy.data.scenes.new('VFX Preview • ' + Path(asset).stem)
    scene['studio_source_scene'] = original.name
    scene['studio_approximation'] = 'Particle size, lifetime, speed, transforms and texture preview. Unity shaders, forces, subemitters and collision are not reproduced.'
    scene.render.engine = 'BLENDER_EEVEE_NEXT'
    scene.render.fps = 30
    scene.frame_start, scene.frame_end = 1, 120
    scene.world = bpy.data.worlds.new('VFX Preview Background')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs[0].default_value = (0.012, 0.018, 0.027, 1)
    context.window.scene = scene
    ctx = bpy.context
    transforms = []
    for i, value in enumerate(data.get('transforms', [])):
        obj = bpy.data.objects.new(value.get('name', 'Emitter') + ' • Transform', None)
        scene.collection.objects.link(obj)
        q = value.get('localRotation', {})
        local = Matrix.LocRotScale(vec(value.get('localPosition')),
            Quaternion(tuple(q.get(k, 1 if k == 'w' else 0) for k in ('w', 'x', 'y', 'z'))),
            vec(value.get('localScale'), (1, 1, 1)))
        parent = value.get('parrentId', -1)
        if parent < 0:
            local.translation = Vector((0, 0, 0))
        obj.matrix_local = san_to_blender_matrix(local)
        transforms.append((obj, parent))
    for obj, parent in transforms:
        if 0 <= parent < len(transforms):
            local = obj.matrix_local.copy()
            obj.parent = transforms[parent][0]
            obj.matrix_local = local
    if asset.lower().endswith('.sanmaterial'):
        bpy.ops.mesh.primitive_uv_sphere_add(segments=32, ring_count=16, radius=2)
        obj = ctx.object
        obj.data.materials.append(preview_material(assets, asset, (1, 1, 1), data))
        scene['studio_preview_particles'] = 1
    elif data.get('particleSystems'):
        rng = random.Random(0)
        total = 0
        for index, particle in enumerate(data.get('particleSystems', [])):
            rendering = particle.get('rendering', {})
            if rendering.get('enabled') is False:
                continue
            main = particle.get('main', {})
            lifetime = min(4, max(0.1, constant(main.get('startLifetime'))))
            size = min(30, max(0.01, constant(main.get('startSize'), 0.3)))
            speed = min(100, max(-100, constant(main.get('startSpeed'), 0)))
            color = main.get('startColor', {}).get('color', {'r': 1, 'g': 1, 'b': 1})
            tint = tuple(max(0, min(1, color.get(k, 1))) for k in 'rgb')
            material = preview_material(assets, rendering.get('material', ''), tint)
            shape = particle.get('shape', {})
            radius = min(20, max(0, shape.get('radius', 0))) if shape.get('enabled') else 0
            count = min(12, max(1, int(main.get('maxParticles', 8))))
            for n in range(count):
                if total >= 192:
                    break
                mesh_path = next((p for p in rendering.get('meshes', []) if p.lower() in assets.records), '')
                if rendering.get('renderMode') == 4 and mesh_path:
                    obj = import_sanmodel(ctx, parse_sanmodel_bytes(assets.read(mesh_path)), None, True, False)
                    if isinstance(obj, tuple): obj = obj[0]
                else:
                    bpy.ops.mesh.primitive_plane_add(size=1)
                    obj = ctx.object
                    obj.rotation_euler.x = math.pi / 2
                obj.name = f'Particle {index + 1}.{n + 1}'
                obj.data.materials.clear()
                obj.data.materials.append(material)
                owner = particle.get('ownerId', -1)
                if 0 <= owner < len(transforms):
                    obj.parent = transforms[owner][0]
                theta = rng.random() * math.tau
                start = Vector((math.cos(theta) * radius, math.sin(theta) * radius, 0))
                birth = 1 + n * 4
                death = birth + max(3, int(lifetime * 30))
                obj.location = start
                obj.scale = (0.001,) * 3
                obj.keyframe_insert('scale', frame=max(1, birth - 1))
                obj.scale = (size,) * 3
                obj.keyframe_insert('scale', frame=birth)
                obj.keyframe_insert('location', frame=birth)
                obj.location = start + Vector((0, 0, speed * lifetime))
                obj.keyframe_insert('location', frame=death)
                obj.scale = (0.001,) * 3
                obj.keyframe_insert('scale', frame=death)
                total += 1
        scene['studio_preview_particles'] = total
    else:
        context.window.scene = original
        bpy.data.scenes.remove(scene)
        raise ValueError('Animated preview supports particle effects and material surfaces. This beam/decal has no particle system.')
    scene.frame_set(30)
    scene.view_layers[0].update()
    corners = [obj.matrix_world @ Vector(c) for obj in scene.objects if obj.type == 'MESH' for c in obj.bound_box]
    center = sum(corners, Vector()) / len(corners) if corners else Vector()
    radius = max(((p-center).length for p in corners), default=2)
    camera = bpy.data.objects.new('Effect Preview Camera', bpy.data.cameras.new('Effect Preview Camera'))
    scene.collection.objects.link(camera)
    camera.location = center + Vector((0, -max(radius*3, 0.5), max(radius, 0.2)))
    camera.rotation_euler = (center-camera.location).to_track_quat('-Z', 'Y').to_euler()
    camera.data.type = 'ORTHO'
    camera.data.ortho_scale = max(0.3, radius*2.4)
    scene.camera = camera
    scene.render.resolution_x = scene.render.resolution_y = 384
    scene.render.resolution_percentage = 100
    scene.sanctuary_studio.status = 'Approximate VFX preview • press Space to play. Return using the button below.'
    scene.sanctuary_studio.stage = 'EFFECTS'
    if context.area and context.area.type == 'VIEW_3D':
        context.area.spaces.active.shading.type = 'MATERIAL'
        if context.area.spaces.active.region_3d:
            context.area.spaces.active.region_3d.view_location = center
            context.area.spaces.active.region_3d.view_distance = max(radius*3, 0.5)
            context.area.spaces.active.region_3d.view_perspective = 'CAMERA'
    return scene
