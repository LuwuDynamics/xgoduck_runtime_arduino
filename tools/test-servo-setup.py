"""Host tests for the actual controller/wire code, with a fake MCU transport.

Run: python3 tools/test-servo-setup.py
No ONNX models, Arduino libraries or connected servos are required.
"""
import ast
from pathlib import Path
import sys
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import wire

# Load the real class without importing or constructing the inference runtime.
source = ast.parse((ROOT / 'python/controller.py').read_text())
class_node = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'Controller')
namespace = dict(vars(wire), threading=threading, time=time)
exec(compile(ast.Module(body=[class_node], type_ignores=[]), 'controller.py', 'exec'), namespace)
Controller = namespace['Controller']


class ServoSetupTest(unittest.TestCase):
    def setUp(self):
        self.controller = c = Controller.__new__(Controller)
        c.lock = threading.RLock()
        c.servo_op_lock = threading.Lock()
        c.servo_reply_event = threading.Event()
        c.servo_expected_reply = c.servo_reply = None
        c.mode = 'servo_debug'
        c.bridge = self
        self.ids = {0, 42, 253}
        self.sent = []
        self.ignore_write = False
        self.no_reply = False
        self.inject_unrelated = False

    def notify(self, method, payload):
        self.assertEqual(method, 'duck_servo')
        magic, op, sid, new, kp, kd, flags, current = wire.SERVO.unpack(payload)
        self.assertEqual(magic, b'DQV2')
        self.sent.append((op, sid, new))
        if self.no_reply:
            return
        if self.inject_unrelated:
            self.controller.receive_servo_reply(wire.SERVO_REPLY.pack(b'DQS3', 0, 10, 1, 0, 0, 2047, 0, 0))
            self.assertFalse(self.controller.servo_reply_event.is_set())
            self.controller.receive_servo_reply(wire.SERVO_REPLY.pack(b'DQS3', op, (sid + 1) % 254, 1, 0, 0, 2047, 0, 0))
            self.assertFalse(self.controller.servo_reply_event.is_set())
        ok = sid in self.ids
        if op in (wire.SERVO_SET_PROTECTION, wire.SERVO_SET_PROTECTION_CURRENT):
            ok = sid == wire.BROADCAST_ID
        if op == wire.SERVO_SET_ID and ok and not self.ignore_write:
            self.ids.remove(sid)
            self.ids.add(new)
        raw = 2048 if op == wire.SERVO_READ and ok else 0
        if op == wire.SERVO_SET_PROTECTION_CURRENT:
            raw = current
        reply = wire.SERVO_REPLY.pack(b'DQS3', op, sid, int(ok), flags, raw, 2047, 5, 10)
        self.controller.receive_servo_reply(reply)

    def test_all_valid_addresses_are_encoded_and_probed(self):
        found = [i for i in range(254) if self.controller.servo_op('ping', i)['ok']]
        self.assertEqual(found, [0, 42, 253])
        self.assertEqual(len(self.sent), 254)

    def test_any_source_and_destination_including_zero(self):
        for old, new in ((0, 123), (123, 1), (1, 0), (253, 252)):
            result = self.controller.servo_op('set_id', old, new_id=new)
            self.assertTrue(result['ok'])
            self.assertEqual(result['new_id'], new)
            self.assertIn(new, self.ids)
            self.assertNotIn(old, self.ids)

    def test_reserved_ids_are_rejected_before_transport(self):
        for sid in (-1, 254, 255, 256):
            with self.assertRaises(ValueError):
                self.controller.servo_op('ping', sid)
            with self.assertRaises(ValueError):
                self.controller.servo_op('set_id', 42, new_id=sid)
        self.assertEqual(self.sent, [])

    def test_collision_and_missing_source_do_not_write(self):
        for old, new, error in ((42, 253, 'already in use'), (99, 50, 'No servo replied')):
            with self.assertRaisesRegex(ValueError, error):
                self.controller.servo_op('set_id', old, new_id=new)
        self.assertTrue(all(op == wire.SERVO_PING for op, _, _ in self.sent))

    def test_no_false_success_after_write(self):
        self.ignore_write = True
        with self.assertRaisesRegex(ValueError, 'did not reply'):
            self.controller.servo_op('set_id', 42, new_id=100)

    def test_read_nonpreset_id_requires_reply(self):
        self.assertEqual(self.controller.servo_op('read', 42)['raw_pos'], 2048)
        with self.assertRaisesRegex(ValueError, 'operation failed'):
            self.controller.servo_op('read', 99)

    def test_late_enter_and_wrong_id_cannot_complete_ping(self):
        self.inject_unrelated = True
        self.assertTrue(self.controller.servo_op('ping', 42)['ok'])
        self.assertIsNone(self.controller.servo_expected_reply)

    def test_bridge_timeout_is_error_not_offline(self):
        self.no_reply = True
        with self.assertRaisesRegex(ValueError, 'timeout'):
            self.controller.servo_op('ping', 42)
        self.assertIsNone(self.controller.servo_expected_reply)

    def test_commands_require_setup(self):
        self.controller.mode = 'shadow'
        with self.assertRaisesRegex(ValueError, 'not in servo_debug'):
            self.controller.servo_op('set_id', 42, new_id=100)
        self.assertEqual(self.sent, [])

    def test_read_returns_parameters_and_reset_mid_uses_matching_protocol(self):
        read = self.controller.servo_op('read', 42)
        self.assertEqual((read['kp'], read['kd'], read['center_pos']), (5, 10, 2047))
        self.assertEqual(self.controller.servo_op('reset_mid', 42)['op'], wire.SERVO_RESET_MID)

    def test_protection_switch_and_current_are_separate_broadcast_operations(self):
        reply = self.controller.servo_op('set_protection', protection_enabled=False)
        self.assertFalse(reply['protection_enabled'])
        self.assertEqual(reply['target_id'], 254)
        reply = self.controller.servo_op('set_protection_current', current_a=1.5)
        self.assertAlmostEqual(reply['current_a'], 1.5, delta=.0065)
        self.assertEqual(reply['op'], wire.SERVO_SET_PROTECTION_CURRENT)
        self.assertTrue(reply['broadcast'])
        with self.assertRaisesRegex(ValueError, 'all servos'):
            self.controller.servo_op('set_protection', 42, protection_enabled=True)


if __name__ == '__main__':
    unittest.main()
