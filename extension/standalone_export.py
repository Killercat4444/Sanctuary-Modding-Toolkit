"""Build the dependency-free, raw-file Dycom distribution."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import io
import hashlib
import json
import os
import tempfile
import zipfile

from .hosted_export import _compile, _digest, _find_compiler, _game_root, _wanted, _release_lua, _validate_unit_assets, normalize_version
from .remmy_export import ASSET_FOLDERS, LUA_SUFFIXES
from .hosted_export import _snapshot_export_inputs, _export_input_inventory


def _build_patcher(game: Path, output: Path, sources: list[Path], runtime: Path | None = None) -> Path:
    compiler = _find_compiler()
    if compiler is None:
        raise RuntimeError("A Unity Roslyn compiler was not found; standalone export refuses stale binaries")
    mono, csc = compiler
    core = (runtime / "BepInEx/core") if runtime else game / "BepInEx/core"
    _compile(mono, csc, output, sources, [
        game / "Sanctuary_Data" / "Managed" / "netstandard.dll",
        game / "Sanctuary_Data" / "Managed" / "System.IO.Compression.dll",
        core / "Mono.Cecil.dll",
    ])
    return output


def _build_runtime_plugin(game: Path, output: Path, source: Path, runtime: Path | None = None) -> Path:
    compiler = _find_compiler()
    if compiler is None:
        raise RuntimeError("A Unity Roslyn compiler was not found; standalone export refuses stale binaries")
    mono, csc = compiler
    core = (runtime / "BepInEx/core") if runtime else game / "BepInEx/core"
    _compile(mono, csc, output, [source], [
        game / "Sanctuary_Data" / "Managed" / "netstandard.dll",
        game / "Sanctuary_Data" / "Managed" / "UnityEngine.dll",
        game / "Sanctuary_Data" / "Managed" / "UnityEngine.CoreModule.dll",
        game / "Sanctuary_Data" / "Managed" / "Trebuchet.dll",
        game / "Sanctuary_Data" / "Managed" / "Unity.Collections.dll",
        core / "BepInEx.dll",
        core / "0Harmony.dll",
    ])
    return output


def _pack_folder(project: Path, folder: str, output: Path, source_hashes=None) -> int:
    root = project / folder
    files = sorted(path for path in root.rglob("*") if path.is_file() and _wanted(path, root)) if root.is_dir() else []
    if not files:
        return 0
    if source_hashes is not None and any(path.relative_to(project).as_posix() not in source_hashes for path in files):
        raise ValueError("Project changed during standalone export; no release committed. Save and export again.")
    from .export_cache import pack_assets
    pack_assets(project,files,output,'standalone-'+folder, source_hashes=source_hashes, exporter_version=_digest(Path(__file__)))
    return len(files)


def _merge_lua_append(base: bytes, patch: bytes) -> bytes:
    """Insert an append before a module's final top-level return, if present."""
    base = base.rstrip(b"\r\n")
    patch = patch.lstrip(b"\r\n").rstrip(b"\r\n")
    lines = base.splitlines(keepends=True)
    insertion = len(lines)
    for index in range(len(lines) - 1, -1, -1):
        line = lines[index]
        if line.startswith(b"return ") or line.rstrip(b"\r\n") == b"return":
            insertion = index
            break
    if insertion == len(lines):
        return base + b"\n" + patch + b"\n"
    return b"".join(lines[:insertion]) + patch + b"\n\n" + b"".join(lines[insertion:])


