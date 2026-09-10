from . import airplay, cast_driver, protocols
from . import playback as agent
from .sonos import (
    play_uri as sonos_play_uri, prepare_uri as sonos_prepare_uri,
    play as sonos_resume, pause as sonos_pause, stop as sonos_stop,
    seek as sonos_seek, get_volume as sonos_get_volume,
    set_volume as sonos_set_volume, get_mute as sonos_get_mute,
    set_mute as sonos_set_mute, state as sonos_state,
)
from .upnp import (
    play_uri as upnp_play_uri, pause as upnp_pause, resume as upnp_resume,
    stop as upnp_stop, seek as upnp_seek, state as upnp_state,
    set_volume as upnp_set_volume, set_mute as upnp_set_mute,
)

NATIVE_KINDS = {'sonos', 'airplay', 'upnp', 'cast'}

_CODEC_MIME = {
    'flac':'audio/flac','mp3':'audio/mpeg','aac':'audio/aac','alac':'audio/mp4',
    'wav':'audio/wav','pcm_s16le':'audio/wav','pcm_s24le':'audio/wav',
    'vorbis':'audio/ogg','opus':'audio/ogg','ac3':'audio/ac3','eac3':'audio/eac3',
    'dts':'audio/vnd.dts','truehd':'audio/true-hd','mlp':'audio/true-hd',
}

def media_mime(media):
    return _CODEC_MIME.get(str((media or {}).get('codec') or '').lower(), 'audio/mpeg')


def definition(endpoint):
    return protocols.protocol(str(endpoint.get('kind') or ''))


def controls(endpoint):
    item = definition(endpoint)
    return set(item.controls) if item else set()


def playable(endpoint):
    item = definition(endpoint)
    if not item or (endpoint.get('capabilities') or {}).get('discovered_only'):
        return False
    kind = endpoint.get('kind')
    if kind == 'alsa' and not (endpoint.get('capabilities') or {}).get('devices'):
        return False
    if kind in NATIVE_KINDS or kind == 'alsa':
        return bool(endpoint.get('address'))
    caps = endpoint.get('capabilities') or {}
    return bool(endpoint.get('address') and caps.get('control_api'))


def play_url(endpoint, url, *, title='SurroundCore', source='stream', device='default', volume=None,
             position_seconds=0.0, mode=None, media=None, plan_source=None):
    kind = endpoint.get('kind')
    endpoint_id = endpoint.get('id')
    if kind == 'sonos':
        uri = url
        if source == 'radio' and url.startswith('http://') and '.m3u8' not in url.lower():
            uri = 'x-rincon-mp3radio://' + url[7:]
        sonos_play_uri(endpoint, uri)
        if position_seconds:
            sonos_seek(endpoint, position_seconds)
        return {'transport': 'sonos', 'uri': uri, 'volume': sonos_get_volume(endpoint)}
    if kind == 'airplay':
        m = media or {'id': None, 'path': url, 'codec': 'stream', 'channels': 2,
                      'sample_rate': 0, 'bit_depth': 0, 'duration': 0.0,
                      'metadata': {'title': title}}
        return airplay.play(endpoint, url, m, position_seconds, 0.35 if volume is None else volume)
    if kind == 'upnp':
        upnp_play_uri(endpoint, url)
        if position_seconds:
            upnp_seek(endpoint, position_seconds)
        return {'transport': 'upnp', 'uri': url}
    if kind == 'cast':
        return cast_driver.play_uri(endpoint, url, content_type=media_mime(media) if media else None, title=title,
                                    stream_type='LIVE' if source == 'radio' else 'BUFFERED',
                                    position_seconds=position_seconds)
    return agent.play_url(endpoint_id, url, device=device, volume=volume,
                          position_seconds=position_seconds, mode=mode, source=plan_source)


