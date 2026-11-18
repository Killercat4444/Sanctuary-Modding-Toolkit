import bpy
from bpy.types import Operator, Panel, UIList
from bpy.props import EnumProperty, IntProperty

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
            # The name is editable directly in the list. Double-clicking selects
            # the whole value, which matches Blender's native text-field behavior.
            row.prop(item, "clip_name", text="", icon="ACTION", emboss=False)
            src = getattr(item, "source_armature", None)
            if src:
                row.label(text=src.name, icon="ARMATURE_DATA")
            row.label(text=f"{item.frame_start}-{item.frame_end}")
            row.prop(item, "fps", text="FPS")
        else:
            row.label(text="Choose Action", icon="ERROR")


def _active_armature(context):
    obj = context.active_object
    return obj if obj and obj.type == "ARMATURE" else None


class SANCTUARY_OT_texture_size_step(Operator):
    bl_idname = "sanctuary.texture_size_step"
    bl_label = "Change Texture Size by 1024"
    direction: IntProperty(default=1, min=-1, max=1)
    def execute(self, context):
        s=context.scene.sanctuary_toolkit
        s.texture_size=max(1024,min(8192,s.texture_size+1024*self.direction))
        return {'FINISHED'}


class SANCTUARY_PT_toolkit(Panel):
    bl_label = "Advanced / Legacy Tools"
    bl_idname = "SANCTUARY_PT_toolkit"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sanctuary"

    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sanctuary_toolkit
        prefs = get_addon_preferences(context)
        arm = _active_armature(context)
        layout.label(text="Established import/export controls")
        layout.operator("sanctuary.open_unit_browser",text="Browse Game & Mod Units",icon="VIEWZOOM")
        layout.operator("sanctuary.lua_window",text="Create Custom Lua File",icon="FILE_SCRIPT")
        box = layout.box()
        box.label(text="Active Mod Project", icon="FILE_FOLDER")
        box.prop(settings, "unit_id", text="Export Unit ID")
        box.prop(settings, "mod_active_path", text="Mod Project Location")
        row = box.row(align=True)
        row.prop(settings, "swap_yz_axis")
        box.operator("sanctuary.mod_export_unit", text="Export Complete Unit Into Project", icon="EXPORT")
        box.label(text="Existing icon is preserved. Use Studio > Icon to replace it.", icon="INFO")



class SANCTUARY_PT_mods(Panel):
    bl_label = "Mod Manager & Deployment"
    bl_idname = "SANCTUARY_PT_mods"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sanctuary"
    bl_parent_id = "SANCTUARY_PT_toolkit"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sanctuary_toolkit
        prefs = get_addon_preferences(context)
        arm = _active_armature(context)
        box = layout.box()
        box.label(text="Mod Project & Live Deployment", icon="PACKAGE")
        if prefs:
            box.prop(prefs,"mod_library_root",text="Project Library")
            box.prop(prefs,"mods_root",text="Live Mods Folder")
        row=box.row(align=True);row.prop(settings,"mod_active_path");row.operator("sanctuary.mod_open",text="",icon="FILE_FOLDER")
        box.label(text="Project edits stay local until Deploy is clicked.")
        create=box.box();create.label(text="New Project")
        create.prop(settings,"mod_project_name");create.prop(settings,"mod_tagged")
        if settings.mod_tagged:
            row=create.row(align=True);row.prop(settings,"mod_faction_name");row.prop(settings,"mod_faction_tag")
        create.operator("sanctuary.mod_create",icon="ADD")
        row=box.row(align=True);row.operator("sanctuary.mod_export_unit",icon="EXPORT");row.operator("sanctuary.mod_deploy",icon="PLAY")
        box.label(text="Unit export preserves icons. Use Studio > Icon to replace one.",icon="INFO")
        box.label(text="Deployment merges project Units/Lua/UI. Backups are optional.",icon="INFO")
        package=box.box();package.label(text="Shareable Project")
        package.prop(settings,"mod_archive_path",text="Archive / Pack")
        package.prop(settings,"mod_import_companions")
        row=package.row(align=True);row.operator("sanctuary.mod_pack",icon="PACKAGE");row.operator("sanctuary.mod_import",icon="IMPORT")
        package.label(text="Import .SMT, .zip, .rar or Units.sanpack",icon="INFO")
        remmy=box.box();remmy.label(text="Remmy Mod Manager",icon="PACKAGE")
        from . import mod_projects as _mod_projects
        current = _mod_projects.active_project(context, required=False)
        if current and ((current / "Mod Manager Compatibility" / "export.json").is_file() or (current / "Remmy" / "remmy.json").is_file()):
            remmy.prop(settings, "mod_export_version", text="Version")
            remmy.operator("sanctuary.mod_export_remmy",text="Export Dycom Mod API 1.3 ZIP",icon="EXPORT")
            remmy.label(text="Dycom-RemmyMM-<version>.zip with manual installer.",icon="INFO")
            if (current / "Standalone").is_dir():
                remmy.operator("sanctuary.mod_export_standalone",text="Export Dycom Standalone ZIP",icon="PACKAGE")
                remmy.label(text="Raw engine + Mods folders; no manager or installer.",icon="INFO")
        elif current and (current / "Remmy" / "submod.json").is_file():
            remmy.operator("sanctuary.mod_export_uber",text="Export Uber Tester ZIP",icon="EXPORT")
            remmy.label(text="Separate optional Uber Engineer installer for Dycom testers.",icon="INFO")
        else:
            remmy.label(text="This project has no Mod API exporter.",icon="INFO")



