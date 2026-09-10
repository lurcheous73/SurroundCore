import hashlib
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time

RENDER_RATE = 44100
RENDER_BITS = 16
RENDER_CHANNELS = 2
DEFAULT_LATENCY_MS = 1750


def _local_ip_for(host):
    override = os.getenv('SURROUNDCORE_AIRPLAY_BIND_IP', '').strip()
    if override:
        return override
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((host, 9))
        return s.getsockname()[0]
    finally:
        s.close()


def _txt_argument(endpoint):
    txt = dict((endpoint.get('capabilities') or {}).get('txt') or {})
    values = {
        'features': txt.get('features') or txt.get('ft'),
        'flags': txt.get('flags') or txt.get('sf'),
        'model': txt.get('model') or txt.get('am') or txt.get('md'),
        'deviceid': txt.get('deviceid'),
        'manufacturer': txt.get('manufacturer'),
        'acl': txt.get('acl'),
    }
    return ' '.join(f'{k}={v}' for k, v in values.items() if v not in (None, ''))


def _metadata_commands(media, progress=0.0):
    meta = media.get('metadata') or {}
    fallback_title = os.path.splitext(os.path.basename(str(media.get('path') or '')))[0]
    duration = max(0, int(round(float(media.get('duration') or 0.0))))
    item_id = str(media.get('id') or hashlib.sha1(str(media).encode()).hexdigest()[:16])
    return [
        f"TITLE={meta.get('title') or fallback_title or 'SurroundCore'}",
        f"ARTIST={meta.get('artist') or meta.get('album_artist') or ''}",
        f"ALBUM={meta.get('album') or ''}",
        f'DURATION={duration}',
        f'ITEMID={item_id}',
        'ACTION=SENDMETA',
    ]


