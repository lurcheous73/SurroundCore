import json, os, shutil, signal, subprocess, threading, time, uuid
from datetime import datetime
from pathlib import Path
from .db import upsert_media, list_media
from . import catalog

ROOT=Path(os.getenv('SURROUNDCORE_RECORDINGS','/data/recordings'))
ATRACDENC=os.getenv('SURROUNDCORE_ATRACDENC','/opt/surroundcore/atracdenc')
ROOT.mkdir(parents=True,exist_ok=True)
_LOCK=threading.RLock(); _SESSIONS={}
CASSETTES={
 'I':{'name':'Type I · Normal/Ferric','ext':'flac','args':['-c:a','flac','-sample_fmt','s16','-ar','44100'],'label':'16-bit / 44.1 kHz'},
 'II':{'name':'Type II · High Bias/Chrome','ext':'oma','args':[],'label':'ATRAC3 LP2 · 132 kbps','encoder':'atrac3'},
 'III':{'name':'Type III · Ferrichrome','ext':'flac','args':['-c:a','flac','-sample_fmt','s32','-ar','44100'],'label':'24-bit / 44.1 kHz'},
 'IV':{'name':'Type IV · Metal','ext':'mka','args':['-c:a','copy'],'label':'preserve source codec'},
}
CAPTURE_TYPES=('album','7-inch','12-inch','dat','reel-to-reel','dss','other')

def _safe(value):
    import re
    return re.sub(r'[^A-Za-z0-9._-]+','-',str(value or '').strip()).strip('-')[:80] or 'recording'

def _manifest(path,data):
    mp=path.with_suffix(path.suffix+'.json');mp.write_text(json.dumps(data,indent=2));return mp
def _spawn_ffmpeg(source,out,args,input_args=None):
    cmd=['ffmpeg','-hide_banner','-loglevel','warning','-nostdin','-y']
    cmd += list(input_args or []) + ['-i',source,'-map','0:a:0','-vn'] + list(args) + [str(out)]
    return subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)

def start_radio(station,cassette='I',engine='auto'):
    profile=CASSETTES.get(cassette)
    if not profile: raise ValueError('unknown cassette type')
    sid=str(station.get('id') or 'radio');name=str(station.get('name') or 'Radio')
    stamp=datetime.now().strftime('%Y-%m-%d_%H-%M-%S');folder=ROOT/'cassettes'/_safe(name);folder.mkdir(parents=True,exist_ok=True)
    out=folder/f'{_safe(name)}_{stamp}.{profile["ext"]}'
    work=out.with_suffix('.capture.wav') if profile.get('encoder')=='atrac3' else out
    args=['-c:a','pcm_s16le','-ar','44100','-ac','2'] if profile.get('encoder')=='atrac3' else profile['args']
    proc=_spawn_ffmpeg(station['url'],work,args)
    rid='rec-'+uuid.uuid4().hex[:16]
    data={'id':rid,'kind':'cassette','cassette_type':cassette,'quality':profile['label'],'station':name,
          'station_id':sid,'source_url':station['url'],'path':str(out),'started_at':time.time(),
          'export_blocked':True,'engine':'ffmpeg' if engine=='auto' else engine,'state':'recording',
          'work_path':str(work) if work!=out else None,'post_encoder':profile.get('encoder')}
    _manifest(out,data)
    with _LOCK:_SESSIONS[rid]={'process':proc,'data':data}
    return data

def start_capture(device,media_type='album',name='Untitled',sample_rate=96000,bit_depth=24,input_role='line'):
    if media_type not in CAPTURE_TYPES: raise ValueError('unknown capture type')
    stamp=datetime.now().strftime('%Y-%m-%d_%H-%M-%S');folder=ROOT/'records'/_safe(media_type);folder.mkdir(parents=True,exist_ok=True)
    out=folder/f'{_safe(name)}_{stamp}.flac';fmt='s16' if int(bit_depth)<=16 else 's32'
    proc=_spawn_ffmpeg(device,out,['-c:a','flac','-sample_fmt',fmt,'-ar',str(int(sample_rate))],['-f','alsa'])
    rid='rec-'+uuid.uuid4().hex[:16]
    data={'id':rid,'kind':'capture','media_type':media_type,'name':name,'device':device,'path':str(out),
          'sample_rate':int(sample_rate),'bit_depth':int(bit_depth),'input_role':input_role,'started_at':time.time(),'export_blocked':False,'state':'recording'}
    _manifest(out,data)
    with _LOCK:_SESSIONS[rid]={'process':proc,'data':data}
    return data
def _probe(path):
    p=subprocess.run(['ffprobe','-v','error','-select_streams','a:0','-show_entries',
      'stream=codec_name,channels,channel_layout,sample_rate,bits_per_raw_sample,bits_per_sample:format=duration',
      '-of','json',str(path)],capture_output=True,text=True,timeout=10)
    if p.returncode:return {}
    try:
        j=json.loads(p.stdout);s=(j.get('streams') or [{}])[0];f=j.get('format') or {};bits=s.get('bits_per_raw_sample') or s.get('bits_per_sample')
        return {'codec':s.get('codec_name'),'channels':int(s.get('channels') or 0),'channel_layout':s.get('channel_layout') or '',
                'sample_rate':int(s.get('sample_rate') or 0),'bit_depth':int(bits) if str(bits or '').isdigit() else None,'duration':float(f.get('duration') or 0)}
    except Exception:return {}

