"""Build Dycom's patcher-owned, in-memory Lua cache payload.

Only Dycom-only paths are stored as complete files. Existing Sanctuary paths
are represented by small, context-checked edits against the matching stock
game build so SCOM's changes to those paths are not replaced wholesale.
"""
from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
import zipfile


def _text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def _scom_construction_preview_edits(desired: str) -> list[tuple[str, str, bool]]:
    """Rebase the odd-grid fix onto SCOM's structure-aware preview lines."""
    required = (
        "local halfCellX = isDycom and unitTemplate.skirtSize.x % 2 == 1",
        "local halfCellZ = isDycom and unitTemplate.skirtSize.y % 2 == 1",
        "PlacementUtils.GetPlacementBounds(snappedPoint, unitTemplate.skirtSize)",
        "PlacementUtils.GetPlacementHeight(unitTemplate, snappedPoint.x, snappedPoint.z)",
    )
    if not all(marker in desired for marker in required):
        raise ValueError("Dycom preview grid fix changed; update the SCOM merge before exporting")
    anchor = "    local domain = PlacementUtils.GetPlacementDomain(unitTemplate)\n"
    start = desired.index("    local mapSizeX = GameInfo.MapInfo.size[1]\n")
    end = desired.index("    for _, point in pairs(buildingPositions) do\n", start)
    new_prefix = desired[start:end]
    old_prefix = ("    local mapSizeX = GameInfo.MapInfo.size[1]\n"
                  "    local mapSizeY = GameInfo.MapInfo.size[2]\n" + anchor + "\n")
    # SCOM's Sea Parity pass rewrites every preview skirt lookup before our
    # post-SCOM cache hook runs. Keep its effective placement size intact.
    old_bounds = "        local minX, minY, maxX, maxY = PlacementUtils.GetPlacementBounds(point, SCOSeaParityPlacementSize(unitTemplate))\n"
    new_bounds = ("        local snappedPoint = { x = point.x + halfCellX, z = point.z + halfCellZ }\n"
                  "        local minX, minY, maxX, maxY = PlacementUtils.GetPlacementBounds(snappedPoint, SCOSeaParityPlacementSize(unitTemplate))\n")
    old_place = "CanPlaceAtArea(minX, minY, maxX, maxY, domain, isAlloyExtractor, unitTemplate, point)"
    # SCOM puts its water-placement height adjustment between these lines.
    # Patch each line separately so that adjustment stays in place.
    old_height = "                local targetHeight = PlacementUtils.GetPlacementHeight(unitTemplate, point.x, point.z)\n"
    new_height = "                local targetHeight = PlacementUtils.GetPlacementHeight(unitTemplate, snappedPoint.x, snappedPoint.z)\n"
    old_insert = "                table.insert(filteredPositions, EngineClasses.float3(point.x, targetHeight, point.z))\n"
    new_insert = "                table.insert(filteredPositions, EngineClasses.float3(snappedPoint.x, targetHeight, snappedPoint.z))\n"
    return [
        (old_prefix, new_prefix, False),
        (old_bounds, new_bounds, False),
        (old_place, old_place.replace("unitTemplate, point)", "unitTemplate, snappedPoint)"), False),
        ("OverhaulFactoryPlacementReservationsAreClear(point, unitTemplate)",
         "OverhaulFactoryPlacementReservationsAreClear(snappedPoint, unitTemplate)", True),
        (old_height, new_height, False),
        (old_insert, new_insert, False),
    ]


