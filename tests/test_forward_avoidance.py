"""Forward ToF limits with a simulated clock; no physical motor commands."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'python'))
from forward_avoidance import ForwardAvoidance, GuardedWalkingPolicy, load_settings


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

    def test_obstacle_release_needs_three_distinct_clear_frames(self):
        self.guard.update(frame(100))
        self.assertFalse(self.guard.status()['forward_ready'])
        self.guard.update(frame(1000, 2))
        for _ in range(10):
            self.guard.update(frame(1000, 2))
        self.assertFalse(self.guard.status()['forward_ready'])
        self.guard.update(frame(1000, 3))
        self.assertFalse(self.guard.status()['forward_ready'])
        self.guard.update(frame(1000, 4))
        self.assertTrue(self.guard.status()['forward_ready'])

    def test_exact_ten_fifteen_cm_boundaries_and_31cm_uses_full_requested_speed(self):
        for mm, expected in [(310, .2), (150, .2), (125, .1), (101, .004), (100, 0), (99, 0)]:
            guard = ForwardAvoidance(self.monitor, clock=lambda: self.now)
            guard.update(frame(mm))
            with self.subTest(mm=mm):
                self.assertAlmostEqual(guard.limit([.2, 0, 0])[0], expected)
                self.assertEqual(guard.status()['stop_distance_mm'], 100)
                self.assertEqual(guard.status()['slow_distance_mm'], 150)

    def test_release_at_fifteen_cm_needs_three_frames_without_old_forty_cm_lock(self):
        self.guard.update(frame(100))
        self.guard.update(frame(149, 2))
        self.assertFalse(self.guard.status()['forward_ready'])
        for seq in (3, 4):
            self.guard.update(frame(150, seq))
            self.assertFalse(self.guard.status()['forward_ready'])
        self.guard.update(frame(150, 5))
        self.assertTrue(self.guard.status()['forward_ready'])
        self.assertEqual(self.guard.limit([.2, 0, 0]), [.2, 0, 0])

    def test_invalid_frame_breaks_clear_confirmation(self):
        self.guard.update(frame(80))
        self.guard.update(frame(1000, 2))
        self.guard.update(frame(None, 3))
        for seq in (4, 5):
            self.guard.update(frame(1000, seq))
            self.assertFalse(self.guard.status()['forward_ready'])
        self.guard.update(frame(1000, 6))
        self.assertTrue(self.guard.status()['forward_ready'])

    def test_stale_age_stops_forward_even_when_background_worker_is_stuck(self):
        self.guard.update(frame())
        self.now += .7
        status = self.guard.status()
        self.assertFalse(status['forward_ready'])
        self.assertIn('过期', status['blocking_reason'])
        self.assertEqual(self.guard.limit([.2, 0, 0]), [0, 0, 0])
        self.assertEqual(self.guard.limit([-.1, 0, 0]), [-.1, 0, 0])

    def test_absent_error_invalid_coverage_and_invalid_metadata_fail_closed(self):
        for data in ({}, dict(state='unavailable', error_message='sensor disconnected'),
                     frame(None), frame(0), frame(float('nan')), frame(True)):
            with self.subTest(data=str(data)[:100]):
                self.guard.update(data)
                self.assertFalse(self.guard.status()['forward_ready'])
                self.assertIsNone(self.guard.status()['front_distance_mm'])
        for field, value in [('fresh', False), ('seq', None), ('seq', 0), ('seq', True),
                             ('age_ms', None), ('age_ms', float('inf')), ('age_ms', -1),
                             ('age_ms', 1001), ('state', 'initializing')]:
            data = frame(); data[field] = value
            self.guard.update(data)
            self.assertFalse(self.guard.status()['forward_ready'], (field, value))

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
            self.assertFalse(ForwardAvoidance(self.monitor, settings_path=path).status()['forward_ready'])
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

    def feedback(self):
        c, w = self.c, self.wire
        seq = c.received + 1
        c.receive(w.STATE.pack(b'DQS1', 1, 3, w.ALL_MASK, seq, seq*10000, seq*10000,
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
        for seq in (3, 4, 5): self.guard.update(frame(1000, seq))
        self.assertTrue(self.guard.status()['forward_ready'])
        self.feedback(); self.c.step()
        self.assertEqual(self.c.twist[0], 0)
        self.assertTrue(self.c.command(twist=[.2, 0, 0])['forward_blocked'])
        self.c.command(twist=[0, 0, 0])  # Joystick/key release.
        self.assertNotIn('forward_blocked', self.c.command(twist=[.2, 0, 0]))
        self.assertEqual(self.c.twist[0], .2)

    def test_forward_rejection_ignores_inactive_owner_and_allows_reverse_and_turn(self):
        self.guard.update(frame(None, 2))
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
        self.c.set_sitstand('sit'); self.feedback(); self.c.step()
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


if __name__ == '__main__':
    unittest.main()
