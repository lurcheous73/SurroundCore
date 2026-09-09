import contextlib
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

CORE_URL = os.getenv('SURROUNDCORE_CORE_URL', 'http://127.0.0.1:8080').rstrip('/')
TOKEN = os.getenv('SURROUNDCORE_TOKEN', '')
INGEST_ROOT = Path(os.getenv('SURROUNDCORE_INGEST_ROOT', '/ingest'))
LIBRARY_ROOT = Path(os.getenv('SURROUNDCORE_INGEST_LIBRARY', '/library'))
MAKEMKV = os.getenv('SURROUNDCORE_MAKEMKVCON', '').strip()
NETMD_DIR = Path('/opt/surroundcore/netmd')


def run(args, cwd=None, check=False, timeout=None):
    p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode:
        raise RuntimeError((p.stderr or p.stdout or 'command failed').strip())
    return p


def safe_name(value, fallback='Unknown'):
    value = re.sub(r'[\x00-\x1f/\\:*?"<>|]+', ' ', str(value or '')).strip()
    value = re.sub(r'\s+', ' ', value).strip(' .')
    return (value or fallback)[:160]


def tool(name):
    explicit = {'makemkvcon': MAKEMKV}.get(name, '')
    if explicit and Path(explicit).is_file() and os.access(explicit, os.X_OK):
        return explicit
    return shutil.which(name)


def makemkv_available():
    return bool(tool('makemkvcon'))


def netmd_available():
    return (NETMD_DIR / 'surroundcore-md-status.cjs').is_file() and bool(tool('node'))


def optical_devices():
    out = []
    for dev in sorted(glob.glob('/dev/sr*')):
        name = Path(dev).name
        sysdev = Path('/sys/class/block') / name / 'device'
        def text(p):
            try: return p.read_text(errors='replace').strip()
            except OSError: return ''
        vendor, model = text(sysdev / 'vendor'), text(sysdev / 'model')
        bus = 'unknown'
        u = run(['udevadm', 'info', '--query=property', '--name', dev]) if tool('udevadm') else None
        if u and u.returncode == 0:
            for line in u.stdout.splitlines():
                if line.startswith('ID_BUS='): bus = line.split('=', 1)[1]
        out.append({'device': dev, 'name': safe_name(f'{vendor} {model}', name), 'bus': bus})
    return out


def audio_cd_info(device):
    if not tool('cd-paranoia'):
        return None
    p = run(['cd-paranoia', '-d', device, '-Q'], timeout=15)
    text = (p.stdout or '') + '\n' + (p.stderr or '')
    if p.returncode != 0 or not re.search(r'\btrack\b|TOTAL', text, re.I):
        return None
    discid = ''
    if tool('cd-discid'):
        d = run(['cd-discid', device], timeout=10)
        if d.returncode == 0: discid = d.stdout.split()[0] if d.stdout.split() else ''
    tracks = len(re.findall(r'^\s*\d+\.', text, re.M))
    return {'kind': 'audio_cd', 'fingerprint': discid or hashlib.sha256(text.encode()).hexdigest()[:24], 'tracks': tracks}


def block_identity(device):
    values = {}
    for key in ('TYPE', 'LABEL', 'UUID'):
        p = run(['blkid', '-o', 'value', '-s', key, device]) if tool('blkid') else None
        values[key.lower()] = p.stdout.strip() if p and p.returncode == 0 else ''
    s = run(['blockdev', '--getsize64', device]) if tool('blockdev') else None
    values['bytes'] = int(s.stdout.strip()) if s and s.returncode == 0 and s.stdout.strip().isdigit() else 0
    raw = '|'.join(str(values[k]) for k in sorted(values))
    values['fingerprint'] = hashlib.sha256(raw.encode()).hexdigest()[:24] if raw.strip('|0') else ''
    return values


def media_identity(device):
    cd = audio_cd_info(device)
    if cd: return cd
    info = block_identity(device)
    if not info.get('type') and not info.get('bytes'): return None
    return {'kind': 'data_disc', **info}


