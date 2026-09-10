import hashlib, hmac, os, re, shutil
from pathlib import Path
import httpx
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile

app=FastAPI(title='SurroundCore ControlMac Ingest',version='0.1')
CORE_TOKEN=os.getenv('SURROUNDCORE_TOKEN','')
INGEST_TOKEN=os.getenv('SURROUNDCORE_CONTROLMAC_TOKEN','')
CORE=os.getenv('SURROUNDCORE_CORE_URL','http://127.0.0.1:8080').rstrip('/')
ROOT=Path(os.getenv('SURROUNDCORE_CONTROLMAC_LIBRARY','/library/controlmac'))
MAX_GB=float(os.getenv('SURROUNDCORE_CONTROLMAC_MAX_GB','0') or 0)


def auth(authorization):
    value=authorization or ''; supplied=value[7:] if value.startswith('Bearer ') else ''
    if not INGEST_TOKEN:
        raise HTTPException(503,'ControlMac ingest token is not configured')
    if not supplied or not hmac.compare_digest(INGEST_TOKEN,supplied):
        raise HTTPException(401,'unauthorised')

def safe(value,fallback='Unknown'):
    text=re.sub(r'[^A-Za-z0-9 ._()\[\]-]+','-',str(value or '')).strip(' .-')
    return text[:120] or fallback
def register(payload):
    try:
        with httpx.Client(timeout=30) as client:
            r=client.post(CORE+'/api/v1/controlmac/register',json=payload,
                headers={'Authorization':'Bearer '+CORE_TOKEN})
    except Exception as exc:
        raise HTTPException(502,f'Core registration failed: {exc}')
    try: data=r.json()
    except Exception: data={'detail':r.text}
    if r.status_code>=400:
        raise HTTPException(r.status_code,data.get('detail') or r.text)
    return data

@app.get('/v1/health')
def health():
    return {'ok':True,'service':'ControlMac ingest','port':8083}

@app.get('/v1/capabilities')
def capabilities(authorization:str|None=Header(default=None)):
    auth(authorization)
    return {'upload':True,'direct_to_core':True,'max_gb':MAX_GB or None,
            'origins':['cd_rip','dvd_rip','bluray_rip','minidisc_rip','file_import','vinyl_import'],
            'edition_grouping':True,'provenance':True}
@app.post('/v1/upload')
async def upload(file:UploadFile=File(...), artist:str=Form('Unknown Artist'), album:str=Form('Unknown Album'),
                 edition:str=Form('Standard edition'), media_type:str=Form('album'), title:str|None=Form(None),
                 origin:str=Form('controlmac_import'), disc_identifier:str|None=Form(None),
                 source_serial:str|None=Form(None), export_blocked:bool=Form(False),
                 authorization:str|None=Header(default=None)):
    auth(authorization); ROOT.mkdir(parents=True,exist_ok=True)
    folder=ROOT/safe(artist)/safe(album)/safe(edition); folder.mkdir(parents=True,exist_ok=True)
    name=safe(Path(file.filename or 'track.bin').name,'track.bin'); dst=folder/name; tmp=dst.with_suffix(dst.suffix+'.part')
    h=hashlib.sha256(); total=0; limit=int(MAX_GB*1024**3) if MAX_GB>0 else 0
    try:
        with tmp.open('wb') as out:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk: break
                total+=len(chunk)
                if limit and total>limit: raise HTTPException(413,'upload exceeds configured limit')
                h.update(chunk); out.write(chunk)
        os.replace(tmp,dst)
    finally:
        if tmp.exists(): tmp.unlink(missing_ok=True)
    payload={'path':str(dst),'artist':artist,'album':album,'edition':edition,'media_type':media_type,
             'title':title or Path(name).stem,'origin':origin,'disc_identifier':disc_identifier,
             'source_serial':source_serial,'content_hash':h.hexdigest(),'export_blocked':export_blocked,
             'bytes':total}
    result=register(payload)
    return {'ok':True,'stored':str(dst),'bytes':total,'sha256':h.hexdigest(),'core':result}

@app.post('/v1/register-existing')
def register_existing(payload:dict,authorization:str|None=Header(default=None)):
    auth(authorization)
    path=Path(str(payload.get('path') or ''))
    if not path.is_file() or ROOT not in path.parents:
        raise HTTPException(404,'file is not inside ControlMac ingest library')
    if not payload.get('content_hash'):
        h=hashlib.sha256()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
        payload['content_hash']=h.hexdigest()
    return register(payload)
