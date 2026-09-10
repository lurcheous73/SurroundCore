import os, subprocess
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

app=FastAPI(title='SurroundCore Meridian Bridge',version='0.1')
TOKEN=os.getenv('SURROUNDCORE_TOKEN','')
CORE=os.getenv('SURROUNDCORE_MERIDIAN_CORE','').strip()
TOOL='/app/BridgeTool.exe'


def auth(value):
    supplied=(value or '').removeprefix('Bearer ').strip()
    if not TOKEN or supplied != TOKEN: raise HTTPException(401,'unauthorised')


def invoke(*args,timeout=25):
    if not CORE: raise HTTPException(503,'Meridian/Sooloos Core host not configured')
    cmd=['mono',TOOL,*map(str,args)]
    env=os.environ.copy()
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout,env=env)
    if p.returncode:
        msg=(p.stderr or p.stdout or 'Meridian helper failed').strip()
        raise HTTPException(502,msg[-1800:])
    return p.stdout
def parse_zones(text):
    zones=[]
    for line in text.splitlines():
        p=line.split('\t')
        if len(p)>=10 and p[0]=='CMZONE':
            zones.append({'zone_id':p[1],'name':p[2],'state':p[3],
                          'volume':int(p[4]),'volume_min':int(p[5]),'volume_max':int(p[6]),
                          'muted':p[7].lower()=='true','title':p[8],'subtitle':p[9]})
    return zones

class WakeIn(BaseModel): zone_id:str; source_index:int=2
class TransportIn(BaseModel): zone_id:str; action:str
class VolumeIn(BaseModel): zone_id:str; volume:int
class MuteIn(BaseModel): zone_id:str; muted:bool
class PairIn(BaseModel): zone_id:str; paired:bool

@app.get('/v1/status')
def status(authorization:str|None=Header(default=None)):
    auth(authorization)
    return {'ok':True,'core':CORE,'zones':parse_zones(invoke('status',CORE))}

@app.post('/v1/wake')
def wake(item:WakeIn,authorization:str|None=Header(default=None)):
    auth(authorization); out=invoke('wake',CORE,item.zone_id,item.source_index)
    return {'ok':True,'zone_id':item.zone_id,'source_index':item.source_index,'confirmed':'confirmed' in out}
@app.post('/v1/transport')
def transport(item:TransportIn,authorization:str|None=Header(default=None)):
    auth(authorization); invoke('transport',CORE,item.zone_id,item.action)
    return {'ok':True,'zone_id':item.zone_id,'action':item.action}

@app.post('/v1/volume')
def volume(item:VolumeIn,authorization:str|None=Header(default=None)):
    auth(authorization); invoke('volume',CORE,item.zone_id,item.volume)
    return {'ok':True,'zone_id':item.zone_id,'volume':item.volume}

@app.post('/v1/mute')
def mute(item:MuteIn,authorization:str|None=Header(default=None)):
    auth(authorization); invoke('mute',CORE,item.zone_id,item.muted)
    return {'ok':True,'zone_id':item.zone_id,'muted':item.muted}

@app.post('/v1/pair')
def pair(item:PairIn,authorization:str|None=Header(default=None)):
    auth(authorization); invoke('pair',CORE,item.zone_id,item.paired)
    return {'ok':True,'zone_id':item.zone_id,'paired':item.paired}

@app.get('/v1/health')
def health():
    return {'ok':True,'service':'SurroundCore Meridian Bridge','configured':bool(CORE)}
