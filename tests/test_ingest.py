import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

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
             patch.object(engine,'block_identity',return_value={'type':'','label':'','uuid':'','bytes':700_000_000,'fingerprint':'disc'}) as block:
            self.assertEqual(engine.media_identity('/dev/sr0')['kind'],'audio_cd'); block.assert_called_once()


    def test_netmd_rip_uses_one_disc_session_and_preserves_metadata(self):
        status={
            'available':True,'rippable':True,'device':'Sony Test','device_signature':'devsig',
            'disc_title':'MD Album','track_count':2,'fingerprint':'mdfp','toc_hash':'tochash',
            'tracks':[
                {'index':0,'title':'First Song','encoding':0,'group_title':'Side A'},
                {'index':1,'title':'Mono Song','encoding':0,'group_title':''},
            ],'groups':[{'index':1,'title':'Side A','tracks':[0]}]
        }
        calls=[]
        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0]=='node':
                rawdir=Path(cmd[3]); rawdir.mkdir(parents=True,exist_ok=True)
                a=rawdir/'01-track.aea'; b=rawdir/'02-track.aea'; a.write_bytes(b'a'); b.write_bytes(b'b')
                return SimpleNamespace(returncode=0,stdout=f'CMMDDONE\t0\t{a}\t1\taea\nCMMDDONE\t1\t{b}\t1\taea\n',stderr='')
            if cmd[0]=='ffmpeg':
                Path(cmd[-1]).write_bytes(b'flac')
                return SimpleNamespace(returncode=0,stdout='',stderr='')
            return SimpleNamespace(returncode=0,stdout='',stderr='')
        probes=iter([[{'channels':2,'sample_rate':44100}],[{'channels':1,'sample_rate':44100}]])
        with patch.object(engine,'netmd_available',return_value=True), \
             patch.object(engine,'_netmd_status_unlocked',return_value=status), \
             patch.object(engine,'run',side_effect=fake_run), \
             patch.object(engine,'ffprobe_streams',side_effect=lambda _p: next(probes)):
            result=engine.rip_netmd()
        node_calls=[c for c in calls if c[0]=='node']
        self.assertEqual(len(node_calls),1)
        self.assertEqual(node_calls[0][2],'all')
        self.assertEqual([x['layout'] for x in result['outputs']],['Stereo','Mono'])
        manifest=Path(result['output_dir'])/'surroundcore-ingest.json'
        data=__import__('json').loads(manifest.read_text())
        self.assertEqual(data['toc_hash'],'tochash')
        self.assertEqual(data['device_signature'],'devsig')
        self.assertEqual(data['tracks'][0]['title'],'First Song')
        self.assertIn('First Song',Path(result['outputs'][0]['path']).name)

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
