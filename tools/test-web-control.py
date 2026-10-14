"""Controller behavior with no browser heartbeat; no hardware or ONNX required."""
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
from recovery import Recovery

source = ast.parse((ROOT / 'python/controller.py').read_text())
cls = next(n for n in source.body if isinstance(n, ast.ClassDef) and n.name == 'Controller')
ns = dict(vars(wire), threading=threading, time=time, DEFAULT_DEG=[0.] * 15,
          MOUTH_INDEX=14, EXCLUSIVE_MODES=('calibrate', 'servo_debug'))
exec(compile(ast.Module(body=[cls], type_ignores=[]), 'controller.py', 'exec'), ns)
Controller = ns['Controller']


class WebControlTest(unittest.TestCase):
    def setUp(self):
        c = self.c = Controller.__new__(Controller)
        c.lock = threading.RLock()
        c.policy_lock = threading.RLock()
        c.mode = 'hold'
        c.generation = c.seq = c.sent = c.inferred = 0
        c.arm_pending = False
        c.web_time = time.monotonic() - 3600
        c.rx_time = time.monotonic()
        c.feedback = SimpleNamespace(imu_ok=True, sample_us=123, angles=[0.] * 15, enabled=True)
        c.filtered = dict(imu_fusion_ready=True, imu_fusion_updated=True, grav=[0, 0, -1],
                          gyro_rad=[0.] * 3, angles_deg=[0.] * 15, vels_deg=[0.] * 15)
        c.twist, c.head, c.mouth = [0.] * 3, [0.] * 4, 0.
        c.recovery = Recovery()
        c.roulade_until, c._roulade_alpha = 0., None
        c.pick_active, c.pick_phi = False, 0.
        c.policy_needs_reset = False
        c.last_error = ''
        c.infer_times, c.infer_ms = collections.deque(), collections.deque()
        c.bridge = Mock()
        c.policy = c.getup_policy = c.pick_policy = c.roulade_policy = Mock()
        c.policy.infer.return_value = [0.] * 14
        c.policy.action_to_deg.return_value = [0.] * 15

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

    def test_stale_sensor_feedback_still_disarms(self):
        self.c.rx_time -= 1
        self.c.step()
        self.assertEqual(self.command_mode(), wire.DISARM)
        self.assertIn('sensor feedback lost', self.c.last_error)

    def test_invalid_imu_still_disarms(self):
        self.c.feedback.imu_ok = False
        self.c.step()
        self.assertEqual(self.command_mode(), wire.DISARM)

    def test_manual_stop_still_disarms(self):
        self.c.set_mode('off')
        self.c.step()
        self.assertEqual(self.command_mode(), wire.DISARM)

    def test_calibration_session_timeout_retained(self):
        self.c.mode = 'calibrate'
        self.c.step()
        self.assertEqual(self.c.mode, 'shadow')
        self.assertIn('heartbeat lost', self.c.last_error)
        self.assertTrue(any(call.args[0] == 'duck_cal' for call in self.c.bridge.notify.call_args_list))


if __name__ == '__main__':
    unittest.main()
