import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))

from app.quality import pcm_playback_plan
from app import sessions


def media(**kw):
    base = {'id': 1, 'codec': 'flac', 'channels': 2, 'channel_layout': 'stereo',
            'sample_rate': 96000, 'bit_depth': 24, 'duration': 100.0,
            'metadata': {'title': 'Track', 'artist': 'Artist', 'album': 'Album'}}
    base.update(kw); return base


class QualityPlanTests(unittest.TestCase):
    def endpoint(self, **device):
        dev = {'alsa': 'plughw:0,0', 'raw_alsa': 'hw:0,0', 'max_channels': 8,
               'sample_rates': [44100, 48000, 96000, 192000], 'bit_depths': [16, 24, 32]}
        dev.update(device)
        return {'id': 'alsa:test', 'kind': 'alsa',
                'capabilities': {'direct_pcm': True, 'transports': ['pcm'], 'devices': [dev]}}

    def test_native_24_96_51_is_direct(self):
        p = pcm_playback_plan(media(channels=6, channel_layout='5.1'), self.endpoint(), 'default', {})
        self.assertTrue(p['supported']); self.assertEqual(p['mode'], 'direct')
        self.assertFalse(p['conversion_required'])

    def test_unsupported_rate_is_rejected_without_conversion(self):
        p = pcm_playback_plan(media(sample_rate=176400), self.endpoint(), 'default', {})
        self.assertFalse(p['supported']); self.assertIsNone(p['mode'])


class SessionTests(unittest.TestCase):
    def test_queue_state_and_controls(self):
        q = [media(id=1, duration=10.0), media(id=2, duration=20.0)]
        s = sessions.start_library('alsa:session-test', q, position_seconds=0.0, plan={'mode':'direct'})
        self.assertEqual(s['state'], 'playing')
        self.assertEqual(s['queue_items_remaining'], 1)
        self.assertTrue(s['is_next_allowed'])
        sessions.seek('alsa:session-test', 11.0)
        s = sessions.set_state('alsa:session-test', 'paused')
        self.assertEqual(s['queue_index'], 1)
        self.assertEqual(s['state'], 'paused')

    def test_next_previous_targets(self):
        q = [media(id=11, duration=10.0), media(id=12, duration=20.0)]
        sessions.start_library('alsa:targets', q)
        self.assertAlmostEqual(sessions.control_target('alsa:targets', 'next'), 10.0, places=3)
        sessions.seek('alsa:targets', 12.0)
        self.assertAlmostEqual(sessions.control_target('alsa:targets', 'previous'), 0.0, places=3)
        sessions.seek('alsa:targets', 15.0)
        self.assertAlmostEqual(sessions.control_target('alsa:targets', 'previous'), 10.0, places=3)


spec = importlib.util.spec_from_file_location('surround_agent', ROOT / 'endpoint' / 'surround-agent.py')
agent = importlib.util.module_from_spec(spec); spec.loader.exec_module(agent)


class AgentFormatTests(unittest.TestCase):
    def test_24bit_uses_32bit_lossless_container(self):
        rate, ch, bits, raw, codec, alsa = agent._pcm_format(
            {'codec':'flac','sample_rate':96000,'channels':6,'bit_depth':24})
        self.assertEqual((rate, ch, bits), (96000, 6, 24))
        self.assertEqual((raw, codec, alsa), ('s32le','pcm_s32le','S32_LE'))

    def test_dsd_is_refused_in_direct_mode(self):
        with self.assertRaises(RuntimeError):
            agent._pcm_format({'codec':'dsd_lsbf','sample_rate':2822400,'channels':2,'bit_depth':1})

    def test_gapless_format_key_detects_layout_change(self):
        a = agent._format_key({'codec':'flac','sample_rate':96000,'channels':6,'bit_depth':24,'channel_layout':'5.1'})
        b = agent._format_key({'codec':'flac','sample_rate':96000,'channels':6,'bit_depth':24,'channel_layout':'5.1(side)'})
        self.assertNotEqual(a, b)


if __name__ == '__main__': unittest.main()
