import hashlib
import os
import subprocess
from pathlib import Path

COVER_NAMES = ('cover.jpg','cover.jpeg','cover.png','folder.jpg','folder.jpeg','folder.png','front.jpg','front.jpeg','front.png')


def _cache_path(item):
    root = Path(os.getenv('SURROUNDCORE_DATA','/data')) / 'artwork'
    root.mkdir(parents=True, exist_ok=True)
    path = Path(str(item.get('path') or ''))
    try: stamp = path.stat().st_mtime_ns
    except OSError: stamp = 0
    key = hashlib.sha1(f'{path}|{stamp}'.encode()).hexdigest()
    return root / f'{key}.jpg'


def cover_bytes(item):
    source = Path(str(item.get('path') or ''))
    if not source.is_file():
        return None
    cached = _cache_path(item)
    if cached.is_file() and cached.stat().st_size > 100:
        return cached.read_bytes()
    parent = source.parent
    by_lower = {p.name.lower(): p for p in parent.iterdir() if p.is_file()}
    for name in COVER_NAMES:
        candidate = by_lower.get(name)
        if candidate:
            data = candidate.read_bytes()
            if candidate.suffix.lower() in ('.jpg','.jpeg'):
                cached.write_bytes(data); return data
            proc = subprocess.run(['ffmpeg','-v','error','-i',str(candidate),'-frames:v','1','-f','image2pipe','-vcodec','mjpeg','pipe:1'],capture_output=True,timeout=8)
            if proc.returncode == 0 and len(proc.stdout) > 100:
                cached.write_bytes(proc.stdout); return proc.stdout
    proc = subprocess.run(['ffmpeg','-v','error','-i',str(source),'-map','0:v:0','-frames:v','1','-f','image2pipe','-vcodec','mjpeg','pipe:1'],capture_output=True,timeout=10)
    if proc.returncode == 0 and len(proc.stdout) > 100:
        cached.write_bytes(proc.stdout); return proc.stdout
    return None
