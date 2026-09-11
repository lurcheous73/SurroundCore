import os
import urllib.parse
import httpx

PROVIDERS = {
    'musicbrainz': {'name':'MusicBrainz','auth':'none','role':['music-metadata','release-ids']},
    'coverartarchive': {'name':'Cover Art Archive','auth':'none','role':['album-art']},
    'discogs': {'name':'Discogs','auth':'token','role':['release-metadata','disc-identity','art']},
    'bandcamp': {'name':'Bandcamp','auth':'account','role':['purchases','artist-metadata','art']},
    'lastfm': {'name':'Last.fm','auth':'api-key','role':['scrobble-history','playlist-import','artist-metadata']},
    'listenbrainz': {'name':'ListenBrainz','auth':'token','role':['listening-history','recommendations']},
    'soundiiz': {'name':'Soundiiz','auth':'handoff','role':['playlist-import-export']},
    'tmdb': {'name':'TMDB','auth':'api-key','role':['concert-posters','concert-metadata']},
    'fanarttv': {'name':'fanart.tv','auth':'api-key','role':['artist-art','concert-art']},
}

USER_AGENT='SurroundCore/0.5 (+https://github.com/lurcheous73/SurroundCore)'

def catalog():
    return [{'id':key, **value} for key,value in PROVIDERS.items()]

def provider(provider_id):
    return PROVIDERS.get(str(provider_id or '').lower())
def _get(url, params=None, headers=None, timeout=15):
    h={'User-Agent':USER_AGENT,'Accept':'application/json'}
    h.update(headers or {})
    with httpx.Client(timeout=timeout,follow_redirects=True,headers=h) as client:
        r=client.get(url,params=params)
        r.raise_for_status()
        return r.json()

def search_musicbrainz(artist=None,album=None,track=None,limit=20):
    terms=[]
    if artist: terms.append(f'artist:"{artist}"')
    if album: terms.append(f'release:"{album}"')
    if track: terms.append(f'recording:"{track}"')
    if not terms: raise ValueError('artist, album or track is required')
    data=_get('https://musicbrainz.org/ws/2/release/',{'query':' AND '.join(terms),'fmt':'json','limit':max(1,min(int(limit),50))})
    return {'provider':'musicbrainz','items':data.get('releases') or []}

def search_discogs(query,token=None,limit=20):
    token=token or os.getenv('SURROUNDCORE_DISCOGS_TOKEN','')
    if not token: return {'provider':'discogs','authorization_required':True,'auth':'token'}
    data=_get('https://api.discogs.com/database/search',{'q':query,'type':'release','per_page':max(1,min(int(limit),50))},
              {'Authorization':'Discogs token='+token})
    return {'provider':'discogs','items':data.get('results') or []}
def search_lastfm(query,api_key=None,limit=20):
    api_key=api_key or os.getenv('SURROUNDCORE_LASTFM_API_KEY','')
    if not api_key: return {'provider':'lastfm','authorization_required':True,'auth':'api-key'}
    data=_get('https://ws.audioscrobbler.com/2.0/',{'method':'album.search','album':query,'api_key':api_key,'format':'json','limit':max(1,min(int(limit),50))})
    items=((data.get('results') or {}).get('albummatches') or {}).get('album') or []
    return {'provider':'lastfm','items':items}

def listenbrainz_history(username,count=100):
    data=_get('https://api.listenbrainz.org/1/user/'+urllib.parse.quote(username,safe='')+'/listens',{'count':max(1,min(int(count),1000))})
    return {'provider':'listenbrainz','payload':data.get('payload') or {}}

def handoff(provider_id):
    urls={'soundiiz':'https://soundiiz.com/','bandcamp':'https://bandcamp.com/','tmdb':'https://www.themoviedb.org/settings/api'}
    p=provider(provider_id)
    if not p: raise ValueError('unknown metadata provider')
    return {'provider':provider_id,'authorization_required':p.get('auth')!='none','auth':p.get('auth'),'url':urls.get(provider_id)}

def search(provider_id,query='',artist=None,album=None,track=None,limit=20):
    pid=str(provider_id or '').lower()
    if pid=='musicbrainz': return search_musicbrainz(artist,album or query,track,limit)
    if pid=='discogs': return search_discogs(query or ' '.join(x for x in (artist,album,track) if x),limit=limit)
    if pid=='lastfm': return search_lastfm(query or album or artist or track,limit=limit)
    return handoff(pid)
