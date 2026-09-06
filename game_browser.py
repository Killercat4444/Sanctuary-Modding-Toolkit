import json
import os
import re
import struct
import time
import zipfile
import zlib
from pathlib import Path

import bpy
import bpy.utils.previews
import numpy as np
from bpy.types import Operator, Panel

from .dds_tools import decode_dds_top
from .material_io import apply_sanctuary_material, classify_texture_name
from .sananimation_io import import_sananimation_bytes
from .sanmodel_io import import_sanmodel, parse_sanmodel_bytes
from .sanskeleton_io import create_armature_from_skeleton, parse_sanskeleton_bytes
from .utils import (
    console,
    database_path,
    get_addon_preferences,
    icon_cache_dir,
    resolve_engine_root,
)

_DATABASE = []
_DATABASE_BY_ID = {}
_PREVIEWS = None
_LAST_CLICK_ID = None
_LAST_CLICK_TIME = 0.0
_DATABASE_SCHEMA_VERSION = 6


def _archive_member_map(zf):
    return {n.lower(): n for n in zf.namelist()}


def _read_template(path):
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _quoted_field(text, field):
    m = re.search(rf"\b{re.escape(field)}\s*=\s*[\"']([^\"']+)[\"']", text)
    return m.group(1).strip() if m else ""


def _tags(text):
    return set(re.findall(r"[\"']([A-Z0-9_]+)[\"']", text.upper()))


def _infer_faction(text, unit_id):
    tags = _tags(text)
    for tag, label in (("CHOSEN", "Chosen"), ("GUARD", "Guard"), ("EDA", "EDA")):
        if tag in tags:
            return label
    # Current Sanctuary prefixes, used only as a fallback when a template is absent.
    prefix = unit_id[:2].lower()
    return {"uc": "Chosen", "ug": "Guard", "ue": "EDA"}.get(prefix, "Other")


def _infer_tech(text, unit_id, display_name):
    tags = _tags(text)
    label = (display_name or "").upper()
    if unit_id.endswith("0000") or "COMMANDER" in label or "COMMAND" in tags:
        return "COMMANDER"
    for n in (1, 2, 3, 4):
        if f"TECH{n}" in tags or f"TIER{n}" in tags:
            return f"T{n}"
    m = re.search(r"\bTIER\s*([1-4])\b", label)
    if m:
        return f"T{m.group(1)}"
    if "EXPERIMENTAL" in tags or "EXPERIMENTAL" in label:
        return "T4"
    return "OTHER"


def _infer_domain(text, unit_id):
    tags = _tags(text)
    if "AIR" in tags:
        return "AIR"
    if "NAVY" in tags or "NAVAL" in tags or "SEA" in tags:
        return "NAVY"
    if "STRUCTURE" in tags or "BUILDING" in tags:
        return "STRUCTURE"
    if "LAND" in tags:
        return "LAND"

    # Unit-ID fallback. The third letter is normally the domain family.
    if len(unit_id) >= 3:
        family = unit_id[2].lower()
        return {
            "a": "AIR",
            "l": "LAND",
            "n": "NAVY",
            "s": "STRUCTURE",
            "b": "STRUCTURE",
        }.get(family, "OTHER")
    return "OTHER"


def _unit_aliases(unit_id, display_name, unit_type, text, faction, tech, domain):
    # Search is intentionally based on user-facing identity fields only.
    # Do NOT index template tags such as BUILDABLE_BY_T1_ENGINEER; those made
    # a search for "engineer" return nearly every unit an engineer can build.
    aliases = [unit_id, display_name, unit_type, faction, tech, domain]
    if unit_id.endswith("0000"):
        aliases.append("commander")
    return " ".join(a for a in aliases if a).lower()


def _texture_members_for_base(umap, base):
    found = {}
    for low, actual in umap.items():
        if not low.startswith(base) or not low.endswith(".dds"):
            continue
        kind = classify_texture_name(actual)
        if not kind:
            continue
        current = found.get(kind)
        if current is None or len(actual) < len(current):
            found[kind] = actual
    return found


def _texture_members_from_material(zf, umap, material_member, base):
    """Prefer DDS paths explicitly referenced by the unit sanmaterial.

    The material serializer has changed over time, so this intentionally scans
    DDS-looking strings instead of assuming one JSON/table schema.
    """
    if not material_member:
        return {}
    try:
        text = zf.read(material_member).decode("utf-8", "replace")
    except Exception:
        return {}
    refs = re.findall(r"(?i)([A-Za-z0-9_./\\ -]+?\.dds)", text)
    found = {}
    for ref in refs:
        clean = ref.strip().replace("\\", "/").lstrip("./")
        actual = umap.get(clean.lower())
        if actual is None:
            actual = umap.get((base + Path(clean).name).lower())
        if actual is None:
            continue
        kind = classify_texture_name(actual)
        if kind and kind not in found:
            found[kind] = actual
    return found


