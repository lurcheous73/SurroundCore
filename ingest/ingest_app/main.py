import hmac
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import engine, jobs
from .webui import html

TOKEN=os.getenv('SURROUNDCORE_TOKEN','')
MAX_UPLOAD_GB=float(os.getenv('SURROUNDCORE_INGEST_MAX_UPLOAD_GB','0') or 0)
ALLOWED={'.iso','.bin','.cue'}

@asynccontextmanager
async def lifespan(app):
    jobs.start()
    yield
    jobs.stop()

app=FastAPI(title='SurroundCore Ingest',version='0.5.0-dev',lifespan=lifespan)

class RipRequest(BaseModel):
    device: str
    force: bool=False


def require_token(authorization=None):
    supplied=authorization[7:] if authorization and authorization.startswith('Bearer ') else ''
    if not TOKEN or not hmac.compare_digest(TOKEN,supplied): raise HTTPException(401,'Invalid SurroundCore token')

@app.get('/api/v1/ingest/health')
def health(): return {'ok':True,'service':'SurroundCore Ingest','version':'0.5.0-dev'}

@app.get('/ingest',response_class=HTMLResponse)
def page(): return HTMLResponse(html())

@app.get('/api/v1/ingest/capabilities')
def capabilities(authorization: str|None=Header(default=None)):
    require_token(authorization)
    return {'audio_only':True,'auto_rip':jobs.AUTO_RIP,'auto_netmd':jobs.AUTO_NETMD,
            'eject_after_success':jobs.AUTO_EJECT,'makemkv':engine.makemkv_available(),
            'netmd_helper':engine.netmd_available(),'formats':['audio_cd','dvd_video_audio','dvd_audio','bluray','iso','bin_cue','netmd'],
            'output':'verified FLAC at source sample rate/channel layout'}

@app.get('/api/v1/ingest/devices')
def devices(authorization: str|None=Header(default=None)):
    require_token(authorization)
    # Hardware enumeration must stay fast. Deep media/NetMD probes are explicit
    # endpoints because real optical/USB stacks can block for many seconds.
    optical=[]
    for item in engine.optical_devices():
        row=dict(item); row['media']={'state':'not_probed'}; optical.append(row)
    return {'optical':optical,'netmd':{'helper_available':engine.netmd_available(),'state':'not_probed'}}

@app.get('/api/v1/ingest/optical/status')
def optical_status(device:str,authorization: str|None=Header(default=None)):
    require_token(authorization)
    allowed={d['device'] for d in engine.optical_devices()}
    if device not in allowed: raise HTTPException(404,'Optical drive not found')
    try:
        with engine.media_lock(): media=engine.media_identity(device)
    except Exception as exc: return {'device':device,'media':{'error':str(exc)}}
    return {'device':device,'media':media or {'state':'empty_or_unreadable'}}

@app.get('/api/v1/ingest/netmd/status')
def netmd_status(authorization: str|None=Header(default=None)):
    require_token(authorization)
    if not engine.netmd_available(): return {'available':False,'reason':'NetMD helper not installed'}
    try:
        with engine.media_lock(): return engine.netmd_status()
    except Exception as exc:
        return {'available':False,'reason':str(exc)}

@app.get('/api/v1/ingest/jobs')
def list_jobs(authorization: str|None=Header(default=None)):
    require_token(authorization); return {'jobs':jobs.list_jobs()}

@app.get('/api/v1/ingest/jobs/{job_id}')
def get_job(job_id:str,authorization: str|None=Header(default=None)):
    require_token(authorization); job=jobs.get_job(job_id)
    if not job: raise HTTPException(404,'Ingest job not found')
    return job

@app.post('/api/v1/ingest/rip')
def rip(req:RipRequest,authorization: str|None=Header(default=None)):
    require_token(authorization)
    allowed={d['device'] for d in engine.optical_devices()}
    if req.device not in allowed: raise HTTPException(404,'Optical drive not found')
    job=jobs.enqueue('physical',req.device,req.device,'',force=True)
    return {'queued':bool(job),'job':job,'already_completed':job is None}

@app.post('/api/v1/ingest/netmd')
def rip_md(authorization: str|None=Header(default=None)):
    require_token(authorization)
    job=jobs.enqueue('netmd','netmd','MiniDisc','',force=True)
    return {'queued':bool(job),'job':job}


def safe_upload_name(name):
    base=Path(name or '').name
    if not base or Path(base).suffix.lower() not in ALLOWED: raise HTTPException(400,'Only ISO, BIN and CUE files are accepted')
    return engine.safe_name(Path(base).stem,'upload')+Path(base).suffix.lower()

@app.post('/api/v1/ingest/upload')
async def upload(files:list[UploadFile]=File(...),authorization: str|None=Header(default=None)):
    require_token(authorization)
    saved=[]; max_bytes=int(MAX_UPLOAD_GB*1024**3) if MAX_UPLOAD_GB>0 else 0
    for incoming in files:
        name=safe_upload_name(incoming.filename); dst=jobs.UPLOADS/name; tmp=dst.with_suffix(dst.suffix+'.part'); size=0
        try:
            with tmp.open('wb') as out:
                while True:
                    chunk=await incoming.read(4*1024*1024)
                    if not chunk: break
                    size+=len(chunk)
                    if max_bytes and size>max_bytes: raise HTTPException(413,'Upload exceeds configured size limit')
                    out.write(chunk)
            tmp.replace(dst); saved.append(dst)
        except Exception:
            tmp.unlink(missing_ok=True); raise
        finally:
            await incoming.close()
    queued=[]; seen=set()
    for path in saved:
        primary=path.with_suffix('.bin') if path.suffix=='.cue' else path
        if primary.suffix=='.bin' and primary.with_suffix('.cue').is_file(): pass
        elif path.suffix=='.cue': continue
        if primary in seen: continue
        seen.add(primary); fp=jobs.hash_file_identity(primary)
        job=jobs.enqueue(primary.suffix.lstrip('.'),str(primary),primary.stem,fp,force=True)
        if job: queued.append(job)
    return {'saved':[p.name for p in saved],'jobs':queued}
