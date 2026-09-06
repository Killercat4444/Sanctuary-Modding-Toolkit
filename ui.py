import bpy
from bpy.types import Operator, Panel, UIList
from bpy.props import EnumProperty

from .utils import get_addon_preferences, resolve_engine_root
from .texture_help import texture_input_description, texture_input_help


class SANCTUARY_OT_texture_input_help(Operator):
    bl_idname = "sanctuary.texture_input_help"
    bl_label = "Which Texture Goes Here?"
    bl_options = {"INTERNAL"}

    slot: EnumProperty(
        name="Texture Input",
        items=(
            ("ALBEDO", "Albedo", "Base color / diffuse texture"),
            ("MASK", "Mask / AO", "Ambient occlusion or a packed Sanctuary mask"),
            ("NORMAL", "Normal", "Tangent-space normal texture"),
        ),
    )

    @classmethod
    def description(cls, context, properties):
        settings = getattr(getattr(context, "scene", None), "sanctuary_toolkit", None)
        return texture_input_description(properties.slot, getattr(settings, "mask_input_mode", None))

    def execute(self, context):
        settings = getattr(context.scene, "sanctuary_toolkit", None)
        title, lines = texture_input_help(self.slot, getattr(settings, "mask_input_mode", None))

        def draw_help(menu, _context):
            for line in lines:
                menu.layout.label(text=line)

        context.window_manager.popup_menu(draw_help, title=title, icon="INFO")
        return {"FINISHED"}


def _draw_texture_input(layout, settings, property_name, label, slot):
    row = layout.row(align=True)
    row.prop(settings, property_name, text=label)
    row.operator("sanctuary.texture_input_help", text="", icon="QUESTION").slot = slot


class SANCTUARY_UL_animations(UIList):
    bl_idname = "SANCTUARY_UL_animations"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        if item.action:
            row.label(text=item.clip_name or item.action.name, icon="ACTION")
            src = getattr(item, "source_armature", None)
            if src:
                row.label(text=src.name, icon="ARMATURE_DATA")
            row.label(text=f"{item.frame_start}-{item.frame_end}")
        else:
            row.label(text="Choose Action", icon="ERROR")


def _active_armature(context):
    obj = context.active_object
    return obj if obj and obj.type == "ARMATURE" else None


