import json, os, re, shutil, subprocess, tempfile, uuid, time, threading
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app=FastAPI(title='SurroundCore Storage',version='0.2')
TOKEN=os.getenv('SURROUNDCORE_TOKEN','')
DATA=Path(os.getenv('SURROUNDCORE_STORAGE_DATA','/data/storage'))
SOURCES=Path(os.getenv('SURROUNDCORE_SOURCES','/sources'))
BACKUPS=Path(os.getenv('SURROUNDCORE_BACKUPS','/backups'))
RCLONE=DATA/'rclone.conf'
STATE=DATA/'targets.json'
MANAGED_POOLS=DATA/'managed-pools.json'
for p in (DATA,SOURCES,BACKUPS): p.mkdir(parents=True,exist_ok=True)

RAW_ALLOWED_DEVICES={x.strip() for x in os.getenv('SURROUNDCORE_STORAGE_ALLOWED_DEVICES','').split(',') if x.strip()}
RAW_ALLOWED_SERIALS={x.strip() for x in os.getenv('SURROUNDCORE_STORAGE_ALLOWED_SERIALS','').split(',') if x.strip()}
POOL_ROOT=Path(os.getenv('SURROUNDCORE_POOL_ROOT','/pools'))
POOL_ROOT.mkdir(parents=True,exist_ok=True)

def auth(value):
    supplied=(value or '').removeprefix('Bearer ').strip()
    if not TOKEN or supplied!=TOKEN: raise HTTPException(401,'unauthorised')

def run(cmd,timeout=12,env=None):
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,env=env)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'command failed').strip())
    return p.stdout

def load_state():
    try:return json.loads(STATE.read_text())
    except Exception:return {'library':{},'backup':{}}

def save_state(data):
    tmp=STATE.with_suffix('.tmp');tmp.write_text(json.dumps(data,indent=2));os.replace(tmp,STATE)
CLOUDS={
 'google_drive':{'name':'Google Drive','rclone':'drive','auth':'oauth'},
 'dropbox':{'name':'Dropbox','rclone':'dropbox','auth':'oauth'},
 'onedrive':{'name':'Microsoft OneDrive','rclone':'onedrive','auth':'oauth'},
 'mega':{'name':'MEGA','rclone':'mega','auth':'credentials'},
 'aws_s3':{'name':'Amazon S3','rclone':'s3','auth':'keys'},
 's3':{'name':'S3-compatible','rclone':'s3','auth':'keys'},
 'icloud':{'name':'iCloud Drive','rclone':'iclouddrive','auth':'credentials'},
 'box':{'name':'Box','rclone':'box','auth':'oauth'},
 'pcloud':{'name':'pCloud','rclone':'pcloud','auth':'oauth'},
 'b2':{'name':'Backblaze B2','rclone':'b2','auth':'keys'},
 'azureblob':{'name':'Azure Blob','rclone':'azureblob','auth':'keys'},
}

class NetworkRequest(BaseModel):
 kind:str; host:str; share:str|None=None; username:str|None=None; password:str|None=None
 domain:str|None=None; name:str|None=None; use:str='library'; writable:bool=False
class UsbRequest(BaseModel):
 device:str; name:str|None=None; use:str='library'
class CloudRequest(BaseModel):
 provider:str; name:str|None=None; username:str|None=None; password:str|None=None
 access_key:str|None=None; secret_key:str|None=None; token:str|None=None
 endpoint:str|None=None; bucket:str|None=None; use:str='library'; extra:dict=Field(default_factory=dict)
def safe_name(value):
    value=re.sub(r'[^A-Za-z0-9._-]+','-',str(value or '').strip()).strip('-')
    return value[:64] or ('target-'+uuid.uuid4().hex[:8])

def target_root(use): return BACKUPS if use=='backup' else SOURCES

def register_target(use,key,item):
    data=load_state(); bucket=data.setdefault('backup' if use=='backup' else 'library',{})
    bucket[key]=item; save_state(data); return item

def flatten_lsblk(nodes,out=None):
    out=out or []
    for n in nodes or []:
        out.append(n); flatten_lsblk(n.get('children'),out)
    return out