def build_database(engine_root):
    engine = Path(engine_root)
    gamedata = engine / "Sanctuary_Data" / "Gamedata"
    units_pack = gamedata / "Units.sanpack"
    ui_pack = gamedata / "ui.sanpack"
    if not units_pack.is_file():
        raise FileNotFoundError(f"Units.sanpack not found: {units_pack}")
    if not ui_pack.is_file():
        raise FileNotFoundError(f"ui.sanpack not found: {ui_pack}")

    with zipfile.ZipFile(units_pack, "r") as uz, zipfile.ZipFile(ui_pack, "r") as iz:
        umap = _archive_member_map(uz)
        imap = _archive_member_map(iz)

        unit_ids = set()
        for member in uz.namelist():
            m = re.match(r"(?i)^Units/([a-z]{3}\d{4})/LOD0/.*_lod0\.sanmodel$", member)
            if m:
                unit_ids.add(m.group(1).lower())

        template_root = engine / "LJ" / "lua" / "common" / "units" / "unitsTemplates"
        records = []
        for unit_id in sorted(unit_ids):
            tp = template_root / unit_id / f"{unit_id}.santp"
            text = _read_template(tp)
            display_name = _quoted_field(text, "displayName") or unit_id
            unit_type = _quoted_field(text, "unitTypeName")
            faction = _infer_faction(text, unit_id)
            tech = _infer_tech(text, unit_id, display_name)
            domain = _infer_domain(text, unit_id)

            base = f"units/{unit_id}/lod0/"
            exact_model = f"{base}{unit_id}_lod0.sanmodel"
            model_member = umap.get(exact_model)
            if not model_member:
                model_member = next((actual for low, actual in umap.items() if low.startswith(base) and low.endswith("_lod0.sanmodel")), "")
            skeleton_member = umap.get(f"{base}{unit_id}.sanskeleton", "")
            animation_member = umap.get(f"{base}{unit_id}.sananimation", "")
            material_member = umap.get(f"{base}{unit_id}_lod0.sanmaterial", "")
            icon_member = imap.get(f"ui/sprites/icons/units/{unit_id}.dds", "")
            texture_members = _texture_members_from_material(uz, umap, material_member, base)
            fallback_textures = _texture_members_for_base(umap, base)
            for kind, member in fallback_textures.items():
                texture_members.setdefault(kind, member)

            records.append({
                "unit_id": unit_id,
                "display_name": display_name,
                "unit_type": unit_type,
                "faction": faction,
                "tech_level": tech,
                "domain": domain,
                "model_member": model_member,
                "skeleton_member": skeleton_member,
                "animation_member": animation_member,
                "material_member": material_member,
                "icon_member": icon_member,
                "albedo_member": texture_members.get("albedo", ""),
                "mask_member": texture_members.get("mask", ""),
                "normal_member": texture_members.get("normal", ""),
                "template_path": str(tp),
                "search": _unit_aliases(unit_id, display_name, unit_type, text, faction, tech, domain),
            })

    payload = {
        "schema_version": _DATABASE_SCHEMA_VERSION,
        "engine_root": str(engine),
        "units_pack": str(units_pack),
        "ui_pack": str(ui_pack),
        "records": records,
    }
    database_path().write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _set_database(records)
    clear_previews()
    return payload


def _set_database(records):
    global _DATABASE, _DATABASE_BY_ID
    _DATABASE = list(records)
    _DATABASE_BY_ID = {r["unit_id"]: r for r in _DATABASE}


def load_database():
    if _DATABASE:
        return _DATABASE
    path = database_path()
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            _set_database(payload.get("records", []))
        except Exception as exc:
            console(f"Could not load cached unit database: {exc}")
    return _DATABASE


def get_database_payload():
    path = database_path()
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def database_needs_upgrade():
    payload = get_database_payload()
    if not payload:
        return True
    if int(payload.get("schema_version", 0)) < _DATABASE_SCHEMA_VERSION:
        return True
    records = payload.get("records", [])
    return bool(records and any(
        "tech_level" not in r or "domain" not in r or "albedo_member" not in r
        for r in records
    ))


def _matches_search(record, query):
    terms = [t for t in (query or "").lower().split() if t]
    if not terms:
        return True
    # Build the haystack from visible/name-like fields at query time so even an
    # older cached database cannot reintroduce template tags into search.
    # Only actual unit identity/name fields are searchable. Faction, tech,
    # domain, build tags and blueprint metadata belong to the checkbox filters,
    # not the text search. Thus "engineer" returns Engineer/Engineering Station
    # names rather than everything an engineer can build.
    fields = ("unit_id", "display_name", "unit_type")
    hay = " ".join(str(record.get(k, "")) for k in fields).casefold()
    if str(record.get("unit_id", "")).lower().endswith("0000"):
        hay += " commander"
    return all(t.casefold() in hay for t in terms)


