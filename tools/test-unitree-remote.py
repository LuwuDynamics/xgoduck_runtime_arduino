"""Unitree packet validation and link freshness; no Bluetooth or motors."""
from pathlib import Path
import struct
import sys
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent/'gamepad'))
from unitree_remote import decode_packet, UnitreeRemote, KEYS

class UnitreeTest(unittest.TestCase):
    def packet(self, lx=0,rx=0,ry=0,ly=0,keys=0):
        return struct.pack('<ffffHBB',lx,rx,ry,ly,keys,84,44)

    def test_real_neutral_packet(self):
        d=decode_packet(bytes.fromhex('000000800000008000000080000000000000542c'))
        self.assertEqual(d['axes'],dict(lx=0,rx=0,ry=0,ly=0))
        self.assertFalse(any(d['buttons'].values()))
        self.assertEqual(d['battery'],84)

    def test_axes_deadzone_and_evdev_sign(self):
        d=decode_packet(self.packet(lx=1,rx=-1,ly=1,ry=-1))
        self.assertEqual(d['axes'],dict(lx=1,rx=-1,ly=-1,ry=1))
        self.assertEqual(decode_packet(self.packet(lx=.1))['axes']['lx'],0)

    def test_every_button_is_independent(self):
        for bit, key in enumerate(KEYS):
            d=decode_packet(self.packet(keys=1<<bit))
            self.assertEqual([k for k,v in d['buttons'].items() if v],[key])

    def test_bad_packets_rejected(self):
        for packet in (b'',b'x'*19,b'x'*21,self.packet(lx=float('nan')),self.packet(ly=float('inf')),self.packet(rx=5)):
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                decode_packet(packet)

    def test_stale_link_is_not_connected(self):
        remote=UnitreeRemote()
        remote.state=dict(connected=True,stamp=time.monotonic()-1)
        self.assertFalse(remote.snapshot()['connected'])
        remote.state['stamp']=time.monotonic()
        self.assertTrue(remote.snapshot()['connected'])
        remote.disconnect()
        self.assertFalse(remote.snapshot()['connected'])

if __name__=='__main__':unittest.main()
