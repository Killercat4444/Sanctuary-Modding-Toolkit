"""Export an SMT project as a native Sanctuary Mod API 1.3 mod.

The framework owns faction registration, AI routing, unit discovery and
match-scoped art mounting. Dycom includes one narrowly-scoped gameplay plugin
to collapse commander variants into its separate commander selector and to
write diagnostics. A hosted export is one ``Dycom`` folder for
``engine/SanctuaryMods``. The release ZIP also carries a small, audited root
installer for users who prefer manual installation; it must not contain a
preloader or whole copies of game Lua files.

The standalone exporter imports a few generic helpers from this module; keep
those helpers independent of Mod API packaging.
"""
from __future__ import annotations

from pathlib import Path, PurePosixPath
import hashlib
import concurrent.futures
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile

from .remmy_export import ASSET_FOLDERS, LUA_SUFFIXES, _safe_name


FORMAT_NAME = "Sanctuary Mod API 1.3"
ART_FOLDERS = ASSET_FOLDERS
INSTALLER_FILES = {"installer.bat", "Install-Dycom.ps1"}
HOSTED_PLUGIN_SOURCES = (
    "DycomMod.cs", "DycomLobbyRuntime.cs", "DycomAiLobby.cs",
    "DycomAiLogContext.cs", "DycomIconColorPolicy.cs", "DycomAssetsPatcher.cs",
    "DycomSessionArchiveBridge.cs",
)
OWNED_LUA_MODULES = {
    "client/ui/reclaimerpanel.lua", "client/units/reclaimerconstructionbeams.lua",
    "common/loading/reclaimerbeams.lua", "common/units/grizzlymotion.lua",
    "common/units/grizzlyrig.lua", "host/units/reclaimereconomy.lua",
    "host/units/reclaimertargets.lua",
}


def normalize_version(value: str) -> str:
    """Return a manifest/filename-safe release version."""
    version = str(value).strip()
    if not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._+-]{0,31}", version):
        raise ValueError("Version must use 1-32 letters, numbers, dots, dashes, plus signs, or underscores")
    return version


def _release_lua(path: Path, root: Path) -> bool:
    """Common release boundary: transport hooks remain development-only."""
    if not _wanted(path, root, LUA_SUFFIXES):
        return False
    relative = path.relative_to(root).as_posix().casefold()
    if relative.startswith("append/"):
        from .shield_support import is_generated_support
        return is_generated_support(relative,path)
    if relative.startswith("common/units/unitstemplates/"):
        return relative.split("/")[3].startswith("ud")
    return True


def _validate_unit_assets(project: Path, lua_files) -> None:
    """Fail before packaging if a shipped Dycom unit lacks its core art."""
    missing = []
    for file in lua_files:
        if file.suffix.lower() != ".santp" or not file.stem.lower().startswith("ud"):
            continue
        model = re.search(r'\bmodelTpId\s*=\s*["\']([^"\']+)["\']', file.read_text(encoding="utf-8-sig"))
        unit = model.group(1) if model else file.stem
        for name in (f"{unit}_lod0.sanmodel", f"{unit}.sanskeleton", f"{unit}_lod0.sanmaterial"):
            asset = project / "Units" / unit / "LOD0" / name
            if not asset.is_file():
                missing.append(str(asset.relative_to(project)))
        material = project / "Units" / unit / "LOD0" / f"{unit}_lod0.sanmaterial"
        if material.is_file():
            for texture in json.loads(material.read_text(encoding="utf-8-sig")).get("textures", []):
                path = texture.get("path", "")
                if path.lower().startswith("units/ud") and not (project / path).is_file():
                    missing.append(path)
        for path in re.findall(r'["\'](Units/ud[^"\']+\.sananimation)["\']', file.read_text(encoding="utf-8-sig")):
            if not (project / path).is_file():
                missing.append(path)
    if missing:
        raise ValueError("Missing unit assets: " + ", ".join(missing))

