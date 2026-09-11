import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title='SurroundCore Remote Transport', version='0.1')
MODE = os.getenv('SURROUNDCORE_REMOTE_MODE', 'unconfigured')

REMOTE_ALLOW = {
    'playback', 'stream', 'queue', 'zone_control',
    'metadata_edit', 'artwork_edit', 'rip_control', 'rip_upload'
}
REMOTE_DENY = {
    'delete_media', 'delete_album', 'delete_user', 'wipe_storage',
    'destroy_pool', 'factory_reset', 'os_update', 'hardware_reconfigure'
}

class RouteProbe(BaseModel):
    local_core_reachable: bool = False

@app.get('/v1/health')
def health():
    return {'ok': True, 'service': 'SurroundCore Remote Transport', 'version': '0.1'}

@app.get('/v1/capabilities')
def capabilities():
    return {
        'mode': MODE,
        'carrier': None,
        'configured': MODE != 'unconfigured',
        'remote_allow': sorted(REMOTE_ALLOW),
        'remote_deny': sorted(REMOTE_DENY),
        'pairing': 'reserved',
        'app_routing': 'local-first',
    }

@app.post('/v1/route')
def route(item: RouteProbe):
    if item.local_core_reachable:
        return {'route': 'local', 'remote_required': False}
    if MODE == 'unconfigured':
        return {'route': 'unavailable', 'remote_required': True, 'reason': 'remote carrier not configured'}
    return {'route': 'remote', 'remote_required': True, 'carrier': MODE}

@app.get('/v1/policy/{action}')
def policy(action: str):
    if action in REMOTE_DENY:
        raise HTTPException(403, 'This action is blocked for remote sessions')
    return {'action': action, 'remote_allowed': action in REMOTE_ALLOW}
