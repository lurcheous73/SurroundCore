import io, json, os, shutil, sqlite3, tarfile, tempfile, time
from pathlib import Path

DATA = Path(os.getenv('SURROUNDCORE_DATA', '/data'))
DB = DATA / 'surroundcore.sqlite3'
STATE_FILES = ('streaming-settings.json', 'provider-secrets.json')
STATE_DIRS = ('artwork', 'artwork-overrides')
BUNDLE_VERSION = 1


def _sqlite_snapshot(dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(DB))
    out = sqlite3.connect(str(dst))
    try:
        src.backup(out)
    finally:
        out.close(); src.close()


def build_bundle():
    with tempfile.TemporaryDirectory(prefix='surroundcore-repl-') as raw:
        root = Path(raw)
        _sqlite_snapshot(root / 'surroundcore.sqlite3')
        manifest = {'bundle_version': BUNDLE_VERSION, 'created_at': time.time()}
        (root / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        for name in STATE_FILES:
            src = DATA / name
            if src.is_file(): shutil.copy2(src, root / name)
        for name in STATE_DIRS:
            src = DATA / name
            if src.is_dir(): shutil.copytree(src, root / name)
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w:gz') as tf:
            for item in root.iterdir(): tf.add(item, arcname=item.name, recursive=True)
        return buffer.getvalue()


def _safe_extract(tf: tarfile.TarFile, root: Path):
    base = root.resolve()
    for member in tf.getmembers():
        target = (root / member.name).resolve()
        if base != target and base not in target.parents:
            raise ValueError('unsafe recovery bundle path')
    tf.extractall(root)


def apply_bundle(payload: bytes):
    if not payload or len(payload) > 512 * 1024 * 1024:
        raise ValueError('invalid recovery bundle')
    with tempfile.TemporaryDirectory(prefix='surroundcore-apply-') as raw:
        root = Path(raw)
        with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as tf:
            _safe_extract(tf, root)
        manifest = json.loads((root / 'manifest.json').read_text())
        if int(manifest.get('bundle_version') or 0) != BUNDLE_VERSION:
            raise ValueError('unsupported recovery bundle version')
        source_db = root / 'surroundcore.sqlite3'
        if not source_db.is_file(): raise ValueError('recovery database missing')
        src = sqlite3.connect(str(source_db))
        dst = sqlite3.connect(str(DB))
        try:
            src.backup(dst)
        finally:
            dst.close(); src.close()
        DATA.mkdir(parents=True, exist_ok=True)
        for name in STATE_FILES:
            src_file = root / name
            if src_file.is_file():
                tmp = DATA / (name + '.replica.tmp')
                shutil.copy2(src_file, tmp); os.chmod(tmp, 0o600); tmp.replace(DATA / name)
        for name in STATE_DIRS:
            src_dir = root / name
            if src_dir.is_dir():
                dst_dir = DATA / name
                tmp_dir = DATA / (name + '.replica.tmp')
                if tmp_dir.exists(): shutil.rmtree(tmp_dir)
                shutil.copytree(src_dir, tmp_dir)
                if dst_dir.exists(): shutil.rmtree(dst_dir)
                tmp_dir.replace(dst_dir)
        return {'ok': True, 'bundle_version': BUNDLE_VERSION,
                'created_at': manifest.get('created_at'), 'applied_at': time.time()}
