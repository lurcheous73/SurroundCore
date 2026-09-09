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
DECODER = None
PLAYER_STATE = {}
PROGRAMME_FILE = None
PREPARED = {}
TOKEN = os.getenv('SURROUNDCORE_TOKEN', '')
CORE_URL = os.getenv('SURROUNDCORE_CORE_URL', 'http://127.0.0.1:8080').rstrip('/')
DEFAULT_DEVICE = os.getenv('SURROUNDCORE_DEFAULT_DEVICE', 'default')
MAX_VOLUME = 0.49
DIRECT_MODE = 'direct'
COMPAT_MODE = 'compatibility'


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


def _ints(text, minimum=0):
    out = []
    for token in __import__('re').findall(r'\b\d+\b', text or ''):
        value = int(token)
        if value >= minimum:
            out.append(value)
    return out


def _hdmi_lpcm_caps(card):
    from pathlib import Path
    rates, bits, channels = set(), set(), []
    for path in Path(f'/proc/asound/card{card}').glob('eld*'):
        try: lines = path.read_text(errors='replace').splitlines()
        except OSError: continue
        sad = {}
        for line in lines:
            line = line.strip()
            m = __import__('re').match(r'sad(\d+)_(\w+)\s+(.*)', line)
            if not m: continue
            sad.setdefault(m.group(1), {})[m.group(2)] = m.group(3)
        for item in sad.values():
            coding = str(item.get('coding_type') or '').upper()
            if 'LPCM' not in coding:
                continue
            channels += _ints(item.get('channels', ''))
            rates.update(x for x in _ints(item.get('rates', ''), 8000) if x <= 768000)
            bits.update(x for x in _ints(item.get('bits', ''), 8) if x <= 64)
    return {
        'max_channels': max(channels) if channels else None,
        'sample_rates': sorted(rates), 'bit_depths': sorted(bits),
        'capability_source': 'hdmi-eld' if rates or channels or bits else 'unknown',
    }


def _usb_pcm_caps(card):
    from pathlib import Path
    path = Path(f'/proc/asound/card{card}/stream0')
    if not path.is_file():
        return {'max_channels': None, 'sample_rates': [], 'bit_depths': [], 'capability_source': 'unknown'}
    try: text = path.read_text(errors='replace')
    except OSError: text = ''
    rates, bits, channels = set(), set(), []
    in_playback = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.endswith('Playback:') or line == 'Playback:': in_playback = True; continue
        if line.endswith('Capture:') or line == 'Capture:': in_playback = False; continue
        if not in_playback: continue
        if line.startswith('Channels:'): channels += _ints(line.split(':',1)[1])
        elif line.startswith('Rates:'): rates.update(x for x in _ints(line.split(':',1)[1], 8000) if x <= 768000)
        elif line.startswith('Format:'):
            fm = line.split(':',1)[1].upper()
            for b in (8,16,20,24,32,64):
                if str(b) in fm: bits.add(b)
    return {
        'max_channels': max(channels) if channels else None,
        'sample_rates': sorted(rates), 'bit_depths': sorted(bits),
        'capability_source': 'usb-audio-descriptor' if rates or channels or bits else 'unknown',
    }


