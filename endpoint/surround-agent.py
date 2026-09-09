#!/usr/bin/env python3
import argparse
import hmac
import json
import os
import signal
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PLAYER = None
PLAYER_STATE = {}
PREPARED = {}
TOKEN = os.getenv('SURROUNDCORE_TOKEN', '')
CORE_URL = os.getenv('SURROUNDCORE_CORE_URL', 'http://127.0.0.1:8080').rstrip('/')
DEFAULT_DEVICE = os.getenv('SURROUNDCORE_DEFAULT_DEVICE', 'default')
MAX_VOLUME = 0.49


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout


def machine_id():
    try:
        return open('/etc/machine-id').read().strip()
    except OSError:
        return os.uname().nodename


def local_ip():
    ips = run('hostname', '-I').split()
    return ips[0] if ips else '127.0.0.1'


def classify_output(description):
    text = description.upper()
    if 'HDMI' in text or 'DISPLAYPORT' in text:
        return 'hdmi'
    if 'S/PDIF' in text or 'SPDIF' in text or 'IEC958' in text or 'DIGITAL' in text:
        return 'spdif'
    if 'USB' in text:
        return 'usb-audio'
    if 'AES' in text or 'EBU' in text:
        return 'aes3'
    if 'I2S' in text or 'I2S' in text.replace('-', ''):
        return 'i2s'
    return 'analog'


def capabilities():
    cards = run('aplay', '-l')
    devices = []
    for line in cards.splitlines():
        if not line.startswith('card '):
            continue
        try:
            left, rest = line.split(':', 1)
            card = int(left.split()[1])
            dev_part = rest.split('device ', 1)[1]
            device = int(dev_part.split(':', 1)[0])
        except (ValueError, IndexError):
            continue
        output_type = classify_output(line)
        devices.append({
            'alsa': f'plughw:{card},{device}',
            'raw_alsa': f'hw:{card},{device}',
            'description': line.strip(),
            'output_type': output_type,
            'digital': output_type in ('hdmi', 'spdif', 'aes3'),
            'multichannel_candidate': output_type in ('hdmi', 'usb-audio', 'aes3', 'i2s'),
        })
    return {
        'kind': 'alsa',
        'hostname': os.uname().nodename,
        'devices': devices,
        'default_device': DEFAULT_DEVICE,
        'supported_output_types': ['analog', 'usb-audio', 'hdmi', 'spdif', 'aes3', 'i2s'],
        'channel_mapping': 'source-layout-preserved',
        'max_software_volume': MAX_VOLUME,
        'control_api': 'surround-agent-v2',
        'scheduled_group_playback': True,
    }


def registration():
    return {
        'id': f'alsa:{machine_id()}',
        'name': os.getenv('SURROUNDCORE_ENDPOINT_NAME', os.uname().nodename),
        'kind': 'alsa',
        'address': os.getenv('SURROUNDCORE_AGENT_ADVERTISE', f'http://{local_ip()}:8090'),
        'capabilities': capabilities(),
    }


def register_once():
    if not TOKEN:
        return False
    body = json.dumps(registration()).encode()
    req = urllib.request.Request(
        CORE_URL + '/api/v1/endpoints/register', data=body,
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {TOKEN}'})
    try:
        with urllib.request.urlopen(req, timeout=4) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def heartbeat():
    while True:
        register_once()
        time.sleep(30)


def stop_player():
    global PLAYER, PLAYER_STATE
    if PLAYER and PLAYER.poll() is None:
        PLAYER.send_signal(signal.SIGTERM)
        try:
            PLAYER.wait(timeout=2)
        except subprocess.TimeoutExpired:
            PLAYER.kill()
    PLAYER = None
    PLAYER_STATE = {}
PREPARED = {}


def play(url, device=None, volume=0.35, position_seconds=0.0):
    global PLAYER, PLAYER_STATE
    stop_player()
    device = device or DEFAULT_DEVICE
    volume = max(0.0, min(float(volume), MAX_VOLUME))
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin']
    if float(position_seconds or 0) > 0:
        cmd += ['-ss', f'{float(position_seconds):.3f}']
    cmd += [
        '-i', url, '-map', '0:a:0', '-vn', '-af', f'volume={volume:.4f}',
        '-f', 'alsa', device,
    ]
    PLAYER = subprocess.Popen(cmd)
    PLAYER_STATE = {'pid': PLAYER.pid, 'device': device, 'volume': volume, 'url': url, 'position_seconds': float(position_seconds or 0)}
    return {'ok': True, **PLAYER_STATE}


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorised(self):
        supplied = self.headers.get('Authorization', '')
        value = supplied[7:] if supplied.startswith('Bearer ') else ''
        return bool(TOKEN and hmac.compare_digest(TOKEN, value))

    def do_GET(self):
        if self.path == '/v1/capabilities':
            return self.send_json(capabilities())
        if self.path == '/v1/status':
            active = bool(PLAYER and PLAYER.poll() is None)
            return self.send_json({'playing': active, **(PLAYER_STATE if active else {})})
        return self.send_json({'error': 'not found'}, 404)

    def do_POST(self):
        global PREPARED
        if not self.authorised():
            return self.send_json({'error': 'unauthorised'}, 401)
        n = int(self.headers.get('Content-Length', '0'))
        data = json.loads(self.rfile.read(n) or b'{}')
        if self.path == '/v1/prepare':
            if not data.get('url'):
                return self.send_json({'error': 'url required'}, 400)
            PREPARED = {
                'url': data['url'], 'device': data.get('device') or DEFAULT_DEVICE,
                'volume': data.get('volume', 0.35), 'position_seconds': data.get('position_seconds', 0.0),
            }
            return self.send_json({'ok': True, 'prepared': PREPARED})
        if self.path == '/v1/start':
            if not PREPARED.get('url'):
                return self.send_json({'error': 'nothing prepared'}, 409)
            return self.send_json(play(PREPARED['url'], PREPARED.get('device'), PREPARED.get('volume', 0.35), PREPARED.get('position_seconds', 0.0)))
        if self.path == '/v1/play':
            if not data.get('url'):
                return self.send_json({'error': 'url required'}, 400)
            return self.send_json(play(data['url'], data.get('device'), data.get('volume', 0.35), data.get('position_seconds', 0.0)))
        if self.path == '/v1/stop':
            stop_player()
            return self.send_json({'ok': True})
        return self.send_json({'error': 'not found'}, 404)

    def log_message(self, fmt, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--listen', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8090)
    args = parser.parse_args()
    threading.Thread(target=heartbeat, daemon=True).start()
    ThreadingHTTPServer((args.listen, args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()
