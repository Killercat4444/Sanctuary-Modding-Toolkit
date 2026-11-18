"""Safe, incremental extraction of browsing copies; never overwrite a file."""

import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
import zipfile


def _check_no_links(path, root):
    current = Path(path)
    while True:
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError(f"Refusing a symlink/junction in the extraction path: {current}")
        if current == root:
            break
        if current.parent == current:
            raise ValueError("Extraction path is outside the target folder")
        current = current.parent


def _target_for_member(info, gamedata):
    name = info.filename.replace("\\", "/")
    raw_parts = name.rstrip("/").split("/")
    if not raw_parts or raw_parts[0].lower() != "units":
        raise ValueError(f"Unexpected archive root: {name!r}; expected Units/")
    if any(not part or part in {".", ".."} or part[-1:] in {".", " "}
           or any(char in part for char in ':<>"|?*\x00')
           or any(ord(char) < 32 for char in part)
           or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
           for part in raw_parts):
        raise ValueError(f"Unsafe archive path: {name!r}")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError(f"Archive links are not supported: {name!r}")
    root = Path(gamedata).resolve()
    target = root.joinpath("Units", *raw_parts[1:])
    _check_no_links(target, root)
    if not target.resolve().is_relative_to(root / "Units"):
        raise ValueError(f"Archive path escapes Units/: {name!r}")
    return target


def _plan(archive, gamedata):
    result, seen = [], set()
    for info in archive.infolist():
        target = _target_for_member(info, gamedata)
        key = str(target).casefold()
        if key in seen:
            raise ValueError(f"Duplicate archive destination: {info.filename}")
        seen.add(key)
        if info.is_dir():
            continue
        if info.flag_bits & 1:
            raise ValueError("Encrypted Units archives are not supported")
        if target.exists() and not target.is_file():
            raise ValueError(f"A folder occupies a required file path: {target}")
        result.append((info, target))
    if not result:
        raise ValueError("The archive contains no unit files")
    return result


def extraction_summary(pack):
    pack = Path(pack).resolve()
    with zipfile.ZipFile(pack) as archive:
        plan = _plan(archive, pack.parent)
        return {"files": len(plan), "bytes": sum(info.file_size for info, _ in plan),
                "missing_bytes": sum(info.file_size for info, target in plan if not target.exists())}


def extract_units_incrementally(pack):
    """Yield (finished bytes, total bytes); close() cancels safely between chunks.

    Completed new files are retained on cancellation; a retry skips them. A
    private temporary file is published atomically with an exclusive hard link
    only after its ZIP CRC has been checked. Preexisting files are never replaced.
    """
    pack = Path(pack).resolve()
    created = skipped = finished = 0
    with zipfile.ZipFile(pack) as archive:
        plan = _plan(archive, pack.parent)
        total = sum(info.file_size for info, _ in plan)
        needed = sum(info.file_size for info, target in plan if not target.exists())
        if shutil.disk_usage(pack.parent).free < needed + 16 * 1024 * 1024:
            raise OSError("Not enough free space to extract the Units browsing folder")
        for info, target in plan:
            _check_no_links(target, pack.parent)
            if target.exists():
                skipped += 1
                finished += info.file_size
                yield finished, total
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            _check_no_links(target.parent, pack.parent)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(prefix=".sanctuary-extract-", suffix=".part", dir=target.parent, delete=False) as output:
                    temporary = Path(output.name)
                    with archive.open(info) as source:
                        while True:
                            block = source.read(4 * 1024 * 1024)
                            if not block:
                                break
                            output.write(block)
                            finished += len(block)
                            yield finished, total
                _check_no_links(target, pack.parent)
                try:
                    os.link(temporary, target)
                    created += 1
                except FileExistsError:
                    skipped += 1
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    return {"created": created, "skipped": skipped, "bytes": finished}
