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


def _endpoint_device_caps(endpoint, device='default'):
    caps = endpoint.get('capabilities') or {}
    devices = caps.get('devices') or []
    if not devices:
        return None
    if not device or device == 'default':
        return devices[0]
    for item in devices:
        if device in (item.get('alsa'), item.get('raw_alsa')):
            return item
    return None


def pcm_playback_plan(media, endpoint, device='default', settings=None):
    settings = settings or {}
    caps = endpoint.get('capabilities') or {}
    if endpoint.get('kind') != 'alsa' or not caps.get('direct_pcm'):
        return {'mode': 'endpoint-native', 'supported': True, 'reason': 'non-ALSA endpoint uses its native transport'}

    source = {
        'codec': media.get('codec') or '',
        'channels': int(media.get('channels') or 0),
        'channel_layout': media.get('channel_layout') or '',
        'sample_rate': int(media.get('sample_rate') or 0),
        'bit_depth': int(media.get('bit_depth') or 0),
    }
    if source['codec'].lower().startswith('dsd'):
        return {'mode': None, 'supported': False, 'reason': 'native DSD/DoP output not implemented', 'source': source}

    dev = _endpoint_device_caps(endpoint, device)
    failures = []
    unknown = []
    if dev:
        max_channels = dev.get('max_channels')
        rates = [int(x) for x in (dev.get('sample_rates') or [])]
        depths = [int(x) for x in (dev.get('bit_depths') or [])]
        if max_channels:
            if source['channels'] > int(max_channels): failures.append(f"{source['channels']}ch exceeds endpoint {max_channels}ch")
        else: unknown.append('channels')
        if rates:
            if source['sample_rate'] not in rates: failures.append(f"{source['sample_rate']}Hz not advertised by endpoint")
        else: unknown.append('sample_rate')
        if source['bit_depth'] and depths:
            if max(depths) < source['bit_depth']: failures.append(f"{source['bit_depth']}-bit exceeds endpoint {max(depths)}-bit")
        elif source['bit_depth']:
            unknown.append('bit_depth')
    else:
        unknown += ['device', 'channels', 'sample_rate', 'bit_depth']

    if failures:
        allow_compat = bool(settings.get('allow_downsample') or settings.get('allow_downmix'))
        if allow_compat:
            return {'mode': 'compatibility', 'supported': True, 'reason': '; '.join(failures), 'source': source,
                    'device_capabilities': dev, 'conversion_required': True}
        return {'mode': None, 'supported': False, 'reason': '; '.join(failures), 'source': source,
                'device_capabilities': dev, 'conversion_required': True}

    return {'mode': 'direct', 'supported': True,
            'reason': 'source-native PCM; raw endpoint validation' if unknown else 'endpoint advertises source-native PCM',
            'source': source, 'device_capabilities': dev, 'runtime_validation': bool(unknown),
            'conversion_required': False}