# Whole game files which the old Dycom distribution copied or modified. The
# framework now supplies their former responsibilities, or ModApi-specific
# append sources replace only the narrow behavior Dycom still needs.
FORBIDDEN_DIRECT_LUA = {
    "ai/_lobbyoptions.lua",
    "ai/_version.lua",
    "ai/aifunctions.lua",
    "ai/aigametelemetry.lua",
    "ai/aiinit.lua",
    "ai/ailocationcreator.lua",
    "ai/ailocationmanager.lua",
    "ai/aimarkergenerator.lua",
    "ai/aistrategymanager.lua",
    "ai/aitargetmanager.lua",
    "ai/debugutilities.lua",
    "ai/profilerai.lua",
    "ai/projectileblueprintvalidator.lua",
    "ai/unitblueprintvalidator.lua",
    "client/input/constructionpreviewsystem.lua",
    "client/rendering/dycomscreenshotmode.lua",
    "client/rendering/rendering.lua",
    "client/ui/constructionpanel.lua",
    "client/units/unitsclasses/unitsdefault.lua",
    "client/units/dycomupgradepresentation.lua",
    "common/gameutils.lua",
    "common/loading/adjacencyconnectorloader.lua",
    "common/loading/constructionspawnpositionloader.lua",
    "common/loading/shieldtemplateloader.lua",
    "common/loading/unittemplateloader.lua",
    "common/systems/factions.lua",
    "common/systems/templateloader.lua",
    "common/units/availableunits.lua",
    "common/units/quicklaunchunits.lua",
    "common/units/unitstemplates/ucl0000/ucl0000.santp",
    "common/units/unitstemplates/ucl1501/ucl1501.santp",
    "common/units/unitstemplates/ues2511/ues2511.santp",
    "common/utilities/buildqueueutils.lua",
}

FORBIDDEN_ARCHIVE_SUFFIXES = {
    ".exe", ".cmd", ".bat", ".ps1", ".pdb", ".config",
}


def _digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def _wanted(path: Path, root: Path, suffixes: set[str] | None = None) -> bool:
    relative = path.relative_to(root)
    if any(part.startswith(".") or part == "__pycache__" for part in relative.parts):
        return False
    return suffixes is None or path.suffix.lower() in suffixes


def _game_root(config: dict) -> Path:
    if config.get("game_root"):
        return Path(config["game_root"]).expanduser().resolve()
    stock = config.get("stock_lua")
    if stock:
        path = Path(stock).expanduser().resolve()
        if path.name.casefold() == "lua" and path.parent.name.casefold() == "lj":
            return path.parents[1]
    raise ValueError("export.json needs game_root (the engine folder)")


def _validate_manifest(data: dict) -> None:
    required_strings = (
        "id", "name", "version", "author", "description", "kind",
        "gameVersion", "luaRoot",
    )
    missing = [key for key in required_strings if not isinstance(data.get(key), str) or not data[key].strip()]
    if missing:
        raise ValueError("Mod API mod.json is missing: " + ", ".join(missing))
    if data["kind"] != "gameplay":
        raise ValueError("Dycom's Mod API manifest must use kind 'gameplay'")
    if data.get("apiVersion") != 1:
        raise ValueError("Mod API 1.3 uses apiVersion 1")
    lua_root = PurePosixPath(data["luaRoot"].replace("\\", "/"))
    if lua_root.is_absolute() or ".." in lua_root.parts or str(lua_root) in ("", "."):
        raise ValueError("mod.json luaRoot must be a folder inside the mod")

    factions = data.get("factions")
    if not isinstance(factions, list) or len(factions) != 1:
        raise ValueError("Dycom mod.json must declare exactly one factions entry")
    faction = factions[0]
    required_faction = {
        "key": "dycom", "name": "Dycom", "tag": "DYCOM",
        "unitPrefix": "ud", "looksLike": "CHOSEN",
    }
    for key, expected in required_faction.items():
        if faction.get(key) != expected:
            raise ValueError(f"Dycom faction {key} must be {expected!r}")
    if faction.get("icon") != "icons/dycom.png":
        raise ValueError("Dycom faction icon must be icons/dycom.png")
    commanders = faction.get("commanders")
    expected_commanders = {("Spider", "udl0000"), ("Mech", "udl0001")}
    actual_commanders = {
        (entry.get("name"), entry.get("unit"))
        for entry in commanders or [] if isinstance(entry, dict)
    }
    if actual_commanders != expected_commanders:
        raise ValueError("Dycom must declare Spider/udl0000 and Mech/udl0001 commanders")
    ai = faction.get("ai")
    if not isinstance(ai, dict) or ai.get("folder") != "AI/mods/AI-Dycom":
        raise ValueError("Dycom AI folder must be AI/mods/AI-Dycom")
    if ai.get("air") is not False or ai.get("naval") is not False:
        raise ValueError("Dycom AI must currently declare air=false and naval=false")


