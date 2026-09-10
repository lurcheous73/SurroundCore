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


def catalog():
    return [{'id':key, **value} for key,value in PROVIDERS.items()]


def provider(provider_id):
    return PROVIDERS.get(str(provider_id or '').lower())
