import json, subprocess
from pathlib import Path
from .models import Edition

AUDIO_EXT = {
    '.flac','.wav','.aiff','.aif','.m4a','.alac','.aac','.mp3','.ogg','.opus',
    '.wma','.ape','.wv','.dsf','.dff','.mka','.mkv','.ac3','.eac3','.dts',
    '.mlp','.thd','.m2ts','.ts'
}
TAG_KEYS = {
    'artist','album','album_artist','albumartist','title','track','disc','date',
    'year','genre','composer','comment','copyright','publisher','label'
}

def clean_tags(tags):
    out = {}
    for key, value in (tags or {}).items():
        k = key.lower()
        if k in TAG_KEYS or k.startswith('musicbrainz_'):
            if k == 'albumartist': k = 'album_artist'
            out[k] = str(value)[:2048]
    return out

def probe(path):
    p = subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries',
        'stream=codec_name,profile,codec_tag_string,channels,channel_layout,sample_rate,bits_per_raw_sample,bits_per_sample,bit_rate:format=duration,bit_rate:format_tags',
        '-of','json',str(path)], capture_output=True, text=True, check=True)
    j=json.loads(p.stdout); s=j['streams'][0]; fmt=j.get('format',{})
    bits=s.get('bits_per_raw_sample') or s.get('bits_per_sample') or None
    meta=clean_tags(fmt.get('tags'))
    profile=str(s.get('profile') or '').strip()
    codec_tag=str(s.get('codec_tag_string') or '').strip()
    if profile: meta['codec_profile']=profile
    if codec_tag: meta['codec_tag']=codec_tag
    marker=' '.join((profile, str(meta.get('comment') or ''))).upper()
    codec=str(s.get('codec_name') or 'unknown').lower()
    if codec in ('truehd','eac3') and ('ATMOS' in marker or 'JOC' in marker): meta['immersive_audio']='Dolby Atmos'
    if codec in ('dts','dca') and ('DTS:X' in marker or 'DTS X' in marker): meta['immersive_audio']='DTS:X'
    return Edition(path=str(path), codec=s.get('codec_name','unknown'), channels=int(s.get('channels',2)),
        channel_layout=s.get('channel_layout','unknown'), sample_rate=int(s.get('sample_rate') or 0),
        bit_depth=int(bits) if bits and str(bits).isdigit() and int(bits) > 0 else None,
        bitrate=int(s.get('bit_rate') or fmt.get('bit_rate') or 0) or None,
        duration=float(fmt.get('duration') or 0), metadata=meta).dict()

def scan(root):
    root=Path(root); out=[]
    if not root.exists(): return out
    for path in root.rglob('*'):
        if path.is_file() and path.suffix.lower() in AUDIO_EXT:
            try: out.append(probe(path))
            except Exception as e: out.append({'path':str(path),'error':str(e)})
    return out