def _find_compiler() -> tuple[Path, Path] | None:
    roots = []
    programs = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    hub = programs / "Unity"
    if hub.is_dir():
        roots.extend(sorted(hub.glob("*/Editor/Data/MonoBleedingEdge"), reverse=True))
    roots.extend(sorted(programs.glob("Unity */Editor/Data/MonoBleedingEdge"), reverse=True))
    for root in roots:
        data = root.parent
        runtime = data / "NetCoreRuntime" / "dotnet.exe"
        modern = data / "DotNetSdkRoslyn" / "csc.dll"
        if runtime.is_file() and modern.is_file():
            return runtime, modern
        mono = root / "bin" / "mono.exe"
        csc = root / "lib" / "mono" / "msbuild" / "Current" / "bin" / "Roslyn" / "csc.exe"
        if mono.is_file() and csc.is_file():
            return mono, csc
    return None


def _compile(mono: Path, csc: Path, output: Path, sources: list[Path], references: list[Path]) -> None:
    """Generic compiler helper retained for the standalone exporter."""
    missing = [str(path) for path in sources + references if not path.is_file()]
    if missing:
        raise FileNotFoundError("Cannot compile plugin; missing: " + ", ".join(missing))
    options = []
    if csc.suffix.lower() == ".dll":
        framework = csc.parents[1] / "MonoBleedingEdge/lib/mono/4.7.2-api"
        framework_references = [framework / name for name in
            ("mscorlib.dll", "System.dll", "System.Core.dll", "System.Runtime.Serialization.dll")]
        if any(not path.is_file() for path in framework_references):
            raise FileNotFoundError("Unity .NET Framework 4.7.2 reference assemblies are missing")
        references = framework_references + references
        options.append("/nostdlib+")
    command = [
        str(mono), str(csc), *options, "/nologo", "/target:library", "/langversion:latest",
        "/optimize+", "/debug-", "/deterministic+", f"/out:{output}",
        *[f"/reference:{path}" for path in references],
        *[str(path) for path in sources],
    ]
    result = subprocess.run(command, cwd=str(csc.parent), capture_output=True, text=True)
    if result.returncode or not output.is_file():
        detail = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise RuntimeError(f"Failed to compile {output.name}.\n{detail or 'Compiler returned no details.'}")
    if output.stat().st_size < 1024 or output.read_bytes()[:2] != b"MZ":
        raise RuntimeError(f"Compiler produced an invalid managed DLL: {output}")


