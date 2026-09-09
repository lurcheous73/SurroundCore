import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path

from . import engine

STATE = engine.INGEST_ROOT / 'state' / 'jobs.json'
UPLOADS = engine.INGEST_ROOT / 'uploads'
AUTO_RIP = os.getenv('SURROUNDCORE_AUTO_RIP', 'true').lower() not in ('0','false','no')
AUTO_NETMD = os.getenv('SURROUNDCORE_AUTO_NETMD', 'true').lower() not in ('0','false','no')
AUTO_EJECT = os.getenv('SURROUNDCORE_INGEST_EJECT', 'true').lower() not in ('0','false','no')
KEEP_UPLOADS = os.getenv('SURROUNDCORE_INGEST_KEEP_UPLOADS', 'false').lower() in ('1','true','yes')
POLL_SECONDS = max(3, int(os.getenv('SURROUNDCORE_INGEST_POLL_SECONDS', '8')))

_lock = threading.RLock()
_jobs = {}
_q = queue.Queue()
_started = False
_stop = threading.Event()


def _now(): return int(time.time())


def _save():
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp=STATE.with_suffix('.tmp'); tmp.write_text(json.dumps(list(_jobs.values()),indent=2,sort_keys=True)+'\n'); tmp.replace(STATE)


def _load():
    if not STATE.is_file(): return
    try:
        for job in json.loads(STATE.read_text()):
            if job.get('status') in ('queued','running'): job['status']='interrupted'
            _jobs[job['id']]=job
    except Exception: pass


def list_jobs():
    with _lock: return sorted((dict(x) for x in _jobs.values()), key=lambda j:j.get('created',0), reverse=True)


def get_job(job_id):
    with _lock: return dict(_jobs[job_id]) if job_id in _jobs else None


def completed_fingerprints():
    with _lock:
        return {j.get('fingerprint') for j in _jobs.values() if j.get('status')=='done' and j.get('fingerprint')}


def active_key(kind, source, fingerprint=''):
    with _lock:
        for j in _jobs.values():
            if j.get('status') not in ('queued','running'): continue
            if fingerprint and j.get('fingerprint') == fingerprint: return True
            if j.get('kind') == kind and j.get('source') == source: return True
    return False


def enqueue(kind, source, label=None, fingerprint='', force=False):
    if not force and fingerprint and fingerprint in completed_fingerprints(): return None
    if active_key(kind, source, fingerprint): return None
    job={'id':uuid.uuid4().hex[:12],'kind':kind,'source':source,'label':label or '',
         'fingerprint':fingerprint or '','status':'queued','created':_now(),'updated':_now(),
         'result':None,'error':'','core_sync':None}
    with _lock: _jobs[job['id']]=job; _save()
    _q.put(job['id']); return dict(job)


def _update(job_id, **values):
    with _lock:
        if job_id not in _jobs: return
        _jobs[job_id].update(values); _jobs[job_id]['updated']=_now(); _save()


def _process(job):
    kind, source = job['kind'], job['source']
    if kind == 'physical':
        result=engine.process_physical(source)
        if AUTO_EJECT: engine.eject(source)
        return result
    if kind == 'netmd': return engine.rip_netmd()
    path=Path(source)
    if kind == 'iso' or path.suffix.lower() == '.iso': return engine.process_iso(path, job.get('label') or path.stem)
    if kind == 'bin' or path.suffix.lower() == '.bin':
        cue=path.with_suffix('.cue'); return engine.process_bin(path, cue if cue.is_file() else None, job.get('label') or path.stem)
    if path.suffix.lower() == '.cue':
        binpath=path.with_suffix('.bin')
        if not binpath.is_file(): raise RuntimeError('CUE uploaded without matching BIN')
        return engine.process_bin(binpath, path, job.get('label') or binpath.stem)
    raise RuntimeError(f'Unsupported ingest file: {path.name}')


def _worker():
    while not _stop.is_set():
        try: job_id=_q.get(timeout=1)
        except queue.Empty: continue
        job=get_job(job_id)
        if not job: continue
        _update(job_id,status='running',error='')
        try:
            result=_process(job); sync=None
            try: sync=engine.notify_core()
            except Exception as exc: sync={'ok':False,'error':str(exc)}
            _update(job_id,status='done',result=result,core_sync=sync)
            if not KEEP_UPLOADS and job.get('kind') in ('iso','bin','cue','upload'):
                p=Path(job.get('source',''))
                try:
                    if p.is_file() and UPLOADS in p.parents: p.unlink()
                    pair=p.with_suffix('.cue' if p.suffix.lower()=='.bin' else '.bin')
                    if pair.is_file() and UPLOADS in pair.parents: pair.unlink()
                except OSError: pass
        except Exception as exc:
            _update(job_id,status='failed',error=str(exc))
        finally: _q.task_done()


def _scan_uploads():
    for path in sorted(UPLOADS.iterdir() if UPLOADS.exists() else []):
        if path.suffix.lower() not in ('.iso','.bin','.cue'): continue
        if path.suffix.lower() == '.cue' and not path.with_suffix('.bin').is_file(): continue
        if path.suffix.lower() == '.bin' and path.with_suffix('.cue').is_file():
            primary=path
        elif path.suffix.lower() == '.cue':
            primary=path.with_suffix('.bin')
        else: primary=path
        fp=hash_file_identity(primary)
        enqueue(primary.suffix.lower().lstrip('.'), str(primary), primary.stem, fp)


def hash_file_identity(path):
    stat=Path(path).stat(); raw=f'{Path(path).name}|{stat.st_size}|{stat.st_mtime_ns}'
    import hashlib
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _monitor():
    last_netmd=''
    while not _stop.wait(POLL_SECONDS):
        try: _scan_uploads()
        except Exception: pass
        if AUTO_RIP:
            for dev in engine.optical_devices():
                try: identity=engine.media_identity(dev['device'])
                except Exception: identity=None
                if identity and identity.get('fingerprint'):
                    enqueue('physical',dev['device'],dev.get('name'),identity['fingerprint'])
        if AUTO_NETMD and engine.netmd_available():
            try:
                status=engine.netmd_status(); fp=status.get('fingerprint','') if status.get('available') else ''
                if fp and fp != last_netmd and status.get('rippable', True):
                    enqueue('netmd','netmd',status.get('disc_title') or 'MiniDisc',fp); last_netmd=fp
                if not fp: last_netmd=''
            except Exception: pass


def start():
    global _started
    with _lock:
        if _started: return
        _load(); _started=True; _stop.clear()
        threading.Thread(target=_worker,name='surround-ingest-worker',daemon=True).start()
        threading.Thread(target=_monitor,name='surround-ingest-monitor',daemon=True).start()


def stop(): _stop.set()
