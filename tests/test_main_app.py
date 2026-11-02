import contextlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class MainAppTest(unittest.TestCase):
    def launch(self, directory, mode):
        folder = Path(directory)
        (folder / 'voice_config.json').write_text(json.dumps({'enabled': False}))
        if mode == 'tof_read_disabled_avoidance':
            (folder / 'obstacle-avoidance.json').write_text(json.dumps({'enabled': False}))
        self.ports = [free_port() for _ in range(3)]
        env = dict(os.environ, DUCK_DATA_DIR=directory, DUCK_GAMEPAD_SOCKET=str(folder / 'gamepad.sock'),
            DUCK_MOTION_PORT=str(self.ports[0]), DUCK_VOICE_PORT=str(self.ports[1]), DUCK_MEDIA_PORT=str(self.ports[2]))
        self.report = folder / 'report.json'
        return subprocess.Popen([sys.executable, str(ROOT / 'tests/main_harness.py'), str(self.report), mode],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)

    def test_tof_read_works_with_avoidance_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            process = self.launch(directory, 'tof_read_disabled_avoidance')
            try:
                output, _ = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, output)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=3)

    def test_actual_main_real_policies_web_proxy_host_and_child_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            process = self.launch(directory, 'normal')
            try:
                output, _ = process.communicate(timeout=30)
                self.assertEqual(process.returncode, 0, output)
                result = json.loads(self.report.read_text())
                self.assertGreater(result['inferred'], 15)
                self.assertEqual(result['restarts'], 1)
                self.assertEqual(result['host_switches'], 0)
                self.assertEqual(result['host_checks'], 1)
                for port in self.ports:
                    with socket.socket() as s:
                        self.assertNotEqual(s.connect_ex(('127.0.0.1', port)), 0)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate(timeout=3)

    def test_voice_exits_if_motion_process_is_killed(self):
        with tempfile.TemporaryDirectory() as directory:
            process = self.launch(directory, 'orphan')
            voice_pid = None
            try:
                until = time.monotonic() + 15
                while not self.report.exists() and process.poll() is None and time.monotonic() < until:
                    time.sleep(.05)
                self.assertTrue(self.report.exists(), process.communicate(timeout=2)[0] if process.poll() is not None else 'Main startup timed out')
                voice_pid = json.loads(self.report.read_text())['voice_pid']
                os.kill(process.pid, signal.SIGKILL)
                # The child inherited stdout: EOF also proves it exited, not
                # only that its private TCP listener stopped accepting.
                output, _ = process.communicate(timeout=10)
                self.assertEqual(process.returncode, -signal.SIGKILL)
                for port in self.ports[1:]:
                    with socket.socket() as s:
                        self.assertNotEqual(s.connect_ex(('127.0.0.1', port)), 0)
                with contextlib.suppress(ChildProcessError):
                    os.waitpid(voice_pid, os.WNOHANG)
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                if voice_pid:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(voice_pid, signal.SIGKILL)
                process.communicate(timeout=3)


if __name__ == '__main__':
    unittest.main()