def _index(data):
    path=Path(data['path'])
    if not path.exists() or path.stat().st_size==0:return None
    info=_probe(path); meta={'title':data.get('station') or data.get('name') or path.stem,
      'album':'Cassettes' if data.get('kind')=='cassette' else data.get('name') or 'Recorded Audio',
      'artist':data.get('station') or 'SurroundCore Recording','recording':data}
    item={'path':str(path),'codec':info.get('codec'),'channels':info.get('channels') or 2,
      'channel_layout':info.get('channel_layout') or 'stereo','sample_rate':info.get('sample_rate') or 0,
      'bit_depth':info.get('bit_depth'),'duration':info.get('duration') or 0,'metadata':meta}
    upsert_media(item); media=next((m for m in list_media() if m.get('path')==str(path)),None)
    if media:
        origin='radio_recording' if data.get('kind')=='cassette' else f"{data.get('media_type','audio')}_capture"
        catalog.attach_media(media['id'],origin=origin,source_identifier=data.get('station_id') or data.get('device'),
          export_blocked=bool(data.get('export_blocked')),canonical_album=meta['album'],edition_title=data.get('quality') or data.get('media_type'))
    return media
def _finish_session(rid,data,proc):
    data=dict(data); data['stopped_at']=time.time()
    data['state']='finished' if proc.returncode in (0,255,-2,-15) else 'failed'
    if data['state']=='finished' and data.get('post_encoder')=='atrac3':
        work=Path(data.get('work_path') or '')
        out=Path(data['path'])
        if not Path(ATRACDENC).is_file():
            data['state']='failed'; data['error']='ATRAC encoder is not installed'
        else:
            enc=subprocess.run([ATRACDENC,'-e','atrac3','-i',str(work),'-o',str(out)],capture_output=True,text=True)
            if enc.returncode:
                data['state']='failed'; data['error']=(enc.stderr or enc.stdout or 'ATRAC encoding failed')[-1000:]
            else:
                work.unlink(missing_ok=True)
    try: _manifest(Path(data['path']),data)
    except Exception: pass
    media=_index(data) if data['state']=='finished' else None
    data['media_id']=media.get('id') if media else None
    return data

def stop(rid):
    with _LOCK: entry=_SESSIONS.get(rid)
    if not entry: raise KeyError('recording session not found')
    proc=entry['process']; data=entry['data']
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try: proc.wait(timeout=12)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try: proc.wait(timeout=4)
            except subprocess.TimeoutExpired: proc.kill(); proc.wait(timeout=3)
    result=_finish_session(rid,data,proc)
    with _LOCK: _SESSIONS.pop(rid,None)
    return result

def sessions():
    out=[]
    with _LOCK: items=list(_SESSIONS.items())
    for rid,entry in items:
        proc=entry['process']; data=dict(entry['data'])
        data['pid']=proc.pid; data['running']=proc.poll() is None
        data['elapsed']=max(0,time.time()-float(data.get('started_at') or time.time()))
        if proc.poll() is not None:
            data['state']='finished' if proc.returncode==0 else 'failed'
        out.append(data)
    return out

def recordings(kind=None):
    out=[]
    for mp in ROOT.rglob('*.json'):
        try: data=json.loads(mp.read_text())
        except Exception: continue
        if kind and data.get('kind')!=kind: continue
        data['exists']=Path(data.get('path','')).exists(); out.append(data)
    return sorted(out,key=lambda x:float(x.get('started_at') or 0),reverse=True)

def cassette_profiles():
    return [{'id':k,**{x:v for x,v in p.items() if x!='args'}} for k,p in CASSETTES.items()]

DEVICE_FAMILIES=[
 (('SOUND BLASTER','CREATIVE'),'Creative Sound Blaster'),(('RME','FIREFACE','BABYFACE'),'RME Audio'),
 (('FOCUSRITE','SCARLETT','CLARETT'),'Focusrite'),(('MOTU',),'MOTU'),(('TOPPING',),'Topping'),
 (('S.M.S.L','SMSL'),'SMSL'),(('IFI AUDIO','IFI '),'iFi Audio'),(('FIIO',),'FiiO'),
 (('CHORD',),'Chord Electronics'),(('SCHIIT',),'Schiit Audio'),(('CAMBRIDGE AUDIO',),'Cambridge Audio'),
 (('BENCHMARK',),'Benchmark Media'),(('MINIDSP',),'miniDSP'),(('PRESONUS',),'PreSonus'),
 (('BEHRINGER',),'Behringer'),(('STEINBERG',),'Steinberg'),(('UNIVERSAL AUDIO',),'Universal Audio'),
 (('DENON',),'Denon'),(('MARANTZ',),'Marantz'),(('YAMAHA',),'Yamaha'),(('NAD',),'NAD'),
 (('ROTEL',),'Rotel'),(('ARCAM',),'Arcam')]

