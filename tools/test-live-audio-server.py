"""Exercise HTTP capture ownership using a synthetic ALSA stdout process."""
import http.client
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent / 'device-test'
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('duck_bench', ROOT / 'server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
REAL_POPEN = subprocess.Popen


class LiveAudioTest(unittest.TestCase):
    def setUp(self):
        def fake_capture(cmd, **kwargs):
            self.assertEqual(cmd[0], 'arecord')
            self.assertIn('raw', cmd)
            self.assertNotIn('-d', cmd)
            return REAL_POPEN([sys.executable, '-u', '-c',
                'import os,time\nwhile True:\n os.write(1,b"\\x00\\x01"*480)\n time.sleep(.02)'], **kwargs)
        self.patches = [patch.object(module.doubao, 'load_config', return_value={}),
                        patch.object(module.Bench, 'audio_card', return_value='test'),
                        patch.object(module.Bench, 'read_volume', return_value=50),
                        patch.object(module.subprocess, 'Popen', side_effect=fake_capture)]
        for p in self.patches:
            p.start()
        self.bench = module.Bench()
        self.server = module.ThreadingHTTPServer(('127.0.0.1', 0), module.Handler)
        self.server.bench = self.bench
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connections = []

    def tearDown(self):
        for c in self.connections:
            c.close()
        self.bench.close()
        self.server.shutdown()
        self.server.server_close()
        for p in reversed(self.patches):
            p.stop()

    def request(self, method, path, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        self.connections.append(c)
        c.request(method, path, body='{}' if method == 'POST' else None, headers=headers or {})
        return c, c.getresponse()

    def idle(self):
        until = time.monotonic() + 3
        while self.bench.audio and time.monotonic() < until:
            time.sleep(.02)
        self.assertIsNone(self.bench.audio, 'capture process was not released')

    def test_stream_disconnect_busy_stop_and_restart(self):
        _, denied = self.request('GET', '/api/audio/stream')
        self.assertEqual(denied.status, 403)
        c, response = self.request('GET', '/api/audio/stream', {'X-Device-Test': '1'})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(1920), b'\x00\x01' * 960)
        self.assertEqual(self.bench.audio_kind, 'listening')
        self.assertFalse((self.bench.dir / 'recording.wav').exists())
        _, busy = self.request('GET', '/api/audio/stream', {'X-Device-Test': '1'})
        self.assertEqual(busy.status, 409)
        response.close()
        c.close()
        self.idle()
        _, again = self.request('GET', '/api/audio/stream', {'X-Device-Test': '1'})
        self.assertEqual(again.status, 200)
        self.assertEqual(again.read(960), b'\x00\x01' * 480)
        _, stopped = self.request('POST', '/api/audio/stop', {'X-Device-Test': '1'})
        self.assertEqual(stopped.status, 200)
        stopped.read()
        self.idle()
        again.read()  # EOF arrives when another audio control stops capture.


if __name__ == '__main__':
    unittest.main()
