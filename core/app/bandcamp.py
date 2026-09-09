import hashlib
import secrets
import urllib.parse

import httpx

DEFAULT_BASE = 'https://bandcamp.com/api/subsonic'
CLIENT_NAME = 'SurroundCore'
API_VERSION = '1.16.1'


def _auth_params(config):
    username = config.get('username', '').strip()
    password = config.get('password', '')
    if not username or not password:
        raise RuntimeError('Bandcamp Subsonic credentials are not configured')
    salt = secrets.token_hex(8)
    token = hashlib.md5((password + salt).encode()).hexdigest()
    return {
        'u': username, 't': token, 's': salt,
        'v': API_VERSION, 'c': CLIENT_NAME, 'f': 'json',
    }


def _url(config, method):
    base = (config.get('server') or DEFAULT_BASE).rstrip('/')
    return f'{base}/rest/{method}.view'


def _response_payload(response):
    response.raise_for_status()
    data = response.json()
    payload = data.get('subsonic-response', data)
    if payload.get('status') == 'failed':
        error = payload.get('error') or {}
        raise RuntimeError(error.get('message') or 'Bandcamp Subsonic request failed')
    return payload


def call(config, method, params=None, timeout=20.0):
    query = _auth_params(config)
    query.update(params or {})
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(_url(config, method), params=query)
        return _response_payload(response)


def ping(config):
    payload = call(config, 'ping')
    return {'ok': payload.get('status') == 'ok', 'version': payload.get('version')}


def albums(config, size=200, offset=0):
    payload = call(config, 'getAlbumList2', {
        'type': 'alphabeticalByArtist', 'size': max(1, min(int(size), 500)),
        'offset': max(0, int(offset)),
    })
    return (payload.get('albumList2') or {}).get('album') or []


def album(config, album_id):
    payload = call(config, 'getAlbum', {'id': album_id})
    return payload.get('album') or {}


def stream_request(config, song_id, range_header=None):
    query = _auth_params(config)
    query['id'] = song_id
    url = _url(config, 'stream')
    headers = {}
    if range_header:
        headers['Range'] = range_header
    client = httpx.Client(timeout=None, follow_redirects=True)
    request = client.build_request('GET', url, params=query, headers=headers)
    response = client.send(request, stream=True)
    if response.status_code >= 400:
        body = response.read()
        response.close(); client.close()
        raise RuntimeError(f'Bandcamp stream failed: HTTP {response.status_code}: {body[:160]!r}')
    return client, response


def redacted_config(config):
    return {
        'server': config.get('server') or DEFAULT_BASE,
        'username': config.get('username', ''),
        'configured': bool(config.get('username') and config.get('password')),
    }
