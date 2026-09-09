import hmac
import json
import os
import urllib.parse
import urllib.request
import uuid
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import Response, HTMLResponse, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from .sonos import endpoints as sonos_endpoints, play_uri as sonos_play_uri, set_volume as sonos_set_volume, stop as sonos_stop
from .mdns_endpoints import endpoints as mdns_endpoints
from .upnp import endpoints as upnp_endpoints
from .media import scan
from .streaming import (ranged_file, stereo_flac_cache, stereo_flac_program_cache,
                        load_settings, save_settings, load_provider_secrets, save_provider_secret)
from .providers import provider_status, quality_policy
from . import bandcamp as bandcamp_provider
from .podcasts import fetch_feed as fetch_podcast_feed
from .quality import output_route, source_request
from .playback import play_url, stop as stop_endpoint, status as endpoint_status
from .webui import streaming_setup_html
from .db import (init_db, upsert_media, list_media, get_media, upsert_endpoint, list_endpoints,
                 save_group, list_groups, get_group, delete_group, update_group_latency)
from .groups import GroupPlayback

app = FastAPI(title='SurroundCore', version='0.4.0-dev')


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


class RadioStationInput(BaseModel):
    name: str
    url: str


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


@app.on_event('startup')
def startup():
    init_db()


@app.get('/api/v1/health')
def health():
    return {'ok': True, 'service': 'SurroundCore', 'version': '0.4.0-dev'}


@app.get('/setup/streaming', response_class=HTMLResponse)
def streaming_setup():
    return HTMLResponse(streaming_setup_html())


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


@app.get('/api/v1/streaming')
def streaming_overview(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    settings = load_settings()
    return {'settings': settings, 'quality': quality_policy(settings), 'providers': provider_status(settings)}


@app.get('/api/v1/streaming/route/{endpoint_id}')
def streaming_route(endpoint_id: str, channels: int = Query(default=2, ge=1, le=32), authorization: str | None = Header(default=None)):
    _require_token(authorization)
    endpoint = next((e for e in _all_endpoints() if e.get('id') == endpoint_id), None)
    if not endpoint:
        raise HTTPException(404, 'Endpoint not found')
    settings = load_settings()
    return {'source_request': source_request(settings, channels), 'output': output_route(endpoint, channels, settings), 'endpoint': endpoint}


@app.post('/api/v1/streaming/settings')
def streaming_settings(item: StreamingSettingsUpdate, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    update = {k: v for k, v in item.model_dump().items() if v is not None}
    settings = save_settings(update)
    return {'settings': settings, 'quality': quality_policy(settings)}


@app.post('/api/v1/streaming/radio')
def add_radio(item: RadioStationInput, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    parsed = urllib.parse.urlparse(item.url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise HTTPException(400, 'Radio URL must be http or https')
    settings = load_settings()
    stations = list(settings.get('radio_stations') or [])
    stations.append({'id': uuid.uuid4().hex[:12], 'name': item.name.strip(), 'url': item.url.strip()})
    settings = save_settings({'radio_stations': stations})
    return {'radio_stations': settings['radio_stations']}


@app.delete('/api/v1/streaming/radio/{station_id}')
def delete_radio(station_id: str, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    settings = load_settings()
    stations = [station for station in settings.get('radio_stations', []) if station.get('id') != station_id]
    settings = save_settings({'radio_stations': stations})
    return {'radio_stations': settings['radio_stations']}


@app.post('/api/v1/streaming/play')
def streaming_play(item: ProviderPlayInput, authorization: str | None = Header(default=None)):
    token = _require_token(authorization)
    settings = load_settings()
    if item.provider == 'internet_radio':
        station = next((station for station in settings.get('radio_stations', []) if station.get('id') == item.item_id), None)
        if not station:
            raise HTTPException(404, 'Radio station not found')
        url = station['url']
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
        url = episode['url']
    else:
        raise HTTPException(501, f'{item.provider} playback module is not installed')
    try:
        return play_url(item.endpoint_id, url, item.device)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post('/api/v1/playback/url')
def playback_url(item: PlaybackURLInput, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    parsed = urllib.parse.urlparse(item.url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc:
        raise HTTPException(400, 'Playback URL must be http or https')
    try:
        return play_url(item.endpoint_id, item.url, item.device)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.post('/api/v1/playback/{endpoint_id}/stop')
def playback_stop(endpoint_id: str, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try:
        return stop_endpoint(endpoint_id)
    except Exception as exc:
        raise HTTPException(502, str(exc))


@app.get('/api/v1/playback/{endpoint_id}/status')
def playback_status(endpoint_id: str, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    try:
        return endpoint_status(endpoint_id)
    except Exception as exc:
        raise HTTPException(502, str(exc))


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
    _require_token(authorization)
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
    _require_token(authorization)
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
    _require_token(authorization)
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
    _require_token(authorization)
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
        media_ids = item.media_ids or ([item.media_id] if item.media_id is not None else [])
        return {'ok': True, 'session': _group_playback.play(
            session_id, group, _all_endpoints(), media_ids, token, item.position_seconds, item.lead_ms)}
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


@app.head('/api/v1/programmes/stereo.flac')
def programme_head(media_ids: str, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    ids = [int(x) for x in media_ids.split(',') if x.strip()]
    items = [get_media(i) for i in ids]
    if not ids or any(item is None for item in items):
        raise HTTPException(404, 'Programme media not found')
    cached = stereo_flac_program_cache([item['path'] for item in items])
    return Response(media_type='audio/flac', headers={'Accept-Ranges':'bytes','Content-Length':str(os.path.getsize(cached))})


@app.get('/api/v1/programmes/stereo.flac')
def programme_stream(request: Request, media_ids: str, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    ids = [int(x) for x in media_ids.split(',') if x.strip()]
    items = [get_media(i) for i in ids]
    if not ids or any(item is None for item in items):
        raise HTTPException(404, 'Programme media not found')
    cached = stereo_flac_program_cache([item['path'] for item in items])
    return ranged_file(cached, request.headers.get('range'), 'SurroundCore Group Programme.flac')


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
