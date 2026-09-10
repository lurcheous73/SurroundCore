import mimetypes
import threading
import time
import pychromecast
from pychromecast.config import APP_MEDIA_RECEIVER
from pychromecast.error import RequestFailed

_LOCK = threading.RLock()
_CASTS = {}


def _uuid_key(value):
    return str(value or '').replace('-', '').lower()


def _connect(endpoint, timeout=6):
    host = str(endpoint.get('address') or '').split(':')[0]
    if not host:
        raise RuntimeError('Chromecast address unavailable')
    wanted = _uuid_key(str(endpoint.get('id') or '').removeprefix('cast:'))
    with _LOCK:
        cached = _CASTS.get(wanted or host)
        if cached:
            try:
                cached.wait(timeout=2)
                return cached
            except Exception:
                _CASTS.pop(wanted or host, None)
    casts, browser = pychromecast.get_chromecasts(timeout=timeout, known_hosts=[host])
    try:
        cast = next((c for c in casts if not wanted or _uuid_key(c.uuid) == wanted), casts[0] if casts else None)
        if not cast:
            raise RuntimeError(f'Chromecast not reachable at {host}')
        cast.wait(timeout=timeout)
        with _LOCK:
            _CASTS[wanted or host] = cast
        return cast
    finally:
        browser.stop_discovery()


def play_uri(endpoint, url, content_type=None, title=None, stream_type='LIVE', position_seconds=0.0):
    cast = _connect(endpoint)
    mime = content_type or mimetypes.guess_type(url.split('?', 1)[0])[0] or 'audio/mpeg'
    cast.start_app(APP_MEDIA_RECEIVER, force_launch=True, timeout=8)
    cast.media_controller.play_media(
        url, mime, title=title or 'SurroundCore',
        current_time=max(0.0, float(position_seconds or 0.0)),
        autoplay=True, stream_type=stream_type)
    cast.media_controller.block_until_active(timeout=8)
    return state(endpoint)


def pause(endpoint):
    cast = _connect(endpoint)
    cast.media_controller.pause()
    return True


def resume(endpoint):
    cast = _connect(endpoint)
    cast.media_controller.play()
    return True


def stop(endpoint):
    cast = _connect(endpoint)
    try:
        cast.media_controller.stop()
    except RequestFailed:
        return False
    return True


def seek(endpoint, seconds):
    cast = _connect(endpoint)
    cast.media_controller.seek(max(0.0, float(seconds or 0.0)))
    return float(seconds or 0.0)


def get_volume(endpoint):
    cast = _connect(endpoint)
    cast.wait(timeout=3)
    return round(float(cast.status.volume_level or 0.0) * 100) if cast.status else 0


def set_volume(endpoint, volume):
    cast = _connect(endpoint)
    level = max(0.0, min(float(volume) / 100.0, 1.0))
    cast.set_volume(level)
    return round(level * 100)


def get_mute(endpoint):
    cast = _connect(endpoint)
    cast.wait(timeout=3)
    return bool(cast.status.volume_muted) if cast.status else False


def set_mute(endpoint, muted):
    cast = _connect(endpoint)
    cast.set_volume_muted(bool(muted))
    return bool(muted)


def state(endpoint):
    cast = _connect(endpoint)
    cast.media_controller.update_status()
    time.sleep(0.15)
    st = cast.media_controller.status
    cs = cast.status
    return {
        'state': st.player_state or 'UNKNOWN',
        'volume': round(float(cs.volume_level or 0.0) * 100) if cs else None,
        'muted': bool(cs.volume_muted) if cs else False,
        'title': st.title or '', 'artist': st.artist or '', 'album': st.album_name or '',
        'uri': st.content_id or '', 'content_type': st.content_type or '',
        'position_seconds': float(st.current_time or 0.0),
        'duration': float(st.duration or 0.0) if st.duration is not None else 0.0,
    }
