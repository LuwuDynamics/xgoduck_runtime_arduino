import json, time, unittest, sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np
import onnxruntime as ort
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from policy import Policy, PYTHON_DIR
from controller import Controller
from motion import MotionRamp
from rl_core import POLICY_INDEX
from wire import COMMAND, DISARM

class Stability(unittest.TestCase):


    def test_ramp_start_stop_reverse(self):
        r=MotionRamp();last=[0]*3
        for target in [[.35,.3,1],[-.2,-.3,-1],[0,0,0]]:
            for _ in range(100):
                actual=r.update(target,.02)
                for a,b,rate in zip(actual,last,r.rates):self.assertLessEqual(abs(a-b),rate*.02+1e-9)
                last=actual
            np.testing.assert_allclose(actual,target,atol=1e-9)

    @classmethod
    def setUpClass(cls):
        cls.bridge=SimpleNamespace(notify=lambda *args:cls.packets.append(args))
        cls.packets=[];cls.c=Controller(cls.bridge)
    def fresh(self):
        c=self.c;c.rx_time=time.monotonic();c.web_time=c.rx_time
        c.feedback=SimpleNamespace(imu_ok=True,mask=32767,sample_us=100,imu_us=100,enabled=True,angles=c.policy.default_deg)
        c.filtered=dict(imu_fusion_ready=True,grav=[0,0,-1],gyro_rad=[0]*3,angles_deg=c.policy.default_deg,vels_deg=[0]*15)
    def reset(self):
        c=self.c;self.fresh();c.set_mode('off');c.set_mode('policy');self.fresh();return c
    def test_controller_ramp_and_stop(self):
        c=self.reset();c.command(twist=[0,.3,0]);c.step()
        self.assertAlmostEqual(c.motion.value[1],.008)
        c.command(twist=[0,0,0]);self.fresh();c.step();self.assertEqual(c.motion.value,[0]*3)
    def test_torque_off_bypasses_ramp(self):
        c=self.reset();c.command(twist=[0,.3,0]);c.step();c.set_mode('off');c.step()
        self.assertEqual(c.motion.value,[0]*3);self.assertEqual(COMMAND.unpack(self.packets[-1][1])[4],DISARM)
    def test_recovery_clears_motion(self):
        c=self.reset();c.command(twist=[0,.3,0]);c.step()
        for _ in range(8):
            self.fresh();c.filtered['grav']=[1,0,0];c.step()
        self.assertEqual(c.recovery.phase,'default_pose');self.assertEqual(c.twist,[0]*3);self.assertEqual(c.motion.value,[0]*3)
    def test_action_command_bypasses_ramp(self):
        c=self.reset();c.set_sitstand('sit');c.step()
        self.assertEqual(c.motion.value,[0]*3);np.testing.assert_allclose(c.sitstand_policy._obs[48:51],[1,0,0])
    def test_timeout_decelerates(self):
        c=self.reset();c.command(twist=[0,.3,0])
        for _ in range(20):self.fresh();c.step()
        before=c.motion.value[1];c.web_time=time.monotonic()-2;c.rx_time=time.monotonic();c.step()
        self.assertEqual(c.twist,[0]*3);self.assertAlmostEqual(c.motion.value[1],before-.008)
    def test_roller_switch_clears_ramp(self):
        c=self.reset();c.command(twist=[0,.3,0]);c.step();c.set_roller_mode(True)
        self.assertEqual(c.motion.value,[0]*3);self.assertEqual(c.twist,[0]*3);c.set_roller_mode(False)

    def test_doubled_limits(self):
        c=self.reset()
        for t in [[.7,.6,2],[-.4,-.6,-2]]:c.command(twist=t)
        for t in [[.701,0,0],[-.401,0,0],[0,.601,0],[0,0,2.001]]:
            with self.assertRaises(ValueError):c.command(twist=t)
        c.set_roller_mode(True)
        try:
            c.command(twist=[.8,0,.5]);c.command(twist=[-.8,0,-.5])
            for t in [[.801,0,0],[0,.01,0],[0,0,.501]]:
                with self.assertRaises(ValueError):c.command(twist=t)
        finally:c.set_roller_mode(False)

    def test_ramp_configuration(self):
        r=MotionRamp()
        for factor in [.25,1,3]:
            r.reset();r.configure(factor)
            np.testing.assert_allclose(r.update([.7,.6,2],.02),np.array([.5,.4,1.5])*factor*.02)
        for factor in [0,.24,3.01,float('nan'),float('inf')]:
            with self.assertRaises(ValueError):r.configure(factor)
        self.assertEqual(r.factor,3)

if __name__ == '__main__':
    unittest.main(verbosity=2)