def ffprobe_streams(path):
    p = run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_streams', str(path)], timeout=60)
    if p.returncode: raise RuntimeError((p.stderr or 'ffprobe failed').strip())
    data = json.loads(p.stdout or '{}')
    streams = []
    for s in data.get('streams', []):
        if s.get('codec_type') != 'audio': continue
        bits = int(s.get('bits_per_raw_sample') or s.get('bits_per_sample') or 0)
        channels = int(s.get('channels') or 0)
        layout = str(s.get('channel_layout') or '')
        codec = str(s.get('codec_name') or '')
        sr = int(s.get('sample_rate') or 0)
        streams.append({'index': int(s['index']), 'codec': codec, 'channels': channels,
                        'layout': layout, 'sample_rate': sr, 'bits': bits})
    return streams


def normalized_layout(s):
    value = (s.get('layout') or '').lower(); ch = int(s.get('channels') or 0)
    if ch == 2: return 'Stereo'
    if ch == 4 or 'quad' in value or '4.0' in value: return '4.0-Quad'
    if ch == 6 or '5.1' in value: return '5.1'
    if ch == 8 or '7.1' in value: return '7.1'
    return f'{ch}ch' if ch else 'Audio'


def lossless(s):
    c = (s.get('codec') or '').lower()
    return c.startswith('pcm_') or c in {'flac', 'alac', 'truehd', 'mlp'} or 'dts' in c and 'ma' in c


def codec_rank(s):
    c = (s.get('codec') or '').lower()
    if c.startswith('pcm_'): return 50
    if c in {'truehd', 'mlp'}: return 40
    if 'dts' in c: return 30
    if c in {'ac3', 'eac3'}: return 20
    return 10 if lossless(s) else 0


def quality_key(s):
    return (1 if lossless(s) else 0, int(s.get('sample_rate') or 0), int(s.get('bits') or 0), codec_rank(s))


def select_audio(streams):
    selected = []
    stereo = sorted((s for s in streams if s.get('channels') == 2), key=quality_key, reverse=True)
    if stereo: selected.append(stereo[0])
    groups = {}
    for s in streams:
        if int(s.get('channels') or 0) <= 2: continue
        groups.setdefault(normalized_layout(s), []).append(s)
    for layout in sorted(groups, key=lambda k: max(x.get('channels', 0) for x in groups[k])):
        selected.append(sorted(groups[layout], key=quality_key, reverse=True)[0])
    if not selected and streams: selected.append(sorted(streams, key=quality_key, reverse=True)[0])
    return selected


def extract_selected_audio(input_path, output_dir, prefix='Programme'):
    output_dir.mkdir(parents=True, exist_ok=True)
    streams = ffprobe_streams(input_path)
    selected = select_audio(streams)
    outputs = []
    for s in selected:
        layout = normalized_layout(s)
        rate = s.get('sample_rate') or 0; bits = s.get('bits') or 0
        detail = '-'.join(x for x in (layout, f'{rate//1000}k' if rate else '', f'{bits}bit' if bits else '') if x)
        out = output_dir / f'{safe_name(prefix)} - {safe_name(detail, layout)}.flac'
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-y', '-i', str(input_path),
               '-map', f"0:{s['index']}", '-vn', '-map_metadata', '-1', '-c:a', 'flac', '-compression_level', '8']
        if bits > 16: cmd += ['-sample_fmt', 's32']
        cmd.append(str(out)); run(cmd, check=True, timeout=None)
        check = ffprobe_streams(out)
        if not check or check[0].get('channels') != s.get('channels') or check[0].get('sample_rate') != s.get('sample_rate'):
            out.unlink(missing_ok=True); raise RuntimeError(f'Audio verification failed for {layout}')
        outputs.append({'path': str(out), 'layout': layout, 'source': s, 'verified': True})
    return outputs


@contextlib.contextmanager
def mounted(source):
    root = Path(tempfile.mkdtemp(prefix='mount-', dir=str(INGEST_ROOT / 'work')))
    try:
        p = run(['mount', '-o', 'ro,nosuid,nodev,loop' if Path(source).is_file() else 'ro,nosuid,nodev', str(source), str(root)])
        if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'mount failed').strip())
        yield root
    finally:
        run(['umount', '-l', str(root)])
        shutil.rmtree(root, ignore_errors=True)

def init_dirs():
    for p in (INGEST_ROOT, INGEST_ROOT / 'uploads', INGEST_ROOT / 'work', INGEST_ROOT / 'state', LIBRARY_ROOT):
        p.mkdir(parents=True, exist_ok=True)


