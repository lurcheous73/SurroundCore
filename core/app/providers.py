import os
import shutil

from .streaming import airia_available, provider_secret_configured

PROVIDERS = [
    {
        'id': 'internet_radio', 'name': 'Internet Radio',
        'kind': 'native', 'auth': 'none', 'implemented': True,
        'quality': 'source-native', 'formats': ['HTTP', 'HTTPS', 'HLS', 'ICY'],
    },
    {
        'id': 'podcasts', 'name': 'Podcasts / RSS',
        'kind': 'native', 'auth': 'optional', 'implemented': True,
        'quality': 'source-native', 'formats': ['RSS', 'Atom', 'HTTP audio'],
    },
    {
        'id': 'bandcamp', 'name': 'Bandcamp',
        'kind': 'subsonic', 'auth': 'fan-generated credentials', 'implemented': True,
        'quality': 'highest available in purchased collection',
        'formats': ['Bandcamp Subsonic beta'],
    },
    {
        'id': 'hdtracks', 'name': 'HDtracks',
        'kind': 'partner', 'auth': 'provider/partner login', 'implemented': False,
        'quality': 'highest-native', 'formats': ['PCM/FLAC', 'MQA', 'AIRIA'],
    },
    {
        'id': 'spotify', 'name': 'Spotify',
        'kind': 'soloist/connect', 'auth': 'Spotify Soloist API key + Connect pairing', 'implemented': False,
        'quality': 'provider-highest', 'formats': ['lossless up to 24/44.1 where account supports it'],
    },
    {
        'id': 'tidal', 'name': 'TIDAL',
        'kind': 'official-sdk', 'auth': 'OAuth 2.1', 'implemented': False,
        'quality': 'highest-native', 'formats': ['Hi-Res FLAC', 'Dolby Atmos where available'],
    },
    {
        'id': 'qobuz', 'name': 'Qobuz',
        'kind': 'partner/connect', 'auth': 'Qobuz partner integration', 'implemented': False,
        'quality': 'highest-native', 'formats': ['Hi-Res FLAC up to 24/192'],
    },
    {
        'id': 'apple_music', 'name': 'Apple Music',
        'kind': 'musickit/native', 'auth': 'MusicKit user authorization', 'implemented': False,
        'quality': 'provider-highest', 'formats': ['Apple Music native'],
    },
    {
        'id': 'audible', 'name': 'Audible',
        'kind': 'native-handoff', 'auth': 'authorised Audible app/device', 'implemented': False,
        'quality': 'provider-native', 'formats': ['Audible native'],
    },
]


def _soloist_path():
    configured = os.getenv('SURROUNDCORE_SPOTIFY_SOLOIST', '').strip()
    if configured:
        return configured if os.path.isfile(configured) and os.access(configured, os.X_OK) else None
    return shutil.which('soloist')


def provider_status(settings):
    states = settings.get('providers') or {}
    result = []
    for provider in PROVIDERS:
        item = dict(provider)
        saved = states.get(provider['id'], {})
        item['enabled'] = bool(saved.get('enabled', provider['id'] in ('internet_radio', 'podcasts')))
        item['connected'] = bool(saved.get('connected', False))
        if provider['id'] == 'bandcamp':
            item['connected'] = provider_secret_configured('bandcamp')
        elif provider['id'] == 'hdtracks':
            item['airia_available'] = airia_available()
            item['partner_credentials'] = bool(os.getenv('SURROUNDCORE_HDTRACKS_CLIENT_ID'))
        elif provider['id'] == 'spotify':
            item['soloist_available'] = bool(_soloist_path())
            item['soloist_path'] = _soloist_path()
        elif provider['id'] == 'tidal':
            item['developer_credentials'] = bool(os.getenv('SURROUNDCORE_TIDAL_CLIENT_ID'))
        elif provider['id'] == 'qobuz':
            item['partner_credentials'] = bool(os.getenv('SURROUNDCORE_QOBUZ_APP_ID'))
        elif provider['id'] == 'apple_music':
            item['developer_credentials'] = bool(os.getenv('SURROUNDCORE_APPLE_MUSIC_TEAM_ID'))
        result.append(item)
    return result


def quality_policy(settings):
    return {
        'source': 'highest_native',
        'prefer_lossless': bool(settings.get('prefer_lossless', True)),
        'prefer_airia': bool(settings.get('prefer_airia', True)),
        'airia_available': airia_available(),
        'mqa_passthrough': bool(settings.get('allow_mqa_passthrough', True)),
        'output_transport': settings.get('output_transport', 'auto'),
        'prefer_mhr': bool(settings.get('prefer_mhr', True)),
        'prefer_mmhr': bool(settings.get('prefer_mmhr', True)),
        'allow_downsample': bool(settings.get('allow_downsample', False)),
        'allow_downmix': bool(settings.get('allow_downmix', False)),
    }
