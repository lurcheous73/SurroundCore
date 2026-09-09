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
TOKEN = os.getenv('SURROUNDCORE_TOKEN', '')
CORE_URL = os.getenv('SURROUNDCORE_CORE_URL', 'http://127.0.0.1:8080').rstrip('/')

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
        devices.append({
            'alsa': f'hw:{card},{device}',
            'description': line.strip(),
            'hdmi': 'HDMI' in line.upper(),
        })
    return {
        'kind': 'alsa',
        'hostname': os.uname().nodename,
        'devices': devices,
        'channel_mapping': 'source-layout-preserved',
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
        with urllib.request.urlopen(req, timeout=4) as r:
            return 200 <= r.status < 300
    except Exception:
        return False

def heartbeat():
    while True:
        register_once()
        time.sleep(30)

def stop_player():
    global PLAYER
    if PLAYER and PLAYER.poll() is None:
        PLAYER.send_signal(signal.SIGTERM)
        try:
            PLAYER.wait(timeout=2)
        except subprocess.TimeoutExpired:
            PLAYER.kill()
    PLAYER = None

def play(url, device='default'):
    global PLAYER
    stop_player()
    PLAYER = subprocess.Popen([
        'ffmpeg', '-hide_banner', '-loglevel', 'warning', '-re', '-i', url,
        '-map', '0:a:0', '-vn', '-f', 'alsa', device,
    ])
    return {'ok': True, 'pid': PLAYER.pid, 'device': device}

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
            return self.send_json({'playing': active, 'pid': PLAYER.pid if active else None})
        return self.send_json({'error': 'not found'}, 404)

    def do_POST(self):
        if not self.authorised():
            return self.send_json({'error': 'unauthorised'}, 401)
        n = int(self.headers.get('Content-Length', '0'))
        data = json.loads(self.rfile.read(n) or b'{}')
        if self.path == '/v1/play':
            if not data.get('url'):
                return self.send_json({'error': 'url required'}, 400)
            return self.send_json(play(data['url'], data.get('device', 'default')))
        if self.path == '/v1/stop':
            stop_player()
            return self.send_json({'ok': True})
        return self.send_json({'error': 'not found'}, 404)

    def log_message(self, fmt, *args):
        pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--listen', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=8090)
    args = ap.parse_args()
    threading.Thread(target=heartbeat, daemon=True).start()
    ThreadingHTTPServer((args.listen, args.port), Handler).serve_forever()

if __name__ == '__main__':
    main()