def album_dir(label, fingerprint=''):
    name = safe_name(label, 'Ingested Disc')
    if fingerprint: name += f' [{fingerprint[:10]}]'
    out = LIBRARY_ROOT / name
    if out.exists():
        i = 2
        while (LIBRARY_ROOT / f'{name} #{i}').exists(): i += 1
        out = LIBRARY_ROOT / f'{name} #{i}'
    out.mkdir(parents=True)
    return out


def write_manifest(outdir, data):
    path = outdir / 'surroundcore-ingest.json'
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    return path


def rip_audio_cd(device, label=None, fingerprint=''):
    outdir = album_dir(label or 'Audio CD', fingerprint)
    work = Path(tempfile.mkdtemp(prefix='cdda-', dir=str(INGEST_ROOT / 'work')))
    outputs = []
    try:
        p = run(['cd-paranoia', '-d', device, '-B'], cwd=str(work), timeout=None)
        if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'CD rip failed').strip())
        wavs = sorted(work.glob('*.wav'))
        if not wavs: raise RuntimeError('cd-paranoia produced no audio tracks')
        for n, wav in enumerate(wavs, 1):
            target = outdir / f'{n:02d} Track {n:02d}.flac'
            run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(wav),'-map','0:a:0','-vn',
                 '-c:a','flac','-compression_level','8','-metadata',f'track={n}',str(target)], check=True)
            s = ffprobe_streams(target)
            if not s or s[0]['channels'] != 2 or s[0]['sample_rate'] != 44100:
                raise RuntimeError(f'CD track {n} verification failed')
            outputs.append({'path': str(target), 'layout': 'Stereo', 'verified': True})
        write_manifest(outdir, {'source':'audio_cd','device':device,'fingerprint':fingerprint,'outputs':outputs})
        return {'kind':'audio_cd','output_dir':str(outdir),'outputs':outputs}
    except Exception:
        shutil.rmtree(outdir, ignore_errors=True); raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def grouped_program_files(root, pattern, prefix_re):
    groups = {}
    for p in sorted(root.glob(pattern)):
        m = re.search(prefix_re, p.name, re.I)
        if not m: continue
        key = m.group(1)
        groups.setdefault(key, []).append(p)
    if not groups: return []
    return max(groups.values(), key=lambda xs: sum(x.stat().st_size for x in xs))


def concat_protocol(paths):
    return 'concat:' + '|'.join(str(p) for p in paths)


def process_dvd_video(root, outdir):
    video = root / 'VIDEO_TS'
    paths = grouped_program_files(video, 'VTS_*_*.VOB', r'^VTS_(\d+)_([1-9]\d*)\.VOB$')
    if not paths: raise RuntimeError('DVD VIDEO_TS contains no title VOB programme')
    return extract_selected_audio(concat_protocol(paths), outdir, 'DVD Programme')


def process_dvd_audio(root, outdir):
    audio = root / 'AUDIO_TS'
    paths = grouped_program_files(audio, 'ATS_*_*.AOB', r'^ATS_(\d+)_([1-9]\d*)\.AOB$')
    if not paths: raise RuntimeError('DVD-A AUDIO_TS contains no AOB programme')
    return extract_selected_audio(concat_protocol(paths), outdir, 'DVD-A Programme')


def largest_m2ts(root):
    stream = root / 'BDMV' / 'STREAM'
    files = [p for p in stream.glob('*.m2ts') if p.is_file()]
    return max(files, key=lambda p: p.stat().st_size) if files else None


def process_bluray_folder(root, outdir):
    path = largest_m2ts(root)
    if not path: raise RuntimeError('Blu-ray BDMV contains no M2TS programme')
    return extract_selected_audio(path, outdir, 'Blu-ray Programme')


def duration_seconds(text):
    try:
        parts = [float(x) for x in text.split(':')]
        if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
    except Exception: pass
    return 0


def makemkv_best_title(source):
    exe = tool('makemkvcon')
    if not exe: raise RuntimeError('Protected/complex Blu-ray requires optional MakeMKV provider')
    p = run([exe,'--robot','--cache=128','--messages=-stdout','info',source], timeout=None)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'MakeMKV scan failed').strip())
    titles = {}
    for line in p.stdout.splitlines():
        if not line.startswith('TINFO:'): continue
        fields = line[6:].split(',', 3)
        if len(fields) != 4: continue
        try: idx, attr = int(fields[0]), int(fields[1])
        except ValueError: continue
        value = fields[3].strip().strip('"')
        if attr == 9: titles[idx] = duration_seconds(value)
    if not titles: raise RuntimeError('MakeMKV found no playable Blu-ray title')
    return max(titles, key=titles.get)


