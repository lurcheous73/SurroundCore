import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ingest'))
TMP=tempfile.TemporaryDirectory()
os.environ['SURROUNDCORE_INGEST_ROOT']=str(Path(TMP.name)/'ingest')
os.environ['SURROUNDCORE_INGEST_LIBRARY']=str(Path(TMP.name)/'library')
os.environ['SURROUNDCORE_TOKEN']='test-token'
os.environ['SURROUNDCORE_AUTO_RIP']='false'
os.environ['SURROUNDCORE_AUTO_NETMD']='false'

from ingest_app import engine

class AudioPolicyTests(unittest.TestCase):
    def test_prefers_best_stereo_and_each_multichannel_layout(self):
        streams=[
          {'index':0,'codec':'ac3','channels':2,'layout':'stereo','sample_rate':48000,'bits':16},
          {'index':1,'codec':'pcm_s24be','channels':2,'layout':'stereo','sample_rate':96000,'bits':24},
          {'index':2,'codec':'dts','channels':6,'layout':'5.1','sample_rate':48000,'bits':16},
          {'index':3,'codec':'pcm_s24be','channels':6,'layout':'5.1','sample_rate':96000,'bits':24},
          {'index':4,'codec':'pcm_s24be','channels':4,'layout':'quad','sample_rate':96000,'bits':24},
          {'index':5,'codec':'truehd','channels':8,'layout':'7.1','sample_rate':96000,'bits':24},
        ]
        chosen=engine.select_audio(streams)
        self.assertEqual([x['index'] for x in chosen],[1,4,3,5])
        self.assertEqual([engine.normalized_layout(x) for x in chosen],['Stereo','4.0-Quad','5.1','7.1'])

    def test_no_downmix_policy(self):
        chosen=engine.select_audio([{'index':7,'codec':'pcm_s24be','channels':6,'layout':'5.1','sample_rate':96000,'bits':24}])
        self.assertEqual(chosen[0]['channels'],6)

    def test_safe_name_strips_path_chars(self):
        self.assertEqual(engine.safe_name('../Queen/A:B?'), 'Queen A B')

class DeviceTests(unittest.TestCase):
    def test_media_identity_prefers_audio_cd(self):
        with patch.object(engine,'audio_cd_info',return_value={'kind':'audio_cd','fingerprint':'abc','tracks':12}), \
             patch.object(engine,'block_identity') as block:
            self.assertEqual(engine.media_identity('/dev/sr0')['kind'],'audio_cd'); block.assert_not_called()

    def test_makemkv_not_claimed_without_binary(self):
        with patch.object(engine.shutil,'which',return_value=None):
            old=engine.MAKEMKV; engine.MAKEMKV=''
            try: self.assertFalse(engine.makemkv_available())
            finally: engine.MAKEMKV=old

class APITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import ingest_app.main as main
        cls.main=main
        from fastapi.testclient import TestClient
        cls.client=TestClient(main.app)
        cls.headers={'Authorization':'Bearer test-token'}

    def test_health(self):
        r=self.client.get('/api/v1/ingest/health'); self.assertEqual(r.status_code,200); self.assertEqual(r.json()['version'],'0.5.0-dev')

    def test_capabilities_require_token(self):
        self.assertEqual(self.client.get('/api/v1/ingest/capabilities').status_code,401)

    def test_capabilities_are_audio_only(self):
        r=self.client.get('/api/v1/ingest/capabilities',headers=self.headers); self.assertEqual(r.status_code,200); self.assertTrue(r.json()['audio_only'])

    def test_upload_rejects_wrong_extension(self):
        r=self.client.post('/api/v1/ingest/upload',headers=self.headers,files={'files':('movie.mp4',b'x','video/mp4')})
        self.assertEqual(r.status_code,400)

    def test_iso_upload_queues_without_mac_staging(self):
        with patch.object(self.main.jobs,'enqueue',return_value={'id':'job1','status':'queued'}) as enqueue:
            r=self.client.post('/api/v1/ingest/upload',headers=self.headers,files={'files':('album.iso',b'iso-data','application/octet-stream')})
        self.assertEqual(r.status_code,200); self.assertEqual(r.json()['jobs'][0]['id'],'job1')
        args=enqueue.call_args.args; self.assertEqual(args[0],'iso'); self.assertTrue(args[1].endswith('/uploads/album.iso'))


def tearDownModule(): TMP.cleanup()
