import fcntl
import hashlib
import mimetypes
import os
import json
import shutil
from pathlib import Path
import subprocess
import tempfile
import threading
from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse

CHUNK = 1024 * 1024
_RENDER_SEMAPHORE = threading.Semaphore(1)


def ranged_file(path, range_header=None, filename=None):
    size = os.path.getsize(path)
    media_type = mimetypes.guess_type(path)[0] or 'application/octet-stream'
    headers = {'Accept-Ranges': 'bytes'}
    if not range_header:
        return FileResponse(path, media_type=media_type, filename=filename, headers=headers)
    if not range_header.startswith('bytes=') or ',' in range_header:
        raise HTTPException(416, 'Only a single byte range is supported')
    spec = range_header[6:]
    try:
        first, last = spec.split('-', 1)
        if first:
            start = int(first)
            end = int(last) if last else size - 1
        else:
            suffix = int(last)
            start = max(0, size - suffix)
            end = size - 1
    except (ValueError, TypeError):
        raise HTTPException(416, 'Invalid byte range')
    if start < 0 or start >= size or end < start:
        raise HTTPException(416, 'Range outside media')
    end = min(end, size - 1)
    length = end - start + 1

    def body():
        remaining = length
        with open(path, 'rb') as fh:
            fh.seek(start)
            while remaining:
                data = fh.read(min(CHUNK, remaining))
                if not data:
                    break
                remaining -= len(data)
                yield data

    headers.update({
        'Content-Range': f'bytes {start}-{end}/{size}',
        'Content-Length': str(length),
    })
    return StreamingResponse(body(), status_code=206, media_type=media_type, headers=headers)


def _render_cache_key(path):
    stat = os.stat(path)
    raw = f'{os.path.realpath(path)}\0{stat.st_size}\0{stat.st_mtime_ns}'.encode()
    return hashlib.sha256(raw).hexdigest()


def stereo_flac_cache(path):
    data_root = os.getenv('SURROUNDCORE_DATA', '/data')
    cache_dir = os.path.join(data_root, 'render-cache', 'stereo-48k-s16-flac')
    os.makedirs(cache_dir, exist_ok=True)
    key = _render_cache_key(path)
    output = os.path.join(cache_dir, key + '.flac')
    lock_path = output + '.lock'
    if os.path.isfile(output) and os.path.getsize(output) > 0:
        return output

    with _RENDER_SEMAPHORE:
        with open(lock_path, 'a+b') as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            if os.path.isfile(output) and os.path.getsize(output) > 0:
                return output
            fd, temp_path = tempfile.mkstemp(prefix=key + '.', suffix='.flac.tmp', dir=cache_dir)
            os.close(fd)
            try:
                subprocess.run([
                    'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-i', path, '-map', '0:a:0', '-vn', '-ac', '2', '-ar', '48000',
                    '-sample_fmt', 's16', '-c:a', 'flac', '-compression_level', '3', '-f', 'flac',
                    temp_path,
                ], check=True)
                os.replace(temp_path, output)
            finally:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
    return output


def stereo_flac_program_cache(paths):
    if not paths:
        raise ValueError('Programme has no media')
    rendered = [stereo_flac_cache(path) for path in paths]
    key = hashlib.sha256(('\0'.join(rendered)).encode()).hexdigest()
    data_root = os.getenv('SURROUNDCORE_DATA', '/data')
    cache_dir = os.path.join(data_root, 'render-cache', 'programmes-48k-s16-flac')
    os.makedirs(cache_dir, exist_ok=True)
    output = os.path.join(cache_dir, key + '.flac')
    if os.path.isfile(output) and os.path.getsize(output) > 0:
        return output
    with _RENDER_SEMAPHORE:
        if os.path.isfile(output) and os.path.getsize(output) > 0:
            return output
        manifest = tempfile.NamedTemporaryFile('w', suffix='.concat', delete=False, dir=cache_dir)
        try:
            for item in rendered:
                manifest.write("file '" + item.replace("'", "'\\''") + "'\n")
            manifest.close()
            fd, temp_path = tempfile.mkstemp(prefix=key + '.', suffix='.flac.tmp', dir=cache_dir)
            os.close(fd)
            try:
                subprocess.run([
                    'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                    '-f', 'concat', '-safe', '0', '-i', manifest.name, '-map', '0:a:0', '-vn',
                    '-ac', '2', '-ar', '48000', '-sample_fmt', 's16', '-c:a', 'flac',
                    '-compression_level', '3', '-f', 'flac', temp_path,
                ], check=True)
                os.replace(temp_path, output)
            finally:
                if os.path.exists(temp_path): os.unlink(temp_path)
        finally:
            try: os.unlink(manifest.name)
            except OSError: pass
    return output


# Streaming service configuration lives on the Core, never in ControlMac.
DATA_DIR = Path(os.getenv('SURROUNDCORE_DATA', '/data'))
SETTINGS_PATH = DATA_DIR / 'streaming-settings.json'
DEFAULT_SETTINGS = {
    'source_quality': 'highest_native',
    'prefer_lossless': True,
    'prefer_airia': True,
    'allow_mqa_passthrough': True,
    'output_transport': 'auto',
    'prefer_mhr': True,
    'prefer_mmhr': True,
    'allow_downsample': False,
    'allow_downmix': False,
    'providers': {},
    'radio_stations': [],
    'podcast_feeds': [],
}


def airia_available():
    configured = os.getenv('SURROUNDCORE_AIRIA_DECODER', '').strip()
    return bool(configured and (Path(configured).is_file() or shutil.which(configured)))


def load_settings():
    data = dict(DEFAULT_SETTINGS)
    try:
        stored = json.loads(SETTINGS_PATH.read_text())
        if isinstance(stored, dict):
            data.update(stored)
    except (OSError, ValueError, TypeError):
        pass
    data['airia_available'] = airia_available()
    return data


def save_settings(update):
    current = load_settings()
    current.pop('airia_available', None)
    for key in DEFAULT_SETTINGS:
        if key in update:
            current[key] = update[key]
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(current, indent=2, sort_keys=True) + '\n')
    os.chmod(tmp, 0o600)
    tmp.replace(SETTINGS_PATH)
    return load_settings()


PROVIDER_SECRETS_PATH = DATA_DIR / 'provider-secrets.json'


def load_provider_secrets():
    try:
        stored = json.loads(PROVIDER_SECRETS_PATH.read_text())
        return stored if isinstance(stored, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_provider_secret(provider_id, secret):
    secrets = load_provider_secrets()
    if secret is None:
        secrets.pop(provider_id, None)
    else:
        secrets[provider_id] = secret
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = PROVIDER_SECRETS_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(secrets, indent=2, sort_keys=True) + '\n')
    os.chmod(tmp, 0o600)
    tmp.replace(PROVIDER_SECRETS_PATH)
    return bool(secret)


def provider_secret_configured(provider_id):
    return provider_id in load_provider_secrets()
