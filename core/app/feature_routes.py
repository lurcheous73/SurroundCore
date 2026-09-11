import hashlib, hmac, os
import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from . import catalog, recordings, userauth, metadata_connectors, metadata_settings, output_profiles

router=APIRouter(prefix='/api/v1')
STORAGE_URL=os.getenv('SURROUNDCORE_STORAGE_URL','http://127.0.0.1:8084').rstrip('/')
MERIDIAN_URL=os.getenv('SURROUNDCORE_MERIDIAN_URL','http://127.0.0.1:8091').rstrip('/')
HARDWARE_URL=os.getenv('SURROUNDCORE_HARDWARE_URL','').rstrip('/')
NODE_URL=os.getenv('SURROUNDCORE_NODE_URL','http://127.0.0.1:8094').rstrip('/')


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


def _hardware(path='/v1/hardware'):
    if not HARDWARE_URL: return {'mode':'unconfigured','storage_modules':[],'devices':[]}
    token=os.getenv('SURROUNDCORE_TOKEN','')
    try:
        with httpx.Client(timeout=8) as client:
            r=client.get(HARDWARE_URL+path,headers={'Authorization':'Bearer '+token})
    except Exception as exc:
        raise HTTPException(502,f'Hardware helper unavailable: {exc}')
    try: data=r.json()
    except Exception: data={'detail':r.text}
    if r.status_code>=400: raise HTTPException(r.status_code,data.get('detail') or r.text)
    return data


def _node(method,path,payload=None):
    token=os.getenv('SURROUNDCORE_NODE_TOKEN','')
    if not token: raise HTTPException(503,'Core Systems service is not configured')
    try:
        with httpx.Client(timeout=120) as client:
            r=client.request(method,NODE_URL+path,json=payload,
                             headers={'Authorization':'Bearer '+token})
    except Exception as exc:
        raise HTTPException(502,f'Core Systems service unavailable: {exc}')
    try: data=r.json()
    except Exception: data={'detail':r.text}
    if r.status_code>=400:
        raise HTTPException(r.status_code,data.get('detail') or data.get('error') or r.text)
    return data

def _register_library_target(item,payload):
    if str((payload or {}).get('use') or 'library')!='library' or not item.get('path'):
        return item
    from .db import save_source
    sid='storage-'+str(item.get('id') or hashlib.sha1(item['path'].encode()).hexdigest()[:16])
    save_source({'id':sid,'name':item.get('name') or sid,'kind':item.get('kind') or 'local',
                 'path':item['path'],'cache_policy':'off','config':{'storage_target':item.get('id')},'enabled':True})
    item=dict(item); item['library_source_id']=sid
    return item


def _meridian(method,path,payload=None):
    token=os.getenv('SURROUNDCORE_TOKEN','')
    try:
        with httpx.Client(timeout=30) as client:
            r=client.request(method,MERIDIAN_URL+path,json=payload,headers={'Authorization':'Bearer '+token})
    except Exception as exc:
        raise HTTPException(502,f'Meridian helper unavailable: {exc}')
    try: data=r.json()
    except Exception: data={'detail':r.text}
    if r.status_code>=400: raise HTTPException(r.status_code,data.get('detail') or r.text)
    return data

class PlaylistInput(BaseModel):
    name:str; items:list=Field(default_factory=list); kind:str='playlist'; metadata:dict=Field(default_factory=dict)

class RadioRecordInput(BaseModel):
    station:dict; cassette_type:str='I'

class CaptureInput(BaseModel):
    device:str; media_type:str='album'; name:str='Untitled'; sample_rate:int=96000; bit_depth:int=24; input_role:str='line'

class ImportRecordingInput(BaseModel):
    path:str; media_type:str='album'; name:str|None=None; origin:str='usb_import'; export_blocked:bool=False

class OutputProfileInput(BaseModel):
    name:str|None=None; transport:str|None=None; settings:dict|None=None

class MetadataProviderInput(BaseModel):
    enabled:bool|None=None
    secret:str|None=None
    clear_secret:bool=False

class CoreRoleInput(BaseModel):
    role:str
    storage_mode:str='local'

class CoreJoinInput(BaseModel):
    primary_url:str
    code:str
    name:str|None=None
    callback_url:str|None=None

class MediaExportInput(BaseModel):
    target_id:str
    relative_path:str|None=None
    delete_extra:bool=False
