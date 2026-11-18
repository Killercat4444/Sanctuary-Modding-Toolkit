"""Declarative Remmy submod installer export.

Projects provide Remmy/submod.json plus assets, Lua, and installer templates.
Packaging and validation remain owned by the Blender addon.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import io
import json
import re
import zipfile


FORMAT = "Sanctuary Remmy Submod Export"
LUA_SUFFIXES = {".lua", ".santp"}


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest().upper()


def _project_path(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"Invalid project-relative path: {relative!r}")
    path = (root / relative).resolve()
    if root.resolve() != path and root.resolve() not in path.parents:
        raise ValueError(f"Project-relative path escapes its project: {relative}")
    return path


def _core_project_path(project: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise ValueError(f"Invalid core-project path: {relative!r}")
    path = (project / relative).resolve()
    if path.parent != project.parent.resolve() or path == project:
        raise ValueError("A Remmy submod's core project must be a sibling project folder")
    if not (path / "smt_project.json").is_file():
        raise FileNotFoundError(f"Core SMT project is missing: {path}")
    return path


def _installed_path(value: str) -> str:
    if (not isinstance(value, str) or not value or "\\" in value or
            value.startswith("/") or ".." in value.split("/") or
            not re.fullmatch(r"[A-Za-z0-9._ /-]+", value)):
        raise ValueError(f"Unsafe installed path: {value!r}")
    return value


def export_submod(project: Path, target: Path | None = None) -> tuple[Path, dict[str, int]]:
    project = Path(project).resolve()
    config_path = project / "Remmy" / "submod.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if config.get("format") != FORMAT or config.get("schema") != 1:
        raise ValueError("Remmy/submod.json is not a supported addon submod configuration")

    core = _core_project_path(project, config["core_project"])
    game_config = json.loads(_project_path(core, config["game_config"]).read_text(encoding="utf-8"))
    game_hash = str(game_config["trebuchet_sha256"]).upper()
    if not re.fullmatch(r"[0-9A-F]{64}", game_hash):
        raise ValueError("Core project has an invalid Trebuchet hash")

    forbidden = str(config.get("forbidden_unit", "")).lower()
    if forbidden:
        if ((core / "Units" / forbidden).exists() or
                (core / "Lua/common/units/unitsTemplates" / forbidden).exists() or
                any((core / "UI").rglob(f"{forbidden}.*"))):
            raise RuntimeError(f"Move {forbidden} out of the core project before exporting the submod")

    for filename, added_line in config.get("registration_lines", {}).items():
        core_file = _project_path(core, f"Lua/common/units/{filename}")
        overlay_file = _project_path(project, f"Lua/common/units/{filename}")
        base = core_file.read_text(encoding="utf-8")
        extended = overlay_file.read_text(encoding="utf-8")
        if added_line not in extended or extended.replace(added_line, "", 1) != base:
            raise RuntimeError(f"Registration overlay is not based on the current core roster: {filename}")

    required_core = []
    for record in config.get("required_core_files", []):
        source = _project_path(core, record["source"])
        if not source.is_file():
            raise FileNotFoundError(source)
        required_core.append({
            "path": _installed_path(record["installed"]),
            "sha256": _digest_bytes(source.read_bytes()),
        })

    assets: list[Path] = []
    for folder in config["asset_folders"]:
        root = _project_path(project, folder)
        assets.extend(path for path in sorted(root.rglob("*")) if path.is_file())
    if not assets:
        raise RuntimeError("Submod assets are missing")
    pack = io.BytesIO()
    with zipfile.ZipFile(pack, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in assets:
            archive.write(path, path.relative_to(project).as_posix())

    payload: dict[str, bytes] = {
        _installed_path(config["asset_pack_destination"]): pack.getvalue(),
    }
    for record in config.get("static_payload", []):
        source = _project_path(project, record["source"])
        if not source.is_file():
            raise FileNotFoundError(source)
        payload[_installed_path(record["installed"])] = source.read_bytes()

    lua_root = _project_path(project, config["lua_source"])
    lua_destination = _installed_path(config["lua_destination"]).rstrip("/")
    lua_count = 0
    for path in sorted(lua_root.rglob("*")):
        if path.is_file() and path.suffix.lower() in LUA_SUFFIXES:
            installed = _installed_path(lua_destination + "/" + path.relative_to(lua_root).as_posix())
            payload[installed] = path.read_bytes()
            lua_count += 1
    if not lua_count:
        raise RuntimeError("Submod Lua is missing")

    for name in payload:
        if name.lower().endswith(("/modmanager.dll", "/modloader.dll")):
            raise ValueError("A submod package must not replace Remmy Mod Manager or Mod Loader")

    installer_ps1 = _project_path(project, config["installer_ps1"])
    installer_cmd = _project_path(project, config["installer_cmd"])
    for installer in (installer_ps1, installer_cmd):
        if not installer.is_file():
            raise FileNotFoundError(installer)

    if target is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = project / "Releases" / f"{config['archive_prefix']}-{stamp}.zip"
    target = Path(target).resolve()
    releases = (project / "Releases").resolve()
    releases.mkdir(parents=True, exist_ok=True)
    if releases not in target.parents:
        raise ValueError("Submod installer must be exported inside this project's Releases folder")
    if target.exists():
        raise FileExistsError(target)

    manifest = {
        "name": config["package_name"],
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "trebuchet_sha256": game_hash,
        "dycom_files": required_core,
        "files": [
            {"path": name, "sha256": _digest_bytes(data), "bytes": len(data)}
            for name, data in sorted(payload.items())
        ],
    }
    readme = "\n".join(str(line) for line in config.get("readme", [])) + "\n"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        archive.write(installer_ps1, installer_ps1.name)
        archive.write(installer_cmd, installer_cmd.name)
        archive.writestr("README.txt", readme)
        for name, data in sorted(payload.items()):
            archive.writestr("payload/engine/" + name, data)
    with zipfile.ZipFile(target) as archive:
        bad = archive.testzip()
        if bad is not None:
            raise RuntimeError(f"Submod ZIP verification failed at {bad}")
    return target, {"assets": len(assets), "lua": lua_count, "payload": len(payload)}
