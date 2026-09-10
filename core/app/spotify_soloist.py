import json
import os
import subprocess
import platform
import tarfile
import tempfile
import threading
import urllib.request
from pathlib import Path


_PROC = None
_LOCK = threading.RLock()
DOWNLOADS = {
    "x86_64": "https://soloist-builds.spotifycdn.com/soloist_release_x86_64.tar.gz",
    "aarch64": "https://soloist-builds.spotifycdn.com/soloist_release_arm64.tar.gz",
    "armv7l": "https://soloist-builds.spotifycdn.com/soloist_release_arm32.tar.gz",
}

def install_latest(config=None):
    cfg=config or {}
    data_dir=Path(cfg.get("data_dir") or "/data/spotify")
    target=data_dir / "bin" / "soloist"
    target.parent.mkdir(parents=True, exist_ok=True)
    arch=platform.machine().lower()
    url=DOWNLOADS.get(arch)
    if not url:
        raise RuntimeError(f"Unsupported Soloist architecture: {arch}")
    with tempfile.TemporaryDirectory(prefix="soloist-") as td:
        archive=Path(td) / "soloist.tar.gz"
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive, "r:gz") as tf:
            member=next((m for m in tf.getmembers() if Path(m.name).name == "soloist" and m.isfile()), None)
            if not member:
                raise RuntimeError("Spotify archive did not contain soloist")
            src=tf.extractfile(member)
            if src is None:
                raise RuntimeError("Could not extract Spotify Soloist")
            tmp=target.with_suffix(".new")
            tmp.write_bytes(src.read())
            tmp.chmod(0o755)
            tmp.replace(target)
    return str(target)

def stop_daemon():
    global _PROC
    with _LOCK:
        proc=_PROC; _PROC=None
    if proc and proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=4)
        except subprocess.TimeoutExpired: proc.kill()

def start_daemon(config):
    global _PROC
    exe=binary(config)
    if not exe: raise RuntimeError("Spotify Soloist binary is not installed")
    key=(config.get("api_key") or "").strip()
    if not key: raise RuntimeError("Spotify Soloist API key is not configured")
    data_dir=Path(config.get("data_dir") or "/data/spotify")
    cache_dir=data_dir / "cache"
    data_dir.mkdir(parents=True, exist_ok=True); cache_dir.mkdir(parents=True, exist_ok=True)
    stop_daemon()
    log=open(data_dir / "soloist.log", "ab", buffering=0)
    args=[exe, "--device-name", config.get("device_name") or "SurroundCore Spotify",
          "--api-key", key, "--data-dir", str(data_dir), "--cache-dir", str(cache_dir),
          "--ws", config.get("ws") or "127.0.0.1:9090"]
    with _LOCK:
        _PROC=subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT)
        pid=_PROC.pid
    return {"ok": True, "pid": pid, "device_name": config.get("device_name") or "SurroundCore Spotify"}

def binary(config=None):
    cfg=config or {}
    path=(cfg.get('binary') or os.getenv('SURROUNDCORE_SPOTIFY_SOLOIST','') or str(Path(cfg.get('data_dir') or '/data/spotify')/'bin'/'soloist')).strip()
    if path and os.path.isfile(path) and os.access(path,os.X_OK):
        return path
    return None


def redacted(config):
    config=config or {}
    return {
        'configured': bool(config.get('api_key')),
        'binary_available': bool(binary(config)),
        'binary': config.get('binary',''),
        'device_name': config.get('device_name','SurroundCore Spotify'),
        'ws': config.get('ws','127.0.0.1:9090'),
        'data_dir': config.get('data_dir','/data/spotify'),
        'quality': 'Spotify highest available; Premium lossless up to 24-bit/44.1 kHz',
        'setup_url': 'https://developer.spotify.com/documentation/soloist',
        'downloads_url': 'https://developer.spotify.com/documentation/soloist/reference/downloads-and-updates',
    }


def _ctl(config,*args,json_output=False):
    exe=binary(config)
    if not exe: raise RuntimeError('Spotify Soloist binary is not installed')
    cmd=[exe,'ctl','-w',config.get('ws','127.0.0.1:9090')]
    if json_output: cmd.append('--json')
    cmd += list(args)
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=12)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'Spotify Soloist control failed').strip())
    text=p.stdout.strip()
    return json.loads(text) if json_output and text else {'ok':True,'output':text}


def status(config):
    return _ctl(config,'status')


def now(config):
    return _ctl(config,'now',json_output=True)


def queue(config,limit=20):
    return _ctl(config,'queue',str(max(1,min(int(limit),80))),json_output=True)


def play(config,uri=None):
    args=['play'] + ([uri] if uri else [])
    return _ctl(config,*args)


def pause(config): return _ctl(config,'pause')
def next_track(config): return _ctl(config,'next')
def previous(config): return _ctl(config,'prev')
def activate(config): return _ctl(config,'activate')
def deactivate(config): return _ctl(config,'deactivate')

def volume(config,value):
    return _ctl(config,'volume',str(max(0,min(int(value),100))))