def _build_lua_cache(lua_root: Path, files: list[Path], output: Path, game: Path) -> int:
    """Build the standalone cache and resolve Mod-API-style append hooks.

    The Mod Manager concatenates ``Lua/append/<target>`` onto the stock target
    at load time. Standalone has no Mod API, so perform the same deterministic
    merge while exporting instead of caching an inert ``append`` directory.
    """
    payload: dict[str, bytes] = {}
    appends: dict[str, list[bytes]] = {}

    for file in files:
        relative = file.relative_to(lua_root).as_posix()
        if relative.casefold().startswith("append/"):
            target = relative[len("append/"):]
            appends.setdefault(target, []).append(file.read_bytes())
        else:
            payload[relative] = file.read_bytes()

    stock_lua = game / "LJ" / "lua"
    for target, patches in sorted(appends.items()):
        base = payload.get(target)
        if base is None:
            stock = stock_lua / Path(target)
            if not stock.is_file():
                raise FileNotFoundError(f"Standalone append target is missing from the game: {stock}")
            base = stock.read_bytes()
        merged = base
        for patch in patches:
            merged = _merge_lua_append(merged, patch)
        payload[target] = merged

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative, contents in sorted(payload.items()):
            archive.writestr(f"files/LJ/lua/{relative}", contents)
    return len(payload)


def _validate(target: Path) -> None:
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        lowered = [name.casefold() for name in names]
        if len(lowered) != len(set(lowered)):
            raise ValueError("Standalone export contains duplicate paths that differ only by case")
        required = {
            "engine/winhttp.dll",
            "engine/doorstop_config.ini",
            "engine/BepInEx/core/BepInEx.dll",
            "engine/BepInEx/core/BepInEx.Preloader.dll",
            "engine/BepInEx/core/Mono.Cecil.dll",
            "engine/BepInEx/patchers/DycomStandalone/DycomStandalonePatcher.dll",
            "engine/BepInEx/plugins/DycomStandalone/DycomStandaloneLogger.dll",
            "engine/DycomStandalone/cache/DycomLuaCache.zip",
            "Mods/Units.sanpack",
            "Mods/UI.sanpack",
            "Mods/Lua/common/systems/factions.lua",
        }
        missing = required.difference(names)
        if missing:
            raise ValueError("Standalone export is incomplete: " + ", ".join(sorted(missing)))
        forbidden = ("sanctuarymods/", "modmanager.dll", "modloader.dll", "mod.json", "dycommod.dll")
        for name in lowered:
            if any(token in name for token in forbidden):
                raise ValueError(f"Mod Manager dependency leaked into standalone export: {name}")
            if name.endswith((".cmd", ".bat", ".ps1")):
                raise ValueError(f"Standalone export must contain raw files only, not an installer: {name}")
        ai_prefix = "engine/LJ/lua/AI/mods/AI-Dycom/"
        if not any(name.startswith(ai_prefix) and Path(name).suffix.lower() in LUA_SUFFIXES for name in names):
            raise ValueError("Standalone export is missing the physical Dycom AI directory")
        for pack_name in ("Mods/Units.sanpack", "Mods/UI.sanpack"):
            if not zipfile.is_zipfile(io.BytesIO(archive.read(pack_name))):
                raise ValueError(f"Standalone pack is invalid: {pack_name}")
        lua_cache = zipfile.ZipFile(io.BytesIO(archive.read("engine/DycomStandalone/cache/DycomLuaCache.zip")))
        cached = set(lua_cache.namelist())
        required_lua = {
            "files/LJ/lua/common/gameUtils.lua",
            "files/LJ/lua/common/systems/factions.lua",
        }
        missing_lua = required_lua.difference(cached)
        if missing_lua:
            raise ValueError("Standalone Lua cache is incomplete: " + ", ".join(sorted(missing_lua)))