def _pcm_caps(card, output_type):
    if output_type == 'hdmi': return _hdmi_lpcm_caps(card)
    if output_type == 'usb-audio': return _usb_pcm_caps(card)
    return {'max_channels': None, 'sample_rates': [], 'bit_depths': [], 'capability_source': 'unknown'}


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
        pcm_caps = _pcm_caps(card, output_type)
        devices.append({
            'alsa': f'plughw:{card},{device}',
            'raw_alsa': f'hw:{card},{device}',
            'description': line.strip(),
            'output_type': output_type,
            'digital': output_type in ('hdmi', 'spdif', 'aes3'),
            'multichannel_candidate': output_type in ('hdmi', 'usb-audio', 'aes3', 'i2s'),
            **pcm_caps,
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
        'transports': ['pcm'],
        'mhr': False,
        'mmhr': False,
        'direct_pcm': True,
        'direct_mode': 'no-volume-filter/no-forced-rate/no-forced-channels',
        'bitperfect_pcm': 'requires-device-validation',
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


def _terminate(proc):
    if not proc or proc.poll() is not None:
        return
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def stop_player():
    global PLAYER, DECODER, PLAYER_STATE, PROGRAMME_FILE
    _terminate(DECODER)
    _terminate(PLAYER)
    PLAYER = None
    DECODER = None
    PLAYER_STATE = {}
    if PROGRAMME_FILE:
        try: os.unlink(PROGRAMME_FILE)
        except OSError: pass
    PROGRAMME_FILE = None
PREPARED = {}


def _raw_device(device):
    if not device or device == 'default':
        caps = capabilities().get('devices') or []
        return caps[0]['raw_alsa'] if caps else device or 'default'
    if device.startswith('plughw:'):
        return 'hw:' + device[7:]
    return device


def _probe_audio(url):
    p = subprocess.run([
        'ffprobe', '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=codec_name,channels,channel_layout,sample_rate,bits_per_raw_sample,bits_per_sample,sample_fmt',
        '-of', 'json', url,
    ], capture_output=True, text=True, timeout=12, check=False)
    if p.returncode:
        raise RuntimeError((p.stderr or 'ffprobe failed').strip())
    data = json.loads(p.stdout or '{}')
    if not data.get('streams'):
        raise RuntimeError('Source contains no audio stream')
    a = data['streams'][0]
    raw_bits = a.get('bits_per_raw_sample') or a.get('bits_per_sample') or 0
    try: bits = int(raw_bits or 0)
    except (TypeError, ValueError): bits = 0
    return {
        'codec': str(a.get('codec_name') or ''),
        'channels': int(a.get('channels') or 0),
        'channel_layout': str(a.get('channel_layout') or ''),
        'sample_rate': int(a.get('sample_rate') or 0),
        'bit_depth': bits,
        'sample_fmt': str(a.get('sample_fmt') or ''),
    }


def _pcm_format(source):
    codec = str(source.get('codec') or '').lower()
    if codec.startswith('dsd'):
        raise RuntimeError('Native DSD/DoP output is not implemented; refusing PCM conversion in direct mode')
    rate = int(source.get('sample_rate') or 0)
    channels = int(source.get('channels') or 0)
    bits = int(source.get('bit_depth') or 0)
    if rate <= 0 or channels <= 0:
        raise RuntimeError('Source PCM rate/channel metadata unavailable')
    if 0 < bits <= 16:
        return rate, channels, bits, 's16le', 'pcm_s16le', 'S16_LE'
    # 20/24-bit samples are carried losslessly in a 32-bit ALSA container.
    return rate, channels, bits or 24, 's32le', 'pcm_s32le', 'S32_LE'


def _format_key(source):
    rate, channels, bits, raw_fmt, pcm_codec, alsa_fmt = _pcm_format(source)
    return (rate, channels, bits, str(source.get('channel_layout') or '').lower(), raw_fmt, pcm_codec, alsa_fmt)


def _start_direct(urls, device, source, position_seconds=0.0):
    global PLAYER, DECODER, PROGRAMME_FILE
    rate, channels, bits, raw_fmt, pcm_codec, alsa_fmt = _pcm_format(source)
    actual_device = _raw_device(device)
    decoder = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin']
    if len(urls) == 1:
        if float(position_seconds or 0) > 0:
            decoder += ['-ss', f'{float(position_seconds):.3f}']
        decoder += ['-i', urls[0]]
    else:
        import tempfile
        fd, path = tempfile.mkstemp(prefix='surroundcore-programme-', suffix='.ffconcat')
        os.close(fd)
        with open(path, 'w') as fh:
            fh.write('ffconcat version 1.0\n')
            for url in urls:
                fh.write("file '" + str(url).replace("'", "'\\''") + "'\n")
        PROGRAMME_FILE = path
        decoder += ['-protocol_whitelist', 'file,http,https,tcp,tls,crypto', '-f', 'concat', '-safe', '0', '-i', path]
        if float(position_seconds or 0) > 0:
            decoder += ['-ss', f'{float(position_seconds):.3f}']
    decoder += ['-map', '0:a:0', '-vn', '-f', raw_fmt, '-acodec', pcm_codec,
                '-ar', str(rate), '-ac', str(channels), 'pipe:1']
    player = ['aplay', '-q', '-D', actual_device, '-t', 'raw', '-f', alsa_fmt,
              '-r', str(rate), '-c', str(channels)]
    DECODER = subprocess.Popen(decoder, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    PLAYER = subprocess.Popen(player, stdin=DECODER.stdout, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    DECODER.stdout.close()
    time.sleep(0.12)
    if PLAYER.poll() is not None:
        detail = (PLAYER.stderr.read().decode(errors='replace') if hasattr(PLAYER.stderr, 'read') else '') or 'ALSA raw device rejected source format'
        _terminate(DECODER)
        raise RuntimeError(detail.strip())
    return actual_device, {
        'sample_rate': rate, 'channels': channels, 'source_bit_depth': bits,
        'alsa_format': alsa_fmt, 'pcm_codec': pcm_codec,
    }


def play(url, device=None, volume=0.35, position_seconds=0.0, mode=DIRECT_MODE, source=None):
    global PLAYER, DECODER, PLAYER_STATE
    stop_player()
    mode = mode if mode in (DIRECT_MODE, COMPAT_MODE) else DIRECT_MODE
    requested_device = device or DEFAULT_DEVICE
    volume = max(0.0, min(float(volume), MAX_VOLUME))
    source = source or _probe_audio(url)
    if mode == DIRECT_MODE:
        actual_device, output = _start_direct([url], requested_device, source, position_seconds)
    else:
        actual_device = requested_device
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin']
        if float(position_seconds or 0) > 0:
            cmd += ['-ss', f'{float(position_seconds):.3f}']
        cmd += ['-i', url, '-map', '0:a:0', '-vn', '-af', f'volume={volume:.4f}', '-f', 'alsa', actual_device]
        PLAYER = subprocess.Popen(cmd)
        DECODER = None
        output = {'compatibility': True}
    PLAYER_STATE = {
        'pid': PLAYER.pid, 'decoder_pid': DECODER.pid if DECODER else None,
        'device': actual_device, 'requested_device': requested_device,
        'mode': mode, 'software_volume': volume if mode == COMPAT_MODE else None,
        'url': url, 'position_seconds': float(position_seconds or 0),
        'dsp': mode == COMPAT_MODE, 'source': source, 'output': output,
        'started_monotonic': time.monotonic(), 'paused': False, 'paused_total': 0.0,
    }
    return {'ok': True, **PLAYER_STATE}


def play_programme(urls, device=None, volume=0.35, position_seconds=0.0, mode=DIRECT_MODE, source=None, sources=None):
    global PLAYER, DECODER, PLAYER_STATE, PROGRAMME_FILE
    stop_player()
    if not urls:
        raise RuntimeError('Programme contains no tracks')
    mode = mode if mode in (DIRECT_MODE, COMPAT_MODE) else DIRECT_MODE
    requested_device = device or DEFAULT_DEVICE
    volume = max(0.0, min(float(volume), MAX_VOLUME))
    programme_sources = list(sources or [])
    if mode == DIRECT_MODE:
        if len(programme_sources) != len(urls):
            programme_sources = [_probe_audio(u) for u in urls]
        source = programme_sources[0]
        keys = [_format_key(x) for x in programme_sources]
        if any(k != keys[0] for k in keys[1:]):
            raise RuntimeError('Native gapless programme requires identical sample rate, channel layout and bit depth')
        actual_device, output = _start_direct(list(urls), requested_device, source, position_seconds)
    else:
        source = source or (programme_sources[0] if programme_sources else _probe_audio(urls[0]))
        import tempfile
        fd, path = tempfile.mkstemp(prefix='surroundcore-programme-', suffix='.ffconcat')
        os.close(fd)
        with open(path, 'w') as fh:
            fh.write('ffconcat version 1.0\n')
            for url in urls:
                fh.write("file '" + str(url).replace("'", "'\\''") + "'\n")
        PROGRAMME_FILE = path
        cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin',
               '-protocol_whitelist', 'file,http,https,tcp,tls,crypto', '-f', 'concat', '-safe', '0', '-i', path]
        if float(position_seconds or 0) > 0:
            cmd += ['-ss', f'{float(position_seconds):.3f}']
        cmd += ['-map', '0:a:0', '-vn', '-af', f'volume={volume:.4f}', '-f', 'alsa', requested_device]
        PLAYER = subprocess.Popen(cmd)
        DECODER = None
        actual_device = requested_device
        output = {'compatibility': True}
    PLAYER_STATE = {
        'pid': PLAYER.pid, 'decoder_pid': DECODER.pid if DECODER else None,
        'device': actual_device, 'requested_device': requested_device,
        'mode': mode, 'software_volume': volume if mode == COMPAT_MODE else None,
        'urls': list(urls), 'programme': True, 'queue_count': len(urls),
        'position_seconds': float(position_seconds or 0), 'dsp': mode == COMPAT_MODE,
        'source': source, 'sources': programme_sources if mode == DIRECT_MODE else None, 'output': output, 'started_monotonic': time.monotonic(),
        'paused': False, 'paused_total': 0.0,
    }
    return {'ok': True, **PLAYER_STATE}


def current_position():
    if not PLAYER_STATE:
        return 0.0
    base = float(PLAYER_STATE.get('position_seconds') or 0.0)
    started = float(PLAYER_STATE.get('started_monotonic') or time.monotonic())
    end = float(PLAYER_STATE.get('paused_at') or time.monotonic()) if PLAYER_STATE.get('paused') else time.monotonic()
    return max(base, base + end - started - float(PLAYER_STATE.get('paused_total') or 0.0))


def pause_player():
    if not PLAYER or PLAYER.poll() is not None:
        return False
    if PLAYER_STATE.get('paused'):
        return True
    for proc in (DECODER, PLAYER):
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGSTOP)
    PLAYER_STATE['paused'] = True
    PLAYER_STATE['paused_at'] = time.monotonic()
    return True


def resume_player():
    if not PLAYER or PLAYER.poll() is not None:
        return False
    if not PLAYER_STATE.get('paused'):
        return True
    now = time.monotonic()
    paused_at = float(PLAYER_STATE.get('paused_at') or now)
    PLAYER_STATE['paused_total'] = float(PLAYER_STATE.get('paused_total') or 0.0) + max(0.0, now - paused_at)
    PLAYER_STATE.pop('paused_at', None)
    PLAYER_STATE['paused'] = False
    for proc in (DECODER, PLAYER):
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGCONT)
    return True


def seek_player(seconds):
    if not PLAYER_STATE:
        return {'ok': False, 'state': 'stopped'}
    state = dict(PLAYER_STATE)
    seconds = max(0.0, float(seconds or 0.0))
    if state.get('programme'):
        return play_programme(state.get('urls') or [], state.get('requested_device'),
                              state.get('software_volume') or 0.35, seconds, state.get('mode', DIRECT_MODE),
                              state.get('source'), state.get('sources'))
    return play(state.get('url'), state.get('requested_device'), state.get('software_volume') or 0.35,
                seconds, state.get('mode', DIRECT_MODE), state.get('source'))


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
            state = 'paused' if active and PLAYER_STATE.get('paused') else ('playing' if active else 'stopped')
            payload = dict(PLAYER_STATE) if PLAYER_STATE else {}
            payload['position_seconds'] = current_position() if active else float(payload.get('position_seconds') or 0.0)
            return self.send_json({'playing': active, 'state': state, **payload})
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
                'mode': data.get('mode', DIRECT_MODE), 'source': data.get('source'),
            }
            return self.send_json({'ok': True, 'prepared': PREPARED})
        if self.path == '/v1/start':
            if not PREPARED.get('url'):
                return self.send_json({'error': 'nothing prepared'}, 409)
            return self.send_json(play(PREPARED['url'], PREPARED.get('device'), PREPARED.get('volume', 0.35), PREPARED.get('position_seconds', 0.0), PREPARED.get('mode', DIRECT_MODE), PREPARED.get('source')))
        if self.path == '/v1/play':
            if not data.get('url'):
                return self.send_json({'error': 'url required'}, 400)
            return self.send_json(play(data['url'], data.get('device'), data.get('volume', 0.35), data.get('position_seconds', 0.0), data.get('mode', DIRECT_MODE), data.get('source')))
        if self.path == '/v1/programme':
            urls = data.get('urls') or []
            if not isinstance(urls, list) or not urls:
                return self.send_json({'error': 'urls required'}, 400)
            return self.send_json(play_programme(urls, data.get('device'), data.get('volume', 0.35), data.get('position_seconds', 0.0), data.get('mode', DIRECT_MODE), data.get('source'), data.get('sources')))
        if self.path == '/v1/pause':
            return self.send_json({'ok': pause_player(), 'state': 'paused' if PLAYER_STATE.get('paused') else 'stopped'})
        if self.path == '/v1/resume':
            return self.send_json({'ok': resume_player(), 'state': 'playing' if PLAYER and PLAYER.poll() is None else 'stopped'})
        if self.path == '/v1/seek':
            return self.send_json(seek_player(data.get('position_seconds', 0.0)))
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
