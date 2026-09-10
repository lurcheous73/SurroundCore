from .codec_caps import features as codec_features, sink_supports_encoded, sink_supports_dsd

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
    if endpoint.get('kind') == 'airplay':
        return {'transport': 'airplay2-native-realtime', 'reason': 'AirPlay 2 realtime/PTP endpoint'}
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


def _source(media):
    meta=media.get('metadata') or {}
    return {
        'codec': media.get('codec') or '',
        'codec_profile': meta.get('codec_profile') or '',
        'immersive_audio': meta.get('immersive_audio') or '',
        'channels': int(media.get('channels') or 0),
        'channel_layout': media.get('channel_layout') or '',
        'sample_rate': int(media.get('sample_rate') or 0),
        'bit_depth': int(media.get('bit_depth') or 0),
    }


def pcm_playback_plan(media, endpoint, device='default', settings=None):
    settings = settings or {}
    caps = endpoint.get('capabilities') or {}
    source = _source(media)

    if endpoint.get('kind') == 'airplay':
        render = dict(caps.get('render') or {
            'codec': 'alac', 'sample_rate': 44100, 'bit_depth': 16, 'channels': 2,
        })
        conversion = (
            source['sample_rate'] != int(render.get('sample_rate') or 0)
            or source['bit_depth'] != int(render.get('bit_depth') or 0)
            or source['channels'] != int(render.get('channels') or 0)
        )
        return {
            'mode': 'airplay-native-realtime', 'supported': True,
            'reason': 'native AirPlay 2 realtime/PTP with explicit endpoint compatibility render',
            'source': source, 'render': render, 'conversion_required': conversion,
            'preserve_source': True, 'timing': 'ptp', 'buffered': False,
        }

    if endpoint.get('kind') != 'alsa' or not caps.get('direct_pcm'):
        return {'mode': 'endpoint-native', 'supported': True,
                'reason': 'non-ALSA endpoint uses its native transport', 'source': source}

    dev = _endpoint_device_caps(endpoint, device)
    cfeat=codec_features(media)
    if cfeat.get('dsd'):
        if dev and dev.get('native_dsd'):
            return {'mode':'native-dsd','supported':True,'reason':'USB DAC advertises native ALSA DSD format',
                    'source':source,'device_capabilities':dev,'conversion_required':False,'runtime_validation':True}
        if dev and dev.get('dop_candidate'):
            return {'mode':'dop','supported':True,'reason':'USB DAC exposes PCM container suitable for DoP',
                    'source':source,'device_capabilities':dev,'conversion_required':False,'runtime_validation':True}
        if not settings.get('allow_downsample'):
            return {'mode':None,'supported':False,'reason':'DSD source requires native DSD/DoP capable output',
                    'source':source,'device_capabilities':dev}
    if dev and sink_supports_encoded(media,dev):
        return {'mode':'encoded-passthrough','supported':True,'reason':'HDMI/IEC61937 sink advertises source codec',
                'source':source,'codec_features':cfeat,'device_capabilities':dev,
                'conversion_required':False,'passthrough':True,'runtime_validation':True}
    failures = []
    unknown = []
    if dev:
        max_channels = dev.get('max_channels')
        rates = [int(x) for x in (dev.get('sample_rates') or [])]
        depths = [int(x) for x in (dev.get('bit_depths') or [])]
        if max_channels:
            if source['channels'] > int(max_channels):
                failures.append(f"{source['channels']}ch exceeds endpoint {max_channels}ch")
        else:
            unknown.append('channels')
        if rates:
            if source['sample_rate'] not in rates:
                failures.append(f"{source['sample_rate']}Hz not advertised by endpoint")
        else:
            unknown.append('sample_rate')
        if source['bit_depth'] and depths:
            if max(depths) < source['bit_depth']:
                failures.append(f"{source['bit_depth']}-bit exceeds endpoint {max(depths)}-bit")
        elif source['bit_depth']:
            unknown.append('bit_depth')
    else:
        unknown += ['device', 'channels', 'sample_rate', 'bit_depth']

    if failures:
        allow_compat = bool(settings.get('allow_downsample') or settings.get('allow_downmix'))
        if allow_compat:
            return {'mode': 'compatibility', 'supported': True, 'reason': '; '.join(failures),
                    'source': source, 'device_capabilities': dev, 'conversion_required': True}
        return {'mode': None, 'supported': False, 'reason': '; '.join(failures),
                'source': source, 'device_capabilities': dev, 'conversion_required': True}

    return {'mode': 'direct', 'supported': True,
            'reason': 'source-native PCM; raw endpoint validation' if unknown else 'endpoint advertises source-native PCM',
            'source': source, 'device_capabilities': dev, 'runtime_validation': bool(unknown),
            'conversion_required': False, 'mqa_passthrough': bool(cfeat.get('mqa') and settings.get('allow_mqa_passthrough',True))}
