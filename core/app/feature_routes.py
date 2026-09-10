import hashlib, hmac, os
import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from . import catalog, recordings, userauth, metadata_connectors, output_profiles

router=APIRouter(prefix='/api/v1')
STORAGE_URL=os.getenv('SURROUNDCORE_STORAGE_URL','http://127.0.0.1:8084').rstrip('/')


def _supplied(authorization):
    return authorization[7:] if authorization and authorization.startswith('Bearer ') else ''


def _user(authorization):
    token=_supplied(authorization); master=os.getenv('SURROUNDCORE_TOKEN','')
    if master and token and hmac.compare_digest(master,token):
        return {'id':0,'username':'core-admin','role':'admin','master':True}
    user=userauth.session_user(token)
    if user: return dict(user)|{'master':False}
    raise HTTPException(401,'Invalid or expired SurroundCore session')


def _admin(authorization):
    user=_user(authorization)
    if user.get('role')!='admin': raise HTTPException(403,'Administrator access required')
    return user
def _storage(method,path,payload=None):
    token=os.getenv('SURROUNDCORE_TOKEN','')
    try:
        with httpx.Client(timeout=35) as client:
            r=client.request(method,STORAGE_URL+path,json=payload,headers={'Authorization':'Bearer '+token})
    except Exception as exc:
        raise HTTPException(502,f'Storage helper unavailable: {exc}')
    try: data=r.json()
    except Exception: data={'detail':r.text}
    if r.status_code>=400: raise HTTPException(r.status_code,data.get('detail') or data.get('error') or r.text)
    return data

class PlaylistInput(BaseModel):
    name:str; items:list=Field(default_factory=list); kind:str='playlist'; metadata:dict=Field(default_factory=dict)

class RadioRecordInput(BaseModel):
    station:dict; cassette_type:str='I'

class CaptureInput(BaseModel):
    device:str; media_type:str='album'; name:str='Untitled'; sample_rate:int=96000; bit_depth:int=24

class ImportRecordingInput(BaseModel):
    path:str; media_type:str='album'; name:str|None=None; origin:str='usb_import'; export_blocked:bool=False

class OutputProfileInput(BaseModel):
    name:str|None=None; transport:str|None=None; settings:dict|None=None
@router.get('/catalog/albums')
def albums(authorization:str|None=Header(default=None)):
    _user(authorization); return {'albums':catalog.album_catalog()}

@router.get('/history')
def playback_history(limit:int=500,authorization:str|None=Header(default=None)):
    user=_user(authorization); uid=None if user.get('role')=='admin' else user.get('id')
    return {'history':catalog.history(limit,user_id=uid)}

@router.get('/playlists')
def playlists(authorization:str|None=Header(default=None)):
    _user(authorization); return {'playlists':catalog.list_playlists()}

@router.post('/playlists')
def playlist_save(item:PlaylistInput,authorization:str|None=Header(default=None)):
    user=_user(authorization)
    pid=catalog.save_playlist(item.name,item.items,item.kind,None if user.get('master') else user.get('id'),item.metadata)
    return {'ok':True,'id':pid}

@router.get('/metadata/providers')
def metadata_providers(authorization:str|None=Header(default=None)):
    _user(authorization); return {'providers':metadata_connectors.catalog()}

@router.get('/recordings/capabilities')
def recording_capabilities(authorization:str|None=Header(default=None)):
    _user(authorization); return recordings.capabilities()
@router.get('/recordings')
def recording_list(kind:str|None=None,authorization:str|None=Header(default=None)):
    _user(authorization); return {'recordings':recordings.recordings(kind),'active':recordings.sessions()}

@router.post('/recordings/radio/start')
def recording_radio_start(item:RadioRecordInput,authorization:str|None=Header(default=None)):
    _admin(authorization)
    try: return recordings.start_radio(item.station,item.cassette_type)
    except Exception as exc: raise HTTPException(502,str(exc))

