import json, os, subprocess
from pathlib import Path
from .models import Edition

AUDIO_EXT = {'.flac','.wav','.aiff','.aif','.m4a','.alac','.dsf','.dff','.mka','.mkv'}

def probe(path):
    p = subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries',
        'stream=codec_name,channels,channel_layout,sample_rate,bits_per_raw_sample,bits_per_sample:format=duration',
        '-of','json',str(path)], capture_output=True, text=True, check=True)
    j=json.loads(p.stdout); s=j['streams'][0]
    bits=s.get('bits_per_raw_sample') or s.get('bits_per_sample') or None
    return Edition(path=str(path), codec=s.get('codec_name','unknown'), channels=int(s.get('channels',2)),
        channel_layout=s.get('channel_layout','unknown'), sample_rate=int(s.get('sample_rate') or 0),
        bit_depth=int(bits) if bits and str(bits).isdigit() else None,
        duration=float(j.get('format',{}).get('duration') or 0)).dict()

def scan(root):
    root=Path(root); out=[]
    if not root.exists(): return out
    for path in root.rglob('*'):
        if path.is_file() and path.suffix.lower() in AUDIO_EXT:
            try: out.append(probe(path))
            except Exception as e: out.append({'path':str(path),'error':str(e)})
    return out
