import contextlib
import fcntl
import glob
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import tempfile
import time
from pathlib import Path

CORE_URL = os.getenv('SURROUNDCORE_CORE_URL', 'http://127.0.0.1:8080').rstrip('/')
TOKEN = os.getenv('SURROUNDCORE_TOKEN', '')
INGEST_ROOT = Path(os.getenv('SURROUNDCORE_INGEST_ROOT', '/ingest'))
LIBRARY_ROOT = Path(os.getenv('SURROUNDCORE_INGEST_LIBRARY', '/library'))
MAKEMKV = os.getenv('SURROUNDCORE_MAKEMKVCON', '').strip()
NETMD_DIR = Path('/opt/surroundcore/netmd')
MOUNT_RETRIES = max(1, int(os.getenv('SURROUNDCORE_INGEST_MOUNT_RETRIES', '3')))
MOUNT_RETRY_SECONDS = max(0.0, float(os.getenv('SURROUNDCORE_INGEST_MOUNT_RETRY_SECONDS', '2')))


def run(args, cwd=None, check=False, timeout=None):
    proc = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        code = proc.returncode
    except subprocess.TimeoutExpired:
        try: os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError: pass
        stdout, stderr = proc.communicate()
        code = 124
        stderr = (stderr or '') + f'\ncommand timed out after {timeout}s'
    p = subprocess.CompletedProcess(args, code, stdout, stderr)
    if check and p.returncode:
        raise RuntimeError((p.stderr or p.stdout or 'command failed').strip())
    return p



@contextlib.contextmanager
def media_lock(timeout=5.0):
    lock_path = INGEST_ROOT / 'state' / 'media.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open('a+')
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                fh.close(); raise RuntimeError('Media device is busy')
            time.sleep(0.1)
    try:
        yield
    finally:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN); fh.close()

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


_NETMD_LOCK = threading.Lock()

def netmd_available():
    return (NETMD_DIR / 'surroundcore-md-status.cjs').is_file() and bool(tool('node'))


def scsi_generic_for(device):
    name=Path(device).name
    try: target=(Path('/sys/class/block')/name/'device').resolve()
    except OSError: return ''
    for sg in sorted(Path('/sys/class/scsi_generic').glob('sg*')):
        try:
            if (sg/'device').resolve()==target: return '/dev/'+sg.name
        except OSError: pass
    return ''


def optical_devices():
    out=[]
    for dev in sorted(glob.glob('/dev/sr*')):
        sysdev=Path('/sys/class/block')/Path(dev).name/'device'
        def text(x):
            try: return x.read_text(errors='replace').strip()
            except OSError: return ''
        vendor,model=text(sysdev/'vendor'),text(sysdev/'model')
        bus='usb' if '/usb' in str(sysdev.resolve()) else 'local'
        out.append({'device':dev,'sg_device':scsi_generic_for(dev),'name':safe_name(f'{vendor} {model}',Path(dev).name),'bus':bus})
    return out

def burner_status(device):
    devices={d['device']:d for d in optical_devices()}
    if device not in devices:
        return {'device':device,'available':False,'writable':False,'profiles':[],'reason':'Optical drive not found'}
    target=devices[device].get('sg_device') or device
    profiles=[]
    if tool('sg_get_config'):
        q=run(['sg_get_config',target],timeout=5)
        text=(q.stdout or '')+'\n'+(q.stderr or '')
        for label in ('CD-R','CD-RW','DVD-R','DVD-RW','DVD+R','DVD+RW','BD-R','BD-RE'):
            if re.search(r'profile:\s*'+re.escape(label)+r'\b',text,re.I): profiles.append(label)
    out=dict(devices[device]); out.update({'available':True,'writable':bool(profiles),'profiles':profiles})
    out['reason']='write capable' if profiles else 'no writable optical profiles reported'
    return out


def burn_image(device,image,image_kind='iso'):
    image=Path(image)
    if device not in {d['device'] for d in optical_devices()}: raise RuntimeError('Optical burner not found')
    if not image.is_file(): raise RuntimeError('Burn image not found')
    if image_kind=='iso':
        exe=tool('xorriso'); cmd=[exe,'-as','cdrecord','-v',f'dev={device}','-eject',str(image)] if exe else None
    elif image_kind=='cue':
        exe=tool('cdrdao'); cmd=[exe,'write','--device',device,'--eject',str(image)] if exe else None
    else: raise RuntimeError('Unsupported burn image type')
    if not cmd: raise RuntimeError('Required optical burn tool is not installed')
    q=run(cmd,timeout=60*60)
    if q.returncode: raise RuntimeError((q.stderr or q.stdout or 'disc burn failed').strip()[-4000:])
    return {'ok':True,'device':device,'image':image.name,'kind':image_kind,'tool':Path(exe).name}