def process_makemkv(source, outdir):
    exe = tool('makemkvcon'); title = makemkv_best_title(source)
    work = Path(tempfile.mkdtemp(prefix='makemkv-', dir=str(INGEST_ROOT / 'work')))
    try:
        p = run([exe,'--robot','--cache=128','mkv',source,str(title),str(work)], timeout=None)
        if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'MakeMKV extraction failed').strip())
        mkvs = sorted(work.glob('*.mkv'), key=lambda x: x.stat().st_size, reverse=True)
        if not mkvs: raise RuntimeError('MakeMKV produced no title file')
        return extract_selected_audio(mkvs[0], outdir, 'Blu-ray Programme')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def process_mounted(root, label='Disc', fingerprint='', makemkv_source=None):
    outdir = album_dir(label, fingerprint)
    try:
        if (root / 'BDMV' / 'index.bdmv').exists() or (root / 'BDMV').is_dir():
            protected = (root / 'AACS').exists()
            if makemkv_source and makemkv_available():
                outputs = process_makemkv(makemkv_source, outdir); kind='bluray_makemkv'
            elif protected:
                raise RuntimeError('AACS-protected Blu-ray requires optional MakeMKV provider')
            else:
                outputs = process_bluray_folder(root, outdir); kind='bluray'
        elif (root / 'AUDIO_TS').is_dir() and any((root/'AUDIO_TS').glob('*.AOB')):
            outputs = process_dvd_audio(root, outdir); kind='dvd_audio'
        elif (root / 'VIDEO_TS').is_dir():
            outputs = process_dvd_video(root, outdir); kind='dvd_video_audio'
        else:
            raise RuntimeError('Disc/image has no supported audio-disc structure')
        write_manifest(outdir, {'source':kind,'fingerprint':fingerprint,'outputs':outputs})
        return {'kind':kind,'output_dir':str(outdir),'outputs':outputs}
    except Exception:
        shutil.rmtree(outdir, ignore_errors=True); raise


def process_iso(path, label=None):
    path = Path(path); fingerprint = hashlib.sha256((str(path)+str(path.stat().st_size)).encode()).hexdigest()[:24]
    with mounted(path) as root:
        return process_mounted(root, label or path.stem, fingerprint, makemkv_source='iso:'+str(path) if makemkv_available() else None)


def process_bin(bin_path, cue_path=None, label=None):
    bin_path = Path(bin_path); cue_path = Path(cue_path) if cue_path else bin_path.with_suffix('.cue')
    if not cue_path.is_file():
        try: return process_iso(bin_path, label or bin_path.stem)
        except Exception as exc: raise RuntimeError(f'BIN requires a matching CUE or mountable data image: {exc}')
    work = Path(tempfile.mkdtemp(prefix='bchunk-', dir=str(INGEST_ROOT / 'work')))
    outdir = None
    try:
        base = work / 'track'
        p = run(['bchunk','-w',str(bin_path),str(cue_path),str(base)], timeout=None)
        if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'BIN/CUE conversion failed').strip())
        wavs = sorted(work.glob('track*.wav')) + sorted(work.glob('track*.cdr'))
        if wavs:
            fp = hashlib.sha256((str(bin_path.stat().st_size)+cue_path.read_text(errors='ignore')).encode()).hexdigest()[:24]
            outdir = album_dir(label or bin_path.stem, fp); outputs=[]
            for n, src in enumerate(wavs,1):
                dst=outdir/f'{n:02d} Track {n:02d}.flac'
                run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(src),'-map','0:a:0','-vn','-c:a','flac','-compression_level','8',str(dst)],check=True)
                outputs.append({'path':str(dst),'layout':'Stereo','verified':True})
            write_manifest(outdir, {'source':'bin_cue_audio','outputs':outputs})
            return {'kind':'bin_cue_audio','output_dir':str(outdir),'outputs':outputs}
        isos = sorted(work.glob('track*.iso'))
        if not isos: raise RuntimeError('BIN/CUE contained no readable audio or data track')
        return process_iso(max(isos,key=lambda p:p.stat().st_size), label or bin_path.stem)
    except Exception:
        if outdir: shutil.rmtree(outdir, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)

