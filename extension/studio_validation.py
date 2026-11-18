"""Actionable preflight and isolated export/reimport verification."""
import json
from pathlib import Path
import tempfile

import bpy
import numpy as np

from .studio_core import Blueprint, lua_compile
from . import mod_projects as mp


def project_preflight(context):
    """Validate the files about to be deployed, without changing live content."""
    from .studio import engine, settings
    from .studio_core import AssetLibrary
    project = mp.active_project(context)
    mp.read_manifest(project)
    from .unit_icons import validate_project
    validate_project(project)
    from .project_blueprints import require_project_templates
    require_project_templates(project, engine(context))
    state = settings(context)
    if state.blueprint and state.blueprint.as_string() != state.baseline:
        raise ValueError('Save your blueprint draft before deploying')
    if state.effect_document and state.effect_document.as_string() != state.effect_baseline:
        raise ValueError('Save your effect/material draft before deploying')
    v=getattr(context.scene,'sanctuary_vfx',None)
    if v:
        from .studio_vfx import has_adjustments
        if not state.effect_asset.startswith('projectile:') and has_adjustments(v):
            raise ValueError('Save your effect adjustments before exporting.')
        if v.projectile_document and v.projectile_document.as_string()!=v.projectile_baseline:
            raise ValueError('Save your projectile draft before exporting.')
    assets = AssetLibrary(engine(context), project).scan()
    counts = {}
    for category in ('Lua', 'Units', 'UI', 'VFX', 'Environment', 'Audio', 'Projectiles'):
        files = [p for p in (project / category).rglob('*') if p.is_file()]
        counts[category] = len(files)
        for path in files:
            if path.is_symlink() or project.resolve() not in path.resolve().parents:
                raise ValueError('Project path escapes its root: ' + str(path))
            if path.suffix.lower() in ('.santp', '.lua'):
                text = path.read_text(encoding='utf-8-sig')
                compiled, message = lua_compile(text, engine(context))
                if not compiled:
                    raise ValueError('Deployment requires the game Lua compiler: ' + message)
                if path.suffix.lower() == '.santp':
                    root = 'ProjectileTemplate' if 'projectilesTemplates' in path.parts else 'UnitTemplate'
                    if Blueprint(text, root).scalar(('general', 'tpId')) != path.stem:
                        raise ValueError('Blueprint filename / tpId mismatch: ' + str(path))
            if path.suffix.lower() in ('.sanvfx', '.sanmaterial', '.sandecal', '.sanbeam'):
                from .studio_core import json_leaves
                from .vfx_documents import decode_asset
                data = decode_asset(path.read_bytes(),path)
                for field_path, value in json_leaves(data):
                    parent = data
                    disabled = False
                    for key in field_path:
                        if isinstance(parent, dict) and parent.get('enabled') is False:
                            disabled = True
                            break
                        parent = parent[key]
                    if disabled:
                        continue  # Shipped effects retain stale references in disabled modules.
                    if isinstance(value, str) and value.lower().startswith(('vfx/', 'environment/', 'units/', 'projectiles/')):
                        if value.lower() not in assets.records:
                            raise ValueError(f'{path.name}: missing dependency {value}')
    if not any(counts.values()):
        raise ValueError('Project contains no deployable files')
    from .release_validation import check
    manifest=mp.read_manifest(project)
    faction=(manifest.get('release',{}).get('factions') or [None])[0]
    result=check(project,engine(context),faction)
    if hasattr(context.scene,'sanctuary_publishing'):
        report=bpy.data.texts.new('Studio Export Preflight');report.write(result.text());report.use_fake_user=True
        context.scene.sanctuary_publishing.report=report
    result.require()
    return counts


