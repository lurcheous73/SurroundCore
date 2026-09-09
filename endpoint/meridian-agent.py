#!/usr/bin/env python3
import argparse
import hmac
import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = os.getenv('SURROUNDCORE_TOKEN', '')
CORE_URL = os.getenv('SURROUNDCORE_CORE_URL', 'http://127.0.0.1:8080').rstrip('/')
ENDPOINT_IP = os.getenv('SURROUNDCORE_MERIDIAN_IP', '')
ENDPOINT_NAME = os.getenv('SURROUNDCORE_ENDPOINT_NAME', 'Meridian / Sooloos')
ADVERTISE = os.getenv('SURROUNDCORE_AGENT_ADVERTISE', '')
MONO = os.getenv('SURROUNDCORE_MERIDIAN_MONO', 'mono')
BRIDGE = os.getenv('SURROUNDCORE_MERIDIAN_BRIDGE', 'MeridianDirectStream.exe')
MANAGED = os.getenv('SURROUNDCORE_MERIDIAN_MANAGED', '')
DEFAULT_VOLUME = int(os.getenv('SURROUNDCORE_MERIDIAN_VOLUME', '42'))

PLAYER = None
PLAYER_LOG = None
PREPARED = {}
PAIRING = None
LOCK = threading.RLock()


def _management(command=None):
    with socket.create_connection((ENDPOINT_IP, 9030), timeout=2) as sock:
        sock.settimeout(0.35)
        chunks = []
        end = time.time() + 0.7
        while time.time() < end:
            try:
                chunks.append(sock.recv(4096))
            except socket.timeout:
                break
        if command:
            sock.sendall(command.encode('ascii') + b'\n')
            time.sleep(0.15)
            try:
                chunks.append(sock.recv(4096))
            except socket.timeout:
                pass
        return b''.join(chunks).decode('latin1', 'replace')


def current_pairing():
    text = _management()
    for line in text.splitlines():
        if line.startswith('*kpairing_1,'):
            return line.split(',', 1)[1].strip()
    return None


def claim():
    global PAIRING
    pair = current_pairing()
    if pair:
        PAIRING = pair
        response = _management('::Kpairing_1')
        if '!!ack' not in response:
            raise RuntimeError('Meridian pairing release was not acknowledged')
    time.sleep(0.35)


def restore_pairing():
    global PAIRING
    if not PAIRING:
        return
    try:
        _management('::kpairing_1,' + PAIRING)
    finally:
        PAIRING = None


def stop_player(restore=True):
    global PLAYER, PLAYER_LOG
    with LOCK:
        proc = PLAYER
        PLAYER = None
    if proc and proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    if PLAYER_LOG:
        try:
            PLAYER_LOG.close()
        except Exception:
            pass
        PLAYER_LOG = None
    if restore:
        restore_pairing()


def start_player():
    global PLAYER, PLAYER_LOG
    with LOCK:
        if not PREPARED.get('url'):
            raise RuntimeError('Nothing prepared')
        stop_player(restore=True)
        claim()
        env = os.environ.copy()
        if MANAGED:
            bcl = os.path.join(os.path.dirname(os.path.dirname(MONO)), 'lib', 'mono', '4.5')
            env['MONO_PATH'] = ':'.join(p for p in (bcl, MANAGED) if p)
        volume = max(1, min(99, int(PREPARED.get('volume', DEFAULT_VOLUME))))
        log_path = os.getenv('SURROUNDCORE_MERIDIAN_LOG', '/tmp/surroundcore-meridian-agent.log')
        PLAYER_LOG = open(log_path, 'ab', buffering=0)
        PLAYER = subprocess.Popen([MONO, BRIDGE, ENDPOINT_IP, PREPARED['url'], str(volume)],
                                  stdout=PLAYER_LOG, stderr=subprocess.STDOUT, env=env)
        pid = PLAYER.pid
    time.sleep(0.25)
    if PLAYER.poll() is not None:
        stop_player(restore=True)
        raise RuntimeError('Meridian bridge exited during startup')
    return {'ok': True, 'pid': pid, 'volume': volume, 'url': PREPARED['url']}


