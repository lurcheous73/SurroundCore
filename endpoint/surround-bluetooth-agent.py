#!/usr/bin/env python3
import hmac, json, os, re, signal, subprocess, threading, time, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN=os.getenv('SURROUNDCORE_TOKEN','')
CORE_URL=os.getenv('SURROUNDCORE_CORE_URL','http://127.0.0.1:8080').rstrip('/')
ADVERTISE=os.getenv('SURROUNDCORE_BLUETOOTH_ADVERTISE','').rstrip('/')
PORT=int(os.getenv('SURROUNDCORE_BLUETOOTH_PORT','8092'))
PLAYER=None; DECODER=None; ACTIVE_DEVICE=None; LOCK=threading.RLock()


def run(*args, timeout=12):
    p=subprocess.run(args,capture_output=True,text=True,timeout=timeout,check=False)
    return p.returncode,(p.stdout or ''),(p.stderr or '')

def bt(*args,timeout=15):
    return run('bluetoothctl',*args,timeout=timeout)

def local_ip():
    rc,out,_=run('hostname','-I')
    return (out.split() or ['127.0.0.1'])[0]

def control_url():
    return ADVERTISE or f'http://{local_ip()}:{PORT}'

def parse_info(mac):
    rc,out,err=bt('info',mac)
    if rc: return None
    data={'mac':mac.upper()}
    uuids=[]
    for raw in out.splitlines():
        line=raw.strip()
        if ':' not in line: continue
        k,v=line.split(':',1); k=k.strip().lower().replace(' ','_'); v=v.strip()
        if k=='uuid': uuids.append(v)
        elif k in ('paired','trusted','connected','blocked'): data[k]=v.lower()=='yes'
        elif k in ('name','alias','icon','class','modalias'): data[k]=v
    data['uuids']=uuids
    text=' '.join(uuids).lower()
    data['audio_sink']=('audio sink' in text or '0000110b-' in text)
    return data

def _flatten_block(nodes, out=None):
    out = out or []
    for node in nodes or []:
        out.append(node); _flatten_block(node.get("children") or [], out)
    return out

def _top_disk(rows, name):
    by_name={str(x.get("name")):x for x in rows}; cur=by_name.get(str(name)); seen=set()
    while cur and cur.get("pkname") and cur.get("pkname") not in seen:
        seen.add(str(cur.get("name"))); cur=by_name.get(str(cur.get("pkname"))) or cur
        if str(cur.get("type"))=="disk": return cur
    return cur if cur and str(cur.get("type"))=="disk" else None

def _size_label(value):
    try: n=int(value or 0)
    except Exception: n=0
    if n >= 10**12: return f"{round(n/10**12):.0f} TB"
    if n >= 10**9: return f"{round(n/10**9):.0f} GB"
    return ""
def _clean_model(value):
    text=re.sub(r"\s+"," ",str(value or "").strip())
    text=re.sub(r"\b(SSD|NVME|NVM EXPRESS)\b","",text,flags=re.I)
    return re.sub(r"\s+"," ",text).strip()

def storage_modules():
    rc,out,_=run("lsblk","-J","-b","-o","NAME,PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,MOUNTPOINTS,PKNAME,FSTYPE")
    if rc: return []
    try: data=json.loads(out)
    except Exception: return []
    rows=_flatten_block(data.get("blockdevices") or []); root=None
    for row in rows:
        if "/" in (row.get("mountpoints") or []): root=_top_disk(rows,row.get("name")); break
    root_name=str((root or {}).get("name") or "")
    nvmes=[x for x in rows if x.get("type")=="disk" and str(x.get("name") or "").startswith("nvme") and x.get("name")!=root_name]
    def pci_key(row):
        link=os.path.realpath(f"/sys/block/{row['name']}/device")
        m=re.findall(r"[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]",link)
        return m[-1] if m else link
    nvmes.sort(key=pci_key); active=set(); rc,zout,_=run("zpool","status","-P")
    if rc==0:
        for row in nvmes:
            serial=str(row.get("serial") or "")
            if serial and serial in zout: active.add(str(row.get("name")))
    modules=[]
    for idx,row in enumerate(nvmes):
        modules.append({"slot":idx,"label":f"Storage module {idx}","present":True,
          "name":_clean_model(row.get("model")) or "NVMe drive","size":_size_label(row.get("size")),
          "in_use":str(row.get("name")) in active or bool(row.get("mountpoints")),"type":"nvme"})
    try: product=open("/sys/class/dmi/id/product_name").read().strip()
    except Exception: product=""
    expected=3 if product.upper()=="N5" else len(modules)
    for idx in range(len(modules),expected):
        modules.append({"slot":idx,"label":f"Storage module {idx}","present":False,"name":None,"size":None,"in_use":False,"type":"nvme"})
    return modules