def _matches_filters(record, settings):
    faction_enabled = {
        "Chosen": settings.browser_faction_chosen,
        "Guard": settings.browser_faction_guard,
        "EDA": settings.browser_faction_eda,
        "Other": settings.browser_faction_other,
    }
    if not faction_enabled.get(record.get("faction", "Other"), settings.browser_faction_other):
        return False

    tech_enabled = {
        "COMMANDER": settings.browser_tech_commander,
        "T1": settings.browser_tech1,
        "T2": settings.browser_tech2,
        "T3": settings.browser_tech3,
        "T4": settings.browser_tech4,
        "OTHER": settings.browser_tech_other,
    }
    if not tech_enabled.get(record.get("tech_level", "OTHER"), settings.browser_tech_other):
        return False

    domain_enabled = {
        "LAND": settings.browser_domain_land,
        "AIR": settings.browser_domain_air,
        "NAVY": settings.browser_domain_navy,
        "STRUCTURE": settings.browser_domain_structure,
        "OTHER": settings.browser_domain_other,
    }
    return domain_enabled.get(record.get("domain", "OTHER"), settings.browser_domain_other)


def filtered_units(settings, query_override=None):
    records = load_database()
    query = settings.browser_search if query_override is None else query_override
    out = [r for r in records if _matches_search(r, query) and _matches_filters(r, settings)]
    out.sort(key=lambda r: (
        {"Chosen": 0, "Guard": 1, "EDA": 2}.get(r.get("faction"), 9),
        {"COMMANDER": 0, "T1": 1, "T2": 2, "T3": 3, "T4": 4, "OTHER": 9}.get(r.get("tech_level"), 9),
        r.get("display_name", ""),
        r.get("unit_id", ""),
    ))
    return out


def search_units(query, limit=30):
    # Kept for API/backward compatibility with v1.0 UI code and scripts.
    records = load_database()
    matches = [r for r in records if _matches_search(r, query)]
    return matches[:limit]


def _ensure_previews():
    global _PREVIEWS
    if _PREVIEWS is None:
        _PREVIEWS = bpy.utils.previews.new()
    return _PREVIEWS


def clear_previews():
    global _PREVIEWS
    if _PREVIEWS is not None:
        bpy.utils.previews.remove(_PREVIEWS)
        _PREVIEWS = None


def _rgb565(c):
    r = ((c >> 11) & 31) * 255 // 31
    g = ((c >> 5) & 63) * 255 // 63
    b = (c & 31) * 255 // 31
    return np.array((r, g, b), dtype=np.uint8)


