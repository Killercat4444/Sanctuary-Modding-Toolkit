"""Editable mod projects, shareable .SMT containers, Lua scaffolding and deployment."""
from pathlib import Path
from datetime import datetime, timezone
import json
import re
import shutil
import subprocess
import tempfile
import zipfile

import bpy
from bpy.types import Operator
from bpy.props import StringProperty

from .utils import console, get_addon_preferences, safe_unit_id
from .hosted_export import export_hosted, normalize_version
from .standalone_export import export_standalone

MANIFEST = "smt_project.json"
SCHEMA = 1


def _manifest_path(project):
    project = Path(project)
    canonical = project / MANIFEST
    if canonical.is_file():
        return canonical
    legacy = sorted(project.glob("smt_*.json"))
    valid = []
    for candidate in legacy:
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if data.get("format") == "Sanctuary Mod Toolkit Project":
                valid.append(candidate)
        except Exception:
            pass
    return valid[0] if len(valid) == 1 else canonical


def _clean_name(value):
    value = re.sub(r"[^A-Za-z0-9 _.-]+", "", str(value)).strip(" .")
    if not value:
        raise ValueError("Enter a project name")
    return value[:80]


def _path(value):
    return Path(bpy.path.abspath(value)).expanduser().resolve()


def library_root(context):
    prefs = get_addon_preferences(context)
    if not prefs or not prefs.mod_library_root:
        raise ValueError("Configure a Mod Project Library in addon preferences")
    return _path(prefs.mod_library_root)


def active_project(context, required=True):
    from .shared_project import check
    try:
        check()
    except ValueError:
        if required:
            raise
        return None
    settings = context.scene.sanctuary_toolkit
    raw = settings.mod_active_path
    path = _path(raw) if raw else None
    # Heal scenes saved before a project folder was flattened one level. This
    # also makes project relocation friendlier without guessing unrelated dirs.
    if path is not None and not _manifest_path(path).is_file():
        parent = path.parent
        if _manifest_path(parent).is_file():
            path = parent
            settings.mod_active_path = str(parent)
    if required and (path is None or not _manifest_path(path).is_file()):
        raise ValueError("Choose or create an active SMT mod project")
    return path


def read_manifest(project):
    data = json.loads(_manifest_path(project).read_text(encoding="utf-8"))
    if data.get("format") != "Sanctuary Mod Toolkit Project":
        raise ValueError("Not a valid SMT project")
    return data


def write_manifest(project, data):
    data["schema"] = SCHEMA
    data["format"] = "Sanctuary Mod Toolkit Project"
    data["updated_utc"] = datetime.now(timezone.utc).isoformat()
    (Path(project) / MANIFEST).write_text(json.dumps(data, indent=2), encoding="utf-8")


def read_export_version(project):
    """Read the project's release version without coupling it to a .blend file."""
    project = Path(project)
    mod_manifest = project / "Mod Manager Compatibility" / "mod.json"
    if mod_manifest.is_file():
        try:
            value = json.loads(mod_manifest.read_text(encoding="utf-8")).get("version", "")
            if value:
                return normalize_version(value)
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    try:
        value = read_manifest(project).get("version", "")
        if value:
            return normalize_version(value)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return "0.1"


def write_export_version(project, version):
    """Persist the last successful release version for later Blender sessions."""
    project = Path(project)
    version = normalize_version(version)
    mod_manifest = project / "Mod Manager Compatibility" / "mod.json"
    if mod_manifest.is_file():
        text = mod_manifest.read_text(encoding="utf-8")
        updated, count = re.subn(
            r'("version"\s*:\s*")[^"]*(")',
            lambda match: match.group(1) + version + match.group(2),
            text,
            count=1,
        )
        if count != 1:
            raise ValueError("Mod Manager mod.json has no writable version field")
        mod_manifest.write_text(updated, encoding="utf-8")

    manifest = read_manifest(project)
    manifest["version"] = version
    write_manifest(project, manifest)


