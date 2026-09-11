from .streaming import load_settings, save_settings, load_provider_secrets, save_provider_secret

DEFAULTS = {
    'musicbrainz': {'name': 'MusicBrainz', 'enabled': True, 'secret_label': None},
    'coverartarchive': {'name': 'Cover Art Archive', 'enabled': True, 'secret_label': None},
    'theaudiodb': {'name': 'TheAudioDB', 'enabled': True, 'secret_label': 'API key'},
    'bandcamp_artwork': {'name': 'Bandcamp artwork', 'enabled': True, 'secret_label': None},
    'discogs': {'name': 'Discogs', 'enabled': False, 'secret_label': 'Personal access token'},
}


def _stored():
    raw = load_settings().get('metadata_providers') or {}
    return raw if isinstance(raw, dict) else {}


def _secret_key(provider_id):
    return 'metadata:' + str(provider_id)


def enabled(provider_id):
    pid = str(provider_id or '').lower()
    default = DEFAULTS.get(pid, {}).get('enabled', False)
    return bool((_stored().get(pid) or {}).get('enabled', default))


def secret(provider_id):
    value = load_provider_secrets().get(_secret_key(provider_id))
    return value if isinstance(value, str) else ''


def public_settings():
    stored = _stored()
    secrets = load_provider_secrets()
    items = []
    for pid, spec in DEFAULTS.items():
        entry = stored.get(pid) or {}
        items.append({
            'id': pid,
            'name': spec['name'],
            'enabled': bool(entry.get('enabled', spec['enabled'])),
            'secret_label': spec['secret_label'],
            'secret_configured': _secret_key(pid) in secrets,
        })
    return {'providers': items}


def configure(provider_id, enabled_value=None, secret_value=None, clear_secret=False):
    pid = str(provider_id or '').lower()
    if pid not in DEFAULTS:
        raise ValueError('unknown metadata provider')
    current = _stored()
    item = dict(current.get(pid) or {})
    if enabled_value is not None:
        item['enabled'] = bool(enabled_value)
    current[pid] = item
    save_settings({'metadata_providers': current})
    if clear_secret:
        save_provider_secret(_secret_key(pid), None)
    elif secret_value is not None and str(secret_value).strip():
        save_provider_secret(_secret_key(pid), str(secret_value).strip())
    return next(x for x in public_settings()['providers'] if x['id'] == pid)
