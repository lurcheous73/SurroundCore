import os

import httpx

from .db import list_endpoints


def _endpoint(endpoint_id):
    for endpoint in list_endpoints():
        if endpoint.get('id') == endpoint_id:
            return endpoint
    raise ValueError('Endpoint not found')


def _agent_call(endpoint_id, path, payload=None):
    endpoint = _endpoint(endpoint_id)
    address = (endpoint.get('address') or '').rstrip('/')
    if not address:
        raise ValueError('Endpoint has no control address')
    if endpoint.get('kind') != 'alsa':
        raise ValueError('Endpoint does not use the SurroundCore agent')
    token = os.getenv('SURROUNDCORE_TOKEN', '')
    headers = {'Authorization': f'Bearer {token}'}
    with httpx.Client(timeout=8.0) as client:
        if payload is None:
            response = client.get(address + path, headers=headers)
        else:
            response = client.post(address + path, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()


def play_url(endpoint_id, url, device='default'):
    return _agent_call(endpoint_id, '/v1/play', {'url': url, 'device': device})


def stop(endpoint_id):
    return _agent_call(endpoint_id, '/v1/stop', {})


def status(endpoint_id):
    return _agent_call(endpoint_id, '/v1/status')
