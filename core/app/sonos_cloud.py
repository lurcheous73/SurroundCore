import base64
import time
import urllib.parse

import httpx

LOGIN='https://api.sonos.com/login/v3/oauth'
TOKEN='https://api.sonos.com/login/v3/oauth/access'
CONTROL='https://api.ws.sonos.com/control/api/v1'


def redacted(config):
    config=config or {}
    return {
        'configured': bool(config.get('client_id') and config.get('client_secret')),
        'authorised': bool(config.get('access_token') or config.get('refresh_token')),
        'client_id': config.get('client_id',''),
        'redirect_uri': config.get('redirect_uri',''),
        'household_id': config.get('household_id',''),
    }


def authorization_url(config, state):
    q=urllib.parse.urlencode({
        'client_id':config['client_id'],'response_type':'code','state':state,
        'scope':'playback-control-all','redirect_uri':config['redirect_uri'],
    })
    return LOGIN+'?'+q


def _basic(config):
    raw=(config['client_id']+':'+config['client_secret']).encode()
    return 'Basic '+base64.b64encode(raw).decode()


def exchange_code(config, code):
    with httpx.Client(timeout=15.0) as client:
        r=client.post(TOKEN, headers={
            'Authorization':_basic(config),
            'Content-Type':'application/x-www-form-urlencoded;charset=utf-8',
        }, data={'grant_type':'authorization_code','code':code,
                 'redirect_uri':config['redirect_uri']})
        r.raise_for_status(); data=r.json()
    data['obtained_at']=int(time.time())
    return data


def refresh(config):
    with httpx.Client(timeout=15.0) as client:
        r=client.post(TOKEN, headers={
            'Authorization':_basic(config),
            'Content-Type':'application/x-www-form-urlencoded;charset=utf-8',
        }, data={'grant_type':'refresh_token','refresh_token':config['refresh_token']})
        r.raise_for_status(); data=r.json()
    data['obtained_at']=int(time.time())
    if not data.get('refresh_token'):
        data['refresh_token']=config.get('refresh_token')
    return data


def _headers(config):
    return {'Authorization':'Bearer '+config['access_token'],
            'Content-Type':'application/json','User-Agent':'SurroundCore/0.5'}


def _get(config, path):
    with httpx.Client(timeout=15.0) as client:
        r=client.get(CONTROL+path,headers=_headers(config))
        r.raise_for_status(); return r.json()


def _post(config, path, payload):
    with httpx.Client(timeout=15.0) as client:
        r=client.post(CONTROL+path,headers=_headers(config),json=payload)
        r.raise_for_status()
        return r.json() if r.content else {'ok':True}


def households(config):
    return _get(config,'/households').get('households',[])


def groups(config, household_id):
    return _get(config,f'/households/{urllib.parse.quote(household_id,safe="")}/groups')


def favorites(config, household_id):
    return _get(config,f'/households/{urllib.parse.quote(household_id,safe="")}/favorites')


def load_favorite(config, group_id, favorite_id):
    gid=urllib.parse.quote(group_id,safe='')
    return _post(config,f'/groups/{gid}/favorites',{
        'favoriteId':favorite_id,'playOnCompletion':True,
    })