@app.get('/v1/usb')
def usb_list(authorization:str|None=Header(default=None)):
    auth(authorization)
    try:data=json.loads(run(['lsblk','-J','-b','-o','NAME,PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,RM,FSTYPE,LABEL,MOUNTPOINTS']))
    except Exception as exc: raise HTTPException(502,str(exc))
    rows=[]
    for n in flatten_lsblk(data.get('blockdevices')):
        if n.get('type') not in ('disk','part'): continue
        if n.get('tran')!='usb' and not n.get('rm'): continue
        if str(n.get('name','')).startswith('sr'): continue
        rows.append({k:n.get(k) for k in ('path','size','model','serial','tran','rm','fstype','label','mountpoints')})
    return {'devices':rows}
@app.get('/v1/network/discover')
def network_discover(authorization:str|None=Header(default=None)):
    auth(authorization); found=[]
    for service,kind in (('_smb._tcp','cifs'),('_nfs._tcp','nfs')):
        try: raw=run(['avahi-browse','-rtp',service],timeout=5)
        except Exception: continue
        for line in raw.splitlines():
            if not line.startswith('='): continue
            parts=line.split(';')
            if len(parts)<9: continue
            found.append({'kind':kind,'name':parts[3],'host':parts[7],'address':parts[8]})
    unique={(x['kind'],x['address']):x for x in found}
    return {'servers':list(unique.values())}

@app.post('/v1/network/shares')
def network_shares(item:NetworkRequest,authorization:str|None=Header(default=None)):
    auth(authorization); host=item.host
    try:
        if item.kind=='cifs':
            cmd=['smbclient','-g','-L',host]
            if item.username: cmd += ['-U',f'{item.username}%{item.password or ""}']
            else: cmd += ['-N']
            raw=run(cmd,timeout=12); shares=[]
            for line in raw.splitlines():
                p=line.split('|')
                if len(p)>=2 and p[0]=='Disk$': shares.append({'share':p[1]})
            return {'shares':shares}
        if item.kind=='nfs':
            raw=run(['showmount','-e',host],timeout=10)
            shares=[{'share':x.split()[0]} for x in raw.splitlines()[1:] if x.strip()]
            return {'shares':shares}
    except Exception as exc: raise HTTPException(502,str(exc))
    raise HTTPException(400,'kind must be cifs or nfs')
def temp_mount(source,kind,options=None):
    root=Path(tempfile.mkdtemp(prefix='surround-storage-'))
    cmd=['mount']
    if kind: cmd += ['-t',kind]
    if options: cmd += ['-o',options]
    cmd += [source,str(root)]
    run(cmd,timeout=15)
    try:
        entries=[x.name for x in list(root.iterdir())[:5]]
        return entries
    finally:
        subprocess.run(['umount',str(root)],capture_output=True,timeout=8)
        root.rmdir()

def permanent_mount(source,path,kind,options=None):
    path.mkdir(parents=True,exist_ok=True)
    cmd=['mount']
    if kind: cmd += ['-t',kind]
    if options: cmd += ['-o',options]
    cmd += [source,str(path)]
    run(cmd,timeout=20); return str(path)


def _managed_nfs_targets():
    data=load_state(); out=[]
    for bucket in ('library','backup'):
        for item in (data.get(bucket) or {}).values():
            if item.get('kind') != 'nfs': continue
            if not str(item.get('id') or '').startswith('SurroundCore-Storage-'): continue
            out.append(item)
    return out

def _restore_managed_nfs():
    pending=_managed_nfs_targets()
    for _ in range(45):
        left=[]
        for item in pending:
            path=Path(item.get('path') or '')
            if path and subprocess.run(['mountpoint','-q',str(path)]).returncode == 0:
                continue
            host=item.get('host'); share=item.get('share') or '/'
            if not host or not path: continue
            source=f'{host}:{share}'
            opts='rw,nosuid,nodev,noexec,vers=4.1,proto=tcp'
            try: permanent_mount(source,path,'nfs',opts)
            except Exception: left.append(item)
        if not left: return
        pending=left; time.sleep(2)

@app.on_event('startup')
def _startup_restore_managed_nfs():
    threading.Thread(target=_restore_managed_nfs,daemon=True,name='storage-remount').start()

@app.post('/v1/usb/connect')
def usb_connect(item:UsbRequest,authorization:str|None=Header(default=None)):
    auth(authorization); dev=Path(item.device)
    if not str(dev).startswith('/dev/'): raise HTTPException(400,'invalid device')
    try: sample=temp_mount(str(dev),'auto')
    except Exception as exc: raise HTTPException(502,f'USB test failed: {exc}')
    key=safe_name(item.name or dev.name); path=target_root(item.use)/key
    try: permanent_mount(str(dev),path,'auto')
    except Exception as exc: raise HTTPException(502,f'USB mount failed: {exc}')
    return register_target(item.use,key,{'id':key,'name':item.name or dev.name,'kind':'usb','path':str(path),'device':str(dev),'sample':sample})
