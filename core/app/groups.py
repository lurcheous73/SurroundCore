import threading
import time
import urllib.parse
from dataclasses import dataclass, asdict

from .db import get_media
from .streaming import stereo_flac_cache, stereo_flac_program_cache
from .sources import media_path
from .sonos import prepare_uri as sonos_prepare_uri, play as sonos_play, seek as sonos_seek, set_volume as sonos_set_volume, stop as sonos_stop


@dataclass
class SessionMember:
    endpoint_id: str
    kind: str
    latency_ms: int
    command_at_ms: int
    state: str = 'pending'
    error: str | None = None


@dataclass
class PlaybackSession:
    id: str
    group_id: str
    media_ids: list
    position_s: float
    target_start_ms: int
    state: str
    created_ms: int
    members: list


class GroupPlayback:
    def __init__(self, public_url, post_json):
        self.public_url = public_url
        self.post_json = post_json
        self._lock = threading.RLock()
        self._sessions = {}
        self._stops = {}

    def _programme_url(self, media_ids, token):
        ids = ','.join(str(i) for i in media_ids)
        return f'{self.public_url()}/api/v1/programmes/stereo.flac?media_ids={ids}&token={urllib.parse.quote(token, safe="")}'

    def _find_endpoint(self, endpoint_id, endpoints):
        return next((e for e in endpoints if e.get('id') == endpoint_id), None)

    def _prepare(self, endpoint, source_url, token, position_s, volume):
        kind = endpoint.get('kind')
        if kind == 'sonos':
            if volume is not None:
                sonos_set_volume(endpoint, int(volume))
            sonos_prepare_uri(endpoint, source_url)
            if position_s > 0:
                sonos_seek(endpoint, position_s)
            return
        address = (endpoint.get('address') or '').rstrip('/')
        caps = endpoint.get('capabilities') or {}
        if address and caps.get('control_api') == 'surround-agent-v2':
            self.post_json(address + '/v1/prepare', {
                'url': source_url,
                'position_seconds': position_s,
                'volume': volume,
            }, token)
            return
        if kind == 'alsa' and address:
            return
        raise RuntimeError(f'Endpoint kind {kind!r} is not group-play capable yet')

    def _start_member(self, session_id, member, endpoint, source_url, token, position_s, volume, command_at_ms):
        delay = max(0.0, (command_at_ms - int(time.time() * 1000)) / 1000.0)
        stop_event = self._stops.get(session_id)
        if stop_event and stop_event.wait(delay):
            return
        try:
            kind = endpoint.get('kind')
            if kind == 'sonos':
                sonos_play(endpoint)
            else:
                address = (endpoint.get('address') or '').rstrip('/')
                caps = endpoint.get('capabilities') or {}
                if address and caps.get('control_api') == 'surround-agent-v2':
                    self.post_json(address + '/v1/start', {}, token)
                elif kind == 'alsa' and address:
                    self.post_json(address + '/v1/play', {
                        'url': source_url,
                        'position_seconds': position_s,
                        'volume': 0.35 if volume is None else volume,
                    }, token)
                else:
                    raise RuntimeError('No start adapter')
            member.state = 'playing'
        except Exception as exc:
            member.state = 'error'
            member.error = str(exc)

    def play(self, session_id, group, endpoints, media_ids, token, position_s=0.0, lead_ms=4000):
        if isinstance(media_ids, int):
            media_ids = [media_ids]
        media_ids = [int(i) for i in media_ids]
        media = [get_media(i) for i in media_ids]
        if not media_ids or any(item is None for item in media):
            raise RuntimeError('Media not found')
        try:
            paths=[media_path(item) for item in media]
        except OSError as exc:
            raise RuntimeError('Media source unavailable and no cached copy exists') from exc
        stereo_flac_program_cache(paths)
        source_url = self._programme_url(media_ids, token)
        now_ms = int(time.time() * 1000)
        target_ms = now_ms + max(1500, int(lead_ms))
        members = []
        resolved = []
        for cfg in group.get('members', []):
            if not cfg.get('enabled', True):
                continue
            endpoint = self._find_endpoint(cfg['endpoint_id'], endpoints)
            if not endpoint:
                raise RuntimeError(f"Endpoint offline: {cfg['endpoint_id']}")
            latency_ms = max(0, int(cfg.get('latency_ms', 0)))
            command_at_ms = target_ms - latency_ms
            member = SessionMember(cfg['endpoint_id'], endpoint.get('kind', 'unknown'), latency_ms, command_at_ms)
            members.append(member)
            resolved.append((member, endpoint, cfg.get('volume')))
        if not members:
            raise RuntimeError('Group has no enabled endpoints')
        session = PlaybackSession(session_id, group['id'], media_ids, float(position_s), target_ms,
                                  'preparing', now_ms, members)
        with self._lock:
            self._sessions[session_id] = session
            self._stops[session_id] = threading.Event()
        try:
            for member, endpoint, volume in resolved:
                self._prepare(endpoint, source_url, token, position_s, volume)
                member.state = 'ready'
        except Exception:
            self.stop(session_id, endpoints, token)
            raise
        session.state = 'scheduled'
        for member, endpoint, volume in resolved:
            threading.Thread(target=self._start_member,
                args=(session_id, member, endpoint, source_url, token, position_s, volume, member.command_at_ms),
                daemon=True).start()
        threading.Thread(target=self._mark_running, args=(session_id, target_ms), daemon=True).start()
        return self.status(session_id)

    def _mark_running(self, session_id, target_ms):
        time.sleep(max(0.0, (target_ms - int(time.time() * 1000)) / 1000.0) + 0.15)
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.state == 'scheduled':
                session.state = 'playing' if any(m.state == 'playing' for m in session.members) else 'error'

    def stop(self, session_id, endpoints, token):
        with self._lock:
            session = self._sessions.get(session_id)
            stop_event = self._stops.get(session_id)
            if stop_event:
                stop_event.set()
        if not session:
            return False
        for member in session.members:
            endpoint = self._find_endpoint(member.endpoint_id, endpoints)
            if not endpoint:
                continue
            try:
                if endpoint.get('kind') == 'sonos':
                    sonos_stop(endpoint)
                else:
                    address = (endpoint.get('address') or '').rstrip('/')
                    if address:
                        self.post_json(address + '/v1/stop', {}, token)
            except Exception:
                pass
            member.state = 'stopped'
        session.state = 'stopped'
        return True

    def status(self, session_id):
        with self._lock:
            session = self._sessions.get(session_id)
            return asdict(session) if session else None

    def list_sessions(self):
        with self._lock:
            return [asdict(s) for s in self._sessions.values()]