def _build_hosted_plugin(game: Path, compatibility: Path, output: Path) -> Path:
    compiler = _find_compiler()
    if compiler is None:
        raise RuntimeError("A Unity Roslyn compiler was not found; Mod API export refuses a stale Dycom plugin")
    mono, csc = compiler
    _compile(mono, csc, output, [compatibility / name for name in HOSTED_PLUGIN_SOURCES], [
        game / "Sanctuary_Data" / "Managed" / "netstandard.dll",
        game / "BepInEx" / "core" / "BepInEx.dll",
        game / "BepInEx" / "core" / "0Harmony.dll",
        game / "BepInEx" / "core" / "Mono.Cecil.dll",
        game / "Sanctuary_Data" / "Managed" / "UnityEngine.dll",
        game / "Sanctuary_Data" / "Managed" / "UnityEngine.CoreModule.dll",
        game / "Sanctuary_Data" / "Managed" / "UnityEngine.ImageConversionModule.dll",
        game / "Sanctuary_Data" / "Managed" / "UnityEngine.IMGUIModule.dll",
        game / "Sanctuary_Data" / "Managed" / "UnityEngine.TextRenderingModule.dll",
        game / "Sanctuary_Data" / "Managed" / "UnityEngine.UI.dll",
        game / "Sanctuary_Data" / "Managed" / "UnityEngine.UIModule.dll",
        game / "Sanctuary_Data" / "Managed" / "Unity.TextMeshPro.dll",
        game / "Sanctuary_Data" / "Managed" / "Unity.Collections.dll",
        game / "Sanctuary_Data" / "Managed" / "Michsky.UI.Beam.dll",
        game / "Sanctuary_Data" / "Managed" / "Trebuchet.dll",
        game / "BepInEx" / "plugins" / "Sanctuary.ModApi.dll",
    ])
    return output