def play_programme(endpoint, url, *, title='SurroundCore Queue', position_seconds=0.0, volume=None):
    kind = endpoint.get('kind')
    if kind == 'sonos':
        sonos_play_uri(endpoint, url)
        if position_seconds:
            sonos_seek(endpoint, position_seconds)
        return {'transport': 'sonos', 'uri': url, 'volume': sonos_get_volume(endpoint)}
    if kind == 'airplay':
        media = {'id': 'programme', 'path': url, 'codec': 'flac', 'channels': 2,
                 'sample_rate': 48000, 'bit_depth': 16, 'duration': 0.0,
                 'metadata': {'title': title}}
        return airplay.play(endpoint, url, media, position_seconds, 0.35 if volume is None else volume)
    if kind == 'upnp':
        upnp_play_uri(endpoint, url)
        if position_seconds:
            upnp_seek(endpoint, position_seconds)
        return {'transport': 'upnp', 'uri': url}
    if kind == 'cast':
        return cast_driver.play_uri(endpoint, url, content_type='audio/flac', title=title,
                                    stream_type='BUFFERED', position_seconds=position_seconds)
    return agent.play_url(endpoint.get('id'), url, volume=volume, position_seconds=position_seconds)


def status(endpoint):
    kind = endpoint.get('kind')
    if kind == 'sonos':
        return sonos_state(endpoint)
    if kind == 'airplay':
        return airplay.status(endpoint.get('id'))
    if kind == 'upnp':
        return upnp_state(endpoint)
    if kind == 'cast':
        return cast_driver.state(endpoint)
    return agent.status(endpoint.get('id'))


def pause(endpoint):
    kind = endpoint.get('kind')
    if kind == 'sonos': return sonos_pause(endpoint)
    if kind == 'airplay': return airplay.pause(endpoint.get('id'))
    if kind == 'upnp': return upnp_pause(endpoint)
    if kind == 'cast': return cast_driver.pause(endpoint)
    return agent.pause(endpoint.get('id'))


def resume(endpoint):
    kind = endpoint.get('kind')
    if kind == 'sonos': return sonos_resume(endpoint)
    if kind == 'airplay': return airplay.resume(endpoint.get('id'))
    if kind == 'upnp': return upnp_resume(endpoint)
    if kind == 'cast': return cast_driver.resume(endpoint)
    return agent.resume(endpoint.get('id'))


def stop(endpoint):
    kind = endpoint.get('kind')
    if kind == 'sonos': return sonos_stop(endpoint)
    if kind == 'airplay': return airplay.stop(endpoint.get('id'))
    if kind == 'upnp': return upnp_stop(endpoint)
    if kind == 'cast': return cast_driver.stop(endpoint)
    return agent.stop(endpoint.get('id'))


def seek(endpoint, seconds):
    kind = endpoint.get('kind')
    if kind == 'sonos': return sonos_seek(endpoint, seconds)
    if kind == 'airplay': return airplay.seek(endpoint.get('id'), seconds)
    if kind == 'upnp': return upnp_seek(endpoint, seconds)
    if kind == 'cast': return cast_driver.seek(endpoint, seconds)
    return agent.seek(endpoint.get('id'), seconds)


def set_volume(endpoint, volume):
    kind = endpoint.get('kind')
    if kind == 'sonos': return sonos_set_volume(endpoint, volume)
    if kind == 'airplay': return airplay.set_volume(endpoint.get('id'), volume)
    if kind == 'upnp': return upnp_set_volume(endpoint, volume)
    if kind == 'cast': return cast_driver.set_volume(endpoint, volume)
    result = agent.set_volume(endpoint.get('id'), volume)
    return result.get('volume', volume) if isinstance(result, dict) else volume


def set_mute(endpoint, muted):
    kind = endpoint.get('kind')
    if kind == 'sonos': return sonos_set_mute(endpoint, muted)
    if kind == 'airplay': return airplay.set_mute(endpoint.get('id'), muted)
    if kind == 'upnp': return upnp_set_mute(endpoint, muted)
    if kind == 'cast': return cast_driver.set_mute(endpoint, muted)
    result = agent.set_mute(endpoint.get('id'), muted)
    return result.get('muted', bool(muted)) if isinstance(result, dict) else bool(muted)


def prepare(endpoint, url, *, position_seconds=0.0, volume=None):
    kind = endpoint.get('kind')
    if kind == 'sonos':
        if volume is not None: sonos_set_volume(endpoint, int(volume))
        sonos_prepare_uri(endpoint, url)
        if position_seconds: sonos_seek(endpoint, position_seconds)
        return True
    caps = endpoint.get('capabilities') or {}
    if caps.get('control_api'):
        agent.prepare(endpoint.get('id'), url=url, position_seconds=position_seconds, volume=volume)
        return True
    return False


def start(endpoint):
    if endpoint.get('kind') == 'sonos':
        return sonos_resume(endpoint)
    return agent.start(endpoint.get('id'))
