import socket
import math
import re
from urllib.parse import quote

import httpx

USER_AGENT = 'SurroundCore/0.5'
DEFAULT_HOST = 'de1.api.radio-browser.info'


def _hosts():
    hosts=[]
    try:
        for info in socket.getaddrinfo('all.api.radio-browser.info', 443, type=socket.SOCK_STREAM):
            try:
                host=socket.gethostbyaddr(info[4][0])[0]
                if host.endswith('.api.radio-browser.info') and host not in hosts:
                    hosts.append(host)
            except Exception:
                pass
    except Exception:
        pass
    if DEFAULT_HOST not in hosts:
        hosts.append(DEFAULT_HOST)
    return hosts[:5]


def _get(path, params=None):
    last=None
    for host in _hosts():
        try:
            with httpx.Client(timeout=8.0, follow_redirects=True,
                              headers={'User-Agent': USER_AGENT}) as client:
                r=client.get(f'https://{host}{path}', params=params)
                r.raise_for_status()
                return r.json()
        except Exception as exc:
            last=exc
    raise RuntimeError(f'Radio Browser unavailable: {last}')

def _clean(s):
    return {
        'id': s.get('stationuuid'),
        'name': s.get('name') or 'Unknown station',
        'url': s.get('url_resolved') or s.get('url'),
        'homepage': s.get('homepage') or '',
        'favicon': s.get('favicon') or '',
        'country': s.get('country') or '',
        'countrycode': s.get('countrycode') or '',
        'state': s.get('state') or '',
        'language': s.get('language') or '',
        'tags': s.get('tags') or '',
        'codec': s.get('codec') or '',
        'bitrate': int(s.get('bitrate') or 0),
        'votes': int(s.get('votes') or 0),
        'clickcount': int(s.get('clickcount') or 0),
        'lastcheckok': bool(s.get('lastcheckok')),
        'geo_lat': s.get('geo_lat'),
        'geo_long': s.get('geo_long'),
    }


def popular(limit=40):
    rows=_get(f'/json/stations/topclick/{max(1,min(int(limit),100))}',
              {'hidebroken':'true'})
    return [_clean(x) for x in rows if x.get('url_resolved') or x.get('url')]


def search(name='', country='', language='', tag='', limit=60):
    params={'hidebroken':'true','limit':max(1,min(int(limit),100)),
            'order':'clickcount','reverse':'true'}
    if name: params['name']=name
    if country:
        if len(str(country).strip()) == 2: params['countrycode']=str(country).strip().upper()
        else: params['country']=country
    if language: params['language']=language
    if tag: params['tag']=tag
    rows=_get('/json/stations/search', params)
    return [_clean(x) for x in rows if x.get('url_resolved') or x.get('url')]



def _distance_km(lat1, lon1, lat2, lon2):
    r=6371.0088
    p1,p2=math.radians(float(lat1)),math.radians(float(lat2))
    dp=math.radians(float(lat2)-float(lat1))
    dl=math.radians(float(lon2)-float(lon1))
    a=math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return r*2*math.atan2(math.sqrt(a), math.sqrt(1-a))


def nearby(lat, lon, countrycode='', limit=40):
    wanted=max(1,min(int(limit),100))
    if countrycode:
        rows=_get('/json/stations/bycountrycodeexact/'+quote(countrycode.upper(), safe=''),
                  {'hidebroken':'true','order':'clickcount','reverse':'true','limit':1000})
    else:
        rows=_get('/json/stations/search', {'hidebroken':'true','has_geo_info':'true',
                  'order':'clickcount','reverse':'true','limit':1000})
    cleaned=[]
    for raw in rows:
        if not (raw.get('url_resolved') or raw.get('url')): continue
        item=_clean(raw)
        if item.get('geo_lat') is None or item.get('geo_long') is None: continue
        try: item['distance_km']=round(_distance_km(lat,lon,item['geo_lat'],item['geo_long']),1)
        except Exception: continue
        cleaned.append(item)
    cleaned.sort(key=lambda x:(x['distance_km'],-x.get('clickcount',0)))
    return cleaned[:wanted]


def search_local_first(name='', country='', language='', tag='', location=None, limit=60):
    wanted=max(1,min(int(limit),100)); location=location or {}
    fetch=max(wanted*4,200); rows=[]
    if location.get('countrycode') and not country:
        rows += search(name=name, country=location['countrycode'], language=language, tag=tag, limit=fetch)
    rows += search(name=name, country=country, language=language, tag=tag, limit=fetch)
    dedup={}
    for item in rows: dedup[item.get('id') or item.get('url')]=item
    local_names={str(location.get(k) or '').strip().casefold() for k in ('district','county','region','place')} - {''}
    cc=str(location.get('countrycode') or '').upper()
    for item in dedup.values():
        state=str(item.get('state') or '').strip().casefold(); same_region=state in local_names
        same_country=bool(cc and str(item.get('countrycode') or '').upper()==cc)
        dist=999999.0
        if location.get('lat') is not None and location.get('lon') is not None and item.get('geo_lat') is not None and item.get('geo_long') is not None:
            try: dist=round(_distance_km(location['lat'],location['lon'],item['geo_lat'],item['geo_long']),1); item['distance_km']=dist
            except Exception: pass
        item['local_tier']=0 if same_region else (1 if same_country else 2)
        item['_sort_distance']=dist
    out=sorted(dedup.values(), key=lambda x:(x['local_tier'],x['_sort_distance'],-x.get('clickcount',0)))
    for item in out: item.pop('_sort_distance',None)
    return out[:wanted]

def click(station_id):
    if not station_id:
        return None
    try:
        return _get('/json/url/'+quote(station_id, safe=''))
    except Exception:
        return None


def resolve_postcode(value):
    code=(value or '').strip()
    if not code:
        raise ValueError('Postcode / ZIP is required')
    compact=re.sub(r'\s+','',code).upper()
    if re.fullmatch(r'[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}',compact):
        r=httpx.get('https://api.postcodes.io/postcodes/'+quote(compact, safe=''), timeout=8.0)
        r.raise_for_status(); data=r.json().get('result') or {}
        return {'postcode':data.get('postcode') or code,'countrycode':'GB',
                'lat':data.get('latitude'),'lon':data.get('longitude'),
                'place':data.get('admin_district') or data.get('parliamentary_constituency') or 'United Kingdom',
                'district':data.get('admin_district') or '', 'county':data.get('admin_county') or '',
                'region':data.get('region') or ''}
    if re.fullmatch(r'\d{5}(?:-\d{4})?',compact):
        z=compact[:5]
        r=httpx.get('https://api.zippopotam.us/us/'+quote(z, safe=''), timeout=8.0)
        r.raise_for_status(); data=r.json(); place=(data.get('places') or [{}])[0]
        return {'postcode':data.get('post code') or z,'countrycode':'US',
                'lat':float(place.get('latitude')),'lon':float(place.get('longitude')),
                'place':', '.join(x for x in (place.get('place name'),place.get('state abbreviation')) if x),
                'district':place.get('place name') or '', 'county':'',
                'region':place.get('state') or place.get('state abbreviation') or ''}
    raise ValueError('Enter a UK postcode or 5-digit US ZIP code')