def validate(context, roundtrip=False):
    from .studio import settings, engine, library
    from .utils import safe_unit_id, get_bone_id_map
    from .sanmodel_io import export_sanmodel_file, parse_sanmodel_file, import_sanmodel, validate_model_against_armature
    from .sanskeleton_io import export_sanskeleton_file, parse_sanskeleton_file, create_armature_from_skeleton
    from .sananimation_io import export_sananimation_file, import_sananimation_bytes
    from .material_io import apply_companion_textures_from_paths
    from .dds_tools import convert_texture_set

    state = settings(context)
    s = context.scene.sanctuary_toolkit
    lines, errors, warnings = [], [], []
    def fail(message):
        errors.append(message)
        lines.append('ERROR  ' + message)
    def warn(message):
        warnings.append(message)
        lines.append('WARN   ' + message)
    def ok(message):
        lines.append('PASS   ' + message)

    uid = safe_unit_id(s.unit_id)
    obj, rig = s.export_model_object, s.animation_target_armature
    if not uid:
        fail('Unit ID is invalid')
    if not obj or obj.type != 'MESH':
        fail('Assets: pin the final mesh')
    if not rig or rig.type != 'ARMATURE':
        fail('Assets: pin the final armature')
    project = mp.active_project(context, required=False)
    try:
        mp.read_manifest(project)
    except Exception:
        fail('Project: create or open a valid mod project')
    mapping, bones = {}, []
    if project and uid:
        from .unit_icons import paths, validate_pair
        try:
            icon_files = paths(project, uid)
            if any(p.exists() for p in icon_files):
                width, height = validate_pair(project, uid)
                ok(f'Icon DDS/sprite pair resolves at {width} x {height}')
            elif s.mod_unit_icon_png:
                warn('Assigned PNG not yet saved as a game icon. Use Save Unit Icon.')
        except Exception as exc:
            fail(str(exc))
    if rig:
        mapping, bones = get_bone_id_map(rig)
        ok(f'{len(bones)} export bones on {rig.name}')
    if obj and rig:
        if obj.find_armature() != rig:
            fail('Pinned mesh is not linked to the pinned armature')
        if not obj.data.uv_layers:
            fail('Mesh has no UV map')
        unknown, unweighted, multiple = set(), 0, 0
        for vertex in obj.data.vertices:
            weights = [g for g in vertex.groups if g.weight > 0]
            if not weights:
                unweighted += 1
            if len(weights) > 1:
                multiple += 1
            for g in weights:
                name = obj.vertex_groups[g.group].name
                if name not in mapping:
                    unknown.add(name)
        if unknown:
            fail('Weighted groups have no exported bone: ' + ', '.join(sorted(unknown)))
        if unweighted:
            warn(f'{unweighted} vertices have no weight and fall back to Root')
        if multiple:
            warn(f'{multiple} vertices have multiple weights; SSS keeps the strongest only')
        if not unknown:
            ok('Every weighted vertex group resolves to an exported bone')
    blueprint_path = project / 'Lua/common/units/unitsTemplates' / uid / (uid + '.santp') if project and uid else None
    text = None
    if state.blueprint:
        text = state.blueprint.as_string()
        if not blueprint_path or not blueprint_path.is_file() or blueprint_path.read_text(encoding='utf-8-sig') != text:
            fail('Blueprint has unsaved changes: save it before export/test')
    elif blueprint_path and blueprint_path.is_file():
        text = blueprint_path.read_text(encoding='utf-8-sig')
    else:
        fail('No saved blueprint for this unit')
    if text:
        try:
            doc = Blueprint(text)
            if doc.scalar(('general', 'tpId')) != uid:
                fail('Blueprint tpId differs from the export Unit ID')
            compiled, message = lua_compile(text, engine(context))
            (ok if compiled else warn)(message)
            assets = None
            for path, raw in doc.leaves():
                value = doc.scalar(path)
                if isinstance(value, str) and str(path[-1]).lower() in ('bonename', 'bone'):
                    if value and value not in mapping:
                        fail('Missing bone at ' + '.'.join(map(str, path)) + ': ' + value)
                if isinstance(value, str) and value.lower().startswith(('vfx/', 'environment/')):
                    assets = assets or library(context)
                    if value.lower() not in assets.records:
                        fail('Unresolved effect/texture: ' + value)
            ok('Blueprint field and reference inspection completed')
        except Exception as exc:
            fail('Blueprint: ' + str(exc))
    for label, field in (('Albedo', 'texture_albedo_png'), ('Mask', 'texture_mask_png'), ('Normal', 'texture_normal_png')):
        value = getattr(s, field)
        if value and not Path(bpy.path.abspath(value)).is_file():
            fail(label + ' input is missing: ' + value)
    if not s.texture_albedo_png:
        lod = project / 'Units' / uid / 'LOD0' if project and uid else None
        if not lod or not any(lod.glob('*.sanmaterial')):
            warn('No albedo conversion input or existing project material; texture appearance is unverified')
    original_scene = context.scene
    preview_scene = None
    if roundtrip and not errors:
        staging = Path(tempfile.mkdtemp(prefix='sanctuary_roundtrip_'))
        try:
            model_path = staging / (uid + '_lod0.sanmodel')
            stats = export_sanmodel_file(context, obj, rig, model_path, uid + '_lod0', s.swap_yz_axis)
            export_sanskeleton_file(rig, staging / (uid + '.sanskeleton'), s.swap_yz_axis)
            if stats['unknown_vertex_groups']:
                raise ValueError('Exporter reported unknown weighted groups')
            model = parse_sanmodel_file(model_path)
            validation = validate_model_against_armature(model, rig, s.swap_yz_axis)
            if validation['model_bindposes'] != len(bones):
                raise ValueError('Exported bone count differs from the rig')
            if validation['bind_identity_max_error'] is None or validation['bind_identity_max_error'] > 0.001:
                raise ValueError('Bind-pose matrices do not match the source rig')
            if len(model.uv2) and (np.min(model.uv2[:, 0]) < 0 or np.max(model.uv2[:, 0]) >= len(bones)):
                raise ValueError('Exported vertex bone ID is outside the skeleton')
            ok(f'Model round-trip: {stats["triangles"]} triangles, {stats["bones"]} bones; bind poses agree')
            animation_path = staging / (uid + '.sananimation')
            if any(i.enabled and i.action for i in s.animations):
                astats = export_sananimation_file(context, rig, s.animations, animation_path, s.swap_yz_axis)
                ok('Exported animation clips: ' + str(astats.get('clips', '?')))
            convert_texture_set(s, staging, uid)
            # Include existing project DDS/material assets when no conversion input was supplied.
            import shutil
            for file in (project / 'Units' / uid / 'LOD0').glob('*'):
                if file.suffix.lower() in ('.dds', '.sanmaterial') and not (staging / file.name).exists():
                    shutil.copy2(file, staging / file.name)
            preview_scene = bpy.data.scenes.new('Studio Test • ' + uid)
            context.window.scene = preview_scene
            test_context = bpy.context
            imported_rig = create_armature_from_skeleton(test_context, parse_sanskeleton_file(staging / (uid + '.sanskeleton')), uid + ' • Test Rig', s.swap_yz_axis)
            imported_mesh = import_sanmodel(test_context, model, imported_rig, s.swap_yz_axis, False)
            if isinstance(imported_mesh, tuple):
                imported_mesh = imported_mesh[0]
            apply_companion_textures_from_paths(imported_mesh, model_path, uid)
            if animation_path.exists():
                import_sananimation_bytes(imported_rig, animation_path.read_bytes(), str(animation_path), s.swap_yz_axis)
                ok('Exported animations reimported successfully')
            preview_scene.sanctuary_toolkit.export_model_object = imported_mesh
            preview_scene.sanctuary_toolkit.animation_target_armature = imported_rig
            preview_scene.sanctuary_toolkit.unit_id = uid
            preview_scene['studio_source_scene'] = original_scene.name
            preview_scene['studio_test_output'] = str(staging)
            ok('Separate reimport scene created: ' + preview_scene.name)
            lines.append('TEST FILES  ' + str(staging))
        except Exception as exc:
            fail('Round-trip: ' + str(exc))
            context.window.scene = original_scene
    lines.extend(['', 'VISUAL REVIEW: inspect textures, play clips, check orientation in the test scene.',
                  'IN-GAME REVIEW: shader appearance, effect timing, sound and faction behavior are not certified by this check.'])
    report = bpy.data.texts.new('Studio Validation • ' + (uid or 'unit'))
    report.use_fake_user = True
    report.write(f'Sanctuary Studio validation\n{len(errors)} errors • {len(warnings)} warnings\n\n' + '\n'.join(lines))
    state.report = report
    state.status = f'{len(errors)} errors • {len(warnings)} warnings. ' + ('Reimport scene ready for visual review.' if preview_scene and not errors else 'Open the report for details.')
    if preview_scene:
        preview_scene.sanctuary_studio.report = report
        preview_scene.sanctuary_studio.status = state.status
        preview_scene.sanctuary_studio.stage = 'VALIDATE'
    return not errors
