import socket
import urllib.request
import xml.etree.ElementTree as ET
from .models import Endpoint

SSDP_ADDR = ('239.255.255.250', 1900)
SEARCH_TARGETS = (
    'urn:schemas-upnp-org:device:MediaRenderer:1',
    'urn:schemas-upnp-org:device:MediaRenderer:2',
    'urn:schemas-upnp-org:device:MediaRenderer:3',
)


def _headers(text):
    out = {}
    for line in text.splitlines()[1:]:
        if ':' in line:
            key, value = line.split(':', 1)
            out[key.strip().lower()] = value.strip()
    return out


def discover(timeout=1.5):
    found = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(timeout)
    for target in SEARCH_TARGETS:
        msg = '\r\n'.join([
            'M-SEARCH * HTTP/1.1', 'HOST: 239.255.255.250:1900',
            'MAN: "ssdp:discover"', 'MX: 1', f'ST: {target}', '', ''
        ]).encode()
        sock.sendto(msg, SSDP_ADDR)
    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            break
        headers = _headers(data.decode('utf-8', 'replace'))
        location = headers.get('location')
        if location:
            found[location] = addr[0]
    sock.close()
    return found


def _local_name(tag):
    return tag.split('}', 1)[-1]


def _description(location):
    with urllib.request.urlopen(location, timeout=3) as response:
        root = ET.fromstring(response.read())
    device = next((node for node in root.iter() if _local_name(node.tag) == 'device'), None)
    if device is None:
        return None
    values = {}
    for child in device:
        values[_local_name(child.tag)] = child.text or ''
    return values


def endpoints():
    result = []
    seen = set()
    for location, ip in discover().items():
        try:
            desc = _description(location) or {}
        except Exception:
            continue
        manufacturer = desc.get('manufacturer', '')
        model = desc.get('modelName', '')
        friendly = desc.get('friendlyName') or model or ip
        udn = desc.get('UDN') or location
        identity = udn.replace('uuid:', '')
        if identity in seen:
            continue
        seen.add(identity)
        # Sonos has a richer dedicated driver; avoid presenting the same zone twice.
        if 'sonos' in manufacturer.lower():
            continue
        result.append(Endpoint(
            id=f'upnp:{identity}',
            name=friendly,
            kind='upnp',
            channels=2,
            channel_map=['LF', 'RF'],
            address=location,
            capabilities={
                'manufacturer': manufacturer,
                'model': model,
                'render_multichannel_to_stereo': True,
                'services': ['AVTransport', 'RenderingControl', 'ConnectionManager'],
            },
        ).dict())
    return result
