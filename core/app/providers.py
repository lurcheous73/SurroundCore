from .streaming import airia_available

PROVIDERS = [
    {
        'id': 'internet_radio', 'name': 'Internet Radio',
        'kind': 'native', 'auth': 'none', 'implemented': True,
        'quality': 'source-native', 'formats': ['http', 'https', 'HLS', 'ICY'],
    },
    {
        'id': 'podcasts', 'name': 'Podcasts / RSS',
        'kind': 'native', 'auth': 'optional', 'implemented': False,
        'quality': 'source-native', 'formats': ['RSS', 'HTTP audio'],
    },
    {
        'id': 'bandcamp', 'name': 'Bandcamp',
        'kind': 'subsonic', 'auth': 'account-token', 'implemented': False,
        'quality': 'highest-native', 'formats': ['lossless where purchased/available'],
    },
    {
        'id': 'hdtracks', 'name': 'HDtracks',
        'kind': 'partner', 'auth': 'provider-login', 'implemented': False,
        'quality': 'highest-native', 'formats': ['PCM/FLAC', 'MQA', 'AIRIA'],
    },
    {
        'id': 'spotify', 'name': 'Spotify',
        'kind': 'native-connect', 'auth': 'native-app-pairing', 'implemented': False,
        'quality': 'provider-highest', 'formats': ['provider-native'],
    },
    {
        'id': 'tidal', 'name': 'TIDAL',
        'kind': 'partner-sdk', 'auth': 'oauth/device', 'implemented': False,
        'quality': 'highest-native', 'formats': ['Hi-Res FLAC', 'Dolby Atmos'],
    },
    {
        'id': 'qobuz', 'name': 'Qobuz',
        'kind': 'partner-sdk', 'auth': 'oauth/device', 'implemented': False,
        'quality': 'highest-native', 'formats': ['Hi-Res FLAC'],
    },
    {
        'id': 'apple_music', 'name': 'Apple Music',
        'kind': 'native-handoff', 'auth': 'authorised-device', 'implemented': False,
        'quality': 'provider-highest', 'formats': ['Apple Music native'],
    },
    {
        'id': 'audible', 'name': 'Audible',
        'kind': 'native-handoff', 'auth': 'authorised-device', 'implemented': False,
        'quality': 'provider-native', 'formats': ['Audible native'],
    },
]


def provider_status(settings):
    states = settings.get('providers') or {}
    result = []
    for provider in PROVIDERS:
        item = dict(provider)
        item['enabled'] = bool(states.get(provider['id'], {}).get('enabled', provider['id'] == 'internet_radio'))
        item['connected'] = bool(states.get(provider['id'], {}).get('connected', False))
        if provider['id'] == 'hdtracks':
            item['airia_available'] = airia_available()
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
