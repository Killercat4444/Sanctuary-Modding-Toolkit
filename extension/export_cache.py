"""Content-addressed deterministic packs; every cache hit validates its contents."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile
import zlib

PACK_FORMAT = 'Studio-pack-v3-fixed-metadata'
STAMP = (1980, 1, 1, 0, 0, 0)


def _workers():
    return min(8, os.cpu_count() or 1)


def _digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest().upper()


def _source_hashes(project, files):
    def one(file):
        return file.relative_to(project).as_posix(), _digest(file)
    with concurrent.futures.ThreadPoolExecutor(max_workers=_workers()) as pool:
        return dict(pool.map(one, files))


def _fingerprint(project, files, source_hashes=None, compression_level=1, exporter_version=''):
    hashes = source_hashes if source_hashes is not None else _source_hashes(project, files)
    options = [PACK_FORMAT, compression_level, exporter_version, _digest(Path(__file__)),
               list(sys.version_info[:3]), zlib.ZLIB_RUNTIME_VERSION,
               [(file.relative_to(project).as_posix(), hashes[file.relative_to(project).as_posix()].upper()) for file in files]]
    return hashlib.sha256(json.dumps(options, separators=(',', ':')).encode()).hexdigest()


def write_member(archive, file, relative, compression=zipfile.ZIP_DEFLATED, compression_level=1):
    """Stable ZIP metadata; stream file bytes instead of allocating whole assets."""
    info = zipfile.ZipInfo(relative, STAMP)
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    info.compress_type = compression
    info._compresslevel = compression_level
    info.file_size = file.stat().st_size
    with file.open('rb') as source, archive.open(info, 'w', force_zip64=info.file_size >= zipfile.ZIP64_LIMIT) as target:
        shutil.copyfileobj(source, target, 1024 * 1024)


def _validate_pack(pack, project, files, hashes):
    """Rehash decompressed members against fresh source hashes, including CRCs."""
    expected = [file.relative_to(project).as_posix() for file in files]
    with zipfile.ZipFile(pack) as archive:
        if archive.namelist() != expected:
            return False
        def check(name):
            info = archive.getinfo(name)
            if info.date_time != STAMP or info.compress_type != zipfile.ZIP_DEFLATED or info.external_attr != 0o100644 << 16:
                return False
            digest = hashlib.sha256()
            with archive.open(info) as source:
                for block in iter(lambda: source.read(1024 * 1024), b''):
                    digest.update(block)
            return digest.hexdigest().upper() == hashes[name].upper()
        with concurrent.futures.ThreadPoolExecutor(max_workers=_workers()) as pool:
            return all(pool.map(check, expected))


def pack_assets(project, files, output, namespace='art', *, source_hashes=None,
                compression_level=1, exporter_version='', report=None):
    """Optional fresh snapshot avoids duplicate reads; caller still rehashes sources."""
    project = Path(project).resolve(); output = Path(output); files = list(files)
    if not 0 <= compression_level <= 9:
        raise ValueError('Invalid ZIP compression level')
    if any(project not in file.resolve().parents for file in files):
        raise ValueError('Pack file escapes project root.')
    hashes = source_hashes if source_hashes is not None else _source_hashes(project, files)
    setting = os.environ.get('SANCTUARY_STUDIO_EXPORT_CACHE')
    if setting:
        root = Path(setting)
    else:
        from .utils import cache_root
        root = cache_root() / 'export-packs'
    root = root / hashlib.sha256(str(project).casefold().encode()).hexdigest()[:20]
    root.mkdir(parents=True, exist_ok=True)
    name = hashlib.sha256(namespace.encode()).hexdigest()[:16]
    pack = root / (name + '.sanpack'); manifest = root / (name + '.json')
    key = _fingerprint(project, files, hashes, compression_level, exporter_version)
    cached = False
    try:
        record = json.loads(manifest.read_text())
        if record['key'] == key and record['size'] == pack.stat().st_size and _validate_pack(pack, project, files, hashes):
            shutil.copy2(pack, output)
            # Bind the copied container too; do not trust metadata or a cache stat.
            digest = _digest(output)
            cached = digest == record['sha256']
    except (OSError, ValueError, KeyError, TypeError, zipfile.BadZipFile, zlib.error):
        pass
    if not cached:
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=compression_level, allowZip64=True) as archive:
            for file in files:
                write_member(archive, file, file.relative_to(project).as_posix(), compression_level=compression_level)
        if not _validate_pack(output, project, files, hashes):
            raise ValueError('Project artwork changed while packaging, or pack validation failed.')
        digest = _digest(output)
        with tempfile.TemporaryDirectory(prefix='pack-', dir=root) as temp:
            candidate = Path(temp) / 'pack'; meta = Path(temp) / 'manifest'
            shutil.copy2(output, candidate)
            meta.write_text(json.dumps({'key': key, 'size': candidate.stat().st_size, 'sha256': digest}), encoding='utf-8')
            os.replace(candidate, pack); os.replace(meta, manifest)
    if source_hashes is None and _source_hashes(project, files) != hashes:
        raise ValueError('Project artwork changed while packaging.')
    if report is not None:
        report.update(sha256=digest, cache_hit=cached, key=key)
    return len(files)
