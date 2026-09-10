import html
import socket
import urllib.parse
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

def _device_description(location):
    with urllib.request.urlopen(location, timeout=3) as response:
        root = ET.fromstring(response.read())
    device = next((n for n in root.iter() if _local_name(n.tag) == 'device'), None)
    if device is None:
        raise RuntimeError('UPnP renderer has no device description')
    values = {}
    for child in device:
        if _local_name(child.tag) != 'serviceList':
            values[_local_name(child.tag)] = child.text or ''
    base = next((n.text for n in root.iter() if _local_name(n.tag) == 'URLBase' and n.text), None)
    if not base:
        u = urllib.parse.urlparse(location)
        base = f'{u.scheme}://{u.netloc}/'
    services = {}
    for svc in device.iter():
        if _local_name(svc.tag) != 'service':
            continue
        item = {_local_name(c.tag): (c.text or '') for c in svc}
        st = item.get('serviceType', '')
        name = st.rsplit(':', 2)[-2] if ':' in st else st
        if name:
            services[name] = {
                'type': st,
                'control': urllib.parse.urljoin(base, item.get('controlURL', '')),
            }
    return values, services

def endpoints():
    result = []
    seen = set()
    for location, ip in discover().items():
        try:
            desc, services = _device_description(location)
        except Exception:
            continue
        manufacturer = desc.get('manufacturer', '')
        model = desc.get('modelName', '')
        friendly = desc.get('friendlyName') or model or ip
        identity = (desc.get('UDN') or location).replace('uuid:', '')
        if identity in seen:
            continue
        seen.add(identity)
        if 'sonos' in manufacturer.lower():
            continue
        result.append(Endpoint(
            id=f'upnp:{identity}', name=friendly, kind='upnp',
            channels=2, channel_map=['LF', 'RF'], address=location,
            capabilities={
                'manufacturer': manufacturer, 'model': model,
                'render_multichannel_to_stereo': True,
                'services': services,
                'native_http_renderer': bool(services.get('AVTransport')),
            },
        ).dict())
    return result

def _service(zone, name):
    service = (zone.get('capabilities') or {}).get('services', {}).get(name)
    if service and service.get('control'):
        return service
    _desc, services = _device_description(zone.get('address'))
    service = services.get(name)
    if not service:
        raise RuntimeError(f'UPnP {name} service unavailable')
    return service
def _soap(zone, service_name, action, fields=None):
    service = _service(zone, service_name)
    payload = ''.join(f'<{k}>{html.escape(str(v))}</{k}>' for k, v in (fields or {}).items())
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        f'<s:Body><u:{action} xmlns:u="{service["type"]}">{payload}</u:{action}></s:Body></s:Envelope>'
    )
    req = urllib.request.Request(
        service['control'], data=body.encode(),
        headers={'Content-Type': 'text/xml; charset="utf-8"',
                 'SOAPACTION': f'"{service["type"]}#{action}"'},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.read().decode('utf-8', 'replace')

def _value(raw, name):
    try:
        root = ET.fromstring(raw)
        for node in root.iter():
            if _local_name(node.tag) == name:
                return node.text or ''
    except Exception:
        pass
    return ''
def play_uri(zone, uri):
    _soap(zone, 'AVTransport', 'SetAVTransportURI', {
        'InstanceID': 0, 'CurrentURI': uri, 'CurrentURIMetaData': ''})
    _soap(zone, 'AVTransport', 'Play', {'InstanceID': 0, 'Speed': 1})
    return True

def pause(zone):
    _soap(zone, 'AVTransport', 'Pause', {'InstanceID': 0})
    return True

def resume(zone):
    _soap(zone, 'AVTransport', 'Play', {'InstanceID': 0, 'Speed': 1})
    return True

def stop(zone):
    _soap(zone, 'AVTransport', 'Stop', {'InstanceID': 0})
    return True

def seek(zone, seconds):
    seconds = max(0, int(round(float(seconds))))
    target = f'{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}'
    _soap(zone, 'AVTransport', 'Seek', {
        'InstanceID': 0, 'Unit': 'REL_TIME', 'Target': target})
    return target

def get_volume(zone):
    raw = _soap(zone, 'RenderingControl', 'GetVolume', {
        'InstanceID': 0, 'Channel': 'Master'})
    return int(_value(raw, 'CurrentVolume') or 0)
def set_volume(zone, volume):
    volume = max(0, min(int(volume), 100))
    _soap(zone, 'RenderingControl', 'SetVolume', {
        'InstanceID': 0, 'Channel': 'Master', 'DesiredVolume': volume})
    return volume

def get_mute(zone):
    raw = _soap(zone, 'RenderingControl', 'GetMute', {
        'InstanceID': 0, 'Channel': 'Master'})
    return _value(raw, 'CurrentMute') in ('1', 'true', 'True')

def set_mute(zone, muted):
    _soap(zone, 'RenderingControl', 'SetMute', {
        'InstanceID': 0, 'Channel': 'Master', 'DesiredMute': 1 if muted else 0})
    return bool(muted)

def state(zone):
    tr = _soap(zone, 'AVTransport', 'GetTransportInfo', {'InstanceID': 0})
    pos = _soap(zone, 'AVTransport', 'GetPositionInfo', {'InstanceID': 0})
    metadata = _value(pos, 'TrackMetaData')
    title = artist = album = ''
    if metadata:
        try:
            md = ET.fromstring(html.unescape(metadata))
            for node in md.iter():
                key = _local_name(node.tag); text = (node.text or '').strip()
                if key == 'title' and not title: title = text
                elif key in ('creator', 'artist') and not artist: artist = text
                elif key == 'album' and not album: album = text
        except Exception:
            pass
    out = {
        'state': _value(tr, 'CurrentTransportState') or 'UNKNOWN',
        'title': title, 'artist': artist, 'album': album,
        'uri': _value(pos, 'TrackURI'),
        'rel_time': _value(pos, 'RelTime'),
        'duration': _value(pos, 'TrackDuration'),
    }
    try: out['volume'] = get_volume(zone)
    except Exception: pass
    try: out['muted'] = get_mute(zone)
    except Exception: pass
    return out
