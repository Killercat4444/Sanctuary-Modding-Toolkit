"""Build a Remmy/Mod Manager distribution without changing the SMT project.

The manager overlays Lua itself. A project-supplied preloader may additionally
own a namespaced .sanpack and faction UI; ordinary project deployment remains
separate and unchanged.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import tempfile
import zipfile


ASSET_FOLDERS = ("Units", "UI", "Environment", "VFX", "Audio", "Projectiles")
LUA_SUFFIXES = {".lua", ".santp"}


def _safe_name(value: str) -> str:
    value = str(value).strip()
    if not value or value in (".", "..") or "/" in value or "\\" in value:
        raise ValueError("Remmy mod folder names must be simple names")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _faction_variants(source: str) -> dict[str, str]:
    required = ("FactionsData[5]", "FactionsData[6]", "return 5, FactionsData[5]")
    if not all(token in source for token in required):
        raise ValueError("Dycom faction Lua is not the expected fifth-slot source; refusing an unsafe rewrite")
    standalone = source.replace("FactionsData[5]", "FactionsData[4]")
    standalone = standalone.replace("FactionsData[6]", "FactionsData[5]")
    standalone = standalone.replace("return 5, FactionsData[4]", "return 4, FactionsData[4]")
    # The final replace is intentionally exact; a changed source should fail validation.
    if "return 4, FactionsData[4].name" not in standalone:
        raise ValueError("Standalone faction identity could not be validated")
    return {"scom": source, "standalone": standalone}


def export_remmy(project: Path, target: Path | None = None) -> tuple[Path, dict[str, int]]:
    project = Path(project).resolve()
    manifest = json.loads((project / "smt_project.json").read_text(encoding="utf-8"))
    config_path = project / "Remmy" / "remmy.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    core = _safe_name(config.get("core_folder", manifest["name"]))
    asset_file = _safe_name(config.get("asset_file", f"{core}.sanpack"))
    if not asset_file.lower().endswith(".sanpack"):
        raise ValueError("Remmy asset_file must end in .sanpack")
    compatibility = config.get("compatibility_folder")
    if compatibility:
        compatibility = _safe_name(compatibility)
        if compatibility == core:
            raise ValueError("Core and compatibility submod folders must differ")
    preloader = project / config["preloader"] if config.get("preloader") else None
    if preloader and not preloader.is_file():
        raise FileNotFoundError(f"Remmy preloader is missing: {preloader}")

    assets = []
    for folder in ASSET_FOLDERS:
        root = project / folder
        if root.is_dir():
            assets.extend(sorted(p for p in root.rglob("*") if p.is_file()))
    if assets and not preloader:
        raise ValueError("Projects with asset packs need a Remmy preloader to install/toggle those packs")
    lua_root = project / "Lua"
    lua_files = sorted(p for p in lua_root.rglob("*") if p.is_file() and p.suffix.lower() in LUA_SUFFIXES)
    if not lua_files:
        raise ValueError("Remmy package needs at least one Lua/template overlay file")

    faction_rel = config.get("faction_lua")
    variants = None
    disabled = None
    if faction_rel:
        faction_file = project / faction_rel
        variants = _faction_variants(faction_file.read_text(encoding="utf-8"))
        disabled_file = project / config["disabled_faction_lua"]
        disabled = disabled_file.read_text(encoding="utf-8")
    if compatibility and not variants:
        raise ValueError("A compatibility submod requires faction Lua variants")

    if target is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        target = project / "Releases" / f"{manifest['name']}-Remmy-{stamp}.zip"
    target = Path(target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise FileExistsError(f"Refusing to overwrite an existing release: {target}")
    counts = {"assets": len(assets), "lua": len(lua_files), "compatibility": int(bool(compatibility))}
    with tempfile.TemporaryDirectory(prefix="smt-remmy-") as temporary:
        pack = Path(temporary) / asset_file
        if assets:
            from .export_cache import pack_assets
            pack_assets(project,assets,pack,'legacy-art')
        with zipfile.ZipFile(target, "w", zipfile.ZIP_STORED, allowZip64=True) as archive:
            prefix = f"SanctuaryMods/{core}"
            for file in lua_files:
                relative = file.relative_to(lua_root).as_posix()
                if variants and relative == Path(faction_rel).relative_to("Lua").as_posix():
                    archive.writestr(f"{prefix}/{relative}", variants["standalone"])
                else:
                    archive.write(file, f"{prefix}/{relative}")
            if assets:
                archive.write(pack, f"{prefix}/Assets/{asset_file}")
            if variants:
                for name, content in variants.items():
                    archive.writestr(f"{prefix}/Variants/factions_{name}.txt", content)
                archive.writestr(f"{prefix}/Variants/factions_disabled.txt", disabled)
            if compatibility:
                archive.writestr(
                    f"SanctuaryMods/{compatibility}/common/dycom_scom_compat.lua",
                    "-- Optional Dycom compatibility marker. The preloader reads Remmy's toggle.\nreturn true\n",
                )
            if preloader:
                archive.write(preloader, f"BepInEx/patchers/DycomFaction/{preloader.name}")
            instructions = (
                f"{manifest['name']} for Remmy's Sanctuary Mod Manager\n"
                "Extract this ZIP into the game's engine folder (the folder containing Sanctuary.exe).\n"
                f"On the Mods page, enable the Lua mod named '{core}', then restart the game.\n"
                + (f"If SCOM is installed, also enable '{compatibility}' and restart.\n" if compatibility else "")
                + "Disable the mod on the Mods page and restart to remove the manager-owned asset pack.\n"
                "The normal SMT project export/deploy workflow is unchanged.\n"
                "Do not install an older Dycom preloader alongside the one in this ZIP.\n"
            )
            archive.writestr("README.txt", instructions)
            archive.writestr("remmy-package.json", json.dumps({
                "project": manifest["name"], "core_folder": core,
                "compatibility_folder": compatibility, "asset_count": len(assets),
                "lua_count": len(lua_files), "asset_sha256": _sha256(pack) if assets else None,
            }, indent=2))
    return target, counts