def create_project(context):
    s = context.scene.sanctuary_toolkit
    name = _clean_name(s.mod_project_name)
    root = library_root(context); root.mkdir(parents=True, exist_ok=True)
    project = root / name
    if project.exists() and any(project.iterdir()):
        raise FileExistsError(f"Project already exists: {project}")
    for relative in ("Units", "Lua", "UI/Sprites/Icons/Units"):
        (project / relative).mkdir(parents=True, exist_ok=True)
    write_manifest(project, {
        "name": name, "tagged_faction": bool(s.mod_tagged),
        "faction_name": s.mod_faction_name.strip(),
        "faction_tag": re.sub(r"[^A-Z0-9_]", "", s.mod_faction_tag.upper()),
        "units": [], "files": {"units": "Units", "lua": "Lua", "ui": "UI"},
    })
    s.mod_active_path = str(project)
    return project


def _unit_id(settings):
    if settings.lua_use_project_id:
        uid = settings.unit_id.strip().lower()
        if not re.fullmatch(r"u[a-z]{2}\d{4}", uid):
            raise ValueError(
                "Project Unit ID must be U + faction letter + family letter + "
                "tier digit + three digits"
            )
        return uid
    faction = re.sub(r"[^A-Za-z]", "", settings.lua_faction_prefix).lower()[:1]
    digits = settings.lua_sequence
    if not faction or not re.fullmatch(r"\d{3}", digits):
        raise ValueError("Faction Prefix must be one letter and Three Digits must contain exactly three digits")
    return "u" + faction + settings.lua_domain.lower() + settings.lua_tier + digits


def _replace_assignment(text, field, value, quoted=True):
    rendered = json.dumps(str(value)) if quoted else str(value)
    pattern = rf"(\b{re.escape(field)}\s*=\s*)(?:[\"'][^\"']*[\"']|[-+]?\d+(?:\.\d+)?)"
    result, count = re.subn(pattern, lambda m: m.group(1) + rendered, text, count=1)
    return result, count


def _csv(value):
    return [v.strip() for v in str(value).split(",") if v.strip()]


def _refresh_browser(context):
    try:
        from .game_browser import build_database
        from .utils import resolve_engine_root
        prefs=get_addon_preferences(context);engine=resolve_engine_root(prefs.sanctuary_root if prefs else "")
        if engine:build_database(engine)
    except Exception as exc:console(f"Mod saved, but browser refresh failed: {exc}")


