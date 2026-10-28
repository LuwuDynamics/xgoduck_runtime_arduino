"""Real authenticated nonblocking Unix datagrams, without physical hardware."""
import json
import errno
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from mouth_ipc import MouthReceiver, MouthSender
import test_voice_motion as motion_fixture


class MouthIPCTest(unittest.TestCase):
    def wait(self, check):
        until = time.monotonic() + 2
        while time.monotonic() < until:
            if check(): return
            time.sleep(.01)
        self.fail('Mouth datagram was not processed')

    def test_actual_datagram_schedules_and_closes_only_the_mouth(self):
        f = motion_fixture.VoiceMotionTest(); f.setUp()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mouth.sock'
            server = MouthReceiver(path, 'fixture', f.access.speech)
            sender = MouthSender(path, 'fixture')
            server.start()
            try:
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                body = dict(session=101, utterance=1, sequence=1, points=[[f.now+.1, 8.]])
                self.assertEqual(sender.send(body), {'submitted':True})
                self.wait(lambda: server.accepted == 1)
                f.advance(.15)
                self.assertEqual(f.c.mouth, 8.)
                self.assertIsNone(f.access.action)
                self.assertEqual(f.c.input_source, 'web')
                sender.send(dict(body, sequence=2, points=[]))
                self.wait(lambda: server.accepted == 2)
                self.assertEqual(f.c.mouth, 0.)
            finally:
                sender.close(); server.close()
            self.assertFalse(path.exists())
            self.assertFalse(server.thread.is_alive())

    def test_invalid_token_oversized_and_invalid_json_never_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mouth.sock'
            dispatch = Mock()
            server = MouthReceiver(path, 'fixture', dispatch)
            sender = MouthSender(path, 'wrong')
            server.start()
            try:
                sender.send({'points':[]})
                sender.socket.sendto(b'{', str(path))
                expected = 4
                try:
                    sender.socket.sendto(b'x' * 4097, str(path))
                except OSError as exc:
                    if exc.errno != errno.EMSGSIZE: raise
                    expected = 3 # macOS rejects this before the receiver sees it.
                sender.socket.sendto(json.dumps({'_token':1}).encode(), str(path))
                self.wait(lambda: server.rejected == expected)
                dispatch.assert_not_called()
                self.assertFalse(sender.socket.getblocking())
            finally:
                sender.close(); server.close()

    def test_regular_files_are_not_removed_and_missing_receiver_is_nonblocking(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mouth.sock'
            path.write_text('keep')
            server = MouthReceiver(path, 'fixture', Mock())
            with self.assertRaisesRegex(ValueError, 'regular file'):
                server.start()
            self.assertEqual(path.read_text(), 'keep')
            sender = MouthSender(path, 'fixture')
            try:
                with self.assertRaises(OSError): sender.send({'points':[]})
            finally:
                sender.close()


if __name__ == '__main__':
    unittest.main()