def audio_cd_info(device):
    if not tool('cd-paranoia'): return None
    q=run(['cd-paranoia','-d',device,'-Q'],timeout=15)
    text=(q.stdout or '')+'\n'+(q.stderr or '')
    if q.returncode!=0 or not re.search(r'\btrack\b|TOTAL',text,re.I): return None
    discid=''
    if tool('cd-discid'):
        d=run(['cd-discid',device],timeout=10)
        if d.returncode==0 and d.stdout.split(): discid=d.stdout.split()[0]
    tracks=len(re.findall(r'^\s*\d+\.',text,re.M))
    toc=''
    if tool('cd-discid'):
        mb=run(['cd-discid','--musicbrainz',device],timeout=10)
        if mb.returncode==0:
            candidate=' '.join((mb.stdout or '').split())
            if candidate and all(x.isdigit() for x in candidate.split()): toc=candidate
    return {'kind':'audio_cd','fingerprint':discid or hashlib.sha256(text.encode()).hexdigest()[:24],
            'tracks':tracks,'musicbrainz_toc':toc}


def optical_media_info(device):
    sg=scsi_generic_for(device) or device
    values={'profile':'','bytes':0,'sg_device':sg}
    if tool('sg_get_config'):
        q=run(['sg_get_config',sg],timeout=5); text=(q.stdout or '')+'\n'+(q.stderr or '')
        m=re.search(r'Current profile:\s*(.+)',text,re.I)
        if m: values['profile']=m.group(1).strip()
    if tool('sg_readcap'):
        q=run(['sg_readcap',sg],timeout=5); text=(q.stdout or '')+'\n'+(q.stderr or '')
        m=re.search(r'Device size:\s*(\d+) bytes',text,re.I)
        if m: values['bytes']=int(m.group(1))
    raw=f"{values['profile']}|{values['bytes']}"
    values['fingerprint']=hashlib.sha256(raw.encode()).hexdigest()[:24] if raw.strip('|0') else ''
    return values


def block_identity(device): return optical_media_info(device)

def media_identity(device):
    info=optical_media_info(device)
    profile=str(info.get('profile') or '').upper()
    if profile.startswith('BD'): return {'kind':'bluray',**info}
    if 'DVD' in profile: return {'kind':'dvd',**info}
    if 'CD' in profile:
        cd=audio_cd_info(device)
        if cd:
            cd.update({'profile':info.get('profile',''),'bytes':info.get('bytes',0),'sg_device':info.get('sg_device','')})
            return cd
        return {'kind':'data_disc',**info}
    cd=audio_cd_info(device)
    if cd: return cd
    if info.get('profile') or info.get('bytes'): return {'kind':'data_disc',**info}
    return None

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
    mounted_ok = False
    try:
        options = 'ro,nosuid,nodev,loop' if Path(source).is_file() else 'ro,nosuid,nodev'
        last_error = 'mount failed'
        for attempt in range(1, MOUNT_RETRIES + 1):
            try:
                proc = run(['mount', '-o', options, str(source), str(root)], timeout=20)
                if proc.returncode == 0:
                    mounted_ok = True
                    break
                last_error = (proc.stderr or proc.stdout or 'mount failed').strip()
            except Exception as exc:
                last_error = str(exc)
            if attempt < MOUNT_RETRIES and MOUNT_RETRY_SECONDS:
                time.sleep(MOUNT_RETRY_SECONDS)
        if not mounted_ok:
            sevenzip = tool('7z') or tool('7zz')
            if not sevenzip:
                raise RuntimeError(f'mount failed after {MOUNT_RETRIES} attempts: {last_error}; 7-Zip fallback unavailable')
            proc = run([sevenzip, 'x', '-y', '-bd', f'-o{root}', str(source)], timeout=None)
            if proc.returncode:
                detail = (proc.stderr or proc.stdout or '7-Zip extraction failed').strip()
                raise RuntimeError(f'mount failed ({last_error}); userspace extraction failed: {detail}')
        yield root
    finally:
        if mounted_ok:
            try:
                run(['umount', '-l', str(root)], timeout=20)
            except Exception:
                pass
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


