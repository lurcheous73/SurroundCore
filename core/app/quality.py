def source_request(settings, requested_channels=None):
    return {
        'quality': 'highest_native',
        'lossless_preferred': bool(settings.get('prefer_lossless', True)),
        'airia_preferred': bool(settings.get('prefer_airia', True)),
        'mqa_passthrough': bool(settings.get('allow_mqa_passthrough', True)),
        'requested_channels': requested_channels,
        'allow_downsample': bool(settings.get('allow_downsample', False)),
        'allow_downmix': bool(settings.get('allow_downmix', False)),
    }


def output_route(endpoint, channels, settings):
    caps = endpoint.get('capabilities') or {}
    transports = {str(x).lower() for x in caps.get('transports', [])}
    channels = int(channels or 2)

    if channels > 2 and settings.get('prefer_mmhr', True) and 'mmhr' in transports:
        return {'transport': 'mmhr', 'reason': 'multichannel Meridian endpoint capability'}
    if settings.get('prefer_mhr', True) and 'mhr' in transports:
        return {'transport': 'mhr', 'reason': 'Meridian endpoint capability'}
    if 'pcm' in transports or endpoint.get('kind') == 'alsa':
        return {'transport': 'pcm', 'reason': 'native PCM endpoint'}
    return {'transport': 'provider-native', 'reason': 'endpoint-specific transport'}