def _normal_path(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _is_allowed_direct_lua(relative: str) -> bool:
    lower = relative.casefold()
    if lower.startswith("append/"):
        return True
    if lower.startswith("ai/mods/ai-dycom/") or re.match(
        r"^ai/mods/ai-dycom-(adaptive|random|rush|tech|turtle)/", lower):
        return True
    if lower in OWNED_LUA_MODULES or re.fullmatch(r"common/units/ud[ls]\d{4}config\.lua", lower):
        return True
    if lower.startswith("common/units/unitstemplates/ud"):
        return True
    if lower.startswith("common/projectiles/projectilestemplates/pd"):
        return True
    # Previous Studio versions generated pud... for Dycom weapon copies.
    if lower.startswith("common/projectiles/projectilestemplates/pud"):
        return True
    # Dycom-owned modules/classes are additions, never replacements of stock.
    return "dycom" in PurePosixPath(lower).name


def _collect_lua(project: Path, compatibility: Path) -> tuple[dict[str, Path], list[str]]:
    project_lua = project / "Lua"
    if not project_lua.is_dir():
        raise FileNotFoundError(f"Dycom project Lua folder is missing: {project_lua}")

    output: dict[str, Path] = {}
    skipped: list[str] = []
    for file in sorted(project_lua.rglob("*")):
        if not file.is_file() or not _wanted(file, project_lua, LUA_SUFFIXES):
            continue
        relative = _normal_path(file, project_lua)
        lower = relative.casefold()
        # Mod API append hooks are release-specific. Only the reviewed hooks
        # under Mod Manager Compatibility/Lua/append may enter a hosted build;
        # project/Lua/append is retained as dormant development groundwork.
        if lower.startswith("append/"):
            from .shield_support import is_generated_support
            if is_generated_support(relative,file):
                output[relative]=file
                continue
            skipped.append(relative)
            continue
        if lower in FORBIDDEN_DIRECT_LUA:
            skipped.append(relative)
            continue
        if not _is_allowed_direct_lua(relative):
            skipped.append(relative)
            continue
        output[relative] = file

    # Mod API-specific appends live beside the manifest. They win over any
    # same-path generated entry and are the only supported way this exporter
    # changes an existing game Lua file.
    overlay = compatibility / "Lua"
    if not overlay.is_dir():
        raise FileNotFoundError(f"Mod API append source folder is missing: {overlay}")
    for file in sorted(overlay.rglob("*")):
        if not file.is_file() or not _wanted(file, overlay, LUA_SUFFIXES):
            continue
        relative = _normal_path(file, overlay)
        lower = relative.casefold()
        if not lower.startswith("append/"):
            raise ValueError(f"Compatibility Lua must be an append: {relative}")
        if relative.casefold() in {key.casefold() for key in output}:
            raise ValueError(f"Conflicting generated support and compatibility append: {relative}")
        output[relative] = file

    if not any(path.casefold().startswith("ai/mods/ai-dycom/") for path in output):
        raise ValueError("Dycom's dedicated AI folder is missing from the Mod API export")
    for commander in ("udl0000", "udl0001"):
        required = f"common/units/unitsTemplates/{commander}/{commander}.santp"
        if required not in output:
            raise ValueError(f"Dycom commander template is missing: {required}")
    for caller, source in output.items():
        for imported in re.findall(r"Import\([\"']([^\"']+)[\"']\)", source.read_text(encoding="utf-8-sig")):
            if ((project_lua / imported).is_file() and imported not in output
                    and imported.casefold() not in FORBIDDEN_DIRECT_LUA):
                raise ValueError(f"Project dependency omitted from export: {caller} -> {imported}")
    return output, skipped


def _export_input_inventory(project):
    output = []
    for folder in (*ART_FOLDERS, "Lua", "Mod Manager Compatibility/Lua"):
        root = project / folder
        suffixes = LUA_SUFFIXES if folder.endswith("Lua") else None
        output.extend(path.relative_to(project).as_posix() for path in root.rglob("*")
            if path.is_file() and _wanted(path, root, suffixes))
    return sorted(output)


def _snapshot_export_inputs(project, files, version):
    # Every checkpoint reads fresh bytes. Threads only hash ordinary files;
    # no bpy/scene APIs or cross-export stat-based digest cache is involved.
    def one(file):
        relative = file.relative_to(project).as_posix()
        if relative == "Mod Manager Compatibility/mod.json":
            data = json.loads(file.read_text(encoding="utf-8-sig"))
            data["version"] = version
            digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest().upper()
        else:
            digest = _digest(file)
        return relative, digest
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
        return dict(pool.map(one, sorted(set(files))))


def _validate_zip(target: Path, mod_folder: str, lua_root: str, asset_file: str) -> None:
    prefix = f"{mod_folder}/"
    expected = {
        f"{prefix}mod.json",
        f"{prefix}DycomMod.dll",
        f"{prefix}icons/dycom.png",
        f"{prefix}icons/commanders/udl0000.png",
        f"{prefix}icons/commanders/udl0001.png",
        f"{prefix}packs/{asset_file}",
        f"{prefix}export-receipt.json",
        f"{prefix}tools/Collect-DycomSessionLogs.ps1",
        *INSTALLER_FILES,
    }
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        lowered = [name.casefold() for name in names]
        if len(lowered) != len(set(lowered)):
            raise ValueError("Mod API export contains duplicate paths that differ only by case")
        missing = expected.difference(names)
        if missing:
            raise ValueError("Mod API export is incomplete: " + ", ".join(sorted(missing)))
        if any(not name.startswith(prefix) and name not in INSTALLER_FILES for name in names):
            raise ValueError("The archive may contain only the Dycom folder and its two installer files")
        for name in names:
            lower = name.casefold()
            path = PurePosixPath(name)
            if path.suffix.casefold() in FORBIDDEN_ARCHIVE_SUFFIXES and name not in INSTALLER_FILES and name != f"{prefix}tools/Collect-DycomSessionLogs.ps1":
                raise ValueError(f"Legacy executable/installer leaked into Mod API export: {name}")
            if path.suffix.casefold() == ".dll" and name != f"{prefix}DycomMod.dll":
                raise ValueError(f"Unexpected DLL leaked into Mod API export: {name}")
            if lower.startswith("sanctuarymods/") or lower.startswith("bepinex/"):
                raise ValueError(f"Engine-level path leaked into Mod API export: {name}")
        lua_prefix = f"{prefix}{lua_root.strip('/')}/"
        lua_names = [
            name for name in names
            if name.startswith(lua_prefix) and Path(name).suffix.lower() in LUA_SUFFIXES
        ]
        if not lua_names:
            raise ValueError("Mod API export has no Lua overlay files")
        for name in lua_names:
            relative = name[len(lua_prefix):]
            if relative.casefold() in FORBIDDEN_DIRECT_LUA:
                raise ValueError(f"Forbidden whole-game Lua replacement leaked into export: {relative}")
            if not _is_allowed_direct_lua(relative):
                raise ValueError(f"Unapproved whole-game Lua file leaked into export: {relative}")
        manifest = json.loads(archive.read(f"{prefix}mod.json").decode("utf-8"))
        _validate_manifest(manifest)
        packed = io.BytesIO(archive.read(f"{prefix}packs/{asset_file}"))
        if not zipfile.is_zipfile(packed):
            raise ValueError("The generated Dycom sanpack is not a valid archive")


def export_hosted(
    project: Path,
    target: Path | None = None,
    version: str | None = None,
) -> tuple[Path, dict[str, int]]:
    project = Path(project).resolve()
    compatibility = project / "Mod Manager Compatibility"
    config_path = compatibility / "export.json"
    if not config_path.is_file():
        raise FileNotFoundError(f"Mod API export configuration is missing: {config_path}")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("format") != "mod_api_1.3":
        raise ValueError("Mod Manager Compatibility/export.json must use format 'mod_api_1.3'")

    mod_manifest_path = compatibility / "mod.json"
    if not mod_manifest_path.is_file():
        raise FileNotFoundError(f"Mod API manifest is missing: {mod_manifest_path}")
    mod_manifest = json.loads(mod_manifest_path.read_text(encoding="utf-8"))
    mod_manifest["version"] = normalize_version(version or mod_manifest.get("version", ""))
    _validate_manifest(mod_manifest)

    installer_sources = {
        "installer.bat": compatibility / "installer.bat",
        "Install-Dycom.ps1": compatibility / "Install-Dycom.ps1",
    }
    missing_installers = [name for name, path in installer_sources.items() if not path.is_file()]
    if missing_installers:
        raise FileNotFoundError("Mod API installer source is missing: " + ", ".join(missing_installers))

    mod_folder = _safe_name(config.get("mod_folder", "Dycom"))
    asset_file = _safe_name(config.get("asset_file", "Dycom.sanpack"))
    if not asset_file.lower().endswith(".sanpack"):
        raise ValueError("asset_file must end in .sanpack")
    icon_source = compatibility / "icons" / "dycom.png"
    if not icon_source.is_file() or icon_source.suffix.casefold() != ".png":
        raise FileNotFoundError(f"Dycom lobby PNG is missing: {icon_source}")
    commander_icons = {
        unit: compatibility / "icons" / "commanders" / f"{unit}.png"
        for unit in ("udl0000", "udl0001")
    }
    for unit, icon in commander_icons.items():
        if not icon.is_file() or icon.suffix.casefold() != ".png":
            raise FileNotFoundError(f"Dycom {unit} commander PNG is missing: {icon}")

    input_inventory = _export_input_inventory(project)
    assets: list[Path] = []
    for folder in ART_FOLDERS:
        root = project / folder
        if root.is_dir():
            assets.extend(sorted(path for path in root.rglob("*") if path.is_file() and _wanted(path, root)))
    if not assets:
        raise ValueError("The Mod API package requires Dycom art assets")
    lua_files, skipped = _collect_lua(project, compatibility)
    _validate_unit_assets(project, lua_files.values())
    game = _game_root(config)
    input_files = [*assets, *lua_files.values(), *installer_sources.values(), icon_source,
        *commander_icons.values(), config_path, mod_manifest_path, compatibility / "tools/Collect-DycomSessionLogs.ps1",
        *[compatibility / name for name in HOSTED_PLUGIN_SOURCES]]
    input_snapshot = _snapshot_export_inputs(project, input_files, mod_manifest["version"])

    replace_existing = target is None
    if target is None:
        target = project / "Releases" / f"Dycom-RemmyMM-{mod_manifest['version']}.zip"
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not replace_existing:
        raise FileExistsError(f"Refusing to overwrite an existing release: {target}")

    with tempfile.TemporaryDirectory(prefix="dycom-modapi13-") as temporary_name:
        temporary = Path(temporary_name)
        pack = temporary / asset_file
        plugin = _build_hosted_plugin(game, compatibility, temporary / "DycomMod.dll")
        generated_manifest = temporary / "mod.json"
        generated_manifest.write_text(json.dumps(mod_manifest, indent=2) + "\n", encoding="utf-8")
        from .export_cache import pack_assets
        pack_report = {}
        exporter_version = _digest(Path(__file__)) + _digest(Path(__file__).with_name('__init__.py'))
        pack_assets(project, assets, pack, 'hosted-art', source_hashes=input_snapshot,
                    exporter_version=exporter_version, report=pack_report)

        prefix = mod_folder
        payload: dict[str, Path] = {
            "installer.bat": installer_sources["installer.bat"],
            "Install-Dycom.ps1": installer_sources["Install-Dycom.ps1"],
            f"{prefix}/mod.json": generated_manifest,
            f"{prefix}/DycomMod.dll": plugin,
            f"{prefix}/tools/Collect-DycomSessionLogs.ps1": compatibility / "tools/Collect-DycomSessionLogs.ps1",
            f"{prefix}/icons/dycom.png": icon_source,
            f"{prefix}/icons/commanders/udl0000.png": commander_icons["udl0000"],
            f"{prefix}/icons/commanders/udl0001.png": commander_icons["udl0001"],
            f"{prefix}/packs/{asset_file}": pack,
        }
        lua_prefix = f"{prefix}/{mod_manifest['luaRoot'].strip('/')}"
        for relative, file in sorted(lua_files.items()):
            payload[f"{lua_prefix}/{relative}"] = file

        if (_snapshot_export_inputs(project, input_files, mod_manifest["version"]) != input_snapshot
                or _export_input_inventory(project) != input_inventory):
            raise ValueError("Project changed during export; no release committed. Save and export again.")
        receipt = {
            "format": "DYCOM project-first export 1", "projectFirst": True,
            "project": str(project), "version": mod_manifest["version"],
            "sourceHashes": input_snapshot,
            "inputInventory": input_inventory,
            "note": "mod.json source hash normalizes only the requested release version",
            "payloadHashes": {relative[len(prefix) + 1:]:
                (pack_report['sha256'] if file == pack else
                 input_snapshot[file.relative_to(project).as_posix()] if project in file.parents else _digest(file))
                for relative, file in payload.items() if relative.startswith(prefix + "/")},
        }
        receipt_path = temporary / "export-receipt.json"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
        payload[f"{prefix}/export-receipt.json"] = receipt_path

        built_archive = temporary / "Dycom-RemmyMM.zip"
        try:
            with zipfile.ZipFile(built_archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
                for relative, file in sorted(payload.items()):
                    compress_type = zipfile.ZIP_STORED if file == pack else zipfile.ZIP_DEFLATED
                    from .export_cache import write_member
                    write_member(archive, file, relative, compress_type, 6)
            _validate_zip(built_archive, mod_folder, mod_manifest["luaRoot"], asset_file)
            if (_snapshot_export_inputs(project, input_files, mod_manifest["version"]) != input_snapshot
                or _export_input_inventory(project) != input_inventory):
                raise ValueError("Project changed during export; no release committed. Save and export again.")
            os.replace(built_archive, target)
        except Exception:
            built_archive.unlink(missing_ok=True)
            raise

    return target, {"assets": len(assets), "lua": len(lua_files), "plugins": 1}
