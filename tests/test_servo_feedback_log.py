import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from controller import Controller
from servo_feedback_log import ServoFeedbackLog
import wire


class ServoFeedbackLogTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'servo-feedback.log'
        self.controller = Controller(Mock(), imu_calibration_path=Path(directory.name) / 'imu.json')
        self.log = self.controller.servo_feedback_log = ServoFeedbackLog(self.path)
        self.now = 10.
        self.clock = patch('controller.time.monotonic', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def frame(self, seq, missing=0, enabled=True, *, v2=False, detail=b'', sample=None,
              imu_ok=True, imu_sample=None):
        sample = seq * 10000 if sample is None else sample
        imu_sample = sample if imu_sample is None else imu_sample
        self.controller.receive(wire.STATE.pack(b'DQS2' if v2 else b'DQS1', 2 if v2 else 1,
            int(imu_ok) | (2 if enabled else 0), wire.ALL_MASK ^ missing, seq, sample, imu_sample, 0, 0, 10000, 500,
            0, 0, 0, 0, 0, -16384, 0, 0, 0, *([31.] * 15 + [2.] * 15 + [75.] * 15)) + detail)

    def voltage(self, mv):
        self.controller.receive_diagnostics(wire.DIAGNOSTIC_V2.pack(b'DQD2', *([0] * 16), mv))

    def rows(self):
        self.log.close()
        return [json.loads(line) for line in self.path.read_text().splitlines()]

    def test_imu_invalid_disarm_logs_once_at_transition(self):
        self.frame(1)
        self.controller.set_mode('hold')
        self.voltage(5600)
        self.now += .012
        self.frame(2, imu_ok=False, imu_sample=13000)
        self.now += .003
        self.controller.step()
        self.assertEqual(self.controller.status()['last_error'], 'sensor feedback lost; disarmed')
        self.now += .01
        self.frame(3, enabled=False, sample=700000, imu_sample=13000)
        self.controller.step()
        self.assertEqual(self.controller.status()['last_error'], 'sensor feedback lost; disarmed')
        self.now += .01
        self.frame(4, enabled=False, sample=710000)
        self.controller.step()
        self.assertEqual(self.controller.status()['last_error'], '')
        self.assertEqual(self.controller.mode, 'shadow')
        events = [r for r in self.rows() if r['event'] == 'imu_feedback_disarm']
        self.assertEqual(len(events), 1)
        r = events[0]
        self.assertEqual((r['seq'], r['mode'], r['reason'], r['imu_ok']), (2, 'hold', 'imu_invalid', False))
        self.assertEqual((r['bus_voltage_v'], r['voltage_age_ms']), (5.6, 15.))
        self.assertEqual((r['feedback_interval_ms'], r['feedback_age_ms'], r['imu_age_ms']), (12., 3., 10.))

    def test_imu_timeout_with_either_validity_flag_across_clock_wrap(self):
        self.voltage(7200)
        for seq, imu_ok in ((2, True), (4, False)):
            self.now += .6  # The last voltage reading is no longer current.
            self.frame(seq - 1, sample=0xfff00000)
            self.controller.set_mode('policy')
            self.now += .010
            self.frame(seq, sample=10000, imu_sample=(10000 - 490000) & 0xffffffff, imu_ok=imu_ok)
            self.now += .020
            self.controller.step()
        events = [r for r in self.rows() if r['event'] == 'imu_feedback_disarm']
        self.assertEqual([(r['imu_ok'], r['reason']) for r in events],
                         [(True, 'imu_timeout'), (False, 'imu_timeout')])
        for r in events:
            self.assertEqual((r['imu_age_ms'], r['feedback_interval_ms'], r['feedback_age_ms']), (510., 10., 20.))
            self.assertIsNone(r['bus_voltage_v'])

    def test_drop_unload_and_first_complete_frame_even_after_rearm(self):
        self.frame(1)
        self.voltage(7700)
        self.now += .05
        self.frame(2, 1 << 2)  # ID 12
        self.frame(2, 0)  # duplicate reports do not close the episode
        self.now += .02
        self.voltage(7000)
        self.controller.set_mode('off')
        self.now += .01
        self.controller.set_mode('off')
        self.frame(3, (1 << 2) | (1 << 7), enabled=False)
        self.now += .02
        self.frame(4, 1 << 7)
        self.now += .1
        self.frame(5)
        rows = self.rows()
        self.assertEqual([r['event'] for r in rows], ['monitor_started', 'missing', 'unload_requested',
                         'missing', 'torque_off', 'missing', 'all_replied'])
        self.assertEqual((rows[1]['missing_ids'], rows[1]['bus_voltage_v'], rows[1]['voltage_age_ms']),
                         ([12], 7.7, 50.0))
        last = rows[-1]
        self.assertEqual((last['online'], last['affected_ids'], last['missing_ms'], last['min_bus_voltage_v']),
                         (15, [12, 22], 150., 7.0))
        self.assertEqual((last['since_unload_request_ms'], last['since_torque_off_ms']), (130., 120.))

    def test_spontaneous_recovery_and_mcu_reset(self):
        self.frame(50)
        self.voltage(7600)
        self.now += .6
        self.frame(51, wire.ALL_MASK)
        self.now += .01
        self.frame(52)
        self.now += .01
        self.frame(53, 1)
        self.controller.set_mode('off')
        self.frame(1, 1 << 14, enabled=False)
        self.frame(2, enabled=False)
        rows = self.rows()
        self.assertIsNone(rows[1]['bus_voltage_v'])
        self.assertEqual((rows[2]['missing_ms'], rows[2]['since_torque_off_ms']), (10., None))
        self.assertEqual(rows[-2]['event'], 'mcu_reset')
        self.assertEqual(rows[-1]['affected_ids'], [34])
        self.assertIsNone(rows[-1]['since_unload_request_ms'])

    def test_release_and_complete_in_same_frame(self):
        self.frame(1, 1)
        self.now += .01
        self.frame(2, enabled=False)
        self.assertEqual(self.rows()[-1]['since_torque_off_ms'], 0.)

    def test_id21_half_second_at_mcu_clock_wrap_and_changing_reply(self):
        i = 6
        bit = 1 << i
        error = bytearray(15)
        error[i] = 0x24
        def send(seq, missing=0, detail=b''):
            self.frame(seq, missing, v2=True, detail=detail, sample=(2**32 - 400000 + seq * 10000) % 2**32)
        for seq in range(1, 62):
            if seq != 50:  # A dropped MCU frame must not be fabricated.
                send(seq)
        send(62, bit, wire.STATE_REPLY_DETAIL.pack(0, 0, error))
        send(63, bit, wire.STATE_REPLY_DETAIL.pack(bit, 0, bytes(15)))
        send(64, bit, wire.STATE_REPLY_DETAIL.pack(0, 0, bytes(15)))
        send(65)
        rows = self.rows()
        rows = [row for row in rows if row['event'] != 'servo_alarm_changed']
        self.assertTrue(rows[0]['reply_diagnostics'])
        self.assertEqual([r['replies']['21']['kind'] for r in rows[1:4]],
                         ['servo_error', 'bad_checksum', 'no_reply_in_window'])
        samples = rows[1]['history_by_id']['21']
        self.assertEqual(samples[0], [-500., 12, 75., 31., 2., 0, 'ok'])
        self.assertEqual(samples[-1], [0., 62, 75., None, None, 0x24, 'servo_error'])
        self.assertNotIn(50, [s[1] for s in samples])
        self.assertIsNone(rows[3]['replies']['21']['raw_error'])

    def test_missing_tail_is_invalid_and_old_firmware_does_not_imply_silence(self):
        self.frame(1, 1, v2=True)
        self.assertEqual(self.controller.bad_frames, 1)
        self.frame(2, 1)
        start, = self.rows()
        self.assertEqual(start['replies']['10']['kind'], 'unknown')
        self.assertIsNone(start['history_by_id']['10'][0][3])

    def test_valid_id21_position_stays_online_and_alarm_is_visible_between_polls(self):
        err = bytearray(15)
        err[6] = 0x24
        detail = wire.STATE_REPLY_DETAIL.pack(0, 0, err)
        self.frame(1, v2=True)
        self.frame(2, v2=True, detail=detail)
        status = self.controller.status()
        self.assertEqual(len(status['servo_ids']), 15)
        self.assertEqual(status['angles'][6], 31)
        self.assertEqual(status['servo_alarms'], {'21': {'code': 0x24, 'active': True}})
        self.frame(3, v2=True)
        self.assertFalse(self.controller.status()['servo_alarms']['21']['active'])
        self.now += 2.1
        self.assertEqual(self.controller.status()['servo_alarms'], {})
        rows = self.rows()
        self.assertEqual([r['event'] for r in rows], ['monitor_started', 'servo_alarm_changed', 'servo_alarm_changed'])
        self.assertEqual(rows[1]['history_by_id']['21'][-1], [0., 2, 75., 31., 2., 0x24, 'ok_with_alarm'])
        self.assertEqual(rows[-1]['alarm_codes'], {})

    def test_out_of_range_position_is_missing_and_distinct_from_timeout(self):
        bit = 1 << 6
        detail = wire.STATE_REPLY_DETAIL.pack(0, 0, bytes(15)) + wire.POSITION_FAULTS.pack(bit)
        self.frame(1, v2=True)
        self.frame(2, missing=bit, v2=True, detail=detail)
        self.assertNotIn(21, self.controller.status()['servo_ids'])
        event = self.rows()[-1]
        self.assertEqual(event['history_by_id']['21'][-1][3:], [None, None, 0, 'invalid_position'])