def generate_lua(context):
    s = context.scene.sanctuary_toolkit
    project = active_project(context)
    manifest = read_manifest(project)
    uid = _unit_id(s)
    if s.lua_use_project_id:
        if uid[3] not in "1234" or uid[2].upper() not in "LSAN":
            raise ValueError("The creation form supports T1Ã¢â‚¬â€œT4 L/S/A/N IDs. Existing commander Lua can be edited from the browser.")
        s.lua_tier = uid[3]
        s.lua_domain = uid[2].upper()
    destination = project / "Lua/common/units/unitsTemplates" / uid / f"{uid}.santp"
    if destination.exists():
        raise FileExistsError("This unit already has Lua. Use Edit Lua in the unit browser to preserve its custom code.")
    donor = safe_unit_id(s.lua_donor_unit)
    if not donor:
        raise ValueError("Template Unit ID must look like ucl1501")
    from .game_browser import load_database,get_database_payload
    records = load_database()
    record = next((r for r in records if r.get("unit_id") == donor and r.get("source_kind", "BASE") == "BASE"), None)
    source = Path(record.get("template_path", "")) if record else None
    if not source or not source.is_file():
        raise FileNotFoundError(f"No readable base template for {donor}; rebuild the unit index")
    text = source.read_text(encoding="utf-8", errors="replace")
    text = re.sub(re.escape(donor), uid, text, flags=re.IGNORECASE)
    from .lua_document import Document, string_table
    from .lua_editor import FIELDS, weapon_paths
    doc = Document(text)
    changes = {path:json.dumps(getattr(s,prop)) for prop,path in FIELDS.items() if doc.span(path)}
    changes[('general','tpId')] = json.dumps(uid)
    if doc.span(('defence','health','value')):
        changes[('defence','health','value')] = str(s.lua_health)
    faction_tags = _csv(s.lua_faction_tag) or [manifest.get("faction_tag", "") or s.mod_faction_tag.upper()]
    domain_tag={"L":"LAND","S":"STRUCTURE","A":"AIR","N":"NAVY"}[s.lua_domain]
    tags = faction_tags + [f"TECH{s.lua_tier}",domain_tag] + _csv(s.lua_buildable_by) + _csv(s.lua_extra_tags)
    tags = list(dict.fromkeys(re.sub(r"[^A-Z0-9_]", "", t.upper()) for t in tags if t))
    changes[('tags',)] = string_table(tags)
    targets = _csv(s.lua_target_layers)
    if targets:
        for path in weapon_paths(doc): changes[path] = string_table(targets)
    text = doc.replace(changes)
    from .studio_core import ensure_builder_skirt
    text = ensure_builder_skirt(text)
    destination = project / "Lua/common/units/unitsTemplates" / uid / f"{uid}.santp"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8", newline="\n")
    # A new ID also needs a unit-local material; otherwise exported textures
    # may be named correctly but never referenced by the game.
    material_member=record.get("material_member") if record else None
    payload=get_database_payload() or {}
    pack=Path(payload.get("units_pack",""))
    if material_member and pack.is_file():
        with zipfile.ZipFile(pack) as z:material_text=z.read(material_member).decode("utf-8-sig","replace")
        material_text=re.sub(re.escape(donor),uid,material_text,flags=re.IGNORECASE)
        material_dest=project/"Units"/uid/"LOD0"/f"{uid}_lod0.sanmaterial"
        material_dest.parent.mkdir(parents=True,exist_ok=True)
        material_dest.write_text(material_text,encoding="utf-8",newline="\n")
    units = manifest.setdefault("units", [])
    item = next((u for u in units if u.get("unit_id") == uid), None)
    entry = {"unit_id": uid, "display_name": s.lua_display_name, "tier": int(s.lua_tier),
             "domain": s.lua_domain, "donor": donor, "template": destination.relative_to(project).as_posix(),
             "authoring": {"faction_prefix":s.lua_faction_prefix,"serial":s.lua_tier+s.lua_sequence,
                "unit_type":s.lua_unit_type,"buildable_by":_csv(s.lua_buildable_by),
                "extra_tags":_csv(s.lua_extra_tags),"target_layers":_csv(s.lua_target_layers),
                "health":s.lua_health,"alloy_cost":s.lua_alloy_cost,"energy_cost":s.lua_energy_cost,
                "build_time":s.lua_build_time}}
    if item: item.update(entry)
    else: units.append(entry)
    write_manifest(project, manifest)
    s.unit_id = uid
    return destination


def export_active_unit(context):
    from .project_blueprints import prepare_active_unit
    template_path = prepare_active_unit(context)
    from .animation_assignment import sync_saved
    sync_saved(context, template_path)
    s = context.scene.sanctuary_toolkit; project = active_project(context)
    uid = safe_unit_id(s.unit_id)
    if not uid: raise ValueError("Enter a valid Unit ID")
    destination = project / "Units" / uid / "LOD0"; destination.mkdir(parents=True, exist_ok=True)
    previous = s.output_dir
    try:
        s.output_dir = str(destination)
        result = bpy.ops.sanctuary.export_complete_assets()
        if result != {"FINISHED"}: raise RuntimeError("Complete unit export did not finish")
    finally:
        s.output_dir = previous
    # Unit assets and unit portraits have deliberately separate lifecycles.
    # A blend file may contain several tiers, while mod_unit_icon_png still
    # points at the last portrait that was rendered.  Saving it here would
    # silently write that stale portrait under the newly exported unit ID.
    # Icons are only changed by the explicit Icon Studio save/render actions.
    manifest = read_manifest(project); units = manifest.setdefault("units", [])
    if not any(u.get("unit_id") == uid for u in units): units.append({"unit_id": uid})
    write_manifest(project, manifest)
    return destination


def icon_project(context):
    project = active_project(context)
    prefs = get_addon_preferences(context)
    for value in (getattr(prefs, 'mods_root', ''), getattr(prefs, 'sanctuary_root', '')):
        if value:
            protected = _path(value)
            if project == protected or protected in project.parents:
                raise ValueError('Choose an authoring mod project outside the live Mods and game installation folders')
    return project


