import mimetypes
import os
import subprocess
from fastapi import HTTPException
from fastapi.responses import FileResponse, StreamingResponse

CHUNK = 1024 * 1024


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


def stereo_flac(path):
    cmd = [
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
        '-i', path, '-map', '0:a:0', '-vn', '-ac', '2', '-ar', '48000',
        '-sample_fmt', 's16', '-c:a', 'flac', '-compression_level', '3',
        '-f', 'flac', 'pipe:1',
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def body():
        try:
            while True:
                data = proc.stdout.read(256 * 1024)
                if not data:
                    break
                yield data
        finally:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()

    return StreamingResponse(body(), media_type='audio/flac', headers={
        'Cache-Control': 'no-store',
        'X-SurroundCore-Render': 'stereo-48k-flac',
    })
