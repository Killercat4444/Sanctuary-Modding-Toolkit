"""One opt-in, reviewed append shared by hosted and standalone exports."""
from pathlib import Path

RELATIVE='append/common/loading/shieldTemplateLoader.lua'
SUPPORT_LUA='''-- Sanctuary Studio shield materials v1: opt-in only; leave stock shields alone.
local StudioOriginalCreateShieldEntity = CreateShieldEntity
function CreateShieldEntity(tp, shield, name, hierarchy, meshes, culling)
    if not shield.materialPath and not shield.glowMaterialPath then
        return StudioOriginalCreateShieldEntity(tp, shield, name, hierarchy, meshes, culling)
    end
    local faction = "EDA"
    if table.find(tp.tags, "CHOSEN") then faction = "Chosen"
    elseif table.find(tp.tags, "GUARD") then faction = "Guard" end
    local radius = math.max(shield.radii.x, shield.radii.y, shield.radii.z)
    local function addMesh(entity, material)
        table.insert(culling, EngineClasses.CullingTemplate(entity, radius))
        table.insert(meshes, EngineClasses.MeshTemplate(entity,
            "VFX/Shields/Models/Sphere_hipol.sanmodel", material,
            EngineClasses.float3(shield.radii.x, shield.radii.y, shield.radii.z),
            ShadowCastingMode.Off, EngineClasses.float4(), EngineClasses.float4(), true))
    end
    table.insert(hierarchy, EngineClasses.EntityTemplate(name, shield.boneName,
        EngineClasses.float3(shield.offset.x or 0, shield.offset.y or 0, shield.offset.z or 0),
        1.0, GetIdentityQuaternion(), false))
    addMesh(name, shield.materialPath or ("VFX/Shields/Shield Forcefield "..faction..".sanmaterial"))
    local glow = name.."_Glow"
    table.insert(hierarchy, EngineClasses.EntityTemplate(glow, name,
        EngineClasses.float3(0, 0, 0), 1.0, GetIdentityQuaternion(), false))
    addMesh(glow, shield.glowMaterialPath or ("VFX/Shields/Shield Edge Detail "..faction..".sanmaterial"))
end
'''


def is_generated_support(relative, file):
    return (str(relative).casefold()==RELATIVE.casefold() and
            Path(file).read_text(encoding='utf-8-sig').replace('\r\n','\n')==SUPPORT_LUA)


def ensure_support(project, engine):
    from .studio_core import lua_compile
    from .unit_icons import atomic
    project=Path(project).resolve();target=(project/'Lua'/RELATIVE).resolve()
    if project not in target.parents:raise ValueError('Shield support must remain inside the project.')
    if target.exists() and not is_generated_support(RELATIVE,target):
        raise ValueError('Existing shield append has custom edits; review it instead of overwriting.')
    lua_compile(SUPPORT_LUA,engine)
    target.parent.mkdir(parents=True,exist_ok=True)
    atomic(target,SUPPORT_LUA.encode('utf-8'))
    return target