def save_unit_icon(context, allow_other_unit_source=False):
    from .unit_icons import save_png
    s = context.scene.sanctuary_toolkit
    if not s.mod_unit_icon_png:
        raise ValueError('Choose a PNG or render an icon first')
    project=icon_project(context);uid=safe_unit_id(s.unit_id);source=_path(s.mod_unit_icon_png)
    from . import unit_icon_preview as preview
    # A unit-ID edit must not accidentally resave the previous unit's portrait.
    if not allow_other_unit_source and source.parent.name.lower()=='icons' and source.parent.parent.name.lower()=='source' and preview.unit_icons.UNIT.fullmatch(source.stem.lower()) and source.stem.lower()!=uid:
        raise ValueError('Assigned PNG belongs to another unit. Reload this unit icon or choose a PNG with Use Existing Image.')
    result = save_png(project,uid,source)
    s.mod_unit_icon_png = str(result[0])
    preview.invalidate(project,uid);preview.prepare(project,uid)
    _refresh_browser(context)
    return result


def _safe_archive_members(archive):
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        parts = Path(name).parts
        if not name or name.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", name):
            raise ValueError(f"Unsafe SMT archive member: {name!r}")
        yield info


def _validate_member_name(name):
    clean=str(name).replace("\\","/")
    parts=Path(clean).parts
    if not clean or clean.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:",clean):
        raise ValueError(f"Unsafe archive member: {name!r}")
    return clean


def _rar_executable():
    for candidate in (Path(r"C:\Program Files\WinRAR\Rar.exe"),Path(r"C:\Program Files\WinRAR\WinRAR.exe")):
        if candidate.is_file():return candidate
    found=shutil.which("rar") or shutil.which("WinRAR")
    return Path(found) if found else None


