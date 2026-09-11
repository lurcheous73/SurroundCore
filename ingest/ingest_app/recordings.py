import json
import shutil
import tempfile
from pathlib import Path

from . import engine


def _track_meta(manifest, filename, index):
    for item in manifest.get('tracks') or []:
        if item.get('name') == filename or int(item.get('track') or 0) == index:
            return item
    return {'track': index, 'title': f'Track {index:02d}', 'artist': '', 'album': manifest.get('album') or 'Recording'}


def import_recording(staged_files, manifest):
    if not staged_files:
        raise RuntimeError('No recording files supplied')
    session = str(manifest.get('session') or '')
    album = engine.safe_name(manifest.get('album') or 'Recorded Session', 'Recorded Session')
    outdir = engine.album_dir(album, session)
    outputs = []
    try:
        for index, source in enumerate(staged_files, 1):
            source = Path(source)
            if source.suffix.lower() not in ('.flac', '.wav'):
                raise RuntimeError(f'Unsupported recording file: {source.name}')
            meta = _track_meta(manifest, source.name, index)
            track = int(meta.get('track') or index)
            title = engine.safe_name(meta.get('title') or f'Track {track:02d}', f'Track {track:02d}')
            artist = engine.safe_name(meta.get('artist') or '', '')
            album_name = engine.safe_name(meta.get('album') or album, album)
            target = outdir / f'{track:02d} {title}.flac'
            cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source), '-map', '0:a:0', '-vn']
            if source.suffix.lower() == '.flac':
                cmd += ['-c:a', 'copy']
            else:
                cmd += ['-c:a', 'flac', '-compression_level', '8']
            cmd += ['-metadata', f'track={track}', '-metadata', f'title={title}', '-metadata', f'artist={artist}', '-metadata', f'album={album_name}', str(target)]
            engine.run(cmd, check=True, timeout=None)
            streams = engine.ffprobe_streams(target)
            if not streams:
                raise RuntimeError(f'Imported recording failed verification: {target.name}')
            outputs.append({'path': str(target), 'verified': True, 'track': track, 'title': title, 'artist': artist, 'album': album_name, 'source': streams[0]})
        engine.write_manifest(outdir, {'source': 'endpoint_recording', 'session': session, 'source_type': manifest.get('source_type') or '', 'outputs': outputs})
        return {'kind': 'endpoint_recording', 'output_dir': str(outdir), 'outputs': outputs}
    except Exception:
        shutil.rmtree(outdir, ignore_errors=True)
        raise