class AirPlaySession:
    def __init__(self, endpoint, volume=0.35):
        self.endpoint = endpoint
        self.host = endpoint.get('address')
        self.port = int((endpoint.get('capabilities') or {}).get('port') or 7000)
        self.bind_ip = _local_ip_for(self.host)
        self.volume = max(0, min(100, int(round(float(volume) * 100))))
        self.muted = False
        self._pre_mute_volume = self.volume or 35
        self.binary = os.getenv('SURROUNDCORE_AIRPLAY_CLI', '/opt/airplay/cliairplay-linux-x86_64')
        self.sender = None
        self.decoder = None
        self.cmdpipe = None
        self._lock = threading.RLock()
        self._status = []
        self._connected = threading.Event()
        self._clock_ready = threading.Event()
        self._bytes_cond = threading.Condition(self._lock)
        self._decoder_bytes = 0
        self._generation = 0
        self.media = None
        self.url = None
        self.position_seconds = 0.0
        self.paused = False
        self.started_monotonic = None

    def _read_status(self, stream):
        for raw in iter(stream.readline, b''):
            line = raw.decode('utf-8', 'replace').rstrip()
            # Do not take the playback lock here: startup waits on events while
            # holding it, so the status reader itself must never depend on it.
            self._status.append(line)
            if len(self._status) > 300:
                del self._status[:-300]
            if '[STATUS] connected' in line:
                self._connected.set()
            if 'clock_ready mode=ptp state=ready' in line:
                self._clock_ready.set()

    def _write_commands(self, commands):
        if not self.cmdpipe:
            raise RuntimeError('AirPlay command pipe is not ready')
        with open(self.cmdpipe, 'w', buffering=1) as fh:
            for command in commands:
                fh.write(command + '\n')

    def _ensure_sender(self):
        if self.sender and self.sender.poll() is None:
            return
        if not os.path.isfile(self.binary) or not os.access(self.binary, os.X_OK):
            raise RuntimeError(f'AirPlay sender not executable: {self.binary}')
        token = hashlib.sha1(str(self.endpoint.get('id') or self.host).encode()).hexdigest()[:12]
        self.cmdpipe = os.path.join(tempfile.gettempdir(), f'surroundcore-airplay-{token}.cmd')
        try:
            os.unlink(self.cmdpipe)
        except FileNotFoundError:
            pass
        os.mkfifo(self.cmdpipe, 0o600)
        txt = _txt_argument(self.endpoint)
        args = [
            self.binary, '--protocol', 'airplay2', '--ap2-native', '--port', str(self.port),
            '--if', self.bind_ip, '--publish-ip', self.bind_ip, '--volume', str(self.volume),
            '--latency', str(DEFAULT_LATENCY_MS), '--samplerate', str(RENDER_RATE),
            '--bitdepth', str(RENDER_BITS), '--channels', str(RENDER_CHANNELS),
            '--ptp', '--timing', 'ptp', '--debug', '3', '--name', self.endpoint.get('name') or 'AirPlay',
            '--hostname', 'SurroundCore', '--cmdpipe', self.cmdpipe,
        ]
        if txt:
            args += ['--txt', txt]
        args.append(self.host)
        env = dict(os.environ)
        env['CLIAIRPLAY_BUFFERED'] = '0'
        self.sender = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        threading.Thread(target=self._read_status, args=(self.sender.stdout,), daemon=True).start()
        threading.Thread(target=self._read_status, args=(self.sender.stderr,), daemon=True).start()
        if not self._connected.wait(8.0):
            self.stop()
            raise RuntimeError('AirPlay sender did not connect')
        if not self._clock_ready.wait(8.0):
            self.stop()
            raise RuntimeError('AirPlay PTP clock did not become ready')

    def _stop_decoder(self):
        proc = self.decoder
        self.decoder = None
        self._generation += 1
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=1.0)

    def _pump(self, proc, generation):
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                with self._lock:
                    if generation != self._generation or not self.sender or self.sender.poll() is not None:
                        break
                    target = self.sender.stdin
                target.write(chunk)
                target.flush()
                with self._bytes_cond:
                    self._decoder_bytes += len(chunk)
                    self._bytes_cond.notify_all()
        except (BrokenPipeError, OSError):
            pass

    def _start_decoder(self, url, position_seconds):
        self._stop_decoder()
        self._decoder_bytes = 0
        args = ['ffmpeg', '-hide_banner', '-loglevel', 'warning', '-nostdin']
        if float(position_seconds or 0) > 0:
            args += ['-ss', f'{float(position_seconds):.3f}']
        args += [
            '-i', url, '-map', '0:a:0', '-vn', '-ac', str(RENDER_CHANNELS),
            '-ar', str(RENDER_RATE), '-sample_fmt', 's16', '-f', 's16le', 'pipe:1',
        ]
        self.decoder = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        generation = self._generation
        threading.Thread(target=self._pump, args=(self.decoder, generation), daemon=True).start()
        deadline = time.monotonic() + 3.0
        with self._bytes_cond:
            while self._decoder_bytes < 32768 and time.monotonic() < deadline:
                self._bytes_cond.wait(timeout=0.05)
        if self._decoder_bytes == 0:
            raise RuntimeError('AirPlay decoder produced no PCM')

    def play(self, url, media, position_seconds=0.0):
        with self._lock:
            self._ensure_sender()
            if self.media is not None:
                self._write_commands(['ACTION=FLUSH'])
            self.url = url
            self.media = media
            self.position_seconds = max(0.0, float(position_seconds or 0.0))
            self.paused = False
            self._start_decoder(url, self.position_seconds)
            self._write_commands(_metadata_commands(media, self.position_seconds))
            self._write_commands(['START_UNIX_MS=0', 'ACTION=START'])
            time.sleep(0.10)
            self._write_commands([f'PROGRESS={max(0, int(self.position_seconds))}'])
            self.started_monotonic = time.monotonic()
            return self.status()

    def pause(self):
        with self._lock:
            if not self.sender or self.sender.poll() is not None:
                return False
            if self.paused:
                return True
            self.position_seconds = self.current_position()
            self._write_commands(['ACTION=PAUSE'])
            if self.decoder and self.decoder.poll() is None:
                self.decoder.send_signal(signal.SIGSTOP)
            self.paused = True
            return True

    def resume(self):
        with self._lock:
            if not self.sender or self.sender.poll() is not None:
                return False
            if not self.paused:
                return True
            if self.decoder and self.decoder.poll() is None:
                self.decoder.send_signal(signal.SIGCONT)
            self._write_commands(['ACTION=PLAY'])
            self.started_monotonic = time.monotonic()
            self.paused = False
            return True

    def seek(self, seconds):
        with self._lock:
            if self.media is None or not self.url:
                raise RuntimeError('AirPlay session has no media')
            target = max(0.0, float(seconds or 0.0))
            was_paused = self.paused
            self._write_commands(['ACTION=FLUSH'])
            self._start_decoder(self.url, target)
            self.position_seconds = target
            self._write_commands(_metadata_commands(self.media, target))
            self._write_commands(['START_UNIX_MS=0', 'ACTION=START'])
            time.sleep(0.10)
            self._write_commands([f'PROGRESS={max(0, int(target))}'])
            self.started_monotonic = time.monotonic()
            self.paused = False
            if was_paused:
                self.pause()
            return self.status()

    def set_volume(self, volume):
        with self._lock:
            value = max(0, min(100, int(round(float(volume)))))
            if self.sender and self.sender.poll() is None:
                self._write_commands([f'VOLUME={value}'])
            self.volume = value
            if value > 0:
                self._pre_mute_volume = value
                self.muted = False
            return value

    def set_mute(self, muted):
        muted = bool(muted)
        if muted:
            if self.volume > 0:
                self._pre_mute_volume = self.volume
            self.set_volume(0)
            self.muted = True
        else:
            self.set_volume(self._pre_mute_volume or 35)
            self.muted = False
        return self.muted

    def current_position(self):
        if self.started_monotonic is None or self.paused:
            return self.position_seconds
        return self.position_seconds + max(0.0, time.monotonic() - self.started_monotonic)

    def status(self):
        active = bool(self.sender and self.sender.poll() is None)
        return {
            'active': active,
            'state': 'paused' if active and self.paused else ('playing' if active else 'stopped'),
            'endpoint_id': self.endpoint.get('id'),
            'position_seconds': self.current_position(),
            'volume': self.volume, 'muted': self.muted,
            'source': {k: self.media.get(k) for k in ('codec', 'sample_rate', 'bit_depth', 'channels', 'channel_layout')} if self.media else None,
            'render': {'codec': 'alac', 'sample_rate': RENDER_RATE, 'bit_depth': RENDER_BITS, 'channels': RENDER_CHANNELS},
            'sender_pid': self.sender.pid if active else None,
            'decoder_pid': self.decoder.pid if self.decoder and self.decoder.poll() is None else None,
            'recent_status': list(self._status[-20:]),
        }

    def stop(self):
        with self._lock:
            self._stop_decoder()
            if self.sender and self.sender.poll() is None:
                try:
                    self._write_commands(['ACTION=STOP'])
                    self.sender.wait(timeout=2.0)
                except Exception:
                    self.sender.terminate()
                    try:
                        self.sender.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        self.sender.kill()
            self.sender = None
            if self.cmdpipe:
                try:
                    os.unlink(self.cmdpipe)
                except FileNotFoundError:
                    pass
            self.cmdpipe = None
            return {'ok': True}