def rip_audio_cd(device, label=None, fingerprint='', metadata=None):
    metadata=dict(metadata or {})
    artist=str(metadata.get('artist') or '').strip()
    album=str(metadata.get('title') or label or 'Audio CD').strip() or 'Audio CD'
    folder_label=(' - '.join(x for x in (artist,album) if x)) or album
    outdir = album_dir(folder_label, fingerprint)
    work = Path(tempfile.mkdtemp(prefix='cdda-', dir=str(INGEST_ROOT / 'work')))
    outputs = []
    titles=list(metadata.get('tracks') or [])
    try:
        p = run(['cd-paranoia', '-d', device, '-B'], cwd=str(work), timeout=None)
        if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'CD rip failed').strip())
        wavs = sorted(work.glob('*.wav'))
        if not wavs: raise RuntimeError('cd-paranoia produced no audio tracks')
        for n, wav in enumerate(wavs, 1):
            title=str(titles[n-1]).strip() if n-1 < len(titles) and str(titles[n-1]).strip() else f'Track {n:02d}'
            target = outdir / f'{n:02d} {safe_name(title)}.flac'
            cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(wav),'-map','0:a:0','-vn',
                 '-c:a','flac','-compression_level','8','-metadata',f'track={n}',
                 '-metadata',f'title={title}','-metadata',f'album={album}']
            if artist: cmd += ['-metadata',f'artist={artist}','-metadata',f'album_artist={artist}']
            if metadata.get('year'): cmd += ['-metadata',f'date={metadata.get("year")}']
            cmd.append(str(target)); run(cmd, check=True)
            s = ffprobe_streams(target)
            if not s or s[0]['channels'] != 2 or s[0]['sample_rate'] != 44100:
                raise RuntimeError(f'CD track {n} verification failed')
            outputs.append({'path': str(target), 'layout': 'Stereo', 'verified': True, 'title':title, 'track':n})
        art=str(metadata.get('artwork_url') or '')
        if art.startswith('https://coverartarchive.org/release/'):
            try:
                import urllib.request
                req=urllib.request.Request(art,headers={'User-Agent':'SurroundCore/0.5'})
                with urllib.request.urlopen(req,timeout=20) as r: data=r.read(15*1024*1024+1)
                if 100 < len(data) <= 15*1024*1024: (outdir/'cover.jpg').write_bytes(data)
            except Exception: pass
        write_manifest(outdir, {'source':'audio_cd','device':device,'fingerprint':fingerprint,'metadata':metadata,'outputs':outputs})
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


def process_makemkv(source, outdir, prefix='Disc Programme'):
    exe = tool('makemkvcon'); title = makemkv_best_title(source)
    work = Path(tempfile.mkdtemp(prefix='makemkv-', dir=str(INGEST_ROOT / 'work')))
    try:
        p = run([exe,'--robot','--cache=128','mkv',source,str(title),str(work)], timeout=None)
        if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'MakeMKV extraction failed').strip())
        mkvs = sorted(work.glob('*.mkv'), key=lambda x: x.stat().st_size, reverse=True)
        if not mkvs: raise RuntimeError('MakeMKV produced no title file')
        return extract_selected_audio(mkvs[0], outdir, prefix)
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

def process_physical(device, metadata=None):
    identity=media_identity(device)
    if not identity: raise RuntimeError('No readable media in optical drive')
    if identity['kind']=='audio_cd':
        return rip_audio_cd(device,'Audio CD',identity.get('fingerprint',''),metadata)
    if identity['kind'] in ('dvd','bluray'):
        if not makemkv_available(): raise RuntimeError('DVD/Blu-ray requires MakeMKV on this Core')
        label='Blu-ray Disc' if identity['kind']=='bluray' else 'DVD Disc'
        outdir=album_dir(label,identity.get('fingerprint',''))
        try:
            outputs=process_makemkv('disc:0',outdir,label+' Programme')
            kind=identity['kind']+'_makemkv'
            write_manifest(outdir,{'source':kind,'device':device,'fingerprint':identity.get('fingerprint',''),'outputs':outputs})
            return {'kind':kind,'output_dir':str(outdir),'outputs':outputs}
        except Exception:
            shutil.rmtree(outdir,ignore_errors=True); raise
    raise RuntimeError('Unsupported optical media profile; create an ISO backup for inspection')


