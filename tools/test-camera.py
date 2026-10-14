"""Camera lifecycle checks using real subprocesses and simulated USB frames.

Run: python3 tools/test-camera.py (no camera or OpenCV required).
"""

import base64
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from camera import USBCamera

JPEG = b'\xff\xd8test-frame\xff\xd9'
PACKET = json.dumps(dict(jpeg=base64.b64encode(JPEG).decode(), device='/dev/video-test', width=640, height=480))


class CameraTest(unittest.TestCase):
    def setUp(self):
        self.camera = USBCamera()
        self.children = []
        self.code = f'import time\nwhile True:\n print({PACKET!r}, flush=True)\n time.sleep(.02)\n'
        popen = subprocess.Popen

        def fake_worker(_args, **kwargs):
            child = popen([sys.executable, '-u', '-c', self.code], **kwargs)
            self.children.append(child)
            return child

        self.mock = patch('camera.subprocess.Popen', side_effect=fake_worker)
        self.mock.start()

    def tearDown(self):
        self.camera.close()
        self.mock.stop()
        for child in self.children:
            if child.poll() is None:
                child.kill()
                child.wait()
                self.fail('Capture process was not released')

    def wait_state(self, state, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.camera.status()['state'] == state:
                return
            time.sleep(.01)
        self.fail(f'Expected {state}, got {self.camera.status()}')

    def test_starts_off_then_can_stop_and_restart(self):
        self.assertEqual(self.camera.status()['state'], 'off')
        self.assertIsNone(self.camera.frame())
        self.assertFalse(self.children)
        for _ in range(2):
            self.camera.start()
            self.wait_state('on')
            self.assertEqual(self.camera.frame(), JPEG)
            self.assertEqual(self.camera.status()['width'], 640)
            self.camera.stop()
            self.assertIsNone(self.camera.frame())
            self.wait_state('off')
            self.assertIsNotNone(self.children[-1].poll())

    def test_duplicate_start_shares_one_capture(self):
        self.camera.start()
        self.wait_state('on')
        for _ in range(10):
            self.camera.start()
        self.assertEqual(len(self.children), 1)

    def test_stop_while_driver_is_opening(self):
        self.code = 'import time; time.sleep(30)'
        self.camera.start()
        self.camera.stop()
        self.wait_state('off')
        self.assertEqual(self.camera.status()['error'], '')

    def test_no_viewer_releases_camera(self):
        self.camera.IDLE_TIMEOUT = .3
        self.camera.start()
        self.wait_state('on')
        self.wait_state('off')
        self.assertIsNotNone(self.children[-1].poll())

    def test_reads_renew_viewer_lease(self):
        self.camera.IDLE_TIMEOUT = .3
        self.camera.start()
        self.wait_state('on')
        for _ in range(8):
            self.assertIsNotNone(self.camera.frame())
            time.sleep(.06)
        self.assertEqual(self.camera.status()['state'], 'on')

    def test_missing_or_unplugged_camera_reports_error_and_can_retry(self):
        normal_code = self.code
        self.code = 'print(\'{"error": "USB camera disconnected"}\', flush=True)'
        self.camera.start()
        self.wait_state('error')
        self.assertIn('disconnected', self.camera.status()['error'])
        self.assertIsNone(self.camera.frame())
        self.code = normal_code
        self.camera.start()
        self.wait_state('on')
        self.assertEqual(self.camera.status()['error'], '')

    def test_hung_driver_is_terminated_after_start_timeout(self):
        self.code = 'import time; time.sleep(30)'
        self.camera.START_TIMEOUT = .25
        self.camera.start()
        self.wait_state('error')
        self.assertIn('timed out', self.camera.status()['error'])
        self.assertIsNotNone(self.children[-1].poll())

    def test_stale_frames_are_removed(self):
        self.code = f'import time; print({PACKET!r}, flush=True); time.sleep(30)'
        self.camera.FRAME_TIMEOUT = .25
        self.camera.start()
        self.wait_state('on')
        self.wait_state('error')
        self.assertIsNone(self.camera.frame())
        self.assertIn('stopped sending', self.camera.status()['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
