import os
import urllib.parse
import httpx
from . import metadata_settings

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
    if not metadata_settings.enabled('musicbrainz'):
        return {'provider':'musicbrainz','items':[]}
    terms=[]
    if artist: terms.append(f'artist:"{artist}"')
    if album: terms.append(f'release:"{album}"')
    if track: terms.append(f'recording:"{track}"')
    if not terms: raise ValueError('artist, album or track is required')
    data=_get('https://musicbrainz.org/ws/2/release/',{'query':' AND '.join(terms),'fmt':'json','limit':max(1,min(int(limit),50))})
    return {'provider':'musicbrainz','items':data.get('releases') or []}

def search_discogs(query,token=None,limit=20):
    if not metadata_settings.enabled('discogs'):
        return {'provider':'discogs','items':[]}
    token=token or metadata_settings.secret('discogs') or os.getenv('SURROUNDCORE_DISCOGS_TOKEN','')
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


def _plain(value):
    import re
    return re.sub(r'[^a-z0-9]+',' ',str(value or '').casefold()).strip()

def _mb_artist(item):
    parts=[]
    for x in item.get('artist-credit') or []:
        if isinstance(x,dict): parts.append(str(x.get('name') or (x.get('artist') or {}).get('name') or ''))
    return ''.join(parts).strip()

def _mb_release_detail(release_id):
    return _get('https://musicbrainz.org/ws/2/release/'+urllib.parse.quote(str(release_id),safe=''),
        {'inc':'recordings+artist-credits','fmt':'json'},timeout=20)


def candidate_search(artist,album,tracks=None,limit=12):
    base=search_musicbrainz(artist,album,None,max(limit,8)).get('items') or []
    wanted=[_plain(x) for x in (tracks or []) if _plain(x)]
    items=[]
    for raw in base[:max(1,min(int(limit),20))]:
        rid=raw.get('id'); detail=raw
        if wanted and rid:
            try: detail=_mb_release_detail(rid)
            except Exception: detail=raw
        media=detail.get('media') or raw.get('media') or []
        track_rows=[]; formats=[]
        for med in media:
            if med.get('format'): formats.append(str(med.get('format')))
            for tr in med.get('tracks') or []:
                rec=tr.get('recording') or {}; title=rec.get('title') or tr.get('title')
                if title: track_rows.append(str(title))

        exact=0
        if wanted and track_rows:
            local=set(wanted); remote=[_plain(x) for x in track_rows]
            exact=sum(1 for x in remote if x in local)
        date=str(detail.get('date') or raw.get('date') or '')
        country=str(detail.get('country') or raw.get('country') or '')
        score=exact*100 - abs(len(track_rows)-len(wanted))*5 if wanted else 0
        items.append({'provider':'musicbrainz','id':rid,'artist':_mb_artist(detail) or _mb_artist(raw) or artist,
            'title':detail.get('title') or raw.get('title') or album,'year':date[:4] if date else '',
            'country':country,'format':'/'.join(sorted(set(formats))),'disc_count':len(media),
            'track_count':len(track_rows) or sum(int(x.get('track-count') or 0) for x in media),
            'tracks':track_rows,'exact_track_matches':exact,'score':score,
            'artwork_url':('https://coverartarchive.org/release/'+str(rid)+'/front-500') if rid else None})
    items.sort(key=lambda x:(x.get('score',0),x.get('exact_track_matches',0),x.get('year','')),reverse=True)
    return {'provider':'combined','items':items}

def candidate_search_toc(toc,limit=12):
    parts=[x for x in str(toc or '').split() if x.isdigit()]
    if len(parts) < 4: raise ValueError('invalid MusicBrainz disc TOC')
    data=_get('https://musicbrainz.org/ws/2/discid/-',{
        'toc':' '.join(parts),'inc':'recordings+artist-credits','fmt':'json'},timeout=20)
    releases=data.get('releases') or []
    items=[]
    for raw in releases[:max(1,min(int(limit),20))]:
        rid=raw.get('id'); media=raw.get('media') or []; formats=[]; tracks=[]
        for med in media:
            if med.get('format'): formats.append(str(med.get('format')))
            for tr in med.get('tracks') or []:
                rec=tr.get('recording') or {}; title=rec.get('title') or tr.get('title')
                if title: tracks.append(str(title))
        date=str(raw.get('date') or ''); country=str(raw.get('country') or '')
        items.append({'provider':'musicbrainz','id':rid,'artist':_mb_artist(raw),
            'title':raw.get('title') or 'Unknown Album','year':date[:4] if date else '',
            'country':country,'format':'/'.join(sorted(set(formats))),'disc_count':len(media),
            'track_count':len(tracks) or sum(int(x.get('track-count') or 0) for x in media),
            'tracks':tracks,'exact_track_matches':0,'score':1000 if tracks else 0,
            'artwork_url':('https://coverartarchive.org/release/'+str(rid)+'/front-500') if rid else None})
    return {'provider':'musicbrainz-disc-toc','items':items}


