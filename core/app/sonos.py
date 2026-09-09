import html
import socket
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from .models import Endpoint

SSDP_ADDR = ("239.255.255.250", 1900)
SONOS_ST = "urn:schemas-upnp-org:device:ZonePlayer:1"
TOPOLOGY_SOAP = '''<?xml version="1.0" encoding="utf-8" ?>
<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>
<u:GetZoneGroupState xmlns:u="urn:schemas-upnp-org:service:ZoneGroupTopology:1"/>
</s:Body></s:Envelope>'''


def _headers(text):
    out = {}
    for line in text.splitlines()[1:]:
        if ':' in line:
            k, v = line.split(':', 1)
            out[k.strip().lower()] = v.strip()
    return out


def discover(timeout=2.5):
    msg = "\r\n".join(["M-SEARCH * HTTP/1.1", "HOST: 239.255.255.250:1900",
        'MAN: "ssdp:discover"', "MX: 2", f"ST: {SONOS_ST}", "", ""]).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(timeout)
    sock.sendto(msg, SSDP_ADDR)
    found = {}
    while True:
        try:
            data, addr = sock.recvfrom(65535)
        except socket.timeout:
            break
        h = _headers(data.decode("utf-8", "replace"))
        if "location" in h:
            found[addr[0]] = h["location"]
    sock.close()
    return found


def _topology(ip):
    req = urllib.request.Request(
        f"http://{ip}:1400/ZoneGroupTopology/Control",
        data=TOPOLOGY_SOAP.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8",
                 "SOAPACTION": '"urn:schemas-upnp-org:service:ZoneGroupTopology:1#GetZoneGroupState"'})
    with urllib.request.urlopen(req, timeout=3) as response:
        raw = response.read().decode()
    outer = ET.fromstring(raw)
    text = next((e.text for e in outer.iter() if e.tag.endswith("ZoneGroupState")), None)
    return ET.fromstring(html.unescape(text)) if text else None


def _ip(location):
    return urllib.parse.urlparse(location or '').hostname


def parse_topology(root):
    result = []
    for group in root.findall(".//ZoneGroup") if root is not None else []:
        members = group.findall("ZoneGroupMember")
        visible = [m for m in members if m.get("Invisible") != "1"]
        coordinator_uuid = group.get('Coordinator')
        coordinator = next((m for m in members if m.get('UUID') == coordinator_uuid), visible[0] if visible else None)
        coordinator_ip = _ip(coordinator.get('Location')) if coordinator is not None else None
        member_ips = [_ip(m.get('Location')) for m in members if _ip(m.get('Location'))]
        for member in visible:
            cmap = member.get("ChannelMapSet", "")
            roles = [part.rsplit(":", 1)[-1].split(",")[0] for part in cmap.split(";") if ":" in part]
            result.append(Endpoint(
                id=group.get("ID", "sonos"),
                name=member.get("ZoneName", "Sonos"),
                kind="sonos",
                channels=max(2, len(set(roles))) if roles else 2,
                channel_map=roles or ["LF", "RF"],
                address=member.get("Location"),
                capabilities={
                    "channel_map_set": cmap,
                    "coordinator_ip": coordinator_ip,
                    "member_ips": member_ips,
                    "group_id": group.get('ID'),
                }
            ).dict())
    return result


def endpoints():
    devices = discover()
    return parse_topology(_topology(next(iter(devices)))) if devices else []


def _soap(ip, control_path, service, action, fields):
    payload = ''.join(f'<{k}>{html.escape(str(v))}</{k}>' for k, v in fields.items())
    envelope = (f'<?xml version="1.0" encoding="utf-8"?>'
                f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                f's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
                f'<s:Body><u:{action} xmlns:u="{service}">{payload}</u:{action}></s:Body></s:Envelope>')
    req = urllib.request.Request(
        f'http://{ip}:1400{control_path}', data=envelope.encode(),
        headers={'Content-Type': 'text/xml; charset="utf-8"',
                 'SOAPACTION': f'"{service}#{action}"'})
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.read().decode('utf-8', 'replace')


def set_volume(zone, volume):
    volume = max(0, min(int(volume), 49))
    ip = zone.get('capabilities', {}).get('coordinator_ip') or _ip(zone.get('address'))
    if not ip:
        raise RuntimeError('Sonos coordinator IP unavailable')
    service = 'urn:schemas-upnp-org:service:GroupRenderingControl:1'
    try:
        _soap(ip, '/MediaRenderer/GroupRenderingControl/Control', service, 'SetGroupVolume',
              {'InstanceID': 0, 'DesiredVolume': volume})
    except Exception:
        service = 'urn:schemas-upnp-org:service:RenderingControl:1'
        for member_ip in zone.get('capabilities', {}).get('member_ips') or [ip]:
            _soap(member_ip, '/MediaRenderer/RenderingControl/Control', service, 'SetVolume',
                  {'InstanceID': 0, 'Channel': 'Master', 'DesiredVolume': volume})
    return volume


def prepare_uri(zone, uri):
    ip = zone.get('capabilities', {}).get('coordinator_ip') or _ip(zone.get('address'))
    if not ip:
        raise RuntimeError('Sonos coordinator IP unavailable')
    service = 'urn:schemas-upnp-org:service:AVTransport:1'
    _soap(ip, '/MediaRenderer/AVTransport/Control', service, 'SetAVTransportURI',
          {'InstanceID': 0, 'CurrentURI': uri, 'CurrentURIMetaData': ''})
    return True


def play(zone):
    ip = zone.get('capabilities', {}).get('coordinator_ip') or _ip(zone.get('address'))
    if not ip:
        raise RuntimeError('Sonos coordinator IP unavailable')
    service = 'urn:schemas-upnp-org:service:AVTransport:1'
    _soap(ip, '/MediaRenderer/AVTransport/Control', service, 'Play', {'InstanceID': 0, 'Speed': 1})
    return True


def seek(zone, seconds):
    ip = zone.get('capabilities', {}).get('coordinator_ip') or _ip(zone.get('address'))
    if not ip:
        raise RuntimeError('Sonos coordinator IP unavailable')
    seconds = max(0, int(round(float(seconds))))
    target = f'{seconds // 3600}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}'
    service = 'urn:schemas-upnp-org:service:AVTransport:1'
    _soap(ip, '/MediaRenderer/AVTransport/Control', service, 'Seek',
          {'InstanceID': 0, 'Unit': 'REL_TIME', 'Target': target})
    return target


def play_uri(zone, uri):
    prepare_uri(zone, uri)
    return play(zone)


def stop(zone):
    ip = zone.get('capabilities', {}).get('coordinator_ip') or _ip(zone.get('address'))
    if not ip:
        return False
    service = 'urn:schemas-upnp-org:service:AVTransport:1'
    _soap(ip, '/MediaRenderer/AVTransport/Control', service, 'Stop', {'InstanceID': 0})
    return True
