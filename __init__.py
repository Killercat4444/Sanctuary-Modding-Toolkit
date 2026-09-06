bl_info = {
    "name": "Sanctuary Modding Toolkit v1.4.9",
    "author": "OpenAI + Sanctuary modding workflow",
    "version": (1, 4, 9),
    "blender": (4, 0, 0),
    "location": "3D View > Sidebar > Sanctuary",
    "description": "Import/export Sanctuary models, skeletons, animations, textures, and browse base-game units",
    "category": "Import-Export",
}

from . import settings
from . import game_browser
from . import operators
from . import ui

_MODULES = (settings, game_browser, operators, ui)


def register():
    for module in _MODULES:
        module.register()


def unregister():
    for module in reversed(_MODULES):
        module.unregister()


print("[Sanctuary Toolkit v1.4.9] module loaded from", __file__)