def _netmd_status_unlocked():
    p = run(['node', str(NETMD_DIR/'surroundcore-md-status.cjs')], timeout=12)
    if p.returncode: return {'available': False, 'reason': (p.stderr or p.stdout).strip()[-500:]}
    try:
        data = json.loads(p.stdout.strip().splitlines()[-1]); data['available'] = True; return data
    except Exception:
        return {'available': False, 'reason': 'NetMD helper returned invalid status'}


def netmd_status():
    if not netmd_available(): return {'available': False, 'reason': 'NetMD helper not installed'}
    if not _NETMD_LOCK.acquire(timeout=1):
        return {'available': False, 'reason': 'NetMD device is busy'}
    try: return _netmd_status_unlocked()
    finally: _NETMD_LOCK.release()


def rip_netmd():
    if not netmd_available(): raise RuntimeError('NetMD helper not installed')
    if not _NETMD_LOCK.acquire(timeout=2): raise RuntimeError('NetMD device is busy')
    outdir = None; work = None
    try:
        status = _netmd_status_unlocked()
        if not status.get('available'): raise RuntimeError(status.get('reason') or 'No NetMD device')
        if not status.get('rippable', True): raise RuntimeError('Hi-MD detected; standard NetMD rip path is not applicable')
        track_count = int(status.get('track_count') or 0)
        if track_count <= 0: raise RuntimeError('MiniDisc contains no tracks')
        fp = status.get('fingerprint') or hashlib.sha256(json.dumps(status,sort_keys=True).encode()).hexdigest()[:24]
        outdir = album_dir(status.get('disc_title') or 'MiniDisc', fp)
        work = Path(tempfile.mkdtemp(prefix='netmd-', dir=str(INGEST_ROOT/'work')))
        rawdir = work / 'raw'; rawdir.mkdir(parents=True, exist_ok=True)
        p = run(['node', str(NETMD_DIR/'surroundcore-md-rip.cjs'), 'all', str(rawdir)], timeout=None)
        if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'NetMD disc rip failed').strip())
        raw_tracks = {}
        for line in p.stdout.splitlines():
            if not line.startswith('CMMDDONE\t'): continue
            parts=line.split('\t')
            if len(parts) >= 3:
                try: raw_tracks[int(parts[1])] = Path(parts[2])
                except Exception: pass
        if len(raw_tracks) != track_count:
            raise RuntimeError(f'NetMD rip returned {len(raw_tracks)} of {track_count} tracks')
        metadata = {int(t.get('index',i)): t for i,t in enumerate(status.get('tracks') or [])}
        outputs=[]
        album = str(status.get('disc_title') or 'MiniDisc')
        for i in range(track_count):
            raw = raw_tracks.get(i)
            if not raw or not raw.is_file(): raise RuntimeError(f'NetMD track {i+1} produced no ATRAC file')
            md = metadata.get(i) or {}
            title = str(md.get('title') or f'Track {i+1:02d}').strip() or f'Track {i+1:02d}'
            dst=outdir/f'{i+1:02d} {safe_name(title)}.flac'
            cmd=['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(raw),'-map','0:a:0','-vn',
                 '-c:a','flac','-compression_level','8','-ar','44100',
                 '-metadata',f'title={title}','-metadata',f'album={album}','-metadata',f'track={i+1}',str(dst)]
            run(cmd,check=True)
            streams=ffprobe_streams(dst)
            if not streams or streams[0]['sample_rate']!=44100 or streams[0]['channels'] not in (1,2):
                raise RuntimeError(f'NetMD track {i+1} verification failed')
            channels=int(streams[0]['channels'])
            outputs.append({'path':str(dst),'layout':'Mono' if channels==1 else 'Stereo','channels':channels,
                            'sample_rate':44100,'verified':True,'track_index':i,'title':title,
                            'encoding':md.get('encoding'),'group_title':md.get('group_title') or ''})
        manifest = {
            'source':'netmd','device':status.get('device'),'device_signature':status.get('device_signature'),
            'fingerprint':fp,'toc_hash':status.get('toc_hash'),'disc_title':album,
            'tracks':status.get('tracks') or [],'groups':status.get('groups') or [],'outputs':outputs
        }
        write_manifest(outdir, manifest)
        return {'kind':'netmd','output_dir':str(outdir),'outputs':outputs,'disc':status}
    except Exception:
        if outdir: shutil.rmtree(outdir, ignore_errors=True)
        raise
    finally:
        if work: shutil.rmtree(work, ignore_errors=True)
        _NETMD_LOCK.release()


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
