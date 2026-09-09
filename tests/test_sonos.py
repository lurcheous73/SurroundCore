import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'core'))
from app.sonos import parse_topology

SAMPLE = '''<ZoneGroupState><ZoneGroups>
<ZoneGroup Coordinator="RINCON_A" ID="group-1">
<ZoneGroupMember UUID="RINCON_A" ZoneName="Office"
 Location="http://192.0.2.10:1400/xml/device_description.xml"
 ChannelMapSet="RINCON_A:LF,LF;RINCON_B:RF,RF" />
<ZoneGroupMember UUID="RINCON_B" ZoneName="Office" Invisible="1"
 Location="http://192.0.2.11:1400/xml/device_description.xml"
 ChannelMapSet="RINCON_A:LF,LF;RINCON_B:RF,RF" />
</ZoneGroup></ZoneGroups></ZoneGroupState>'''

class SonosTopologyTests(unittest.TestCase):
    def test_bonded_stereo_pair_is_one_zone(self):
        zones = parse_topology(ET.fromstring(SAMPLE))
        self.assertEqual(len(zones), 1)
        self.assertEqual(zones[0]['name'], 'Office')
        self.assertEqual(zones[0]['channels'], 2)
        self.assertEqual(zones[0]['channel_map'], ['LF', 'RF'])

if __name__ == '__main__':
    unittest.main()
