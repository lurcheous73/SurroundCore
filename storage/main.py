import json, os, re, shutil, subprocess, tempfile, uuid
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

app=FastAPI(title='SurroundCore Storage',version='0.1')
TOKEN=os.getenv('SURROUNDCORE_TOKEN','')
DATA=Path(os.getenv('SURROUNDCORE_STORAGE_DATA','/data/storage'))
SOURCES=Path(os.getenv('SURROUNDCORE_SOURCES','/sources'))
BACKUPS=Path(os.getenv('SURROUNDCORE_BACKUPS','/backups'))
RCLONE=DATA/'rclone.conf'
STATE=DATA/'targets.json'
for p in (DATA,SOURCES,BACKUPS): p.mkdir(parents=True,exist_ok=True)

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
 domain:str|None=None; name:str|None=None; use:str='library'
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
        source=f'{item.host}:{item.share}'; opts='ro,nosuid,nodev'
    try: sample=temp_mount(source,item.kind,opts)
    except Exception as exc: raise HTTPException(502,f'Connection test failed: {exc}')
    try: permanent_mount(source,path,item.kind,opts)
    except Exception as exc: raise HTTPException(502,f'Mount failed: {exc}')
    return register_target(item.use,key,{'id':key,'name':item.name or item.share,'kind':item.kind,'path':str(path),'host':item.host,'share':item.share,'sample':sample})

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
def health(): return {'ok':True,'service':'SurroundCore Storage','version':'0.1'}
