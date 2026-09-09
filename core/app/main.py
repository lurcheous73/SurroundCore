import hmac
import json
import os
import time
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
from . import spotify_soloist, sonos_cloud, provider_bridge
from .podcasts import fetch_feed as fetch_podcast_feed
from .quality import output_route, source_request, pcm_playback_plan
from .playback import play_url, stop as stop_endpoint, status as endpoint_status
from .webui import streaming_setup_html
from .sources import (SOURCE_KINDS, CACHE_POLICIES, validate_source, source_status,
                      media_path, cache_file, purge_source_cache, cache_stats)
from .db import (init_db, upsert_media, list_media, get_media, upsert_endpoint, list_endpoints,
                 save_group, list_groups, get_group, delete_group, update_group_latency,
                 save_source, list_sources, get_source, delete_source, prune_source_media)
from .groups import GroupPlayback
from . import sessions

app = FastAPI(title='SurroundCore', version='0.5.0-dev')


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


class SpotifyConfigInput(BaseModel):
    api_key: str
    binary: str = '/opt/spotify/soloist'
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


@app.on_event('startup')
def startup():
    init_db()


@app.get('/api/v1/health')
def health():
    return {'ok': True, 'service': 'SurroundCore', 'version': '0.5.0-dev'}


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


@app.post('/api/v1/providers/spotify/configure')
def spotify_configure(item: SpotifyConfigInput, authorization: str | None = Header(default=None)):
    _require_token(authorization); config=item.model_dump(); save_provider_secret('spotify',config)
    return spotify_soloist.redacted(config)


@app.delete('/api/v1/providers/spotify/configure')
def spotify_disconnect(authorization: str | None = Header(default=None)):
    _require_token(authorization); save_provider_secret('spotify',None); return {'ok':True}


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
    _require_token(authorization)
    config=load_provider_secrets().get('sonos') or {}; config.update(item.model_dump()); save_provider_secret('sonos',config)
    return sonos_cloud.redacted(config)


@app.delete('/api/v1/providers/sonos/configure')
def sonos_cloud_disconnect(authorization: str | None = Header(default=None)):
    _require_token(authorization); save_provider_secret('sonos',None); return {'ok':True}


@app.get('/api/v1/providers/sonos/authorize-url')
def sonos_cloud_authorize_url(authorization: str | None = Header(default=None)):
    _require_token(authorization); config=_sonos_config(); state=uuid.uuid4().hex
    config['oauth_state']=state; save_provider_secret('sonos',config)
    return {'url':sonos_cloud.authorization_url(config,state),'state':state}


@app.post('/api/v1/providers/sonos/exchange-code')
def sonos_cloud_exchange(item: SonosCodeInput, authorization: str | None = Header(default=None)):
    _require_token(authorization); config=_sonos_config()
    try: token=sonos_cloud.exchange_code(config,item.code.strip())
    except Exception as exc: raise HTTPException(502,f'Sonos authorization failed: {exc}')
    config.update(token); save_provider_secret('sonos',config); return sonos_cloud.redacted(config)


@app.post('/api/v1/providers/sonos/tokens')
def sonos_cloud_tokens(item: SonosTokenInput, authorization: str | None = Header(default=None)):
    _require_token(authorization); config=_sonos_config(); config.update(item.model_dump(exclude_none=True)); config['obtained_at']=int(time.time())
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
    _require_token(authorization)
    if provider not in provider_bridge.SUPPORTED: raise HTTPException(404,'Unknown licensed provider')
    config={k:v for k,v in item.model_dump().items() if v not in (None,'')}; save_provider_secret(provider,config)
    return provider_bridge.redacted(config)


@app.delete('/api/v1/providers/bridge/{provider}/configure')
def bridge_disconnect(provider: str, authorization: str | None = Header(default=None)):
    _require_token(authorization)
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

