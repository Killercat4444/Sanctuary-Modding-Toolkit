bl_info = {
    "name": "Sanctuary Studio 2.2.10 Preview",
    "author": "OpenAI + Sanctuary modding workflow",
    "version": (2, 2, 10),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar > Sanctuary",
    "description": "Import/export Sanctuary assets, author and deploy mod projects, and browse game/mod units",
    "category": "Import-Export",
}

from . import settings
from . import game_browser
from . import operators
from . import mod_projects
from . import lua_editor
from . import ui
from . import studio
from . import studio_sizes
from . import icon_camera, unit_icon_preview
from . import shared_project
from . import studio_workshop, studio_vfx, studio_lua, studio_publishing, studio_shields, shield_import

_MODULES = (settings, game_browser, operators, mod_projects, lua_editor, ui, studio, studio_sizes, icon_camera, unit_icon_preview, shared_project, studio_workshop, studio_vfx, studio_lua, studio_publishing, studio_shields, shield_import)


def register():
    for module in _MODULES:
        module.register()


def unregister():
    for module in reversed(_MODULES):
        module.unregister()


print("[Sanctuary Studio 2.2.10 Preview] module loaded from", __file__)