def _device_family(text):
    upper=str(text or '').upper()
    for needles,name in DEVICE_FAMILIES:
        if any(n in upper for n in needles): return name
    return 'USB Audio Class' if 'USB' in upper else 'Built-in / Generic Audio'

def _capture_pcm_caps(card):
    path=Path(f'/proc/asound/card{card}/stream0')
    if not path.is_file(): return {'max_channels':None,'sample_rates':[],'bit_depths':[],'sample_formats':[],'capability_source':'unknown'}
    try: text=path.read_text(errors='replace')
    except OSError: text=''
    import re
    rates,bits,channels,formats=set(),set(),[],set(); in_capture=False
    for raw in text.splitlines():
        line=raw.strip()
        if line.endswith('Playback:') or line=='Playback:': in_capture=False; continue
        if line.endswith('Capture:') or line=='Capture:': in_capture=True; continue
        if not in_capture: continue
        if line.startswith('Channels:'):
            channels += [int(x) for x in re.findall(r'\b\d+\b',line.split(':',1)[1])]
        elif line.startswith('Rates:'):
            rates.update(int(x) for x in re.findall(r'\b\d+\b',line.split(':',1)[1]) if 8000<=int(x)<=768000)
        elif line.startswith('Format:') or line.startswith('Formats:'):
            fm=line.split(':',1)[1].upper(); formats.update(x.strip() for x in fm.replace(',',' ').split() if x.strip())
            for b in (8,16,20,24,32,64):
                if str(b) in fm: bits.add(b)
    return {'max_channels':max(channels) if channels else None,'sample_rates':sorted(rates),'bit_depths':sorted(bits),
            'sample_formats':sorted(formats),'capability_source':'usb-audio-descriptor' if rates or channels or bits or formats else 'unknown'}

def capture_devices():
    import re
    devices=[]
    try:
        p=subprocess.run(['arecord','-l'],capture_output=True,text=True,timeout=5)
        for raw in p.stdout.splitlines():
            m=re.match(r'^card\s+(\d+):\s*([^,]+),\s*device\s+(\d+):\s*(.*)$',raw.strip())
            if not m: continue
            card,device=int(m.group(1)),int(m.group(3)); card_desc=m.group(2).strip(); dev_desc=m.group(4).strip()
            description=f'{card_desc} · {dev_desc}'; upper=description.upper()
            input_type='usb-audio' if 'USB' in upper else ('digital' if any(x in upper for x in ('S/PDIF','SPDIF','IEC958','AES')) else 'analog')
            caps=_capture_pcm_caps(card)
            card_id=''
            try: card_id=Path(f'/proc/asound/card{card}/id').read_text().strip()
            except OSError: pass
            usb_id=''
            try: usb_id=Path(f'/proc/asound/card{card}/usbid').read_text().strip()
            except OSError: pass
            devices.append({'id':f'plughw:{card},{device}','raw_alsa':f'hw:{card},{device}','name':card_desc,
              'description':description,'backend':'alsa','device_family':_device_family(description),'input_type':input_type,
              'card_index':card,'device_index':device,'card_id':card_id,'usb_id':usb_id,
              'multichannel_candidate':bool((caps.get('max_channels') or 0)>2),**caps})
    except Exception: pass
    return devices

def import_recording(path,media_type='album',name=None,origin='usb_import',export_blocked=False,artist=None,album=None,edition=None,source_identifier=None,source_serial=None,content_hash=None):
    src=Path(path)
    if not src.is_file(): raise FileNotFoundError(str(src))
    info=_probe(src); meta={'title':name or src.stem,'album':album or name or src.parent.name,
      'artist':artist or 'Imported recording','recording':{'media_type':media_type,'origin':origin,'edition':edition}}
    item={'path':str(src),'codec':info.get('codec'),'channels':info.get('channels') or 2,
      'channel_layout':info.get('channel_layout') or 'stereo','sample_rate':info.get('sample_rate') or 0,
      'bit_depth':info.get('bit_depth'),'duration':info.get('duration') or 0,'metadata':meta}
    upsert_media(item); media=next((m for m in list_media() if m.get('path')==str(src)),None)
    if media:
        catalog.attach_media(media['id'],origin=origin,source_identifier=source_identifier or str(src),source_serial=source_serial,
          content_hash=content_hash,export_blocked=export_blocked,canonical_album=meta['album'],
          edition_title=edition or media_type,media_type=media_type)
    return media

def capabilities():
    return {'ffmpeg':bool(shutil.which('ffmpeg')),'streamripper':bool(shutil.which('streamripper')),
      'arecord':bool(shutil.which('arecord')),'atracdenc':Path(ATRACDENC).is_file(),'cassette_profiles':cassette_profiles(),
      'capture_types':list(CAPTURE_TYPES),'capture_devices':capture_devices()}