@app.post('/v1/network/connect')
def network_connect(item:NetworkRequest,authorization:str|None=Header(default=None)):
    auth(authorization)
    if item.kind not in ('cifs','nfs') or not item.share: raise HTTPException(400,'share required')
    key=safe_name(item.name or f'{item.host}-{Path(item.share).name}')
    path=target_root(item.use)/key; opts=None; source=''
    cred=None
    if item.kind=='cifs':
        source=f'//{item.host}/{item.share.lstrip("/")}'
        if item.username:
            cred=DATA/'credentials';cred.mkdir(exist_ok=True); cf=cred/f'{key}.cred'
            cf.write_text(f'username={item.username}\npassword={item.password or ""}\n'+(f'domain={item.domain}\n' if item.domain else ''))
            os.chmod(cf,0o600); opts=f'credentials={cf},iocharset=utf8,vers=3.0'
        else: opts='guest,iocharset=utf8,vers=3.0'
    else:
        source=f'{item.host}:{item.share}'; opts=('rw' if item.writable else 'ro')+',nosuid,nodev,noexec,vers=4.1,proto=tcp'
    try: sample=temp_mount(source,item.kind,opts)
    except Exception as exc: raise HTTPException(502,f'Connection test failed: {exc}')
    try: permanent_mount(source,path,item.kind,opts)
    except Exception as exc: raise HTTPException(502,f'Mount failed: {exc}')
    return register_target(item.use,key,{'id':key,'name':item.name or item.share,'kind':item.kind,'path':str(path),'host':item.host,'share':item.share,'sample':sample,'writable':bool(item.writable)})

@app.get('/v1/cloud/providers')
def cloud_providers(authorization:str|None=Header(default=None)):
    auth(authorization); return {'providers':[{'id':k,**v} for k,v in CLOUDS.items()]}
def rclone_create(name,item,spec):
    cmd=['rclone','config','create',name,spec['rclone'],'--config',str(RCLONE),'--non-interactive']
    if item.provider=='mega':
        if not item.username or not item.password: raise RuntimeError('MEGA username and password required')
        obsc=run(['rclone','obscure',item.password]).strip(); cmd += ['user',item.username,'pass',obsc]
    elif item.provider=='icloud':
        if not item.username or not item.password: raise RuntimeError('Apple ID and app/session credential required')
        obsc=run(['rclone','obscure',item.password]).strip();cmd += ['apple_id',item.username,'password',obsc]
    elif item.provider in ('aws_s3','s3'):
        cmd += ['provider','AWS' if item.provider=='aws_s3' else 'Other','env_auth','false']
        if item.access_key: cmd += ['access_key_id',item.access_key]
        if item.secret_key: cmd += ['secret_access_key',item.secret_key]
        if item.endpoint: cmd += ['endpoint',item.endpoint]
    elif item.token:
        cmd += ['token',item.token]
    else:
        return {'authorization_required':True,'provider':item.provider,'remote':name,'auth':spec['auth']}
    run(cmd,timeout=20); os.chmod(RCLONE,0o600); return {'configured':True,'remote':name}

@app.post('/v1/cloud/connect')
def cloud_connect(item:CloudRequest,authorization:str|None=Header(default=None)):
    auth(authorization); spec=CLOUDS.get(item.provider)
    if not spec: raise HTTPException(400,'unknown cloud provider')
    key=safe_name(item.name or item.provider); remote='sc-'+key
    try: result=rclone_create(remote,item,spec)
    except Exception as exc: raise HTTPException(502,str(exc))
    if result.get('authorization_required'): return result
    root=(item.bucket or '').strip('/'); remote_path=f'{remote}:{root}'
    try: run(['rclone','lsd',remote_path,'--config',str(RCLONE),'--max-depth','1'],timeout=20)
    except Exception as exc: raise HTTPException(502,f'Cloud connection test failed: {exc}')
    path=target_root(item.use)/key;path.mkdir(parents=True,exist_ok=True)
    cmd=['rclone','mount',remote_path,str(path),'--config',str(RCLONE),'--daemon','--vfs-cache-mode','full','--dir-cache-time','5m']
    try: run(cmd,timeout=15)
    except Exception as exc: raise HTTPException(502,f'Cloud mount failed: {exc}')
    return register_target(item.use,key,{'id':key,'name':item.name or spec['name'],'kind':'cloud','provider':item.provider,'path':str(path),'remote':remote_path})
