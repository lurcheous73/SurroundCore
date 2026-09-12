import re
import time
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from .models import Endpoint

SERVICE_KINDS = {
    '_googlecast._tcp.local.': 'cast',
    '_raop._tcp.local.': 'airplay',
    '_airplay._tcp.local.': 'airplay',
    '_oaat._tcp.local.': 'oaat',
}


def _normalise_airplay_id(name, props):
    deviceid = str(props.get('deviceid') or '').strip()
    if deviceid:
        raw = re.sub(r'[^0-9A-Fa-f]', '', deviceid)
    else:
        instance = name.split('._', 1)[0].split('@', 1)[0]
        raw = re.sub(r'[^0-9A-Fa-f]', '', instance)
    if len(raw) == 12:
        return ':'.join(raw[i:i+2] for i in range(0, 12, 2)).upper()
    return deviceid or name


def _oaat_caps(props, port):
    raw_caps = str(props.get('caps') or '')
    pcm_rate = pcm_bits = None
    flac = False
    for part in (x.strip() for x in raw_caps.split(',') if x.strip()):
        if part.startswith('pcm:'):
            try:
                rate_khz, bits = part.split(':', 1)[1].split('/', 1)
                pcm_rate = int(float(rate_khz) * 1000)
                pcm_bits = int(bits)
            except (TypeError, ValueError):
                pass
        elif part == 'flac':
            flac = True
    return {
        'service_type': '_oaat._tcp.local.',
        'protocol': 'oaat',
        'protocol_version': props.get('v'),
        'endpoint_id': props.get('id'),
        'vendor': props.get('vendor'),
        'firmware': props.get('fw'),
        'volume_control': props.get('vol'),
        'control_port': port,
        'pcm_max_rate': pcm_rate,
        'pcm_max_bits': pcm_bits,
        'flac': flac,
        'txt': dict(props),
        'discovered_only': False,
    }


def _display_name(name, props):
    if props.get('fn'):
        return props['fn']
    instance = name.split('._', 1)[0]
    if '@' in instance:
        return instance.split('@', 1)[1]
    return instance


class _Listener(ServiceListener):
    def __init__(self, zc, service_type, kind):
        self.zc = zc
        self.service_type = service_type
        self.kind = kind
        self.items = {}

    def add_service(self, zc, service_type, name):
        self._store(name)

    def update_service(self, zc, service_type, name):
        self._store(name)

    def remove_service(self, zc, service_type, name):
        self.items.pop(name, None)

    def _store(self, name):
        info = self.zc.get_service_info(self.service_type, name, timeout=1200)
        if not info:
            return
        addresses = info.parsed_addresses()
        props = {}
        for key, value in (info.properties or {}).items():
            k = key.decode('utf-8', 'replace') if isinstance(key, bytes) else str(key)
            v = value.decode('utf-8', 'replace') if isinstance(value, bytes) else str(value)
            props[k] = v
        display = props.get('name') or _display_name(name, props)
        endpoint_id = _normalise_airplay_id(name, props) if self.kind == 'airplay' else (props.get('id') or name)
        capabilities = {
            'service_type': self.service_type,
            'port': info.port,
            'txt': props,
            'render_multichannel_to_stereo': True,
        }
        channels = 2
        channel_map = ['FL', 'FR']
        if self.kind == 'oaat':
            try:
                channels = max(1, min(int(props.get('ch') or 2), 32))
            except (TypeError, ValueError):
                channels = 2
            standard = ['FL','FR','FC','LFE','SL','SR','BL','BR']
            channel_map = standard[:channels] if channels <= len(standard) else standard + [f'CH{i+1}' for i in range(len(standard), channels)]
            capabilities = _oaat_caps(props, info.port)
            capabilities['address'] = addresses[0] if addresses else None
        elif self.kind == 'airplay':
            capabilities.update({
                'airplay2_native_realtime': True,
                'timing': 'ptp',
                'buffered': False,
                'render': {'codec': 'alac', 'sample_rate': 44100, 'bit_depth': 16, 'channels': 2},
            })
        self.items[name] = Endpoint(
            id=f'{self.kind}:{endpoint_id}',
            name=display,
            kind=self.kind,
            channels=channels,
            channel_map=channel_map,
            address=addresses[0] if addresses else None,
            capabilities=capabilities,
        ).dict()


def endpoints(timeout=1.5):
    zc = Zeroconf()
    listeners = []
    browsers = []
    try:
        for service_type, kind in SERVICE_KINDS.items():
            listener = _Listener(zc, service_type, kind)
            listeners.append(listener)
            browsers.append(ServiceBrowser(zc, service_type, listener))
        time.sleep(timeout)
        combined = {}
        for listener in listeners:
            for item in listener.items.values():
                combined[item['id']] = item
        return list(combined.values())
    finally:
        for browser in browsers:
            browser.cancel()
        zc.close()
