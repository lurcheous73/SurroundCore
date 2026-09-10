import hashlib, json, re, time, uuid
from pathlib import Path
from .db import connect, get_media

def _slug(value):
    value=re.sub(r'[^a-z0-9]+','-',str(value or '').casefold()).strip('-')
    return value[:180] or 'unknown'

def init_catalog():
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS canonical_albums(
          id TEXT PRIMARY KEY, artist TEXT NOT NULL, title TEXT NOT NULL,
          sort_key TEXT NOT NULL UNIQUE, metadata_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS editions(
          id TEXT PRIMARY KEY, album_id TEXT NOT NULL, title TEXT NOT NULL,
          media_type TEXT, source_format TEXT, release_year TEXT,
          metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(album_id) REFERENCES canonical_albums(id));
        CREATE TABLE IF NOT EXISTS playback_history(
          id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,endpoint_id TEXT,media_id INTEGER,
          source TEXT,provider TEXT,started_at REAL NOT NULL,metadata_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE IF NOT EXISTS playlists(
          id TEXT PRIMARY KEY,name TEXT NOT NULL,kind TEXT NOT NULL DEFAULT 'playlist',
          owner_user_id INTEGER,items_json TEXT NOT NULL DEFAULT '[]',metadata_json TEXT NOT NULL DEFAULT '{}');
        ''')
        cols={r['name'] for r in con.execute('PRAGMA table_info(media)')}
        additions={
          'edition_id':'TEXT','origin':'TEXT','source_identifier':'TEXT','source_serial':'TEXT',
          'content_hash':'TEXT','export_blocked':'INTEGER NOT NULL DEFAULT 0','imported_at':'REAL'}
        for name,typ in additions.items():
            if name not in cols: con.execute(f'ALTER TABLE media ADD COLUMN {name} {typ}')

def ensure_album(artist,title,metadata=None):
    artist=str(artist or 'Unknown Artist').strip();title=str(title or 'Unknown Album').strip()
    key=_slug(artist)+'|'+_slug(title); aid='alb-'+hashlib.sha1(key.encode()).hexdigest()[:20]
    with connect() as con:
        con.execute('''INSERT INTO canonical_albums(id,artist,title,sort_key,metadata_json) VALUES(?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET artist=excluded.artist,title=excluded.title''',
          (aid,artist,title,key,json.dumps(metadata or {})))
    return aid

def ensure_edition(album_id,title,media_type=None,source_format=None,release_year=None,metadata=None):
    key='|'.join(map(str,(album_id,title,media_type or '',source_format or '',release_year or '')))
    eid='ed-'+hashlib.sha1(key.encode()).hexdigest()[:20]
    with connect() as con:
        con.execute('''INSERT INTO editions(id,album_id,title,media_type,source_format,release_year,metadata_json)
          VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET title=excluded.title,media_type=excluded.media_type,
          source_format=excluded.source_format,release_year=excluded.release_year,metadata_json=excluded.metadata_json''',
          (eid,album_id,str(title or 'Standard edition'),media_type,source_format,release_year,json.dumps(metadata or {})))
    return eid
def attach_media(media_id,origin=None,source_identifier=None,source_serial=None,content_hash=None,
                 export_blocked=False,canonical_album=None,edition_title=None,media_type=None,metadata=None):
    media=get_media(media_id)
    if not media: raise ValueError('media not found')
    tags=media.get('metadata') or {}; artist=tags.get('album_artist') or tags.get('artist') or 'Unknown Artist'
    album=canonical_album or tags.get('album') or Path(media.get('path','Unknown Album')).parent.name
    aid=ensure_album(artist,album,metadata)
    edition_title=edition_title or tags.get('edition') or tags.get('version') or origin or 'Standard edition'
    eid=ensure_edition(aid,edition_title,media_type,media.get('codec'),tags.get('date') or tags.get('year'),metadata)
    with connect() as con:
        con.execute('''UPDATE media SET edition_id=?,origin=?,source_identifier=?,source_serial=?,content_hash=?,
          export_blocked=?,imported_at=? WHERE id=?''',(eid,origin,source_identifier,source_serial,content_hash,
          1 if export_blocked else 0,time.time(),int(media_id)))
    return {'album_id':aid,'edition_id':eid}

def album_catalog():
    with connect() as con:
        albums=[]
        for a in con.execute('SELECT * FROM canonical_albums ORDER BY artist COLLATE NOCASE,title COLLATE NOCASE'):
            item=dict(a);item['metadata']=json.loads(item.pop('metadata_json') or '{}');item['editions']=[]
            for e in con.execute('SELECT * FROM editions WHERE album_id=? ORDER BY title COLLATE NOCASE',(item['id'],)):
                ed=dict(e);ed['metadata']=json.loads(ed.pop('metadata_json') or '{}')
                ed['tracks']=[dict(x) for x in con.execute('SELECT id,path,codec,channels,sample_rate,bit_depth,origin,source_identifier,export_blocked FROM media WHERE edition_id=? ORDER BY path',(ed['id'],))]
                item['editions'].append(ed)
            albums.append(item)
    return albums
def log_play(user_id,endpoint_id,media_id=None,source='library',provider=None,metadata=None):
    with connect() as con:
        cur=con.execute('''INSERT INTO playback_history(user_id,endpoint_id,media_id,source,provider,started_at,metadata_json)
          VALUES(?,?,?,?,?,?,?)''',(user_id,endpoint_id,media_id,source,provider,time.time(),json.dumps(metadata or {})))
        return cur.lastrowid

def history(limit=500,user_id=None):
    q='SELECT * FROM playback_history';args=[]
    if user_id is not None:q+=' WHERE user_id=?';args.append(int(user_id))
    q+=' ORDER BY started_at DESC LIMIT ?';args.append(max(1,min(int(limit),5000)))
    with connect() as con:
        rows=[]
        for r in con.execute(q,args):
            d=dict(r);d['metadata']=json.loads(d.pop('metadata_json') or '{}');rows.append(d)
        return rows

def save_playlist(name,items,kind='playlist',owner_user_id=None,metadata=None,playlist_id=None):
    if kind not in ('playlist','mixtape'): raise ValueError('invalid playlist kind')
    pid=playlist_id or 'pl-'+uuid.uuid4().hex[:16]
    with connect() as con:
        con.execute('''INSERT INTO playlists(id,name,kind,owner_user_id,items_json,metadata_json) VALUES(?,?,?,?,?,?)
          ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,items_json=excluded.items_json,
          metadata_json=excluded.metadata_json''',(pid,name,kind,owner_user_id,json.dumps(items),json.dumps(metadata or {})))
    return pid

def list_playlists():
    with connect() as con:
        return [dict(r)|{'items':json.loads(r['items_json'] or '[]'),'metadata':json.loads(r['metadata_json'] or '{}')}
                for r in con.execute('SELECT * FROM playlists ORDER BY name COLLATE NOCASE')]
