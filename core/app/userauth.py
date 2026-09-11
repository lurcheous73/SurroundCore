import hashlib
import hmac
import secrets
import time
from .db import connect

PBKDF2_ROUNDS = 310_000
SESSION_SECONDS = 60 * 60 * 24 * 30
FACTORY_PASSWORD = ''.join(('pass','word'))


def init_auth_db():
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT NOT NULL UNIQUE,
          display_name TEXT NOT NULL,
          password_salt TEXT NOT NULL,
          password_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'user',
          enabled INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS user_zone_permissions (
          user_id INTEGER NOT NULL,
          endpoint_id TEXT NOT NULL,
          PRIMARY KEY(user_id, endpoint_id),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS user_sessions (
          token_hash TEXT PRIMARY KEY,
          user_id INTEGER NOT NULL,
          expires_at REAL NOT NULL,
          created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS user_zone_queues (
          user_id INTEGER NOT NULL,
          endpoint_id TEXT NOT NULL,
          queue_json TEXT NOT NULL DEFAULT '[]',
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(user_id, endpoint_id),
          FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS auth_meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        ''')
        existing=con.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        meta=con.execute("SELECT 1 FROM auth_meta WHERE key='core_key_revealed'").fetchone()
        if existing and not meta:
            con.execute("INSERT INTO auth_meta(key,value) VALUES('core_key_revealed','1')")


def _username(value):
    value = str(value or '').strip().lower()
    if not value or len(value) > 64 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789._-' for c in value):
        raise ValueError('Username may contain letters, numbers, dot, underscore and hyphen')
    return value


def _password(password, salt_hex=None):
    password = str(password or '')
    if len(password) < 8:
        raise ValueError('Password must be at least 8 characters')
    salt = bytes.fromhex(salt_hex) if salt_hex else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PBKDF2_ROUNDS)
    return salt.hex(), digest.hex()


def take_core_key_reveal():
    with connect() as con:
        row=con.execute("SELECT value FROM auth_meta WHERE key='core_key_revealed'").fetchone()
        if row and row['value']=='1': return False
        con.execute("INSERT INTO auth_meta(key,value) VALUES('core_key_revealed','1') ON CONFLICT(key) DO UPDATE SET value='1'")
        return True

def ensure_default_admin():
    if count_users(): return None
    return create_user('admin','Administrator',FACTORY_PASSWORD,'admin')

def invalidate_sessions():
    with connect() as con:
        con.execute('DELETE FROM user_sessions')

def recover_admin_default():
    user=find_user('admin')
    if user:
        update_user(user['id'],display_name='Administrator',password='password',role='admin',enabled=True)
    else:
        user=create_user('admin','Administrator','password','admin')
    invalidate_sessions()
    return authenticate('admin','password')

def count_users():
    with connect() as con:
        return int(con.execute('SELECT COUNT(*) FROM users').fetchone()[0])


def create_user(username, display_name, password, role='user'):
    username = _username(username)
    role = str(role or 'user').lower()
    if role not in ('admin', 'user'):
        raise ValueError('Role must be admin or user')
    salt, digest = _password(password)
    with connect() as con:
        cur = con.execute('INSERT INTO users(username,display_name,password_salt,password_hash,role) VALUES(?,?,?,?,?)',
                          (username, str(display_name or username)[:120], salt, digest, role))
        user_id = cur.lastrowid
    return get_user(user_id)


def get_user(user_id):
    with connect() as con:
        row = con.execute('SELECT id,username,display_name,role,enabled,created_at FROM users WHERE id=?',(int(user_id),)).fetchone()
        if not row:
            return None
        d = dict(row); d['enabled'] = bool(d['enabled']); d['zones'] = user_zones(d['id']); return d


def list_users():
    with connect() as con:
        ids = [r['id'] for r in con.execute('SELECT id FROM users ORDER BY role DESC, display_name COLLATE NOCASE')]
    return [get_user(i) for i in ids]


def find_user(username):
    username=_username(username)
    with connect() as con:
        row=con.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
    return get_user(row['id']) if row else None



def update_user(user_id, display_name=None, password=None, role=None, enabled=None):
    user=get_user(user_id)
    if not user:
        return None
    fields=[]; values=[]
    if display_name is not None:
        fields.append('display_name=?'); values.append(str(display_name or user['username'])[:120])
    if role is not None:
        role=str(role).lower()
        if role not in ('admin','user'): raise ValueError('Role must be admin or user')
        fields.append('role=?'); values.append(role)
    if enabled is not None:
        fields.append('enabled=?'); values.append(1 if enabled else 0)
    if password:
        salt,digest=_password(password)
        fields += ['password_salt=?','password_hash=?']; values += [salt,digest]
    if fields:
        with connect() as con:
            con.execute('UPDATE users SET '+','.join(fields)+' WHERE id=?', values+[int(user_id)])
    return get_user(user_id)


def delete_user(user_id):
    user_id=int(user_id)
    with connect() as con:
        exists=con.execute('SELECT id FROM users WHERE id=?',(user_id,)).fetchone()
        if not exists: return False
        con.execute('DELETE FROM user_sessions WHERE user_id=?',(user_id,))
        con.execute('DELETE FROM user_zone_permissions WHERE user_id=?',(user_id,))
        con.execute('DELETE FROM user_zone_queues WHERE user_id=?',(user_id,))
        con.execute('UPDATE playback_history SET user_id=NULL WHERE user_id=?',(user_id,))
        con.execute('UPDATE playlists SET owner_user_id=NULL WHERE owner_user_id=?',(user_id,))
        con.execute('DELETE FROM users WHERE id=?',(user_id,))
    return True

def admin_count():
    with connect() as con:
        return int(con.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0])

def set_user_zones(user_id, endpoint_ids):
    with connect() as con:
        con.execute('DELETE FROM user_zone_permissions WHERE user_id=?',(int(user_id),))
        con.executemany('INSERT INTO user_zone_permissions(user_id,endpoint_id) VALUES(?,?)',
                        [(int(user_id), str(x)) for x in dict.fromkeys(endpoint_ids or [])])
    return user_zones(user_id)


def user_zones(user_id):
    with connect() as con:
        return [r['endpoint_id'] for r in con.execute('SELECT endpoint_id FROM user_zone_permissions WHERE user_id=? ORDER BY endpoint_id',(int(user_id),))]


def zone_allowed(user, endpoint_id):
    return bool(user and (user.get('role') == 'admin' or str(endpoint_id) in set(user.get('zones') or user_zones(user['id']))))


def authenticate(username, password):
    username = _username(username)
    with connect() as con:
        row = con.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
    if not row or not row['enabled']:
        return None
    _, digest = _password(password, row['password_salt'])
    if not hmac.compare_digest(digest, row['password_hash']):
        return None
    token = secrets.token_urlsafe(40)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = time.time() + SESSION_SECONDS
    with connect() as con:
        con.execute('DELETE FROM user_sessions WHERE expires_at < ?', (time.time(),))
        con.execute('INSERT INTO user_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',(token_hash,row['id'],expires))
    return token, get_user(row['id']), expires


def session_user(token):
    if not token:
        return None
    token_hash = hashlib.sha256(str(token).encode()).hexdigest()
    with connect() as con:
        row = con.execute('SELECT user_id,expires_at FROM user_sessions WHERE token_hash=?',(token_hash,)).fetchone()
        if not row or float(row['expires_at']) < time.time():
            if row: con.execute('DELETE FROM user_sessions WHERE token_hash=?',(token_hash,))
            return None
    user = get_user(row['user_id'])
    return user if user and user.get('enabled') else None


def logout(token):
    if not token:
        return False
    with connect() as con:
        return con.execute('DELETE FROM user_sessions WHERE token_hash=?',(hashlib.sha256(str(token).encode()).hexdigest(),)).rowcount > 0


def queue_get(user_id, endpoint_id):
    import json
    with connect() as con:
        row=con.execute("SELECT queue_json FROM user_zone_queues WHERE user_id=? AND endpoint_id=?",(int(user_id),str(endpoint_id))).fetchone()
        return json.loads(row[0]) if row else []


def queue_save(user_id, endpoint_id, media_ids):
    import json
    clean=[int(x) for x in media_ids]
    with connect() as con:
        con.execute("""INSERT INTO user_zone_queues(user_id,endpoint_id,queue_json,updated_at)
        VALUES(?,?,?,CURRENT_TIMESTAMP) ON CONFLICT(user_id,endpoint_id) DO UPDATE SET
        queue_json=excluded.queue_json,updated_at=CURRENT_TIMESTAMP""",
        (int(user_id),str(endpoint_id),json.dumps(clean)))
    return clean


def queue_append(user_id, endpoint_id, media_id):
    q=queue_get(user_id,endpoint_id); q.append(int(media_id)); return queue_save(user_id,endpoint_id,q)


def queue_remove(user_id, endpoint_id, index):
    q=queue_get(user_id,endpoint_id); index=int(index)
    if index < 0 or index >= len(q): raise IndexError('Queue item out of range')
    q.pop(index); return queue_save(user_id,endpoint_id,q)