class SANCTUARY_PT_toolkit(Panel):
    bl_label = "Sanctuary Modding Toolkit"
    bl_idname = "SANCTUARY_PT_toolkit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sanctuary"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sanctuary_toolkit
        prefs = get_addon_preferences(context)
        vrow = layout.row()
        vrow.label(text="v1.4.9 • preserved export normals", icon="CHECKMARK")

        box = layout.box()
        box.label(text="Project", icon="FILE_FOLDER")
        box.prop(settings, "unit_id")
        box.prop(settings, "output_dir")
        row = box.row(align=True)
        row.prop(settings, "swap_yz_axis")
        box.operator("sanctuary.export_complete_assets", icon="EXPORT")

        # The old long inline unit list is intentionally gone. The spyglass opens
        # a wide, image-card unit browser instead.
        box = layout.box()
        header = box.row(align=True)
        header.label(text="Base Game Units")
        spy = header.row(align=True)
        spy.scale_x = 1.4
        spy.operator("sanctuary.open_unit_browser", text="", icon="VIEWZOOM")
        if prefs:
            box.prop(prefs, "sanctuary_root", text="Game Folder")
        engine = resolve_engine_root(prefs.sanctuary_root if prefs else "")
        row = box.row(align=True)
        row.enabled = engine is not None
        row.operator("sanctuary.build_unit_database", text="Rebuild Unit Index", icon="FILE_REFRESH")
        if engine is None:
            box.label(text="Choose the Sanctuary folder (or engine folder) first.", icon="INFO")
        else:
            box.label(text="Click the spyglass to browse units.", icon="INFO")

        box = layout.box()
        box.label(text="Model", icon="MESH_DATA")
        box.prop(settings, "export_model_object", text="Canonical Mesh")
        row = box.row(align=True)
        row.operator("sanctuary.import_sanmodel", icon="IMPORT")
        row.operator("sanctuary.export_sanmodel", icon="EXPORT")
        row = box.row(align=True)
        row.prop(settings, "import_companion_textures", text="Auto Textures")
        row.prop(settings, "import_companion_animations", text="Auto Animations")
        texrow = box.row()
        texrow.enabled = bool(context.active_object and context.active_object.type == "MESH" and context.active_object.get("san_source_model_path"))
        texrow.operator("sanctuary.load_companion_textures", icon="TEXTURE")
        box.operator("sanctuary.validate_rig", icon="CHECKMARK")
        box.label(text="Skin export maps vertex-group names to stable skeleton bone IDs.", icon="INFO")
        box.label(text="Sanctuary stores one bone ID per vertex; strongest weight wins.", icon="INFO")

        box = layout.box()
        box.label(text="Skeleton", icon="ARMATURE_DATA")
        arm = _active_armature(context)
        if arm:
            box.label(text=f"Selected: {arm.name}", icon="CHECKMARK")
        else:
            box.label(text="Select an armature to enable export.", icon="INFO")
        row = box.row()
        row.enabled = arm is not None
        row.operator("sanctuary.export_skeleton", text="Export Selected Armature", icon="ARMATURE_DATA")

        box = layout.box()
        box.label(text="Animations", icon="ACTION")
        preview = box.box()
        preview.label(text="Imported Clip Preview", icon="PLAY")
        preview.prop(settings, "preview_animation")
        prow = preview.row(align=True)
        prow.operator("sanctuary.preview_animation_play", text="Play", icon="PLAY")
        prow.operator("sanctuary.preview_animation_stop", text="Pause", icon="PAUSE")
        prow.operator("sanctuary.preview_animation_restore", text="Restore Rig", icon="LOOP_BACK")
        loadrow = preview.row()
        loadrow.enabled = arm is not None or bool(context.active_object and context.active_object.type == "MESH" and context.active_object.find_armature())
        loadrow.operator("sanctuary.load_sananimation", text="Load .sananimation", icon="IMPORT")
        preview.label(text="Imported clips are real Blender Actions and remain editable in the Dope Sheet.", icon="INFO")

        box.separator()
        box.label(text="Animation Export", icon="EXPORT")
        target = box.box()
        target.label(text="Pinned Blender Rig", icon="ARMATURE_DATA")
        target.prop(settings, "animation_target_armature", text="")
        trow = target.row()
        trow.enabled = arm is not None
        trow.operator("sanctuary.animation_set_target", text="Use Selected Armature", icon="EYEDROPPER")
        target.prop(settings, "animation_default_fps")
        target.label(text="Model/Skeleton export automatically sets this canonical rig.", icon="INFO")
        target.label(text="Source rigs may contain IK/helper bones; they are never used as the target manifest.", icon="INFO")
        row = box.row(align=True)
        row.operator("sanctuary.animation_add_current", text="Add Current")
        row.operator("sanctuary.animation_add_all", text="Add All")
        row.operator("sanctuary.animation_remove", text="Remove")
        row.operator("sanctuary.animation_clear", text="Clear")
        box.template_list(
            "SANCTUARY_UL_animations",
            "",
            settings,
            "animations",
            settings,
            "animation_index",
            rows=4,
        )
        if 0 <= settings.animation_index < len(settings.animations):
            item = settings.animations[settings.animation_index]
            detail = box.box()
            detail.prop(item, "enabled")
            detail.prop(item, "action")
            detail.prop(item, "source_armature")
            detail.prop(item, "source_evaluation")
            if item.action and item.action.get("san_native_key_import", False):
                detail.prop(item, "preserve_native_curves")
            detail.prop(item, "clip_name")
            row = detail.row(align=True)
            row.prop(item, "frame_start")
            row.prop(item, "frame_end")
            row = detail.row(align=True)
            row.prop(item, "fps")
            row.prop(item, "loop")
        row = box.row()
        has_target = bool(settings.animation_target_armature)
        row.enabled = has_target and any(item.enabled and item.action for item in settings.animations)
        row.operator("sanctuary.export_animations", icon="EXPORT")
        box.label(text="Each clip freezes its Source Rig pose when added; later selection/save state cannot change it.", icon="INFO")
        box.label(text="The sibling .sanskeleton file is the canonical animation target.", icon="INFO")
        box.label(text="Baked Pose preserves authored poses without rest-pose retargeting or a second IK solve.", icon="INFO")

        box = layout.box()
        box.label(text="PNG → Sanctuary DDS", icon="TEXTURE")
        _draw_texture_input(box, settings, "texture_albedo_png", "Albedo PNG", "ALBEDO")
        _draw_texture_input(box, settings, "texture_mask_png", "Mask / AO PNG", "MASK")
        _draw_texture_input(box, settings, "texture_normal_png", "Normal PNG", "NORMAL")
        row = box.row(align=True)
        row.prop(settings, "texture_size")
        row.prop(settings, "texture_mips")
        box.prop(settings, "albedo_alpha_mode")
        box.prop(settings, "mask_input_mode")
        if settings.mask_input_mode == "AO_ONLY":
            box.prop(settings, "mask_default_smoothness")
        box.operator("sanctuary.convert_textures", icon="IMAGE_DATA")
        box.label(text="Top Mip Only uses the bundled, verified Pillow-compatible DXT5 encoder.", icon="INFO")
        box.label(text="DDS filenames follow the unit material (default: _Albedo.dds).", icon="FILE_IMAGE")


CLASSES = (
    SANCTUARY_OT_texture_input_help,
    SANCTUARY_UL_animations,
    SANCTUARY_PT_toolkit,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