def export_standalone(
    project: Path,
    target: Path | None = None,
    version: str | None = None,
    engine_root: Path | None = None,
) -> tuple[Path, dict[str, int]]:
    project = Path(project).resolve()
    project_manifest = json.loads((project / "smt_project.json").read_text(encoding="utf-8"))
    release_version = normalize_version(version or project_manifest.get("version", "0.1"))
    source_folder = project / "Standalone"
    config_path = source_folder / "export.json"
    if not config_path.is_file():
        config_path = project / "Remmy/remmy.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    runtime = source_folder / config.get("runtime", "Runtime")
    if not runtime.is_dir():
        raise FileNotFoundError(f"Standalone runtime folder is missing: {runtime}")
    game = Path(engine_root).expanduser().resolve() if engine_root else _game_root(config)
    if not (game / "Sanctuary.exe").is_file() or not (game / "LJ/lua").is_dir():
        raise ValueError("Standalone export needs the selected Sanctuary engine folder: " + str(game))

    lua_root = project / "Lua"
    all_lua = sorted(path for path in lua_root.rglob("*") if path.is_file() and _release_lua(path, lua_root))
    _validate_unit_assets(project, all_lua)
    ai_root = lua_root / "AI" / "mods" / "AI-Dycom"
    ai_files = sorted(path for path in ai_root.rglob("*") if path.is_file() and _wanted(path, ai_root, LUA_SUFFIXES))
    if not all_lua or not ai_files:
        raise ValueError("Standalone Dycom requires project Lua and Lua/AI/mods/AI-Dycom")
    faction = lua_root / "common" / "systems" / "factions.lua"
    faction_text = faction.read_text(encoding="utf-8")
    if "FactionsData[4]" not in faction_text or "FactionsData[5]" not in faction_text:
        raise ValueError("Standalone factions.lua must register Dycom in slots 4 and 5")

    source_files = [project / relative for relative in _export_input_inventory(project)]
    source_files += [path for path in runtime.rglob('*') if path.is_file() and _wanted(path, runtime)]
    source_files += [config_path, *[source_folder / config.get(key, default) for key, default in
        (("patcher_source", "DycomStandalonePatcher.cs"), ("lua_cache_source", "DycomStandaloneLuaCachePatcher.cs"),
         ("runtime_plugin_source", "DycomStandaloneLogger.cs"))]]
    def inventory():
        return _export_input_inventory(project) + sorted(path.relative_to(project).as_posix()
            for path in runtime.rglob('*') if path.is_file() and _wanted(path, runtime))
    input_inventory = inventory()
    input_snapshot = _snapshot_export_inputs(project, source_files, release_version)
    def manifest_digest():
        current = json.loads((project / "smt_project.json").read_text(encoding="utf-8-sig"))
        current.pop("updated_utc", None); current["version"] = release_version
        return hashlib.sha256(json.dumps(current, sort_keys=True).encode()).hexdigest().upper()
    manifest_snapshot = manifest_digest()
    def unchanged():
        if (_snapshot_export_inputs(project, source_files, release_version) != input_snapshot
                or inventory() != input_inventory or manifest_digest() != manifest_snapshot):
            raise ValueError("Project changed during standalone export; no release committed. Save and export again.")

    replace_existing = target is None
    if target is None:
        target = project / "Releases" / f"Dycom-Standalone-{release_version}.zip"
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not replace_existing:
        raise FileExistsError(f"Refusing to overwrite an existing release: {target}")

    with tempfile.TemporaryDirectory(prefix="dycom-standalone-") as temporary_name:
        temporary = Path(temporary_name)
        patcher_source = source_folder / config.get("patcher_source", "DycomStandalonePatcher.cs")
        lua_cache_source = source_folder / config.get("lua_cache_source", "DycomStandaloneLuaCachePatcher.cs")
        patcher = _build_patcher(game, temporary / "DycomStandalonePatcher.dll", [patcher_source, lua_cache_source], runtime)
        plugin_source = source_folder / config.get("runtime_plugin_source", "DycomStandaloneLogger.cs")
        plugin = _build_runtime_plugin(game, temporary / "DycomStandaloneLogger.dll", plugin_source, runtime)
        lua_cache = temporary / "DycomLuaCache.zip"
        lua_cache_count = _build_lua_cache(lua_root, all_lua, lua_cache, game)
        packs = {}
        pack_counts = {}
        for folder in ASSET_FOLDERS:
            pack = temporary / f"{folder}.sanpack"
            count = _pack_folder(project, folder, pack, input_snapshot)
            if count:
                packs[f"Mods/{folder}.sanpack"] = pack
                pack_counts[folder.lower()] = count

        payload: dict[str, Path] = {}
        for file in sorted(path for path in runtime.rglob("*") if path.is_file() and _wanted(path, runtime)):
            relative = file.relative_to(runtime).as_posix()
            if relative.casefold() in ("bepinex/plugins/modloader.dll", "sanctuarymods/modmanager/modmanager.dll"):
                continue
            payload[f"engine/{relative}"] = file
        payload["engine/BepInEx/patchers/DycomStandalone/DycomStandalonePatcher.dll"] = patcher
        payload["engine/BepInEx/plugins/DycomStandalone/DycomStandaloneLogger.dll"] = plugin
        payload["engine/DycomStandalone/cache/DycomLuaCache.zip"] = lua_cache
        payload.update(packs)
        for file in all_lua:
            if file.relative_to(lua_root).parts[0].casefold() == "append":
                continue
            if ai_root in file.parents:
                continue
            payload[f"Mods/Lua/{file.relative_to(lua_root).as_posix()}"] = file
        for file in ai_files:
            payload[f"engine/LJ/lua/AI/mods/AI-Dycom/{file.relative_to(ai_root).as_posix()}"] = file

        unchanged()
        report = {
            "format": "Dycom Standalone raw files",
            "gameRoot": str(game),
            "sourceHashes": input_snapshot,
            "inputInventory": input_inventory,
            "projectManifestNormalizedSha256": manifest_snapshot,
            "sourceHashNote": "Only requested project version and authoring updated_utc are normalized in manifest digest",

            "compileReferences": {str(path.relative_to(game)): _digest(path) for path in
                (game / "Sanctuary_Data/Managed/Trebuchet.dll", game / "Sanctuary_Data/Managed/Unity.Collections.dll")},
            "runtimeReferences": {str(path.relative_to(runtime)): _digest(path) for path in
                (runtime / "BepInEx/core/BepInEx.dll", runtime / "BepInEx/core/0Harmony.dll", runtime / "BepInEx/core/Mono.Cecil.dll")},
            "project": project_manifest.get("name", project.name),
            "createdUtc": datetime.now(timezone.utc).isoformat(),
            "counts": {"lua": len(all_lua), "luaCache": lua_cache_count, "aiPhysical": len(ai_files), "packs": len(packs), "plugins": 1, **pack_counts},
            "sha256": {relative: _digest(file) for relative, file in sorted(payload.items())},
        }
        readme = (
            "Dycom Standalone (no Mod Manager dependency)\n\n"
            "This archive contains raw files and deliberately has no installer. Close Sanctuary first.\n"
            "1. Copy the CONTENTS of the engine folder into the game's engine folder containing Sanctuary.exe.\n"
            "2. Copy the CONTENTS of the Mods folder into Documents\\My Games\\Enhearten Media\\Sanctuary\\Mods.\n"
            "3. Start Sanctuary normally. Dycom is always enabled in this standalone build.\n\n"
            "Session logs are written to engine\\Dycom Logs. For AI problems, send the newest AI_Logs_ file as well as the normal session log. The newest ten files of each type are retained independently.\n\n"
            "Do not combine this package with Remmy Mod Manager, the Mod Manager Dycom build, or SCOM.\n"
            "The standalone preloader owns faction registration; the physical engine AI directory allows the stock AI scanner to discover AI-Dycom.\n"
            "Dycom Lua is also injected from engine\\DycomStandalone\\cache so it does not depend on loose-Lua discovery.\n"
        )
        built_archive = temporary / "Dycom-Standalone.zip"
        with zipfile.ZipFile(built_archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for relative, file in sorted(payload.items()):
                archive.write(file, relative,compress_type=zipfile.ZIP_STORED if file.suffix.lower() in ('.sanpack','.zip') else zipfile.ZIP_DEFLATED)
            archive.writestr("README.txt", readme)
            archive.writestr("standalone-export.json", json.dumps(report, indent=2) + "\n")
        _validate(built_archive)
        unchanged()
        os.replace(built_archive, target)
    return target, {"lua": len(all_lua), "lua_cache": lua_cache_count, "ai": len(ai_files), "packs": len(packs), "plugins": 1}
