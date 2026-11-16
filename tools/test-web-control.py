"""Controller behavior with no browser heartbeat; no hardware or ONNX required.

Shared fixture for the AI motion tests: the real Controller class is executed
against the real wire ABI with a mocked Bridge, policy sessions and feedback.
"""
import ast
import collections
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import wire
import controller
from heading import HeadingHold
from recovery import Recovery

source = ast.parse((ROOT / 'python/controller.py').read_text())
cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'Controller')
ns = vars(controller).copy()
exec(compile(ast.Module(body=[cls], type_ignores=[]), 'controller.py', 'exec'), ns)
Controller = ns['Controller']


def policy_mock(file_name='mock.onnx'):
    p = Mock()
    p.file = file_name
    p.default_deg = [0.] * 15
    p.home_deg = Mock()
    p.infer.return_value = [0.] * 14
    p.action_to_deg.return_value = [0.] * 15
    return p


class WebControlTest(unittest.TestCase):
    def setUp(self):
        c = self.c = Controller.__new__(Controller)
        c.lock = threading.RLock()
        c.servo_op_lock = threading.Lock()
        c.cal_op_lock = threading.Lock()
        c.policy_lock = threading.RLock()
        c.mode = 'hold'
        c.generation = c.seq = c.sent = c.inferred = 0
        c.arm_pending = False
        # AI-layer compatibility attributes.
        c.input_source = 'web'
        c.fault_hold = False
        c._arm_ack_seq, c._arm_ack_deadline = None, 0.0
        c.web_time = time.monotonic() - 3600
        c.rx_time = time.monotonic()
        c.feedback = SimpleNamespace(imu_ok=True, sample_us=123, angles=[0.] * 15, enabled=True,
                                     flags=3, mask=wire.ALL_MASK, command_seq=0,
                                     command_age_us=10000, imu_us=0, work_us=0)
        c.filtered = dict(imu_fusion_ready=True, imu_fusion_updated=True, grav=[0, 0, -1],
                          gyro_rad=[0.] * 3, angles_deg=[0.] * 15, vels_deg=[0.] * 15)
        c.filter = Mock(command_acc_x_bias_ms2=0.)
        c.twist, c.head, c.mouth = [0.] * 3, [0.] * 4, 0.
        c.recovery = Recovery()
        c.heading = HeadingHold()
        c._imu_job, c._imu_record = {'active': False}, None
        c.action_preparation = c._action_first_target = None
        c.roulade_until, c._roulade_alpha = 0., None
        c.roulade_pending = False
        c._roulade_requested_at, c._roulade_started_at = 0., None
        c._roulade_frames = collections.deque(maxlen=128)
        c._roulade_timing = {}
        c.roller_mode = False
        c.sitstand_phase, c.sitstand_until = 'idle', 0.
        c.kick_until, c.kick_side = 0., None
        c._imu_source_us, c._imu_source_time = None, 0.
        c.diagnostics, c.diagnostic_time = {}, float('-inf')
        c.battery_phase, c.battery_since = 'normal', None
        c.pick_active, c.pick_phi = False, 0.
        c.policy_needs_reset = False
        c.last_error = ''
        c.infer_times, c.infer_ms = collections.deque(), collections.deque()
        c.bridge = Mock()
        c.walk_policy = c.policy = policy_mock()
        c.roller_policy = policy_mock('roller.onnx')
        c.getup_policy = policy_mock('getup.onnx')
        c.pick_policy = policy_mock('pick.onnx')
        c.roulade_policy = policy_mock('roulade.onnx')
        c.sitstand_policy = policy_mock('sitstand.onnx')
        c.left_kick_policy = policy_mock('left-kick.onnx')
        c.right_kick_policy = policy_mock('right-kick.onnx')
        c.kick_policies = {'left': c.left_kick_policy, 'right': c.right_kick_policy}
        c.cal_done = [False] * 15
        c.cal_expected_index = None
        c.cal_reply_event = threading.Event()
        c.raw_pos = [2047] * 15
        c.zero_pos = [2047] * 15
        c.raw_mask = 0
        c._zeros_pushed = True
        c.boot_pd = {}

    def command_mode(self):
        method, payload = self.c.bridge.notify.call_args.args
        self.assertEqual(method, 'duck_command')
        return wire.COMMAND.unpack(payload)[4]

    def test_hold_survives_absent_web_heartbeat(self):
        self.c.step()
        self.assertEqual(self.c.mode, 'hold')
        self.assertEqual(self.command_mode(), wire.HOLD)

    def test_policy_survives_absent_web_heartbeat(self):
        self.c.mode = 'policy'
        self.c.step()
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.command_mode(), wire.POLICY)
        self.assertEqual(self.c.inferred, 1)

    def test_stale_sensor_feedback_holds_last_targets_and_resumes_without_travel(self):
        self.c.mode = 'policy'
        self.c.command(twist=[.1, 0., 0.])
        self.c.step()
        self.assertEqual(self.command_mode(), wire.POLICY)
        self.c.bridge.notify.reset_mock()
        self.c.rx_time -= .16
        self.c.step()
        self.assertTrue(self.c.fault_hold)
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.c.twist, [0., 0., 0.])
        self.c.bridge.notify.assert_not_called()
        with self.assertRaisesRegex(ValueError, 'feedback is stale'):
            self.c.set_mode('policy')

        self.c.rx_time = time.monotonic()
        self.assertIn('ignored', self.c.command(twist=[.1, 0., 0.]))
        self.c.step()
        self.assertFalse(self.c.fault_hold)
        self.assertEqual(self.c.mode, 'policy')
        self.assertEqual(self.c.twist, [0., 0., 0.])
        self.assertEqual(self.command_mode(), wire.POLICY)

    def test_torque_off_still_disarms_during_stale_feedback(self):
        self.c.rx_time -= .16
        self.c.step()
        self.assertTrue(self.c.fault_hold)
        self.c.set_mode('off')
        self.c.step()
        self.assertFalse(self.c.fault_hold)
        self.assertEqual(self.command_mode(), wire.DISARM)

    def test_invalid_imu_disarms(self):
        self.c.feedback.imu_ok = False
        self.c.step()
        self.assertEqual(self.c.mode, 'shadow')
        self.assertEqual(self.command_mode(), wire.DISARM)

    def test_manual_stop_still_disarms(self):
        self.c.set_mode('off')
        self.c.step()
        self.assertEqual(self.command_mode(), wire.DISARM)

    def test_calibration_session_timeout_exits_and_disarms(self):
        self.c.mode = 'calibrate'
        self.c.step()
        self.assertEqual(self.c.mode, 'shadow')
        self.assertIn('heartbeat lost', self.c.last_error)
        modes = [wire.CAL.unpack(call.args[1])[2] for call in self.c.bridge.notify.call_args_list
                 if call.args[0] == 'duck_cal']
        self.assertIn(wire.CAL_EXIT, modes)
        self.assertEqual(self.command_mode(), wire.DISARM)


if __name__ == '__main__':
    unittest.main()
