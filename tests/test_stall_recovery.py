import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from controller import Controller
from motion_access import MotionAccess
from rl_core import MOTOR_IDS
import wire


class MotionFeedbackCase(unittest.TestCase):
    def setUp(self):
        self.c = Controller(Mock())
        self.now = 20.
        clock = patch('controller.time.monotonic', side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        original_update = self.c.filter.update
        def filtered(*args, **kwargs):
            return dict(original_update(*args, **kwargs), grav=self.gravity, gyro_rad=self.gyro, imu_fusion_ready=True)
        self.c.filter.update = filtered
        self.gyro = (0., 0., 0.)
        self.seq = 0
        self.c.mode = 'policy'
        self.c.servo_feedback_log = Mock()
        self.angles = [0.] * 15
        self.targets = [0.] * 15
        # Measured positions and commanded targets from the .219 stall.
        self.joints = [MOTOR_IDS.index(sid) for sid in (12, 21)]
        for i, actual, target in zip(self.joints, (-50.02, 31.03), (65.90, 96.83)):
            self.angles[i], self.targets[i] = actual, target

    def frame(self, alarms=(12, 21), *, enabled=True, mask=wire.ALL_MASK, gravity=(0, 0, -1), speed=0., echo=True, code=8):
        self.seq += 1
        self.now += .02
        us = self.seq * 20000
        self.gravity = gravity
        targets = self.c.last_action if echo and self.c.stall.phase == 'retreat' else self.targets
        status = wire.STATE.pack(b'DQS2', 2, 1 + 2 * enabled, mask,
            self.seq, us, us, self.c.seq, 0, 10000, 500, 0, 0, 0, 0, 0, -16384, 0, 0, 0,
            *self.angles, *([speed] * 15), *targets)
        codes = bytes(code if sid in alarms else 0 for sid in MOTOR_IDS)
        if alarms or mask != wire.ALL_MASK:
            status += wire.STATE_REPLY_DETAIL.pack(0, 0, codes)
        self.c.receive(status)
        self.c.step()
        packet = self.c.bridge.notify.call_args.args[1]
        cmd = wire.COMMAND.unpack(packet)
        return cmd[4], cmd[-15:]


class RollerStopTest(MotionFeedbackCase):
    def setUp(self):
        super().setUp()
        self.c.set_roller_mode(True)
        self.home = self.c.policy.default_deg
        self.angles = self.home[:]
        self.angles[1] += 24.
        self.angles[6] -= 18.
        action = [.25] * 14
        self.c.policy.infer = Mock(return_value=action)
        self.driving = self.c.policy.action_to_deg(action)

    def move(self, vx=.25, yaw=0.):
        self.c.command(twist=[vx, 0., yaw])
        self.frame(())

    def release(self):
        return self.c.command(twist=[0., 0., 0.], roller_release=True)['roller_gliding']

    def phase(self):
        return self.c.status()['roller']['stop_phase']

    def test_decelerates_in_closed_loop_policy_without_open_loop_override_and_confirms_stance(self):
        self.move()
        self.assertTrue(self.release())
        for _ in range(25):  # 0.50 s of closed-loop deceleration while splayed.
            self.frame(())
            self.assertTrue(self.release())
        self.assertEqual(self.phase(), 'gliding')
        self.assertEqual(self.c.policy.infer.call_args.args[4], [0., 0., 0.])
        # Policy outputs are NEVER clamped or overwritten open-loop.
        self.assertEqual(self.c.last_action[:10], self.driving[:10])
        self.angles = self.home[:]
        self.angles[4] += 5.2  # Slight ankle load error (<= 8°) is allowed.
        self.c.command(mouth=12.)
        for _ in range(15):
            self.release()
            _, target = self.frame((), speed=20.)
            self.assertEqual(list(target[:10]), self.driving[:10])
            self.assertEqual(target[-1], 12.)
        self.assertEqual(self.phase(), 'gliding')  # Still moving too fast.
        for _ in range(12):
            self.release()
            self.frame(())
        self.assertEqual(self.phase(), 'idle')
        self.assertEqual(self.c.status()['roller']['glide_result'], 'stance_confirmed')
        # Policy remains live in closed-loop balance after confirmation.
        self.assertEqual(list(self.frame(())[1][:10]), self.driving[:10])
        self.move(-.1)
        self.assertEqual(self.phase(), 'idle')
        self.assertEqual(self.c.twist, [-.1, 0., 0.])

    def test_manual_repush_immediately_takes_over_and_glide_times_out_cleanly(self):
        # Repush during gliding takes over immediately (never locked out).
        self.move(-.36, .42)
        self.assertTrue(self.release())
        for _ in range(10):
            self.release()
            self.frame((11, 21), speed=15.)
        self.assertEqual(self.phase(), 'gliding')
        self.c.command(twist=[.2, 0., -.3])
        self.assertEqual(self.phase(), 'idle')
        self.assertEqual(self.c.status()['roller']['glide_result'], 'manual_input')
        self.assertEqual(self.c.twist, [.2, 0., -.3])

        # Splayed legs time out cleanly after GLIDE_TIMEOUT_S instead of stalling forever.
        self.move(.3)
        self.assertTrue(self.release())
        for _ in range(110):
            self.release()
            self.frame(())
            if self.phase() == 'idle':
                break
        self.assertEqual(self.phase(), 'idle')
        self.assertEqual(self.c.status()['roller']['glide_result'], 'glide_timeout')
        self.assertEqual(self.c.twist, [0., 0., 0.])

    def test_idle_web_heartbeat_during_roller_release(self):
        access = MotionAccess(self.c, clock=lambda: self.now)
        neutral = {'twist': [0., 0., 0.]}
        self.angles[1], self.angles[6] = 9.85, -10.37
        self.move(-.39)
        self.frame((11, 21))
        self.assertTrue(access.web_command(**neutral, roller_release=True)['roller_gliding'])
        self.now += .009
        self.assertTrue(access.web_command(**neutral, idle_heartbeat=True)['roller_gliding'])
        self.assertEqual(self.c.twist, [0., 0., 0.])
        self.frame((11, 21))
        self.angles = self.targets = self.home[:]
        for _ in range(36):
            access.web_command(**neutral, roller_release=True)
            access.web_command(**neutral, idle_heartbeat=True)
            self.frame(())
        self.assertEqual(self.phase(), 'idle')
        self.assertEqual(self.c.status()['roller']['glide_result'], 'stance_confirmed')

        self.angles[1] += 15.
        self.move()
        self.assertTrue(self.release())
        access.web_command(**neutral)
        self.assertEqual(self.c.status()['roller']['glide_result'], 'stopped')
        self.assertEqual(self.c.twist, [0., 0., 0.])

    def test_idle_web_panel_cannot_start_or_renew_a_glide(self):
        access = MotionAccess(self.c, clock=lambda: self.now)
        neutral = {'twist': [0., 0., 0.], 'idle_heartbeat': True}
        self.move()
        access.web_command(**neutral)
        self.assertEqual(self.phase(), 'idle')
        self.assertEqual(self.c.twist, [0., 0., 0.])
        self.move()
        self.assertTrue(self.release())
        for _ in range(55):
            access.web_command(**neutral)
            self.frame(())
        self.assertEqual(self.c.status()['roller']['glide_result'], 'input_timeout')
        self.assertEqual(self.c.twist, [0., 0., 0.])

    def test_only_a_deliberate_fresh_longitudinal_release_starts_travel(self):
        self.frame(())
        self.c.command(twist=[.2, 0., 0.])
        self.assertFalse(self.release())  # Released before the first skating frame.
        self.move(.2)
        self.c.command(twist=[0., 0., 0.])
        self.assertFalse(self.release())
        self.move(0., .3)
        self.assertFalse(self.release())
        self.move()
        self.now += .25
        self.assertFalse(self.release())
        self.move()
        self.c.set_roller_mode(False)
        self.move(.1)
        self.assertFalse(self.release())
        self.assertEqual(self.c.twist, [0., 0., 0.])

    def test_disconnect_missing_feedback_other_faults_fall_and_disarm_still_stop_travel(self):
        self.move()
        self.assertTrue(self.release())
        for _ in range(52):
            self.frame(())
        self.assertEqual(self.c.twist, [0., 0., 0.])
        self.assertEqual(self.c.status()['roller']['glide_result'], 'input_timeout')
        for kwargs in (dict(alarms=(21,), code=9), dict(mask=wire.ALL_MASK ^ 2), dict(gravity=(0., 1., 0.))):
            self.move()
            self.assertTrue(self.release())
            self.frame(**dict({'alarms': ()}, **kwargs))
            self.assertEqual(self.c.twist, [0., 0., 0.])
            self.assertEqual(self.c.status()['roller']['glide_result'], 'protection')
        self.move()
        self.assertTrue(self.release())
        self.c.set_mode('off')
        self.assertEqual(self.frame((), enabled=False)[0], wire.DISARM)

    def test_sustained_overcurrent_is_warning_only_during_roller_drive_and_release(self):
        self.move()
        self.targets = self.home[:]
        for _ in range(250):  # Five seconds of overcurrent, encoder error and zero speed.
            self.c.command(twist=[.25, 0., 0.])
            mode, target = self.frame((11, 21))
            self.assertEqual(mode, wire.POLICY)
            self.assertEqual(list(target), self.driving)
        self.assertEqual(self.phase(), 'idle')
        for _ in range(50):  # 1.0 s of release under overcurrent stays active without disarming.
            self.assertTrue(self.release())
            self.assertEqual(self.frame((11, 21))[0], wire.POLICY)
        self.assertEqual(self.c.stall.phase, 'idle')
        self.assertEqual(self.phase(), 'gliding')
        alarms = self.c.status()['servo_alarms']
        for sid in ('11', '21'):
            self.assertEqual(alarms[sid], dict(code=wire.PROTECTION_OVERCURRENT, active=True))
        self.assertTrue(self.c.servo_feedback_log.observe.called)

    def test_switching_walking_modes_discards_pending_overcurrent_evidence(self):
        self.c.set_roller_mode(False)
        self.targets = self.home[:]
        self.frame(())
        with patch.object(self.c, 'step'):
            for _ in range(32):
                self.frame((11, 21))
        self.assertTrue(self.c.stall.qualified)
        self.c.set_roller_mode(True)
        self.c.step()
        self.assertEqual(self.c.stall.phase, 'idle')
        for _ in range(40):
            self.assertEqual(self.frame((11, 21))[0], wire.POLICY)
        self.c.set_roller_mode(False)
        for _ in range(30):
            self.assertEqual(self.frame((11, 21))[0], wire.POLICY)
        self.assertEqual(self.frame((11, 21))[0], wire.HOLD)
        self.assertEqual(self.c.stall.phase, 'retreat')

    def test_roller_stall_with_an_additional_fault_keeps_existing_recovery(self):
        self.move()
        self.targets = self.home[:]
        for _ in range(31):
            mode, _ = self.frame((11, 21), code=wire.PROTECTION_OVERCURRENT | wire.PROTECTION_VOLTAGE)
        self.assertEqual(self.c.stall.phase, 'retreat')
        self.assertEqual(mode, wire.HOLD)

    def test_cockpit_normal_release_uses_lease_and_manual_input_or_hard_release_cancels(self):
        access = MotionAccess(self.c, clock=lambda: self.now)
        owner = 'a' * 24
        for _ in range(55):
            self.frame(())
            access.movement_state(self.c.status())
        access.cockpit_motion(owner, 1, 'begin', [.2, 0., 0.])
        self.frame(())
        self.assertTrue(access.cockpit_motion(owner, 2, 'drive', [0.,0.,0.], roller_release=True)['roller_gliding'])
        self.assertEqual(self.frame(())[1][2], self.driving[2])
        # New cockpit drive command immediately takes over instead of locking out.
        access.cockpit_motion(owner, 3, 'begin', [-.3, 0., -.4])
        self.assertEqual(self.c.twist, [-.3, 0., -.4])
        self.assertFalse(self.c.status()['roller']['stop_pose_active'])
        self.frame(())
        self.assertTrue(access.cockpit_motion(owner, 4, 'drive', [0.,0.,0.], roller_release=True)['roller_gliding'])
        access.cockpit_motion(owner, 5, 'release')
        self.assertEqual(self.c.twist, [0., 0., 0.])
        self.assertFalse(self.c.status()['roller']['stop_pose_active'])
        with self.assertRaises(ValueError):
            access.cockpit_motion(owner, 6, 'drive', [0.,0.,0.], roller_release=True)


class StallRecoveryTest(MotionFeedbackCase):
    def retreat(self, alarms=(21,)):
        for _ in range(31):
            mode, target = self.frame(alarms)
        self.assertEqual(self.c.stall.phase, 'retreat')
        self.assertEqual(mode, wire.HOLD)
        for a, b in zip(target, self.angles):
            self.assertAlmostEqual(a, b, places=4)

    def test_full_stance_moves_all_joints_without_a_jump(self):
        home = self.c.policy.default_deg
        self.angles = [goal + 42 for goal in home]
        self.targets = [angle + 60 for angle in self.angles]
        self.retreat()
        previous = self.angles[:]
        for _ in range(50):
            mode, target = self.frame(())
            self.assertEqual(mode, wire.HOLD)
            for old, new, goal in zip(previous, target, home):
                self.assertLessEqual(abs(new - old), .02 * self.c.stall.PEAK_SPEED_DEG_S + .0001)
                self.assertLessEqual(goal - .0001, new)
                self.assertLessEqual(new, old + .0001)
            previous = target
        for actual, expected in zip(target, home):
            self.assertAlmostEqual(actual, expected, places=4)
        self.assertEqual(self.c.stall.phase, 'retreat')  # Command reached home, encoders have not.

    def test_alarm_on_a_single_other_servo_disarms_and_requires_manual_rearm(self):
        self.retreat((12, 21))
        self.assertIn('ignored', self.c.command(twist=[.1, 0, 0]))
        with self.assertRaises(ValueError):
            self.c.set_mode('hold')
        for _ in range(75):  # First alarm through 1.48 s, at 20 ms per frame.
            self.assertEqual(self.frame((31,))[0], wire.HOLD)
        self.assertEqual(self.frame((31,))[0], wire.DISARM)
        event = self.c.servo_feedback_log.record.call_args
        self.assertEqual(event.kwargs['sustained_alarm_ids'], [31])
        self.assertEqual(set(event.kwargs['servo_ids']), {12, 21})
        self.assertEqual(self.c.mode, 'off')
        access = MotionAccess(self.c)
        with self.assertRaises(ValueError):
            access._begin_action({'kind': 'move', 'remote_controller': 'phone'}, {})
        for _ in range(8):
            self.assertEqual(self.frame((), enabled=False)[0], wire.DISARM)
        self.assertEqual(self.c.stall.phase, 'disarmed')
        self.c.set_mode('policy')
        self.assertEqual(self.frame((), enabled=False)[0], wire.ARM)

    def assert_resume(self, gravity, phase, offsets=()):
        self.retreat()
        self.assertEqual(self.c.stall.indices, (MOTOR_IDS.index(21),))
        home = self.c.policy.default_deg
        self.angles = list(home)
        for servo_id, offset in offsets:
            self.angles[MOTOR_IDS.index(servo_id)] += offset
        for _ in range(130):
            mode, target = self.frame((), gravity=gravity)
            self.assertEqual(mode, wire.HOLD)
            if self.c.stall.phase == 'monitor':
                break
        self.assertEqual(self.c.stall.phase, 'monitor')
        self.assertEqual(self.c.recovery.phase, phase)
        self.assertEqual(self.frame((), gravity=gravity)[0], wire.POLICY)

    def test_stable_upright_walks_but_second_single_stall_disarms(self):
        self.assert_resume((0, 0, -1), 'walk')
        for _ in range(150):
            self.frame(())
        for _ in range(32):
            self.frame((12,))
        self.assertEqual(self.c.stall.phase, 'disarmed')

    def test_stable_lying_down_selects_getup(self):
        self.assert_resume((0, 1, 0), 'getup')

    def test_single_joint_at_ten_degrees_can_resume_walking(self):
        self.assert_resume((0, 0, -1), 'walk', offsets=((34, -10.), (20, 5.), (21, -5.)))

    def test_single_joint_at_7_03_degrees_allows_getup_only_after_alarm_clears(self):
        self.retreat()
        gravity = (-.4492, -.8752, .1797)  # Side-lying attitude recorded on .219.
        self.angles = list(self.c.policy.default_deg)
        self.angles[MOTOR_IDS.index(20)] += 7.03
        self.angles[MOTOR_IDS.index(21)] += 3.44
        while self.now < self.c.stall.started + self.c.stall.duration:
            self.frame((), gravity=gravity, echo=False)
        for _ in range(15):
            self.frame((30,), gravity=gravity)
        self.assertEqual(self.c.stall.phase, 'retreat')
        for _ in range(9):
            self.frame((), gravity=gravity)
        self.assertEqual(self.c.stall.phase, 'retreat')
        for _ in range(3):
            self.frame((), gravity=gravity)
        self.assertEqual(self.c.stall.phase, 'monitor')
        self.assertEqual(self.c.recovery.phase, 'getup')
        self.assertEqual(self.frame((), gravity=gravity)[0], wire.POLICY)

    def test_transient_moving_or_at_target_alarms_allow_ordinary_getup(self):
        for alarms, speed, code, count in [((21,), 0., 8, 10), ((12, 21), 10., 8, 40),
                                            ((21,), 0., 2, 40), ((31,), 0., 8, 40)]:
            for _ in range(count):
                self.frame(alarms, speed=speed, code=code, gravity=(0, 1, 0))
        self.assertEqual(self.c.stall.phase, 'idle')
        self.assertEqual(self.c.recovery.phase, 'getup')

    def test_motor_home_requires_echo_and_stable_orientation(self):
        self.retreat()
        self.angles = list(self.c.policy.default_deg)
        while self.now < self.c.stall.started + self.c.stall.duration:
            self.frame((), echo=False)
        for _ in range(12):
            self.frame((), echo=False)
        self.assertEqual(self.c.stall.phase, 'retreat')
        for i in range(12):
            self.frame((), gravity=(0, 1, 0) if i % 2 else (0, 0, -1))
        self.assertEqual(self.c.stall.phase, 'retreat')
        self.gyro = (1., 0., 0.)
        for _ in range(12):
            self.frame(())
        self.assertEqual(self.c.stall.phase, 'retreat')
        self.gyro = (0., 0., 0.)
        for _ in range(12):
            self.frame(())
        self.assertEqual(self.c.stall.phase, 'monitor')

    def test_feedback_loss_manual_stop_and_missing_positions(self):
        self.retreat()
        # A single transient dropped servo reply during retreat does not immediately drop torque.
        self.assertEqual(self.frame((), mask=wire.ALL_MASK ^ 1)[0], wire.HOLD)
        self.assertEqual(self.c.stall.phase, 'retreat')
        self.assertEqual(self.frame(())[0], wire.HOLD)
        # Sustained missing feedback (> 30 ms) disarms promptly.
        for _ in range(3):
            mode, _ = self.frame((), mask=wire.ALL_MASK ^ 1)
        self.assertEqual(mode, wire.DISARM)
        self.assertEqual(self.c.mode, 'off')
        self.frame((), enabled=False)
        self.c.set_mode('policy')
        self.frame((), enabled=False)
        self.retreat()
        self.c.set_mode('off')
        self.assertEqual(self.frame((), enabled=False)[0], wire.DISARM)
        self.assertEqual(self.c.stall.phase, 'disarmed')

    def test_uninvolved_joint_outside_default_disarms_after_deadline(self):
        self.retreat()
        self.angles = list(self.c.policy.default_deg)
        self.angles[MOTOR_IDS.index(34)] += 10.01
        while self.now < self.c.stall.deadline:
            mode, _ = self.frame(())
        self.assertEqual(mode, wire.DISARM)
        calls = self.c.servo_feedback_log.record.call_args_list
        self.assertEqual([c.args[0] for c in calls], ['stall_retreat', 'stall_disarmed'])
        self.assertEqual(calls[-1].kwargs['outside_default_ids'], [34])

    def test_two_joints_just_over_five_degrees_disarm_at_four_seconds(self):
        self.retreat()
        self.angles = list(self.c.policy.default_deg)
        self.angles[MOTOR_IDS.index(12)] += 5.01
        self.angles[MOTOR_IDS.index(20)] -= 5.01
        while self.now < self.c.stall.started + 4.:
            mode, _ = self.frame(())
        self.assertEqual(mode, wire.DISARM)
        event = self.c.servo_feedback_log.record.call_args.kwargs
        self.assertEqual(event['outside_default_ids'], [12, 20])
        self.assertLessEqual(abs(event['elapsed_ms'] - 4000), 20)

    def test_overcurrent_can_continue_until_joint_arrives_and_alarm_clears(self):
        self.retreat()
        i = MOTOR_IDS.index(21)
        home = self.c.policy.default_deg
        self.angles = list(home)
        self.angles[i] += 16.
        for _ in range(80):
            self.angles[i] -= .2
            mode, _ = self.frame((21,))
            self.assertEqual(mode, wire.HOLD)
        self.assertEqual(self.c.stall.phase, 'retreat')  # Still alarming at home.
        for _ in range(12):
            self.frame(())
        self.assertEqual(self.c.stall.phase, 'monitor')
        self.assertEqual(self.c.recovery.phase, 'walk')

    def test_overcurrent_uses_each_joints_own_net_progress(self):
        self.retreat()
        i = MOTOR_IDS.index(21)
        for _ in range(30):
            self.angles[i] -= .1  # Leave room for a 3 s stall before the 4 s deadline.
            self.assertEqual(self.frame((21,))[0], wire.HOLD)
        stopped_at = self.now
        last_angle = self.angles[i]
        for tick in range(80):
            self.angles[i] = last_angle + (2. if tick % 2 else 0.)
            self.angles[MOTOR_IDS.index(12)] += .2  # A different servo advances.
            mode, _ = self.frame((21,))
            if mode == wire.DISARM:
                break
        self.assertEqual(mode, wire.DISARM)
        self.assertGreater(self.now - stopped_at, 0.9)
        self.assertLessEqual(self.now - stopped_at, 1.52)
        event = self.c.servo_feedback_log.record.call_args.kwargs
        self.assertEqual(event['sustained_alarm_ids'], [21])
        self.assertIn('without measured progress', event['reason'])
        self.assertAlmostEqual(event['default_error_deg'][21], abs(self.angles[i] - self.c.policy.default_deg[i]), places=2)

    def test_last_servo_can_finish_after_previous_settle_timeout(self):
        self.retreat()
        home = self.c.policy.default_deg
        i = MOTOR_IDS.index(20)
        self.angles = list(home)
        self.angles[i] += 21.
        for _ in range(100):
            self.angles[i] -= .1
            self.assertEqual(self.frame(())[0], wire.HOLD)
        self.assertAlmostEqual(self.angles[i] - home[i], 11.)
        self.assertGreater(self.now - self.c.stall.started, self.c.stall.duration + 1.)
        for _ in range(50):
            self.angles[i] -= .1
            self.frame((), gravity=(0, 1, 0))
        self.assertEqual(self.c.stall.phase, 'monitor')
        self.assertEqual(self.c.recovery.phase, 'getup')

    def test_progress_never_extends_past_four_seconds(self):
        self.retreat()
        i = MOTOR_IDS.index(21)
        self.angles[i] += 30.
        while self.now + .021 < self.c.stall.started + 4.:
            self.angles[i] -= .1
            self.assertEqual(self.frame((21,))[0], wire.HOLD)
        for _ in range(2):
            self.angles[i] -= .1
            self.frame((21,))
        self.assertEqual(self.c.mode, 'off')
        event = self.c.servo_feedback_log.record.call_args.kwargs
        self.assertIn('deadline', event['reason'])
        self.assertLessEqual(abs(event['elapsed_ms'] - 4000), 20)

    def test_motion_cannot_extend_another_fault_bit(self):
        self.retreat()
        i = MOTOR_IDS.index(21)
        for _ in range(45):
            self.angles[i] -= .1
            self.assertEqual(self.frame((21,))[0], wire.HOLD)
        for _ in range(40):
            self.angles[i] -= .1
            self.assertEqual(self.frame((21,), code=0x09)[0], wire.HOLD)
        self.angles[i] -= .1
        self.assertEqual(self.frame((21,), code=0x09)[0], wire.DISARM)
        self.assertIn('persistent servo fault', self.c.servo_feedback_log.record.call_args.kwargs['reason'])

    def test_timeout_without_more_samples_disarms_immediately(self):
        self.retreat()
        self.now += .2
        self.c.step()
        packet = self.c.bridge.notify.call_args.args[1]
        self.assertEqual(wire.COMMAND.unpack(packet)[4], wire.DISARM)

    def test_single_stall_with_invalid_id22_goes_directly_off(self):
        for _ in range(31):
            mode, _ = self.frame((21,), mask=wire.ALL_MASK ^ (1 << MOTOR_IDS.index(22)))
        self.assertEqual(mode, wire.DISARM)
        self.assertEqual(self.c.stall.phase, 'disarmed')
        events = [call.args[0] for call in self.c.servo_feedback_log.record.call_args_list]
        self.assertEqual(events, ['stall_disarmed'])

    def test_gaps_and_moving_encoders_restart_stall_confirmation(self):
        for _ in range(29):
            self.frame((12,))
        self.seq += 10
        self.now += .2
        for _ in range(29):
            self.frame((12,))
        self.assertEqual(self.c.stall.phase, 'idle')
        for _ in range(10):
            self.angles[self.joints[0]] += 2.
            self.frame((12,))  # A stale zero-speed field alone cannot claim a stall.
        self.assertEqual(self.c.stall.phase, 'idle')

    def test_dynamic_high_difficulty_actions_do_not_trip_on_overcurrent(self):
        # Kick (0.4s), stand (1.2s), and roulade (1.7s) must not disarm on pure overcurrent.
        self.c.kick_side = 'left'
        self.c.kick_until = self.now + 0.4
        for _ in range(20):
            self.assertEqual(self.frame((12, 21))[0], wire.POLICY)
        self.assertEqual(self.c.stall.phase, 'idle')
        for _ in range(40):
            self.frame(())

        self.c.sitstand_phase = 'stand'
        self.c.sitstand_until = self.now + 1.2
        for _ in range(60):
            self.assertEqual(self.frame((12, 21))[0], wire.POLICY)
        self.assertEqual(self.c.stall.phase, 'idle')
        for _ in range(80):
            self.frame(())

        self.c._roulade_started_at = self.now
        self.c.roulade_until = self.now + 1.7
        for _ in range(85):
            self.assertEqual(self.frame((12, 21))[0], wire.POLICY)
        self.assertEqual(self.c.stall.phase, 'idle')

    def test_getup_tolerates_longer_push_and_protects_roller_falls(self):
        # Even when roller_mode is enabled, falling enters getup protection (not roller warning-only).
        self.c.set_roller_mode(True)
        self.c.recovery.phase = 'getup'
        # 1.5 s of overcurrent during getup push-off does not interrupt getup.
        for _ in range(75):
            self.assertEqual(self.frame((12,), gravity=(0, 1, 0))[0], wire.POLICY)
        self.assertEqual(self.c.stall.phase, 'idle')
        # Active torso rotation slows stall accumulation so vigorous getup keeps trying.
        self.c.stall.clear_candidates()
        self.gyro = (0.8, 0., 0.)
        for _ in range(110):  # 2.2 s while torso is actively rotating upright.
            self.assertEqual(self.frame((12,), gravity=(0, 1, 0))[0], wire.POLICY)
        self.assertEqual(self.c.stall.phase, 'idle')
        # Genuine static jam during getup (gyro=0) for 2.0 s triggers retreat.
        self.gyro = (0., 0., 0.)
        self.c.stall.clear_candidates()
        for _ in range(100):
            self.assertEqual(self.frame((12,), gravity=(0, 1, 0))[0], wire.POLICY)
        self.assertEqual(self.frame((12,), gravity=(0, 1, 0))[0], wire.HOLD)
        self.assertEqual(self.c.stall.phase, 'retreat')

    def test_speed_quantization_jitter_and_overcurrent_flicker_do_not_defeat_detection(self):
        # Periodic 2-LSB speed jitter (8.79 deg/s) and 1-frame overcurrent drops still confirm stall.
        for tick in range(45):
            alarms = () if tick % 10 == 5 else (12,)
            speed = 8.79 if tick % 7 == 3 else 0.
            mode, _ = self.frame(alarms, speed=speed)
            if self.c.stall.phase == 'retreat':
                break
        self.assertEqual(self.c.stall.phase, 'retreat')
        self.assertEqual(mode, wire.HOLD)
        # During retreat, periodic 1-frame overcurrent drops still disarm on no progress.
        for tick in range(100):
            alarms = () if tick % 8 == 4 else (12,)
            mode, _ = self.frame(alarms)
            if mode == wire.DISARM:
                break
        self.assertEqual(mode, wire.DISARM)
        self.assertIn('without measured progress', self.c.servo_feedback_log.record.call_args.kwargs['reason'])

    def test_small_error_static_overcurrent_and_long_roller_overcurrent_trigger_thermal_retreat(self):
        # < 12 deg error (static heavy load) still triggers thermal retreat at 5.0 s in walk/sit.
        i = MOTOR_IDS.index(21)
        self.angles = list(self.c.policy.default_deg)
        self.targets = list(self.angles)
        self.targets[i] += 8.  # Below MIN_ERROR_DEG (12 deg).
        for _ in range(250):
            self.assertEqual(self.frame((21,))[0], wire.POLICY)
        self.assertEqual(self.frame((21,))[0], wire.HOLD)
        self.assertEqual(self.c.stall.phase, 'retreat')
        self.assertIn('thermal', self.c.servo_feedback_log.record.call_args.kwargs['reason'])

        # In roller mode, 8.0 s of continuous non-stop overcurrent triggers thermal retreat.
        self.c.set_mode('off')
        self.frame((), enabled=False)
        self.c.set_mode('policy')
        self.c.set_roller_mode(True)
        self.frame(())
        for _ in range(400):
            self.assertEqual(self.frame((21,))[0], wire.POLICY)
        self.assertEqual(self.frame((21,))[0], wire.HOLD)
        self.assertEqual(self.c.stall.phase, 'retreat')

    def test_pure_overheat_triggers_recovery_even_in_roller_or_dynamic_and_short_error_packet(self):
        self.c.set_roller_mode(True)
        self.c._roulade_started_at = self.now
        self.c.roulade_until = self.now + 1.7
        for _ in range(40):
            self.assertEqual(self.frame((11,), code=wire.PROTECTION_OVERHEAT)[0], wire.POLICY)
        self.assertEqual(self.frame((11,), code=wire.PROTECTION_OVERHEAT)[0], wire.HOLD)
        self.assertEqual(self.c.stall.phase, 'retreat')
        self.assertIn('hardware servo fault', self.c.servo_feedback_log.record.call_args.kwargs['reason'])

        # Error-only short packet (missing from fb.mask) with overheat disarms directly at 0.8 s.
        self.c.set_mode('off')
        self.frame((), enabled=False)
        self.c.set_mode('policy')
        self.c.set_roller_mode(False)
        self.frame(())
        mask_no_11 = wire.ALL_MASK ^ (1 << MOTOR_IDS.index(11))
        for _ in range(40):
            self.assertEqual(self.frame((11,), mask=mask_no_11, code=wire.PROTECTION_OVERHEAT)[0], wire.POLICY)
        self.assertEqual(self.frame((11,), mask=mask_no_11, code=wire.PROTECTION_OVERHEAT)[0], wire.DISARM)
        self.assertEqual(self.c.stall.phase, 'disarmed')

    def test_mouth_grip_overcurrent_during_pick_never_unloads_legs(self):
        self.c.pick_active = True
        mouth = MOTOR_IDS.index(34)
        self.angles = list(self.c.policy.default_deg)
        self.targets = list(self.angles)
        self.angles[mouth] = 20.
        self.targets[mouth] = 0.
        for _ in range(300):  # 6.0 s of mouth gripping an object under overcurrent.
            self.assertEqual(self.frame((34,))[0], wire.POLICY)
        self.assertEqual(self.c.stall.phase, 'idle')

    def test_monitor_resets_to_idle_after_clean_operation_and_logs_fresh_elapsed_on_restall(self):
        self.assert_resume((0, 0, -1), 'walk')
        for _ in range(50):  # 1.0 s later, still in monitor -> second stall disarms immediately.
            self.frame(())
        for _ in range(32):
            self.frame((12,))
        self.assertEqual(self.c.stall.phase, 'disarmed')
        self.assertEqual(self.c.servo_feedback_log.record.call_args.kwargs['elapsed_ms'], 0)

        # After 45 s of clean operation in monitor, phase resets to idle and allows a new retreat.
        self.c.set_mode('off')
        self.frame((), enabled=False)
        self.c.set_mode('policy')
        self.frame(())
        self.assert_resume((0, 0, -1), 'walk')
        for _ in range(2260):  # > 45 s clean operation at 20 ms/frame.
            self.frame(())
        self.assertEqual(self.c.stall.phase, 'idle')
        self.retreat((12,))
        self.assertEqual(self.c.stall.phase, 'retreat')