_sessions = {}
_manager_lock = threading.RLock()


def _session(endpoint, volume=0.35):
    endpoint_id = endpoint.get('id')
    with _manager_lock:
        session = _sessions.get(endpoint_id)
        endpoint_port = int((endpoint.get('capabilities') or {}).get('port') or 7000)
        if session and (session.host != endpoint.get('address') or session.port != endpoint_port):
            session.stop()
            session = None
        if not session:
            session = AirPlaySession(endpoint, volume)
            _sessions[endpoint_id] = session
        return session


def play(endpoint, url, media, position_seconds=0.0, volume=0.35):
    return _session(endpoint, volume).play(url, media, position_seconds)


def pause(endpoint_id):
    with _manager_lock:
        return bool(_sessions.get(endpoint_id) and _sessions[endpoint_id].pause())


def resume(endpoint_id):
    with _manager_lock:
        return bool(_sessions.get(endpoint_id) and _sessions[endpoint_id].resume())


def seek(endpoint_id, seconds):
    with _manager_lock:
        if endpoint_id not in _sessions:
            raise RuntimeError('AirPlay session not found')
        return _sessions[endpoint_id].seek(seconds)


def status(endpoint_id):
    with _manager_lock:
        return _sessions[endpoint_id].status() if endpoint_id in _sessions else {'active': False, 'state': 'stopped'}


def stop(endpoint_id):
    with _manager_lock:
        session = _sessions.pop(endpoint_id, None)
    return session.stop() if session else {'ok': True}


def stop_all():
    with _manager_lock:
        ids = list(_sessions)
    for endpoint_id in ids:
        stop(endpoint_id)