@app.get('/api/v1/sources')
def sources_list(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    items=[]
    for source in list_sources():
        row=dict(source); row['status']=source_status(source); items.append(row)
    return {'sources':items,'kinds':list(SOURCE_KINDS),'cache_policies':list(CACHE_POLICIES)}


@app.post('/api/v1/sources')
def sources_save(item: LibrarySourceRequest, authorization: str | None = Header(default=None)):
    _require_token(authorization)
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
    _require_token(authorization)
    if not delete_source(source_id):
        raise HTTPException(404, 'Library source not found')
    return {'ok':True}


@app.post('/api/v1/sources/{source_id}/scan')
def sources_scan(source_id: str, authorization: str | None = Header(default=None)):
    _require_token(authorization)
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
    _require_token(authorization)
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
    _require_token(authorization)
    if not get_source(source_id):
        raise HTTPException(404, 'Library source not found')
    return {'ok':True,'source_id':source_id,'purged':purge_source_cache(source_id)}


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
    _require_token(authorization)
    endpoint = next((e for e in _all_endpoints() if e['id'] == endpoint_id), None)
    if not endpoint or endpoint.get('kind') != 'alsa':
        raise HTTPException(404, 'ALSA endpoint not found')
    media = get_media(media_id)
    if not media:
        raise HTTPException(404, 'Media not found')
    return {'endpoint': endpoint_id, 'media_id': media_id, 'plan': pcm_playback_plan(media, endpoint, device, load_settings())}


@app.post('/api/v1/endpoints/{endpoint_id}/play')
def endpoint_play(endpoint_id: str, item: EndpointPlayRequest, authorization: str | None = Header(default=None)):
    token = _require_token(authorization)
    endpoint = next((e for e in _all_endpoints() if e['id'] == endpoint_id), None)
    if not endpoint or endpoint.get('kind') != 'alsa' or not endpoint.get('address'):
        raise HTTPException(404, 'ALSA endpoint not found')
    media = get_media(item.media_id)
    if not media:
        raise HTTPException(404, 'Media not found')
    plan = pcm_playback_plan(media, endpoint, item.device, load_settings())
    if not plan.get('supported'):
        raise HTTPException(409, plan.get('reason') or 'Endpoint cannot play source natively')
    volume = max(0.0, min(float(item.volume), 1.0))
    result = _post_json(endpoint['address'].rstrip('/') + '/v1/play', {
        'url': _media_url(item.media_id, token), 'device': item.device, 'volume': volume,
        'position_seconds': item.position_seconds,
        'mode': plan.get('mode', 'direct'), 'source': plan.get('source'),
    }, token)
    session = sessions.start_library(endpoint_id, [media], position_seconds=item.position_seconds, plan=plan)
    return {'ok': True, 'endpoint': endpoint_id, 'volume': volume, 'plan': plan, 'session': session, 'result': result}


@app.post('/api/v1/endpoints/{endpoint_id}/programme')
def endpoint_programme(endpoint_id: str, item: EndpointProgrammeRequest, authorization: str | None = Header(default=None)):
    token = _require_token(authorization)
    endpoint = next((e for e in _all_endpoints() if e['id'] == endpoint_id), None)
    if not endpoint or endpoint.get('kind') != 'alsa' or not endpoint.get('address'):
        raise HTTPException(404, 'ALSA endpoint not found')
    if not item.media_ids:
        raise HTTPException(400, 'Programme requires media_ids')
    media = [get_media(media_id) for media_id in item.media_ids]
    if any(x is None for x in media):
        raise HTTPException(404, 'Programme media not found')
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
    volume = max(0.0, min(float(item.volume), 1.0))
    result = _post_json(endpoint['address'].rstrip('/') + '/v1/programme', {
        'urls': [_media_url(media_id, token) for media_id in item.media_ids],
        'device': item.device, 'volume': volume, 'position_seconds': item.position_seconds,
        'mode': 'direct', 'source': plans[0]['source'], 'sources': [p['source'] for p in plans],
    }, token)
    session = sessions.start_library(endpoint_id, media, position_seconds=item.position_seconds,
                                     plan={'mode':'direct','gapless':True,'tracks':len(media)})
    return {'ok': True, 'endpoint': endpoint_id, 'plan': session['plan'], 'session': session, 'result': result}


@app.get('/api/v1/sessions')
def playback_sessions(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    return {'sessions': sessions.list_views()}


@app.get('/api/v1/sessions/{endpoint_id}')
def playback_session(endpoint_id: str, authorization: str | None = Header(default=None)):
    _require_token(authorization)
    session = sessions.view(endpoint_id)
    if not session: raise HTTPException(404, 'Playback session not found')
    return session


def _session_endpoint(endpoint_id):
    endpoint = next((e for e in _all_endpoints() if e.get('id') == endpoint_id), None)
    if not endpoint or endpoint.get('kind') != 'alsa' or not endpoint.get('address'):
        raise HTTPException(404, 'ALSA endpoint not found')
    return endpoint


@app.post('/api/v1/sessions/{endpoint_id}/{control}')
def playback_session_control(endpoint_id: str, control: str, position_seconds: float | None = Query(default=None), authorization: str | None = Header(default=None)):
    token = _require_token(authorization); endpoint = _session_endpoint(endpoint_id)
    if not sessions.view(endpoint_id): raise HTTPException(404, 'Playback session not found')
    if control in ('pause','resume','stop'):
        result = _post_json(endpoint['address'].rstrip('/') + '/v1/' + control, {}, token)
        session = sessions.clear(endpoint_id) if control == 'stop' else sessions.set_state(endpoint_id, 'paused' if control == 'pause' else 'playing')
        return {'ok': True, 'session': session, 'result': result}
    if control == 'seek':
        if position_seconds is None: raise HTTPException(400, 'position_seconds required')
        target = max(0.0, float(position_seconds)); result = _post_json(endpoint['address'].rstrip('/') + '/v1/seek', {'position_seconds': target}, token)
        return {'ok': True, 'session': sessions.seek(endpoint_id, target), 'result': result}
    if control in ('next','previous'):
        target = sessions.control_target(endpoint_id, control)
        if target is None: raise HTTPException(409, f'{control} is not available')
        result = _post_json(endpoint['address'].rstrip('/') + '/v1/seek', {'position_seconds': target}, token)
        return {'ok': True, 'session': sessions.seek(endpoint_id, target), 'result': result}
    raise HTTPException(404, 'Unknown playback control')


@app.post('/api/v1/endpoints/{endpoint_id}/stop')
def endpoint_stop(endpoint_id: str, authorization: str | None = Header(default=None)):
    token = _require_token(authorization)
    endpoint = next((e for e in _all_endpoints() if e['id'] == endpoint_id), None)
    if not endpoint or endpoint.get('kind') != 'alsa' or not endpoint.get('address'):
        raise HTTPException(404, 'ALSA endpoint not found')
    result = _post_json(endpoint['address'].rstrip('/') + '/v1/stop', {}, token)
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
