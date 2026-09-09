import socket
import time
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from .models import Endpoint

SERVICE_KINDS = {
    '_googlecast._tcp.local.': 'cast',
    '_raop._tcp.local.': 'airplay',
    '_airplay._tcp.local.': 'airplay',
}


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
        display = props.get('fn') or props.get('md') or name.split('._', 1)[0]
        endpoint_id = props.get('id') or props.get('deviceid') or name
        channels = 2
        self.items[name] = Endpoint(
            id=f'{self.kind}:{endpoint_id}',
            name=display,
            kind=self.kind,
            channels=channels,
            channel_map=['LF', 'RF'],
            address=addresses[0] if addresses else None,
            capabilities={
                'service_type': self.service_type,
                'port': info.port,
                'txt': props,
                'render_multichannel_to_stereo': True,
            },
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
