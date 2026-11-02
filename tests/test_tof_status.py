"""Optional sensor failures, stale data and viewers must not block motion/HTTP."""
import sys
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from tof_status import ToFMonitor, _decoder


reader_decode = _decoder()


def packet(seq=1, state=3):
    import struct
    return struct.pack('<4s8B6I64h64B64B', b'DTF1', 1, 8, 8, state,
                       0x29, 0xf0, 2, 0, seq, 100, 10, 2000, 0, 0,
                       *([350] * 64), *([5] * 64), *([1] * 64))


def wait_for(predicate, timeout=1.5):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('Timed out waiting for sensor worker')


class ToFStatusTest(unittest.TestCase):
    def monitor(self, factory, **kwargs):
        monitor = ToFMonitor(factory, **kwargs)
        self.addCleanup(monitor.close)
        return monitor

    def test_slow_rpc_does_not_block_any_http_reader_or_create_more_clients(self):
        release, entered = threading.Event(), threading.Event()
        client = Mock()
        def read(*args, **kwargs):
            entered.set()
            release.wait(1)
            return packet()
        client.call.side_effect = read
        factory = Mock(return_value=client)
        monitor = self.monitor(factory)
        monitor.status()
        self.assertTrue(entered.wait(1))
        started = time.monotonic()
        for _ in range(20):
            self.assertFalse(monitor.status()['fresh'])
        self.assertLess(time.monotonic() - started, .1)
        factory.assert_called_once()
        release.set()
        wait_for(lambda: monitor.status()['fresh'])
        client.call.assert_called_with('duck_tof', timeout=.5)
        client.provide.assert_not_called()
        client.notify.assert_not_called()

    def test_cached_frame_ages_even_when_rpc_is_stalled_and_copy_is_private(self):
        monitor = self.monitor(Mock())
        # Keep this test deterministic without starting a worker.
        monitor._stop.set()
        monitor._data = reader_decode(packet())
        monitor._received = time.monotonic() - 1.1
        data = monitor.status()
        self.assertFalse(data['fresh'])
        for key in ['center_mm', 'min_mm', 'max_mm']:
            self.assertIsNone(data[key])
        self.assertEqual(data['valid_zones'], 0)
        self.assertEqual(data['grid_mm'], [[None] * 8 for _ in range(8)])
        self.assertGreaterEqual(data['age_ms'], 1110)
        data['grid_mm'][0][0] = 9
        self.assertEqual(monitor._data['grid_mm'][0][0], 350)

    def test_missing_rpc_is_contained_and_recovers_without_restarting_app(self):
        bad, good = Mock(), Mock()
        bad.call.side_effect = RuntimeError('method duck_tof not available')
        good.call.return_value = packet(seq=2)
        monitor = self.monitor(Mock(side_effect=[bad, good]), retry=.1)
        monitor.status()
        wait_for(lambda: monitor.status()['state'] == 'unavailable')
        self.assertIn('duck_tof', monitor.status()['error_message'])
        bad.disconnect.assert_called_once()
        wait_for(lambda: monitor.status()['fresh'])
        self.assertEqual(monitor.status()['seq'], 2)

    def test_viewer_lease_releases_connection_and_resume_reopens_it(self):
        first, second = Mock(), Mock()
        for client in [first, second]:
            client.call.return_value = packet()
        factory = Mock(side_effect=[first, second])
        monitor = self.monitor(factory, interval=.01, lease=.07)
        monitor.status()
        wait_for(lambda: first.call.called)
        wait_for(lambda: first.disconnect.called)
        calls = first.call.call_count
        time.sleep(.08)
        self.assertEqual(first.call.call_count, calls)
        monitor.status()
        wait_for(lambda: second.call.called)
        self.assertEqual(factory.call_count, 2)

    def test_bad_packet_or_connection_failure_does_not_kill_the_worker(self):
        for failure in ['connect', 'packet']:
            with self.subTest(failure=failure):
                client = Mock()
                client.connect.return_value = failure != 'connect'
                client.call.return_value = b'invalid packet'
                monitor = self.monitor(Mock(return_value=client))
                monitor.status()
                wait_for(lambda: monitor.status()['state'] == 'unavailable')
                self.assertFalse(monitor.status()['fresh'])
                self.assertTrue(client.disconnect.called)
                self.assertTrue(monitor._thread.is_alive())

    def test_sensor_absence_is_distinct_from_missing_rpc(self):
        client = Mock()
        client.call.return_value = packet(seq=0, state=1)
        monitor = self.monitor(Mock(return_value=client))
        monitor.status()
        wait_for(lambda: monitor.status()['state'] == 'absent')
        self.assertFalse(monitor.status()['fresh'])
        self.assertEqual(monitor.status()['valid_zones'], 0)


if __name__ == '__main__':
    unittest.main()
