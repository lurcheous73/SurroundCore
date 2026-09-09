import json
import os
import sqlite3

DB_PATH = os.path.join(os.getenv('SURROUNDCORE_DATA', '/data'), 'surroundcore.sqlite3')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL UNIQUE,
  codec TEXT,
  channels INTEGER,
  channel_layout TEXT,
  sample_rate INTEGER,
  bit_depth INTEGER,
  duration REAL,
  metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS endpoints (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  address TEXT,
  capabilities_json TEXT NOT NULL DEFAULT '{}',
  last_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
'''

def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect() as con:
        con.executescript(SCHEMA)

def upsert_media(item):
    values=(item['path'], item.get('codec'), item.get('channels'), item.get('channel_layout'), item.get('sample_rate'), item.get('bit_depth'), item.get('duration'))
    with connect() as con:
        con.execute('''INSERT INTO media(path,codec,channels,channel_layout,sample_rate,bit_depth,duration)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(path) DO UPDATE SET
          codec=excluded.codec, channels=excluded.channels,
          channel_layout=excluded.channel_layout, sample_rate=excluded.sample_rate,
          bit_depth=excluded.bit_depth, duration=excluded.duration''', values)

def list_media():
    with connect() as con:
        return [dict(r) for r in con.execute('SELECT * FROM media ORDER BY path')]

def get_media(media_id):
    with connect() as con:
        row=con.execute('SELECT * FROM media WHERE id=?',(media_id,)).fetchone()
        return dict(row) if row else None

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
