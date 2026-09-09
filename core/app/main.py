import hmac
import json
import os
import urllib.parse
import urllib.request
import uuid
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from .sonos import endpoints as sonos_endpoints, play_uri as sonos_play_uri, set_volume as sonos_set_volume, stop as sonos_stop
from .mdns_endpoints import endpoints as mdns_endpoints
from .upnp import endpoints as upnp_endpoints
from .media import scan
from .streaming import ranged_file, stereo_flac_cache
from .db import (init_db, upsert_media, list_media, get_media, upsert_endpoint, list_endpoints,
                 save_group, list_groups, get_group, delete_group, update_group_latency)
from .groups import GroupPlayback

app = FastAPI(title='SurroundCore', version='0.3.0')


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
    media_id: int
    position_seconds: float = 0.0
    lead_ms: int = 4000


class LatencyRequest(BaseModel):
    latency_ms: int


@app.on_event('startup')
def startup():
    init_db()


@app.get('/api/v1/health')
def health():
    return {'ok': True, 'service': 'SurroundCore', 'version': '0.3.0'}


def _require_token(authorization=None, token=None):
    expected = os.getenv('SURROUNDCORE_TOKEN', '')
    supplied = token or (authorization[7:] if authorization and authorization.startswith('Bearer ') else '')
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(401, 'Invalid SurroundCore token')
    return expected


def _all_endpoints():
    combined = {e['id']: e for e in list_endpoints()}
    for discover in (sonos_endpoints, mdns_endpoints, upnp_endpoints):
        try:
            for endpoint in discover():
                combined[endpoint['id']] = endpoint
        except Exception:
            pass
    return list(combined.values())


def _public_url():
    return os.getenv('SURROUNDCORE_PUBLIC_URL', 'http://127.0.0.1:8080').rstrip('/')


def _media_url(media_id, token, sonos=False):
    route = f'/api/v1/media/{media_id}/sonos.flac' if sonos else f'/api/v1/media/{media_id}/stream'
    return f'{_public_url()}{route}?token={urllib.parse.quote(token, safe="")}'


