import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from app import db
from app.groups import GroupPlayback


class GroupDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = db.DB_PATH
        db.DB_PATH = os.path.join(self.tmp.name, 'test.sqlite3')
        db.init_db()

    def tearDown(self):
        db.DB_PATH = self.old
        self.tmp.cleanup()

    def test_group_and_latency_persist(self):
        db.save_group('whole-house', 'Whole House', [
            {'endpoint_id': 'sonos:office', 'latency_ms': 1800, 'volume': 40, 'enabled': True},
            {'endpoint_id': 'meridian:c15', 'latency_ms': 250, 'volume': 50, 'enabled': True},
        ])
        self.assertTrue(db.update_group_latency('whole-house', 'meridian:c15', 325))
        group = db.get_group('whole-house')
        self.assertEqual(group['name'], 'Whole House')
        latencies = {m['endpoint_id']: m['latency_ms'] for m in group['members']}
        self.assertEqual(latencies['sonos:office'], 1800)
        self.assertEqual(latencies['meridian:c15'], 325)


class SchedulerTests(unittest.TestCase):
    @patch('app.groups.stereo_flac_program_cache', return_value='/tmp/programme.flac')
    @patch('app.groups.get_media', return_value={'id': 1, 'path': '/tmp/x.flac'})
    def test_higher_latency_gets_earlier_command(self, _media, _programme):
        engine = GroupPlayback(lambda: 'http://core', lambda *a: {})
        group = {'id': 'g', 'members': [
            {'endpoint_id': 'a', 'latency_ms': 0, 'enabled': True},
            {'endpoint_id': 'b', 'latency_ms': 900, 'enabled': True},
        ]}
        endpoints = [
            {'id': 'a', 'kind': 'alsa', 'address': 'http://a', 'capabilities': {}},
            {'id': 'b', 'kind': 'alsa', 'address': 'http://b', 'capabilities': {}},
        ]
        with patch.object(engine, '_prepare', return_value=None), patch.object(engine, '_start_member', return_value=None):
            session = engine.play('s', group, endpoints, 1, 'token', lead_ms=3000)
        members = {m['endpoint_id']: m for m in session['members']}
        self.assertEqual(members['a']['command_at_ms'] - members['b']['command_at_ms'], 900)
        self.assertEqual(session['target_start_ms'] - members['a']['command_at_ms'], 0)


if __name__ == '__main__':
    unittest.main()