def _decode_dxt5_top(blob):
    """Decode the top DXT5/BC3 mip of a DDS icon to RGBA uint8."""
    if blob[:4] != b"DDS " or len(blob) < 128:
        raise ValueError("not a DDS file")
    height, width = struct.unpack_from("<II", blob, 12)
    fourcc = blob[84:88]
    if fourcc != b"DXT5":
        raise ValueError(f"unsupported DDS FourCC {fourcc!r}; expected DXT5")
    bw = (width + 3) // 4
    bh = (height + 3) // 4
    needed = 128 + bw * bh * 16
    if len(blob) < needed:
        raise ValueError("truncated DXT5 DDS")

    out = np.zeros((bh * 4, bw * 4, 4), dtype=np.uint8)
    off = 128
    for by in range(bh):
        for bx in range(bw):
            block = blob[off:off + 16]
            off += 16
            a0, a1 = block[0], block[1]
            ap = [int(a0), int(a1)]
            if a0 > a1:
                ap.extend([((8-k)*a0 + (k-1)*a1) // 7 for k in range(2, 8)])
            else:
                ap.extend([((6-k)*a0 + (k-1)*a1) // 5 for k in range(2, 6)])
                ap.extend([0, 255])
            abits = int.from_bytes(block[2:8], "little")

            c0, c1 = struct.unpack_from("<HH", block, 8)
            p0 = _rgb565(c0).astype(np.uint16)
            p1 = _rgb565(c1).astype(np.uint16)
            p2 = ((2 * p0 + p1) // 3).astype(np.uint8)
            p3 = ((p0 + 2 * p1) // 3).astype(np.uint8)
            cp = [p0.astype(np.uint8), p1.astype(np.uint8), p2, p3]
            cbits = int.from_bytes(block[12:16], "little")

            for i in range(16):
                y, x = divmod(i, 4)
                ai = (abits >> (3 * i)) & 7
                ci = (cbits >> (2 * i)) & 3
                out[by * 4 + y, bx * 4 + x, :3] = cp[ci]
                out[by * 4 + y, bx * 4 + x, 3] = ap[ai]
    return out[:height, :width]


def _write_png(path, rgba):
    rgba = np.asarray(rgba, dtype=np.uint8)
    h, w, c = rgba.shape
    if c != 4:
        raise ValueError("PNG writer expects RGBA")
    raw = b"".join(b"\x00" + rgba[y].tobytes() for y in range(h))

    def chunk(kind, payload):
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    Path(path).write_bytes(png)


def _question_mark_png(path):
    size = 96
    img = np.zeros((size, size, 4), dtype=np.uint8)
    img[..., :3] = 42
    img[..., 3] = 255
    # border
    img[2:5, 2:-2, :3] = 90
    img[-5:-2, 2:-2, :3] = 90
    img[2:-2, 2:5, :3] = 90
    img[2:-2, -5:-2, :3] = 90
    # A chunky '?', deliberately simple and readable as a fallback icon.
    mask = np.zeros((7, 5), dtype=np.uint8)
    glyph = [
        "11110",
        "00001",
        "00001",
        "00110",
        "00100",
        "00000",
        "00100",
    ]
    for y, row in enumerate(glyph):
        for x, ch in enumerate(row):
            mask[y, x] = ch == "1"
    scale = 9
    gh, gw = mask.shape
    y0 = (size - gh * scale) // 2
    x0 = (size - gw * scale) // 2
    for y in range(gh):
        for x in range(gw):
            if mask[y, x]:
                img[y0+y*scale:y0+(y+1)*scale, x0+x*scale:x0+(x+1)*scale, :3] = 230
    _write_png(path, img)


def _preview_from_path(key, path):
    pcoll = _ensure_previews()
    if key in pcoll:
        return pcoll[key].icon_id
    try:
        preview = pcoll.load(key, str(path), "IMAGE")
        return preview.icon_id
    except Exception as exc:
        console(f"Could not load preview {path}: {exc}")
        return 0


def question_icon_id():
    cache = icon_cache_dir() / "__question__.png"
    if not cache.is_file():
        _question_mark_png(cache)
    return _preview_from_path("__question__", cache)


def icon_id_for_record(record):
    member = record.get("icon_member")
    if not member:
        return question_icon_id()
    pcoll = _ensure_previews()
    key = record["unit_id"]
    if key in pcoll:
        return pcoll[key].icon_id

    payload = get_database_payload()
    if not payload:
        return question_icon_id()
    ui_pack = Path(payload.get("ui_pack", ""))
    if not ui_pack.is_file():
        return question_icon_id()

    # Convert to PNG ourselves. This avoids depending on Blender's DDS preview
    # support and gives the large card view a deterministic icon.
    cache_file = icon_cache_dir() / f"{key}.png"
    if not cache_file.is_file():
        try:
            with zipfile.ZipFile(ui_pack, "r") as zf:
                rgba = decode_dds_top(zf.read(member))
            _write_png(cache_file, rgba)
        except Exception as exc:
            console(f"Could not decode icon {member}: {exc}")
            return question_icon_id()
    icon_id = _preview_from_path(key, cache_file)
    return icon_id or question_icon_id()


def import_unit_from_database(context, unit_id, include_skeleton=True, include_textures=True, include_animations=True):
    load_database()
    record = _DATABASE_BY_ID.get(unit_id)
    if not record:
        raise ValueError(f"Unit '{unit_id}' is not in the indexed database")
    payload = get_database_payload()
    if not payload:
        raise ValueError("Unit database metadata is unavailable; rebuild the database")
    units_pack = Path(payload["units_pack"])
    if not units_pack.is_file():
        raise FileNotFoundError(units_pack)
    if not record.get("model_member"):
        raise ValueError(f"No LOD0 .sanmodel was indexed for {unit_id}")

    settings = context.scene.sanctuary_toolkit
    with zipfile.ZipFile(units_pack, "r") as zf:
        model = parse_sanmodel_bytes(zf.read(record["model_member"]))
        skeleton_obj = None
        if include_skeleton and record.get("skeleton_member"):
            skeleton = parse_sanskeleton_bytes(zf.read(record["skeleton_member"]))
            skeleton_obj = create_armature_from_skeleton(
                context, skeleton, name=f"{unit_id}_Rig", swap_yz=settings.swap_yz_axis,
            )
        obj = import_sanmodel(
            context, model, skeleton_obj=skeleton_obj,
            swap_yz=settings.swap_yz_axis, mirror_uv=False,
        )

        if include_textures:
            blobs = {}
            labels = {}
            for kind, key in (("albedo", "albedo_member"), ("mask", "mask_member"), ("normal", "normal_member")):
                member = record.get(key)
                if member:
                    try:
                        blobs[kind] = zf.read(member)
                        labels[kind] = f"{units_pack}!/{member}"
                    except Exception as exc:
                        console(f"Could not read {member}: {exc}")
            if blobs:
                try:
                    apply_sanctuary_material(obj, unit_id, blobs, labels)
                except Exception as exc:
                    console(f"Could not build texture preview for {unit_id}: {exc}")

        if include_animations and skeleton_obj and record.get("animation_member"):
            try:
                clips, actions = import_sananimation_bytes(
                    skeleton_obj, zf.read(record["animation_member"]),
                    source_label=f"{units_pack}!/{record['animation_member']}",
                    swap_yz=settings.swap_yz_axis,
                )
                if actions:
                    settings.preview_animation = clips[0].name
            except Exception as exc:
                console(f"Could not import animations for {unit_id}: {exc}")

    if skeleton_obj:
        skeleton_obj.location = context.scene.cursor.location
    else:
        obj.location = context.scene.cursor.location
    settings.unit_id = unit_id
    obj["san_unit_id"] = unit_id
    obj["san_archive_model_member"] = record["model_member"]
    if skeleton_obj:
        skeleton_obj["san_unit_id"] = unit_id
        skeleton_obj["san_archive_skeleton_member"] = record.get("skeleton_member", "")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    context.view_layer.objects.active = obj
    return obj, skeleton_obj


class SANCTUARY_OT_build_unit_database(Operator):
    bl_idname = "sanctuary.build_unit_database"
    bl_label = "Index Sanctuary Units"
    bl_description = "Scan Sanctuary unit templates, Units.sanpack, and ui.sanpack to build the searchable unit database"

    def execute(self, context):
        prefs = get_addon_preferences(context)
        engine = resolve_engine_root(prefs.sanctuary_root if prefs else "")
        if not engine:
            self.report({"ERROR"}, "Choose a valid Sanctuary game folder first")
            return {"CANCELLED"}
        try:
            payload = build_database(engine)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Indexed {len(payload['records'])} Sanctuary units")
        return {"FINISHED"}


class SANCTUARY_OT_import_database_unit(Operator):
    bl_idname = "sanctuary.import_database_unit"
    bl_label = "Import Selected Unit"
    unit_id: bpy.props.StringProperty(default="")

    def execute(self, context):
        unit_id = self.unit_id or context.scene.sanctuary_toolkit.browser_selected_unit
        if not unit_id:
            self.report({"ERROR"}, "No unit selected")
            return {"CANCELLED"}
        try:
            st = context.scene.sanctuary_toolkit
            import_unit_from_database(
                context, unit_id,
                include_skeleton=st.browser_import_skeleton,
                include_textures=st.browser_import_textures,
                include_animations=st.browser_import_animations,
            )
        except Exception as exc:
            console(f"Import failed: {exc}")
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self.report({"INFO"}, f"Imported {unit_id} LOD0")
        return {"FINISHED"}


class SANCTUARY_OT_browser_click(Operator):
    bl_idname = "sanctuary.browser_click"
    bl_label = "Select Unit"
    bl_description = "Select this unit; double-click to import its LOD0 model"
    unit_id: bpy.props.StringProperty()

    def execute(self, context):
        global _LAST_CLICK_ID, _LAST_CLICK_TIME
        now = time.monotonic()
        settings = context.scene.sanctuary_toolkit
        settings.browser_selected_unit = self.unit_id
        if _LAST_CLICK_ID == self.unit_id and (now - _LAST_CLICK_TIME) <= 0.55:
            _LAST_CLICK_ID = None
            _LAST_CLICK_TIME = 0.0
            return bpy.ops.sanctuary.import_database_unit(unit_id=self.unit_id)
        _LAST_CLICK_ID = self.unit_id
        _LAST_CLICK_TIME = now
        return {"FINISHED"}


def _resolve_unit_location(unit_id, destination, record, payload):
    """Resolve the real loose-file location, with an honest archive fallback."""
    unit_id = str(unit_id).lower()
    if not re.fullmatch(r"[a-z]{3}\d{4}", unit_id):
        raise ValueError("Invalid unit ID")
    engine = Path(payload.get("engine_root", ""))
    units_pack = Path(payload.get("units_pack", ""))
    if destination == "LUA_TEMPLATE":
        template_dir = engine / "LJ" / "lua" / "common" / "units" / "unitsTemplates" / unit_id
        candidates = [template_dir / f"{unit_id}.lua", template_dir / f"{unit_id}.santp"]
        for candidate in candidates:
            if candidate.is_file():
                return {"path": candidate, "kind": "file", "member": ""}
        raise FileNotFoundError(f"No Lua/.santp unit template found in {template_dir}")

    if destination != "UNIT_FILES":
        raise ValueError(f"Unknown unit location: {destination}")
    loose = units_pack.parent / "Units" / unit_id / "LOD0"
    expected_names = {f"{unit_id}_lod0.sanmodel"}
    for key in ("model_member", "skeleton_member", "animation_member", "material_member", "albedo_member", "mask_member", "normal_member"):
        member = str(record.get(key, "")).replace("\\", "/")
        if member.lower().startswith(f"units/{unit_id}/lod0/"):
            expected_names.add(member.rsplit("/", 1)[-1])
    if loose.is_dir() and all((loose / name).is_file() for name in expected_names):
        return {"path": loose, "kind": "folder", "member": ""}
    if units_pack.is_file():
        return {
            "path": units_pack,
            "kind": "archive",
            "member": f"Units/{unit_id}/LOD0",
        }
    raise FileNotFoundError(f"Neither {loose} nor {units_pack} exists")


def _reveal_in_file_browser(path, select_file=False):
    path = Path(path).resolve()
    # Open the actual directory. Explorer's /select argument was parsed
    # inconsistently for paths with spaces and could open Documents instead.
    folder = path.parent if select_file else path
    if not folder.is_dir():
        raise FileNotFoundError(f"Folder not found: {folder}")
    if os.name == "nt":
        os.startfile(str(folder))
    else:
        bpy.ops.wm.path_open(filepath=str(folder))


class SANCTUARY_OT_open_unit_location(Operator):
    bl_idname = "sanctuary.open_unit_location"
    bl_label = "Open Unit Location"
    bl_description = "Open this unit's LOD0 files or reveal its Lua/.santp template"

    unit_id: bpy.props.StringProperty(default="")
    destination: bpy.props.EnumProperty(
        name="Location",
        items=(
            ("UNIT_FILES", "Unit Files (LOD0)", "Open LOD0; ask to extract browsing copies if the folder is missing", "FILE_FOLDER", 0),
            ("LUA_TEMPLATE", "Lua / Unit Template", "Reveal the unit's Lua/.santp template in the system file browser", "FILE_SCRIPT", 1),
        ),
        default="UNIT_FILES",
    )

    def execute(self, context):
        load_database()
        unit_id = self.unit_id or context.scene.sanctuary_toolkit.browser_selected_unit
        record = _DATABASE_BY_ID.get(unit_id)
        payload = get_database_payload()
        if not record or not payload:
            self.report({"ERROR"}, "Unit database metadata is unavailable; rebuild the unit index")
            return {"CANCELLED"}
        try:
            location = _resolve_unit_location(unit_id, self.destination, record, payload)
            path = location["path"]
            if location["kind"] == "archive":
                dispatched = bpy.ops.sanctuary.extract_units_for_browsing(
                    "INVOKE_DEFAULT", unit_id=unit_id, archive_path=str(path.resolve())
                )
                return {"CANCELLED"} if "CANCELLED" in dispatched else {"FINISHED"}
            else:
                _reveal_in_file_browser(path, select_file=location["kind"] == "file")
        except Exception as exc:
            self.report({"ERROR"}, f"Could not open unit location: {exc}")
            return {"CANCELLED"}
        return {"FINISHED"}


class SANCTUARY_OT_extract_units_for_browsing(Operator):
    bl_idname = "sanctuary.extract_units_for_browsing"
    bl_label = "Extract Units for Direct Folder Access?"
    bl_description = "Extract reference copies beside Units.sanpack, preserving existing files"

    unit_id: bpy.props.StringProperty(default="", options={"HIDDEN"})
    archive_path: bpy.props.StringProperty(default="", options={"HIDDEN"})

    def invoke(self, context, event):
        from .unit_extraction import extraction_summary
        try:
            if not re.fullmatch(r"[a-z]{3}\d{4}", self.unit_id):
                raise ValueError("Invalid unit ID")
            self._summary = extraction_summary(self.archive_path)
        except Exception as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=590)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Direct folder access requires extracting Units.sanpack.", icon="INFO")
        layout.label(text="A Units folder will be created beside the archive.")
        layout.label(text="These are browsing/reference copies, not a deployed mod.", icon="ERROR")
        layout.label(text="The game uses its packed/mod assets; editing these copies alone")
        layout.label(text="does not update the game. Use your normal mod deployment.")
        layout.separator()
        summary = getattr(self, "_summary", {})
        layout.label(text=f"{summary.get('files', 0):,} files; up to {summary.get('missing_bytes', 0) / (1024**2):,.1f} MiB additional space")
        layout.label(text="Existing files are kept. The archive is not modified.")
        layout.label(text="After extraction, the requested LOD0 folder will open.")
        layout.label(text="Press Esc while extracting to cancel; completed files are kept.")

    def execute(self, context):
        from .unit_extraction import extract_units_incrementally
        if not re.fullmatch(r"[a-z]{3}\d{4}", self.unit_id):
            self.report({"ERROR"}, "Invalid unit ID")
            return {"CANCELLED"}
        self._iterator = extract_units_incrementally(self.archive_path)
        self._timer = context.window_manager.event_timer_add(0.03, window=context.window)
        context.window_manager.progress_begin(0, 100)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def _finish(self, context):
        if getattr(self, "_iterator", None):
            self._iterator.close()
            self._iterator = None
        if getattr(self, "_timer", None):
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        context.window_manager.progress_end()

    def cancel(self, context):
        self._finish(context)

    def modal(self, context, event):
        if event.type == "ESC":
            self._finish(context)
            self.report({"WARNING"}, "Extraction cancelled. Existing and completed files were kept; click again to resume.")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"RUNNING_MODAL"}
        deadline = time.perf_counter() + 0.025
        try:
            while time.perf_counter() < deadline:
                finished, total = next(self._iterator)
                context.window_manager.progress_update(100.0 * finished / max(1, total))
        except StopIteration as result:
            self._finish(context)
            folder = Path(self.archive_path).resolve().parent / "Units" / self.unit_id / "LOD0"
            try:
                _reveal_in_file_browser(folder)
            except Exception as exc:
                self.report({"ERROR"}, f"Extraction completed, but the requested folder could not open: {exc}")
                return {"CANCELLED"}
            stats = result.value or {}
            self.report({"INFO"}, f"Units extracted successfully: {stats.get('created', 0):,} new files, {stats.get('skipped', 0):,} existing files kept. Opened {self.unit_id}/LOD0.")
            return {"FINISHED"}
        except Exception as exc:
            self._finish(context)
            self.report({"ERROR"}, f"Extraction stopped safely: {exc}")
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


class SANCTUARY_OT_browser_prev_page(Operator):
    bl_idname = "sanctuary.browser_prev_page"
    bl_label = "Previous Page"

    def execute(self, context):
        s = context.scene.sanctuary_toolkit
        s.browser_page = max(0, s.browser_page - 1)
        return {"FINISHED"}


class SANCTUARY_OT_browser_next_page(Operator):
    bl_idname = "sanctuary.browser_next_page"
    bl_label = "Next Page"

    def execute(self, context):
        s = context.scene.sanctuary_toolkit
        # The dialog search update mirrors the operator-owned query into
        # scene settings, so pagination must read the same persisted query.
        # `active_query` only exists inside _draw_browser and was causing a
        # NameError when the Next Page operator executed.
        records = filtered_units(s)
        pages = max(1, (len(records) + s.browser_page_size - 1) // s.browser_page_size)
        s.browser_page = min(pages - 1, s.browser_page + 1)
        return {"FINISHED"}


class SANCTUARY_OT_browser_reset_filters(Operator):
    bl_idname = "sanctuary.browser_reset_filters"
    bl_label = "Reset Filters"

    def execute(self, context):
        s = context.scene.sanctuary_toolkit
        s.browser_search = ""
        for name in (
            "browser_faction_chosen", "browser_faction_guard", "browser_faction_eda", "browser_faction_other",
            "browser_tech_commander", "browser_tech1", "browser_tech2", "browser_tech3", "browser_tech4", "browser_tech_other",
            "browser_domain_land", "browser_domain_air", "browser_domain_navy", "browser_domain_structure", "browser_domain_other",
        ):
            setattr(s, name, True)
        s.browser_page = 0
        return {"FINISHED"}


def _draw_browser(layout, context, operator=None):
    # Deliberately mirrors the original v1.1 operator-dialog layout. The user
    # preferred this compact card browser over a duplicated Properties editor.
    s = context.scene.sanctuary_toolkit
    header = layout.row(align=True)
    header.label(text="SANCTUARY UNIT DATABASE", icon="VIEWZOOM")
    if operator is not None:
        header.prop(operator, "search_query", text="", icon="VIEWZOOM")
        active_query = operator.search_query
    else:
        header.prop(s, "browser_search", text="", icon="VIEWZOOM")
        active_query = s.browser_search
    header.operator("sanctuary.build_unit_database", text="", icon="FILE_REFRESH")
    header.operator("sanctuary.browser_reset_filters", text="", icon="LOOP_BACK")

    split = layout.split(factor=0.19)
    filters = split.column()
    content = split.column()

    fbox = filters.box(); fbox.label(text="FACTIONS")
    fbox.prop(s, "browser_faction_chosen", text="Chosen")
    fbox.prop(s, "browser_faction_guard", text="Guard")
    fbox.prop(s, "browser_faction_eda", text="EDA")
    fbox.prop(s, "browser_faction_other", text="Other")

    tbox = filters.box(); tbox.label(text="TECH LEVEL")
    tbox.prop(s, "browser_tech_commander", text="Commander")
    tbox.prop(s, "browser_tech1", text="T1")
    tbox.prop(s, "browser_tech2", text="T2")
    tbox.prop(s, "browser_tech3", text="T3")
    tbox.prop(s, "browser_tech4", text="T4 / Experimental")
    tbox.prop(s, "browser_tech_other", text="Other")

    dbox = filters.box(); dbox.label(text="UNIT TYPE")
    dbox.prop(s, "browser_domain_land", text="Land")
    dbox.prop(s, "browser_domain_air", text="Air")
    dbox.prop(s, "browser_domain_navy", text="Navy")
    dbox.prop(s, "browser_domain_structure", text="Structure")
    dbox.prop(s, "browser_domain_other", text="Other")

    ibox = filters.box(); ibox.label(text="IMPORT")
    ibox.prop(s, "browser_import_skeleton")
    ibox.prop(s, "browser_import_textures")
    ibox.prop(s, "browser_import_animations")
    selected = _DATABASE_BY_ID.get(s.browser_selected_unit)
    if selected:
        ibox.separator()
        ibox.label(text=selected.get("display_name", selected["unit_id"]))
        ibox.label(text=selected["unit_id"].upper())
        ibox.label(text=f"{selected.get('faction','')} • {selected.get('tech_level','')}")
        ibox.operator("sanctuary.import_database_unit", text="Import Selected", icon="IMPORT")
    else:
        ibox.label(text="Select a unit card.", icon="INFO")
    filters.label(text="Double-click a unit name to import.", icon="MOUSE_LMB")

    records = filtered_units(s)
    page_size = max(8, int(s.browser_page_size))
    pages = max(1, (len(records) + page_size - 1) // page_size)
    if s.browser_page >= pages:
        s.browser_page = pages - 1
    start = s.browser_page * page_size
    page_records = records[start:start + page_size]

    top = content.row(align=True)
    top.label(text=f"{len(records)} unit{'s' if len(records) != 1 else ''}")
    nav = top.row(align=True); nav.alignment = "RIGHT"
    prev = nav.row(align=True); prev.enabled = s.browser_page > 0
    prev.operator("sanctuary.browser_prev_page", text="", icon="TRIA_LEFT")
    nav.label(text=f"Page {s.browser_page + 1} / {pages}")
    nxt = nav.row(align=True); nxt.enabled = s.browser_page < pages - 1
    nxt.operator("sanctuary.browser_next_page", text="", icon="TRIA_RIGHT")

    if not page_records:
        content.box().label(text="No units match these filters.", icon="QUESTION")
        return

    # Fixed four-card grid reproduces the clean original browser; at the common
    # dialog width this appears as the same large 3-4 card presentation.
    grid = content.grid_flow(row_major=True, columns=4, even_columns=True, even_rows=False, align=True)
    for record in page_records:
        card = grid.box()
        hdr = card.row(align=True)
        hdr.label(text=record["unit_id"].upper())
        hdr.label(text=record.get("tech_level", ""))
        card.template_icon(icon_value=icon_id_for_record(record), scale=4.0)
        name_row = card.row(align=True)
        op = name_row.operator(
            "sanctuary.browser_click",
            text=record.get("display_name") or record["unit_id"],
            depress=s.browser_selected_unit == record["unit_id"],
        )
        op.unit_id = record["unit_id"]
        location = name_row.operator_menu_enum(
            "sanctuary.open_unit_location", "destination", text="", icon="FILE_FOLDER"
        )
        location.unit_id = record["unit_id"]
        meta = card.row(align=True)
        meta.label(text=record.get("faction", ""))
        meta.label(text=record.get("domain", "").title())




def _browser_operator_search_update(self, context):
    """Keep the modal dialog's live text field and scene paging in sync."""
    try:
        s = context.scene.sanctuary_toolkit
        s.browser_search = self.search_query
        s.browser_page = 0
        if context.area:
            context.area.tag_redraw()
    except Exception:
        pass

class SANCTUARY_OT_open_unit_browser(Operator):
    bl_idname = "sanctuary.open_unit_browser"
    bl_label = "Sanctuary Unit Browser v1.4.7"
    bl_description = "Open the clean visual Sanctuary unit browser"

    search_query: bpy.props.StringProperty(
        name="Search",
        default="",
        update=_browser_operator_search_update,
    )

    def invoke(self, context, event):
        prefs = get_addon_preferences(context)
        engine = resolve_engine_root(prefs.sanctuary_root if prefs else "")
        if not engine:
            self.report({"ERROR"}, "Choose a valid Sanctuary game folder first")
            return {"CANCELLED"}
        try:
            # Use an operator-owned field for the modal dialog so what is typed
            # is exactly what the same dialog uses to draw/filter its cards.
            self.search_query = context.scene.sanctuary_toolkit.browser_search
            if database_needs_upgrade():
                build_database(engine)
            else:
                load_database()
        except Exception as exc:
            self.report({"ERROR"}, f"Could not index Sanctuary units: {exc}")
            return {"CANCELLED"}
        return context.window_manager.invoke_props_dialog(self, width=1050)

    def execute(self, context):
        return {"FINISHED"}

    def draw(self, context):
        _draw_browser(self.layout, context, self)


CLASSES = (
    SANCTUARY_OT_build_unit_database,
    SANCTUARY_OT_import_database_unit,
    SANCTUARY_OT_browser_click,
    SANCTUARY_OT_open_unit_location,
    SANCTUARY_OT_extract_units_for_browsing,
    SANCTUARY_OT_browser_prev_page,
    SANCTUARY_OT_browser_next_page,
    SANCTUARY_OT_browser_reset_filters,
    SANCTUARY_OT_open_unit_browser,
)


def register():
    load_database()
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    clear_previews()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
