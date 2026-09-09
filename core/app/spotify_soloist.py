import json
import os
import subprocess
from pathlib import Path


def binary(config=None):
    cfg=config or {}
    path=(cfg.get('binary') or os.getenv('SURROUNDCORE_SPOTIFY_SOLOIST','')).strip()
    if path and os.path.isfile(path) and os.access(path,os.X_OK):
        return path
    return None


def redacted(config):
    config=config or {}
    return {
        'configured': bool(config.get('api_key')),
        'binary_available': bool(binary(config)),
        'binary': config.get('binary',''),
        'device_name': config.get('device_name','SurroundCore Spotify'),
        'ws': config.get('ws','127.0.0.1:9090'),
        'data_dir': config.get('data_dir','/data/spotify'),
        'quality': 'Spotify highest available; Premium lossless up to 24-bit/44.1 kHz',
    }


def _ctl(config,*args,json_output=False):
    exe=binary(config)
    if not exe: raise RuntimeError('Spotify Soloist binary is not installed')
    cmd=[exe,'ctl','-w',config.get('ws','127.0.0.1:9090')]
    if json_output: cmd.append('--json')
    cmd += list(args)
    p=subprocess.run(cmd,capture_output=True,text=True,timeout=12)
    if p.returncode: raise RuntimeError((p.stderr or p.stdout or 'Spotify Soloist control failed').strip())
    text=p.stdout.strip()
    return json.loads(text) if json_output and text else {'ok':True,'output':text}


def status(config):
    return _ctl(config,'status')


def now(config):
    return _ctl(config,'now',json_output=True)


def queue(config,limit=20):
    return _ctl(config,'queue',str(max(1,min(int(limit),80))),json_output=True)


def play(config,uri=None):
    args=['play'] + ([uri] if uri else [])
    return _ctl(config,*args)


def pause(config): return _ctl(config,'pause')
def next_track(config): return _ctl(config,'next')
def previous(config): return _ctl(config,'prev')
def activate(config): return _ctl(config,'activate')
def deactivate(config): return _ctl(config,'deactivate')

def volume(config,value):
    return _ctl(config,'volume',str(max(0,min(int(value),100))))
