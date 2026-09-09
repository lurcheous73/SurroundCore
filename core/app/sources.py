import fcntl
import hashlib
import os
import json
import re
import shutil
from pathlib import Path

from .db import get_source, list_sources

SOURCE_KINDS = ('local', 'usb', 'nfs', 'cifs', 'plex', 'rclone')
CACHE_POLICIES = ('off', 'metadata', 'read-through', 'pin')
CACHE_ROOT = Path(os.getenv('SURROUNDCORE_SOURCE_CACHE', '/cache'))
CACHE_MAX_GB = float(os.getenv('SURROUNDCORE_CACHE_MAX_GB', '0') or 0)
CACHE_MIN_FREE_GB = float(os.getenv('SURROUNDCORE_CACHE_MIN_FREE_GB', '5') or 5)
SOURCE_ID_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')


def validate_source(item):
    source_id = item.get('id', '')
    if not SOURCE_ID_RE.fullmatch(source_id):
        raise ValueError('Source id may contain only letters, numbers, dot, underscore and hyphen')
    kind = item.get('kind')
    policy = item.get('cache_policy', 'off')
    if kind not in SOURCE_KINDS:
        raise ValueError(f'Unsupported source kind: {kind}')
    if policy not in CACHE_POLICIES:
        raise ValueError(f'Unsupported cache policy: {policy}')
    if kind != 'plex':
        path = item.get('path') or f"/sources/{item['id']}"
        pp=Path(path)
        if not path.startswith('/sources/') or '..' in pp.parts:
            raise ValueError('Mounted sources must live below /sources')
        item['path'] = path.rstrip('/')
    return item


def source_status(source):
    if source['kind'] == 'plex':
        return {'available': True, 'mounted': False}
    path = Path(source.get('path') or '')
    return {'available': path.is_dir(), 'mounted': path.is_mount() if path.exists() else False}


def media_path(item):
    source_id = item.get('source_id')
    if not source_id:
        return item['path']
    source = get_source(source_id)
    if not source or not source.get('enabled', True):
        return item['path']
    if source.get('cache_policy') not in ('read-through', 'pin'):
        return item['path']
    return cache_file(item['path'], source_id)


def _cache_paths(path, source_id):
    src = Path(path)
    key = hashlib.sha256(str(src).encode()).hexdigest()
    root = CACHE_ROOT / source_id
    suffix = src.suffix.lower()
    return src, root, root / f'{key}{suffix}', root / f'{key}.meta.json', root / f'{key}.lock'


def cache_file(path, source_id):
    src, root, dst, meta_path, lock_path = _cache_paths(path, source_id)
    root.mkdir(parents=True, exist_ok=True)
    try:
        stat = src.stat()
    except OSError:
        if dst.is_file():
            os.utime(dst, None)
            return str(dst)
        raise
    expected = {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
    try:
        cached_meta = json.loads(meta_path.read_text())
    except Exception:
        cached_meta = {}
    if dst.is_file() and dst.stat().st_size == stat.st_size and cached_meta == expected:
        os.utime(dst, None)
        return str(dst)
    with lock_path.open('a+b') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            cached_meta = json.loads(meta_path.read_text())
        except Exception:
            cached_meta = {}
        if dst.is_file() and dst.stat().st_size == stat.st_size and cached_meta == expected:
            os.utime(dst, None)
            return str(dst)
        tmp = root / f'.{dst.name}.{os.getpid()}.tmp'
        meta_tmp = root / f'.{meta_path.name}.{os.getpid()}.tmp'
        try:
            shutil.copyfile(src, tmp)
            meta_tmp.write_text(json.dumps(expected))
            os.replace(tmp, dst)
            os.replace(meta_tmp, meta_path)
            enforce_cache_budget(preserve=dst)
        finally:
            tmp.unlink(missing_ok=True)
            meta_tmp.unlink(missing_ok=True)
    return str(dst)

def _meta_for(path):
    return path.parent / (path.name[:64] + '.meta.json')


def purge_source_cache(source_id):
    root = CACHE_ROOT / source_id
    if not root.exists():
        return 0
    media = [p for p in root.iterdir() if p.is_file() and not p.name.endswith(('.lock','.meta.json'))]
    for path in media:
        path.unlink(missing_ok=True); _meta_for(path).unlink(missing_ok=True)
    return len(media)


def cache_stats():
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    files=[p for p in CACHE_ROOT.rglob('*') if p.is_file() and not p.name.endswith(('.lock','.meta.json'))]
    usage=shutil.disk_usage(CACHE_ROOT)
    return {'files':len(files),'bytes':sum(p.stat().st_size for p in files),
            'free_bytes':usage.free,'max_gb':CACHE_MAX_GB,'min_free_gb':CACHE_MIN_FREE_GB}

def enforce_cache_budget(preserve=None):
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    source_map={s['id']:s for s in list_sources()}
    pinned={sid for sid,s in source_map.items() if s.get('cache_policy')=='pin'}

    # Enforce optional per-source limits first.
    for source_id, source in source_map.items():
        if source_id in pinned:
            continue
        limit_gb=float((source.get('config') or {}).get('cache_limit_gb') or 0)
        if limit_gb <= 0:
            continue
        root=CACHE_ROOT / source_id
        if not root.exists():
            continue
        entries=[]; total=0
        for path in root.iterdir():
            if not path.is_file() or path.name.endswith(('.lock','.meta.json')):
                continue
            stat=path.stat(); total += stat.st_size
            if path != preserve:
                entries.append((stat.st_mtime_ns,stat.st_size,path))
        limit=int(limit_gb * 1024**3)
        for _,size,path in sorted(entries):
            if total <= limit:
                break
            path.unlink(missing_ok=True); _meta_for(path).unlink(missing_ok=True); total -= size

    # Global budget and free-space reserve use LRU, never pinned media.
    entries=[]; total=0
    for path in CACHE_ROOT.rglob('*'):
        if not path.is_file() or path.name.endswith('.lock') or path.name.endswith('.meta.json'):
            continue
        stat=path.stat(); total += stat.st_size
        try: source_id=path.relative_to(CACHE_ROOT).parts[0]
        except Exception: source_id=''
        if source_id not in pinned and path != preserve:
            entries.append((stat.st_mtime_ns,stat.st_size,path))
    max_bytes=int(CACHE_MAX_GB * 1024**3) if CACHE_MAX_GB>0 else 0
    min_free=int(CACHE_MIN_FREE_GB * 1024**3)
    for _,size,path in sorted(entries):
        free=shutil.disk_usage(CACHE_ROOT).free
        if not (max_bytes>0 and total>max_bytes) and free>=min_free:
            break
        path.unlink(missing_ok=True); _meta_for(path).unlink(missing_ok=True); total -= size
