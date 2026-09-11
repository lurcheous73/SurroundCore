import hmac
import base64
import subprocess
import concurrent.futures
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
import uuid
import httpx
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response, HTMLResponse, StreamingResponse, RedirectResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from .sonos import (endpoints as sonos_endpoints, play_uri as sonos_play_uri, set_volume as sonos_set_volume,
                    stop as sonos_stop, seek as sonos_seek, play as sonos_resume, pause as sonos_pause,
                    get_volume as sonos_get_volume, set_mute as sonos_set_mute, get_mute as sonos_get_mute, state as sonos_state)
from .mdns_endpoints import endpoints as mdns_endpoints
from .upnp import (endpoints as upnp_endpoints, play_uri as upnp_play_uri, pause as upnp_pause,
                   resume as upnp_resume, stop as upnp_stop, seek as upnp_seek, state as upnp_state,
                   set_volume as upnp_set_volume, set_mute as upnp_set_mute)
from .media import scan
from .streaming import (ranged_file, stereo_flac_cache, stereo_flac_program_cache,
                        load_settings, save_settings, load_provider_secrets, save_provider_secret)
from .providers import provider_status, quality_policy
from . import bandcamp as bandcamp_provider
from . import spotify_soloist, sonos_cloud, provider_bridge
from .podcasts import fetch_feed as fetch_podcast_feed
from .quality import output_route, source_request, pcm_playback_plan
from .playback import play_url, stop as stop_endpoint, status as endpoint_status
from .webui import streaming_setup_html
from .feature_routes import router as feature_router
from .controlmac_upload import router as controlmac_upload_router
from .sources import (SOURCE_KINDS, CACHE_POLICIES, validate_source, source_status,
                      media_path, cache_file, purge_source_cache, cache_stats)
from .db import (init_db, upsert_media, list_media, get_media, upsert_endpoint, list_endpoints,
                 save_group, list_groups, get_group, delete_group, update_group_latency,
                 save_source, list_sources, get_source, delete_source, prune_source_media, delete_media)
from .groups import GroupPlayback
from . import sessions, airplay, userauth, artwork, radio_browser, meridian_discovery, cast_driver, protocols, catalog, output_profiles, transport

app = FastAPI(title='SurroundCore', version='0.5.0-dev')
app.include_router(feature_router)
app.include_router(controlmac_upload_router)

_ENDPOINT_CACHE={'at':0.0,'items':[]}
_ENDPOINT_CACHE_LOCK=threading.Lock()
_ENDPOINT_CACHE_SECONDS=10.0
_RADIO_META={}
_RADIO_META_LOCK=threading.Lock()
_RADIO_ACTIVE={}
SOOLOOS_LOG = logging.getLogger("surroundcore.sooloos")


class EndpointRegistration(BaseModel):
    id: str
    name: str
    kind: str
    address: Optional[str] = None
    capabilities: dict = Field(default_factory=dict)


class EndpointPlayRequest(BaseModel):
    media_id: int
    device: str = 'default'
    volume: float = 0.35
    position_seconds: float = 0.0


class EndpointProgrammeRequest(BaseModel):
    media_ids: list[int]
    device: str = 'default'
    volume: float = 0.35
    position_seconds: float = 0.0


class SonosPlayRequest(BaseModel):
    media_id: int
    zone_id: Optional[str] = None
    volume: int = 25


class GroupMemberRequest(BaseModel):
    endpoint_id: str
    latency_ms: int = 0
    volume: Optional[float] = None
    enabled: bool = True


class GroupRequest(BaseModel):
    id: Optional[str] = None
    name: str
    members: list[GroupMemberRequest]


class GroupPlayRequest(BaseModel):
    media_id: Optional[int] = None
    media_ids: list[int] = Field(default_factory=list)
    position_seconds: float = 0.0
    lead_ms: int = 4000


class LatencyRequest(BaseModel):
    latency_ms: int


class StreamingSettingsUpdate(BaseModel):
    prefer_lossless: Optional[bool] = None
    prefer_airia: Optional[bool] = None
    allow_mqa_passthrough: Optional[bool] = None
    output_transport: Optional[str] = None
    prefer_mhr: Optional[bool] = None
    prefer_mmhr: Optional[bool] = None
    allow_downsample: Optional[bool] = None
    allow_downmix: Optional[bool] = None
    providers: Optional[dict] = None
    output_transports: Optional[dict] = None


class MeridianModelInput(BaseModel):
    model: str

class OutputTransportInput(BaseModel):
    host: str
    transport: str


class RadioStationInput(BaseModel):
    name: str
    url: str
    favicon: Optional[str] = None
    country: Optional[str] = None
    countrycode: Optional[str] = None
    codec: Optional[str] = None
    bitrate: Optional[int] = None
    tags: Optional[str] = None
    directory_id: Optional[str] = None


class RadioLocationInput(BaseModel):
    postcode: str


class PlaybackURLInput(BaseModel):
    endpoint_id: str
    url: str
    device: str = 'default'


class ProviderPlayInput(BaseModel):
    provider: str
    item_id: str
    endpoint_id: str
    device: str = 'default'


class BandcampConfigInput(BaseModel):
    username: str
    password: str
    server: str = bandcamp_provider.DEFAULT_BASE


class PodcastFeedInput(BaseModel):
    url: str
    name: Optional[str] = None


class SpotifyConfigInput(BaseModel):
    api_key: str
    binary: str = '/data/spotify/bin/soloist'
    device_name: str = 'SurroundCore Spotify'
    ws: str = '127.0.0.1:9090'
    data_dir: str = '/data/spotify'


class SpotifyPlayInput(BaseModel):
    uri: Optional[str] = None


class SonosCloudConfigInput(BaseModel):
    client_id: str
    client_secret: str
    redirect_uri: str


class SonosCodeInput(BaseModel):
    code: str


class SonosTokenInput(BaseModel):
    access_token: str
    refresh_token: Optional[str] = None
    expires_in: int = 86400


class SonosFavoritePlayInput(BaseModel):
    group_id: str
    favorite_id: str


class ProviderBridgeConfigInput(BaseModel):
    base_url: str
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    access_token: Optional[str] = None


class ProviderBridgePlayInput(BaseModel):
    item_id: str
    endpoint_id: Optional[str] = None

class LibrarySourceRequest(BaseModel):
    id: Optional[str] = None
    name: str
    kind: str
    path: Optional[str] = None
    cache_policy: str = 'off'
    config: dict = Field(default_factory=dict)
    enabled: bool = True


class WebLoginInput(BaseModel):
    username: str
    password: str

class CoreRecoveryInput(BaseModel):
    core_key: str

class WebBootstrapInput(BaseModel):
    username: str
    display_name: str
    password: str


class WebUserInput(BaseModel):
    username: Optional[str] = None
    display_name: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = 'user'
    enabled: Optional[bool] = True
    zones: list[str] = Field(default_factory=list)


class QueueInput(BaseModel):
    media_ids: list[int] = Field(default_factory=list)


class QueueAddInput(BaseModel):
    media_id: int


class CatalogAlbumInput(BaseModel):
    artist: Optional[str] = None
    title: Optional[str] = None
    metadata: dict = Field(default_factory=dict)

class CatalogEditionInput(BaseModel):
    title: Optional[str] = None
    media_type: Optional[str] = None
    source_format: Optional[str] = None
    release_year: Optional[str] = None
    metadata: dict = Field(default_factory=dict)

class CatalogTrackInput(BaseModel):
    metadata: dict = Field(default_factory=dict)
    edition_id: Optional[str] = None

class CatalogMergeInput(BaseModel):
    target_id: str

class CatalogSplitInput(BaseModel):
    artist: str
    title: str

class CatalogArtworkInput(BaseModel):
    data_base64: str

class CatalogMetadataApplyInput(BaseModel):
    provider: str='musicbrainz'
    release_id: Optional[str]=None
    artist: str
    title: str
    year: Optional[str]=None
    country: Optional[str]=None
    release_format: Optional[str]=None
    artwork_url: Optional[str]=None
    tracks: list[str]=Field(default_factory=list)

@app.on_event('startup')
def startup():
    init_db()
    userauth.init_auth_db()
    userauth.ensure_default_admin()
    catalog.init_catalog()
    catalog.sync_unattached_media()
    try:
        cfg=(load_provider_secrets().get('spotify') or {})
        if cfg.get('api_key') and spotify_soloist.binary(cfg):
            spotify_soloist.start_daemon(cfg)
    except Exception:
        pass


@app.on_event('shutdown')
def shutdown():
    airplay.stop_all()
    spotify_soloist.stop_daemon()


@app.get('/api/v1/health')
def health():
    return {'ok': True, 'service': 'SurroundCore', 'version': '0.5.0-dev'}


@app.get('/', response_class=HTMLResponse)
def web_dashboard():
    return HTMLResponse(streaming_setup_html())


@app.get('/setup/streaming')
def streaming_setup():
    return RedirectResponse('/', status_code=307)


def _supplied_token(authorization=None, token=None):
    return token or (authorization[7:] if authorization and authorization.startswith('Bearer ') else '')


def _auth_context(authorization=None, token=None):
    supplied = _supplied_token(authorization, token)
    expected = os.getenv('SURROUNDCORE_TOKEN', '')
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return {'id': 0, 'username': 'core-admin', 'display_name': 'Core Admin', 'role': 'admin',
                'enabled': True, 'zones': [], 'master': True}, supplied
    user = userauth.session_user(supplied)
    if user:
        user = dict(user); user['master'] = False
        return user, supplied
    raise HTTPException(401, 'Invalid or expired SurroundCore session')


def _require_token(authorization=None, token=None):
    _user, supplied = _auth_context(authorization, token)
    return supplied


def _require_admin(authorization=None, token=None):
    user, supplied = _auth_context(authorization, token)
    if user.get('role') != 'admin':
        raise HTTPException(403, 'Administrator access required')
    return user, supplied


def _zone_parent_id(zone_id):
    return str(zone_id or '').split('::', 1)[0]


def _require_zone(endpoint_id, authorization=None, token=None):
    user, supplied = _auth_context(authorization, token)
    parent = _zone_parent_id(endpoint_id)
    if user.get('role') != 'admin' and not (userauth.zone_allowed(user, endpoint_id) or userauth.zone_allowed(user, parent)):
        raise HTTPException(403, 'This user is not allowed to control that zone')
    zone=next((z for z in _logical_zones(user) if z.get('id')==endpoint_id or z.get('endpoint_id')==endpoint_id),None)
    if zone and zone.get('enabled') is False:
        raise HTTPException(409, 'This zone is disabled')
    if zone and zone.get('hardware_present') is False:
        raise HTTPException(409, 'Required zone hardware is not present')
    return user, supplied



def _agent_token():
    token = os.getenv('SURROUNDCORE_TOKEN', '')
    if not token:
        raise HTTPException(500, 'Core agent token is not configured')
    return token


def _visible_endpoints(user):
    items = _all_endpoints()
    if user.get('role') == 'admin':
        return items
    allowed = set(user.get('zones') or [])
    return [e for e in items if e.get('id') in allowed]



def _endpoint_host(endpoint):
    address = str(endpoint.get('address') or '')
    if '://' in address:
        return urllib.parse.urlparse(address).hostname or address
    return address.split(':', 1)[0] if address else ''