def _art_choice(source, source_id, title, artist, detail, preview_url, image_url):
    if not image_url:
        return None
    return {
        'id': f'{source}:{source_id}', 'source': source, 'provider': source, 'title': title or 'Artwork',
        'artist': artist or '', 'detail': detail or '',
        'preview_url': preview_url or image_url, 'image_url': image_url,
    }


def _cover_art_archive_choice(release, artist, album):
    if not metadata_settings.enabled('coverartarchive'):
        return None
    rid = release.get('id')
    if not rid:
        return None
    try:
        data = _get('https://coverartarchive.org/release/' + urllib.parse.quote(str(rid), safe=''), timeout=15)
    except Exception:
        return None
    images = data.get('images') or []
    if not images:
        return None
    image = next((x for x in images if x.get('front') is True), images[0])
    original = image.get('image')
    thumbs = image.get('thumbnails') or {}
    preview = thumbs.get('250') or thumbs.get('small') or original
    date = str(release.get('date') or '')
    detail = ' · '.join(x for x in (date[:4], str(release.get('country') or ''), 'MusicBrainz') if x)
    return _art_choice('musicbrainz', rid, release.get('title') or album,
                       _mb_artist(release) or artist, detail, preview, original)


def _audiodb_artwork(artist, album):
    if not metadata_settings.enabled('theaudiodb'):
        return []
    key = metadata_settings.secret('theaudiodb') or '123'
    try:
        data = _get(f'https://www.theaudiodb.com/api/v1/json/{urllib.parse.quote(key, safe="")}/searchalbum.php',
                    {'s': artist or '', 'a': album or ''}, timeout=15)
    except Exception:
        return []
    out = []
    for row in (data.get('album') or [])[:12]:
        image = row.get('strAlbumThumbHQ') or row.get('strAlbumThumb')
        preview = row.get('strAlbumThumb') or image
        item = _art_choice('theaudiodb', row.get('idAlbum') or image,
                           row.get('strAlbum') or album, row.get('strArtist') or artist,
                           ' · '.join(x for x in (str(row.get('intYearReleased') or ''), str(row.get('strReleaseFormat') or ''), 'TheAudioDB') if x),
                           preview, image)
        if item: out.append(item)
    return out


def _bandcamp_artwork(artist, album):
    if not metadata_settings.enabled('bandcamp_artwork'):
        return []
    try:
        data = _get('https://bandcamp.com/api/fuzzysearch/2/app_autocomplete',
                    {'q': ' '.join(x for x in (artist, album) if x), 'param_with_locations': 'true'}, timeout=15)
    except Exception:
        return []
    out=[]; seen=set()
    for row in data.get('results') or []:
        typ=row.get('type'); sid=row.get('id') if typ=='a' else row.get('album_id') if typ=='t' else None
        title=row.get('name') if typ=='a' else row.get('album_name') if typ=='t' else None
        image=row.get('img')
        if not sid or not title or not image or str(sid) in seen: continue
        seen.add(str(sid))
        item=_art_choice('bandcamp', sid, title, row.get('band_name') or artist, 'Bandcamp', image, image)
        if item: out.append(item)
    return out[:12]


def _discogs_artwork(artist, album):
    if not metadata_settings.enabled('discogs'):
        return []
    token = metadata_settings.secret('discogs') or os.getenv('SURROUNDCORE_DISCOGS_TOKEN', '')
    if not token:
        return []
    try:
        data = _get('https://api.discogs.com/database/search',
                    {'artist': artist or '', 'release_title': album or '', 'type': 'release', 'per_page': 20},
                    {'Authorization': 'Discogs token=' + token}, timeout=15)
    except Exception:
        return []
    out=[]
    for row in data.get('results') or []:
        image=row.get('cover_image') or row.get('thumb')
        preview=row.get('thumb') or image
        title=str(row.get('title') or album)
        if ' - ' in title: title=title.split(' - ',1)[1]
        formats='/'.join(row.get('format') or [])
        detail=' · '.join(x for x in ('Discogs', str(row.get('year') or ''), str(row.get('country') or ''), formats) if x)
        item=_art_choice('discogs', row.get('id') or image, title, artist, detail, preview, image)
        if item: out.append(item)
    return out[:12]


def artwork_candidates(artist, album, limit=20):
    clean_artist=str(artist or '').strip(); clean_album=str(album or '').strip()
    if not clean_album:
        raise ValueError('album is required')
    items=[]
    if metadata_settings.enabled('musicbrainz'):
        try:
            releases=search_musicbrainz(clean_artist, clean_album, None, min(max(int(limit), 8), 20)).get('items') or []
            for release in releases[:8]:
                choice=_cover_art_archive_choice(release, clean_artist, clean_album)
                if choice: items.append(choice)
        except Exception:
            pass
    items.extend(_audiodb_artwork(clean_artist, clean_album))
    items.extend(_bandcamp_artwork(clean_artist, clean_album))
    items.extend(_discogs_artwork(clean_artist, clean_album))
    seen=set(); result=[]
    for item in items:
        key=str(item.get('image_url') or item.get('id') or '').lower()
        if not key or key in seen: continue
        seen.add(key); result.append(item)
        if len(result) >= max(1,min(int(limit),50)): break
    return {'items': result, 'providers': metadata_settings.public_settings()['providers']}
