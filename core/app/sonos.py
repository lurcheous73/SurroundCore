import html
import socket
import urllib.request
import xml.etree.ElementTree as ET
from .models import Endpoint

SSDP_ADDR = ("239.255.255.250", 1900)
SONOS_ST = "urn:schemas-upnp-org:device:ZonePlayer:1"
SOAP = '''<?xml version="1.0" encoding="utf-8" ?>
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
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    s.settimeout(timeout)
    s.sendto(msg, SSDP_ADDR)
    found = {}
    while True:
        try:
            data, addr = s.recvfrom(65535)
        except socket.timeout:
            break
        h = _headers(data.decode("utf-8", "replace"))
        if "location" in h:
            found[addr[0]] = h["location"]
    return found

def _topology(ip):
    req = urllib.request.Request(
        f"http://{ip}:1400/ZoneGroupTopology/Control",
        data=SOAP.encode(),
        headers={"Content-Type": "text/xml; charset=utf-8",
                 "SOAPACTION": '"urn:schemas-upnp-org:service:ZoneGroupTopology:1#GetZoneGroupState"'})
    with urllib.request.urlopen(req, timeout=3) as r:
        raw = r.read().decode()
    outer = ET.fromstring(raw)
    text = next((e.text for e in outer.iter() if e.tag.endswith("ZoneGroupState")), None)
    return ET.fromstring(html.unescape(text)) if text else None

def parse_topology(root):
    result = []
    for group in root.findall(".//ZoneGroup") if root is not None else []:
        visible = [m for m in group.findall("ZoneGroupMember") if m.get("Invisible") != "1"]
        for m in visible:
            cmap = m.get("ChannelMapSet", "")
            roles = [part.rsplit(":", 1)[-1].split(",")[0] for part in cmap.split(";") if ":" in part]
            result.append(Endpoint(
                id=group.get("ID", "sonos"),
                name=m.get("ZoneName", "Sonos"),
                kind="sonos",
                channels=max(2, len(set(roles))) if roles else 2,
                channel_map=roles or ["LF", "RF"],
                address=m.get("Location"),
                capabilities={"channel_map_set": cmap}
            ).dict())
    return result

def endpoints():
    devices = discover()
    return parse_topology(_topology(next(iter(devices)))) if devices else []