def _local_zone_name(device, index):
    kind=str(device.get('output_type') or 'audio').lower()
    base={'hdmi':'HDMI','usb-audio':'USB DAC','analog':'Soundcard','spdif':'S/PDIF','aes3':'AES/EBU','i2s':'I²S'}.get(kind, kind.upper())
    desc=str(device.get('description') or '')
    family=str(device.get('device_family') or '')
    if family and family not in ('Generic ALSA','USB Audio Class'):
        return family + (f' · {base}' if base not in family else '')
    label=''
    if '[' in desc and ']' in desc:
        label=desc.split('[',1)[1].split(']',1)[0].strip()
    return base + (f' · {label}' if label and label.casefold()!=base.casefold() else '')


def _logical_zones(user):
    prefs=load_settings().get('output_transports') or {}
    zones=[]; groups={}
    for endpoint in _visible_endpoints(user):
        if endpoint.get('kind')=='alsa' and (endpoint.get('capabilities') or {}).get('devices'):
            for i,dev in enumerate(endpoint['capabilities']['devices']):
                device=dev.get('raw_alsa') or dev.get('alsa') or f'device-{i}'
                zone_id=f"{endpoint['id']}::{device}"
                profile=output_profiles.get_profile(zone_id)
                settings=dict(profile.get('settings') or {})
                enabled=bool(settings.get('enabled',True))
                hardware_present=bool(dev.get('hardware_present',True))
                name=profile.get('name') or _local_zone_name(dev,i)
                zones.append({'id':zone_id,'endpoint_id':endpoint['id'],'device':device,'name':name,
                    'address':'local','kind':'alsa','transports':['alsa'],'transport_options':[protocols.describe('alsa')],
                    'transport_preference':'alsa','transport_key':zone_id,
                    'playable':bool(endpoint.get('address')) and enabled and hardware_present,
                    'enabled':enabled,'hardware_present':hardware_present,
                    'local':True,'physical_device':dict(dev),'endpoints':[endpoint]})
            continue
        host=_endpoint_host(endpoint); name=str(endpoint.get('name') or endpoint.get('id') or 'Zone'); base=name.removesuffix(' (L)').removesuffix(' (R)')
        key=host or base.casefold(); groups.setdefault(key,{'name':base,'host':host,'key':key,'endpoints':[]})['endpoints'].append(endpoint)
    for group in groups.values():
        profile=output_profiles.get_profile(group['key'])
        settings=dict(profile.get('settings') or {})
        enabled=bool(settings.get('enabled',True))
        wanted=profile.get('transport') or prefs.get(group['key'])
        if profile.get('name'): group['name']=profile['name']
        eps=sorted(group['endpoints'],key=lambda e:(0 if wanted and e.get('kind')==wanted else 1, protocols.rank(e.get('kind'))))
        preferred=eps[0]; kinds=[]
        for e in eps:
            if e.get('kind') not in kinds:kinds.append(e.get('kind'))
        local=preferred.get('kind') in ('alsa','meridian'); pcaps=preferred.get('capabilities') or {}
        playable=preferred.get('kind') in ('alsa','airplay','sonos','upnp','cast','meridian') and not bool(pcaps.get('discovered_only'))
        if preferred.get('kind')=='alsa' and not (pcaps.get('devices') or []): playable=False
        options=[protocols.describe(k) for k in kinds]
        zones.append({'id':preferred.get('id'),'endpoint_id':preferred.get('id'),'device':'default','name':group['name'],'address':'local' if local else group['host'],'kind':preferred.get('kind'),'transports':kinds,'transport_options':options,'transport_preference':wanted or preferred.get('kind'),'transport_key':group['key'],'playable':playable and enabled,'enabled':enabled,'hardware_present':True,'local':local,'endpoints':eps})
    return sorted(zones,key=lambda z:z['name'].casefold())


@app.get('/api/v1/auth/state')
def auth_state():
    return {'configured': userauth.count_users() > 0, 'master_key_available': bool(os.getenv('SURROUNDCORE_TOKEN', ''))}


@app.post('/api/v1/auth/login')
def auth_login(item: WebLoginInput):
    try:
        result = userauth.authenticate(item.username, item.password)
    except ValueError:
        result = None
    if not result:
        raise HTTPException(401, 'Invalid username or password')
    token, user, expires = result
    response={'ok': True, 'token': token, 'expires_at': expires, 'user': user}
    if user.get('role')=='admin' and userauth.take_core_key_reveal():
        response['core_key']=os.getenv('SURROUNDCORE_TOKEN','')
        response['core_key_notice']='Save this recovery key somewhere safe. It is not your browser session credential.'
    return response


@app.post('/api/v1/auth/recover')
def auth_recover(item: CoreRecoveryInput):
    expected=os.getenv('SURROUNDCORE_TOKEN','')
    if not expected or not item.core_key or not hmac.compare_digest(expected,item.core_key):
        raise HTTPException(401,'Invalid Core recovery key')
    result=userauth.recover_admin_default()
    if not result: raise HTTPException(500,'Admin recovery failed')
    token,user,expires=result
    return {'ok':True,'token':token,'expires_at':expires,'user':user,'temporary_credentials':{'username':'admin','password':'password'}}


@app.post('/api/v1/auth/bootstrap')
def auth_bootstrap(item: WebBootstrapInput, authorization: str | None = Header(default=None)):
    user, _token = _require_admin(authorization)
    if not user.get('master'):
        raise HTTPException(403, 'The Core master key is required for bootstrap')
    if userauth.count_users():
        raise HTTPException(409, 'Web users are already configured')
    try:
        created = userauth.create_user(item.username, item.display_name, item.password, role='admin')
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {'ok': True, 'user': created}


@app.get('/api/v1/auth/me')
def auth_me(authorization: str | None = Header(default=None)):
    user, _token = _auth_context(authorization)
    return {'user': user, 'zones': _visible_endpoints(user)}


@app.post('/api/v1/auth/logout')
def auth_logout(authorization: str | None = Header(default=None)):
    supplied = _supplied_token(authorization)
    expected = os.getenv('SURROUNDCORE_TOKEN', '')
    if expected and supplied and hmac.compare_digest(expected, supplied):
        return {'ok': True, 'master': True}
    return {'ok': userauth.logout(supplied)}


