import http.client
import json
import os
from pathlib import Path
import signal
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from local_ipc import JsonServer, request
from voice_supervisor import VoiceSupervisor


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def eventually(check, timeout=10):
    until = time.monotonic() + timeout
    last = None
    while time.monotonic() < until:
        try:
            last = check()
            if last:
                return last
        except (OSError, ValueError):
            pass
        time.sleep(.05)
    raise AssertionError('Condition did not become true: ' + repr(last))


class LocalIPCTest(unittest.TestCase):
    def setUp(self):
        self.seen = []
        def dispatch(method, path, body):
            self.seen.append((method, path, body))
            return {'ok': True}
        self.server = JsonServer(0, 'secret-token', dispatch)
        self.server.start()
        self.addCleanup(self.server.close)
        self.port = self.server.server.server_port

    def test_authentication_body_bounds_and_loopback(self):
        self.assertEqual(self.server.server.server_address[0], '127.0.0.1')
        with self.assertRaisesRegex(ValueError, 'authentication'):
            request(self.port, 'wrong-token', '/action', {})
        with self.assertRaisesRegex(ValueError, 'too large'):
            request(self.port, 'secret-token', '/action', {'data': 'x' * 16384})
        with self.assertRaisesRegex(ValueError, 'JSON object'):
            request(self.port, 'secret-token', '/action', [])
        self.assertFalse(self.seen)
        self.assertEqual(request(self.port, 'secret-token', '/state'), {'ok': True})
        self.assertEqual(request(self.port, 'secret-token', '/action', {'kind': 'stop'}), {'ok': True})
        self.assertEqual(self.seen, [('GET', '/state', {}), ('POST', '/action', {'kind': 'stop'})])

    def test_disconnected_caller_does_not_retry_dispatch_or_break_server(self):
        entered, release, finished = (threading.Event() for _ in range(3))
        calls, errors = [], []

        def dispatch(method, path, body):
            calls.append(path)
            if path == '/slow':
                entered.set()
                if not release.wait(3):
                    raise AssertionError('Test did not release the delayed reply')
            return {'ok': True}

        server = JsonServer(0, 'test-token', dispatch)
        original = server.server.finish_request

        def finish_request(*args):
            try:
                return original(*args)
            finally:
                finished.set()

        server.server.finish_request = finish_request
        server.server.handle_error = lambda *_: errors.append('unhandled request error')
        server.start()
        self.addCleanup(server.close)
        self.addCleanup(release.set)
        port = server.server.server_port
        with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
            client.sendall(b'POST /slow HTTP/1.0\r\nAuthorization: Bearer test-token\r\nContent-Length: 2\r\n\r\n{}')
            self.assertTrue(entered.wait(2))
            # Reset instead of FIN so the delayed write deterministically fails.
            client.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
        release.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual(errors, [])
        self.assertEqual(calls, ['/slow'])
        self.assertEqual(request(port, 'test-token', '/state'), {'ok': True})


class VoiceProcessTest(unittest.TestCase):
    def launch(self, enabled=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        folder = Path(temp.name)
        (folder / 'voice_config.json').write_text(json.dumps({'enabled': enabled, 'ota_url': 'https://127.0.0.1:9/ota/'}))
        self.heartbeats, self.exits, self.preparations = 0, 0, 0
        def motion(method, path, body):
            if path == '/heartbeat':
                self.heartbeats += 1
                return {'ok': True}
            if path == '/cancel':
                return {'ok': True}
            raise ValueError('unsupported test endpoint')
        token = 'integration-only-token'
        ipc = JsonServer(0, token, motion)
        ipc.start()
        self.addCleanup(ipc.close)
        self.media_port, self.voice_port = free_port(), free_port()
        def exited():
            self.exits += 1
        def prepare():
            self.preparations += 1
        self.supervisor = VoiceSupervisor(token, motion_port=ipc.server.server_port, voice_port=self.voice_port,
            on_exit=exited, prepare=prepare,
            env={'DUCK_DATA_DIR': str(folder), 'DUCK_MEDIA_PORT': str(self.media_port)})
        self.addCleanup(self.supervisor.close)
        self.supervisor.start()
        status = eventually(lambda: (s if (s := self.supervisor.status()).get('state') in ('disabled', 'checking_binding') else None))
        return status

    def test_real_child_media_api_heartbeat_restart_and_shutdown(self):
        before = self.launch()
        self.assertEqual(before['state'], 'disabled')
        self.assertNotEqual(before['pid'], os.getpid())
        eventually(lambda: self.heartbeats > 0)
        connection = http.client.HTTPConnection('127.0.0.1', self.media_port, timeout=2)
        connection.request('GET', '/api/status')
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        media = json.load(response)
        self.assertEqual(set(media), {'camera', 'audio', 'tts'})
        connection.close()
        os.kill(before['pid'], signal.SIGKILL)
        after = eventually(lambda: (s if (s := self.supervisor.status()).get('state') == 'disabled'
                                  and s.get('pid') != before['pid'] else None))
        self.assertGreater(after['restarts'], 0)
        self.assertEqual(self.exits, 1)
        self.assertEqual(self.preparations, 1, 'USB Host must not be reapplied on a chat restart')
        self.supervisor.close()
        self.assertFalse(self.supervisor.status()['running'])
        for port in (self.media_port, self.voice_port):
            with socket.socket() as s:
                s.settimeout(.5)
                self.assertNotEqual(s.connect_ex(('127.0.0.1', port)), 0)

    def test_default_enabled_headless_setup_runs_without_usb_and_keeps_media(self):
        if Path('/proc/asound').exists():
            self.skipTest('Missing-device case is specifically tested on the Mac developer host.')
        status = self.launch(enabled=True)
        self.assertEqual(status['state'], 'checking_binding')
        self.assertTrue(status['onboarding']['hardware_error'])
        self.assertFalse(status['audio_ready'])
        eventually(lambda: self.heartbeats > 0)


if __name__ == '__main__':
    unittest.main()
