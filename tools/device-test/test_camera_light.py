import subprocess
import threading
import unittest
from unittest.mock import patch

from server import Bench


class CameraLightTest(unittest.TestCase):
    def setUp(self):
        self.bench = Bench.__new__(Bench)
        self.bench.lock = threading.RLock()
        self.bench.light = dict(supported=False, enabled=None, error='')
        self.bench.light_device = ''
        self.bench.light_checked = 0
        self.bench.light_requested = None
        self.path = patch.object(Bench, 'camera_path', return_value='/dev/video-test')
        self.usb = patch.object(Bench, 'camera_usb_id', return_value=('0c45', '636b'))
        self.path.start()
        self.usb.start()
        self.addCleanup(self.path.stop)
        self.addCleanup(self.usb.stop)

    @patch('server.subprocess.run')
    def test_status_reads_without_switching_lamp_and_caches_result(self, run):
        run.return_value.stdout = 'gain: 0\n'
        self.assertIs(self.bench.camera_light_status()['enabled'], False)
        self.assertIs(self.bench.camera_light_status()['enabled'], False)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-1], '--get-ctrl=gain')
        self.assertIsNone(self.bench.light_requested)

    @patch('server.subprocess.run')
    def test_invalid_requests_never_touch_camera(self, run):
        for value in (None, 0, 1, 'true', {}, []):
            with self.assertRaises(ValueError):
                self.bench.set_camera_light(value)
        run.assert_not_called()

    @patch('server.subprocess.run')
    def test_other_cameras_gain_is_never_treated_as_a_lamp(self, run):
        with patch.object(Bench, 'camera_usb_id', return_value=('1234', '5678')):
            self.assertFalse(self.bench.camera_light_status()['supported'])
            with self.assertRaises(ValueError):
                self.bench.set_camera_light(True)
        run.assert_not_called()

    @patch('server.subprocess.run')
    def test_failed_readback_cannot_report_switch_success(self, run):
        run.return_value.stdout = 'gain: 0\n'
        with self.assertRaisesRegex(ValueError, '没有生效'):
            self.bench.set_camera_light(True)
        self.assertFalse(self.bench.light['enabled'])
        self.assertTrue(self.bench.light['error'])
        self.assertIsNone(self.bench.light_requested)

    @patch('server.subprocess.run')
    def test_control_failure_does_not_leave_a_stale_on_state(self, run):
        run.return_value.stdout = 'gain: 1\n'
        self.bench.camera_light_status()
        run.side_effect = subprocess.TimeoutExpired('v4l2-ctl', 3)
        with self.assertRaises(ValueError):
            self.bench.set_camera_light(False)
        self.assertIsNone(self.bench.light['enabled'])
        self.assertTrue(self.bench.light['error'])


if __name__ == '__main__':
    unittest.main()