def useful_usb_devices():
    rc,out,_=run("lsusb"); items=[]
    if rc:return items
    skip=("Linux Foundation","Genesys Logic","ASMedia Technology","JMicron Technology","Realtek Semiconductor")
    for line in out.splitlines():
        m=re.match(r"Bus \d+ Device \d+: ID ([0-9a-fA-F]{4}):([0-9a-fA-F]{4}) (.+)$",line.strip())
        if not m: continue
        vid,pid,desc=m.group(1).lower(),m.group(2).lower(),m.group(3).strip()
        if (vid,pid)==('2357','0604'): desc='TP-Link UB500'
        if any(x in desc for x in skip): continue
        low=desc.lower(); kind="USB device"
        if "bluetooth" in low or "ub500" in low: kind="Bluetooth adapter"
        elif any(x in low for x in ("audio","silicon gs3","dac")): kind="USB audio"
        elif any(x in low for x in ("dvd","blu-ray","cd-rom","optical")): kind="Optical drive"
        items.append({"name":desc,"type":kind,"present":True})
    return items
def ct_storage_modules():
    ctid=str(os.getenv("SURROUNDCORE_CTID") or "").strip()
    if not ctid: return []
    conf=Path(f"/etc/pve/lxc/{ctid}.conf")
    if not conf.is_file(): return []
    modules=[]
    for raw in conf.read_text(errors="replace").splitlines():
        line=raw.strip()
        m=re.match(r"^mp(\d+):\s*([^,]+),mp=([^,]+)",line)
        if m:
            source,target=m.group(2).strip(),m.group(3).strip()
            name=Path(target).name.replace("-"," ").replace("_"," ").strip() or f"Storage module {len(modules)}"
            modules.append({"slot":len(modules),"label":f"Storage module {len(modules)}","present":True,
              "name":name.title(),"size":None,"in_use":True,"type":"bind","mount":target})
            continue
        m=re.match(r"^dev(\d+):\s*path=([^,]+)",line)
        if not m: continue
        path=m.group(2).strip()
        if not re.match(r"^/dev/(nvme|sd)[A-Za-z0-9._/-]+$",path): continue
        if any(x in path for x in ("/sr","/sg","/bus/usb/","/snd/","/dri/")): continue
        modules.append({"slot":len(modules),"label":f"Storage module {len(modules)}","present":True,
          "name":"Presented disk","size":None,"in_use":False,"type":"disk"})
    return modules

def hardware_inventory():
    ct_modules=ct_storage_modules()
    mode='ct' if os.getenv('SURROUNDCORE_CTID','').strip() else 'bare-metal'
    modules=ct_modules if mode=='ct' else storage_modules()
    return {"mode":mode,"storage_modules":[{k:v for k,v in x.items() if k!='mount'} for x in modules],
            "devices":useful_usb_devices()}

def paired_devices():
    rc,out,_=bt('devices','Paired')
    if rc: return []
    rows=[]
    for line in out.splitlines():
        m=re.match(r'^Device\s+([0-9A-Fa-f:]{17})\s+(.+)$',line.strip())
        if not m: continue
        item=parse_info(m.group(1)) or {'mac':m.group(1).upper(),'name':m.group(2)}
        item.setdefault('name',m.group(2)); item.setdefault('alias',item['name'])
        rows.append(item)
    return rows

def discovered_devices(seconds=6):
    seconds=max(2,min(int(seconds),20))
    bt('power','on'); bt('pairable','on'); bt('discoverable','on')
    run('timeout',str(seconds),'bluetoothctl','scan','on',timeout=seconds+3)
    rc,out,_=bt('devices')
    found=[]
    for line in out.splitlines():
        m=re.match(r'^Device\s+([0-9A-Fa-f:]{17})\s+(.+)$',line.strip())
        if m:
            item=parse_info(m.group(1)) or {'mac':m.group(1).upper(),'name':m.group(2)}
            item.setdefault('name',m.group(2)); item.setdefault('alias',item['name']); found.append(item)
    return found

def endpoint_id(mac): return 'bluetooth:'+mac.replace(':','').lower()

def registration(dev):
    name=dev.get('alias') or dev.get('name') or dev['mac']
    return {'id':endpoint_id(dev['mac']),'name':name,'kind':'bluetooth','address':control_url(),
      'capabilities':{'control_api':'surroundcore-bluetooth-v1','device':dev['mac'],'paired':bool(dev.get('paired')),
      'trusted':bool(dev.get('trusted')),'connected':bool(dev.get('connected')),'audio_sink':bool(dev.get('audio_sink')),
      'transports':['bluetooth'],'codec_path':'BlueALSA A2DP','auto_connect':True}}

def register(dev):
    if not TOKEN or not dev.get('audio_sink'): return False
    body=json.dumps(registration(dev)).encode()
    req=urllib.request.Request(CORE_URL+'/api/v1/endpoints/register',data=body,
      headers={'Content-Type':'application/json','Authorization':'Bearer '+TOKEN})
    try:
        with urllib.request.urlopen(req,timeout=5) as r: return 200 <= r.status < 300
    except Exception: return False

def heartbeat():
    while True:
        for dev in paired_devices(): register(dev)
        time.sleep(20)

def ensure_connected(mac):
    info=parse_info(mac)
    if info and info.get('connected'): return True
    rc,_,_=bt('connect',mac,timeout=15)
    return rc==0