class CopyRequest(BaseModel):
 source:str; target_id:str; relative_path:str|None=None; delete_extra:bool=False

@app.get('/v1/targets')
def targets(authorization:str|None=Header(default=None)):
    auth(authorization); return load_state()

@app.post('/v1/copy')
def copy_to_backup(item:CopyRequest,authorization:str|None=Header(default=None)):
    auth(authorization); data=load_state(); target=data.get('backup',{}).get(item.target_id)
    if not target: raise HTTPException(404,'backup target not found')
    src=Path(item.source); dst=Path(target['path'])/(item.relative_path or src.name)
    if not src.exists(): raise HTTPException(404,'source not found')
    dst.parent.mkdir(parents=True,exist_ok=True)
    cmd=['rsync','-a','--info=stats2']
    if item.delete_extra: cmd.append('--delete')
    cmd += [str(src),str(dst)]
    try: out=run(cmd,timeout=3600)
    except Exception as exc: raise HTTPException(502,str(exc))
    return {'ok':True,'target':target,'destination':str(dst),'output':out[-2000:]}

@app.get('/v1/rclone/remotes')
def rclone_remotes(authorization:str|None=Header(default=None)):
    auth(authorization)
    if not RCLONE.exists(): return {'remotes':[]}
    try: names=[x.rstrip(':') for x in run(['rclone','listremotes','--config',str(RCLONE)]).splitlines() if x.strip()]
    except Exception as exc: raise HTTPException(502,str(exc))
    return {'remotes':names,'advanced':True}

@app.get('/v1/health')
def health(): return {'ok':True,'service':'SurroundCore Storage','version':'0.2'}

class DiskWipeRequest(BaseModel):
    device:str
    confirm:str
    allow_unhealthy:bool=False

class PoolCreateRequest(BaseModel):
    name:str
    devices:list[str]
    layout:str='mirror'
    allow_unhealthy:bool=False

class PoolDestroyRequest(BaseModel):
    confirm:str

def _lsblk_disks():
    raw=run(['lsblk','-J','-b','-d','-o','NAME,PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,ROTA,FSTYPE,MOUNTPOINTS'])
    return json.loads(raw).get('blockdevices') or []

def _system_block_paths():
    blocked=set()
    for target in ('/','/boot','/boot/efi'):
        p=subprocess.run(['findmnt','-rn','-o','SOURCE',target],capture_output=True,text=True)
        src=(p.stdout or '').strip()
        if src.startswith('/dev/'):
            blocked.add(os.path.realpath(src))
    return blocked

def _serial_from_provisioned_path(device):
    name=Path(device).name
    hits=[serial for serial in RAW_ALLOWED_SERIALS if serial and serial in name]
    return hits[0] if len(hits)==1 else None

def _smart(device):
    p=subprocess.run(['smartctl','-j','-i','-H','-A',device],capture_output=True,text=True)
    try: data=json.loads(p.stdout or '{}')
    except Exception: data={}
    passed=((data.get('smart_status') or {}).get('passed'))
    attrs={}
    for a in ((data.get('ata_smart_attributes') or {}).get('table') or []):
        try: attrs[int(a.get('id'))]=int(((a.get('raw') or {}).get('value')) or 0)
        except Exception: pass
    reallocated=attrs.get(5,0); pending=attrs.get(197,0); uncorrectable=attrs.get(198,0)
    available=bool(data.get('device') or data.get('model_name') or data.get('serial_number') or attrs or passed is not None)
    healthy=bool(available and passed is not False and reallocated==0 and pending==0 and uncorrectable==0)
    return {'available':available,'smart_passed':passed,'reallocated':reallocated,'pending':pending,
            'uncorrectable':uncorrectable,'healthy_for_new_pool':healthy,
            'error':None if available else ((p.stderr or '').strip() or 'SMART data unavailable')}