@app.get('/api/v1/admin/users')
def admin_users(authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    return {'users': userauth.list_users(), 'zones': _all_endpoints()}


@app.post('/api/v1/admin/users')
def admin_user_create(item: WebUserInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if not item.username or not item.password:
        raise HTTPException(400, 'username and password are required')
    if userauth.find_user(item.username):
        raise HTTPException(409, 'User already exists')
    try:
        user = userauth.create_user(item.username, item.display_name or item.username, item.password, item.role or 'user')
        userauth.update_user(user['id'], enabled=item.enabled)
        userauth.set_user_zones(user['id'], item.zones)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {'ok': True, 'user': userauth.get_user(user['id'])}


@app.put('/api/v1/admin/users/{user_id}')
def admin_user_update(user_id: int, item: WebUserInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    try:
        user = userauth.update_user(user_id, item.display_name, item.password, item.role, item.enabled)
        if not user:
            raise HTTPException(404, 'User not found')
        userauth.set_user_zones(user_id, item.zones)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {'ok': True, 'user': userauth.get_user(user_id)}


@app.delete('/api/v1/admin/users/{user_id}')
def admin_user_delete(user_id: int, authorization: str | None = Header(default=None)):
    actor, _token = _require_admin(authorization)
    victim=userauth.get_user(user_id)
    if not victim: raise HTTPException(404, 'User not found')
    if not actor.get('master') and int(actor.get('id') or 0)==int(user_id):
        raise HTTPException(409, 'You cannot delete the account you are currently using')
    if victim.get('role')=='admin' and victim.get('enabled') and userauth.admin_count()<=1:
        raise HTTPException(409, 'Cannot delete the last enabled administrator')
    userauth.delete_user(user_id)
    return {'ok': True, 'deleted_user': user_id}


def _queue_view(user, endpoint_id):
    if user.get('role') != 'admin' and not userauth.zone_allowed(user, endpoint_id):
        raise HTTPException(403, 'This user is not allowed to use that zone')
    if user.get('master'):
        return {'endpoint_id': endpoint_id, 'media_ids': [], 'items': [], 'persistent': False}
    ids = userauth.queue_get(user['id'], endpoint_id)
    items=[]
    for media_id in ids:
        item=get_media(media_id)
        if item:
            items.append(item)
    return {'endpoint_id': endpoint_id, 'media_ids': ids, 'items': items, 'persistent': True}


@app.get('/api/v1/me/queue/{endpoint_id}')
def my_queue(endpoint_id: str, authorization: str | None = Header(default=None)):
    user, _token = _auth_context(authorization)
    return _queue_view(user, endpoint_id)


@app.put('/api/v1/me/queue/{endpoint_id}')
def my_queue_save(endpoint_id: str, item: QueueInput, authorization: str | None = Header(default=None)):
    user, _token = _auth_context(authorization)
    if user.get('master'):
        raise HTTPException(409, 'Create a web admin account to use persistent personal queues')
    _require_zone(endpoint_id, authorization)
    for media_id in item.media_ids:
        if not get_media(media_id): raise HTTPException(404, f'Media {media_id} not found')
    userauth.queue_save(user['id'], endpoint_id, item.media_ids)
    return _queue_view(user, endpoint_id)


@app.post('/api/v1/me/queue/{endpoint_id}/items')
def my_queue_add(endpoint_id: str, item: QueueAddInput, authorization: str | None = Header(default=None)):
    user, _token = _auth_context(authorization)
    if user.get('master'):
        raise HTTPException(409, 'Create a web admin account to use persistent personal queues')
    _require_zone(endpoint_id, authorization)
    if not get_media(item.media_id): raise HTTPException(404, 'Media not found')
    userauth.queue_append(user['id'], endpoint_id, item.media_id)
    return _queue_view(user, endpoint_id)


@app.delete('/api/v1/me/queue/{endpoint_id}/items/{index}')
def my_queue_remove(endpoint_id: str, index: int, authorization: str | None = Header(default=None)):
    user, _token = _auth_context(authorization)
    if user.get('master'):
        raise HTTPException(409, 'Create a web admin account to use persistent personal queues')
    _require_zone(endpoint_id, authorization)
    try:
        userauth.queue_remove(user['id'], endpoint_id, index)
    except IndexError:
        raise HTTPException(404, 'Queue item not found')
    return _queue_view(user, endpoint_id)



INGEST_URL = os.getenv('SURROUNDCORE_INGEST_URL', 'http://127.0.0.1:8082').rstrip('/')


@app.api_route('/api/v1/ingest/{ingest_path:path}', methods=['GET', 'POST', 'DELETE'])
async def ingest_proxy(ingest_path: str, request: Request, authorization: str | None = Header(default=None)):
    _admin, token = _require_admin(authorization)
    body = await request.body()
    headers = {'Authorization': f'Bearer {token}'}
    content_type = request.headers.get('content-type')
    if content_type:
        headers['Content-Type'] = content_type
    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            upstream = await client.request(
                request.method, f'{INGEST_URL}/api/v1/ingest/{ingest_path}',
                params=list(request.query_params.multi_items()), headers=headers, content=body)
    except httpx.HTTPError as exc:
        raise HTTPException(503, f'Ingest service unavailable: {exc}')
    response_type = (upstream.headers.get('content-type') or 'application/json').split(';', 1)[0]
    return Response(content=upstream.content, status_code=upstream.status_code, media_type=response_type)


def _meridian_zone_candidates():
    url=os.getenv('SURROUNDCORE_MERIDIAN_URL','http://127.0.0.1:8091').rstrip('/')
    token=os.getenv('SURROUNDCORE_TOKEN','')
    if not token: return []
    try:
        r=httpx.get(url+'/v1/status',headers={'Authorization':'Bearer '+token},timeout=3.5)
        if r.status_code>=400: return []
        return (r.json() or {}).get('zones') or []
    except Exception:
        return []

def _name_key(value):
    return ''.join(ch for ch in str(value or '').casefold() if ch.isalnum())

def _attach_meridian_zone_ids(items):
    zones=_meridian_zone_candidates()
    if not zones: return
    for endpoint in items:
        if endpoint.get('kind')!='meridian': continue
        caps=dict(endpoint.get('capabilities') or {})
        if caps.get('sooloos_zone_id'): continue
        names=[endpoint.get('name'),caps.get('zone'),caps.get('device_id'),caps.get('serial')]
        keys={_name_key(x) for x in names if x}
        matches=[z for z in zones if _name_key(z.get('name')) in keys]
        if len(matches)==1:
            caps['sooloos_zone_id']=matches[0].get('zone_id')
            caps['sooloos_zone_name']=matches[0].get('name')
            caps['control_bridge']=True
            endpoint['capabilities']=caps

def _all_endpoints(force=False):
    if force: SOOLOOS_LOG.info("endpoint refresh requested force=true; Sooloos discovery permitted")
    with _ENDPOINT_CACHE_LOCK:
        if not force and time.time()-_ENDPOINT_CACHE['at'] < _ENDPOINT_CACHE_SECONDS:
            return [dict(x) for x in _ENDPOINT_CACHE['items']]
    combined={e['id']:e for e in list_endpoints()}
    jobs=[('sonos',sonos_endpoints),('mdns',mdns_endpoints),('upnp',upnp_endpoints)]
    if force:
        jobs.insert(0,('meridian',lambda:meridian_discovery.endpoints(force=True)))
    pool=concurrent.futures.ThreadPoolExecutor(max_workers=4)
    futures={pool.submit(fn):name for name,fn in jobs}
    done,_=concurrent.futures.wait(futures,timeout=5.5)
    for future in done:
        try: items=future.result()
        except Exception: continue
        for endpoint in items:
            current=combined.get(endpoint['id'])
            if current and endpoint.get('kind')=='meridian':
                caps=dict(endpoint.get('capabilities') or {}); caps.update(current.get('capabilities') or {})
                merged=dict(endpoint); merged.update(current); merged['capabilities']=caps; combined[endpoint['id']]=merged
            else: combined[endpoint['id']]=endpoint
    pool.shutdown(wait=False,cancel_futures=True)
    overrides=load_settings().get('meridian_models') or {}
    for endpoint in combined.values():
        if endpoint.get('kind')=='meridian':
            caps=dict(endpoint.get('capabilities') or {}); caps['model']=overrides.get(endpoint.get('id')) or caps.get('model') or 'Meridian / Sooloos'; endpoint['capabilities']=caps
    result=list(combined.values())
    _attach_meridian_zone_ids(result)
    with _ENDPOINT_CACHE_LOCK:
        _ENDPOINT_CACHE['at']=time.time(); _ENDPOINT_CACHE['items']=[dict(x) for x in result]
    return result


def _public_url():
    return os.getenv('SURROUNDCORE_PUBLIC_URL', 'http://127.0.0.1:8080').rstrip('/')


def _resolved_media(item):
    if not item:
        raise HTTPException(404, 'Media not found')
    try:
        path=media_path(item)
    except OSError:
        raise HTTPException(404, 'Media source unavailable and no cached copy exists')
    if not os.path.isfile(path):
        raise HTTPException(404, 'Media not found')
    return path


def _media_url(media_id, token, sonos=False):
    route = f'/api/v1/media/{media_id}/sonos.flac' if sonos else f'/api/v1/media/{media_id}/stream'
    return f'{_public_url()}{route}?token={urllib.parse.quote(token, safe="")}'


def _post_json(url, body, token):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode() or '{}')


def _playback_endpoint(endpoint_id):
    endpoint = next((e for e in _all_endpoints() if e.get('id') == endpoint_id), None)
    if not endpoint:
        raise HTTPException(404, 'Playback endpoint not found')
    return endpoint


def _programme_url(media_ids, token):
    ids=','.join(str(int(x)) for x in media_ids)
    return f'{_public_url()}/api/v1/programmes/stereo.flac?media_ids={ids}&token={urllib.parse.quote(token, safe="")}'


def _radio_relay_sig(station_id, exp):
    key=os.getenv('SURROUNDCORE_TOKEN','').encode()
    return hmac.new(key,f'{station_id}:{int(exp)}'.encode(),'sha256').hexdigest()

def _radio_relay_url(station_id):
    exp=int(time.time())+7*24*3600
    sig=_radio_relay_sig(station_id,exp)
    return f'{_public_url()}/api/v1/radio/relay/{urllib.parse.quote(str(station_id),safe="")}?exp={exp}&sig={sig}'

def _radio_is_hls(url):
    return '.m3u8' in str(url).lower()


def _radio_meta_update(station_id, title):
    title=str(title or '').strip()
    if not title:return
    with _RADIO_META_LOCK:_RADIO_META[str(station_id)]={'title':title,'updated_at':time.time()}

def _radio_meta_get(station_id):
    with _RADIO_META_LOCK:item=dict(_RADIO_META.get(str(station_id)) or {})
    return item if item and time.time()-item.get('updated_at',0)<180 else {}


def _external_media(title, url):
    return {'id': None, 'path': url, 'codec': 'stream', 'channels': 2, 'sample_rate': 0,
            'bit_depth': 0, 'duration': 0.0, 'metadata': {'title': title or 'Stream'}}


def _play_external(endpoint, url, device='default', volume=None, title='Stream', source='stream'):
    endpoint_id=endpoint.get('id')
    result=transport.play_url(endpoint,url,title=title,source=source,device=device,volume=volume,
                              media=_external_media(title,url))
    session=sessions.start_external(endpoint_id,source,{'title':title,'duration':0.0},
                                    {'mode':'transport-registry','transport':endpoint.get('kind'),'source_url':url})
    return {'ok':True,'transport':endpoint.get('kind'),'result':result,'session':session}


def _play_compat_programme(endpoint, media, media_ids, token, volume=None, position_seconds=0.0):
    endpoint_id=endpoint['id']; url=_programme_url(media_ids,token); kind=endpoint.get('kind')
    plan={'mode':'compatibility-programme','transport':kind,'render':{'codec':'flac','sample_rate':48000,'bit_depth':16,'channels':2},'tracks':len(media)}
    result=transport.play_programme(endpoint,url,title='SurroundCore Queue',position_seconds=position_seconds,volume=volume)
    session=sessions.start_library(endpoint_id,media,position_seconds=position_seconds,plan=plan)
    return {'ok':True,'endpoint':endpoint_id,'plan':plan,'session':session,'result':result}


_group_playback = GroupPlayback(_public_url, _post_json)


@app.get('/api/v1/streaming')
def streaming_overview(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    settings = load_settings()
    return {'settings': settings, 'quality': quality_policy(settings), 'providers': provider_status(settings)}


@app.get('/api/v1/streaming/route/{endpoint_id}')
def streaming_route(endpoint_id: str, channels: int = Query(default=2, ge=1, le=32), authorization: str | None = Header(default=None)):
    _require_zone(endpoint_id, authorization)
    endpoint = next((e for e in _all_endpoints() if e.get('id') == endpoint_id), None)
    if not endpoint:
        raise HTTPException(404, 'Endpoint not found')
    settings = load_settings()
    return {'source_request': source_request(settings, channels), 'output': output_route(endpoint, channels, settings), 'endpoint': endpoint}


@app.post('/api/v1/streaming/settings')
def streaming_settings(item: StreamingSettingsUpdate, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    update = {k: v for k, v in item.model_dump().items() if v is not None}
    settings = save_settings(update)
    return {'settings': settings, 'quality': quality_policy(settings)}


@app.get('/api/v1/radio/popular')
def radio_popular(limit: int = Query(default=40, ge=1, le=100), authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try:
        return {'stations': radio_browser.popular(limit)}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get('/api/v1/radio/search')
def radio_search(name: str = '', country: str = '', language: str = '', tag: str = '', limit: int = Query(default=60, ge=1, le=100), authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try:
        location=load_settings().get('radio_location') or {}
        return {'stations': radio_browser.search_local_first(name=name, country=country, language=language, tag=tag, location=location, limit=limit), 'local_first': bool(location)}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get('/api/v1/radio/location')
def radio_location(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    return {'location': load_settings().get('radio_location')}


@app.post('/api/v1/radio/location')
def radio_location_save(item: RadioLocationInput, limit: int = Query(default=40, ge=1, le=100), authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    try:
        location=radio_browser.resolve_postcode(item.postcode)
        stations=radio_browser.nearby(location['lat'], location['lon'], location.get('countrycode') or '', limit)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(502, str(exc))
    save_settings({'radio_location': location})
    return {'location': location, 'stations': stations}


@app.get('/api/v1/radio/nearby')
def radio_nearby(limit: int = Query(default=40, ge=1, le=100), authorization: str | None = Header(default=None)):
    _require_token(authorization)
    location=load_settings().get('radio_location') or {}
    if location.get('lat') is None or location.get('lon') is None:
        raise HTTPException(409, 'Set a postcode / ZIP under Streaming → Internet Radio first')
    try:
        return {'location': location, 'stations': radio_browser.nearby(location['lat'], location['lon'], location.get('countrycode') or '', limit)}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post('/api/v1/streaming/radio')
def add_radio(item: RadioStationInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    parsed = urllib.parse.urlparse(item.url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise HTTPException(400, 'Radio URL must be http or https')
    settings = load_settings()
    stations = list(settings.get('radio_stations') or [])
    data={'name':item.name.strip(),'url':item.url.strip()}
    for field in ('favicon','country','countrycode','codec','bitrate','tags','directory_id'):
        value=getattr(item,field,None)
        if value not in (None,''): data[field]=value
    existing=next((station for station in stations if station.get('url') == data['url']), None)
    if existing:
        existing.update(data)
    else:
        data['id']=uuid.uuid4().hex[:12]
        stations.append(data)
    settings = save_settings({'radio_stations': stations})
    return {'radio_stations': settings['radio_stations']}


@app.delete('/api/v1/streaming/radio/{station_id}')
def delete_radio(station_id: str, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    settings = load_settings()
    stations = [station for station in settings.get('radio_stations', []) if station.get('id') != station_id]
    settings = save_settings({'radio_stations': stations})
    return {'radio_stations': settings['radio_stations']}


@app.get('/api/v1/radio/relay/{station_id}')
def radio_relay(station_id: str, exp: int = Query(), sig: str = Query()):
    if exp < int(time.time()) or not hmac.compare_digest(sig,_radio_relay_sig(station_id,exp)):
        raise HTTPException(401,'Expired or invalid radio relay link')
    station=next((x for x in load_settings().get('radio_stations',[]) if x.get('id')==station_id),None)
    if not station: raise HTTPException(404,'Radio station not found')
    req=urllib.request.Request(station['url'],headers={'User-Agent':'SurroundCore/0.5','Icy-MetaData':'1'})
    try: upstream=urllib.request.urlopen(req,timeout=10)
    except Exception as exc: raise HTTPException(502,f'Radio upstream failed: {exc}')
    ctype=upstream.headers.get('Content-Type') or 'audio/mpeg'
    headers={}
    for key in ('icy-metaint','icy-name','icy-br','icy-genre','icy-url'):
        value=upstream.headers.get(key)
        if value: headers[key]=value
    metaint=int(upstream.headers.get('icy-metaint') or 0)
    def body():
        try:
            if not metaint:
                while True:
                    chunk=upstream.read(65536)
                    if not chunk: break
                    yield chunk
                return
            while True:
                remain=metaint
                while remain:
                    chunk=upstream.read(min(65536,remain))
                    if not chunk:return
                    remain-=len(chunk); yield chunk
                marker=upstream.read(1)
                if not marker:return
                yield marker
                mlen=marker[0]*16
                if mlen:
                    meta=upstream.read(mlen)
                    if not meta:return
                    yield meta
                    text=meta.rstrip(b'\x00').decode('utf-8','replace')
                    for part in text.split(';'):
                        if part.startswith("StreamTitle='") and part.endswith("'"):
                            _radio_meta_update(station_id,part[13:-1]); break
        finally: upstream.close()
    return StreamingResponse(body(),media_type=ctype,headers=headers)


@app.post('/api/v1/streaming/play')
def streaming_play(item: ProviderPlayInput, authorization: str | None = Header(default=None)):
    _user, token = _require_zone(item.endpoint_id, authorization)
    endpoint=_playback_endpoint(item.endpoint_id)
    settings = load_settings()
    title = item.provider.replace('_',' ').title()
    if item.provider == 'internet_radio':
        station = next((station for station in settings.get('radio_stations', []) if station.get('id') == item.item_id), None)
        if not station:
            raise HTTPException(404, 'Radio station not found')
        url = station['url']; title = station.get('name') or title
        _RADIO_ACTIVE[item.endpoint_id]=station.get('id')
        if endpoint.get('kind') in ('sonos','upnp') and not _radio_is_hls(url): url=_radio_relay_url(station.get('id'))
    elif item.provider == 'bandcamp':
        config = load_provider_secrets().get('bandcamp')
        if not config:
            raise HTTPException(409, 'Bandcamp account is not configured')
        song_id = urllib.parse.quote(item.item_id, safe='')
        url = f'{_public_url()}/api/v1/providers/bandcamp/stream/{song_id}?token={urllib.parse.quote(token, safe="")}'
    elif item.provider == 'podcasts':
        feed_id, sep, episode_id = item.item_id.partition(':')
        if not sep:
            raise HTTPException(400, 'Podcast item must be feed_id:episode_id')
        feed = next((feed for feed in settings.get('podcast_feeds', []) if feed.get('id') == feed_id), None)
        if not feed:
            raise HTTPException(404, 'Podcast feed not found')
        try:
            parsed = fetch_podcast_feed(feed['url'])
        except Exception as exc:
            raise HTTPException(502, f'Podcast feed failed: {exc}')
        episode = next((episode for episode in parsed['episodes'] if episode.get('id') == episode_id), None)
        if not episode:
            raise HTTPException(404, 'Podcast episode not found')
        url = episode['url']; title = episode.get('title') or title
    else:
        raise HTTPException(501, f'{item.provider} playback module is not installed')
    try:
        result=_play_external(endpoint, url, item.device, None, title, 'radio' if item.provider=='internet_radio' else item.provider)
        catalog.log_play(None if _user.get('master') else _user.get('id'),item.endpoint_id,None,'radio' if item.provider=='internet_radio' else 'streaming',item.provider,{'item_id':item.item_id,'title':title})
        return result
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post('/api/v1/playback/url')
def playback_url(item: PlaybackURLInput, authorization: str | None = Header(default=None)):
    _require_zone(item.endpoint_id, authorization)
    parsed = urllib.parse.urlparse(item.url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise HTTPException(400, 'Playback URL must be http or https')
    try:
        return _play_external(_playback_endpoint(item.endpoint_id), item.url, item.device, None, 'Internet Radio', 'radio')
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post('/api/v1/playback/{endpoint_id}/stop')
def playback_stop(endpoint_id: str, authorization: str | None = Header(default=None)):
    _require_zone(endpoint_id, authorization); endpoint=_playback_endpoint(endpoint_id)
    try:
        result=transport.stop(endpoint); sessions.clear(endpoint_id); return {'ok':True,'result':result}
    except Exception as exc: raise HTTPException(502,str(exc))


@app.get('/api/v1/playback/{endpoint_id}/status')
def playback_status(endpoint_id: str, authorization: str | None = Header(default=None)):
    _require_zone(endpoint_id, authorization); endpoint=_playback_endpoint(endpoint_id)
    try:
        data=transport.status(endpoint) or {}
        sid=_RADIO_ACTIVE.get(endpoint_id)
        if sid:
            meta=_radio_meta_get(sid)
            if meta.get('title'): data['stream_content']=meta['title']
            data['radio_station_id']=sid
        return data
    except Exception as exc: raise HTTPException(502,str(exc))


@app.post('/api/v1/playback/{endpoint_id}/volume')
def playback_volume(endpoint_id: str, volume: int = Query(ge=0, le=100), authorization: str | None = Header(default=None)):
    _require_zone(endpoint_id, authorization); endpoint=_playback_endpoint(endpoint_id)
    if 'volume' not in transport.controls(endpoint): raise HTTPException(501,'Volume control is not available for this transport')
    try: return {'ok':True,'volume':transport.set_volume(endpoint,volume)}
    except Exception as exc: raise HTTPException(502,str(exc))

@app.post('/api/v1/playback/{endpoint_id}/mute')
def playback_mute(endpoint_id: str, muted: bool = Query(), authorization: str | None = Header(default=None)):
    _require_zone(endpoint_id, authorization); endpoint=_playback_endpoint(endpoint_id)
    if 'mute' not in transport.controls(endpoint): raise HTTPException(501,'Mute control is not available for this transport')
    try: return {'ok':True,'muted':transport.set_mute(endpoint,muted)}
    except Exception as exc: raise HTTPException(502,str(exc))


def _spotify_config():
    config = load_provider_secrets().get('spotify')
    if not config:
        raise HTTPException(409, 'Spotify Soloist is not configured')
    return config


def _sonos_config(require_authorised=False):
    config = load_provider_secrets().get('sonos')
    if not config:
        raise HTTPException(409, 'Sonos cloud account is not configured')
    if require_authorised and not config.get('access_token'):
        raise HTTPException(409, 'Sonos account is not authorised')
    if config.get('refresh_token') and config.get('access_token'):
        obtained=int(config.get('obtained_at') or 0); expires=int(config.get('expires_in') or 86400)
        if obtained and time.time() >= obtained + expires - 120:
            try:
                fresh=sonos_cloud.refresh(config); config.update(fresh); save_provider_secret('sonos',config)
            except Exception as exc:
                raise HTTPException(502, f'Sonos token refresh failed: {exc}')
    return config


@app.get('/api/v1/providers/spotify')
def spotify_status(authorization: str | None = Header(default=None)):
    _require_token(authorization); config=load_provider_secrets().get('spotify') or {}
    out=spotify_soloist.redacted(config)
    if out['binary_available']:
        try: out['runtime']=spotify_soloist.status(config)
        except Exception as exc: out['runtime_error']=str(exc)
    return out


@app.post('/api/v1/providers/spotify/install')
def spotify_install(authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    config=load_provider_secrets().get('spotify') or {'data_dir':'/data/spotify'}
    try:
        config['binary']=spotify_soloist.install_latest(config)
        if config.get('api_key'):
            save_provider_secret('spotify',config)
            spotify_soloist.start_daemon(config)
        return {'ok':True, **spotify_soloist.redacted(config)}
    except Exception as exc:
        raise HTTPException(502, f'Spotify Soloist install failed: {exc}')


@app.post('/api/v1/providers/spotify/configure')
def spotify_configure(item: SpotifyConfigInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    config=item.model_dump()
    try:
        if not spotify_soloist.binary(config):
            config['binary']=spotify_soloist.install_latest(config)
        save_provider_secret('spotify',config)
        runtime=spotify_soloist.start_daemon(config)
        return {**spotify_soloist.redacted(config), 'runtime_start':runtime}
    except Exception as exc:
        raise HTTPException(502, f'Spotify setup failed: {exc}')


@app.delete('/api/v1/providers/spotify/configure')
def spotify_disconnect(authorization: str | None = Header(default=None)):
    _require_admin(authorization); save_provider_secret('spotify',None); return {'ok':True}


@app.post('/api/v1/providers/spotify/play')
def spotify_play(item: SpotifyPlayInput, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try: return spotify_soloist.play(_spotify_config(),item.uri)
    except Exception as exc: raise HTTPException(502,str(exc))


@app.post('/api/v1/providers/spotify/{command}')
def spotify_command(command: str, value: int | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization); config=_spotify_config()
    funcs={'pause':spotify_soloist.pause,'next':spotify_soloist.next_track,'previous':spotify_soloist.previous,
           'activate':spotify_soloist.activate,'deactivate':spotify_soloist.deactivate}
    try:
        if command=='volume':
            if value is None: raise HTTPException(400,'volume value required')
            return spotify_soloist.volume(config,value)
        if command not in funcs: raise HTTPException(404,'Unknown Spotify command')
        return funcs[command](config)
    except HTTPException: raise
    except Exception as exc: raise HTTPException(502,str(exc))


@app.get('/api/v1/providers/spotify/now')
def spotify_now(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try: return spotify_soloist.now(_spotify_config())
    except Exception as exc: raise HTTPException(502,str(exc))


@app.get('/api/v1/providers/spotify/queue')
def spotify_queue(limit: int = Query(default=20, ge=1, le=80), authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try: return spotify_soloist.queue(_spotify_config(),limit)
    except Exception as exc: raise HTTPException(502,str(exc))


@app.get('/api/v1/providers/sonos')
def sonos_cloud_status(authorization: str | None = Header(default=None)):
    _require_token(authorization); config=load_provider_secrets().get('sonos') or {}
    return sonos_cloud.redacted(config)


@app.post('/api/v1/providers/sonos/configure')
def sonos_cloud_configure(item: SonosCloudConfigInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    config=load_provider_secrets().get('sonos') or {}; config.update(item.model_dump()); save_provider_secret('sonos',config)
    return sonos_cloud.redacted(config)


@app.delete('/api/v1/providers/sonos/configure')
def sonos_cloud_disconnect(authorization: str | None = Header(default=None)):
    _require_admin(authorization); save_provider_secret('sonos',None); return {'ok':True}


@app.get('/api/v1/providers/sonos/authorize-url')
def sonos_cloud_authorize_url(authorization: str | None = Header(default=None)):
    _require_admin(authorization); config=_sonos_config(); state=uuid.uuid4().hex
    config['oauth_state']=state; save_provider_secret('sonos',config)
    return {'url':sonos_cloud.authorization_url(config,state),'state':state}


@app.post('/api/v1/providers/sonos/exchange-code')
def sonos_cloud_exchange(item: SonosCodeInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization); config=_sonos_config()
    try: token=sonos_cloud.exchange_code(config,item.code.strip())
    except Exception as exc: raise HTTPException(502,f'Sonos authorization failed: {exc}')
    config.update(token); save_provider_secret('sonos',config); return sonos_cloud.redacted(config)


@app.post('/api/v1/providers/sonos/tokens')
def sonos_cloud_tokens(item: SonosTokenInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization); config=_sonos_config(); config.update(item.model_dump(exclude_none=True)); config['obtained_at']=int(time.time())
    save_provider_secret('sonos',config); return sonos_cloud.redacted(config)


@app.get('/api/v1/providers/sonos/households')
def sonos_cloud_households(authorization: str | None = Header(default=None)):
    _require_token(authorization); config=_sonos_config(True)
    try: return {'households':sonos_cloud.households(config)}
    except Exception as exc: raise HTTPException(502,str(exc))


@app.get('/api/v1/providers/sonos/households/{household_id}/groups')
def sonos_cloud_groups(household_id: str, authorization: str | None = Header(default=None)):
    _require_token(authorization); config=_sonos_config(True)
    try: return sonos_cloud.groups(config,household_id)
    except Exception as exc: raise HTTPException(502,str(exc))


@app.get('/api/v1/providers/sonos/households/{household_id}/favorites')
def sonos_cloud_favorites(household_id: str, authorization: str | None = Header(default=None)):
    _require_token(authorization); config=_sonos_config(True)
    try: return sonos_cloud.favorites(config,household_id)
    except Exception as exc: raise HTTPException(502,str(exc))


@app.post('/api/v1/providers/sonos/favorite')
def sonos_cloud_play_favorite(item: SonosFavoritePlayInput, authorization: str | None = Header(default=None)):
    _require_token(authorization); config=_sonos_config(True)
    try: return sonos_cloud.load_favorite(config,item.group_id,item.favorite_id)
    except Exception as exc: raise HTTPException(502,str(exc))


def _bridge_config(provider):
    if provider not in provider_bridge.SUPPORTED:
        raise HTTPException(404,'Unknown licensed provider')
    config=load_provider_secrets().get(provider)
    if not config: raise HTTPException(409,f'{provider} bridge is not configured')
    return config


@app.get('/api/v1/providers/bridge/{provider}')
def bridge_status(provider: str, authorization: str | None = Header(default=None)):
    _require_token(authorization); config=load_provider_secrets().get(provider) or {}
    if provider not in provider_bridge.SUPPORTED: raise HTTPException(404,'Unknown licensed provider')
    out=provider_bridge.redacted(config)
    if out['configured']:
        try: out['runtime']=provider_bridge.status(config); out['available']=True
        except Exception as exc: out['available']=False; out['runtime_error']=str(exc)
    else: out['available']=False
    return out


@app.post('/api/v1/providers/bridge/{provider}/configure')
def bridge_configure(provider: str, item: ProviderBridgeConfigInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if provider not in provider_bridge.SUPPORTED: raise HTTPException(404,'Unknown licensed provider')
    config={k:v for k,v in item.model_dump().items() if v not in (None,'')}; save_provider_secret(provider,config)
    return provider_bridge.redacted(config)


@app.delete('/api/v1/providers/bridge/{provider}/configure')
def bridge_disconnect(provider: str, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if provider not in provider_bridge.SUPPORTED: raise HTTPException(404,'Unknown licensed provider')
    save_provider_secret(provider,None); return {'ok':True}


@app.get('/api/v1/providers/bridge/{provider}/search')
def bridge_search(provider: str, q: str = Query(min_length=1), limit: int = Query(default=25, ge=1, le=100), authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try: return provider_bridge.search(_bridge_config(provider),q,limit)
    except HTTPException: raise
    except Exception as exc: raise HTTPException(502,str(exc))


@app.post('/api/v1/providers/bridge/{provider}/play')
def bridge_play(provider: str, item: ProviderBridgePlayInput, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try: return provider_bridge.play(_bridge_config(provider),item.item_id,item.endpoint_id,'highest_native')
    except HTTPException: raise
    except Exception as exc: raise HTTPException(502,str(exc))


def _bandcamp_config():
    config = load_provider_secrets().get('bandcamp')
    if not config:
        raise HTTPException(409, 'Bandcamp account is not configured')
    return config


@app.get('/api/v1/providers/bandcamp')
def bandcamp_status(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    config = load_provider_secrets().get('bandcamp') or {}
    return bandcamp_provider.redacted_config(config)


@app.post('/api/v1/providers/bandcamp/configure')
def bandcamp_configure(item: BandcampConfigInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    config = {'server': item.server.strip() or bandcamp_provider.DEFAULT_BASE,
              'username': item.username.strip(), 'password': item.password}
    try:
        result = bandcamp_provider.ping(config)
    except Exception as exc:
        raise HTTPException(502, f'Bandcamp login failed: {exc}')
    if not result.get('ok'):
        raise HTTPException(502, 'Bandcamp Subsonic ping failed')
    save_provider_secret('bandcamp', config)
    return {'ok': True, 'account': bandcamp_provider.redacted_config(config), 'server_version': result.get('version')}


@app.delete('/api/v1/providers/bandcamp/configure')
def bandcamp_disconnect(authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    save_provider_secret('bandcamp', None)
    return {'ok': True}


@app.get('/api/v1/providers/bandcamp/albums')
def bandcamp_albums(size: int = Query(default=200, ge=1, le=500), offset: int = Query(default=0, ge=0), authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try:
        return {'albums': bandcamp_provider.albums(_bandcamp_config(), size=size, offset=offset)}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get('/api/v1/providers/bandcamp/albums/{album_id}')
def bandcamp_album(album_id: str, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try:
        return {'album': bandcamp_provider.album(_bandcamp_config(), album_id)}
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get('/api/v1/providers/bandcamp/stream/{song_id}')
def bandcamp_stream(song_id: str, request: Request, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    try:
        client, response = bandcamp_provider.stream_request(_bandcamp_config(), song_id, request.headers.get('range'))
    except Exception as exc:
        raise HTTPException(502, str(exc))
    headers = {key: value for key, value in response.headers.items()
               if key.lower() in ('content-length', 'content-range', 'accept-ranges')}
    media_type = response.headers.get('content-type', 'application/octet-stream')
    def close_remote():
        response.close(); client.close()
    return StreamingResponse(response.iter_bytes(), status_code=response.status_code, media_type=media_type,
                             headers=headers, background=BackgroundTask(close_remote))


@app.post('/api/v1/providers/podcasts')
def podcast_add(item: PodcastFeedInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    parsed_url = urllib.parse.urlparse(item.url)
    if parsed_url.scheme not in ('http', 'https') or not parsed_url.netloc:
        raise HTTPException(400, 'Podcast feed URL must be http or https')
    try:
        parsed = fetch_podcast_feed(item.url)
    except Exception as exc:
        raise HTTPException(502, f'Podcast feed failed: {exc}')
    settings = load_settings()
    feeds = list(settings.get('podcast_feeds') or [])
    existing = next((feed for feed in feeds if feed.get('url') == item.url), None)
    if existing:
        existing['name'] = item.name or parsed['title']
    else:
        feeds.append({'id': uuid.uuid4().hex[:12], 'name': item.name or parsed['title'], 'url': item.url})
    settings = save_settings({'podcast_feeds': feeds})
    return {'podcast_feeds': settings['podcast_feeds']}


@app.delete('/api/v1/providers/podcasts/{feed_id}')
def podcast_delete(feed_id: str, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    settings = load_settings()
    feeds = [feed for feed in settings.get('podcast_feeds', []) if feed.get('id') != feed_id]
    settings = save_settings({'podcast_feeds': feeds})
    return {'podcast_feeds': settings['podcast_feeds']}


@app.get('/api/v1/providers/podcasts/{feed_id}/episodes')
def podcast_episodes(feed_id: str, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    feed = next((feed for feed in load_settings().get('podcast_feeds', []) if feed.get('id') == feed_id), None)
    if not feed:
        raise HTTPException(404, 'Podcast feed not found')
    try:
        parsed = fetch_podcast_feed(feed['url'])
    except Exception as exc:
        raise HTTPException(502, f'Podcast feed failed: {exc}')
    return {'feed': feed, 'title': parsed['title'], 'episodes': parsed['episodes']}

@app.get('/api/v1/sources')
def sources_list(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    items=[]
    for source in list_sources():
        row=dict(source); row['status']=source_status(source); items.append(row)
    return {'sources':items,'kinds':list(SOURCE_KINDS),'cache_policies':list(CACHE_POLICIES)}


@app.post('/api/v1/sources')
def sources_save(item: LibrarySourceRequest, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    data=item.model_dump(); data['id']=data.get('id') or ('source-' + uuid.uuid4().hex[:12])
    try:
        validate_source(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    save_source(data)
    source=get_source(data['id']); source['status']=source_status(source)
    return {'ok':True,'source':source}


@app.delete('/api/v1/sources/{source_id}')
def sources_delete(source_id: str, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if not delete_source(source_id):
        raise HTTPException(404, 'Library source not found')
    return {'ok':True}


@app.post('/api/v1/sources/{source_id}/scan')
def sources_scan(source_id: str, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    source=get_source(source_id)
    if not source:
        raise HTTPException(404, 'Library source not found')
    if source['kind']=='plex':
        raise HTTPException(409, 'Plex libraries are indexed through the Plex provider')
    root=source.get('path')
    if not root or not os.path.isdir(root):
        raise HTTPException(409, 'Library source is not mounted')
    items=scan(root)
    for entry in items:
        if 'error' not in entry:
            entry['source_id']=source_id; upsert_media(entry)
    pruned=prune_source_media(source_id, [entry['path'] for entry in items if entry.get('path')])
    return {'source_id':source_id,'root':root,'scanned':len(items),'pruned':pruned,'items':items}


@app.post('/api/v1/sources/{source_id}/cache/warm')
def sources_cache_warm(source_id: str, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    source=get_source(source_id)
    if not source:
        raise HTTPException(404, 'Library source not found')
    if source.get('cache_policy') not in ('read-through','pin'):
        raise HTTPException(409, 'Source cache policy does not store media files')
    files=[m for m in list_media() if m.get('source_id')==source_id]
    for entry in files:
        cache_file(entry['path'], source_id)
    return {'ok':True,'source_id':source_id,'cached':len(files)}


@app.get('/api/v1/sources/cache/status')
def sources_cache_status(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    return cache_stats()


@app.delete('/api/v1/sources/{source_id}/cache')
def sources_cache_purge(source_id: str, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if not get_source(source_id):
        raise HTTPException(404, 'Library source not found')
    return {'ok':True,'source_id':source_id,'purged':purge_source_cache(source_id)}


@app.get('/api/v1/groups')
def groups_list(authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    return {'groups': list_groups()}


@app.post('/api/v1/groups')
def groups_save(item: GroupRequest, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    group_id = item.id or ('group-' + uuid.uuid4().hex[:12])
    members = [m.model_dump() for m in item.members]
    save_group(group_id, item.name, members)
    return {'ok': True, 'group': get_group(group_id)}


@app.delete('/api/v1/groups/{group_id}')
def groups_delete(group_id: str, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if not delete_group(group_id):
        raise HTTPException(404, 'Group not found')
    return {'ok': True}


@app.put('/api/v1/groups/{group_id}/members/{endpoint_id}/latency')
def groups_latency(group_id: str, endpoint_id: str, item: LatencyRequest, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if not update_group_latency(group_id, endpoint_id, item.latency_ms):
        raise HTTPException(404, 'Group member not found')
    return {'ok': True, 'group': get_group(group_id)}


@app.post('/api/v1/groups/{group_id}/play')
def groups_play(group_id: str, item: GroupPlayRequest, authorization: str | None = Header(default=None)):
    _admin, token = _require_admin(authorization)
    group = get_group(group_id)
    if not group:
        raise HTTPException(404, 'Group not found')
    session_id = 'session-' + uuid.uuid4().hex[:12]
    try:
        media_ids = item.media_ids or ([item.media_id] if item.media_id is not None else [])
        return {'ok': True, 'session': _group_playback.play(
            session_id, group, _all_endpoints(), media_ids, token, item.position_seconds, item.lead_ms)}
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post('/api/v1/groups/sessions/{session_id}/stop')
def groups_stop(session_id: str, authorization: str | None = Header(default=None)):
    _admin, token = _require_admin(authorization)
    if not _group_playback.stop(session_id, _all_endpoints(), token):
        raise HTTPException(404, 'Session not found')
    return {'ok': True}


@app.get('/api/v1/groups/sessions')
def groups_sessions(authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    return {'sessions': _group_playback.list_sessions()}



@app.get('/api/v1/endpoints')
def endpoints(authorization: str | None = Header(default=None)):
    user, _token = _auth_context(authorization)
    return {'endpoints': _visible_endpoints(user), 'meridian_models': meridian_discovery.MODELS, 'output_transports': load_settings().get('output_transports') or {}}


@app.put('/api/v1/outputs/transport')
def output_transport(item: OutputTransportInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    host=item.host.strip(); transport=item.transport.strip().lower()
    if not host: raise HTTPException(400,'Output host required')
    candidates=[e for e in _all_endpoints() if _endpoint_host(e)==host]
    kinds=sorted({str(e.get('kind') or '').lower() for e in candidates if e.get('kind')})
    if transport not in kinds: raise HTTPException(400,f'{transport} is not available on this device')
    settings=load_settings(); prefs=dict(settings.get('output_transports') or {}); prefs[host]=transport
    save_settings({'output_transports':prefs})
    return {'ok':True,'host':host,'transport':transport,'available':kinds}


@app.post('/api/v1/endpoints/discover')
def endpoints_discover(authorization: str | None = Header(default=None)):
    SOOLOOS_LOG.info("explicit administrator endpoint discovery invoked")
    user, _token = _auth_context(authorization)
    if user.get('role') != 'admin':
        raise HTTPException(403, 'Administrator access required')
    items = _all_endpoints(force=True)
    return {'ok': True, 'count': len(items), 'endpoints': items, 'zones': _logical_zones(user)}


@app.put('/api/v1/endpoints/{endpoint_id}/meridian-model')
def endpoint_meridian_model(endpoint_id: str, item: MeridianModelInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    model = item.model.strip()
    if model not in meridian_discovery.MODELS:
        raise HTTPException(400, 'Unknown Meridian model')
    settings = load_settings()
    models = dict(settings.get('meridian_models') or {})
    if model == 'Meridian / Sooloos': models.pop(endpoint_id, None)
    else: models[endpoint_id] = model
    save_settings({'meridian_models': models})
    return {'ok': True, 'endpoint_id': endpoint_id, 'model': model}



@app.get('/api/v1/zones')
def zones(authorization: str | None = Header(default=None)):
    user, _token = _auth_context(authorization)
    return {'zones': _logical_zones(user)}


@app.post('/api/v1/endpoints/register')
def register_endpoint(item: EndpointRegistration, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    upsert_endpoint(item.model_dump())
    return {'ok': True}


@app.get('/api/v1/endpoints/sonos')
def sonos(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    return {'endpoints': sonos_endpoints()}


@app.get('/api/v1/endpoints/mdns')
def mdns(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    return {'endpoints': mdns_endpoints()}


@app.get('/api/v1/endpoints/upnp')
def upnp(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    return {'endpoints': upnp_endpoints()}


@app.post('/api/v1/library/scan')
def library_scan(path: str = Query(default=None), authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    root = path or os.getenv('SURROUNDCORE_MEDIA', '/media')
    items = scan(root)
    for item in items:
        if 'error' not in item:
            upsert_media(item)
    return {'root': root, 'scanned': len(items), 'items': items}


def _media_matches(item, q='', artist='', album=''):
    meta=item.get('metadata') or {}
    if artist and str(meta.get('artist') or meta.get('album_artist') or '').casefold() != artist.casefold(): return False
    if album and str(meta.get('album') or '').casefold() != album.casefold(): return False
    if not q: return True
    hay=' '.join(str(x or '') for x in (meta.get('title'),meta.get('artist'),meta.get('album_artist'),meta.get('album'),item.get('path'))).casefold()
    return q.casefold() in hay


@app.get('/api/v1/library')
def library(q: str = Query(default=''), artist: str = Query(default=''), album: str = Query(default=''),
            offset: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=5000),
            authorization: str | None = Header(default=None)):
    _require_token(authorization)
    items=[x for x in list_media() if _media_matches(x,q,artist,album)]
    return {'total':len(items),'offset':offset,'limit':limit,'items':items[offset:offset+limit]}


def _is_audiobook(item):
    meta=item.get('metadata') or {}
    source=str(item.get('source_id') or '').casefold()
    path=str(item.get('path') or '').casefold()
    genre=' '.join(str(meta.get(k) or '') for k in ('genre','content_type','media_type')).casefold()
    return ('audible' in source or 'audiobook' in source or '/audiobooks/' in path or
            'audiobooks' in path or 'audiobook' in genre or 'spoken word' in genre)


@app.put('/api/v1/admin/catalog/albums/{album_id}')
def admin_catalog_album_update(album_id: str, body: CatalogAlbumInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    try: item=catalog.update_album(album_id,body.artist,body.title,body.metadata)
    except ValueError as exc: raise HTTPException(409,str(exc))
    if not item: raise HTTPException(404,'Album not found')
    return {'ok':True,'album':item}

@app.put('/api/v1/admin/catalog/editions/{edition_id}')
def admin_catalog_edition_update(edition_id: str, body: CatalogEditionInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    item=catalog.update_edition(edition_id,body.title,body.media_type,body.source_format,body.release_year,body.metadata)
    if not item: raise HTTPException(404,'Edition not found')
    return {'ok':True,'edition':item}

@app.put('/api/v1/admin/catalog/tracks/{media_id}')
def admin_catalog_track_update(media_id: int, body: CatalogTrackInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    item=catalog.update_media_tags(media_id,body.metadata)
    if not item: raise HTTPException(404,'Media not found')
    if body.edition_id:
        try: catalog.move_media_to_edition(media_id,body.edition_id)
        except ValueError as exc: raise HTTPException(404,str(exc))
    return {'ok':True,'media':get_media(media_id)}

@app.post('/api/v1/admin/catalog/albums/{album_id}/merge')
def admin_catalog_album_merge(album_id: str, body: CatalogMergeInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if not catalog.merge_albums(album_id,body.target_id): raise HTTPException(404,'Album not found or target invalid')
    return {'ok':True,'source_id':album_id,'target_id':body.target_id}

@app.post('/api/v1/admin/catalog/editions/{edition_id}/merge')
def admin_catalog_edition_merge(edition_id: str, body: CatalogMergeInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if not catalog.merge_editions(edition_id,body.target_id): raise HTTPException(404,'Edition not found or target invalid')
    return {'ok':True,'source_id':edition_id,'target_id':body.target_id}

@app.post('/api/v1/admin/catalog/editions/{edition_id}/split')
def admin_catalog_edition_split(edition_id: str, body: CatalogSplitInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    album_id=catalog.split_edition_to_album(edition_id,body.artist,body.title)
    if not album_id: raise HTTPException(404,'Edition not found')
    return {'ok':True,'album_id':album_id}

@app.put('/api/v1/admin/catalog/albums/{album_id}/artwork')
def admin_catalog_artwork(album_id: str, body: CatalogArtworkInput, authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    if not catalog.album_info(album_id): raise HTTPException(404,'Album not found')
    try: raw=base64.b64decode(body.data_base64,validate=True)
    except Exception: raise HTTPException(400,'Invalid artwork data')
    if not raw or len(raw)>15*1024*1024: raise HTTPException(400,'Artwork must be between 1 byte and 15 MB')
    root=os.path.join(os.getenv('SURROUNDCORE_DATA','/data'),'artwork-overrides'); os.makedirs(root,exist_ok=True)
    src=os.path.join(root,album_id+'.upload'); dst=os.path.join(root,album_id+'.jpg')
    with open(src,'wb') as f: f.write(raw)
    try:
        proc=subprocess.run(['ffmpeg','-y','-v','error','-i',src,'-frames:v','1','-q:v','2',dst],capture_output=True,timeout=15)
        if proc.returncode!=0 or not os.path.isfile(dst): raise HTTPException(400,'Artwork is not a supported image')
    finally:
        try: os.unlink(src)
        except OSError: pass
    catalog.update_album(album_id,metadata={'artwork_override':dst})
    return {'ok':True,'album_id':album_id}

def _save_remote_artwork(album_id,url):
    parsed=urllib.parse.urlparse(str(url or ''))
    allowed={'coverartarchive.org','www.coverartarchive.org'}
    if parsed.scheme!='https' or parsed.hostname not in allowed: return False
    try:
        with httpx.Client(timeout=20,follow_redirects=True) as client:
            r=client.get(url,headers={'User-Agent':'SurroundCore/0.5'}); r.raise_for_status(); raw=r.content
    except Exception: return False
    if not raw or len(raw)>15*1024*1024: return False
    root=os.path.join(os.getenv('SURROUNDCORE_DATA','/data'),'artwork-overrides'); os.makedirs(root,exist_ok=True)
    src=os.path.join(root,album_id+'.lookup'); dst=os.path.join(root,album_id+'.jpg')
    with open(src,'wb') as f:f.write(raw)
    try:
        proc=subprocess.run(['ffmpeg','-y','-v','error','-i',src,'-frames:v','1','-q:v','2',dst],capture_output=True,timeout=15)
        return proc.returncode==0 and os.path.isfile(dst)
    finally:
        try:os.unlink(src)
        except OSError:pass

@app.post('/api/v1/admin/catalog/albums/{album_id}/apply-metadata')
def admin_catalog_apply_metadata(album_id:str, body:CatalogMetadataApplyInput, authorization:str|None=Header(default=None)):
    _require_admin(authorization)
    album=catalog.album_info(album_id)
    if not album: raise HTTPException(404,'Album not found')
    meta={'metadata_provider':body.provider,'release_id':body.release_id,'release_year':body.year,
          'release_country':body.country,'release_format':body.release_format}
    item=catalog.update_album(album_id,body.artist,body.title,{k:v for k,v in meta.items() if v})
    editions=[]
    with catalog.connect() as con:
        editions=[dict(x) for x in con.execute('SELECT id FROM editions WHERE album_id=? ORDER BY created_at,id',(album_id,))]
    if editions and body.year:
        catalog.update_edition(editions[0]['id'],release_year=body.year,metadata={k:v for k,v in meta.items() if v})
    media_ids=catalog.album_media_ids(album_id)
    if body.tracks and len(body.tracks)==len(media_ids):
        for idx,(mid,title) in enumerate(zip(media_ids,body.tracks),1): catalog.update_media_tags(mid,{'title':title,'track':str(idx)})
    art=False
    if body.artwork_url: art=_save_remote_artwork(album_id,body.artwork_url)
    if art: catalog.update_album(album_id,metadata={'artwork_override':os.path.join(os.getenv('SURROUNDCORE_DATA','/data'),'artwork-overrides',album_id+'.jpg')})
    return {'ok':True,'album':item,'tracks_updated':bool(body.tracks and len(body.tracks)==len(media_ids)),'artwork_updated':art}

@app.get('/api/v1/admin/media')
def admin_media(q: str = Query(default=''), offset: int = Query(default=0, ge=0),
                limit: int = Query(default=250, ge=1, le=5000), authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    items=[x for x in list_media() if _media_matches(x,q)]
    return {'total':len(items),'offset':offset,'limit':limit,'items':items[offset:offset+limit]}

def _validated_media_file(media_id):
    item=get_media(media_id)
    if not item: raise HTTPException(404,'Media not found')
    path=item.get('path') or ''
    roots=[]
    src=get_source(item.get('source_id')) if item.get('source_id') else None
    if src and src.get('path'): roots.append(os.path.realpath(src['path']))
    roots.append(os.path.realpath(os.getenv('SURROUNDCORE_MEDIA','/media')))
    roots.append(os.path.realpath(os.getenv('SURROUNDCORE_DATA','/data')))
    real=os.path.realpath(path)
    if not any(real==r or real.startswith(r.rstrip('/')+'/') for r in roots):
        raise HTTPException(409,'Refusing to delete a file outside a configured media source')
    if os.path.exists(real) and not os.path.isfile(real):
        raise HTTPException(409,'Media path is not a regular file')
    return item,real

def _delete_media_item(media_id, delete_file=False):
    item=get_media(media_id)
    if not item: raise HTTPException(404,'Media not found')
    path=item.get('path') or ''
    if delete_file:
        item,real=_validated_media_file(media_id)
        if os.path.exists(real):
            try: os.unlink(real)
            except OSError as exc: raise HTTPException(409,f'File could not be deleted: {exc}')
    if not delete_media(media_id): raise HTTPException(404,'Media not found')
    return {'media_id':int(media_id),'file_deleted':bool(delete_file),'path':path}

@app.delete('/api/v1/admin/media/{media_id}')
def admin_media_delete(media_id: int, delete_file: bool = Query(default=False),
                       authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    return {'ok':True,**_delete_media_item(media_id,delete_file)}

@app.delete('/api/v1/admin/catalog/editions/{edition_id}')
def admin_catalog_edition_delete(edition_id: str, delete_files: bool = Query(default=False),
                                 authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    info=catalog.edition_info(edition_id)
    if not info: raise HTTPException(404,'Edition not found')
    ids=catalog.edition_media_ids(edition_id)
    if delete_files:
        for mid in ids: _validated_media_file(mid)
    deleted=[_delete_media_item(mid,delete_files) for mid in ids]
    return {'ok':True,'edition_id':edition_id,'deleted_items':deleted,'file_count':len(deleted),'files_deleted':bool(delete_files)}

@app.delete('/api/v1/admin/catalog/albums/{album_id}')
def admin_catalog_album_delete(album_id: str, delete_files: bool = Query(default=False),
                               authorization: str | None = Header(default=None)):
    _require_admin(authorization)
    info=catalog.album_info(album_id)
    if not info: raise HTTPException(404,'Album not found')
    ids=catalog.album_media_ids(album_id)
    if delete_files:
        for mid in ids: _validated_media_file(mid)
    deleted=[_delete_media_item(mid,delete_files) for mid in ids]
    return {'ok':True,'album_id':album_id,'deleted_items':deleted,'file_count':len(deleted),'files_deleted':bool(delete_files)}

@app.get('/api/v1/library/audiobooks')
def library_audiobooks(q: str = Query(default=''), offset: int = Query(default=0, ge=0),
                       limit: int = Query(default=500, ge=1, le=5000), authorization: str | None = Header(default=None)):
    _require_token(authorization)
    items=[x for x in list_media() if _is_audiobook(x) and _media_matches(x,q)]
    return {'total':len(items),'offset':offset,'limit':limit,'items':items[offset:offset+limit]}


@app.get('/api/v1/library/albums')
def library_albums(q: str = Query(default=''), offset: int = Query(default=0, ge=0),
                   limit: int = Query(default=250, ge=1, le=2000), authorization: str | None = Header(default=None)):
    _require_token(authorization)
    albums={}
    for item in list_media():
        meta=item.get('metadata') or {}
        title=str(meta.get('album') or 'Unknown Album').strip() or 'Unknown Album'
        artist=str(meta.get('album_artist') or meta.get('artist') or 'Unknown Artist').strip() or 'Unknown Artist'
        key=(artist.casefold(),title.casefold())
        if q and q.casefold() not in (artist+' '+title).casefold(): continue
        row=albums.setdefault(key,{'artist':artist,'album':title,'representative_media_id':item.get('id'),'tracks':0,
                                   'sample_rates':set(),'bit_depths':set(),'channels':set(),'codecs':set()})
        row['tracks'] += 1
        if item.get('sample_rate'): row['sample_rates'].add(int(item['sample_rate']))
        if item.get('bit_depth'): row['bit_depths'].add(int(item['bit_depth']))
        if item.get('channels'): row['channels'].add(int(item['channels']))
        if item.get('codec'): row['codecs'].add(str(item['codec']))
    out=[]
    for row in albums.values():
        row=dict(row)
        for field in ('sample_rates','bit_depths','channels','codecs'): row[field]=sorted(row[field])
        out.append(row)
    out.sort(key=lambda x:(x['artist'].casefold(),x['album'].casefold()))
    return {'total':len(out),'offset':offset,'limit':limit,'albums':out[offset:offset+limit]}


@app.get('/api/v1/media/{media_id}/artwork.jpg')
def media_artwork(media_id: int, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item=get_media(media_id)
    if not item: raise HTTPException(404,'Media not found')
    try:
        data=None
        album_id=catalog.media_album_id(media_id)
        if album_id:
            info=catalog.album_info(album_id) or {}
            try: meta=json.loads(info.get('metadata_json') or '{}')
            except Exception: meta={}
            override=meta.get('artwork_override')
            if override and os.path.isfile(override): data=open(override,'rb').read()
        if data is None:
            source=dict(item); source['path']=_resolved_media(item)
            data=artwork.cover_bytes(source)
    except Exception:
        data=None
    if not data: raise HTTPException(404,'Artwork not found')
    return Response(content=data,media_type='image/jpeg',headers={'Cache-Control':'private, max-age=86400'})


@app.head('/api/v1/media/{media_id}/stream')
def stream_head(media_id: int, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    path=_resolved_media(item)
    return Response(headers={'Accept-Ranges': 'bytes', 'Content-Length': str(os.path.getsize(path))})


@app.get('/api/v1/media/{media_id}/stream')
def stream(media_id: int, request: Request, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    path=_resolved_media(item)
    return ranged_file(path, request.headers.get('range'), os.path.basename(item['path']))


@app.head('/api/v1/media/{media_id}/sonos.flac')
def sonos_render_head(media_id: int, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    path=_resolved_media(item)
    cached = stereo_flac_cache(path)
    return Response(media_type='audio/flac', headers={
        'Accept-Ranges': 'bytes',
        'Content-Length': str(os.path.getsize(cached)),
        'X-SurroundCore-Render': 'cached-stereo-48k-s16-flac',
    })


@app.get('/api/v1/media/{media_id}/sonos.flac')
def sonos_render(media_id: int, request: Request, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    path=_resolved_media(item)
    cached = stereo_flac_cache(path)
    filename = os.path.splitext(os.path.basename(item['path']))[0] + ' - stereo.flac'
    return ranged_file(cached, request.headers.get('range'), filename)


@app.head('/api/v1/programmes/stereo.flac')
def programme_head(media_ids: str, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    ids = [int(x) for x in media_ids.split(',') if x.strip()]
    items = [get_media(i) for i in ids]
    if not ids or any(item is None for item in items):
        raise HTTPException(404, 'Programme media not found')
    cached = stereo_flac_program_cache([_resolved_media(item) for item in items])
    return Response(media_type='audio/flac', headers={'Accept-Ranges':'bytes','Content-Length':str(os.path.getsize(cached))})


@app.get('/api/v1/programmes/stereo.flac')
def programme_stream(request: Request, media_ids: str, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    ids = [int(x) for x in media_ids.split(',') if x.strip()]
    items = [get_media(i) for i in ids]
    if not ids or any(item is None for item in items):
        raise HTTPException(404, 'Programme media not found')
    cached = stereo_flac_program_cache([_resolved_media(item) for item in items])
    return ranged_file(cached, request.headers.get('range'), 'SurroundCore Group Programme.flac')


@app.get('/api/v1/endpoints/{endpoint_id}/plan/{media_id}')
def endpoint_plan(endpoint_id: str, media_id: int, device: str = Query(default='default'), authorization: str | None = Header(default=None)):
    _require_zone(endpoint_id, authorization)
    endpoint = next((e for e in _all_endpoints() if e['id'] == endpoint_id), None)
    if not endpoint or endpoint.get('kind') not in ('alsa', 'airplay', 'sonos', 'upnp', 'cast', 'meridian'):
        raise HTTPException(404, 'Playback endpoint not found')
    media = get_media(media_id)
    if not media:
        raise HTTPException(404, 'Media not found')
    return {'endpoint': endpoint_id, 'media_id': media_id, 'plan': pcm_playback_plan(media, endpoint, device, load_settings())}


@app.post('/api/v1/endpoints/{endpoint_id}/play')
def endpoint_play(endpoint_id: str, item: EndpointPlayRequest, authorization: str | None = Header(default=None)):
    _user, token = _require_zone(endpoint_id, authorization)
    endpoint = next((e for e in _all_endpoints() if e['id'] == endpoint_id), None)
    if not endpoint or endpoint.get('kind') not in ('alsa', 'airplay', 'sonos', 'upnp', 'cast', 'meridian') or not endpoint.get('address'):
        raise HTTPException(404, 'Playback endpoint not found')
    media = get_media(item.media_id)
    if not media:
        raise HTTPException(404, 'Media not found')
    plan = pcm_playback_plan(media, endpoint, item.device, load_settings())
    if not plan.get('supported'):
        raise HTTPException(409, plan.get('reason') or 'Endpoint cannot play source')
    volume = max(0.0, min(float(item.volume), 1.0))
    if endpoint.get('kind') == 'airplay':
        try:
            result = airplay.play(endpoint, _media_url(item.media_id, token), media,
                                  position_seconds=item.position_seconds, volume=volume)
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))
    elif endpoint.get('kind') == 'sonos':
        stereo_flac_cache(_resolved_media(media))
        sonos_volume=sonos_set_volume(endpoint, round(volume*49))
        uri=_media_url(item.media_id, token, sonos=True)
        sonos_play_uri(endpoint, uri)
        if item.position_seconds > 0: sonos_seek(endpoint, item.position_seconds)
        result={'transport':'sonos','volume':sonos_volume,'uri':uri}
    elif endpoint.get('kind') == 'upnp':
        uri=_media_url(item.media_id, token)
        upnp_play_uri(endpoint,uri)
        if item.position_seconds > 0: upnp_seek(endpoint,item.position_seconds)
        result={'transport':'upnp','uri':uri}
    elif endpoint.get('kind') == 'cast':
        return _play_compat_programme(endpoint,[media],[item.media_id],token,volume,item.position_seconds)
    else:
        result = _post_json(endpoint['address'].rstrip('/') + '/v1/play', {
            'url': _media_url(item.media_id, token), 'device': item.device, 'volume': volume,
            'position_seconds': item.position_seconds,
            'mode': plan.get('mode', 'direct'), 'source': plan.get('source'),
        }, _agent_token())
    session = sessions.start_library(endpoint_id, [media], position_seconds=item.position_seconds, plan=plan)
    catalog.log_play(None if _user.get('master') else _user.get('id'),endpoint_id,item.media_id,'library',None,{'device':item.device})
    return {'ok': True, 'endpoint': endpoint_id, 'volume': volume, 'plan': plan, 'session': session, 'result': result}


@app.post('/api/v1/endpoints/{endpoint_id}/programme')
def endpoint_programme(endpoint_id: str, item: EndpointProgrammeRequest, authorization: str | None = Header(default=None)):
    _user, token = _require_zone(endpoint_id, authorization)
    endpoint = _playback_endpoint(endpoint_id)
    if endpoint.get('kind') not in ('alsa','airplay','sonos','upnp','cast','meridian') or not endpoint.get('address'):
        raise HTTPException(404, 'Playback endpoint not found')
    if not item.media_ids:
        raise HTTPException(400, 'Programme requires media_ids')
    media = [get_media(media_id) for media_id in item.media_ids]
    if any(x is None for x in media):
        raise HTTPException(404, 'Programme media not found')
    volume = max(0.0, min(float(item.volume), 1.0))
    if endpoint.get('kind') in ('sonos','airplay','upnp','cast'):
        try:
            result=_play_compat_programme(endpoint, media, item.media_ids, token, volume, item.position_seconds)
            for media_id in item.media_ids:
                catalog.log_play(None if _user.get('master') else _user.get('id'),endpoint_id,media_id,'library',None,{'programme':True,'device':item.device})
            return result
        except Exception as exc:
            raise HTTPException(502, str(exc))
    settings = load_settings()
    plans = [pcm_playback_plan(x, endpoint, item.device, settings) for x in media]
    failed = next((p for p in plans if not p.get('supported')), None)
    if failed:
        raise HTTPException(409, failed.get('reason') or 'Endpoint cannot play programme natively')
    if any(p.get('mode') != 'direct' for p in plans):
        raise HTTPException(409, 'Native gapless programme requires direct-capable media')
    keys = [(p['source'].get('sample_rate'), p['source'].get('channels'), p['source'].get('bit_depth'),
             str(p['source'].get('channel_layout') or '').lower()) for p in plans]
    if any(k != keys[0] for k in keys[1:]):
        raise HTTPException(409, 'Native gapless programme requires identical sample rate, channel layout and bit depth')
    result = _post_json(endpoint['address'].rstrip('/') + '/v1/programme', {
        'urls': [_media_url(media_id, token) for media_id in item.media_ids],
        'device': item.device, 'volume': volume, 'position_seconds': item.position_seconds,
        'mode': 'direct', 'source': plans[0]['source'], 'sources': [p['source'] for p in plans],
    }, _agent_token())
    session = sessions.start_library(endpoint_id, media, position_seconds=item.position_seconds,
                                     plan={'mode':'direct','gapless':True,'tracks':len(media)})
    for media_id in item.media_ids:
        catalog.log_play(None if _user.get('master') else _user.get('id'),endpoint_id,media_id,'library',None,{'programme':True,'device':item.device})
    return {'ok': True, 'endpoint': endpoint_id, 'plan': session['plan'], 'session': session, 'result': result}


@app.get('/api/v1/sessions')
def playback_sessions(authorization: str | None = Header(default=None)):
    user, _token = _auth_context(authorization)
    allowed = {e.get('id') for e in _visible_endpoints(user)}
    return {'sessions': [x for x in sessions.list_views() if x.get('endpoint_id') in allowed]}


@app.get('/api/v1/sessions/{endpoint_id}')
def playback_session(endpoint_id: str, authorization: str | None = Header(default=None)):
    _require_zone(endpoint_id, authorization)
    session = sessions.view(endpoint_id)
    if not session: raise HTTPException(404, 'Playback session not found')
    return session


def _session_endpoint(endpoint_id):
    endpoint = next((e for e in _all_endpoints() if e.get('id') == endpoint_id), None)
    if not endpoint or endpoint.get('kind') not in ('alsa', 'airplay', 'sonos', 'upnp', 'cast', 'meridian') or not endpoint.get('address'):
        raise HTTPException(404, 'Playback endpoint not found')
    return endpoint


@app.post('/api/v1/sessions/{endpoint_id}/{control}')
def playback_session_control(endpoint_id: str, control: str, position_seconds: float | None = Query(default=None), authorization: str | None = Header(default=None)):
    if str(endpoint_id).startswith("meridian:"):
        SOOLOOS_LOG.info("Meridian session control endpoint=%s control=%s position=%s", endpoint_id, control, position_seconds)
    _user, token = _require_zone(endpoint_id, authorization); endpoint = _session_endpoint(endpoint_id)
    if not sessions.view(endpoint_id): raise HTTPException(404, 'Playback session not found')
    kind=endpoint.get('kind'); is_airplay=kind=='airplay'; is_sonos=kind=='sonos'; is_upnp=kind=='upnp'; is_cast=kind=='cast'
    if control in ('pause','resume','stop'):
        try:
            if is_airplay:
                result = airplay.stop(endpoint_id) if control == 'stop' else ({'ok': airplay.pause(endpoint_id)} if control == 'pause' else {'ok': airplay.resume(endpoint_id)})
            elif is_sonos:
                if control=='stop': result={'ok':sonos_stop(endpoint)}
                elif control=='pause': result={'ok':sonos_pause(endpoint)}
                else: result={'ok':sonos_resume(endpoint)}
            elif is_upnp:
                if control=='stop': result={'ok':upnp_stop(endpoint)}
                elif control=='pause': result={'ok':upnp_pause(endpoint)}
                else: result={'ok':upnp_resume(endpoint)}
            elif is_cast:
                if control=='stop': result={'ok':cast_driver.stop(endpoint)}
                elif control=='pause': result={'ok':cast_driver.pause(endpoint)}
                else: result={'ok':cast_driver.resume(endpoint)}
            else:
                result = _post_json(endpoint['address'].rstrip('/') + '/v1/' + control, {}, _agent_token())
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))
        session = sessions.clear(endpoint_id) if control == 'stop' else sessions.set_state(endpoint_id, 'paused' if control == 'pause' else 'playing')
        return {'ok': True, 'session': session, 'result': result}
    if control == 'seek':
        if position_seconds is None: raise HTTPException(400, 'position_seconds required')
        target = max(0.0, float(position_seconds))
        try:
            result = airplay.seek(endpoint_id, target) if is_airplay else (sonos_seek(endpoint,target) if is_sonos else (upnp_seek(endpoint,target) if is_upnp else (cast_driver.seek(endpoint,target) if is_cast else _post_json(endpoint['address'].rstrip('/') + '/v1/seek', {'position_seconds': target}, _agent_token()))))
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))
        return {'ok': True, 'session': sessions.seek(endpoint_id, target), 'result': result}
    if control in ('next','previous'):
        target = sessions.control_target(endpoint_id, control)
        if target is None: raise HTTPException(409, f'{control} is not available')
        try:
            result = airplay.seek(endpoint_id, target) if is_airplay else (sonos_seek(endpoint,target) if is_sonos else (upnp_seek(endpoint,target) if is_upnp else (cast_driver.seek(endpoint,target) if is_cast else _post_json(endpoint['address'].rstrip('/') + '/v1/seek', {'position_seconds': target}, _agent_token()))))
        except RuntimeError as exc:
            raise HTTPException(502, str(exc))
        return {'ok': True, 'session': sessions.seek(endpoint_id, target), 'result': result}
    raise HTTPException(404, 'Unknown playback control')


@app.post('/api/v1/endpoints/{endpoint_id}/stop')
def endpoint_stop(endpoint_id: str, authorization: str | None = Header(default=None)):
    if str(endpoint_id).startswith("meridian:"):
        SOOLOOS_LOG.info("Meridian endpoint stop endpoint=%s", endpoint_id)
    _user, token = _require_zone(endpoint_id, authorization)
    endpoint = next((e for e in _all_endpoints() if e['id'] == endpoint_id), None)
    if not endpoint or endpoint.get('kind') not in ('alsa', 'airplay', 'sonos', 'upnp', 'cast', 'meridian') or not endpoint.get('address'):
        raise HTTPException(404, 'Playback endpoint not found')
    try:
        result = airplay.stop(endpoint_id) if endpoint.get('kind') == 'airplay' else ({'ok':sonos_stop(endpoint)} if endpoint.get('kind')=='sonos' else ({'ok':upnp_stop(endpoint)} if endpoint.get('kind')=='upnp' else ({'ok':cast_driver.stop(endpoint)} if endpoint.get('kind')=='cast' else _post_json(endpoint['address'].rstrip('/') + '/v1/stop', {}, _agent_token()))))
    except RuntimeError as exc:
        raise HTTPException(502, str(exc))
    session = sessions.clear(endpoint_id)
    return {'ok': True, 'session': session, 'result': result}


@app.post('/api/v1/sonos/play')
def sonos_play(item: SonosPlayRequest, authorization: str | None = Header(default=None)):
    token = _require_token(authorization)
    zones = [e for e in _all_endpoints() if e.get('kind') == 'sonos']
    zone = next((e for e in zones if item.zone_id in (e.get('id'), e.get('name'))), None) if item.zone_id else (zones[0] if zones else None)
    if not zone:
        raise HTTPException(404, 'Sonos zone not found')
    media = get_media(item.media_id)
    if not media:
        raise HTTPException(404, 'Media not found')
    stereo_flac_cache(_resolved_media(media))
    volume = sonos_set_volume(zone, max(0, min(int(item.volume), 100)))
    uri = _media_url(item.media_id, token, sonos=True)
    sonos_play_uri(zone, uri)
    return {'ok': True, 'zone': zone.get('name'), 'volume': volume, 'uri': uri}


@app.post('/api/v1/sonos/stop')
def stop_sonos(zone_id: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization)
    zones = [e for e in _all_endpoints() if e.get('kind') == 'sonos']
    zone = next((e for e in zones if zone_id in (e.get('id'), e.get('name'))), None) if zone_id else (zones[0] if zones else None)
    if not zone:
        raise HTTPException(404, 'Sonos zone not found')
    sonos_stop(zone)
    return {'ok': True, 'zone': zone.get('name')}
