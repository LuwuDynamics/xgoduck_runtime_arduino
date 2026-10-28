import importlib.util
import collections
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
sys.path.insert(0, str(ROOT / 'python/vendor/luwu_os/apps/voice_chat'))
from motion_access import MotionAccess
from local_ipc import JsonServer
from duck_thing import DuckThing
from iot.thing import ThingManager
from mcp_server import McpServer

spec = importlib.util.spec_from_file_location('web_fixture', ROOT / 'tools/test-web-control.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
fixture.ns['MOUTH_MAX_DEG'] = 30
fixture.ns['ROULADE_S'] = 1.9
fixture.ns['ROULADE_ALPHA'] = .15


class VoiceMotionTest(unittest.TestCase):
    def setUp(self):
        setup = fixture.WebControlTest()
        setup.setUp()
        self.c = setup.c
        self.c.input_source = 'web'
        self.c.mode = 'policy'
        self.c.recovery.angle_deg = 0.
        self.c.feedback.mask = 0x7fff
        self.motion_status = dict(full_robot_ready=True, mcu_watchdog=False, mcu_command_age_us=10000)
        self.c.status = Mock(side_effect=lambda: dict(self.motion_status, mode=self.c.mode,
            enabled=self.c.feedback.enabled, recovery=self.c.recovery.status(),
            fault_hold=self.c.fault_hold, fault_hold_supported=bool(self.c.feedback.flags & 32),
            arming=self.c.arm_pending or self.c._arm_ack_seq is not None,
            pick={'active': self.c.pick_active}, roulade={'active': bool(self.c.roulade_until)}))
        self.now, self.allowed = 99., True
        self.access = MotionAccess(self.c, allowed=lambda: self.allowed, clock=lambda: self.now)
        self.access.heartbeat(101)
        self.access.tick()
        self.advance(1.)

    def advance(self, seconds):
        end = self.now + seconds
        while self.now < end:
            self.now = min(end, self.now + .05)
            self.access.heartbeat(101)
            self.access.tick()

    def move(self, **changes):
        return self.access.start_action(**({'kind': 'move', 'twist': [.08, 0, 0], 'duration': 2, 'session': 101} | changes))

    def ack_arm(self):
        command = fixture.wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])
        self.assertEqual(command[4], fixture.wire.ARM)
        self.c.filter = Mock(update=Mock(return_value=self.c.filtered))
        self.c.rx_times, self.c.rx_intervals = collections.deque(), collections.deque()
        self.c.received = self.c.gaps = 0
        if not hasattr(self.c.feedback, 'seq'): self.c.feedback.seq = 100
        ack = fixture.wire.STATE.pack(b'DQS1', 1, 35, 0x7fff,
            self.c.feedback.seq + 1, 123, 123, command[1], 0, 10000, 500, 0, 0, 0,
            0, 0, -16384, 0, 0, 0, *([0.] * 45))
        self.c.receive(ack)

    def mcp(self):
        def dispatch(method, path, body):
            if path == '/action':
                return self.access.start_action(**body)
            if path == '/state':
                return {'motion': self.c.status(), 'control': self.access.state()}
            raise ValueError('Unknown path')
        server = JsonServer(0, 'test-token', dispatch)
        server.start()
        self.addCleanup(server.close)
        manager = ThingManager()
        manager.add_thing(DuckThing(server.server.server_port, 'test-token', session=101, clock=lambda: self.now))
        return McpServer(manager)

    def test_stand_arms_at_measured_pose_then_runs_zero_speed_balance(self):
        self.c.mode = 'shadow'
        self.c.feedback.enabled = False
        self.c.twist = [.08, -.04, .2]
        result = self.access.start_action(kind='stand', session=101)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(result['duration_s'], 5.)
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.c.step()
        payload = self.c.bridge.notify.call_args.args[1]
        command = fixture.wire.COMMAND.unpack(payload)
        self.assertEqual(command[4], fixture.wire.ARM)
        self.assertEqual(list(command[7:]), self.c.feedback.angles)
        self.ack_arm()
        self.c.step()
        self.assertEqual(fixture.wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4], fixture.wire.POLICY)
        self.advance(1.1)
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.c.twist, [0, 0, 0])

    def test_stand_already_in_policy_cancels_special_actions_and_keeps_recovery(self):
        self.c.mode = 'policy'
        self.c.recovery.phase = 'getup'
        self.c.pick_active = True
        self.c.roulade_until = 1000
        self.c.twist, self.c.head, self.c.mouth = [.08, 0, .2], [.2] * 4, 20
        self.access.start_action(kind='stand', session=101)
        self.assertFalse(self.c.pick_active)
        self.assertEqual(self.c.roulade_until, 0)
        self.assertEqual(self.c.recovery.phase, 'getup')
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.head, [0] * 4)
        self.assertEqual(self.c.mouth, 0)

    def test_fresh_sensor_feedback_with_stale_command_link_rejects_motion(self):
        for fault in ({'mcu_command_age_us': 150001}, {'mcu_watchdog': True},
                      {'mcu_command_age_us': 2**32 - 1}):
            self.motion_status = dict(full_robot_ready=True, **fault)
            for kind in ('stand', 'move'):
                with self.subTest(fault=fault, kind=kind):
                    with self.assertRaisesRegex(ValueError, 'command link'):
                        self.move(kind=kind)
                    self.assertEqual(self.c.mode, 'policy')
                    self.assertEqual(self.c.input_source, 'web')
                    self.assertFalse(self.c.arm_pending)

    def test_stand_keeps_permission_ownership_and_remote_guards(self):
        self.allowed = False
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.access.start_action(kind='stand', session=101)
        self.allowed = True
        self.c.input_source = 'gamepad'
        with self.assertRaisesRegex(ValueError, 'Gamepad'):
            self.access.start_action(kind='stand', session=101)
        self.c.input_source, self.c.mode = 'web', 'calibrate'
        with self.assertRaisesRegex(ValueError, 'calibration'):
            self.access.start_action(kind='stand', session=101)
        self.c.mode = 'hold'
        with self.assertRaisesRegex(ValueError, 'unsupported remote'):
            self.access.start_action(kind='stand', session=101, remote=True,
                                     controller_id='phone_session_A', sequence=1)
        self.motion_status.update(mcu_watchdog=False, mcu_command_age_us=150000)
        self.access.start_action(kind='stand', session=101)
        self.assertEqual(self.c.twist, [0, 0, 0])

    def test_mcp_stand_is_explicit_and_permission_error_is_readable_to_cloud(self):
        mcp = self.mcp()
        tools = mcp.handle_message({'id': 1, 'method': 'tools/list'})['result']['tools']
        stand = next(t for t in tools if t['name'] == 'Duck.Stand')
        self.assertEqual(stand['inputSchema']['required'], [])
        self.assertEqual(stand['inputSchema']['properties'], {})
        self.assertLess(len(json.dumps(tools, ensure_ascii=False).encode()), 8192)
        self.allowed = False
        reply = mcp.handle_message({'id': 2, 'method': 'tools/call', 'params': {'name': 'Duck.Stand'}})['result']
        self.assertTrue(reply['isError'])
        # The deployed cloud reads result.error before result.content on failure.
        self.assertIn('开启语音运动', reply['error'])
        self.assertEqual(json.loads(reply['content'][0]['text'])['message'], reply['error'])
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.c.input_source, 'web')
        self.allowed = True
        reply = mcp.handle_message({'id': 3, 'method': 'tools/call', 'params': {'name': 'Duck.Stand'}})['result']
        self.assertFalse(reply['isError'])
        self.assertNotIn('error', reply)
        self.assertEqual(self.access.action['kind'], 'stand')
        self.assertEqual(self.c.twist, [0, 0, 0])

    def test_full_mcp_tools_list_with_camera_stays_within_gateway_limit(self):
        mcp = self.mcp()
        # Production also exposes the two permission tools when settings exist.
        mcp.thing_manager.add_thing(DuckThing(0, 'unused', settings=Mock(), sound_player=Mock()))
        mcp.self_provider = Mock()
        mcp.self_provider.mcp_camera_available.return_value = True
        reply = mcp.handle_message({'id': 1, 'method': 'tools/list', 'params': {'withUserTools': True}})
        self.assertLess(len(json.dumps(reply, ensure_ascii=False).encode()), 8192)
        self.assertIn('Duck.Stand', {t['name'] for t in reply['result']['tools']})
        self.assertIn('Duck.Quack', {t['name'] for t in reply['result']['tools']})

    def test_mcp_status_reports_stale_command_link_and_actual_error(self):
        self.motion_status.update(mcu_watchdog=True, mcu_command_age_us=1000000)
        mcp = self.mcp()
        status = mcp.handle_message({'id': 1, 'method': 'tools/call', 'params': {'name': 'Duck.GetStatus'}})
        data = json.loads(status['result']['content'][0]['text'])
        self.assertFalse(data['ready'])
        self.assertFalse(data['command_link_ready'])
        self.assertTrue(data['enabled'])
        reply = mcp.handle_message({'id': 2, 'method': 'tools/call', 'params': {'name': 'Duck.Stand'}})['result']
        self.assertTrue(reply['isError'])
        self.assertIn('STM32', reply['error'])

    def test_mcp_status_keeps_motion_action_out_of_cloud_reply_envelope(self):
        mcp = self.mcp()
        for moving in (False, True):
            if moving:
                self.move()
            with self.subTest(moving=moving):
                reply = mcp.handle_message({'id': 1, 'method': 'tools/call',
                    'params': {'name': 'Duck.GetStatus'}})['result']
                self.assertFalse(reply['isError'])
                data = json.loads(reply['content'][0]['text'])
                # Xiaozhi reserves top-level action for its Action enum.
                # null/dict here would be parsed as a reply instruction.
                self.assertNotIn('action', data)
                self.assertEqual(data['motion_action'], self.access.action)
                self.assertEqual(data['status'], 'success')

    def test_mcp_forward_with_voice_permission_off_returns_reason_without_moving(self):
        self.allowed = False
        before = (self.c.mode, self.c.input_source, list(self.c.twist), self.c.bridge.notify.call_count)
        reply = self.mcp().handle_message({'id': 1, 'method': 'tools/call',
            'params': {'name': 'Duck.Move', 'arguments': {'direction': 'forward', 'speed': .05, 'seconds': 2}}})['result']
        self.assertTrue(reply['isError'])
        self.assertIn('语音运动未开启', reply['error'])
        self.assertNotEqual(reply['error'], 'None')
        self.assertEqual(before, (self.c.mode, self.c.input_source, self.c.twist, self.c.bridge.notify.call_count))

    def test_direction_keys_never_arm_a_disarmed_fallen_or_recovering_duck(self):
        self.access.remote_allowed = lambda: True
        states = [('shadow', False, 'walk', 0), ('off', False, 'walk', 0),
                  ('hold', True, 'walk', 0), ('policy', False, 'walk', 0),
                  ('policy', True, 'default_pose', 60), ('policy', True, 'getup', 5),
                  ('policy', True, 'walk', 16), ('policy', True, 'walk', 60), ('policy', True, 'walk', None),
                  ('policy', True, 'walk', float('nan'))]
        for index, (mode, enabled, phase, angle) in enumerate(states):
            self.c.mode, self.c.feedback.enabled = mode, enabled
            self.c.recovery.phase, self.c.recovery.angle_deg = phase, angle
            for remote in (False, True):
                with self.subTest(mode=mode, enabled=enabled, phase=phase, angle=angle, remote=remote):
                    before = self.c.bridge.notify.call_count
                    with self.assertRaisesRegex(ValueError, 'Motion not ready'):
                        self.move(remote=remote, duration=.7,
                                  controller_id='phone_guard_' + str(index), sequence=1)
                    self.assertEqual(self.c.mode, mode)
                    self.assertEqual(self.c.input_source, 'web')
                    self.assertFalse(self.c.arm_pending)
                    self.assertEqual(self.c.bridge.notify.call_count, before)

    def test_explicit_stand_and_continuous_upright_feedback_precede_remote_move(self):
        self.access.remote_allowed = lambda: True
        self.c.mode, self.c.feedback.enabled = 'shadow', False
        self.access.start_action(kind='stand', session=101)
        self.c.step()  # The explicit Stand, rather than a direction key, sends ARM.
        self.ack_arm()
        self.c.recovery.phase, self.c.recovery.angle_deg = 'getup', 65
        self.advance(.2)
        with self.assertRaisesRegex(ValueError, 'Standing in progress'):
            self.move(remote=True, duration=.7, controller_id='phone_session_A', sequence=1)
        self.c.recovery.phase, self.c.recovery.angle_deg = 'walk', 5
        self.advance(.5)
        with self.assertRaisesRegex(ValueError, 'Standing in progress'):
            self.move(remote=True, duration=.7, controller_id='phone_session_A', sequence=2)
        self.advance(.6)
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertTrue(self.access.state()['movement_ready'])
        self.move(remote=True, duration=.7, controller_id='phone_session_A', sequence=3)
        self.assertEqual(self.c.twist, [.08, 0, 0])
        self.assertFalse(self.c.arm_pending)

    def test_falling_during_remote_lease_stops_and_retires_controller(self):
        self.access.remote_allowed = lambda: True
        base = dict(remote=True, duration=.7, controller_id='phone_session_A')
        self.move(**base, sequence=1)
        self.c.recovery.angle_deg = 60
        self.access.tick()
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.access.action['state'], 'cancelled')
        self.assertEqual(self.c.mode, 'policy')
        self.c.recovery.angle_deg = 0
        self.advance(1.1)
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.move(**base, sequence=10)
        self.move(remote=True, duration=.7, controller_id='phone_session_B', sequence=1)
        self.assertEqual(self.c.twist, [.08, 0, 0])

    def test_normal_gait_tilt_does_not_cancel_an_already_started_move(self):
        self.move(duration=3)
        for angle in (12, 16, 24, 8):
            self.c.recovery.angle_deg = angle
            self.advance(.3)
            self.assertEqual(self.access.action['state'], 'running')
            self.assertEqual(self.c.twist, [.08, 0, 0])
            self.assertTrue(self.access.state()['movement_ready'])
        self.advance(2)
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertEqual(self.c.twist, [0, 0, 0])

    def test_gait_still_stops_on_fall_recovery_unknown_pose_or_disabled_motors(self):
        for enabled, phase, angle in [(True, 'walk', 55), (True, 'getup', 5),
                                      (True, 'walk', None), (True, 'walk', float('nan')),
                                      (False, 'walk', 5)]:
            self.c.feedback.enabled = True
            self.c.recovery.phase, self.c.recovery.angle_deg = 'walk', 5
            self.advance(1.1)
            self.move()
            with self.subTest(enabled=enabled, phase=phase, angle=angle):
                self.c.feedback.enabled = enabled
                self.c.recovery.phase, self.c.recovery.angle_deg = phase, angle
                self.access.tick()
                self.assertEqual(self.access.action['state'], 'cancelled')
                self.assertEqual(self.c.twist, [0, 0, 0])
                self.assertEqual(self.c.mode, 'policy')

    def test_action_status_and_logs_report_command_elapsed_time_and_stop_reason(self):
        from unittest.mock import patch
        with patch('builtins.print') as output:
            self.move(duration=3)
            self.advance(.4)
            self.access.cancel('stopped by voice')
        records = [json.loads(call.args[1]) for call in output.call_args_list
                   if call.args[0] == '[motion-access]']
        self.assertEqual([r['state'] for r in records], ['running', 'cancelled'])
        self.assertEqual(records[0]['id'], records[1]['id'])
        self.assertEqual(records[1]['command_twist'], [.08, 0, 0])
        self.assertAlmostEqual(records[1]['elapsed_s'], .4)
        self.assertEqual(records[1]['reason'], 'stopped by voice')
        self.assertEqual(self.access.state()['action']['elapsed_s'], .4)

    def test_remote_can_renew_during_normal_gait_but_new_controller_needs_standing(self):
        self.access.remote_allowed = lambda: True
        base = dict(remote=True, duration=.7, controller_id='phone_session_A')
        self.move(**base, sequence=1)
        self.c.recovery.angle_deg = 22
        self.advance(.3)
        self.move(**base, sequence=2)
        self.advance(.5)
        self.assertEqual(self.access.action['state'], 'running')
        self.assertEqual(self.c.twist, [.08, 0, 0])
        self.access.start_action(kind='stop', session=101, remote=True,
                                 controller_id='phone_session_A', sequence=3)
        with self.assertRaisesRegex(ValueError, 'Motion not ready'):
            self.move(remote=True, duration=.7, controller_id='phone_session_B', sequence=1)
        self.c.recovery.angle_deg = 5
        self.advance(1.1)
        self.move(remote=True, duration=.7, controller_id='phone_session_B', sequence=2)

    def test_mcp_voice_speeds_match_existing_web_inputs_and_keep_bounds(self):
        mcp = self.mcp()
        commands = [('Duck.Move', {'direction': 'forward', 'speed': .2, 'seconds': 2}, [.2, 0, 0]),
                    ('Duck.Move', {'direction': 'backward', 'speed': .4, 'seconds': 2}, [-.4, 0, 0]),
                    ('Duck.Move', {'direction': 'left', 'speed': .3, 'seconds': 2}, [0, .3, 0]),
                    ('Duck.Move', {'direction': 'right', 'speed': .15, 'seconds': 2}, [0, -.15, 0]),
                    ('Duck.Turn', {'yaw_rate': 1., 'seconds': 2}, [0, 0, 1.])]
        for name, arguments, twist in commands:
            with self.subTest(name=name, arguments=arguments):
                reply = mcp.handle_message({'id': 1, 'method': 'tools/call',
                    'params': {'name': name, 'arguments': arguments}})['result']
                self.assertFalse(reply['isError'])
                self.assertEqual(self.c.twist, twist)
                self.assertFalse(self.c.arm_pending)
                self.access.cancel('test')
        for direction, speed in [('forward', .401), ('backward', .401), ('left', .301),
                                 ('right', .301), ('forward', float('nan')), ('forward', True)]:
            with self.subTest(direction=direction, speed=speed):
                reply = mcp.handle_message({'id': 2, 'method': 'tools/call',
                    'params': {'name': 'Duck.Move', 'arguments':
                               {'direction': direction, 'speed': speed, 'seconds': 2}}})['result']
                self.assertTrue(reply['isError'])
                self.assertEqual(self.c.twist, [0, 0, 0])
        self.access.remote_allowed = lambda: True
        with self.assertRaisesRegex(ValueError, 'bounds'):
            self.move(twist=[.2, 0, 0], remote=True, duration=.7,
                      controller_id='phone_session_A', sequence=1)

    def test_command_link_loss_during_remote_lease_cancels_velocity(self):
        self.access.remote_allowed = lambda: True
        self.move(remote=True, duration=.7, controller_id='phone_session_A', sequence=1)
        self.motion_status['mcu_command_age_us'] = 150001
        self.access.tick()
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.access.action['state'], 'cancelled')
        self.assertIn('link lost', self.access.action['reason'])
        self.assertFalse(self.access.state()['movement_ready'])

    def test_stand_timeout_does_not_report_standing_success(self):
        self.c.mode, self.c.feedback.enabled = 'shadow', False
        self.access.start_action(kind='stand', session=101)
        self.advance(5.1)
        self.assertEqual(self.access.action['state'], 'failed')
        self.assertIn('Standing not confirmed', self.access.action['reason'])
        self.assertFalse(self.access.state()['movement_ready'])
        self.assertEqual(self.c.twist, [0, 0, 0])

    def test_bounded_action_and_deadline_keep_balance(self):
        result = self.move()
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.c.twist, [.08, 0, 0])
        self.assertIn('ignored', self.access.web_command(twist=[0, 0, 0]))
        self.advance(2)
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertEqual(self.c.mode, 'policy')

    def test_crash_or_stale_heartbeat_zeros_motion_without_disarming(self):
        self.move()
        self.now += .81
        self.access.tick()
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.input_source, 'web')
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.access.action['state'], 'cancelled')

    def test_invalid_requests_never_arm_or_steal_ownership(self):
        for body in ({'twist': [float('nan'), 0, 0]}, {'twist': [.401, 0, 0]},
                     {'twist': [0, .301, 0]}, {'twist': [0, 0, 1.001]},
                     {'twist': [0, 0]}, {'twist': [True, 0, 0]}, {'duration': 10.001},
                     {'duration': True}, {'duration': float('inf')}, {'session': 101.0}, {'session': True}):
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self.access.start_action(**({'kind': 'move', 'twist': [.08, 0, 0], 'duration': 2, 'session': 101} | body))
                self.assertEqual(self.c.input_source, 'web')
                self.assertEqual(self.c.mode, 'policy')

    def test_remote_renews_lease_and_stop_rejects_late_commands(self):
        self.access.remote_allowed = lambda: True
        base = dict(kind='move', twist=[.04, 0, 0], duration=.7, session=101,
                    remote=True, controller_id='phone_session_A')
        self.access.start_action(**base, sequence=1)
        self.advance(.3)
        self.access.start_action(**(base | {'twist': [0, 0, .2]}), sequence=3)
        self.advance(.45)
        self.assertEqual(self.access.action['state'], 'running')
        self.assertEqual(self.c.twist, [0, 0, .2])
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.access.start_action(**base, sequence=2)
        self.access.start_action(kind='stop', session=101, remote=True,
                                 controller_id='phone_session_A', sequence=5)
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.access.start_action(**base, sequence=4)
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.mode, 'policy')

    def test_remote_permission_is_separate_and_expiry_stops_with_balance(self):
        base = dict(kind='move', twist=[.04, 0, 0], duration=.7, session=101,
                    remote=True, controller_id='phone_session_A', sequence=1)
        with self.assertRaisesRegex(ValueError, 'Remote motion disabled'):
            self.access.start_action(**base)
        self.access.remote_allowed = lambda: True
        self.access.start_action(**base)
        self.advance(.71)
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.mode, 'policy')
        self.access.start_action(**(base | {'sequence': 2}))
        self.now += .81
        self.access.tick()
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.access.action['state'], 'cancelled')

    def test_previous_remote_stop_does_not_cancel_new_controller_or_voice_owner(self):
        self.access.remote_allowed = lambda: True
        self.access.start_action(kind='move', twist=[.04, 0, 0], duration=.7, session=101,
            remote=True, controller_id='phone_session_B', sequence=1)
        self.access.start_action(kind='stop', session=101, remote=True,
                                 controller_id='phone_session_A', sequence=2)
        self.assertEqual(self.c.twist, [.04, 0, 0])
        self.assertEqual(self.access.action['state'], 'running')
        self.access.cancel('test')
        self.move()
        self.access.start_action(kind='stop', session=101, remote=True,
                                 controller_id='phone_session_A', sequence=3)
        self.assertEqual(self.c.twist, [.08, 0, 0])

    def test_manual_mode_retires_remote_page_even_for_newer_inflight_leases(self):
        self.access.remote_allowed = lambda: True
        body = dict(kind='move', twist=[.04, 0, 0], duration=.7, session=101,
                    remote=True, controller_id='phone_session_A', sequence=1)
        self.access.start_action(**body)
        self.access.web_mode('hold')
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.access.start_action(**(body | {'sequence': 100}))
        self.assertEqual(self.c.mode, 'hold')

    def test_permission_gamepad_calibration_feedback_and_running_guards(self):
        self.allowed = False
        with self.assertRaisesRegex(ValueError, 'disabled'):
            self.move()
        self.allowed = True
        self.c.input_source = 'gamepad'
        with self.assertRaisesRegex(ValueError, 'Gamepad'):
            self.move()
        self.c.input_source, self.c.mode = 'web', 'calibrate'
        with self.assertRaisesRegex(ValueError, 'calibration'):
            self.move()
        self.c.mode = 'policy'
        self.motion_status['full_robot_ready'] = False
        with self.assertRaisesRegex(ValueError, 'not ready'):
            self.move()
        self.motion_status['full_robot_ready'] = True
        self.move()
        self.access.remote_allowed = lambda: True
        with self.assertRaisesRegex(ValueError, 'running'):
            self.move(remote=True, duration=.7, controller_id='phone_session_A', sequence=1)

    def test_mcp_ten_second_move_is_one_bounded_action(self):
        reply = self.mcp().handle_message({'id': 1, 'method': 'tools/call', 'params':
            {'name': 'Duck.Move', 'arguments': {'direction': 'forward', 'speed': .2, 'seconds': 10}}})['result']
        self.assertFalse(reply['isError'])
        self.assertEqual(self.access.action['duration_s'], 10)
        self.advance(9.9)
        self.assertEqual(self.access.action['state'], 'running')
        self.assertEqual(self.c.twist, [.2, 0, 0])
        self.advance(.2)
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertAlmostEqual(self.access.action['elapsed_s'], 10, delta=.051)
        self.assertEqual(self.c.twist, [0, 0, 0])

    def test_mcp_head_failure_names_missing_servo_without_claiming_imu_or_hardware_failure(self):
        self.motion_status.update(full_robot_ready=False, imu_ok=True,
                                  missing_joint_ids=[33], feedback_age_ms=5.)
        before = (self.c.mode, self.c.input_source, list(self.c.head), list(self.c.twist),
                  self.c.arm_pending, self.c.bridge.notify.call_count)
        mcp = self.mcp()
        reply = mcp.handle_message({'id': 1, 'method': 'tools/call', 'params':
            {'name': 'Duck.SetHead', 'arguments': {'head4': .1}}})['result']
        self.assertTrue(reply['isError'])
        message = json.loads(reply['content'][0]['text'])['message']
        self.assertIn('33 号舵机无有效位置反馈', message)
        self.assertIn('IMU 正常', message)
        self.assertIn('原因尚未确认', message)
        for diagnosis in ('过热', '过载', '损坏', '断线'):
            self.assertNotIn(diagnosis, message)
        self.assertEqual(before, (self.c.mode, self.c.input_source, self.c.head, self.c.twist,
                                 self.c.arm_pending, self.c.bridge.notify.call_count))
        status = mcp.handle_message({'id': 2, 'method': 'tools/call', 'params':
            {'name': 'Duck.GetStatus'}})['result']
        data = json.loads(status['content'][0]['text'])
        self.assertTrue(data['ready'])
        self.assertEqual(data['missing_joint_ids'], [33])
        self.assertTrue(data['imu_ok'])
        self.assertIn('33 号舵机', data['feedback_warning'])
        self.assertEqual(data['error'], '')
        self.assertTrue(data['head_feedback_ready']['head3'])
        self.assertFalse(data['head_feedback_ready']['head4'])
        self.assertNotIn('33 号舵机', data['movement_block_reason'])

    def test_mcp_head_axes_are_explicit_and_yaw_request_keeps_other_goals_zero(self):
        mcp = self.mcp()
        tools = mcp.handle_message({'id': 1, 'method': 'tools/list'})['result']['tools']
        head = next(tool for tool in tools if tool['name'] == 'Duck.SetHead')
        properties = head['inputSchema']['properties']
        self.assertIn('颈部俯仰', properties['head1']['description'])
        self.assertIn('头部俯仰', properties['head2']['description'])
        self.assertIn('左右转头', properties['head3']['description'])
        self.assertIn('侧倾', properties['head4']['description'])
        self.assertIn('不会自动回中', head['description'])
        self.assertEqual(head['inputSchema']['required'], [])
        reply = mcp.handle_message({'id': 2, 'method': 'tools/call', 'params':
            {'name': 'Duck.SetHead', 'arguments': {'head1': 0, 'head2': 0, 'head3': .1, 'head4': 0}}})['result']
        self.assertFalse(reply['isError'])
        self.assertEqual(self.c.head, [0, 0, .1, 0])
        self.assertEqual(self.c.twist, [0, 0, 0])

    def missing_servos(self, *ids):
        self.motion_status.update(full_robot_ready=not ids, imu_ok=True,
                                  missing_joint_ids=list(ids), feedback_age_ms=5.)

    def test_missing_33_allows_partial_yaw_without_changing_other_head_targets(self):
        self.missing_servos(33)
        self.c.head = [.1, -.2, 0., -.3]
        mcp = self.mcp()
        reply = mcp.handle_message({'id': 1, 'method': 'tools/call', 'params':
            {'name': 'Duck.SetHead', 'arguments': {'head3': .2}}})['result']
        self.assertFalse(reply['isError'])
        self.assertEqual(self.c.head, [.1, -.2, .2, -.3])
        self.assertEqual(self.access.action['required_joint_ids'], [32])
        self.advance(.5)
        self.assertEqual(self.access.action['state'], 'running')
        self.assertEqual(self.c.head, [.1, -.2, .2, -.3])
        self.advance(.6)
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertEqual(self.c.mode, 'policy')

    def test_running_head_action_ignores_unrelated_loss_but_cancels_if_its_joint_is_lost(self):
        self.access.start_action(kind='head', head=[None, None, .2, None], session=101)
        self.missing_servos(33)
        self.advance(.2)
        self.assertEqual(self.access.action['state'], 'running')
        self.missing_servos(32, 33)
        self.advance(.1)
        self.assertEqual(self.access.action['state'], 'cancelled')
        self.assertIn('32 号舵机', self.access.action['reason'])
        self.assertNotIn('33', self.access.action['reason'])
        self.assertEqual(self.c.mode, 'policy')
        self.assertFalse(self.c.arm_pending)

    def test_queued_partial_head_rechecks_its_axis_and_merges_with_latest_goals(self):
        self.missing_servos(33)
        self.c.head = [0., 0., 0., -.3]
        self.access.start_action(kind='head', head=[None, None, .2, None], session=101)
        queued = self.access.start_action(kind='head', head=[.1, None, None, None], session=101)
        self.assertEqual(queued['status'], 'queued')
        self.advance(1.1)
        self.assertEqual(self.access.action['kind'], 'head')
        self.assertEqual(self.access.action['state'], 'running')
        self.assertEqual(self.c.head, [.1, 0., .2, -.3])
        self.assertEqual(self.access.action['required_joint_ids'], [30])
        self.advance(1.1)
        self.assertEqual(self.access.action['state'], 'completed')

    def test_queued_head_axis_lost_before_start_is_rejected_without_replaying_others(self):
        self.access.start_action(kind='head', head=[None, None, .2, None], session=101)
        self.access.start_action(kind='head', head=[None, None, None, .1], session=101)
        self.missing_servos(33)
        self.advance(1.1)
        self.assertEqual(self.access.action['state'], 'failed')
        self.assertIn('33 号舵机', self.access.action['reason'])
        self.assertEqual(self.c.head, [0., 0., .2, 0.])
        self.assertFalse(self.access.pending)

    def test_queued_head_can_recover_its_missing_feedback_before_execution(self):
        self.access.start_action(kind='head', head=[None, None, .2, None], session=101)
        self.missing_servos(33)
        queued = self.access.start_action(kind='head', head=[None, None, None, .1], session=101)
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(self.c.head, [0., 0., .2, 0.])
        self.advance(.5)
        self.assertEqual(self.access.action['state'], 'running')
        self.missing_servos()
        self.advance(.6)
        self.assertEqual(self.access.action['id'], queued['action_id'])
        self.assertEqual(self.access.action['state'], 'running')
        self.assertEqual(self.access.action['required_joint_ids'], [33])
        self.assertEqual(self.c.head, [0., 0., .2, .1])

    def test_queued_head_still_missing_at_execution_reports_failure_then(self):
        self.access.start_action(kind='head', head=[None, None, .2, None], session=101)
        self.missing_servos(33)
        queued = self.access.start_action(kind='head', head=[None, None, None, .1], session=101)
        self.assertEqual(queued['status'], 'queued')
        self.advance(1.1)
        self.assertEqual(self.access.action['id'], queued['action_id'])
        self.assertEqual(self.access.action['state'], 'failed')
        self.assertIn('33 号舵机', self.access.action['reason'])
        self.assertEqual(self.c.head, [0., 0., .2, 0.])
        self.assertFalse(self.access.pending)

    def test_head_and_mouth_faults_do_not_block_leg_motion_but_a_leg_fault_does(self):
        self.missing_servos(33, 34)
        self.move()
        self.advance(.3)
        self.assertEqual(self.access.action['state'], 'running')
        self.missing_servos(12, 33, 34)
        self.advance(.1)
        self.assertEqual(self.access.action['state'], 'cancelled')
        self.assertIn('12 号舵机', self.access.action['reason'])
        self.assertNotIn('33', self.access.action['reason'])
        self.assertEqual(self.c.twist, [0, 0, 0])

    def test_stand_can_resume_with_an_unused_head_missing(self):
        self.c.mode, self.c.feedback.enabled = 'shadow', False
        self.c.feedback.mask &= ~(1 << 13)
        self.missing_servos(33)
        self.access.start_action(kind='stand', session=101)
        self.assertTrue(self.c.arm_pending)
        self.c.step()
        self.assertEqual(fixture.wire.COMMAND.unpack(self.c.bridge.notify.call_args.args[1])[4], fixture.wire.ARM)
        self.assertNotIn(33, self.access.action['required_joint_ids'])

    def test_only_changing_a_failed_head_axis_is_rejected_including_return_to_zero(self):
        self.c.head = [0., 0., 0., -.3]
        self.missing_servos(33)
        self.access.start_action(kind='head', head=[None, None, None, -.3], session=101)
        self.assertEqual(self.access.action['required_joint_ids'], [])
        self.advance(1.1)
        with self.assertRaisesRegex(ValueError, '33 号舵机'):
            self.access.start_action(kind='head', head=[None, None, None, 0.], session=101)
        self.assertEqual(self.c.head[3], -.3)

    def test_mouth_checks_34_only_and_empty_head_request_does_not_move(self):
        self.missing_servos(33)
        self.access.start_action(kind='mouth', mouth=6., session=101)
        self.assertEqual(self.access.action['required_joint_ids'], [34])
        self.advance(1.1)
        self.missing_servos(34)
        with self.assertRaisesRegex(ValueError, '34 号舵机'):
            self.access.start_action(kind='mouth', mouth=10., session=101)
        self.assertEqual(self.c.mouth, 0.)  # Explicit mouth lease ended normally.
        with self.assertRaisesRegex(ValueError, 'at least one'):
            self.access.start_action(kind='head', head=[None] * 4, session=101)

    def test_feedback_errors_distinguish_imu_invalid_from_stale_or_absent_mcu_feedback(self):
        cases = (
            ({'imu_ok': False, 'missing_joint_ids': [], 'feedback_age_ms': 4.},
             'IMU 当前没有有效数据'),
            ({'imu_ok': True, 'missing_joint_ids': [], 'feedback_age_ms': 301.},
             'STM32 反馈已 301 毫秒未更新'),
            ({'feedback_age_ms': None, 'missing_joint_ids': []},
             '尚未收到 STM32'),
        )
        for details, expected in cases:
            with self.subTest(details=details):
                self.motion_status = dict(full_robot_ready=False, mcu_watchdog=False,
                                          mcu_command_age_us=10000, **details)
                before = self.c.bridge.notify.call_count
                with self.assertRaisesRegex(ValueError, expected):
                    self.move()
                self.assertEqual(self.c.bridge.notify.call_count, before)
                self.assertEqual(self.c.input_source, 'web')
                self.assertFalse(self.c.arm_pending)

    def test_get_status_clears_observed_fault_after_feedback_recovers(self):
        self.motion_status.update(full_robot_ready=False, imu_ok=True,
                                  missing_joint_ids=[33], feedback_age_ms=5.)
        mcp = self.mcp()
        def status():
            reply = mcp.handle_message({'id': 1, 'method': 'tools/call', 'params':
                {'name': 'Duck.GetStatus'}})['result']
            return json.loads(reply['content'][0]['text'])
        self.assertIn('33 号舵机', status()['feedback_warning'])
        self.motion_status.update(full_robot_ready=True, missing_joint_ids=[])
        data = status()
        self.assertTrue(data['ready'])
        self.assertEqual(data['error'], '')
        self.assertEqual(data['feedback_warning'], '')
        self.assertEqual(data['missing_joint_ids'], [])

    def test_mcp_move_then_turn_is_queued_and_started_after_completion(self):
        mcp = self.mcp()
        first = self.move(twist=[.2, 0, 0], duration=2)
        reply = mcp.handle_message({'id': 2, 'method': 'tools/call', 'params':
            {'name': 'Duck.Turn', 'arguments': {'yaw_rate': .5, 'seconds': 2}}})['result']
        self.assertFalse(reply['isError'])
        queued = json.loads(reply['content'][0]['text'])['execution']
        self.assertEqual(queued['status'], 'queued')
        self.assertEqual(self.access.action['id'], first['action_id'])
        self.assertEqual(self.c.twist, [.2, 0, 0])
        self.assertEqual(self.access.state()['queued_actions'][0]['id'], queued['action_id'])
        self.advance(2.05)
        self.assertEqual(self.access.action['id'], queued['action_id'])
        self.assertEqual(self.c.twist, [0, 0, .5])
        self.advance(2)
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual([a['kind'] for a in self.access.state()['recent_actions']], ['move', 'turn'])

    def test_two_five_second_segments_do_not_overlap_or_expire_in_queue(self):
        self.move(duration=5)
        self.advance(1.6)
        queued = self.move(duration=5)
        self.assertEqual(queued['status'], 'queued')
        self.advance(3.4)
        self.assertEqual(self.access.action['id'], queued['action_id'])
        self.advance(4.9)
        self.assertEqual(self.access.action['state'], 'running')
        self.advance(.2)
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertAlmostEqual(self.access.action['elapsed_s'], 5, delta=.051)

    def test_stop_cancels_current_and_all_queued_actions_without_revival(self):
        first = self.move(duration=10)
        second = self.access.start_action(kind='turn', twist=[0, 0, .5], duration=2, session=101)
        third = self.move(duration=2)
        self.access.start_action(kind='stop', session=101)
        state = self.access.state()
        self.assertEqual(state['queued_actions'], [])
        stopped = {a['id']: a['state'] for a in state['recent_actions']}
        self.assertEqual(stopped, {r['action_id']: 'cancelled' for r in (first, second, third)})
        self.advance(15)
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.mode, 'policy')

    def test_queue_is_bounded_and_invalid_requests_do_not_change_it(self):
        self.move(duration=1)
        for _ in range(4): self.move(duration=1)
        before = self.access.state()['queued_actions']
        with self.assertRaisesRegex(ValueError, 'queue|排队'):
            self.move(duration=1)
        with self.assertRaises(ValueError): self.move(twist=[float('nan'), 0, 0])
        self.assertEqual(before, self.access.state()['queued_actions'])
        self.access.cancel('test')
        self.move(duration=10)
        self.move(duration=10)
        self.move(duration=10)
        with self.assertRaisesRegex(ValueError, '30|budget|排队'):
            self.move(duration=.1)

    def test_queued_move_waits_for_explicit_stand_feedback(self):
        self.c.mode, self.c.feedback.enabled = 'shadow', False
        self.access.start_action(kind='stand', session=101)
        queued = self.move(twist=[.2, 0, 0], duration=2)
        self.assertEqual(queued['status'], 'queued')
        self.c.step()
        self.ack_arm()
        self.c.recovery.phase, self.c.recovery.angle_deg = 'getup', 65
        self.advance(.2)
        self.assertEqual(self.access.action['kind'], 'stand')
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.c.recovery.phase, self.c.recovery.angle_deg = 'walk', 5
        self.advance(1.1)
        self.assertEqual(self.access.action['id'], queued['action_id'])
        self.assertEqual(self.c.twist, [.2, 0, 0])

    def test_queue_is_discarded_on_heartbeat_loss_takeover_or_feedback_fault(self):
        for fault in ('heartbeat', 'takeover', 'feedback', 'permission', 'fall', 'session'):
            self.access.cancel('reset')
            self.allowed = True
            self.motion_status.update(full_robot_ready=True, mcu_watchdog=False, mcu_command_age_us=10000)
            self.c.mode, self.c.input_source, self.c.recovery.angle_deg = 'policy', 'web', 0
            self.advance(1.1)
            self.move()
            self.move()
            with self.subTest(fault=fault):
                if fault == 'heartbeat': self.now += .81
                elif fault == 'takeover': self.access.web_mode('hold')
                elif fault == 'feedback': self.motion_status['mcu_command_age_us'] = 150001
                elif fault == 'permission': self.allowed = False
                elif fault == 'fall': self.c.recovery.angle_deg = 60
                elif fault == 'session': self.access.heartbeat(None)
                self.access.tick()
                self.assertEqual(self.access.state()['queued_actions'], [])
                self.assertEqual(self.c.twist, [0, 0, 0])
                self.assertEqual(self.access.action['state'], 'cancelled')

    def test_voice_and_remote_commands_cannot_queue_behind_each_other(self):
        self.access.remote_allowed = lambda: True
        self.move(remote=True, duration=.7, controller_id='phone_session_A', sequence=1)
        with self.assertRaisesRegex(ValueError, 'running|remote|遥控'):
            self.move()
        self.access.cancel('test')
        self.move()
        self.move()
        with self.assertRaisesRegex(ValueError, 'running|queue|排队'):
            self.move(remote=True, duration=.7, controller_id='phone_session_B', sequence=1)
        self.access.start_action(kind='stop', session=101, remote=True,
                                 controller_id='phone_session_B', sequence=2)
        self.assertEqual(len(self.access.state()['queued_actions']), 1)
        self.assertEqual(self.c.twist, [.08, 0, 0])

    def test_parallel_mcp_commands_are_accepted_in_one_serial_queue(self):
        from concurrent.futures import ThreadPoolExecutor
        mcp = self.mcp()
        calls = [('Duck.Move', {'direction': 'forward', 'speed': .2, 'seconds': 2}),
                 ('Duck.Turn', {'yaw_rate': .5, 'seconds': 2})]
        def call(item):
            name, arguments = item
            return mcp.handle_message({'id': name, 'method': 'tools/call',
                                      'params': {'name': name, 'arguments': arguments}})['result']
        with ThreadPoolExecutor(2) as pool: replies = list(pool.map(call, calls))
        self.assertTrue(all(not r['isError'] for r in replies))
        self.assertEqual({json.loads(r['content'][0]['text'])['execution']['status'] for r in replies},
                         {'accepted', 'queued'})
        self.advance(4.1)
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual([r['state'] for r in self.access.state()['recent_actions']], ['completed', 'completed'])

    def test_request_issued_before_stop_cannot_arrive_late_and_restart_motion(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        import duck_thing
        from unittest.mock import patch
        mcp = self.mcp()
        self.move(duration=10)
        sent, release = threading.Event(), threading.Event()
        original = duck_thing.request
        def delayed(port, token, path, body=None, **kwargs):
            if path == '/action' and body['kind'] == 'turn':
                sent.set()
                if not release.wait(3): raise TimeoutError('test did not release delayed request')
            return original(port, token, path, body, **kwargs)
        with ThreadPoolExecutor(1) as pool, patch.object(duck_thing, 'request', side_effect=delayed):
            pending = pool.submit(mcp.handle_message, {'id': 1, 'method': 'tools/call', 'params':
                {'name': 'Duck.Turn', 'arguments': {'yaw_rate': .5, 'seconds': 2}}})
            self.assertTrue(sent.wait(2))
            self.now += .1
            stopped = mcp.handle_message({'id': 2, 'method': 'tools/call', 'params': {'name': 'Duck.Stop'}})
            release.set()
            rejected = pending.result(3)['result']
        self.assertFalse(stopped['result']['isError'])
        self.assertTrue(rejected['isError'])
        self.assertIn('旧动作已取消', rejected['error'])
        self.advance(12)
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.access.state()['queued_actions'], [])

    def test_queued_head_waits_for_standing_and_wait_is_bounded(self):
        self.move(duration=1)
        queued = self.access.start_action(kind='head', head=[.1] * 4, duration=1, session=101)
        self.c.recovery.angle_deg = 22
        self.advance(1.1)
        self.assertEqual(self.access.action['kind'], 'move')
        self.assertIn('waiting_for', self.access.state()['queued_actions'][0])
        self.c.recovery.angle_deg = 5
        self.advance(1.1)
        self.assertEqual(self.access.action['id'], queued['action_id'])
        self.assertEqual(self.c.head, [.1] * 4)
        self.access.cancel('test')
        self.advance(1.1)
        self.move(duration=1)
        self.access.start_action(kind='head', head=[.2] * 4, duration=1, session=101)
        self.move(duration=1)
        self.c.recovery.angle_deg = 22
        self.advance(6.2)
        self.assertEqual(self.access.action['state'], 'failed')
        self.assertIn('confirm standing', self.access.action['reason'])
        self.assertEqual(self.access.state()['queued_actions'], [])
        self.assertEqual(self.c.twist, [0, 0, 0])

    def test_failed_stand_does_not_run_queued_move_or_pick(self):
        self.c.mode, self.c.feedback.enabled = 'shadow', False
        self.access.start_action(kind='stand', session=101)
        self.move(duration=10)
        self.access.start_action(kind='pick', session=101)
        self.advance(5.1)
        self.assertEqual(self.access.action['state'], 'failed')
        self.assertEqual(self.access.state()['queued_actions'], [])
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertFalse(self.c.pick_active)

    def test_voice_stop_retires_active_phone_controller(self):
        self.access.remote_allowed = lambda: True
        self.move(remote=True, duration=.7, controller_id='phone_session_A', sequence=1)
        self.access.start_action(kind='stop', session=101)
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.move(remote=True, duration=.7, controller_id='phone_session_A', sequence=100)
        self.assertEqual(self.c.twist, [0, 0, 0])

    def test_mcp_status_with_recent_results_and_full_queue_fits_gateway(self):
        for _ in range(4):
            self.access.start_action(kind='head', head=[.123456789] * 4, duration=1, session=101)
            self.advance(1.1)
        self.move(duration=10)
        for _ in range(4): self.move(duration=2)
        reply = self.mcp().handle_message({'id': 1, 'method': 'tools/call', 'params': {'name': 'Duck.GetStatus'}})
        self.assertLess(len(json.dumps(reply, ensure_ascii=False).encode()), 8192)
        data = json.loads(reply['result']['content'][0]['text'])
        self.assertNotIn('action', data)
        self.assertEqual(len(data['queued_actions']), 4)
        self.assertEqual(len(data['recent_actions']), 4)

    def test_timestamp_and_phone_duration_bounds_do_not_change_motion(self):
        for issued_at in (True, -1, float('nan'), float('inf'), self.now + .1, self.now - 1.1):
            with self.subTest(issued_at=issued_at):
                with self.assertRaises(ValueError): self.move(issued_at=issued_at)
                self.assertEqual(self.c.input_source, 'web')
                self.assertEqual(self.access.state()['queued_actions'], [])
        self.access.remote_allowed = lambda: True
        for duration in (1.001, 10):
            with self.assertRaisesRegex(ValueError, 'lease'):
                self.move(remote=True, duration=duration, controller_id='phone_session_A', sequence=1)
            self.assertEqual(self.c.input_source, 'web')

    def test_torque_off_and_app_close_clear_the_queue(self):
        for stop in ('torque_off', 'close'):
            self.c.mode, self.c.feedback.enabled, self.c.recovery.angle_deg = 'policy', True, 5
            self.advance(1.1)
            self.move(duration=10)
            self.move(duration=2)
            if stop == 'torque_off': self.access.start_action(kind='torque_off', session=101)
            else: self.access.close()
            self.assertEqual(self.access.state()['queued_actions'], [])
            self.advance(12)
            self.assertEqual(self.c.twist, [0, 0, 0])
            self.assertEqual(self.c.mode, 'off' if stop == 'torque_off' else 'policy')

    def test_lost_enable_during_head_action_cancels_queued_stand_without_arming(self):
        self.access.start_action(kind='head', head=[.1] * 4, duration=1, session=101)
        self.access.start_action(kind='stand', session=101)
        self.c.feedback.enabled = False
        before = self.c.bridge.notify.call_count
        self.access.tick()
        self.assertEqual(self.access.action['state'], 'cancelled')
        self.assertEqual(self.access.state()['queued_actions'], [])
        self.assertFalse(self.c.arm_pending)
        self.assertEqual(self.c.bridge.notify.call_count, before)

    def test_gamepad_takeover_discards_queued_voice_without_reclaiming_control(self):
        self.move(duration=10)
        self.move(duration=2)
        self.c.input_source = 'gamepad'
        self.access.tick()
        self.assertEqual(self.access.action['state'], 'cancelled')
        self.assertEqual(self.access.state()['queued_actions'], [])
        self.assertEqual(self.c.input_source, 'gamepad')
        self.advance(12)
        self.assertEqual(self.c.input_source, 'gamepad')

    def test_manual_takeover_and_rejected_mode(self):
        self.move()
        with self.assertRaisesRegex(ValueError, 'disarm'):
            self.access.web_mode('servo_debug')
        self.assertEqual(self.c.input_source, 'voice')
        self.assertEqual(self.access.action['state'], 'running')
        self.access.web_mode('hold')
        self.assertEqual(self.c.input_source, 'web')
        self.assertEqual(self.access.action['state'], 'cancelled')
        self.assertEqual(self.c.mode, 'hold')

    def test_pick_cancellation_and_explicit_torque_off(self):
        self.access.start_action(kind='pick', session=101)
        self.assertTrue(self.c.pick_active)
        self.access.cancel('voice exited')
        self.assertFalse(self.c.pick_active)
        self.assertEqual(self.c.mouth, 0)
        self.assertEqual(self.c.mode, 'policy')
        self.access.start_action(kind='torque_off', session=101)
        self.assertEqual(self.c.mode, 'off')

    def test_mcp_roulade_exposes_fixed_single_roll_and_records_status(self):
        mcp = self.mcp()
        tools = mcp.handle_message({'id': 1, 'method': 'tools/list'})['result']['tools']
        roll = next(t for t in tools if t['name'] == 'Duck.Roulade')
        self.assertEqual(roll['inputSchema']['properties'], {})
        self.assertEqual(roll['inputSchema']['required'], [])
        self.assertIn('1.9', roll['description'])
        with patch.object(self.c, 'start_roulade', wraps=self.c.start_roulade) as start:
            reply = mcp.handle_message({'id': 2, 'method': 'tools/call',
                'params': {'name': 'Duck.Roulade', 'arguments': {}}})['result']
            self.assertFalse(reply['isError'])
            start.assert_called_once_with()
        self.assertEqual(self.access.action['kind'], 'roulade')
        self.assertEqual(self.access.action['duration_s'], 1.9)
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.head, [0] * 4)
        self.assertEqual(self.c.mouth, 0)
        state = json.loads(mcp.handle_message({'id': 3, 'method': 'tools/call',
            'params': {'name': 'Duck.GetStatus'}})['result']['content'][0]['text'])
        self.assertNotIn('action', state)
        self.assertEqual(state['motion_action']['kind'], 'roulade')

    def test_roulade_requires_permission_standing_feedback_and_control_ownership(self):
        faults = ('permission', 'off', 'disabled', 'fallen', 'recovering', 'unknown',
                  'feedback', 'command', 'watchdog', 'gamepad', 'calibration', 'servo_debug', 'pick')
        for fault in faults:
            with self.subTest(fault=fault):
                self.allowed = True
                self.c.mode, self.c.input_source, self.c.feedback.enabled = 'policy', 'web', True
                self.c.recovery.phase, self.c.recovery.angle_deg = 'walk', 0
                self.c.pick_active = False
                self.motion_status.update(full_robot_ready=True, mcu_watchdog=False, mcu_command_age_us=10000)
                self.advance(1.1)
                if fault == 'permission': self.allowed = False
                elif fault == 'off': self.c.mode = 'off'
                elif fault == 'disabled': self.c.feedback.enabled = False
                elif fault == 'fallen': self.c.recovery.angle_deg = 70
                elif fault == 'recovering': self.c.recovery.phase = 'getup'
                elif fault == 'unknown': self.c.recovery.angle_deg = None
                elif fault == 'feedback': self.motion_status['full_robot_ready'] = False
                elif fault == 'command': self.motion_status['mcu_command_age_us'] = 150001
                elif fault == 'watchdog': self.motion_status['mcu_watchdog'] = True
                elif fault == 'gamepad': self.c.input_source = 'gamepad'
                elif fault == 'calibration': self.c.mode = 'calibrate'
                elif fault == 'servo_debug': self.c.mode = 'servo_debug'
                elif fault == 'pick': self.c.pick_active = True
                before = (self.c.mode, self.c.input_source, list(self.c.twist),
                          self.c.roulade_until, self.c.bridge.notify.call_count)
                with self.assertRaises(ValueError):
                    self.access.start_action(kind='roulade', session=101)
                self.assertEqual(before, (self.c.mode, self.c.input_source, self.c.twist,
                                          self.c.roulade_until, self.c.bridge.notify.call_count))
                self.assertIsNone(self.access.action)

    def test_roulade_uses_original_model_window_and_restores_smoothing(self):
        for name in ('policy', 'getup_policy', 'pick_policy', 'roulade_policy'):
            model = Mock(action_alpha=.45, action_alpha_head=.55)
            model.infer.return_value = [0.] * 14
            model.action_to_deg.return_value = [0.] * 15
            setattr(self.c, name, model)
        self.c.walk_policy = self.c.policy
        with patch.object(fixture.ns['time'], 'monotonic', side_effect=lambda: self.now):
            self.c.rx_time = self.now
            self.access.start_action(kind='roulade', session=101)
            self.c.policy.set_action_alphas.assert_called_with(leg=.15, head=.15)
            self.c.filtered['grav'] = [0, 0, 1]  # Full inversion during an intentional roll.
            self.c.step()
            self.c.roulade_policy.infer.assert_called_once()
            self.c.policy.infer.assert_not_called()
            self.c.getup_policy.infer.assert_not_called()
            self.assertEqual(self.c.recovery.phase, 'walk')
            self.c.recovery.fall_elapsed = .12
            self.advance(1.85)
            self.assertEqual(self.access.action['state'], 'running')
            self.advance(.1)
            self.assertEqual(self.access.action['state'], 'completed')
            self.assertAlmostEqual(self.access.action['elapsed_s'], 1.9, delta=.051)
            self.assertEqual(self.c.roulade_until, 0)
            self.assertEqual(self.c.recovery.fall_elapsed, 0)
            self.assertTrue(self.c.policy_needs_reset)
            self.c.policy.set_action_alphas.assert_called_with(leg=.45, head=.55)
            self.assertEqual(self.c.mode, 'policy')
            for _ in range(9):
                self.now += .02
                self.c.rx_time = self.now
                self.c.step()
            self.assertEqual(self.c.recovery.phase, 'default_pose')

    def test_queued_roulade_waits_for_standing_after_move(self):
        self.move(duration=2)
        with patch.object(self.c, 'start_roulade', wraps=self.c.start_roulade) as start:
            queued = self.access.start_action(kind='roulade', session=101)
            self.assertEqual(queued['status'], 'queued')
            self.assertEqual(self.c.roulade_until, 0)
            self.c.recovery.angle_deg = 20  # Normal gait, not yet standing.
            self.advance(2.05)
            start.assert_not_called()
            self.assertEqual(self.c.twist, [0, 0, 0])
            self.c.recovery.angle_deg = 0
            self.advance(1.1)
            start.assert_called_once_with()
            self.assertEqual(self.access.action['id'], queued['action_id'])
            self.advance(2)
            self.assertEqual(self.access.action['state'], 'completed')

    def test_move_after_roulade_waits_through_recovery_and_standing(self):
        self.access.start_action(kind='roulade', session=101)
        queued = self.move(duration=2, twist=[.2, 0, 0])
        self.advance(1.8)
        self.c.recovery.phase, self.c.recovery.angle_deg = 'getup', 70
        self.advance(.2)
        self.assertEqual(self.access.action['kind'], 'roulade')
        self.assertEqual(self.access.action['state'], 'completed')
        self.assertEqual(self.c.roulade_until, 0)
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertIn('waiting_for', self.access.state()['queued_actions'][0])
        self.advance(2)
        self.assertEqual(len(self.access.pending), 1)
        self.c.recovery.phase, self.c.recovery.angle_deg = 'walk', 0
        self.advance(1.1)
        self.assertEqual(self.access.action['id'], queued['action_id'])
        self.assertEqual(self.c.twist, [.2, 0, 0])
        self.advance(2.1)
        self.assertEqual(self.access.action['state'], 'completed')

    def test_roulade_recovery_wait_timeout_discards_following_actions(self):
        self.access.start_action(kind='roulade', session=101)
        self.move(duration=2)
        self.access.start_action(kind='roulade', session=101)
        self.advance(1.8)
        self.c.recovery.phase, self.c.recovery.angle_deg = 'getup', 70
        self.advance(5.4)
        self.assertEqual(self.access.action['state'], 'failed')
        self.assertIn('confirm standing', self.access.action['reason'])
        self.assertEqual(self.access.state()['queued_actions'], [])
        self.assertEqual(self.c.roulade_until, 0)
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.mode, 'policy')

    def test_roulade_stop_faults_and_takeover_cancel_roll_and_queue(self):
        for fault in ('stop', 'heartbeat', 'permission', 'feedback', 'command', 'enable', 'manual', 'session'):
            with self.subTest(fault=fault):
                self.allowed = True
                self.c.mode, self.c.input_source, self.c.feedback.enabled = 'policy', 'web', True
                self.c.recovery.phase, self.c.recovery.angle_deg = 'walk', 0
                self.motion_status.update(full_robot_ready=True, mcu_watchdog=False, mcu_command_age_us=10000)
                self.access = MotionAccess(self.c, allowed=lambda: self.allowed, clock=lambda: self.now)
                self.access.heartbeat(101)
                self.advance(1.1)
                self.access.start_action(kind='roulade', session=101)
                self.move()
                if fault == 'stop': self.access.start_action(kind='stop', session=101)
                elif fault == 'heartbeat': self.now += .81
                elif fault == 'permission': self.allowed = False
                elif fault == 'feedback': self.motion_status['full_robot_ready'] = False
                elif fault == 'command': self.motion_status['mcu_command_age_us'] = 150001
                elif fault == 'enable': self.c.feedback.enabled = False
                elif fault == 'manual': self.access.web_mode('hold')
                elif fault == 'session': self.access.heartbeat(None)
                self.access.tick()
                self.assertEqual(self.access.action['state'], 'cancelled')
                self.assertEqual(self.access.state()['queued_actions'], [])
                self.assertEqual(self.c.roulade_until, 0)
                self.assertIsNone(self.c._roulade_alpha)
                self.assertEqual(self.c.twist, [0, 0, 0])
                self.assertNotEqual(self.c.mode, 'off')  # Cancellation keeps balance.

    def test_delayed_roulade_cannot_restart_after_stop_and_remote_cannot_roll(self):
        issued_at = self.now
        self.access.start_action(kind='roulade', session=101)
        self.advance(.1)
        self.access.start_action(kind='stop', session=101)
        with self.assertRaisesRegex(ValueError, 'expired|predates'):
            self.access.start_action(kind='roulade', session=101, issued_at=issued_at)
        self.access.remote_allowed = lambda: True
        with self.assertRaisesRegex(ValueError, 'unsupported remote'):
            self.access.start_action(kind='roulade', session=101, remote=True,
                                     controller_id='phone-1234', sequence=1)
        self.assertEqual(self.c.roulade_until, 0)
        self.assertEqual(self.access.state()['queued_actions'], [])

    def test_mcp_reports_accepted_action_as_success_and_uses_real_ipc(self):
        mcp = self.mcp()
        reply = mcp.handle_message({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': 'Duck.Move', 'arguments': {'direction': 'forward', 'speed': .08, 'seconds': 2}}})
        self.assertFalse(reply['result']['isError'])
        self.assertIn('accepted', reply['result']['content'][0]['text'])
        self.assertEqual(self.c.twist, [.08, 0, 0])
        self.assertTrue(mcp._is_blocking_tool('Duck.Move'))
        status = mcp.handle_message({'id': 2, 'method': 'tools/call', 'params': {'name': 'Duck.GetStatus'}})
        self.assertFalse(status['result']['isError'])

    def test_revoked_session_cannot_move_or_reactivate_via_delayed_heartbeat(self):
        self.move()
        self.access.heartbeat(None)
        self.assertEqual(self.c.twist, [0, 0, 0])
        self.assertEqual(self.c.input_source, 'web')
        self.assertFalse(self.access.heartbeat(101)['ok'])
        with self.assertRaisesRegex(ValueError, 'session ended'):
            self.move()
        self.access.heartbeat(102)
        self.access.start_action(kind='move', twist=[.05, 0, 0], session=102)
        self.assertEqual(self.c.twist, [.05, 0, 0])


if __name__ == '__main__':
    unittest.main()