def _template_loader_edits(desired: str) -> list[tuple[str, str, bool]]:
    """Anchor tarmac/factory hooks to one stable line each, preserving SCOM edits."""
    nuke = '    _G.NukeVFXPrefabID = VFXTemplateLoader.GetOrCreateVFXPrefab("VFX/Death/vcd0301_death_rdu.sanvfx")\n'
    old_create = "        local errorCode, unitPrefabID = UnitLoader.CreateUnitPrefab(tp)\n"
    old_effect = "function LoadEffect(effectName)\n"
    try:
        nuke_start = desired.index(nuke)
        nuke_end = desired.index("    _G.TarmacEDAPrefabID", nuke_start)
        create_start = desired.index("        local created, errorCode, unitPrefabID = pcall(UnitLoader.CreateUnitPrefab, tp)\n")
        create_end = desired.index("        -- These are required for UI", create_start)
        effect_start = desired.index(old_effect)
        effect_end = desired.index("    Engine.LoadEffect(effectName)\n", effect_start)
    except ValueError as error:
        raise ValueError("Dycom templateLoader hooks changed; update the SCOM merge before exporting") from error
    edits = [
        (nuke, desired[nuke_start:nuke_end], False),
        (old_create, desired[create_start:create_end].rstrip(" \n") + "\n", False),
        (old_effect, desired[effect_start:effect_end], False),
    ]
    wreck_old = '''    wTp.visuals = {
        skeleton = "Units/"..tpId.."/LOD0/"..tpId..".sanskeleton",
        isWreckage = true,
        lods = {
            {
                distance = 100.0,
                shadowCastingMode = 1,
                model = "Units/"..tpId.."/LOD0/"..tpId.."_lod0.sanmodel",
                material = "Units/"..tpId.."/LOD0/"..tpId.."_lod0.sanmaterial"
            }
        }
    }
'''
    wreck_new = '''    local modelTpId = tp.general.modelTpId or tpId
    wTp.visuals = {
        skeleton = "Units/"..modelTpId.."/LOD0/"..modelTpId..".sanskeleton",
        isWreckage = true,
        lods = {
            {
                distance = 100.0,
                shadowCastingMode = 1,
                model = "Units/"..modelTpId.."/LOD0/"..modelTpId.."_lod0.sanmodel",
                material = "Units/"..modelTpId.."/LOD0/"..modelTpId.."_lod0.sanmaterial"
            }
        }
    }
'''
    hierarchy_old = '''                processedPrefabIds[prefabId] = true

                local unitPath = "Units/"..unitTemplate.general.tpId.."/LOD0/"..unitTemplate.general.tpId
'''
    hierarchy_new = '''                processedPrefabIds[prefabId] = true

                local modelTpId = unitTemplate.general.modelTpId or unitTemplate.general.tpId
                local unitPath = "Units/"..modelTpId.."/LOD0/"..modelTpId
'''
    wreck_hierarchy_old = '''            local unitTemplate = GetTemplate(propTemplate.general.unitTpId, "Units")
            local unitPath = "Units/"..unitTemplate.general.tpId.."/LOD0/"..unitTemplate.general.tpId
'''
    wreck_hierarchy_new = '''            local unitTemplate = GetTemplate(propTemplate.general.unitTpId, "Units")
            local modelTpId = unitTemplate.general.modelTpId or unitTemplate.general.tpId
            local unitPath = "Units/"..modelTpId.."/LOD0/"..modelTpId
'''
    model_alias_edits = (
        (wreck_old, wreck_new, False),
        (hierarchy_old, hierarchy_new, False),
        (wreck_hierarchy_old, wreck_hierarchy_new, False),
    )
    for old, new, optional in model_alias_edits:
        if new not in desired:
            raise ValueError("Dycom modelTpId hierarchy support changed; update the export merge")
        edits.append((old, new, optional))
    if not all(old != new for old, new, _ in edits):
        raise ValueError("Dycom templateLoader lost a required tarmac/factory/effect hook")
    return edits