def _disk_row(device):
    supplied=str(device or '').strip()
    if supplied not in RAW_ALLOWED_DEVICES:
        raise HTTPException(403,'device is not provisioned as a SurroundCore data disk')
    path=Path(supplied)
    if not path.exists(): raise HTTPException(404,'provisioned data disk is not present')
    real=os.path.realpath(supplied)
    if real in _system_block_paths(): raise HTTPException(403,'system/boot disk is never a storage candidate')
    p=subprocess.run(['lsblk','-J','-b','-d','-o','NAME,PATH,TYPE,SIZE,MODEL,SERIAL,TRAN,ROTA,FSTYPE,MOUNTPOINTS',supplied],capture_output=True,text=True)
    try: rows=(json.loads(p.stdout or '{}').get('blockdevices') or [])
    except Exception: rows=[]
    if not rows: raise HTTPException(404,'data disk metadata unavailable')
    row=dict(rows[0])
    if row.get('type')!='disk': raise HTTPException(400,'provisioned device is not a whole disk')
    serial=str(row.get('serial') or '').strip() or _serial_from_provisioned_path(supplied)
    if RAW_ALLOWED_SERIALS and serial not in RAW_ALLOWED_SERIALS:
        raise HTTPException(403,'provisioned device identity does not match its expected serial')
    row['path']=supplied; row['real_path']=real; row['serial']=serial
    return row

def _signatures(device):
    p=subprocess.run(['wipefs','-n','-J',device],capture_output=True,text=True)
    try:
        sigs=(json.loads(p.stdout or '{}').get('signatures') or [])
        return [{'type':x.get('type'),'label':x.get('label'),'offset':x.get('offset')} for x in sigs]
    except Exception: return []

def _imported_pool_for(device):
    p=subprocess.run(['zpool','status','-P'],capture_output=True,text=True)
    real=os.path.realpath(device); supplied=str(device)
    return any(real in line or supplied in line for line in (p.stdout or '').splitlines())

@app.get('/v1/disks')
def raw_disks(authorization:str|None=Header(default=None)):
    auth(authorization); items=[]
    for device in sorted(RAW_ALLOWED_DEVICES):
        try: row=_disk_row(device)
        except HTTPException as exc:
            items.append({'path':device,'present':False,'eligible':False,'error':str(exc.detail)})
            continue
        item={k:row.get(k) for k in ('path','real_path','size','model','serial','tran','rota','fstype','mountpoints')}
        item['present']=True; item['smart']=_smart(item['path'])
        item['signatures']=_signatures(item['path'])
        item['in_imported_pool']=_imported_pool_for(item['path'])
        item['eligible']=not item['in_imported_pool']
        items.append(item)
    return {'devices':items,'boot_devices_hidden':True,'provisioned_count':len(RAW_ALLOWED_DEVICES)}

def _wipe_data_disk(device,allow_unhealthy=False):
    row=_disk_row(device); serial=str(row.get('serial') or '').strip(); smart=_smart(device)
    if not smart['healthy_for_new_pool'] and not allow_unhealthy:
        raise HTTPException(409,'SMART warnings block destructive use unless unhealthy-disk override is explicit')
    if _imported_pool_for(device): raise HTTPException(409,'device belongs to an imported ZFS pool')
    subprocess.run(['zpool','labelclear','-f',device],capture_output=True,text=True)
    for cmd in (['wipefs','-a','-f',device],['sgdisk','--zap-all',device]):
        p=subprocess.run(cmd,capture_output=True,text=True)
        if p.returncode: raise HTTPException(502,(p.stderr or p.stdout or 'disk wipe failed').strip())
    subprocess.run(['dd','if=/dev/zero',f'of={device}','bs=1M','count=16','conv=fsync'],capture_output=True,text=True)
    subprocess.run(['partprobe',device],capture_output=True,text=True)
    return {'ok':True,'device':device,'serial':serial,'wiped_at':time.time()}

@app.post('/v1/disks/wipe')
def wipe_disk(item:DiskWipeRequest,authorization:str|None=Header(default=None)):
    auth(authorization); row=_disk_row(item.device); serial=str(row.get('serial') or '').strip()
    if item.confirm != f'WIPE {serial}': raise HTTPException(400,f'confirmation must be WIPE {serial}')
    return _wipe_data_disk(item.device,item.allow_unhealthy)

def _pool_name(value):
    name=re.sub(r'[^A-Za-z0-9_.:-]+','-',str(value or '').strip()).strip('-')
    if not name or name.lower() in ('mirror','raidz','spare','log','cache'):
        raise HTTPException(400,'invalid ZFS pool name')
    return name[:48]

def _managed_pool_state():
    try: return json.loads(MANAGED_POOLS.read_text())
    except Exception: return {'pools':{}}

def _save_managed_pool(name,devices):
    data=_managed_pool_state(); data.setdefault('pools',{})[name]={'devices':list(devices),'created_at':time.time()}
    tmp=MANAGED_POOLS.with_suffix('.tmp'); tmp.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n'); os.replace(tmp,MANAGED_POOLS)

