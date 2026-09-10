import concurrent.futures
import ipaddress
import os
import socket
import threading
import time

PORT = 9030
CACHE_SECONDS = 60
_CACHE = {'at': 0.0, 'items': []}
_LOCK = threading.Lock()

MODELS = [
    'Meridian / Sooloos', 'MC200', 'MC600', 'MS200', 'MS600',
    'ID40', 'ID41', 'Control 10', 'Control 15', 'Other Meridian'
]


def _network():
    value = os.getenv('SURROUNDCORE_MERIDIAN_SCAN_NET', '').strip()
    if value:
        return ipaddress.ip_network(value, strict=False)
    bind = os.getenv('SURROUNDCORE_AIRPLAY_BIND', '10.26.30.20')
    return ipaddress.ip_network(f'{bind}/24', strict=False)


def _parse_banner(text):
    out = {}
    for line in text.splitlines():
        if line.startswith('*N'): out['name'] = line[2:].strip()
        elif line.startswith('*I'): out['uuid'] = line[2:].strip()
        elif line.startswith('*b'): out['device_id'] = line[2:].strip().lower()
        elif line.startswith('*V'): out['protocol_version'] = line[2:].strip()
        elif line.startswith('*D'):
            out['description'] = line[2:].strip()
            bits = out['description'].split(' Zone ', 1)
            if bits: out['serial'] = bits[0].replace('Serial #', '').strip().lower()
            if len(bits) == 2: out['zone'] = 'Zone ' + bits[1].strip()
    return out

def _probe(ip):
    try:
        with socket.create_connection((str(ip), PORT), timeout=0.18) as sock:
            sock.settimeout(0.45)
            chunks = []
            end = time.time() + 0.7
            while time.time() < end:
                try:
                    data = sock.recv(8192)
                    if not data: break
                    chunks.append(data)
                except socket.timeout:
                    break
        info = _parse_banner(b''.join(chunks).decode('latin1', 'replace'))
        if not info.get('name') and not info.get('device_id'):
            return None
        ident = info.get('device_id') or str(ip)
        return {
            'id': f'meridian:{str(ip)}',
            'name': info.get('name') or f'Meridian {ident}',
            'kind': 'meridian',
            'address': None,
            'capabilities': {
                'native_protocol': 'sooloos-streaming',
                'management_ip': str(ip),
                'management_port': PORT,
                'device_id': ident,
                'serial': info.get('serial') or ident,
                'zone': info.get('zone') or '',
                'sooloos_uuid': info.get('uuid') or '',
                'protocol_version': info.get('protocol_version') or '',
                'model': 'Meridian / Sooloos',
                'discovered_only': True,
                'channels': 2,
            },
        }
    except OSError:
        return None


def endpoints(force=False):
    with _LOCK:
        if not force and time.time() - _CACHE['at'] < CACHE_SECONDS:
            return [dict(x) for x in _CACHE['items']]
    hosts = list(_network().hosts())
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(64, len(hosts) or 1)) as pool:
        items = [x for x in pool.map(_probe, hosts) if x]
    with _LOCK:
        _CACHE['at'] = time.time(); _CACHE['items'] = items
    return [dict(x) for x in items]
