import urllib.parse

import httpx

SUPPORTED={'tidal','qobuz','hdtracks','apple_music','audible'}


def redacted(config):
    config=config or {}
    return {
        'configured': bool(config.get('base_url')),
        'base_url': config.get('base_url',''),
        'client_id': config.get('client_id',''),
        'has_client_secret': bool(config.get('client_secret')),
        'has_access_token': bool(config.get('access_token')),
        'quality': 'highest_native',
    }


def _headers(config):
    out={'Accept':'application/json','User-Agent':'SurroundCore/0.5'}
    token=config.get('access_token') or config.get('token')
    if token: out['Authorization']='Bearer '+token
    return out


def _url(config,path):
    base=(config.get('base_url') or '').rstrip('/')
    if not base: raise RuntimeError('Provider bridge URL is not configured')
    return base+path


def status(config):
    with httpx.Client(timeout=10.0) as client:
        r=client.get(_url(config,'/v1/status'),headers=_headers(config))
        r.raise_for_status(); return r.json()


def search(config,query,limit=25):
    q=urllib.parse.urlencode({'q':query,'limit':max(1,min(int(limit),100)),
                              'quality':'highest_native'})
    with httpx.Client(timeout=15.0) as client:
        r=client.get(_url(config,'/v1/search?'+q),headers=_headers(config))
        r.raise_for_status(); return r.json()


def play(config,item_id,endpoint_id=None,quality=None):
    payload={
        'item_id':item_id,
        'endpoint_id':endpoint_id,
        'quality':quality or 'highest_native',
        'prefer_lossless':True,
        'prefer_airia':True,
        'allow_mqa_passthrough':True,
        'allow_downsample':False,
        'allow_downmix':False,
    }
    with httpx.Client(timeout=15.0) as client:
        r=client.post(_url(config,'/v1/play'),headers=_headers(config),json=payload)
        r.raise_for_status(); return r.json() if r.content else {'ok':True}