def stop_player():
    global PLAYER,DECODER,ACTIVE_DEVICE
    with LOCK:
        for p in (DECODER,PLAYER):
            if p and p.poll() is None:
                try: p.terminate(); p.wait(timeout=2)
                except Exception:
                    try:p.kill()
                    except Exception:pass
        PLAYER=DECODER=None; ACTIVE_DEVICE=None

def start_play(url,mac,volume=0.35):
    global PLAYER,DECODER,ACTIVE_DEVICE
    if not ensure_connected(mac): raise RuntimeError('Bluetooth device could not connect')
    stop_player()
    pcm=f'bluealsa:DEV={mac},PROFILE=a2dp'
    vol=max(0.0,min(float(volume),1.0))
    decoder=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-i',url,'-map','0:a:0','-vn',
             '-af',f'volume={vol:.4f}','-f','s16le','-acodec','pcm_s16le','-ar','48000','-ac','2','pipe:1']
    player=['aplay','-q','-D',pcm,'-t','raw','-f','S16_LE','-r','48000','-c','2']
    with LOCK:
        PLAYER=subprocess.Popen(player,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
        DECODER=subprocess.Popen(decoder,stdout=PLAYER.stdin,stderr=subprocess.PIPE)
        PLAYER.stdin.close(); ACTIVE_DEVICE=mac
    return {'ok':True,'device':mac,'pcm':pcm}

def json_body(handler):
    try:
        n=int(handler.headers.get('Content-Length','0')); return json.loads(handler.rfile.read(n) or b'{}')
    except Exception:return {}

def authorized(h):
    value=h.headers.get('Authorization','')
    supplied=value[7:] if value.startswith('Bearer ') else ''
    return bool(TOKEN and supplied and hmac.compare_digest(TOKEN,supplied))

class Handler(BaseHTTPRequestHandler):
    def send_json(self,code,obj):
        raw=json.dumps(obj).encode(); self.send_response(code); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        if not authorized(self): return self.send_json(401,{'detail':'unauthorised'})
        if self.path.startswith('/v1/status'):
            return self.send_json(200,{'ok':True,'controller':bt('show')[1],'devices':paired_devices(),'playing':DECODER is not None and DECODER.poll() is None,'active_device':ACTIVE_DEVICE})
        if self.path.startswith('/v1/hardware'):
            return self.send_json(200,hardware_inventory())
        return self.send_json(404,{'detail':'not found'})
    def do_POST(self):
        if not authorized(self): return self.send_json(401,{'detail':'unauthorised'})
        data=json_body(self); path=self.path.split('?',1)[0]
        try:
            if path=='/v1/scan': return self.send_json(200,{'devices':discovered_devices(data.get('seconds',6))})
            if path in ('/v1/pair','/v1/connect','/v1/disconnect','/v1/remove'):
                mac=str(data.get('device') or '').upper()
                if not re.match(r'^[0-9A-F]{2}(:[0-9A-F]{2}){5}$',mac): raise ValueError('valid Bluetooth MAC required')
                if path=='/v1/pair':
                    bt('power','on'); rc,out,err=bt('pair',mac,timeout=35)
                    if rc: raise RuntimeError(err or out or 'pair failed')
                    bt('trust',mac); bt('connect',mac,timeout=15)
                    dev=parse_info(mac); register(dev or {'mac':mac,'name':mac,'audio_sink':True})
                else:
                    cmd=path.rsplit('/',1)[-1]; rc,out,err=bt(cmd,mac,timeout=20)
                    if rc: raise RuntimeError(err or out or f'{cmd} failed')
                return self.send_json(200,{'ok':True,'device':parse_info(mac)})
            if path=='/v1/play':
                mac=str(data.get('device') or ACTIVE_DEVICE or '').upper(); url=str(data.get('url') or '')
                if not url or not mac: raise ValueError('url and Bluetooth device are required')
                return self.send_json(200,start_play(url,mac,data.get('volume',0.35)))
            if path=='/v1/programme':
                urls=data.get('urls') or []
                if not urls: raise ValueError('urls required')
                mac=str(data.get('device') or '').upper()
                # Start first item; programme sequencing is intentionally delegated to Core for now.
                return self.send_json(200,start_play(str(urls[0]),mac,data.get('volume',0.35)))
            if path=='/v1/pause':
                if DECODER and DECODER.poll() is None: DECODER.send_signal(signal.SIGSTOP)
                return self.send_json(200,{'ok':True})
            if path=='/v1/resume':
                if DECODER and DECODER.poll() is None: DECODER.send_signal(signal.SIGCONT)
                return self.send_json(200,{'ok':True})
            if path=='/v1/stop': stop_player(); return self.send_json(200,{'ok':True})
            return self.send_json(404,{'detail':'not found'})
        except Exception as exc: return self.send_json(409,{'detail':str(exc)})
    def log_message(self,*args): pass

def main():
    threading.Thread(target=heartbeat,daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0',PORT),Handler).serve_forever()

if __name__=='__main__': main()
