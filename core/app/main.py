import hmac
import json
import os
import urllib.parse
import urllib.request
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from .sonos import endpoints as sonos_endpoints, play_uri as sonos_play_uri, set_volume as sonos_set_volume, stop as sonos_stop
from .media import scan
from .streaming import ranged_file, stereo_flac
from .db import init_db, upsert_media, list_media, get_media, upsert_endpoint, list_endpoints

app = FastAPI(title='SurroundCore', version='0.2.0')


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


@app.on_event('startup')
def startup():
    init_db()


@app.get('/api/v1/health')
def health():
    return {'ok': True, 'service': 'SurroundCore', 'version': '0.2.0'}


def _require_token(authorization=None, token=None):
    expected = os.getenv('SURROUNDCORE_TOKEN', '')
    supplied = token or (authorization[7:] if authorization and authorization.startswith('Bearer ') else '')
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(401, 'Invalid SurroundCore token')
    return expected


def _all_endpoints():
    combined = {e['id']: e for e in list_endpoints()}
    try:
        for endpoint in sonos_endpoints():
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


@app.get('/api/v1/media/{media_id}/stream')
def stream(media_id: int, request: Request, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    if not item or not os.path.isfile(item['path']):
        raise HTTPException(404, 'Media not found')
    return ranged_file(item['path'], request.headers.get('range'), os.path.basename(item['path']))


@app.get('/api/v1/media/{media_id}/sonos.flac')
def sonos_render(media_id: int, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    if not item or not os.path.isfile(item['path']):
        raise HTTPException(404, 'Media not found')
    return stereo_flac(item['path'])


@app.post('/api/v1/endpoints/{endpoint_id}/play')
def endpoint_play(endpoint_id: str, item: EndpointPlayRequest, authorization: str | None = Header(default=None)):
    token = _require_token(authorization)
    endpoint = next((e for e in _all_endpoints() if e['id'] == endpoint_id), None)
    if not endpoint or endpoint.get('kind') != 'alsa' or not endpoint.get('address'):
        raise HTTPException(404, 'ALSA endpoint not found')
    if not get_media(item.media_id):
        raise HTTPException(404, 'Media not found')
    volume = max(0.0, min(float(item.volume), 0.49))
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
    if not get_media(item.media_id):
        raise HTTPException(404, 'Media not found')
    volume = sonos_set_volume(zone, min(int(item.volume), 49))
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