def _post_json(url, body, token):
    request = urllib.request.Request(
        url, data=json.dumps(body).encode(), method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {token}'})
    with urllib.request.urlopen(request, timeout=8) as response:
        return json.loads(response.read().decode() or '{}')


_group_playback = GroupPlayback(_public_url, _post_json)


@app.get('/api/v1/groups')
def groups_list(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    return {'groups': list_groups()}


@app.post('/api/v1/groups')
def groups_save(item: GroupRequest, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    group_id = item.id or ('group-' + uuid.uuid4().hex[:12])
    members = [m.model_dump() for m in item.members]
    save_group(group_id, item.name, members)
    return {'ok': True, 'group': get_group(group_id)}


@app.delete('/api/v1/groups/{group_id}')
def groups_delete(group_id: str, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    if not delete_group(group_id):
        raise HTTPException(404, 'Group not found')
    return {'ok': True}


@app.put('/api/v1/groups/{group_id}/members/{endpoint_id}/latency')
def groups_latency(group_id: str, endpoint_id: str, item: LatencyRequest, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    if not update_group_latency(group_id, endpoint_id, item.latency_ms):
        raise HTTPException(404, 'Group member not found')
    return {'ok': True, 'group': get_group(group_id)}


@app.post('/api/v1/groups/{group_id}/play')
def groups_play(group_id: str, item: GroupPlayRequest, authorization: str | None = Header(default=None)):
    token = _require_token(authorization)
    group = get_group(group_id)
    if not group:
        raise HTTPException(404, 'Group not found')
    session_id = 'session-' + uuid.uuid4().hex[:12]
    try:
        return {'ok': True, 'session': _group_playback.play(
            session_id, group, _all_endpoints(), item.media_id, token, item.position_seconds, item.lead_ms)}
    except RuntimeError as exc:
        raise HTTPException(409, str(exc))


@app.post('/api/v1/groups/sessions/{session_id}/stop')
def groups_stop(session_id: str, authorization: str | None = Header(default=None)):
    token = _require_token(authorization)
    if not _group_playback.stop(session_id, _all_endpoints(), token):
        raise HTTPException(404, 'Session not found')
    return {'ok': True}


@app.get('/api/v1/groups/sessions')
def groups_sessions(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    return {'sessions': _group_playback.list_sessions()}



@app.get('/api/v1/endpoints')
def endpoints(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    return {'endpoints': _all_endpoints()}


@app.post('/api/v1/endpoints/register')
def register_endpoint(item: EndpointRegistration, authorization: str | None = Header(default=None)):
    _require_token(authorization)
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
    _require_token(authorization)
    root = path or os.getenv('SURROUNDCORE_MEDIA', '/media')
    items = scan(root)
    for item in items:
        if 'error' not in item:
            upsert_media(item)
    return {'root': root, 'scanned': len(items), 'items': items}


@app.get('/api/v1/library')
def library(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    return {'items': list_media()}


@app.head('/api/v1/media/{media_id}/stream')
def stream_head(media_id: int, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    if not item or not os.path.isfile(item['path']):
        raise HTTPException(404, 'Media not found')
    return Response(headers={'Accept-Ranges': 'bytes', 'Content-Length': str(os.path.getsize(item['path']))})


@app.get('/api/v1/media/{media_id}/stream')
def stream(media_id: int, request: Request, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    if not item or not os.path.isfile(item['path']):
        raise HTTPException(404, 'Media not found')
    return ranged_file(item['path'], request.headers.get('range'), os.path.basename(item['path']))


@app.head('/api/v1/media/{media_id}/sonos.flac')
def sonos_render_head(media_id: int, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    if not item or not os.path.isfile(item['path']):
        raise HTTPException(404, 'Media not found')
    cached = stereo_flac_cache(item['path'])
    return Response(media_type='audio/flac', headers={
        'Accept-Ranges': 'bytes',
        'Content-Length': str(os.path.getsize(cached)),
        'X-SurroundCore-Render': 'cached-stereo-48k-s16-flac',
    })


@app.get('/api/v1/media/{media_id}/sonos.flac')
def sonos_render(media_id: int, request: Request, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    if not item or not os.path.isfile(item['path']):
        raise HTTPException(404, 'Media not found')
    cached = stereo_flac_cache(item['path'])
    filename = os.path.splitext(os.path.basename(item['path']))[0] + ' - stereo.flac'
    return ranged_file(cached, request.headers.get('range'), filename)


@app.post('/api/v1/endpoints/{endpoint_id}/play')
def endpoint_play(endpoint_id: str, item: EndpointPlayRequest, authorization: str | None = Header(default=None)):
    token = _require_token(authorization)
    endpoint = next((e for e in _all_endpoints() if e['id'] == endpoint_id), None)
    if not endpoint or endpoint.get('kind') != 'alsa' or not endpoint.get('address'):
        raise HTTPException(404, 'ALSA endpoint not found')
    if not get_media(item.media_id):
        raise HTTPException(404, 'Media not found')
    volume = max(0.0, min(float(item.volume), 1.0))
    result = _post_json(endpoint['address'].rstrip('/') + '/v1/play', {
        'url': _media_url(item.media_id, token), 'device': item.device, 'volume': volume,
    }, token)
    return {'ok': True, 'endpoint': endpoint_id, 'volume': volume, 'result': result}


@app.post('/api/v1/endpoints/{endpoint_id}/stop')
def endpoint_stop(endpoint_id: str, authorization: str | None = Header(default=None)):
    token = _require_token(authorization)
    endpoint = next((e for e in _all_endpoints() if e['id'] == endpoint_id), None)
    if not endpoint or endpoint.get('kind') != 'alsa' or not endpoint.get('address'):
        raise HTTPException(404, 'ALSA endpoint not found')
    result = _post_json(endpoint['address'].rstrip('/') + '/v1/stop', {}, token)
    return {'ok': True, 'result': result}


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
    stereo_flac_cache(media['path'])
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
