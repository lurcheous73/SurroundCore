from .streaming import load_settings, save_settings

KEY='output_profiles'


def all_profiles():
    return dict(load_settings().get(KEY) or {})


def get_profile(key):
    return all_profiles().get(str(key), {})


def save_profile(key, name=None, transport=None, settings=None):
    data=load_settings(); profiles=dict(data.get(KEY) or {})
    item=dict(profiles.get(str(key)) or {})
    if name is not None: item['name']=str(name).strip()[:120]
    if transport is not None: item['transport']=str(transport).strip().lower()
    if settings is not None:
        merged=dict(item.get('settings') or {}); merged.update(dict(settings or {})); item['settings']=merged
    profiles[str(key)]=item; data[KEY]=profiles
    if transport is not None:
        prefs=dict(data.get('output_transports') or {}); prefs[str(key)]=item['transport']; data['output_transports']=prefs
    save_settings(data)
    return item


def profile_enabled(key, default=True):
    item=get_profile(key)
    settings=dict(item.get('settings') or {})
    return bool(settings.get('enabled', default))
