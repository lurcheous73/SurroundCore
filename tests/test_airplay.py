import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'core'))

from app.airplay import _metadata_commands, _txt_argument
from app.mdns_endpoints import _normalise_airplay_id
from app.quality import pcm_playback_plan


def media(**kw):
    item = {
        'id': 99, 'codec': 'flac', 'channels': 2, 'channel_layout': 'stereo',
        'sample_rate': 96000, 'bit_depth': 24, 'duration': 297.6,
        'metadata': {'title': 'Hi Res', 'artist': 'Artist', 'album': 'Album'},
    }
    item.update(kw)
    return item


def endpoint(**caps):
    base = {
        'service_type': '_airplay._tcp.local.', 'port': 7000,
        'render': {'codec': 'alac', 'sample_rate': 44100, 'bit_depth': 16, 'channels': 2},
        'txt': {'features': '0x445F8A00,0x801C340', 'flags': '0x404', 'model': 'Bookshelf',
                'deviceid': '38:42:0B:93:07:24', 'manufacturer': 'Sonos', 'acl': '0'},
    }
    base.update(caps)
    return {'id': 'airplay:38:42:0B:93:07:24', 'kind': 'airplay', 'name': 'Office',
            'address': '10.26.30.17', 'capabilities': base}


class AirPlayPlanTests(unittest.TestCase):
    def test_24_96_source_is_preserved_and_render_is_explicit(self):
        plan = pcm_playback_plan(media(), endpoint(), settings={})
        self.assertEqual(plan['mode'], 'airplay-native-realtime')
        self.assertEqual((plan['source']['sample_rate'], plan['source']['bit_depth']), (96000, 24))
        self.assertEqual((plan['render']['sample_rate'], plan['render']['bit_depth']), (44100, 16))
        self.assertTrue(plan['conversion_required']); self.assertTrue(plan['preserve_source'])
        self.assertEqual(plan['timing'], 'ptp'); self.assertFalse(plan['buffered'])

    def test_native_44_1_16_stereo_needs_no_conversion(self):
        plan = pcm_playback_plan(media(sample_rate=44100, bit_depth=16), endpoint(), settings={})
        self.assertFalse(plan['conversion_required'])


class AirPlayDiscoveryTests(unittest.TestCase):
    def test_raop_and_airplay_normalise_to_same_id(self):
        self.assertEqual(_normalise_airplay_id('38420B930724@Office._raop._tcp.local.', {}), '38:42:0B:93:07:24')
        self.assertEqual(_normalise_airplay_id('Office._airplay._tcp.local.', {'deviceid':'38:42:0b:93:07:24'}), '38:42:0B:93:07:24')


class AirPlayMetadataTests(unittest.TestCase):
    def test_duration_and_progress_are_sent(self):
        commands = _metadata_commands(media(), progress=42.9)
        self.assertIn('DURATION=298', commands); self.assertNotIn('PROGRESS=42', commands)
        self.assertIn('ITEMID=99', commands); self.assertEqual(commands[-1], 'ACTION=SENDMETA')

    def test_raop_txt_translation(self):
        arg = _txt_argument(endpoint(txt={'ft':'0x445F8A00,0x801C340','sf':'0x404','am':'Bookshelf','deviceid':'38:42:0B:93:07:24'}))
        self.assertIn('features=0x445F8A00,0x801C340', arg); self.assertIn('flags=0x404', arg); self.assertIn('model=Bookshelf', arg)


if __name__ == '__main__': unittest.main()