class SANCTUARY_PT_model(Panel):
    bl_label = "Model Import / Export"
    bl_idname = "SANCTUARY_PT_model"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sanctuary"
    bl_parent_id = "SANCTUARY_PT_toolkit"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sanctuary_toolkit
        prefs = get_addon_preferences(context)
        arm = _active_armature(context)
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
        box.separator()
        box.label(text="Skeleton", icon="ARMATURE_DATA")
        arm = settings.animation_target_armature or _active_armature(context)
        if arm:
            box.label(text=f"Canonical rig: {arm.name}", icon="CHECKMARK")
        else:
            box.label(text="Select an armature to enable export.", icon="INFO")
        row = box.row()
        row.enabled = arm is not None
        row.operator("sanctuary.export_skeleton", text="Export Selected Armature", icon="ARMATURE_DATA")



class SANCTUARY_PT_animation(Panel):
    bl_label = "Animations"
    bl_idname = "SANCTUARY_PT_animation"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sanctuary"
    bl_parent_id = "SANCTUARY_PT_toolkit"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sanctuary_toolkit
        prefs = get_addon_preferences(context)
        arm = _active_armature(context)
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
        row.operator("sanctuary.animation_add_all", text="Add All Selected")
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



class SANCTUARY_PT_textures(Panel):
    bl_label = "Textures & Faction Preview"
    bl_idname = "SANCTUARY_PT_textures"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Sanctuary"
    bl_parent_id = "SANCTUARY_PT_toolkit"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.sanctuary_toolkit
        prefs = get_addon_preferences(context)
        arm = _active_armature(context)
        box = layout.box()
        box.label(text="PNG â†’ Sanctuary DDS", icon="TEXTURE")
        _draw_texture_input(box, settings, "texture_albedo_png", "Albedo PNG", "ALBEDO")
        _draw_texture_input(box, settings, "texture_mask_png", "Mask / AO PNG", "MASK")
        _draw_texture_input(box, settings, "texture_normal_png", "Normal PNG", "NORMAL")
        row = box.row(align=True)
        row.operator("sanctuary.texture_size_step",text="",icon="REMOVE").direction=-1
        row.label(text=f"{settings.texture_size} Ã— {settings.texture_size}")
        row.operator("sanctuary.texture_size_step",text="",icon="ADD").direction=1
        row.prop(settings, "texture_mips")
        box.prop(settings, "albedo_alpha_mode")
        if settings.albedo_alpha_mode == "TEAM_MASK":
            _draw_texture_input(box, settings, "texture_team_mask_png", "Team Mask PNG", "ALBEDO")
        row=box.row(align=True);row.prop(settings,"preview_team_color");row.operator("sanctuary.update_team_preview",text="Apply",icon="EYEDROPPER")
        box.prop(settings, "mask_input_mode")
        if settings.mask_input_mode == "AO_ONLY":
            box.prop(settings, "mask_default_smoothness")
            box.prop(settings, "mask_ao_strength")
        box.operator("sanctuary.convert_textures", icon="IMAGE_DATA")
        box.label(text="Top Mip Only uses the bundled, verified Pillow-compatible DXT5 encoder.", icon="INFO")
        box.label(text="Creates canonical DDS files and refreshes the unit .sanmaterial.", icon="FILE_IMAGE")


CLASSES = (SANCTUARY_OT_texture_input_help, SANCTUARY_OT_texture_size_step, SANCTUARY_UL_animations,
    SANCTUARY_PT_toolkit,
    SANCTUARY_PT_mods,
    SANCTUARY_PT_model,
    SANCTUARY_PT_animation,
    SANCTUARY_PT_textures,
)

def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
