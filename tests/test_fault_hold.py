"""Real Controller/IPC semantics for torque retention after stream failure."""
import importlib.util
import collections
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import wire
import test_voice_motion as motion_fixture


class FaultHoldTest(unittest.TestCase):
    def setUp(self):
        self.f = motion_fixture.VoiceMotionTest()
        self.f.setUp()
        self.c, self.access = self.f.c, self.f.access

    def mode(self):
        return wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4]

    def paused_feedback(self, command_seq, *, paused=True):
        if not hasattr(self.c.feedback, 'seq'):
            self.c.feedback.seq = 100
            self.c.rx_times, self.c.rx_intervals = collections.deque(), collections.deque()
            self.c.received = self.c.gaps = 0
            self.c.filter = Mock(update=Mock(return_value=self.c.filtered))
        raw = wire.STATE.pack(b'DQS1', 1, 35 | (4 if paused else 0), wire.ALL_MASK,
            self.c.feedback.seq + 1, 9000000, 9000000, command_seq, 10000, 10000, 500, 0, 0, 0,
            0, 0, -16384, 0, 0, 0, *([0.] * 45))
        self.c.receive(raw)

    def test_delayed_pause_feedback_cannot_cancel_explicit_resume_before_or_after_arm(self):
        for last_seq in (40, 0xffffffff):
            with self.subTest(last_seq=last_seq):
                self.c.seq = last_seq
                self.c.pause_motion('test gap')
                self.c.set_mode('policy')
                self.paused_feedback(last_seq)
                self.assertTrue(self.c.arm_pending)
                self.assertFalse(self.c.fault_hold)
                self.c.step()
                self.assertEqual(self.mode(), wire.ARM)
                arm_seq = wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[1]
                count = self.c.bridge.notify.call_count
                self.paused_feedback(last_seq)
                self.c.step()
                self.assertFalse(self.c.fault_hold)
                self.assertEqual(self.c.bridge.notify.call_count, count)
                self.paused_feedback(arm_seq, paused=False)
                self.assertIsNone(self.c._arm_ack_seq)
                self.c.step()
                self.assertEqual(self.mode(), wire.POLICY)

    def test_arm_ack_timeout_retains_torque_and_never_sends_unconfirmed_policy(self):
        self.c.pause_motion('test gap')
        self.c.set_mode('policy')
        self.c.step()
        self.c._arm_ack_deadline = 0
        self.c.step()
        self.assertTrue(self.c.fault_hold)
        self.assertTrue(self.c.feedback.enabled)
        self.assertEqual(self.mode(), wire.PAUSE)
        self.assertIn('acknowledgement timed out', self.c.last_error)

    def test_fresh_pause_after_ack_is_not_masked_by_resume_wait(self):
        self.c.pause_motion('test gap')
        self.c.set_mode('policy')
        self.c.step()
        arm_seq = wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[1]
        self.paused_feedback(arm_seq)
        self.assertTrue(self.c.fault_hold)
        self.c.step()
        self.assertEqual(self.mode(), wire.PAUSE)

    def test_explicit_off_wins_over_inflight_arm_ack(self):
        self.c.pause_motion('test gap')
        self.c.set_mode('policy')
        self.c.step()
        arm_seq = wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[1]
        self.c.set_mode('off')
        self.assertEqual(self.mode(), wire.DISARM)
        self.paused_feedback(arm_seq, paused=False)
        self.c.step()
        self.assertEqual(self.mode(), wire.DISARM)
        self.assertFalse(self.c.fault_hold)

    def test_stand_lease_waits_for_arm_ack_but_other_motion_stays_blocked(self):
        self.c.pause_motion('test gap')
        self.f.motion_status.update(mcu_watchdog=True, mcu_command_age_us=999999)
        self.access.start_action(kind='stand', session=101)
        self.access.tick()
        self.assertEqual(self.access.action['state'], 'running')
        self.c.step()
        self.access.tick()
        self.assertEqual(self.access.action['state'], 'running')
        self.assertFalse(self.access.movement_state(self.c.status())['movement_ready'])
        self.c._arm_ack_deadline = 0
        self.c.step()
        self.access.tick()
        self.assertEqual(self.access.action['state'], 'cancelled')

    def test_paused_move_reports_explicit_resume_instead_of_broken_bridge(self):
        self.c.pause_motion('test gap')
        self.f.motion_status.update(mcu_watchdog=True, mcu_command_age_us=999999)
        count = self.c.bridge.notify.call_count
        with self.assertRaisesRegex(ValueError, '站起来'):
            self.f.move()
        self.assertEqual(self.c.bridge.notify.call_count, count)
        self.assertIn('站起来', self.access.state()['movement_block_reason'])

    def test_feedback_gap_sends_one_freeze_not_disarm_or_stale_gait(self):
        self.c.twist = [.2,0,0]
        self.c.rx_time -= .3
        self.c.step()
        self.assertEqual(self.mode(), wire.PAUSE)
        self.assertTrue(self.c.fault_hold)
        self.assertEqual(self.c.twist, [0,0,0])
        self.assertEqual(len(self.c.pause_events), 1)
        self.assertGreater(self.c.pause_events[0]['feedback_age_ms'], 150)
        count = self.c.bridge.notify.call_count
        self.c.rx_time += .3
        for _ in range(10): self.c.step()
        self.assertEqual(self.c.bridge.notify.call_count, count)
        self.assertTrue(self.c.fault_hold) # Healthy feedback alone cannot replay motion.

    def test_nonzero_commands_rejected_until_explicit_resume_and_off_always_works(self):
        self.c.pause_motion('test gap')
        with self.assertRaisesRegex(ValueError, 'Motion paused'):
            self.c.command(twist=[.2,0,0], source='web')
        self.c.command(twist=[0,0,0], mouth=0, source='web')
        self.c.set_mode('off')
        self.assertFalse(self.c.fault_hold)
        self.assertEqual(self.mode(), wire.DISARM)

    def test_explicit_stand_can_resume_a_supported_mcu_watchdog_hold(self):
        self.c.pause_motion('test gap')
        self.f.motion_status.update(mcu_watchdog=True, mcu_command_age_us=999999)
        self.c.feedback.flags |= 4
        self.access.start_action(kind='stand', session=101)
        self.assertFalse(self.c.fault_hold)
        self.assertTrue(self.c.arm_pending)
        self.c.step()
        self.assertEqual(self.mode(), wire.ARM)
        self.assertEqual(self.c.twist, [0,0,0])

    def test_old_watchdog_firmware_and_missing_joint_cannot_be_resumed(self):
        self.c.pause_motion('test gap')
        self.c.feedback.flags = 7 # Old MCU: watchdog means motors stopped, no hold capability.
        self.f.motion_status.update(mcu_watchdog=True, mcu_command_age_us=999999)
        with self.assertRaisesRegex(ValueError, 'command link'):
            self.access.start_action(kind='stand', session=101)
        self.c.feedback.flags |= wire.FAULT_HOLD_CAPABLE
        self.c.feedback.mask = 1
        with self.assertRaisesRegex(ValueError, 'feedback'):
            self.c.set_mode('policy')
        self.assertTrue(self.c.fault_hold)

    def test_old_unpaused_mcu_cannot_be_armed_without_matching_firmware(self):
        self.c.mode = 'shadow'
        self.c.feedback.flags = 3
        with self.assertRaisesRegex(ValueError, 'matching sketch'):
            self.c.set_mode('policy')
        self.assertEqual(self.c.mode, 'shadow')
        self.assertFalse(self.c.arm_pending)
        self.c.bridge.notify.assert_not_called()

    def test_explicit_resume_cancels_old_queue_even_before_supervisor_observes_pause(self):
        self.f.move(duration=10)
        old = self.access.action
        self.f.move(kind='turn', twist=[0,0,1], duration=2)
        self.c.pause_motion('feedback gap')
        self.access.start_action(kind='stand', session=101)
        self.assertEqual(old['state'], 'cancelled')
        self.assertFalse(self.access.pending)
        self.assertEqual(self.access.action['kind'], 'stand')
        self.assertFalse(self.c.fault_hold)

    def test_app_shutdown_retains_torque_and_aborts_an_inflight_policy_generation(self):
        self.c.stop_event = threading.Event()
        generation = self.c.generation
        self.c.stop()
        self.assertTrue(self.c.fault_hold)
        self.assertGreater(self.c.generation, generation)
        self.assertEqual(self.mode(), wire.PAUSE)
        self.assertTrue(self.c.stop_event.is_set())

    def test_mouth_is_suppressed_while_body_is_paused_with_torque_on(self):
        self.c.pause_motion('test gap')
        self.access.speech(101, 1, 1, [[self.f.now+.1, 10.]])
        self.f.advance(.15)
        self.assertEqual(self.c.mouth, 0)
        self.assertTrue(self.c.feedback.enabled)

    def test_new_linux_app_adopts_enabled_mcu_without_disarm_and_continues_sequence(self):
        self.c.mode, self.c.feedback = 'shadow', None
        self.c.filter = Mock()
        self.c.filter.update.return_value = self.c.filtered
        self.c.rx_times = collections.deque()
        self.c.rx_intervals = collections.deque()
        self.c.received = self.c.gaps = 0
        raw = wire.STATE.pack(b'DQS1', 1, 35, wire.ALL_MASK,
            500, 9000000, 9000000, 999, 10000, 10000, 500, 0, 0, 0,
            0, 0, -16384, 0, 0, 0, *([0.] * 45))
        self.c.receive(raw)
        self.assertTrue(self.c.fault_hold)
        self.assertEqual(self.c.seq, 999)
        self.c.step()
        command = wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])
        self.assertEqual(command[1], 1000)
        self.assertEqual(command[4], wire.PAUSE)

    def test_policy_exception_pauses_without_issuing_disarm(self):
        self.c.stop_event = threading.Event()
        self.c.loop_missed = 0
        motion_fixture.fixture.ns['CONTROL_PERIOD'] = .02
        def fail(*args):
            self.c.stop_event.set()
            raise RuntimeError('inference failure')
        self.c.policy.infer.side_effect = fail
        self.c.run()
        self.assertTrue(self.c.fault_hold)
        self.assertIn('inference failure', self.c.last_error)
        self.c.bridge.notify.assert_not_called()

    def test_diagnostic_shutdown_leaves_last_joint_positions_until_explicit_exit(self):
        self.c.stop_event = threading.Event()
        for mode in ('calibrate', 'servo_debug'):
            self.c.mode = mode
            self.c.stop()
            self.assertEqual(self.c.mode, mode)
            self.c.bridge.notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
