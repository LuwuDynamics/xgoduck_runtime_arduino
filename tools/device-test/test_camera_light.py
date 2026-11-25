import io
import subprocess
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from server import Bench


class CameraLightTest(unittest.TestCase):
    def setUp(self):
        self.bench = Bench.__new__(Bench)
        self.bench.lock = threading.RLock()
        self.bench.light = dict(supported=False, enabled=None, error='')
        self.bench.light_device = ''
        self.bench.light_checked = 0
        self.bench.light_requested = None
        self.bench.frame_ready = threading.Condition(self.bench.lock)
        self.bench.light_epoch = 0
        self.bench.closed = False
        self.bench.camera = Mock()
        self.bench.camera.poll.return_value = None
        self.bench.jpeg = b'frame'
        self.bench.last_frame = self.bench.last_view = time.monotonic()
        self.bench.camera_device = '/dev/video-test'
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

    def test_cold_light_on_waits_for_frame_and_off_never_starts_capture(self):
        bench = self.bench
        proc, bench.camera, bench.jpeg = bench.camera, None, None
        started = threading.Event()
        def start():
            bench.camera = proc
            started.set()
        def write(enabled):
            if enabled:
                self.assertIsNotNone(bench.jpeg)
            bench.light['enabled'] = enabled
        with patch.object(bench, 'camera_start', side_effect=start) as start_camera, \
                patch.object(bench, '_write_camera_light', side_effect=write) as lamp, \
                patch.object(bench, 'read_camera_light', return_value=False), ThreadPoolExecutor() as pool:
            self.assertEqual(bench.set_camera_light(False), {'enabled': False})
            start_camera.assert_not_called()
            pending = pool.submit(bench.set_camera_light, True)
            self.assertTrue(started.wait(1))
            with bench.frame_ready:
                self.assertFalse(pending.done())
                lamp.assert_called_once_with(False)
                bench.jpeg = b'frame'
                bench.last_frame = time.monotonic()
                bench.frame_ready.notify_all()
            self.assertEqual(pending.result(2), {'enabled': True})
            bench.last_view -= 21
            bench.check_camera_timeout()
            self.assertIs(bench.camera, proc)
            self.assertEqual(bench.set_camera_light(False), {'enabled': False})
            self.assertIs(bench.camera, proc)
            self.assertEqual(start_camera.call_count, 1)
            bench.last_view -= 21
            bench.check_camera_timeout()
            self.assertIsNone(bench.camera)

    def test_off_and_close_cancel_pending_light_on(self):
        for close in (False, True):
            with self.subTest(close=close):
                bench = self.bench
                bench.camera = Mock()
                bench.camera.poll.return_value = None
                bench.jpeg = None
                started = threading.Event()
                with patch.object(bench, 'camera_start', side_effect=started.set), \
                        patch.object(bench, 'read_camera_light', return_value=False), \
                        patch.object(bench, '_write_camera_light') as lamp, ThreadPoolExecutor() as pool:
                    pending = pool.submit(bench.set_camera_light, True)
                    self.assertTrue(started.wait(1))
                    if close:
                        self.assertEqual(bench.camera_stop(), {'running': False})
                        self.assertIsNone(bench.camera)
                    else:
                        bench.set_camera_light(False)
                        self.assertIsNotNone(bench.camera)
                    with self.assertRaisesRegex(ValueError, '取消'):
                        pending.result(2)
                    lamp.assert_called_once_with(False)
                    self.assertIs(bench.light_requested, False)

    def test_no_frame_reports_timeout_and_never_writes_on(self):
        self.bench.jpeg = None
        with patch.object(self.bench.frame_ready, 'wait_for', return_value=False) as wait, \
                patch.object(self.bench, 'read_camera_light', return_value=False), \
                patch.object(self.bench, '_write_camera_light') as lamp:
            with self.assertRaisesRegex(ValueError, '未出图'):
                self.bench.set_camera_light(True)
            self.assertEqual(wait.call_args.kwargs['timeout'], 5)
            lamp.assert_not_called()
            self.assertFalse(self.bench.light_requested)

    def test_frame_stall_stops_even_with_light_on_and_next_capture_restores_off(self):
        bench = self.bench
        bench.light_requested = True
        bench.last_frame -= 11
        with patch.object(bench, 'read_camera_light', return_value=True), \
                patch.object(bench, '_write_camera_light') as lamp:
            bench.check_camera_timeout()
            self.assertIsNone(bench.camera)
            self.assertFalse(bench.light_requested)
            self.assertIn('without a frame', bench.camera_error)
            lamp.assert_called_once_with(False)
            bench.camera = Mock()
            bench.camera.poll.return_value = 0
            bench.camera.stdout = io.BytesIO(bytes.fromhex('ffd8ffd9'))
            bench.frames, bench.frame_times = 0, []
            bench.read_camera(bench.camera, io.BytesIO())
            self.assertEqual(bench.frames, 1)
            self.assertEqual([c.args for c in lamp.call_args_list], [(False,), (False,)])

    def test_close_stops_capture_even_if_switch_off_fails(self):
        proc = self.bench.camera
        self.bench.light_requested = True
        with patch.object(self.bench, 'read_camera_light', return_value=True), \
                patch.object(self.bench, '_write_camera_light', side_effect=ValueError('写入失败')):
            with self.assertRaisesRegex(ValueError, '关灯未确认'):
                self.bench.camera_stop()
        self.assertIsNone(self.bench.camera)
        self.assertFalse(self.bench.light_requested)
        proc.send_signal.assert_called_once()


if __name__ == '__main__':
    unittest.main()