def _forget_managed_pool(name):
    data=_managed_pool_state(); data.setdefault('pools',{}).pop(name,None)
    tmp=MANAGED_POOLS.with_suffix('.tmp'); tmp.write_text(json.dumps(data,indent=2,sort_keys=True)+'\n'); os.replace(tmp,MANAGED_POOLS)

def _all_zpool_names():
    p=subprocess.run(['zpool','list','-H','-o','name'],capture_output=True,text=True)
    return {x.strip() for x in (p.stdout or '').splitlines() if x.strip()}

def _pool_rows():
    managed=_managed_pool_state().get('pools',{})
    if not managed: return []
    p=subprocess.run(['zpool','list','-Hp','-o','name,size,alloc,free,health'],capture_output=True,text=True)
    current={}
    if not p.returncode:
        for line in p.stdout.splitlines():
            cols=line.split('\t')
            if len(cols)>=5: current[cols[0]]=cols[1:5]
    out=[]
    for name,meta in managed.items():
        cols=current.get(name)
        root=str(POOL_ROOT/name); lib=str(Path(root)/'library')
        if cols:
            size,alloc,free,health=cols
            out.append({'name':name,'size':int(size),'allocated':int(alloc),'free':int(free),'health':health,
                        'root':root,'library_path':lib,'devices':meta.get('devices',[]),'online':True})
        else:
            out.append({'name':name,'size':0,'allocated':0,'free':0,'health':'OFFLINE',
                        'root':root,'library_path':lib,'devices':meta.get('devices',[]),'online':False})
    return out

@app.get('/v1/pools')
def pools(authorization:str|None=Header(default=None)):
    auth(authorization); return {'pools':_pool_rows()}

@app.post('/v1/pools')
def create_pool(item:PoolCreateRequest,authorization:str|None=Header(default=None)):
    auth(authorization); name=_pool_name(item.name)
    if item.layout!='mirror' or len(item.devices)!=2:
        raise HTTPException(400,'first release supports exactly two disks in a ZFS mirror')
    devices=[]
    for dev in item.devices:
        row=_disk_row(dev); smart=_smart(dev)
        if not smart['healthy_for_new_pool'] and not item.allow_unhealthy:
            raise HTTPException(409,f'{dev} has SMART warnings; explicit unhealthy-disk override required')
        if _imported_pool_for(dev): raise HTTPException(409,f'{dev} is already in an imported pool')
        devices.append(str(dev))
    if name in _all_zpool_names(): raise HTTPException(409,'ZFS pool name is already in use')
    for dev in devices:
        if _signatures(dev): raise HTTPException(409,f'{dev} still has filesystem/ZFS signatures; wipe it first')
    root=POOL_ROOT/name
    cmd=['zpool','create','-f','-o','ashift=12',
         '-O','compression=zstd','-O','atime=off','-O','xattr=sa',
         '-O','acltype=posixacl','-O',f'mountpoint={root}',name,'mirror',*devices]
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    if p.returncode: raise HTTPException(502,(p.stderr or p.stdout or 'zpool create failed').strip())
    _save_managed_pool(name,devices)
    for dataset,props in ((f'{name}/library',['-o','recordsize=1M']),
                          (f'{name}/staging',['-o','recordsize=1M'])):
        q=subprocess.run(['zfs','create',*props,dataset],capture_output=True,text=True)
        if q.returncode:
            subprocess.run(['zpool','destroy','-f',name],capture_output=True,text=True)
            _forget_managed_pool(name)
            raise HTTPException(502,(q.stderr or q.stdout or 'dataset create failed').strip())
    return {'ok':True,'pool':next(x for x in _pool_rows() if x['name']==name)}

@app.delete('/v1/pools/{name}')
def destroy_pool(name:str,item:PoolDestroyRequest,authorization:str|None=Header(default=None)):
    auth(authorization); name=_pool_name(name)
    if item.confirm != f'DESTROY {name}': raise HTTPException(400,f'confirmation must be DESTROY {name}')
    if name not in (_managed_pool_state().get('pools') or {}): raise HTTPException(404,'managed pool not found')
    p=subprocess.run(['zpool','destroy','-f',name],capture_output=True,text=True,timeout=60)
    if p.returncode: raise HTTPException(502,(p.stderr or p.stdout or 'zpool destroy failed').strip())
    _forget_managed_pool(name)
    return {'ok':True,'name':name}