def local_ip():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((ENDPOINT_IP or '8.8.8.8', 9))
        return sock.getsockname()[0]
    finally:
        sock.close()


def registration(port=8095):
    address = ADVERTISE or f'http://{local_ip()}:{port}'
    return {
        'id': 'meridian:' + (ENDPOINT_IP or ENDPOINT_NAME),
        'name': ENDPOINT_NAME,
        'kind': 'meridian',
        'address': address,
        'capabilities': {
            'control_api': 'surround-agent-v2',
            'native_protocol': 'sooloos-streaming',
            'channels': 2,
            'sample_rates': [44100, 48000, 88200, 96000],
            'bit_depths': [16, 24],
            'scheduled_group_playback': True,
            'recommended_latency_ms': 2500,
        },
    }


def register_once(port):
    if not TOKEN or not CORE_URL:
        return False
    body = json.dumps(registration(port)).encode()
    req = urllib.request.Request(CORE_URL + '/api/v1/endpoints/register', data=body,
        headers={'Content-Type': 'application/json', 'Authorization': f'Bearer {TOKEN}'})
    try:
        with urllib.request.urlopen(req, timeout=4) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def heartbeat(port):
    while True:
        register_once(port)
        time.sleep(30)


class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorised(self):
        value = self.headers.get('Authorization', '')
        supplied = value[7:] if value.startswith('Bearer ') else ''
        return bool(TOKEN and hmac.compare_digest(TOKEN, supplied))

    def do_GET(self):
        if self.path == '/v1/capabilities':
            return self.send_json(registration(SERVER.server_port)['capabilities'])
        if self.path == '/v1/status':
            playing = bool(PLAYER and PLAYER.poll() is None)
            return self.send_json({'playing': playing, 'prepared': PREPARED, 'paired_originally': bool(PAIRING)})
        return self.send_json({'error': 'not found'}, 404)

    def do_POST(self):
        global PREPARED
        if not self.authorised():
            return self.send_json({'error': 'unauthorised'}, 401)
        n = int(self.headers.get('Content-Length', '0'))
        data = json.loads(self.rfile.read(n) or b'{}')
        try:
            if self.path == '/v1/play':
                if not data.get('url'):
                    return self.send_json({'error': 'url required'}, 400)
                PREPARED = {'url': data['url'], 'volume': data.get('volume', DEFAULT_VOLUME),
                            'position_seconds': float(data.get('position_seconds', 0.0))}
                return self.send_json(start_player())
            if self.path == '/v1/prepare':
                if not data.get('url'):
                    return self.send_json({'error': 'url required'}, 400)
                PREPARED = {'url': data['url'], 'volume': data.get('volume', DEFAULT_VOLUME),
                            'position_seconds': float(data.get('position_seconds', 0.0))}
                return self.send_json({'ok': True, 'prepared': PREPARED})
            if self.path == '/v1/start':
                return self.send_json(start_player())
            if self.path == '/v1/stop':
                stop_player(restore=True)
                PREPARED = {}
                return self.send_json({'ok': True})
        except Exception as exc:
            return self.send_json({'error': str(exc)}, 500)
        return self.send_json({'error': 'not found'}, 404)

    def log_message(self, fmt, *args):
        pass


def main():
    global SERVER
    parser = argparse.ArgumentParser()
    parser.add_argument('--listen', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=8095)
    args = parser.parse_args()
    if not ENDPOINT_IP:
        raise SystemExit('SURROUNDCORE_MERIDIAN_IP is required')
    SERVER = ThreadingHTTPServer((args.listen, args.port), Handler)
    threading.Thread(target=heartbeat, args=(args.port,), daemon=True).start()
    try:
        SERVER.serve_forever()
    finally:
        stop_player(restore=True)


if __name__ == '__main__':
    main()
