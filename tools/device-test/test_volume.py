import subprocess
import threading
import unittest
from unittest.mock import patch
from server import Bench


class VolumeTest(unittest.TestCase):
    def setUp(self):
        self.bench = Bench.__new__(Bench)
        self.bench.lock = threading.RLock()
        self.bench.volume = None
        self.bench.volume_card = None
        self.bench.volume_checked = 0
        self.bench.volume_error = ''

    @patch.object(Bench, 'audio_card', return_value='1')
    @patch('server.subprocess.run')
    def test_applies_and_reports_hardware_rounding(self, run, card):
        run.return_value.stdout = 'Front Left: Playback 23 [77%] [-7.00dB] [on]'
        self.bench.set_volume(75)
        self.assertEqual(self.bench.volume, 77)
        self.assertEqual(run.call_args.args[0], ['amixer', '-c', '1', 'sset', 'Speaker', '75%', 'unmute'])
        self.assertEqual(self.bench.read_volume('1'), 77)
        self.assertEqual(run.call_count, 1)

    @patch('server.subprocess.run')
    def test_invalid_values_never_touch_hardware(self, run):
        for value in (-1, 101, None, True, '70', 1.5):
            with self.assertRaises(ValueError):
                self.bench.set_volume(value)
        run.assert_not_called()

    @patch('server.subprocess.run', side_effect=subprocess.CalledProcessError(1, 'amixer'))
    def test_unreadable_volume_is_not_reported_as_success(self, run):
        self.assertIsNone(self.bench.read_volume('1'))
        self.assertTrue(self.bench.volume_error)


if __name__ == '__main__':
    unittest.main()