def build_cache_payload(project: Path, stock_lua: Path, output: Path,
                        faction_variants: dict[str, str]) -> dict[str, int]:
    lua_root = project / "Lua"
    if not stock_lua.is_dir():
        raise FileNotFoundError(f"Stock Sanctuary Lua directory is missing: {stock_lua}")
    tarmac_required = (
        "client/units/dycomTarmac.lua",
        "client/units/unitsClasses/unitsDycomFactories.lua",
        "common/loading/dycomDecalTemplateLoader.lua",
        "common/systems/templateLoader.lua",
    )
    for relative in tarmac_required:
        if not (lua_root / relative).is_file():
            raise FileNotFoundError(f"Dycom factory/tarmac dependency is missing: {relative}")
    if "TarmacDycomSquarePrefabID" not in _text(lua_root / "common/systems/templateLoader.lua"):
        raise ValueError("Dycom's tarmac prefab registration is missing")
    counts = {"lua_sources": 0, "physical_only_lua": 0,
              "excluded_foreign_templates": 0,
              "virtual_lua": 0, "shared_patches": 0, "patch_hunks": 0, "decals": 0}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for source in sorted(lua_root.rglob("*")):
            if not source.is_file() or source.suffix.lower() not in {".lua", ".santp"}:
                continue
            relative = source.relative_to(lua_root).as_posix()
            if relative.startswith("common/units/unitsTemplates/") and not relative.split("/")[3].lower().startswith("ud"):
                # SCOM's copied Chosen/EDA templates are not part of Dycom and
                # must not be baked into a standalone or combined install.
                counts["excluded_foreign_templates"] += 1
                continue
            counts["lua_sources"] += 1
            # Sanctuary discovers AI formers and strategies with the native
            # filesystem enumerator, so AI-Dycom is deliberately installed as
            # a real project-owned folder by hosted_export.py.  Do not also
            # add these same paths to the virtual cache: the cache guard treats
            # a physical/virtual duplicate as an unsafe collision and aborts
            # all later Dycom Lua patches, including faction registration.
            if relative.startswith("AI/mods/AI-Dycom/"):
                counts["physical_only_lua"] += 1
                continue
            stock = stock_lua / relative
            if not stock.is_file():
                archive.write(source, "files/LJ/lua/" + relative)
                counts["virtual_lua"] += 1
                continue
            variants = ({"standalone": faction_variants["standalone"],
                         "scom": faction_variants["scom"]}
                        if relative == "common/systems/factions.lua" else
                        {"common": _text(source)})
            stock_text = _text(stock)
            original = stock_text.splitlines(keepends=True)
            for mode, desired_text in variants.items():
                # A different final newline is not a semantic mod edit and can
                # accidentally conflict with SCOM's own appended scripts.
                if not stock_text.endswith("\n"):
                    desired_text = desired_text.rstrip("\n")
                base = f"patches/{mode}/{relative}/"
                if relative == "common/systems/templateLoader.lua":
                    edits = _template_loader_edits(desired_text)
                else:
                    desired = desired_text.splitlines(keepends=True)
                    groups = list(SequenceMatcher(None, original, desired,
                                                  autojunk=False).get_grouped_opcodes(1))
                    edits = [("".join(original[group[0][1]:group[-1][2]]),
                              "".join(desired[group[0][3]:group[-1][4]]), False)
                             for group in groups]
                simulated = stock_text
                for number, (old, new, _) in enumerate(edits):
                    if simulated.count(old) != 1:
                        raise ValueError(f"Ambiguous stock Lua patch: {relative} hunk {number}")
                    simulated = simulated.replace(old, new, 1)
                # templateLoader is deliberately represented by three narrow
                # hooks.  The current stock game may contain newer unrelated
                # code (for example scorch marks) that must be preserved.
                if relative != "common/systems/templateLoader.lua" and simulated != desired_text:
                    raise ValueError(f"Stock Lua patch did not reproduce project source: {relative}")
                if edits:
                    archive.writestr(base + "count.txt", str(len(edits)))
                    counts["shared_patches"] += 1
                for number, (old, new, _) in enumerate(edits):
                    if not old:
                        raise ValueError(f"Cannot safely anchor an insertion in {relative}")
                    archive.writestr(base + f"{number:03}.old", old.encode("utf-8"))
                    archive.writestr(base + f"{number:03}.new", new.encode("utf-8"))
                    counts["patch_hunks"] += 1
            if relative == "client/input/constructionPreviewSystem.lua":
                edits = _scom_construction_preview_edits(_text(source))
                base = f"patches/scom/{relative}/"
                archive.writestr(base + "count.txt", str(len(edits)))
                counts["shared_patches"] += 1
                for number, (old, new, optional) in enumerate(edits):
                    archive.writestr(base + f"{number:03}.old", old.encode("utf-8"))
                    archive.writestr(base + f"{number:03}.new", new.encode("utf-8"))
                    if optional:
                        archive.writestr(base + f"{number:03}.optional", "1")
                    counts["patch_hunks"] += 1
        # These descriptors are looked up through FilesCache, not merely by
        # mounting the Environment asset pack. They are vital to Dycom tarmacs.
        for source in sorted((project / "Environment").rglob("*.sandecal")):
            relative = source.relative_to(project).as_posix()
            archive.write(source, "files/" + relative)
            counts["decals"] += 1
    if not counts["virtual_lua"] or not counts["shared_patches"] or not counts["decals"]:
        raise ValueError("Dycom cache payload is missing Lua files, shared patches, or tarmac decals")
    return counts
