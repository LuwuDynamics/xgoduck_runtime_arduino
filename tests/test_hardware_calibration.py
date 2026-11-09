"""Real stable controller/protocol, simulated MCU replies, no physical writes."""
import json
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import wire
from motion_calibration import AppController
from motion_access import MotionAccess
from rl_core import DEFAULT_DEG, MOTOR_IDS


class HardwareCalibrationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        self.legacy = self.data / 'zero_pos.json'
        self.legacy.write_text(json.dumps({'ids': MOTOR_IDS, 'zero_pos': [1800 + i * 7 for i in range(15)]}))
        self.original = self.legacy.read_bytes()
        self.reply_ok = True

    def controller(self):
        bridge = Mock()
        c = AppController(bridge, data_dir=self.data)
        def notify(method, payload):
            if method != 'duck_cal':
                return
            _, seq, op, index = wire.CAL.unpack(payload)
            if op == wire.CAL_CENTER_ONE:
                # An old reply for the same joint cannot acknowledge this call.
                c.receive_cal_reply(wire.CAL_REPLY.pack(b'DQA1', seq - 1, index, 1))
                self.assertFalse(c.cal_reply_event.is_set())
                c.receive_cal_reply(wire.CAL_REPLY.pack(b'DQA1', seq, index, int(self.reply_ok)))
        bridge.notify.side_effect = notify
        self.feedback(c)
        return c

    def feedback(self, c):
        seq = c.received + 1
        raw = wire.STATE.pack(b'DQS1', 1, 1, wire.ALL_MASK,
            seq, seq * 10000, seq * 10000, c.seq, 0, 10000, 500, 0, 0, 0,
            0, 0, -16384, 0, 0, 0, *DEFAULT_DEG, *([0.] * 15), *DEFAULT_DEG)
        c.receive(raw)

    def test_nondefault_legacy_calibration_blocks_web_and_voice_arm_without_applying_offsets(self):
        c = self.controller()
        self.assertTrue(c.status()['calibration']['requires_centering'])
        for mode in ('hold', 'policy'):
            with self.assertRaisesRegex(ValueError, 'Set center & hold'):
                c.set_mode(mode)
        for action in ('sit', 'stand'):
            with self.assertRaisesRegex(ValueError, 'Set center & hold'):
                c.set_sitstand(action)
        self.assertEqual(c.mode, 'shadow')
        access = MotionAccess(c)
        access.heartbeat(101)
        with self.assertRaisesRegex(ValueError, '旧版软件零位'):
            access.start_action(kind='stand', session=101)
        c.receive_raw(wire.RAW.pack(b'DQR2', wire.ALL_MASK, 0, *([2047] * 15)))
        self.assertFalse(any(call.args[0] == 'duck_cal' for call in c.bridge.notify.call_args_list))
        self.assertEqual(self.legacy.read_bytes(), self.original)
        self.assertEqual(c.mode, 'shadow')

    def test_all_joint_acknowledgements_complete_migration_and_survive_restart(self):
        c = self.controller()
        c.set_mode('calibrate')
        for i, sid in enumerate(MOTOR_IDS):
            self.feedback(c)
            self.assertEqual(c.cal_center(i)['id'], sid)
            self.assertEqual(c.status()['calibration']['requires_centering'], i != 14)
        self.assertEqual(self.legacy.read_bytes(), self.original)
        self.assertEqual(stat.S_IMODE((self.data / 'hardware-centers.json').stat().st_mode), 0o600)
        c.set_mode('shadow')
        c.set_mode('policy')
        self.assertTrue(c.arm_pending)
        restarted = self.controller()
        self.assertFalse(restarted.status()['calibration']['requires_centering'])
        self.assertEqual(restarted.status()['calibration']['centered_ids'], MOTOR_IDS)

    def test_partial_calibration_resumes_but_changed_legacy_file_invalidates_marker(self):
        c = self.controller()
        c.set_mode('calibrate')
        c.cal_center(0)
        restarted = self.controller()
        self.assertEqual(restarted.status()['calibration']['centered_ids'], [10])
        self.assertTrue(restarted.status()['calibration']['requires_centering'])
        self.legacy.write_text(json.dumps({'zero_pos': [1900] * 15}))
        changed = self.controller()
        self.assertEqual(changed.status()['calibration']['centered_ids'], [])
        self.assertTrue(changed.status()['calibration']['requires_centering'])

    def test_failed_servo_acknowledgement_never_records_a_center(self):
        c = self.controller()
        c.set_mode('calibrate')
        self.reply_ok = False
        with self.assertRaisesRegex(ValueError, 'No servo replied'):
            c.cal_center(0)
        self.assertFalse((self.data / 'hardware-centers.json').exists())
        self.assertTrue(c.status()['calibration']['requires_centering'])

    def test_failed_last_marker_write_never_enables_motion_or_loses_legacy_file(self):
        c = self.controller()
        c.set_mode('calibrate')
        for i in range(14):
            self.feedback(c)
            c.cal_center(i)
        self.feedback(c)
        with patch('motion_calibration.os.replace', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError, '校准记录保存失败'):
                c.cal_center(14)
        self.assertTrue(c.status()['calibration']['requires_centering'])
        self.assertEqual(self.legacy.read_bytes(), self.original)
        self.assertEqual(list(self.data.glob('.hardware-centers-*')), [])

    def test_no_legacy_or_already_hardware_centered_robot_keeps_stable_workflow(self):
        for zeros in (None, [2047] * 15):
            if zeros is None:
                self.legacy.unlink()
            else:
                self.legacy.write_text(json.dumps({'zero_pos': zeros}))
            c = self.controller()
            self.assertFalse(c.status()['calibration']['requires_centering'])
            c.set_mode('hold')
            self.assertTrue(c.arm_pending)

    def test_invalid_legacy_and_marker_data_do_not_silently_allow_arm(self):
        for raw in ('not json', '[]', '{"zero_pos":[true]}', '{"zero_pos":null}'):
            self.legacy.write_text(raw)
            (self.data / 'hardware-centers.json').write_text('[]')
            c = self.controller()
            with self.assertRaisesRegex(ValueError, '旧版软件零位'):
                c.set_mode('policy')

    def test_invalid_joint_indices_cannot_write_a_migration_record(self):
        c = self.controller()
        c.set_mode('calibrate')
        for index in (-1, 15, True, 1.5, '1'):
            with self.assertRaises(ValueError):
                c.cal_center(index)
        self.assertFalse((self.data / 'hardware-centers.json').exists())


if __name__ == '__main__':
    unittest.main()
