import sys,time,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'python'))
import controller
from wire import VOLTAGE,decode_voltage

class VoltageTests(unittest.TestCase):
    def test_decode_and_invalid_values(self):
        packet=VOLTAGE.pack(b'DQV1',123,7,74,0,255,*([80]*12))
        stamp,values=decode_voltage(packet)
        self.assertEqual(stamp,123)
        self.assertEqual(values,[7.4]+[None]*14)
        for bad in (packet[:-1], b'BAD!'+packet[4:],VOLTAGE.pack(b'DQV1',1,0x8000,*([74]*15))):
            with self.assertRaises(ValueError):decode_voltage(bad)

    def test_status_stale_missing_and_duplicate(self):
        self.assertEqual(Path(controller.__file__).resolve(), Path(__file__).resolve().parents[1]/'python/controller.py')
        c=controller.Controller(None)
        self.assertFalse(c.status()['supply_voltage']['available'])
        now=time.monotonic()
        with patch('controller.time.monotonic',return_value=now):
            c.rx_time=now
            c.receive_voltage(VOLTAGE.pack(b'DQV1',123,7,74,76,75,*([0]*12)))
            s=c.status()['supply_voltage']
            self.assertEqual((s['volts'],s['min_volts'],s['max_volts'],s['valid_count']),(7.5,7.4,7.6,3))
        with patch('controller.time.monotonic',return_value=now+1.1):
            c.rx_time=now+1.1
            c.receive_voltage(VOLTAGE.pack(b'DQV1',123,7,74,76,75,*([0]*12)))
            self.assertFalse(c.status()['supply_voltage']['available'])
            self.assertEqual(c.status()['supply_voltage']['per_servo'],[None]*15)
        c.receive_voltage(VOLTAGE.pack(b'DQV1',124,0,*([75]*15)))
        self.assertFalse(c.status()['supply_voltage']['available'])

if __name__=='__main__':unittest.main()
