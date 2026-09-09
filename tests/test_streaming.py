import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))

TEST_DATA = tempfile.TemporaryDirectory()
os.environ['SURROUNDCORE_DATA'] = TEST_DATA.name
os.environ['SURROUNDCORE_TOKEN'] = 'test-token'
os.environ.pop('SURROUNDCORE_AIRIA_DECODER', None)

from fastapi.testclient import TestClient
from app.main import app
from app.db import init_db
init_db()


class StreamingAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.headers = {'Authorization': 'Bearer test-token'}

    def test_setup_page_exists(self):
        r = self.client.get('/setup/streaming')
        self.assertEqual(r.status_code, 200)
        self.assertIn('SurroundCore Streaming', r.text)

    def test_streaming_requires_token(self):
        r = self.client.get('/api/v1/streaming')
        self.assertEqual(r.status_code, 401)

    def test_provider_and_quality_overview(self):
        r = self.client.get('/api/v1/streaming', headers=self.headers)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        ids = {p['id'] for p in data['providers']}
        self.assertIn('bandcamp', ids)
        self.assertIn('hdtracks', ids)
        self.assertEqual(data['quality']['source'], 'highest_native')
        self.assertFalse(data['quality']['airia_available'])
        self.assertFalse(data['quality']['allow_downsample'])
        self.assertFalse(data['quality']['allow_downmix'])

    def test_radio_add_and_remove(self):
        r = self.client.post('/api/v1/streaming/radio', headers=self.headers,
                             json={'name': 'Test Radio', 'url': 'https://example.invalid/stream'})
        self.assertEqual(r.status_code, 200)
        station = r.json()['radio_stations'][-1]
        self.assertEqual(station['name'], 'Test Radio')
        r = self.client.delete('/api/v1/streaming/radio/' + station['id'], headers=self.headers)
        self.assertEqual(r.status_code, 200)
        ids = {s['id'] for s in r.json()['radio_stations']}
        self.assertNotIn(station['id'], ids)

    def test_radio_rejects_non_http(self):
        r = self.client.post('/api/v1/streaming/radio', headers=self.headers,
                             json={'name': 'Bad', 'url': 'file:///tmp/test.flac'})
        self.assertEqual(r.status_code, 400)

    def test_quality_preferences_persist(self):
        r = self.client.post('/api/v1/streaming/settings', headers=self.headers,
                             json={'prefer_airia': False, 'allow_downsample': False, 'allow_downmix': False})
        self.assertEqual(r.status_code, 200)
        q = r.json()['quality']
        self.assertFalse(q['prefer_airia'])
        self.assertFalse(q['allow_downsample'])
        self.assertFalse(q['allow_downmix'])


if __name__ == '__main__':
    unittest.main()

class QualityRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.headers = {'Authorization': 'Bearer test-token'}
        cls.client.post('/api/v1/endpoints/register', headers=cls.headers, json={
            'id': 'meridian:test', 'name': 'Meridian Test', 'kind': 'meridian',
            'address': 'http://127.0.0.1:9',
            'capabilities': {'transports': ['pcm', 'mhr', 'mmhr']},
        })

    def test_stereo_prefers_mhr(self):
        r = self.client.get('/api/v1/streaming/route/meridian:test?channels=2', headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['output']['transport'], 'mhr')

    def test_multichannel_prefers_mmhr(self):
        r = self.client.get('/api/v1/streaming/route/meridian:test?channels=6', headers=self.headers)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['output']['transport'], 'mmhr')


def tearDownModule():
    TEST_DATA.cleanup()