@router.get('/core-systems')
def core_systems(authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('GET','/v1/system')

@router.post('/core-systems/role')
def core_systems_role(item:CoreRoleInput,authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('POST','/v1/configure/role',item.model_dump())

@router.post('/core-systems/join-code')
def core_systems_join_code(authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('POST','/v1/join-codes',{})

@router.post('/core-systems/join')
def core_systems_join(item:CoreJoinInput,authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('POST','/v1/configure/join',item.model_dump())

@router.post('/core-systems/sync')
def core_systems_sync(authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('POST','/v1/replication/run',{})


@router.get('/core-systems/maintenance')
def core_systems_maintenance(authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('GET','/v1/maintenance/local')

@router.get('/core-systems/nodes/{node_id}/maintenance')
def core_node_maintenance(node_id:str,authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('GET',f'/v1/nodes/{node_id}/maintenance/status')

@router.get('/core-systems/nodes/{node_id}/storage/disks')
def core_node_storage_disks(node_id:str,authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('GET',f'/v1/nodes/{node_id}/storage/disks')

@router.get('/core-systems/nodes/{node_id}/storage/pools')
def core_node_storage_pools(node_id:str,authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('GET',f'/v1/nodes/{node_id}/storage/pools')

@router.post('/core-systems/nodes/{node_id}/storage/disks/wipe')
def core_node_storage_wipe(node_id:str,payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('POST',f'/v1/nodes/{node_id}/storage/disks/wipe',payload)

@router.post('/core-systems/nodes/{node_id}/storage/pools')
def core_node_storage_pool_create(node_id:str,payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('POST',f'/v1/nodes/{node_id}/storage/pools',payload)

@router.delete('/core-systems/nodes/{node_id}/storage/pools/{name}')
def core_node_storage_pool_destroy(node_id:str,name:str,payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _node('DELETE',f'/v1/nodes/{node_id}/storage/pools/{name}',payload)

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

@router.get('/metadata/settings')
def metadata_settings_get(authorization:str|None=Header(default=None)):
    _admin(authorization); return metadata_settings.public_settings()

@router.put('/metadata/settings/{provider_id}')
def metadata_settings_put(provider_id:str,item:MetadataProviderInput,authorization:str|None=Header(default=None)):
    _admin(authorization)
    try: return {'ok':True,'provider':metadata_settings.configure(provider_id,item.enabled,item.secret,item.clear_secret)}
    except ValueError as exc: raise HTTPException(400,str(exc))

@router.get('/metadata/artwork-candidates')
def metadata_artwork_candidates(artist:str='',album:str='',limit:int=20,authorization:str|None=Header(default=None)):
    _user(authorization)
    try: return metadata_connectors.artwork_candidates(artist,album,limit)
    except ValueError as exc: raise HTTPException(400,str(exc))
    except Exception as exc: raise HTTPException(502,f'Artwork lookup failed: {exc}')

@router.get('/metadata/candidates')
def metadata_candidates(artist:str,album:str,tracks:str='',limit:int=12,authorization:str|None=Header(default=None)):
    _user(authorization)
    titles=[x.strip() for x in tracks.split('\n') if x.strip()]
    try: return metadata_connectors.candidate_search(artist,album,titles,limit)
    except ValueError as exc: raise HTTPException(400,str(exc))
    except Exception as exc: raise HTTPException(502,f'Metadata lookup failed: {exc}')

@router.get('/metadata/disc-candidates')
def metadata_disc_candidates(toc:str,limit:int=12,authorization:str|None=Header(default=None)):
    _user(authorization)
    try: return metadata_connectors.candidate_search_toc(toc,limit)
    except ValueError as exc: raise HTTPException(400,str(exc))
    except Exception as exc: raise HTTPException(502,f'Disc metadata lookup failed: {exc}')

@router.get('/metadata/{provider_id}/search')
def metadata_search(provider_id:str,q:str='',artist:str|None=None,album:str|None=None,track:str|None=None,limit:int=20,authorization:str|None=Header(default=None)):
    _user(authorization)
    try: return metadata_connectors.search(provider_id,q,artist,album,track,limit)
    except ValueError as exc: raise HTTPException(400,str(exc))
    except Exception as exc: raise HTTPException(502,f'Metadata lookup failed: {exc}')

@router.get('/metadata/listenbrainz/{username}/history')
def metadata_listenbrainz_history(username:str,count:int=100,authorization:str|None=Header(default=None)):
    _user(authorization)
    try: return metadata_connectors.listenbrainz_history(username,count)
    except Exception as exc: raise HTTPException(502,f'ListenBrainz lookup failed: {exc}')

@router.get('/meridian/zones')
def meridian_zones(authorization:str|None=Header(default=None)):
    _admin(authorization); return _meridian('GET','/v1/status')

@router.post('/meridian/{action}')
def meridian_control(action:str,payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization)
    if action not in ('wake','transport','volume','mute','pair'):
        raise HTTPException(400,'Unsupported Meridian action')
    return _meridian('POST','/v1/'+action,payload)

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
    try: return recordings.start_capture(item.device,item.media_type,item.name,item.sample_rate,item.bit_depth,item.input_role)
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


@router.get('/hardware')
def hardware_inventory(authorization:str|None=Header(default=None)):
    _admin(authorization); return _hardware()

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
    _admin(authorization)
    return _register_library_target(_storage('POST','/v1/usb/connect',payload),payload)

@router.post('/storage/network/connect')
def storage_share_connect(payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization)
    return _register_library_target(_storage('POST','/v1/network/connect',payload),payload)

@router.post('/storage/cloud/connect')
def storage_cloud_connect(payload:dict,authorization:str|None=Header(default=None)):
    _admin(authorization)
    result=_storage('POST','/v1/cloud/connect',payload)
    return result if result.get('authorization_required') else _register_library_target(result,payload)

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

@router.get('/media/{media_id}/export-check')
def media_export_check(media_id:int,authorization:str|None=Header(default=None)):
    _user(authorization)
    from .db import get_media
    media=get_media(media_id)
    if not media: raise HTTPException(404,'Media not found')
    blocked=bool(media.get('export_blocked'))
    return {'media_id':media_id,'export_allowed':not blocked,'export_blocked':blocked,
            'origin':media.get('origin'),'reason':'Source policy blocks export' if blocked else None}

@router.post('/media/{media_id}/export-to-backup')
def media_export_to_backup(media_id:int,item:MediaExportInput,authorization:str|None=Header(default=None)):
    _admin(authorization)
    from .db import get_media
    media=get_media(media_id)
    if not media: raise HTTPException(404,'Media not found')
    if bool(media.get('export_blocked')):
        raise HTTPException(403,'This recording is playback-only and cannot be exported')
    return _storage('POST','/v1/copy',{'source':media['path'],'target_id':item.target_id,
        'relative_path':item.relative_path,'delete_extra':item.delete_extra})
