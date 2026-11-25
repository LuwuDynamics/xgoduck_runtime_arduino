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
namespace = dict(vars(wire), threading=threading, time=time, RECOMMENDED_GAINS=(5, 10))
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
        self.fail_mid = False
        self.gain_readback = None
        self.id_protection = (wire.PROTECTION_ENABLED | 0x20, 308)
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
        if op == wire.SERVO_RESET_MID and self.fail_mid:
            ok = False
        if op in (wire.SERVO_SET_PROTECTION, wire.SERVO_SET_PROTECTION_CURRENT):
            ok = sid == wire.BROADCAST_ID
        if op == wire.SERVO_SET_ID and ok and not self.ignore_write:
            self.ids.remove(sid)
            self.ids.add(new)
        raw = 2048 if op == wire.SERVO_READ and ok else 0
        if op == wire.SERVO_INIT_ID_PROTECTION:
            self.assertEqual((new, kp, kd, flags, current), (0, 0, 0, 0, 0))
            flags, raw = self.id_protection
        if op == wire.SERVO_SET_PROTECTION_CURRENT:
            raw = current
        gain_values = (5, 10)
        if op == wire.SERVO_SET_PERM_KP_KD:
            self.assertEqual((kp, kd), (5, 10))
            gain_values = self.gain_readback or (kp, kd)
        reply = wire.SERVO_REPLY.pack(b'DQS3', op, sid, int(ok), flags, raw, 2047, *gain_values)
        self.controller.receive_servo_reply(reply)

    def test_all_valid_addresses_are_encoded_and_probed(self):
        found = [i for i in range(254) if self.controller.servo_op('ping', i)['ok']]
        self.assertEqual(found, [0, 42, 253])
        self.assertEqual(len(self.sent), 254)

    def test_any_source_and_destination_including_zero(self):
        for old, new in ((0, 123), (123, 1), (1, 0), (253, 252)):
            self.sent.clear()
            result = self.controller.servo_op('set_id', old, new_id=new, kp=99, kd=77)
            self.assertTrue(result['ok'])
            self.assertEqual(result['new_id'], new)
            self.assertIn(new, self.ids)
            self.assertNotIn(old, self.ids)
            self.assertEqual([(op, sid) for op, sid, _ in self.sent], [
                (wire.SERVO_PING, old), (wire.SERVO_PING, new), (wire.SERVO_SET_ID, old),
                (wire.SERVO_PING, new), (wire.SERVO_PING, old), (wire.SERVO_RESET_MID, new),
                (wire.SERVO_INIT_ID_PROTECTION, new), (wire.SERVO_SET_PERM_KP_KD, new)])
            self.assertEqual((result['gains']['kp'], result['gains']['kd']), (5, 10))
            self.assertEqual(result['protection']['current_raw'], 308)
            self.assertTrue(result['protection']['voltage_protection'] and result['protection']['overcurrent_protection'])

    def test_retry_after_mid_failure_uses_current_id_without_renumbering(self):
        self.fail_mid = True
        with self.assertRaisesRegex(ValueError, 'ID 100 is set'):
            self.controller.servo_op('set_id', 42, new_id=100)
        self.assertIn(100, self.ids)
        self.assertNotIn(wire.SERVO_INIT_ID_PROTECTION, [op for op, _, _ in self.sent])
        self.fail_mid = False
        self.sent.clear()
        result = self.controller.servo_op('set_id', 100, new_id=100)
        self.assertEqual((result['op'], result['new_id']), (wire.SERVO_SET_ID, 100))
        self.assertEqual([(op, sid) for op, sid, _ in self.sent], [
            (wire.SERVO_PING, 100), (wire.SERVO_RESET_MID, 100), (wire.SERVO_INIT_ID_PROTECTION, 100),
            (wire.SERVO_SET_PERM_KP_KD, 100)])

    def test_unconfirmed_protection_reports_the_effective_id(self):
        for flags, current in ((wire.PROTECTION_VOLTAGE, 308), (wire.PROTECTION_ENABLED, 231)):
            self.id_protection = flags, current
            source = 42 if 42 in self.ids else 100
            with self.assertRaisesRegex(ValueError, 'ID 100 is set.*Protection settings were not confirmed'):
                self.controller.servo_op('set_id', source, new_id=100)
            self.assertIn(100, self.ids)
            self.assertNotIn(wire.BROADCAST_ID, [sid for _, sid, _ in self.sent])

    def test_gain_failure_reports_new_id_after_successful_center_and_protection(self):
        self.gain_readback = (5, 20)
        with self.assertRaisesRegex(ValueError, 'ID 50 is set.*KP/KD were not confirmed'):
            self.controller.servo_op('set_id', 42, new_id=50)
        self.assertIn(50, self.ids)
        self.assertEqual(self.sent[-1][:2], (wire.SERVO_SET_PERM_KP_KD, 50))
        self.assertNotIn(wire.BROADCAST_ID, [sid for _, sid, _ in self.sent])

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
        self.assertNotIn(wire.SERVO_RESET_MID, [op for op, _, _ in self.sent])

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