def _rar_members(source):
    exe=_rar_executable()
    if not exe:raise RuntimeError("RAR import/export requires WinRAR. Use the ZIP-compatible .SMT format on this computer.")
    run=subprocess.run([str(exe),"lb","-c-",str(source)],capture_output=True,text=True,
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
    if run.returncode>1:raise RuntimeError("WinRAR could not read the archive: "+run.stderr[-500:])
    return [_validate_member_name(line.strip()) for line in run.stdout.splitlines() if line.strip()]


def pack_project(project, destination):
    project = Path(project); read_manifest(project)
    from .unit_icons import validate_project
    validate_project(project)
    destination = Path(destination)
    if destination.suffix.lower() not in {".smt",".zip",".rar"}: destination = destination.with_suffix(".smt")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    excluded={'Releases','.smt_history','.smt_cache','__pycache__','.git'}
    files=[p for p in sorted(project.rglob('*')) if p.is_file()
           and not any(part in excluded for part in p.relative_to(project).parts)
           and p.resolve() not in (destination.resolve(),temp.resolve())]
    for file in files:
        if file.is_symlink() or project.resolve() not in file.resolve().parents:
            raise ValueError('Project exchange cannot include linked/escaping files: '+str(file))
    if destination.suffix.lower()==".rar":
        exe=_rar_executable()
        if not exe:raise RuntimeError("RAR export requires WinRAR. Export as .SMT instead.")
        rar_temp=destination.with_suffix(".tmp.rar")
        if rar_temp.exists():rar_temp.unlink()
        # Stage the same allowlisted source as ZIP; never recursively archive releases/caches.
        with tempfile.TemporaryDirectory(prefix='studio_share_') as folder:
            share=Path(folder)
            for file in files:
                target=share/file.relative_to(project);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
            run=subprocess.run([str(exe),"a","-idq","-r","-ep1",str(rar_temp.resolve()),str(share/"*")],
            capture_output=True,text=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
        if run.returncode>1:raise RuntimeError("WinRAR export failed: "+(run.stderr or run.stdout)[-500:])
        if MANIFEST not in _rar_members(rar_temp):rar_temp.unlink(missing_ok=True);raise RuntimeError("RAR verification failed: manifest missing")
        rar_temp.replace(destination);return destination
    # Level 1 is dramatically faster for project exchange archives. Sanctuary
    # assets (DDS, PNG and the binary model formats) gain very little from the
    # CPU cost of level 9 compression.
    with zipfile.ZipFile(temp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as z:
        for file in files:z.write(file, file.relative_to(project).as_posix())
    with zipfile.ZipFile(temp) as z:
        _safe_archive_members(z)
        if MANIFEST not in z.namelist(): raise ValueError("SMT package is missing its manifest")
    temp.replace(destination)
    return destination


def import_project(context, source):
    source = Path(source)
    if source.suffix.lower() == ".sanpack":
        from .project_import import import_sanpack
        return import_sanpack(context, source)
    with source.open('rb') as stream: magic=stream.read(8)
    if magic.startswith(b"Rar!\x1a\x07"):
        members=_rar_members(source)
        if MANIFEST not in members:raise ValueError("RAR package is missing smt_project.json")
        with tempfile.TemporaryDirectory(prefix="smt_rar_") as tmp:
            exe=_rar_executable();run=subprocess.run([str(exe),"x","-idq","-y",str(source),str(Path(tmp))+"\\"],
                capture_output=True,text=True,creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0))
            if run.returncode>1:raise RuntimeError("WinRAR extraction failed: "+(run.stderr or run.stdout)[-500:])
            # Repack the already-validated extraction into the canonical parser.
            bridge=Path(tmp)/"bridge.smt"
            with zipfile.ZipFile(bridge,"w",zipfile.ZIP_DEFLATED) as z:
                for file in Path(tmp).rglob("*"):
                    if file.is_file() and file!=bridge:z.write(file,file.relative_to(tmp).as_posix())
            return import_project(context,bridge)
    if not magic.startswith(b"PK\x03\x04"):
        raise ValueError("Unsupported package. Use .SMT/.zip, or a genuine .rar archive created by WinRAR.")
    with zipfile.ZipFile(source) as z:
        infos = list(_safe_archive_members(z))
        try: manifest = json.loads(z.read(MANIFEST).decode("utf-8"))
        except Exception as exc: raise ValueError(f"Invalid SMT manifest: {exc}")
        name = _clean_name(manifest.get("name", source.stem))
        destination = library_root(context) / name
        if destination.exists() and any(destination.iterdir()):
            base=name + " Imported " + datetime.now().strftime("%Y%m%d-%H%M%S")
            destination=destination.with_name(base);counter=2
            while destination.exists():
                destination=destination.with_name(f"{base} ({counter})");counter+=1
        destination.mkdir(parents=True, exist_ok=False)
        root = destination.resolve()
        for info in infos:
            target = (root / info.filename).resolve()
            if root not in target.parents and target != root: raise ValueError("SMT path escaped its project folder")
            if info.is_dir(): target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as src, target.open("wb") as dst: shutil.copyfileobj(src, dst)
    read_manifest(destination)
    from .project_blueprints import ensure_project_templates, game_root
    ensure_project_templates(destination, game_root(context))
    from .unit_icons import repair_missing
    repair_missing(destination)
    context.scene.sanctuary_toolkit.mod_active_path = str(destination)
    return destination


def _merge_pack(source_root, destination, prefix):
    files = [p for p in Path(source_root).rglob("*") if p.is_file()]
    if not files: return 0
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    temp.unlink(missing_ok=True)
    if destination.is_file():
        # ZipFile append mode seeks to the old central directory before it
        # writes. Removing replaced entries from filelist lets us preserve the
        # already-compressed bytes of every unchanged asset and append only the
        # project files. The previous implementation inflated and recompressed
        # the entire live pack for every deployment.
        shutil.copy2(destination, temp)
    updates = {
        (Path(prefix) / file.relative_to(source_root)).as_posix(): file
        for file in files
    }
    mode = "a" if temp.is_file() else "w"
    with zipfile.ZipFile(temp, mode, zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as z:
        if mode == "a":
            replaced = {name.replace("\\", "/").lower() for name in updates}
            z.filelist[:] = [
                info for info in z.filelist
                if info.filename.replace("\\", "/").lower() not in replaced
            ]
            z.NameToInfo = {info.filename: info for info in z.filelist}
        for name, file in sorted(updates.items()):
            z.write(file, name)
    # Validate only the new records. Unchanged records were copied byte-for-byte
    # and a full testzip() needlessly inflates a multi-gigabyte game archive.
    with zipfile.ZipFile(temp) as z:
        names = {name.replace("\\", "/").lower(): name for name in z.namelist()}
        for wanted in updates:
            actual = names.get(wanted.lower())
            if actual is None:
                raise RuntimeError(f"Deployment archive is missing {wanted}")
            with z.open(actual) as stream:
                while stream.read(1024 * 1024):
                    pass
    temp.replace(destination)
    return len(files)


def deploy_project(context):
    project = active_project(context); manifest = read_manifest(project)
    from .project_release import legacy_dycom
    if legacy_dycom(project):
        raise ValueError('This project uses a custom faction/loader adapter. Use its MM release; direct installation cannot safely claim standalone faction slots or replace its existing Lua catcher.')
    if manifest.get('release'):
        from .studio_runtime import deploy_project as deploy_generic
        from .studio import engine
        prefs=get_addon_preferences(context)
        if not prefs or not prefs.mods_root:raise ValueError('Configure the live Sanctuary Mods folder.')
        return deploy_generic(project,engine(context),_path(prefs.mods_root),
                              context.scene.sanctuary_toolkit.mod_export_version,
                              bpy.path.abspath(context.scene.sanctuary_publishing.mm_bundle))
    raise ValueError('Save Project > Faction & Release setup first. Direct installation must validate the Lua runtime; simply copying loose Lua to Mods does not activate it. New faction projects must use MM.')
class _ProjectOperator(Operator):
    def fail(self, exc): console(str(exc)); self.report({"ERROR"}, str(exc)); return {"CANCELLED"}


class SANCTUARY_OT_mod_create(_ProjectOperator):
    bl_idname="sanctuary.mod_create"; bl_label="Create Mod Project"
    def execute(self, context):
        try: path=create_project(context);_refresh_browser(context);self.report({"INFO"}, f"Created {path.name}"); return {"FINISHED"}
        except Exception as exc:return self.fail(exc)


class SANCTUARY_OT_mod_generate_lua(_ProjectOperator):
    bl_idname="sanctuary.mod_generate_lua"; bl_label="Create / Update Unit Template"
    def execute(self, context):
        try:
            from .lua_editor import save_draft
            path=save_draft(context);suffix=""
            _refresh_browser(context);self.report({"INFO"}, f"Wrote {path.name}{suffix}"); return {"FINISHED"}
        except Exception as exc:return self.fail(exc)


class SANCTUARY_OT_mod_export_unit(_ProjectOperator):
    bl_idname="sanctuary.mod_export_unit"; bl_label="Export Unit Into Project"
    def execute(self, context):
        try:
            path=export_active_unit(context);suffix=""
            _refresh_browser(context);self.report({"INFO"}, f"Updated {path}{suffix}; existing icon preserved"); return {"FINISHED"}
        except Exception as exc:return self.fail(exc)


class SANCTUARY_OT_mod_deploy(_ProjectOperator):
    bl_idname="sanctuary.mod_deploy"; bl_label="Deploy / Update Live Mod"
    def execute(self, context):
        try:
            counts,backup=deploy_project(context);_refresh_browser(context)
            suffix=""
            self.report({"INFO"}, f"Deployed {counts}{suffix}"); return {"FINISHED"}
        except Exception as exc:return self.fail(exc)


class SANCTUARY_OT_mod_pack(_ProjectOperator):
    bl_idname="sanctuary.mod_pack"; bl_label="Export Shareable .SMT"
    def execute(self, context):
        try:
            s=context.scene.sanctuary_toolkit; project=active_project(context)
            target=_path(s.mod_archive_path) if s.mod_archive_path else project.parent/(project.name+".smt")
            target=pack_project(project,target); s.mod_archive_path=str(target)
            self.report({"INFO"}, f"Exported {target.name}"); return {"FINISHED"}
        except Exception as exc:return self.fail(exc)


class SANCTUARY_OT_mod_export_remmy(_ProjectOperator):
    bl_idname="sanctuary.mod_export_remmy"
    bl_label="Export Mod API 1.3 ZIP"
    bl_description="Recommended: validate and package this project for Remmy Mod Manager / Mod API 1.3"
    def execute(self, context):
        try:
            project = active_project(context)
            settings = context.scene.sanctuary_toolkit
            version = normalize_version(settings.mod_export_version or read_export_version(project))
            from .studio import engine
            from .project_release import export_mm, legacy_dycom
            from .studio_validation import project_preflight
            project_preflight(context)
            target, counts = export_hosted(project, version=version) if legacy_dycom(project) else export_mm(project,engine(context),version)
            write_export_version(project, version)
            settings.mod_export_version = version
            self.report({"INFO"}, f"Mod API 1.3 ZIP: {target.name} ({counts['assets']} assets, {counts['lua']} Lua files, {counts['plugins']} plugin)")
            return {"FINISHED"}
        except Exception as exc:
            return self.fail(exc)


class SANCTUARY_OT_mod_export_standalone(_ProjectOperator):
    bl_idname="sanctuary.mod_export_standalone"
    bl_label="Export Standalone ZIP"
    bl_description="Compile and validate raw engine and Mods folders with no Mod Manager dependency"
    def execute(self, context):
        try:
            project = active_project(context)
            settings = context.scene.sanctuary_toolkit
            version = normalize_version(settings.mod_export_version or read_export_version(project))
            from .studio import engine
            from .project_release import legacy_dycom
            from .studio_validation import project_preflight
            project_preflight(context)
            if legacy_dycom(project):target, counts = export_standalone(project, version=version, engine_root=engine(context))
            else:
                from .studio_runtime import export_standalone_project
                v=context.scene.sanctuary_publishing
                target,counts=export_standalone_project(project,engine(context),version,bpy.path.abspath(v.mm_bundle))
            write_export_version(project, version)
            settings.mod_export_version = version
            self.report({"INFO"}, f"Standalone ZIP: {target.name} ({counts['packs']} packs, {counts['lua']} Lua files, {counts['ai']} physical AI files)")
            return {"FINISHED"}
        except Exception as exc:
            return self.fail(exc)


class SANCTUARY_OT_mod_export_uber(_ProjectOperator):
    bl_idname="sanctuary.mod_export_uber"
    bl_label="Export Uber Engineer Tester ZIP"
    bl_description="Create the separate optional Uber Engineer installer ZIP for testers"
    def execute(self, context):
        try:
            from .submod_export import export_submod
            project = active_project(context)
            target, counts = export_submod(project)
            self.report({"INFO"}, f"Uber tester installer: {target.name} ({counts['assets']} assets, {counts['lua']} Lua files)")
            return {"FINISHED"}
        except Exception as exc:
            return self.fail(exc)


class SANCTUARY_OT_mod_import(_ProjectOperator):
    bl_idname="sanctuary.mod_import"; bl_label="Import .SMT / .sanpack"
    def execute(self, context):
        try:
            source=_path(context.scene.sanctuary_toolkit.mod_archive_path)
            path=import_project(context,source);_refresh_browser(context);self.report({"INFO"}, f"Imported {path.name}"); return {"FINISHED"}
        except Exception as exc:return self.fail(exc)


class SANCTUARY_OT_mod_open(_ProjectOperator):
    bl_idname="sanctuary.mod_open";bl_label="Open Active Project Folder"
    def execute(self,context):
        try:
            path=active_project(context);bpy.ops.wm.path_open(filepath=str(path));return {"FINISHED"}
        except Exception as exc:return self.fail(exc)


CLASSES=(SANCTUARY_OT_mod_create,SANCTUARY_OT_mod_generate_lua,SANCTUARY_OT_mod_export_unit,
         SANCTUARY_OT_mod_deploy,SANCTUARY_OT_mod_pack,SANCTUARY_OT_mod_export_remmy,
         SANCTUARY_OT_mod_export_standalone,SANCTUARY_OT_mod_export_uber,
         SANCTUARY_OT_mod_import,SANCTUARY_OT_mod_open)

def register():
    for cls in CLASSES:bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(CLASSES):bpy.utils.unregister_class(cls)
