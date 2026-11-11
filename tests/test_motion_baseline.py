"""Exercise the pinned upstream motion stack through the AI boundary without physical I/O."""
import ast
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import controller
import wire
from motion_access import MotionAccess
from rl_core import DEFAULT_DEG


def method_sha256(node):
    """Pin syntax independently of Python 3.11/3.13 AST dump formatting."""
    def canonical(value):
        if isinstance(value, ast.AST):
            return [type(value).__name__, [[key, canonical(item)]
                    for key, item in ast.iter_fields(value) if key != 'type_params']]
        if isinstance(value, list):
            return [canonical(item) for item in value]
        if isinstance(value, bytes):
            return {'bytes': value.hex()}
        return value
    return hashlib.sha256(json.dumps(canonical(node), separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


class BaselineTest(unittest.TestCase):
    def test_motion_sources_models_and_matching_web_match_recorded_baseline(self):
        manifest = json.loads((ROOT / 'provenance/motion.json').read_text())
        for name, expected in manifest['sha256'].items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)
        source = (ROOT / 'sketch/sketch.ino').read_text()
        for line in manifest['sketch']['optional_overlay']:
            self.assertEqual(source.count(line), 1)
            source = source.replace(line+'\n', '')
        bridge = manifest['sketch']['bridge_overlay']
        self.assertEqual(source.count(bridge['integrated']), 1)
        source = source.replace(bridge['integrated'], bridge['upstream'])
        for overlay in manifest['sketch']['servo_test_overlays']:
            self.assertEqual(source.count(overlay['integrated']), 1)
            source = source.replace(overlay['integrated'], overlay['upstream'])
        for overlay in manifest['sketch'].get('runtime_gains_overlays', []):
            self.assertEqual(source.count(overlay['integrated']), overlay.get('count', 1))
            source = source.replace(overlay['integrated'], overlay['upstream'])
        for overlay in manifest['sketch'].get('transport_overlays', []):
            self.assertEqual(source.count(overlay['integrated']), 1)
            source = source.replace(overlay['integrated'], overlay['upstream'])
        for overlay in manifest['mcu_compatibility']['battery_overlays']:
            self.assertEqual(source.count(overlay['integrated']), 1)
            source = source.replace(overlay['integrated'], overlay['upstream'])
        self.assertEqual(hashlib.sha256(source.encode()).hexdigest(), manifest['sketch']['source_sha256'])

    def test_motion_math_models_and_control_methods_keep_upstream_implementation(self):
        manifest = json.loads((ROOT / 'provenance/motion.json').read_text())
        for name in manifest['stable_exact_files']:
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(),
                                 manifest['source']['sha256'][name])
        for name, overlays in manifest.get('speech_mouth_overlay', {}).get('source_overlays', {}).items():
            with self.subTest(mouth_overlay=name):
                source = (ROOT / name).read_text()
                for overlay in overlays:
                    self.assertEqual(source.count(overlay['integrated']), 1)
                    source = source.replace(overlay['integrated'], overlay['upstream'])
                self.assertEqual(hashlib.sha256(source.encode()).hexdigest(),
                                 manifest['source']['sha256'][name])
        node = next(n for n in ast.parse((ROOT / 'python/controller.py').read_text()).body
                    if isinstance(n, ast.ClassDef) and n.name == 'Controller')
        actual = {n.name: method_sha256(n)
                  for n in node.body if isinstance(n, ast.FunctionDef)}
        for name, digest in manifest['stable_controller_methods'].items():
            with self.subTest(method=name):
                self.assertEqual(actual[name], digest)
        for name, digest in manifest['preserved_sha256'].items():
            with self.subTest(preserved=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), digest)

    def test_existing_mcu_normal_control_matches_stable_and_preserves_wire_abi(self):
        provenance = json.loads((ROOT / 'provenance/motion.json').read_text())
        manifest = provenance['mcu_compatibility']
        self.assertEqual(wire.STATE.format, manifest['state_format'])
        self.assertEqual(wire.COMMAND.format, manifest['command_format'])
        source = (ROOT / 'sketch/sketch.ino').read_text()
        start = source.index('static void controlTask(')
        task = source[start:source.index('\nstatic void reportTask(', start)]
        servo_test_overlays = [o for o in provenance['sketch']['servo_test_overlays'] if o.get('control_task')]
        for overlay in servo_test_overlays + manifest['gain_call_overlays'] + manifest['battery_overlays']:
            self.assertEqual(task.count(overlay['integrated']), overlay.get('count', 1))
            task = task.replace(overlay['integrated'], overlay['upstream'])
        self.assertEqual(hashlib.sha256(task.encode()).hexdigest(), manifest['normal_control_task_sha256'])

    def make_motion(self, enabled, synchronized_clock=False):
        self.now, self.seq = 20., 0
        if synchronized_clock:
            timer = patch('controller.time.monotonic', side_effect=lambda: self.now)
            timer.start()
            self.addCleanup(timer.stop)
        self.c = controller.Controller(Mock())  # Actual five ONNX sessions.
        self.access = MotionAccess(self.c, clock=lambda: self.now)
        self.feedback(enabled)
        self.access.heartbeat(101)

    def feedback(self, enabled=True):
        self.seq += 1
        sample = self.seq * 10000
        raw = wire.STATE.pack(b'DQS1', 1, 3 if enabled else 1, wire.ALL_MASK,
            self.seq, sample, sample, self.c.seq, 0, 10000, 500, 0, 0, 0,
            0, 0, -16384, 0, 0, 0, *DEFAULT_DEG, *([0.] * 15), *DEFAULT_DEG)
        self.c.receive(raw)

    def battery_tick(self, millivolts, *, enabled=True, elapsed=.125):
        self.now += elapsed
        self.feedback(enabled)
        self.c.receive_diagnostics(wire.DIAGNOSTIC_V2.pack(b'DQD2', *([0] * 16), millivolts))
        self.c.step()

    def test_feedback_pause_preserves_torque_and_resumes_original_mode(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        for mode, command_mode in (('hold', wire.HOLD), ('policy', wire.POLICY)):
            with self.subTest(mode=mode):
                self.c.set_mode(mode)
                self.c.command(twist=[.1, 0., 0.])
                self.c.step()
                self.c.bridge.notify.reset_mock()
                self.now += .16
                self.c.step()
                self.now += .5
                self.c.step()
                self.c.bridge.notify.assert_not_called()
                self.assertTrue(self.c.fault_hold)
                self.assertEqual(self.c.mode, mode)

                self.feedback()
                self.c.step()
                command = wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])
                self.assertEqual(command[4], command_mode)
                self.assertEqual(command[2], self.c.feedback.sample_us)
                self.assertEqual(self.c.mode, mode)
                self.assertFalse(self.c.fault_hold)
                self.assertEqual(self.c.last_error, '')
                self.assertEqual(self.c.twist, [0., 0., 0.])
                self.assertEqual(self.c.command(twist=[.1, 0., 0.]), {'ok': True})

    def test_feedback_pause_preserves_sitting_and_cancels_queued_stand(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('policy')
        self.c.set_sitstand('sit')
        self.c.action_preparation['warm_passes'] = 1
        self.now += .21
        self.feedback()
        self.c.step()
        self.assertEqual(self.c.sitstand_phase, 'sit')
        self.c.set_sitstand('stand')

        self.now += .16
        self.c.step()
        self.feedback()
        self.c.step()
        self.assertFalse(self.c.fault_hold)
        self.assertIsNone(self.c.action_preparation)
        self.assertEqual(self.c.sitstand_phase, 'sit')
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_sitstand.onnx')

    def test_feedback_return_reports_actual_mcu_disarm_without_rearming(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('hold')
        self.now += .16
        self.c.step()
        self.feedback(enabled=False)
        self.c.step()
        self.assertEqual(self.c.mode, 'shadow')
        self.assertFalse(self.c.fault_hold)
        self.assertEqual(self.c.last_error, 'MCU released torque during the feedback pause')
        self.assertEqual(wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4], wire.DISARM)

    def test_battery_warns_once_then_disarms_and_requires_recovery(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('policy')
        self.c.battery_warning = Mock()
        with patch('controller.threading.Thread') as audio:
            for _ in range(40):
                self.battery_tick(6000)
            self.assertEqual(self.c.status()['battery'], dict(voltage_v=6.0, low=False, phase='normal'))
            self.battery_tick(6001)
            self.assertIsNone(self.c.battery_since)
            for _ in range(40):
                self.battery_tick(6000)
            self.assertEqual(self.c.battery_phase, 'normal')
            self.battery_tick(6000)
            self.assertEqual(self.c.battery_phase, 'warning')
            audio.assert_called_once_with(target=self.c._play_battery_warning,
                                          args=(self.c.battery_warning_done,),
                                          name='battery-warning', daemon=True)
            self.assertEqual(self.c.status()['battery'], dict(voltage_v=6.0, low=True, phase='warning'))
            with self.assertRaisesRegex(ValueError, 'Low battery'):
                self.c.set_mode('hold')
            for _ in range(64):
                self.battery_tick(6000)
            self.assertEqual(self.c.battery_phase, 'warning')
            self.assertEqual(self.c.sitstand_phase, 'idle')
            self.c._play_battery_warning(self.c.battery_warning_done)
            self.c.battery_warning.assert_called_once_with()
            self.battery_tick(6000)
            self.assertEqual(self.c.battery_phase, 'locked')
            self.assertEqual(wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4], wire.DISARM)
            for _ in range(20):
                self.battery_tick(7500, enabled=False)
            self.assertEqual(self.c.battery_phase, 'locked')
            for _ in range(17):
                self.battery_tick(7600, enabled=False)
            self.assertEqual(self.c.battery_phase, 'normal')
            self.assertEqual(self.c.mode, 'off')
            audio.assert_called_once()

    def test_battery_disarms_if_warning_never_finishes(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('hold')
        self.c.battery_warning = Mock()
        with patch('controller.threading.Thread'):
            for _ in range(41):
                self.battery_tick(6000)
            self.assertEqual(self.c.battery_phase, 'warning')
            self.assertFalse(self.c.battery_warning_done.is_set())
            for _ in range(int(controller.BATTERY_WARNING_TIMEOUT_S / .125)):
                self.battery_tick(6000)
        self.assertEqual(self.c.battery_phase, 'locked')
        self.assertEqual(wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4], wire.DISARM)

    def test_battery_does_not_arm_unpowered_servos_and_ignores_missing_voltage(self):
        self.make_motion(enabled=False, synchronized_clock=True)
        self.c.receive_diagnostics(wire.DIAGNOSTIC.pack(b'DQD1', *([0] * 16)))
        self.assertIsNone(self.c.status()['battery']['voltage_v'])
        for _ in range(41):
            self.battery_tick(6000, enabled=False)
        self.assertEqual(self.c.battery_phase, 'locked')
        self.assertEqual(self.c.sitstand_phase, 'idle')
        self.assertTrue(self.c.bridge.notify.call_args_list)
        for command in self.c.bridge.notify.call_args_list:
            self.assertEqual(wire.COMMAND.unpack(command.args[1])[4], wire.DISARM)

    def test_stand_uses_restored_signature_then_arms_at_measured_pose(self):
        self.make_motion(enabled=False)
        result = self.access.start_action(kind='stand', session=101)
        self.assertEqual(result['status'], 'accepted')
        self.c.step()
        command = wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])
        self.assertEqual(command[4], wire.ARM)
        self.assertEqual(list(command[7:]), list(self.c.feedback.angles))
        self.feedback(enabled=True)
        self.c.step()
        self.assertEqual(wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4], wire.POLICY)
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_walk.onnx')

    def test_ten_second_voice_move_uses_walk_and_stops_inputs_on_deadline(self):
        # Advance the controller's input timeout and the voice lease together.
        # A fast loop with real wall time would miss the one-second cutoff.
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('policy')
        self.c.step()
        self.access.state()
        for _ in range(22):
            self.now += .05
            self.feedback()
            self.c.step()
            self.access.heartbeat(101)
            self.access.tick()
        result = self.access.start_action(kind='move', twist=[.2, 0, 0], duration=10., session=101)
        self.assertEqual(result['status'], 'accepted')
        self.assertAlmostEqual(self.access.action['deadline']-self.access.action['started_at'], 10.)
        self.c.step()
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_walk.onnx')
        self.assertEqual(self.c.twist, [.2, 0., 0.])
        deadline = self.access.action['deadline']
        # Keep the actual standing observation/feedback alive during the lease.
        for _ in range(200):
            self.now += .05
            self.feedback()
            self.c.step()
            self.access.heartbeat(101)
            self.access.tick()
            self.assertEqual(self.access.action['deadline'], deadline)
            if self.now < deadline:
                self.assertEqual(self.access.action['state'], 'running')
                self.assertEqual(self.c.twist, [.2, 0., 0.])
        self.now += .01
        self.feedback()
        self.access.heartbeat(101)
        self.access.tick()
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.mode, 'policy')

    def test_web_input_expiry_stops_travel_and_keeps_balance(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('policy')
        self.c.command(twist=[.2, 0., 0.])
        self.now += 1.1
        self.feedback()
        self.c.step()
        self.assertEqual(self.c.twist, [0., 0., 0.])
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4], wire.POLICY)

    def test_stale_voice_session_does_not_renew_stable_input_timeout(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('policy')
        self.access.start_action(kind='stand', session=101)
        self.now += .9
        self.feedback()
        self.access.tick()
        self.assertEqual(self.access.action['state'], 'cancelled')
        self.assertEqual(self.c.input_source, 'web')
        self.assertEqual(self.c.twist, [0., 0., 0.])
        cancelled_input_time = self.c.web_time
        self.now += 2.
        self.feedback()
        self.access.tick()
        self.assertEqual(self.c.web_time, cancelled_input_time)

    def test_stable_defaults_and_roller_selection_keep_fixed_encoder_center(self):
        self.make_motion(enabled=True)
        self.assertFalse(self.c.roller_mode)
        self.assertEqual(self.c.policy.file, 'xgoduck_walk.onnx')
        self.assertEqual(self.c.policy.action_alpha_head, .45)
        encoder_center = self.c.status()['encoder_center']
        self.c.set_roller_mode(True)
        self.assertEqual(self.c.policy.file, 'xgoduck_roller.onnx')
        self.assertEqual(self.c.policy.action_scale, .8)
        self.assertNotEqual(self.c.policy.default_deg, DEFAULT_DEG)
        self.c.set_roller_mode(False)
        self.assertEqual(self.c.policy.default_deg, DEFAULT_DEG)
        self.assertEqual(self.c.status()['encoder_center'], encoder_center)

    def test_hardware_raw_feedback_never_pushes_software_center_commands(self):
        self.make_motion(enabled=False)
        self.c.receive_raw(wire.RAW.pack(b'DQR2', wire.ALL_MASK, 0, *([2047] * 15)))
        self.assertEqual(self.c.raw_pos, [2047] * 15)
        self.assertEqual(self.c.status()['encoder_center'], 2047)
        self.c.bridge.notify.assert_not_called()

    def test_web_roller_switch_retires_queued_voice_commands_without_disarming(self):
        self.make_motion(enabled=True)
        self.access.start_action(kind='stand', session=101)
        self.access.start_action(kind='move', duration=2., twist=[.2, 0, 0], session=101)
        result = self.access.web_roller(True)
        self.assertTrue(result['enabled'])
        self.assertEqual(self.c.policy.file, 'xgoduck_roller.onnx')
        self.assertEqual(self.c.input_source, 'web')
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.c.twist, [0., 0., 0.])
        self.assertFalse(self.access.pending)
        self.assertEqual(self.access.action['state'], 'cancelled')

    def test_web_voice_and_gamepad_use_master_walking_bounds(self):
        self.make_motion(enabled=True)
        for source in ('web', 'voice', 'gamepad'):
            self.c.input_source = source
            for vx in (-.2, .2):
                self.c.command(twist=[vx, 0., 0.], source=source)
                self.assertEqual(self.c.twist[0], vx)
            for vx in (-.201, .201):
                with self.assertRaisesRegex(ValueError, 'walking vx'):
                    self.c.command(twist=[vx, 0., 0.], source=source)
        self.c.set_roller_mode(True)
        for vx in (-.4, .4):
            self.c.command(twist=[vx, 0., 0.], source='gamepad')
            self.assertEqual(self.c.twist[0], vx)

    def test_real_sitstand_policy_returns_to_walking_after_stand(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('policy')
        self.c.step()
        self.access.web_sitstand('sit')
        self.finish_preparation()
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_sitstand.onnx')
        self.assertEqual(self.c.sitstand_phase, 'sit')
        self.access.web_sitstand('stand')
        self.finish_preparation()
        self.now += 1.01
        self.feedback()
        self.c.step()
        self.assertEqual(self.c.sitstand_phase, 'idle')
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_walk.onnx')

    def test_heading_hold_reaches_both_walking_policies(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('policy')
        for skating, kp in ((False, 1.5), (True, 1.0)):
            with self.subTest(skating=skating):
                self.c.set_roller_mode(skating)
                self.now += .02
                self.feedback()
                self.assertTrue(self.c.heading.status()['initialized'])
                head = [.1, .2, 0., 0.]
                self.c.command(twist=[.1, 0., 0.], head=head)
                self.c.heading.yaw += math.radians(10.)
                with patch.object(self.c.policy, 'infer', wraps=self.c.policy.infer) as infer:
                    self.c.step()
                    twist, pose = infer.call_args.args[-2:]
                    self.assertAlmostEqual(twist[2], -kp * math.radians(10.))
                    self.assertEqual(pose, [0.] * 4 if skating else head)
                    self.c.command(twist=[.1, 0., .2])
                    self.c.step()
                    self.assertEqual(infer.call_args.args[-2][2], .2)
                    self.assertFalse(self.c.heading.status()['active'])

    def test_real_kick_policies_suppress_travel_and_return_to_walking(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('policy')
        self.c.step()
        for side in ('left', 'right'):
            self.access.web_action('kick', side)
            self.finish_preparation()
            self.c.command(twist=[.2, 0., 0.])
            self.assertEqual(self.c.twist, [0., 0., 0.])
            self.c.step()
            self.assertEqual(self.c.status()['active_model'], f'xgoduck_ballkick_{side}.onnx')
            self.now += .41
            self.feedback()
            self.c.step()
            self.assertEqual(self.c.status()['active_model'], 'xgoduck_walk.onnx')

    def test_web_sit_can_follow_voice_motion_without_a_mode_change(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('policy')
        for _ in range(23):
            self.now += .05
            self.feedback()
            self.c.step()
            self.access.heartbeat(101)
            self.access.tick()
        self.access.start_action(kind='move', twist=[.1, 0., 0.], session=101)
        self.access.web_sitstand('sit')
        self.assertEqual(self.c.twist, [0., 0., 0.])
        self.assertEqual(self.c.input_source, 'web')
        self.assertEqual(self.access.action['state'], 'cancelled')
        self.finish_preparation()
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_sitstand.onnx')

    def finish_preparation(self):
        self.c._keep_warm_idle()
        self.now += controller.ACTION_PREPARE_S + .01
        self.feedback()
        self.c.step()

    def test_voice_kick_runs_full_window_after_preparation(self):
        self.make_motion(enabled=True, synchronized_clock=True)
        self.c.set_mode('policy')
        for _ in range(23):
            self.now += .05
            self.feedback()
            self.c.step()
            self.access.heartbeat(101)
            self.access.tick()
        self.access.start_action(kind='left_kick', session=101)
        self.access.tick()
        self.assertTrue(self.c.status()['action_preparation']['active'])
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_walk.onnx')
        self.finish_preparation()
        self.access.tick()
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_ballkick_left.onnx')
        self.now += .25
        self.feedback()
        self.c.step()
        self.access.tick()
        self.assertEqual(self.access.action['state'], 'running')
        self.now += .16
        self.feedback()
        self.c.step()
        self.access.tick()
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertEqual(self.c.status()['active_model'], 'xgoduck_walk.onnx')


class HardwareCenterMigrationTest(unittest.TestCase):
    def test_migration_keeps_hardware_center_angle_origin_and_never_overwrites(self):
        spec = importlib.util.spec_from_file_location('center_migration', ROOT / 'tools/migrate-hardware-centers.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'data/zero_pos.json'
            result = module.migrate(target)
            self.assertEqual(result['zero_pos'], [2047]*15)
            self.assertEqual(result['ids'], list(controller.MOTOR_IDS))
            before = target.read_bytes()
            with self.assertRaises(FileExistsError):
                module.migrate(target)
            self.assertEqual(target.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
