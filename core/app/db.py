import json
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = None

def _db_path():
    return DB_PATH or os.path.join(os.getenv('SURROUNDCORE_DATA', '/data'), 'surroundcore.sqlite3')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL UNIQUE,
  codec TEXT,
  channels INTEGER,
  channel_layout TEXT,
  sample_rate INTEGER,
  bit_depth INTEGER,
  bitrate INTEGER,
  duration REAL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS library_sources (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT,
  cache_policy TEXT NOT NULL DEFAULT 'off',
  config_json TEXT NOT NULL DEFAULT '{}',
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS endpoints (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  address TEXT,
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS playback_groups (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS playback_group_members (
  group_id TEXT NOT NULL,
  endpoint_id TEXT NOT NULL,
  latency_ms INTEGER NOT NULL DEFAULT 0,
  volume INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(group_id, endpoint_id),
  FOREIGN KEY(group_id) REFERENCES playback_groups(id) ON DELETE CASCADE
);
'''

@contextmanager
def connect():
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()

def init_db():
    with connect() as con:
        con.executescript(SCHEMA)
        cols={r['name'] for r in con.execute('PRAGMA table_info(media)')}
        if 'source_id' not in cols:
            con.execute('ALTER TABLE media ADD COLUMN source_id TEXT')
        if 'bitrate' not in cols:
            con.execute('ALTER TABLE media ADD COLUMN bitrate INTEGER')

def upsert_media(item):
    values=(item['path'], item.get('codec'), item.get('channels'), item.get('channel_layout'),
            item.get('sample_rate'), item.get('bit_depth'), item.get('bitrate'), item.get('duration'),
            json.dumps(item.get('metadata') or {}), item.get('source_id'))
    with connect() as con:
        con.execute('''INSERT INTO media(path,codec,channels,channel_layout,sample_rate,bit_depth,bitrate,duration,metadata_json,source_id)
        VALUES(?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(path) DO UPDATE SET
          codec=excluded.codec, channels=excluded.channels,
          channel_layout=excluded.channel_layout, sample_rate=excluded.sample_rate,
          bit_depth=excluded.bit_depth, bitrate=excluded.bitrate, duration=excluded.duration,
          metadata_json=excluded.metadata_json, source_id=excluded.source_id''', values)

def _media_row(row):
    d=dict(row); d['metadata']=json.loads(d.pop('metadata_json') or '{}'); return d

def list_media():
    with connect() as con:
        return [_media_row(r) for r in con.execute('SELECT * FROM media ORDER BY path')]

def get_media(media_id):
    with connect() as con:
        row=con.execute('SELECT * FROM media WHERE id=?',(media_id,)).fetchone()
        return _media_row(row) if row else None


def delete_media(media_id):
    media_id=int(media_id)
    with connect() as con:
        row=con.execute('SELECT id,edition_id FROM media WHERE id=?',(media_id,)).fetchone()
        if not row: return False
        edition_id=row['edition_id'] if 'edition_id' in row.keys() else None
        for q in con.execute('SELECT user_id,endpoint_id,queue_json FROM user_zone_queues').fetchall():
            try: ids=json.loads(q['queue_json'] or '[]')
            except Exception: ids=[]
            clean=[x for x in ids if int(x)!=media_id]
            if clean!=ids:
                con.execute('UPDATE user_zone_queues SET queue_json=? WHERE user_id=? AND endpoint_id=?',
                            (json.dumps(clean),q['user_id'],q['endpoint_id']))
        for pl in con.execute('SELECT id,items_json FROM playlists').fetchall():
            try: items=json.loads(pl['items_json'] or '[]')
            except Exception: items=[]
            clean=[]
            for item in items:
                try: mid=int(item.get('media_id')) if isinstance(item,dict) else int(item)
                except Exception: mid=None
                if mid!=media_id: clean.append(item)
            if clean!=items: con.execute('UPDATE playlists SET items_json=? WHERE id=?',(json.dumps(clean),pl['id']))
        con.execute('UPDATE playback_history SET media_id=NULL WHERE media_id=?',(media_id,))
        con.execute('DELETE FROM media WHERE id=?',(media_id,))
        if edition_id:
            remaining=con.execute('SELECT COUNT(*) FROM media WHERE edition_id=?',(edition_id,)).fetchone()[0]
            if remaining==0:
                album=con.execute('SELECT album_id FROM editions WHERE id=?',(edition_id,)).fetchone()
                con.execute('DELETE FROM editions WHERE id=?',(edition_id,))
                if album and con.execute('SELECT COUNT(*) FROM editions WHERE album_id=?',(album['album_id'],)).fetchone()[0]==0:
                    con.execute('DELETE FROM canonical_albums WHERE id=?',(album['album_id'],))
    return True

def upsert_endpoint(item):
    caps=json.dumps(item.get('capabilities') or {})
    with connect() as con:
        con.execute('''INSERT INTO endpoints(id,name,kind,address,capabilities_json,last_seen)
        VALUES(?,?,?,?,?,CURRENT_TIMESTAMP)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,
        address=excluded.address,capabilities_json=excluded.capabilities_json,last_seen=CURRENT_TIMESTAMP''',
        (item['id'],item['name'],item['kind'],item.get('address'),caps))

def list_endpoints():
    with connect() as con:
        rows=[]
        for r in con.execute('SELECT * FROM endpoints ORDER BY name'):
            d=dict(r); d['capabilities']=json.loads(d.pop('capabilities_json') or '{}'); rows.append(d)
        return rows


def save_group(group_id, name, members):
    with connect() as con:
        con.execute('INSERT INTO playback_groups(id,name) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name',
                    (group_id, name))
        con.execute('DELETE FROM playback_group_members WHERE group_id=?', (group_id,))
        con.executemany('''INSERT INTO playback_group_members(group_id,endpoint_id,latency_ms,volume,enabled)
            VALUES(?,?,?,?,?)''', [(group_id, m['endpoint_id'], int(m.get('latency_ms', 0)),
                    m.get('volume'), 1 if m.get('enabled', True) else 0) for m in members])

def list_groups():
    with connect() as con:
        groups=[]
        for row in con.execute('SELECT * FROM playback_groups ORDER BY name'):
            item=dict(row)
            item['members']=[dict(m) for m in con.execute(
                'SELECT endpoint_id,latency_ms,volume,enabled FROM playback_group_members WHERE group_id=? ORDER BY endpoint_id',
                (item['id'],))]
            for m in item['members']:
                m['enabled']=bool(m['enabled'])
            groups.append(item)
        return groups

def get_group(group_id):
    return next((g for g in list_groups() if g['id'] == group_id), None)

def delete_group(group_id):
    with connect() as con:
        con.execute('DELETE FROM playback_group_members WHERE group_id=?', (group_id,))
        cur=con.execute('DELETE FROM playback_groups WHERE id=?', (group_id,))
        return cur.rowcount > 0

def update_group_latency(group_id, endpoint_id, latency_ms):
    with connect() as con:
        cur=con.execute('UPDATE playback_group_members SET latency_ms=? WHERE group_id=? AND endpoint_id=?',
                        (int(latency_ms), group_id, endpoint_id))
        return cur.rowcount > 0


def save_source(item):
    config=json.dumps(item.get('config') or {})
    with connect() as con:
        con.execute("""INSERT INTO library_sources(id,name,kind,path,cache_policy,config_json,enabled)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET name=excluded.name,kind=excluded.kind,path=excluded.path,
        cache_policy=excluded.cache_policy,config_json=excluded.config_json,enabled=excluded.enabled""",
        (item['id'],item['name'],item['kind'],item.get('path'),item.get('cache_policy','off'),config,1 if item.get('enabled',True) else 0))

def list_sources():
    with connect() as con:
        out=[]
        for row in con.execute('SELECT * FROM library_sources ORDER BY name'):
            d=dict(row); d['config']=json.loads(d.pop('config_json') or '{}'); d['enabled']=bool(d['enabled']); out.append(d)
        return out

def get_source(source_id):
    return next((s for s in list_sources() if s['id']==source_id), None)

def prune_source_media(source_id, present_paths):
    keep=set(present_paths)
    with connect() as con:
        rows=con.execute('SELECT id,path FROM media WHERE source_id=?',(source_id,)).fetchall()
        stale=[r['id'] for r in rows if r['path'] not in keep]
        con.executemany('DELETE FROM media WHERE id=?',[(i,) for i in stale])
        return len(stale)

def delete_source(source_id):
    with connect() as con:
        con.execute('DELETE FROM media WHERE source_id=?',(source_id,))
        return con.execute('DELETE FROM library_sources WHERE id=?',(source_id,)).rowcount>0