@router.post('/recordings/capture/start')
def recording_capture_start(item:CaptureInput,authorization:str|None=Header(default=None)):
    _admin(authorization)
    try: return recordings.start_capture(item.device,item.media_type,item.name,item.sample_rate,item.bit_depth)
    except Exception as exc: raise HTTPException(502,str(exc))

@router.post('/recordings/{recording_id}/stop')
def recording_stop(recording_id:str,authorization:str|None=Header(default=None)):
    _admin(authorization)
    try: return recordings.stop(recording_id)
    except KeyError as exc: raise HTTPException(404,str(exc))
    except Exception as exc: raise HTTPException(502,str(exc))

@router.post('/recordings/import')
def recording_import(item:ImportRecordingInput,authorization:str|None=Header(default=None)):
    _admin(authorization)
    try: return {'ok':True,'media':recordings.import_recording(item.path,item.media_type,item.name,item.origin,item.export_blocked)}
    except FileNotFoundError: raise HTTPException(404,'Import file not found')
@router.get('/output-profiles')
def output_profile_list(authorization:str|None=Header(default=None)):
    _admin(authorization); return {'profiles':output_profiles.all_profiles()}

@router.put('/output-profiles/{profile_key}')
def output_profile_save(profile_key:str,item:OutputProfileInput,authorization:str|None=Header(default=None)):
    _admin(authorization)
    return {'ok':True,'profile':output_profiles.save_profile(profile_key,item.name,item.transport,item.settings)}

@router.get('/storage/devices')
def storage_devices(authorization:str|None=Header(default=None)):
    _admin(authorization); return _storage('GET','/v1/usb')

@router.get('/storage/network/discover')
def storage_network(authorization:str|None=Header(default=None)):
    _admin(authorization); return _storage('GET','/v1/network/discover')

@router.post('/storage/network/shares')
def storage_network_shares(payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization); return _storage('POST','/v1/network/shares',payload)

@router.get('/storage/cloud/providers')
def storage_clouds(authorization:str|None=Header(default=None)):
    _admin(authorization); return _storage('GET','/v1/cloud/providers')

@router.get('/storage/targets')
def storage_targets(authorization:str|None=Header(default=None)):
    _admin(authorization); return _storage('GET','/v1/targets')

@router.get('/storage/rclone/remotes')
def storage_rclone(authorization:str|None=Header(default=None)):
    _admin(authorization); return _storage('GET','/v1/rclone/remotes')
@router.post('/storage/usb/connect')
def storage_usb_connect(payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization); return _storage('POST','/v1/usb/connect',payload)

@router.post('/storage/network/connect')
def storage_share_connect(payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization); return _storage('POST','/v1/network/connect',payload)

@router.post('/storage/cloud/connect')
def storage_cloud_connect(payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization); return _storage('POST','/v1/cloud/connect',payload)

@router.post('/storage/copy')
def storage_copy(payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization); return _storage('POST','/v1/copy',payload)
class ControlMacRegisterInput(BaseModel):
    path:str
    artist:str='Unknown Artist'
    album:str='Unknown Album'
    edition:str='Standard edition'
    media_type:str='album'
    title:str|None=None
    origin:str='controlmac_import'
    disc_identifier:str|None=None
    source_serial:str|None=None
    content_hash:str|None=None
    export_blocked:bool=False
    bytes:int|None=None

@router.post('/controlmac/register')
def controlmac_register(item:ControlMacRegisterInput,authorization:str|None=Header(default=None)):
    _admin(authorization)
    media=recordings.import_recording(item.path,item.media_type,item.title,item.origin,item.export_blocked,
        item.artist,item.album,item.edition,item.disc_identifier,item.source_serial,item.content_hash)
    if not media: raise HTTPException(502,'uploaded media could not be indexed')
    return {'ok':True,'media_id':media.get('id'),'album':item.album,'edition':item.edition,
            'origin':item.origin,'content_hash':item.content_hash}
