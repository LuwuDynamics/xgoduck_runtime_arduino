"""Forward ToF limits with a simulated clock; no physical motor commands."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'python'))
from forward_avoidance import ForwardAvoidance, GuardedWalkingPolicy, load_settings
from motion_access import COCKPIT_LEASE_S, MotionAccess


ABSENT = dict(state='absent', fresh=False, seq=0, age_ms=0xffffffff)
OFFLINE = dict(state='offline', fresh=False, seq=2, age_ms=1600,
               error_message='Distance frame I2C read failed')


def frame(distance=1000, seq=1, **changes):
    return dict(state='ranging', fresh=True, seq=seq, age_ms=10,
                grid_mm=[[distance]*8 for _ in range(8)], **changes)


class ForwardAvoidanceTest(unittest.TestCase):
    def setUp(self):
        self.now = 10.
        self.monitor = Mock()
        self.guard = ForwardAvoidance(self.monitor, clock=lambda: self.now)

    def test_forward_slows_and_stops_but_backward_sideways_turning_are_unchanged(self):
        self.guard.update(frame(125))
        self.assertEqual(self.guard.limit([.2, .1, .5]), [.1, .1, .5])
        self.guard.update(frame(80, 2))
        self.assertEqual(self.guard.limit([.2, .1, .5]), [0, .1, .5])
        self.assertEqual(self.guard.limit([-.18, .1, .5]), [-.18, .1, .5])
        self.assertEqual(self.guard.limit([0, 0, 1]), [0, 0, 1])
        self.monitor.status.assert_not_called()  # All control calls use a cache.

    def test_current_far_frame_clears_a_previous_near_reading(self):
        self.guard.update(frame(100))
        self.assertFalse(self.guard.status()['forward_ready'])
        self.guard.update(frame(1000, 2))
        self.assertTrue(self.guard.status()['forward_ready'])

    def test_exact_ten_fifteen_cm_boundaries_and_31cm_uses_full_requested_speed(self):
        for mm, expected in [(310, .2), (150, .2), (125, .1), (101, .004), (100, 0), (99, 0)]:
            guard = ForwardAvoidance(self.monitor, clock=lambda: self.now)
            guard.update(frame(mm))
            with self.subTest(mm=mm):
                self.assertAlmostEqual(guard.limit([.2, 0, 0])[0], expected)
                self.assertEqual(guard.status()['stop_distance_mm'], 100)
                self.assertEqual(guard.status()['slow_distance_mm'], 150)

    def test_move_away_from_near_obstacle_slows_then_allows_full_speed(self):
        self.guard.update(frame(100))
        self.guard.update(frame(125, 2))
        self.assertEqual(self.guard.limit([.2, 0, 0]), [.1, 0, 0])
        self.guard.update(frame(150, 3))
        self.assertEqual(self.guard.limit([.2, 0, 0]), [.2, 0, 0])

    def test_only_side_returns_allow_forward_and_a_single_valid_front_return_is_used(self):
        data = frame(None)
        for row in data['grid_mm']:
            row[0:2] = [749, 1029]
        self.guard.update(data)
        self.assertFalse(self.guard.status()['available'])
        self.assertTrue(self.guard.status()['forward_ready'])
        self.assertEqual(self.guard.limit([.15, 0, 0]), [.15, 0, 0])
        for seq, mm, expected in ((2, 800, .2), (3, 125, .1), (4, 80, 0), (5, None, .2)):
            data['seq'] = seq
            data['grid_mm'][3][3] = mm
            self.guard.update(data)
            with self.subTest(mm=mm):
                self.assertEqual(self.guard.status()['front_distance_mm'], mm)
                self.assertEqual(self.guard.limit([.2, 0, 0])[0], expected)

    def test_stale_near_reading_no_longer_limits_forward(self):
        self.guard.update(frame(80))
        self.assertFalse(self.guard.status()['forward_ready'])
        self.now += .7
        status = self.guard.status()
        self.assertFalse(status['available'])
        self.assertTrue(status['forward_ready'])
        self.assertIsNone(status['front_distance_mm'])
        self.assertEqual(status['blocking_reason'], '')
        self.assertEqual(self.guard.limit([.2, 0, 0]), [.2, 0, 0])
        self.assertEqual(self.guard.limit([-.1, 0, 0]), [-.1, 0, 0])

    def test_absent_and_offline_allow_forward_and_valid_frames_restore_avoidance(self):
        for sensor in (ABSENT, OFFLINE):
            with self.subTest(state=sensor['state']):
                # An obstacle from the last ranging frame cannot block manual travel offline.
                self.guard.update(frame(80))
                self.guard.update(sensor)
                status = self.guard.status()
                self.assertEqual(status['state'], sensor['state'])
                self.assertFalse(status['available'])
                self.assertTrue(status['forward_ready'])
                self.assertIsNone(status['front_distance_mm'])
                self.assertEqual(status['blocking_reason'], '')
                self.assertEqual(self.guard.limit([.2, .1, .5]), [.2, .1, .5])
                self.guard.update(frame(80, 3))
                self.assertEqual(self.guard.limit([.2, .1, .5]), [0, .1, .5])

    def test_unknown_state_errors_and_invalid_measurements_do_not_limit_forward(self):
        self.guard.update(ABSENT)
        for data in ({}, dict(state='unavailable', error_message='Router timed out'),
                     dict(state='error', error_message='sensor initialization failed'),
                     frame(None), frame(0), frame(float('nan')), frame(True)):
            with self.subTest(data=str(data)[:100]):
                self.guard.update(data)
                self.assertTrue(self.guard.status()['forward_ready'])
                self.assertIsNone(self.guard.status()['front_distance_mm'])
        for field, value in [('fresh', False), ('seq', None), ('seq', 0), ('seq', True),
                             ('age_ms', None), ('age_ms', float('inf')), ('age_ms', -1),
                             ('age_ms', 1001), ('state', 'initializing')]:
            data = frame(80); data[field] = value
            self.guard.update(data)
            self.assertTrue(self.guard.status()['forward_ready'], (field, value))

    def test_default_region_ignores_edge_floor_but_uses_nearest_valid_front_zone(self):
        data = frame(20)
        for row in range(2, 6):
            for col in range(2, 6):
                data['grid_mm'][row][col] = 800
        self.guard.update(data)
        self.assertEqual(self.guard.status()['front_distance_mm'], 800)
        data['seq'] = 2; data['grid_mm'][3][3] = 80
        self.guard.update(data)
        self.assertFalse(self.guard.status()['forward_ready'])

    def test_faster_roller_velocity_gets_larger_stopping_distance(self):
        self.guard.update(frame(125))
        status = self.guard.preview(.4)
        self.assertEqual(status['stop_distance_mm'], 250)
        self.assertFalse(status['forward_ready'])

    def test_bad_config_fails_closed_and_explicit_disable_allows_missing_sensor(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'settings.json'
            self.assertTrue(load_settings(path)['enabled'])
            for values in ({'enabled': 'false'}, {'stop_mm': 0}, {'slow_mm': float('nan')},
                           {'rows': [-1, 8]}, {'columns': [4, 4]}, {'min_valid_zones': 17},
                           {'new_unknown_option': True}):
                path.write_text(json.dumps(values))
                bad = ForwardAvoidance(self.monitor, settings_path=path, clock=lambda: self.now)
                bad.update(frame())
                self.assertFalse(bad.status()['forward_ready'], values)
                self.assertIn('配置无效', bad.status()['blocking_reason'])
            path.write_text('{bad')
            bad = ForwardAvoidance(self.monitor, settings_path=path)
            bad.update(ABSENT)
            self.assertFalse(bad.status()['forward_ready'])
        disabled = ForwardAvoidance(self.monitor, settings={'enabled': False})
        self.assertTrue(disabled.status()['forward_ready'])
        disabled.start()
        self.assertIsNone(disabled.thread)

    def test_policy_proxy_preserves_model_attributes_reset_and_output(self):
        policy = Mock(action_scale=.8)
        policy.infer.return_value = [42]*14
        wrapped = GuardedWalkingPolicy(policy, self.guard)
        wrapped.action_scale = 1.1
        self.assertEqual(policy.action_scale, 1.1)
        wrapped.warm(2); policy.warm.assert_called_once_with(2)
        self.guard.update(frame(125))
        self.assertEqual(wrapped.infer('g', 'w', 'q', 'v', [.2, .1, 0], [0]*4), [42]*14)
        self.assertEqual(policy.infer.call_args.args[4], [.1, .1, 0])
        self.assertEqual(wrapped.infer('g', 'w', 'q', 'v', [-.1, 0, 0], [0]*4), [42]*14)
        self.assertEqual(policy.infer.call_args.args[4], [-.1, 0, 0])

    def test_headless_guard_renews_monitor_lease_and_shuts_down(self):
        read = threading.Event()
        self.monitor.status.side_effect = lambda: (read.set() or frame())
        self.guard.start()
        try:
            self.assertTrue(read.wait(1))
            self.guard.thread.join(.12)
            self.assertGreaterEqual(self.monitor.status.call_count, 2)
            self.assertTrue(self.guard.status()['forward_ready'])
        finally:
            self.guard.close()
        self.assertFalse(self.guard.thread.is_alive())


@unittest.skipUnless(importlib.util.find_spec('onnxruntime'), 'Real ONNX runtime not available')
class ForwardControllerTest(unittest.TestCase):
    def setUp(self):
        from motion_calibration import AppController
        import wire
        from rl_core import DEFAULT_DEG
        self.wire, self.defaults = wire, DEFAULT_DEG
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = AppController(Mock(), data_dir=Path(self.temp.name))
        self.guard = ForwardAvoidance(Mock())
        self.guard.update(frame())
        self.c.attach_forward_avoidance(self.guard)
        self.feedback()
        self.c.mode, self.c.input_source = 'policy', 'web'
        self.c.recovery.phase = 'walk'
        self.now = 10.
        self.access = MotionAccess(self.c, clock=lambda: self.now)

    def feedback(self, *, enabled=True):
        c, w = self.c, self.wire
        seq = c.received + 1
        c.receive(w.STATE.pack(b'DQS1', 1, 3 if enabled else 1, w.ALL_MASK, seq, seq*10000, seq*10000,
            c.seq, 0, 10000, 500, 0, 0, 0, 0, 0, -16384, 0, 0, 0,
            *self.defaults, *([0.]*15), *self.defaults))

    def test_block_during_live_control_keeps_torque_and_does_not_replay_old_input(self):
        self.c.command(twist=[.2, .1, .5])
        self.guard.update(frame(80, 2))
        self.feedback(); self.c.step()
        self.assertEqual(self.c.twist, [0, .1, .5])
        self.assertEqual(self.c.mode, 'policy')
        self.assertTrue(self.c.feedback.enabled)
        self.assertNotEqual(self.wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4], self.wire.DISARM)
        self.guard.update(frame(None, 3))
        self.assertTrue(self.guard.status()['forward_ready'])
        self.feedback(); self.c.step()
        self.assertEqual(self.c.twist[0], 0)
        self.assertTrue(self.c.command(twist=[.2, 0, 0])['forward_blocked'])
        self.c.command(twist=[0, 0, 0])  # Joystick/key release.
        self.assertNotIn('forward_blocked', self.c.command(twist=[.2, 0, 0]))
        self.assertEqual(self.c.twist[0], .2)

    def test_forward_rejection_ignores_inactive_owner_and_allows_reverse_and_turn(self):
        self.guard.update(frame(80, 2))
        self.assertIn('ignored', self.c.command(twist=[.2, 0, 0], source='gamepad'))
        self.assertEqual(self.guard.status()['stop_count'], 0)
        self.assertTrue(self.c.command(twist=[.2, 0, 0])['forward_blocked'])
        self.assertEqual(self.c.twist[0], 0)
        self.assertNotIn('forward_blocked', self.c.command(twist=[-.1, 0, 1]))
        self.assertEqual(self.c.twist, [-.1, 0, 1])
        self.assertEqual(self.c.mode, 'policy')

    def test_walk_and_roller_wrappers_leave_all_special_policy_objects_untouched(self):
        self.assertIsInstance(self.c.walk_policy, GuardedWalkingPolicy)
        self.c.set_roller_mode(True)
        self.assertIs(self.c.policy, self.c.roller_policy)
        for p in (self.c.getup_policy, self.c.sitstand_policy, self.c.pick_policy,
                  self.c.roulade_policy, *self.c.kick_policies.values()):
            self.assertNotIsInstance(p, GuardedWalkingPolicy)
        self.guard.update(frame(80, 2))
        self.c.set_sitstand('sit')
        self.finish_preparation()
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_sitstand.onnx')
        self.assertEqual(self.c.sitstand_phase, 'sit')

    def test_stop_during_onnx_drops_the_obsolete_action_before_bridge_send(self):
        self.c.command(twist=[.2, 0, 0])
        policy = self.c.walk_policy._policy
        original = policy.infer
        def infer(*args):
            # An input callback arrives while the existing model is computing.
            self.guard.update(frame(80, 2))
            self.c.command(twist=[.2, 0, 0])
            return original(*args)
        policy.infer = infer
        self.c.bridge.notify.reset_mock()
        self.feedback(); self.c.step()
        self.c.bridge.notify.assert_not_called()
        self.assertEqual(self.c.twist[0], 0)
        self.assertEqual(self.c.mode, 'policy')
        policy.infer = original
        self.feedback(); self.c.step()
        self.c.bridge.notify.assert_called_once()

    def ready_cockpit(self):
        self.c.recovery.angle_deg = 0.
        for _ in range(23):
            self.now += .05
            self.feedback()
            self.access.tick()

    def test_offline_tof_passes_forward_commands_from_web_cockpit_voice_and_phone(self):
        self.guard.update(OFFLINE)
        self.ready_cockpit()
        twist = [.1, 0., 0.]
        self.assertNotIn('forward_blocked', self.access.web_command(twist=twist))
        self.assertEqual(self.c.twist, twist)
        self.access.web_command(twist=[0, 0, 0])
        driver = 'a' * 24
        reply = self.access.cockpit_motion(driver, 1, 'begin', twist)
        self.assertNotIn('forward_blocked', reply)
        self.assertEqual(self.c.twist, twist)
        self.access.cockpit_motion(driver, 2, 'release')

        self.access.allowed = self.access.remote_allowed = lambda: True
        self.access.heartbeat(101)
        for remote in ({}, dict(remote=True, controller_id='phone-1234', sequence=1)):
            result = self.access.start_action('move', session=101, twist=twist, duration=.7, **remote)
            self.assertEqual(result['status'], 'accepted')
            self.assertEqual(self.c.twist, twist)
            self.access.start_action('stop', session=101)

    def test_cockpit_stops_on_release_expiry_and_feedback_loss(self):
        self.ready_cockpit()
        cockpit = self.access.cockpit_motion
        driver, other = 'a' * 24, 'b' * 24
        twist = [.12, -.08, .45]
        self.guard.update(frame())
        self.assertTrue(cockpit(driver, 1, 'begin', twist)['ok'])
        self.assertEqual(self.c.twist, twist)
        self.assertIn('ignored', self.access.web_command(twist=[0, 0, 0]))
        with self.assertRaisesRegex(ValueError, 'Another cockpit'):
            cockpit(other, 1, 'begin', twist)
        cockpit(other, 2, 'release')
        self.assertEqual(self.c.twist, twist)
        cockpit(driver, 3, 'release')
        self.assertEqual(self.c.twist, [0, 0, 0])
        with self.assertRaisesRegex(ValueError, 'retired'):
            cockpit(driver, 2, 'drive', twist)
        cockpit(driver, 4, 'begin', twist)
        expires = self.now + COCKPIT_LEASE_S
        while self.now <= expires:
            self.now += .05
            self.feedback()
            self.access.tick()
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.input_source, 'web')
        self.assertEqual(self.c.mode, 'policy')
        with self.assertRaisesRegex(ValueError, 'timed out'):
            cockpit(driver, 5, 'drive', twist)
        cockpit(driver, 6, 'begin', twist)
        self.c.rx_time -= .2
        self.access.tick()
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.input_source, 'web')

    def test_cockpit_tof_blocks_only_forward_until_the_stick_is_released(self):
        self.ready_cockpit()
        cockpit = self.access.cockpit_motion
        driver, twist = 'a' * 24, [.1, .04, -.5]
        self.guard.update(frame(80, 2))
        self.assertTrue(cockpit(driver, 1, 'begin', twist)['forward_blocked'])
        self.assertEqual(self.c.twist, [0, .04, -.5])
        self.guard.update(OFFLINE)
        self.assertTrue(self.guard.status()['forward_ready'])
        self.assertTrue(cockpit(driver, 2, 'drive', twist)['forward_blocked'])
        cockpit(driver, 3, 'release')
        self.assertNotIn('forward_blocked', cockpit(driver, 4, 'begin', twist))
        self.assertEqual(self.c.twist, twist)
        self.guard.update(frame(80, 5))
        self.assertTrue(cockpit(driver, 5, 'drive', twist)['forward_blocked'])
        self.guard.update(frame(None, 6))
        self.assertNotIn('forward_blocked', cockpit(driver, 6, 'drive', [-.1, 0, .5]))
        self.assertEqual(self.c.twist, [-.1, 0, .5])

    def test_cockpit_buttons_select_the_existing_policies_without_voice(self):
        driver = 'a' * 24
        self.c.set_mode('shadow')
        self.feedback()
        cockpit = self.access.cockpit_motion
        self.assertTrue(cockpit(driver, 1, 'stand')['ok'])
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.ready_cockpit()
        models = {'pick':'xgoduck_pick.onnx', 'roulade':'xgoduck_roulade.onnx',
                  'left_kick':'xgoduck_ballkick_left.onnx', 'right_kick':'xgoduck_ballkick_right.onnx'}
        for sequence, (kind, model) in enumerate(models.items(), start=1):
            self.feedback()
            self.assertTrue(cockpit(driver, 2*sequence, kind)['ok'])
            self.finish_preparation()
            self.assertEqual(self.c.status()['active_model'], model)
            cockpit(driver, 2*sequence+1, 'stop')
            self.assertEqual(self.c.status()['active_model'], 'xgoduck_walk.onnx')
            self.assertEqual(self.c.mouth, 0.)
        cockpit(driver, 10, 'sit')
        self.finish_preparation()
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_sitstand.onnx')
        cockpit(driver, 11, 'stand')
        self.finish_preparation()
        self.assertEqual(self.c.status()['sitstand']['phase'], 'stand')
        self.c.battery_phase = 'locked'
        with self.assertRaisesRegex(ValueError, 'Low battery'):
            cockpit(driver, 12, 'stand')
        self.assertEqual(cockpit(driver, 13, 'torque_off')['mode'], 'off')

    def test_cockpit_pose_buttons_preserve_other_axes_and_respect_motion_state(self):
        self.ready_cockpit()
        cockpit, driver = self.access.cockpit_motion, 'a' * 24
        self.c.command(twist=[.1, 0., 0.], head=[.1, -.2, 0., .4])
        for sequence, (kind, yaw) in enumerate(
                [('head_left', .3), ('head_right', -.3), ('head_center', 0.)], 1):
            self.assertTrue(cockpit(driver, sequence, kind)['ok'])
            self.assertEqual(self.c.head, [.1, -.2, yaw, .4])
            self.assertEqual(self.c.twist, [.1, 0., 0.])
        for sequence, (kind, mouth) in enumerate([('mouth_open', 20.), ('mouth_close', 0.)], 4):
            self.assertTrue(cockpit(driver, sequence, kind)['ok'])
            self.assertEqual(self.c.mouth, mouth)
            self.assertEqual(self.c.head, [.1, -.2, 0., .4])
        self.c.set_roller_mode(True)
        with self.assertRaisesRegex(ValueError, 'require Walk'):
            cockpit(driver, 6, 'head_left')
        self.c.set_mode('shadow')
        self.feedback(enabled=False)
        with self.assertRaisesRegex(ValueError, '尚未使能'):
            cockpit(driver, 7, 'mouth_open')
        self.assertEqual(cockpit(driver, 8, 'default_pose')['mode'], 'hold')

    def finish_preparation(self):
        pending = self.c.action_preparation
        pending['ready_at'] = pending['requested_at']
        self.c._keep_warm_idle()
        self.feedback()
        self.c.step()

    def test_cockpit_stand_rearms_in_policy_and_waits_for_stm32_ack(self):
        with patch('motion_calibration.time.monotonic', side_effect=lambda: self.now):
            self.feedback(enabled=False)
            self.c.recovery.phase = 'getup'
            self.c.recovery.angle_deg = 110.
            self.assertTrue(self.access.cockpit_motion('a' * 24, 1, 'stand')['ok'])
            self.assertTrue(self.c.status()['arming'])
            for _ in range(2):
                self.now += .02
                self.feedback(enabled=False)
                self.c.step()
                packet = self.wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])
                self.assertEqual(packet[4], self.wire.ARM)
                self.assertEqual(list(packet[7:]), list(self.c.feedback.angles))
            self.now += .02
            self.feedback(enabled=True)
            self.c.step()
            self.assertFalse(self.c.status()['arming'])
            self.assertEqual(self.wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4],
                             self.wire.POLICY)
            self.now += .02
            self.feedback(enabled=False)
            self.c.step()
            self.assertEqual(self.wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4],
                             self.wire.POLICY)

    def test_unconfirmed_arming_stops_on_timeout_or_cockpit_stop(self):
        from motion_calibration import ARM_CONFIRM_S
        with patch('motion_calibration.time.monotonic', side_effect=lambda: self.now):
            cockpit = self.access.cockpit_motion
            for sequence, timed_out in enumerate((True, False), start=1):
                self.feedback(enabled=False)
                cockpit('a' * 24, sequence * 2, 'stand')
                self.c.step()
                self.assertTrue(self.c.status()['arming'])
                if timed_out:
                    self.now += ARM_CONFIRM_S + .02
                else:
                    cockpit('a' * 24, sequence * 2 + 1, 'stop')
                self.feedback(enabled=False)
                self.c.step()
                self.assertEqual(self.c.mode, 'shadow')
                self.assertFalse(self.c.status()['arming'])
                self.assertEqual(self.c.status()['arm_failed'], timed_out)
                self.assertEqual(self.wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4],
                                 self.wire.DISARM)


if __name__ == '__main__':
    unittest.main()
