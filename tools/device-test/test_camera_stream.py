"""Verify zero-backlog preview frame pull, burst skipping, compression, and lock-free probing."""
import http.client
import io
import random
import threading
import time
import unittest
from unittest.mock import Mock, patch

import server
from server import Bench, Handler, ThreadingHTTPServer


def make_large_jpeg():
    from PIL import Image

    rng = random.Random(42)
    raw = bytes(rng.getrandbits(8) for _ in range(640 * 480 * 3))
    image = Image.frombytes('RGB', (640, 480), raw)
    buf = io.BytesIO()
    image.save(buf, format='JPEG', quality=98, subsampling=0)
    return buf.getvalue()


class CameraStreamPerfTest(unittest.TestCase):
    def setUp(self):
        self.bench = Bench.__new__(Bench)
        self.bench.lock = threading.RLock()
        self.bench.frame_ready = threading.Condition(self.bench.lock)
        self.bench.frame_times = []
        self.bench.camera = Mock()
        self.bench.camera.poll.return_value = None
        self.bench.jpeg = None
        self.bench._preview_seq = 0
        self.bench._preview_jpeg = None
        self.bench._stream_semaphore = threading.Semaphore(server.MAX_CAMERA_STREAMS)
        self.bench.frames = 0
        self.bench.last_frame = self.bench.last_view = time.monotonic()
        self.bench.camera_error = ''
        self.bench.camera_device = '/dev/video-test'
        self.bench.light = dict(supported=False, enabled=None, error='')
        self.bench.light_device = ''
        self.bench.light_checked = 0
        self.bench.light_requested = None
        self.bench.light_epoch = 0
        self.bench.closed = False

    def test_read_camera_publishes_only_latest_frame_in_burst(self):
        f1 = b'\xff\xd8frame-1\xff\xd9'
        f2 = b'\xff\xd8frame-2\xff\xd9'
        f3 = b'\xff\xd8frame-3\xff\xd9'
        self.bench.camera.stdout = io.BytesIO(f1 + f2 + f3 + b'\xff\xd8partial')
        notifications = 0
        real_notify = self.bench.frame_ready.notify_all

        def count_notify():
            nonlocal notifications
            notifications += 1
            real_notify()

        with patch.object(self.bench.frame_ready, 'notify_all', side_effect=count_notify):
            self.bench.read_camera(self.bench.camera, io.BytesIO())
        # One notify for the burst (skipping f1/f2), one notify in finally when stdout hits EOF.
        self.assertEqual(notifications, 2)
        self.assertEqual(self.bench.frames, 3)

    def test_camera_light_status_never_blocks_frame_lock_and_uses_longer_running_cache(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_read(_device):
            entered.set()
            self.assertTrue(release.wait(2))
            return False

        with patch.object(Bench, 'camera_path', return_value='/dev/video-test'), \
                patch.object(Bench, 'camera_usb_id', return_value=('0c45', '636b')), \
                patch.object(self.bench, 'read_camera_light', side_effect=slow_read) as probe:
            result = {}
            worker = threading.Thread(target=lambda: result.setdefault('v', self.bench.camera_light_status()))
            worker.start()
            self.assertTrue(entered.wait(2))
            # While v4l2-ctl --get-ctrl=gain is running, Bench.lock must stay free for frames.
            acquired = self.bench.lock.acquire(timeout=0.2)
            self.assertTrue(acquired, 'camera_light_status must not hold Bench.lock across v4l2-ctl')
            self.bench.jpeg = b'\xff\xd8ok\xff\xd9'
            self.bench.frames = 1
            self.bench.lock.release()
            jpeg, seq, ready = self.bench.next_frame(after=0, timeout=0.2, preview=False)
            self.assertTrue(ready)
            self.assertEqual((jpeg, seq), (b'\xff\xd8ok\xff\xd9', 1))
            release.set()
            worker.join(2)
            self.assertEqual(result['v']['enabled'], False)
            # While streaming with confirmed status, cache lasts longer than 5s (up to 60s).
            self.bench.light_checked = time.monotonic() - 10
            self.assertEqual(self.bench.camera_light_status()['enabled'], False)
            self.assertEqual(probe.call_count, 1)

    @unittest.skipIf(server.Image is None, 'Pillow not installed')
    def test_preview_reencodes_oversized_jpeg_while_snapshot_stays_original(self):
        raw = make_large_jpeg()
        self.assertGreater(len(raw), server.CAMERA_PREVIEW_REENCODE_MIN_BYTES)
        self.bench.jpeg = raw
        self.bench.frames = 7
        preview_1, seq_1, ok_1 = self.bench.next_frame(after=0, timeout=0.2, preview=True)
        preview_2, seq_2, ok_2 = self.bench.next_frame(after=0, timeout=0.2, preview=True)
        snapshot, seq_s, ok_s = self.bench.next_frame(after=None, timeout=0.2, preview=False)
        self.assertTrue(ok_1 and ok_2 and ok_s)
        self.assertEqual((seq_1, seq_2, seq_s), (7, 7, 7))
        self.assertIs(preview_1, preview_2)
        self.assertLess(len(preview_1), len(raw) * 0.5)
        self.assertEqual(snapshot, raw)

    def test_http11_keepalive_pull_skips_frames_and_returns_204_on_idle(self):
        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        httpd.daemon_threads = True
        httpd.bench = self.bench
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (httpd.shutdown(), httpd.server_close(), thread.join(2)))

        self.bench.jpeg = b'\xff\xd8frame-1\xff\xd9'
        self.bench.frames = 1

        conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
        self.addCleanup(conn.close)

        # 1. Initial pull on persistent HTTP/1.1 connection.
        conn.request('GET', '/api/camera/frame?preview=1&after=0')
        res = conn.getresponse()
        body = res.read()
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader('X-Frame-Seq'), '1')
        self.assertEqual(body, b'\xff\xd8frame-1\xff\xd9')
        sock = conn.sock

        # 2. No new frame within wait window -> 204 No Content, keep-alive preserved.
        conn.request('GET', '/api/camera/frame?preview=1&after=1&wait=80')
        res = conn.getresponse()
        self.assertEqual(res.status, 204)
        self.assertEqual(res.read(), b'')
        self.assertIs(conn.sock, sock)

        # 3. Multiple frames arrive while client is busy -> next pull jumps straight to frame 4.
        with self.bench.frame_ready:
            self.bench.jpeg = b'\xff\xd8frame-4\xff\xd9'
            self.bench.frames = 4
            self.bench.frame_ready.notify_all()
        conn.request('GET', '/api/camera/frame?preview=1&after=1')
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader('X-Frame-Seq'), '4')
        self.assertEqual(res.read(), b'\xff\xd8frame-4\xff\xd9')
        self.assertIs(conn.sock, sock)

        # 4. Snapshot without preview/after returns raw frame with sequence header.
        conn.request('GET', '/api/camera/frame')
        res = conn.getresponse()
        self.assertEqual(res.status, 200)
        self.assertEqual(res.getheader('X-Frame-Seq'), '4')
        self.assertEqual(res.read(), b'\xff\xd8frame-4\xff\xd9')

        # 5. MJPEG stream still works with X-Accel-Buffering: no and Connection: close.
        stream_conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=3)
        self.addCleanup(stream_conn.close)
        stream_conn.request('GET', '/api/camera/stream')
        sres = stream_conn.getresponse()
        self.assertEqual(sres.status, 200)
        self.assertEqual(sres.getheader('X-Accel-Buffering'), 'no')
        self.assertEqual(sres.getheader('Connection'), 'close')
        chunk = sres.read(64)
        self.assertIn(b'--duckframe', chunk)


if __name__ == '__main__':
    unittest.main()
