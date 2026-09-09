import os
import hmac
from typing import Optional
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from .sonos import endpoints as sonos_endpoints
from .media import scan
from .db import init_db, upsert_media, list_media, get_media, upsert_endpoint, list_endpoints

app = FastAPI(title='SurroundCore', version='0.1.0')

class EndpointRegistration(BaseModel):
    id: str
    name: str
    kind: str
    address: Optional[str] = None
    capabilities: dict = Field(default_factory=dict)

@app.on_event('startup')
def startup():
    init_db()

@app.get('/api/v1/health')
def health():
    return {'ok': True, 'service': 'SurroundCore', 'version': '0.1.0'}

@app.get('/api/v1/endpoints')
def endpoints(authorization: str | None = Header(default=None)):
    _require_token(authorization)
    combined = {e['id']: e for e in list_endpoints()}
    try:
        for e in sonos_endpoints():
            combined[e['id']] = e
    except Exception:
        pass
    return {'endpoints': list(combined.values())}

def _require_token(authorization=None, token=None):
    expected = os.getenv('SURROUNDCORE_TOKEN', '')
    supplied = token or (authorization[7:] if authorization and authorization.startswith('Bearer ') else '')
    if not expected or not hmac.compare_digest(expected, supplied):
        raise HTTPException(401, 'Invalid SurroundCore token')

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
def stream(media_id: int, token: str | None = Query(default=None), authorization: str | None = Header(default=None)):
    _require_token(authorization, token)
    item = get_media(media_id)
    if not item or not os.path.isfile(item['path']):
        raise HTTPException(404, 'Media not found')
    return FileResponse(item['path'], filename=os.path.basename(item['path']))
