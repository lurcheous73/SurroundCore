import os
import httpx
from .db import list_endpoints

CONTROL_APIS = {
    'surround-agent-v2',
    'surroundcore-bridge-v1',
    'surroundcore-bluetooth-v1',
    'surroundcore-meridian-v1',
}


def _endpoint(endpoint_id):
    for endpoint in list_endpoints():
        if endpoint.get('id') == endpoint_id:
            return endpoint
    raise ValueError('Endpoint not found')


def _control_endpoint(endpoint_id):
    endpoint = _endpoint(endpoint_id)
    address = (endpoint.get('address') or '').rstrip('/')
    if not address:
        raise ValueError('Endpoint has no control address')
    api = str((endpoint.get('capabilities') or {}).get('control_api') or '')
    if endpoint.get('kind') != 'alsa' and api not in CONTROL_APIS:
        raise ValueError('Endpoint does not expose the SurroundCore control API')
    return endpoint, address


def _agent_call(endpoint_id, path, payload=None, timeout=12.0):
    _endpoint_obj, address = _control_endpoint(endpoint_id)
    token = os.getenv('SURROUNDCORE_TOKEN', '')
    headers = {'Authorization': f'Bearer {token}'} if token else {}
    with httpx.Client(timeout=timeout) as client:
        if payload is None:
            response = client.get(address + path, headers=headers)
        else:
            response = client.post(address + path, headers=headers, json=payload)
        response.raise_for_status()
        return response.json() if response.content else {'ok': True}


def play_url(endpoint_id, url, device='default', **options):
    body = {'url': url, 'device': device}
    body.update({k: v for k, v in options.items() if v is not None})
    return _agent_call(endpoint_id, '/v1/play', body, timeout=30.0)


def play_programme(endpoint_id, urls, device='default', **options):
    body = {'urls': list(urls), 'device': device}
    body.update({k: v for k, v in options.items() if v is not None})
    return _agent_call(endpoint_id, '/v1/programme', body, timeout=30.0)


def prepare(endpoint_id, **payload):
    return _agent_call(endpoint_id, '/v1/prepare', payload)


def start(endpoint_id):
    return _agent_call(endpoint_id, '/v1/start', {})


def pause(endpoint_id):
    return _agent_call(endpoint_id, '/v1/pause', {})


def resume(endpoint_id):
    return _agent_call(endpoint_id, '/v1/resume', {})


def stop(endpoint_id):
    return _agent_call(endpoint_id, '/v1/stop', {})


def seek(endpoint_id, seconds):
    return _agent_call(endpoint_id, '/v1/seek', {'position_seconds': float(seconds)})


def status(endpoint_id):
    return _agent_call(endpoint_id, '/v1/status')


def set_volume(endpoint_id, volume):
    return _agent_call(endpoint_id, '/v1/volume', {'volume': int(volume)})


def set_mute(endpoint_id, muted):
    return _agent_call(endpoint_id, '/v1/mute', {'muted': bool(muted)})
