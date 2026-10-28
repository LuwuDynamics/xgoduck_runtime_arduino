"""Actual MCU/Controller/MCP diagnostic behavior; no physical robot is accessed."""
import collections
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import test_voice_motion as fixture
from motion_access import command_link_ready
from motion_feedback import SERVO_IDS, feedback_ready, feedback_problem
import wire


class TimeoutDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.VoiceMotionTest()
        self.f.setUp()
        self.c, self.access = self.f.c, self.f.access
        self.c.feedback.flags |= wire.TIMEOUT_CHECKS_DISABLED
        self.c.feedback.seq = 100
        self.c.last_action = [2.] * 14 + [0.]
        self.f.motion_status.update(timeout_checks_disabled=True, imu_ok=True,
            feedback_age_ms=5., servo_ids=list(SERVO_IDS), missing_joint_ids=[])

    def mode(self):
        return wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4]

    def test_feedback_gap_retains_last_target_and_resumes_without_explicit_stand(self):
        self.c.twist = [.2, 0, 0]
        self.c.rx_time -= 9
        with patch('builtins.print'):
            self.c.step()
        command = wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])
        self.assertEqual(command[4], wire.POLICY)
        self.assertEqual(list(command[7:]), self.c.last_action)
        self.assertFalse(self.c.fault_hold)
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.c.twist, [.2, 0, 0])
        self.c.policy.infer.assert_not_called()
        self.assertIn('feedback older', self.c.timeout_warning)
        self.c.rx_time += 9
        with patch('builtins.print'):
            self.c.step()
        self.c.policy.infer.assert_called_once()
        self.assertFalse(self.c.fault_hold)
        self.assertFalse(self.c.arm_pending)
        self.assertEqual(self.mode(), wire.POLICY)
        self.assertEqual(self.c.timeout_warning, '')

    def test_invalid_imu_retains_target_without_disarm_pause_or_invalid_inference(self):
        self.c.feedback.imu_ok = False
        with patch('builtins.print'):
            self.c.step()
        self.assertEqual(self.mode(), wire.POLICY)
        self.assertFalse(self.c.fault_hold)
        self.assertEqual(self.c.mode, 'policy')
        self.c.policy.infer.assert_not_called()
        self.assertIn('IMU feedback invalid', self.c.timeout_warning)

    def test_timeout_diagnostics_are_rate_limited_and_keep_ages_visible(self):
        self.c.rx_time -= 4
        self.c.feedback.command_age_us = 900000
        self.c.feedback.imu_us = 0
        self.c.feedback.sample_us = 500000
        with patch('builtins.print') as log:
            for _ in range(20):
                self.c.step()
        self.assertEqual(log.call_count, 1)
        self.assertEqual(len(self.c.timeout_events), 1)
        event = self.c.timeout_events[0]
        self.assertEqual(event['action'], 'log_only')
        self.assertGreater(event['feedback_age_ms'], 150)
        self.assertEqual(event['last_reported_command_age_us'], 900000)
        self.assertEqual(event['last_reported_imu_age_us'], 500000)
        self.assertEqual(len(event['problems']), 3)

    def test_changing_timeout_conditions_do_not_spam_the_control_loop(self):
        self.c.rx_time -= 4
        with patch('builtins.print') as log:
            for index in range(20):
                self.c.feedback.imu_ok = bool(index % 2)
                self.c.step()
        self.assertEqual(log.call_count, 1)

    def test_imu_age_uses_end_of_sample_work_and_handles_counter_wrap(self):
        for sample_us in (1000000, 0xfffffff0):
            with self.subTest(sample_us=sample_us):
                self.c.feedback.sample_us = sample_us
                self.c.feedback.imu_us = (sample_us + 9000) & 0xffffffff
                self.c.feedback.work_us = 10000
                with patch('builtins.print') as log:
                    self.c.step()
                self.assertEqual(self.c.timeout_warning, '')
                log.assert_not_called()

    def test_late_arm_ack_does_not_latch_pause_or_send_unconfirmed_policy(self):
        self.c.pause_motion('explicit pause')
        self.c.set_mode('policy')
        self.c.step()
        self.assertEqual(self.mode(), wire.ARM)
        arm_seq = wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[1]
        self.c._arm_ack_deadline = 0
        calls = self.c.bridge.notify.call_count
        with patch('builtins.print'):
            self.c.step()
        self.assertFalse(self.c.fault_hold)
        self.assertEqual(self.c.bridge.notify.call_count, calls)
        self.c.filter = Mock(update=Mock(return_value=self.c.filtered))
        self.c.rx_times, self.c.rx_intervals = collections.deque(), collections.deque()
        self.c.received = self.c.gaps = 0
        raw = wire.STATE.pack(b'DQS1', 1, 35 | wire.TIMEOUT_CHECKS_DISABLED, wire.ALL_MASK,
            101, 9000000, 9000000, arm_seq, 10000, 10000, 500, 0, 0, 0,
            0, 0, -16384, 0, 0, 0, *([0.] * 45))
        with patch('builtins.print'):
            self.c.receive(raw)
            self.c.step()
        self.assertIsNone(self.c._arm_ack_seq)
        self.assertFalse(self.c.fault_hold)
        self.assertEqual(self.mode(), wire.POLICY)

    def test_old_mcu_keeps_guards_until_matching_sketch_is_uploaded(self):
        self.c.feedback.flags &= ~wire.TIMEOUT_CHECKS_DISABLED
        self.c.rx_time -= .3
        with patch('builtins.print'):
            self.c.step()
        self.assertTrue(self.c.fault_hold)
        self.assertEqual(self.mode(), wire.PAUSE)

    def test_manual_off_and_explicit_pause_still_work(self):
        self.c.pause_motion('explicit pause')
        self.c.step()
        self.assertTrue(self.c.fault_hold)
        self.assertEqual(self.mode(), wire.PAUSE)
        self.c.set_mode('off')
        self.assertEqual(self.mode(), wire.DISARM)

    def test_empty_feedback_or_missing_requested_joint_is_not_fabricated(self):
        self.c.feedback.mask &= ~(1 << 12)
        with self.assertRaisesRegex(ValueError, 'required servo feedback'):
            self.c._require_feedback(1 << 12)
        self.c.feedback = None
        self.c.mode = 'shadow'
        with self.assertRaisesRegex(ValueError, 'feedback is stale'):
            self.c.set_mode('policy')

    def test_mcp_does_not_cancel_action_for_the_disabled_age_and_imu_checks(self):
        self.f.motion_status.update(full_robot_ready=False, feedback_age_ms=9000.,
                                    imu_ok=False, mcu_command_age_us=9000000)
        self.assertTrue(feedback_ready(self.c.status()))
        self.assertTrue(command_link_ready(self.c.status()))
        result = self.f.move(duration=10)
        self.assertEqual(result['status'], 'accepted')
        self.f.advance(.3)
        self.assertEqual(self.access.action['state'], 'running')
        self.assertEqual(self.c.mode, 'policy')

    def test_debug_health_warning_remains_visible_without_becoming_mcp_error(self):
        self.f.motion_status.update(full_robot_ready=False, feedback_age_ms=9000.,
                                    imu_ok=False, mcu_command_age_us=9000000,
                                    timeout_warning='STM32 feedback older than 150 ms')
        mcp = self.f.mcp()
        # Use the real authenticated IPC route used by the existing MCP fixture.
        result = mcp.thing_manager.things['Duck'].state()
        self.assertTrue(result['timeout_checks_disabled'])
        self.assertFalse(result['full_robot_ready'])
        self.assertTrue(result['ready'])
        self.assertIn('9000', result['feedback_warning'])
        self.assertIn('IMU', result['feedback_warning'])
        self.assertEqual(result['error'], '')

    def test_bypass_preserves_missing_joint_and_uninitialized_link_errors(self):
        status = self.c.status()
        self.assertFalse(command_link_ready(status | {'mcu_command_age_us': 0xffffffff}))
        self.assertFalse(command_link_ready(status | {'mcu_watchdog': True}))
        self.assertFalse(feedback_ready(status | {'feedback_age_ms': None}))
        status['servo_ids'] = [i for i in SERVO_IDS if i != 33]
        self.assertTrue(feedback_ready(status, (32,)))
        self.assertFalse(feedback_ready(status, (33,)))
        self.assertIn('33', feedback_problem(status, (33,)))

    @unittest.skipUnless(importlib.util.find_spec('onnxruntime'), 'real ONNX runtime required')
    def test_real_controller_status_advertises_bypass_without_fabricating_sensor_health(self):
        import controller
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(controller, 'ZERO_PATH', Path(directory) / 'zero_pos.json'):
                c = controller.Controller(Mock())
        raw = wire.STATE.pack(b'DQS1', 1, 33 | wire.TIMEOUT_CHECKS_DISABLED, wire.ALL_MASK,
            1, 10000, 10000, 0, 0, 10000, 500, 0, 0, 0,
            0, 0, -16384, 0, 0, 0, *([0.] * 45))
        c.receive(raw)
        c.rx_time -= 3
        status = c.status()
        self.assertTrue(status['timeout_checks_disabled'])
        self.assertTrue(status['control_ready'])
        self.assertFalse(status['full_robot_ready'])
        self.assertGreater(status['feedback_age_ms'], 150)
        self.assertIn('timeout_events', status)


if __name__ == '__main__':
    unittest.main()