def process_physical(device):
    identity = media_identity(device)
    if not identity: raise RuntimeError('No readable media in optical drive')
    label = identity.get('label') or Path(device).name
    if identity['kind'] == 'audio_cd':
        return rip_audio_cd(device, 'Audio CD', identity.get('fingerprint',''))
    with mounted(device) as root:
        mk_source = None
        if (root/'BDMV').is_dir() and makemkv_available() and len(optical_devices()) == 1:
            mk_source = 'disc:0'
        return process_mounted(root, label or 'Optical Disc', identity.get('fingerprint',''), mk_source)


def netmd_status():
    if not netmd_available(): return {'available': False, 'reason': 'NetMD helper not installed'}
    p = run(['node', str(NETMD_DIR/'surroundcore-md-status.cjs')], timeout=20)
    if p.returncode: return {'available': False, 'reason': (p.stderr or p.stdout).strip()[-500:]}
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1]); data['available'] = True; return data
    except Exception:
        return {'available': False, 'reason': 'NetMD helper returned invalid status'}


def rip_netmd():
    status = netmd_status()
    if not status.get('available'): raise RuntimeError(status.get('reason') or 'No NetMD device')
    tracks = int(status.get('track_count') or 0)
    if tracks <= 0: raise RuntimeError('MiniDisc contains no tracks')
    fp = status.get('fingerprint') or hashlib.sha256(json.dumps(status,sort_keys=True).encode()).hexdigest()[:24]
    outdir = album_dir(status.get('disc_title') or 'MiniDisc', fp)
    work = Path(tempfile.mkdtemp(prefix='netmd-', dir=str(INGEST_ROOT/'work')))
    outputs=[]
    try:
        for i in range(tracks):
            base = work / f'{i+1:02d}-track'
            p = run(['node', str(NETMD_DIR/'surroundcore-md-rip.cjs'), str(i), str(base)], timeout=None)
            if p.returncode: raise RuntimeError((p.stderr or p.stdout or f'NetMD track {i+1} failed').strip())
            raw = None
            for line in p.stdout.splitlines():
                if line.startswith('CMMDDONE\t'):
                    parts=line.split('\t'); raw=Path(parts[1]) if len(parts)>1 else None
            if not raw or not raw.is_file(): raise RuntimeError(f'NetMD track {i+1} produced no ATRAC file')
            dst=outdir/f'{i+1:02d} Track {i+1:02d}.flac'
            run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(raw),'-map','0:a:0','-vn',
                 '-c:a','flac','-compression_level','8','-sample_fmt','s16','-ar','44100','-ac','2',str(dst)],check=True)
            s=ffprobe_streams(dst)
            if not s or s[0]['channels']!=2 or s[0]['sample_rate']!=44100: raise RuntimeError(f'NetMD track {i+1} verification failed')
            outputs.append({'path':str(dst),'layout':'Stereo','verified':True})
        write_manifest(outdir, {'source':'netmd','device':status.get('device'),'fingerprint':fp,'outputs':outputs})
        return {'kind':'netmd','output_dir':str(outdir),'outputs':outputs}
    except Exception:
        shutil.rmtree(outdir, ignore_errors=True); raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def core_headers():
    return {'Authorization': f'Bearer {TOKEN}', 'Content-Type': 'application/json'}


def notify_core():
    import urllib.request
    if not TOKEN: return {'ok':False,'reason':'Core token not configured'}
    source = json.dumps({'id':'ingest','name':'Optical / Image Ingest','kind':'local','path':'/sources/ingest',
                         'cache_policy':'off','config':{},'enabled':True}).encode()
    req=urllib.request.Request(CORE_URL+'/api/v1/sources',data=source,method='POST',headers=core_headers())
    with urllib.request.urlopen(req,timeout=15) as r: json.loads(r.read().decode() or '{}')
    req=urllib.request.Request(CORE_URL+'/api/v1/sources/ingest/scan',data=b'',method='POST',headers=core_headers())
    with urllib.request.urlopen(req,timeout=300) as r: return json.loads(r.read().decode() or '{}')


def eject(device):
    if tool('eject'): run(['eject', device], timeout=20)


init_dirs()
