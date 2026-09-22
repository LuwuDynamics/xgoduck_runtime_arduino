"""QMI body-axis adapter for the Raspberry Pi deployment's receive-side filter."""
from __future__ import annotations
import math
import time
from rl_core import State, ImuFilter, FEEDBACK_PERIOD, VEL_ALPHA, GYRO_ALPHA

class SignalFilter:
    def __init__(self):
        self.gyro_alpha = GYRO_ALPHA
        self.vel_alpha = VEL_ALPHA
        self.reset()

    def reset(self):
        self._imu = ImuFilter(self.gyro_alpha, self.vel_alpha)
        self._last_t = None
        self._last = None

    def update(self, acc_ms2, gyro_dps, angles_deg, vels_deg, *, timestamp=None, imu_ok=True):
        acc, gyro = list(acc_ms2), list(gyro_dps)
        now = time.perf_counter() if timestamp is None else float(timestamp)
        valid = imu_ok and all(math.isfinite(v) for v in (*acc, *gyro, *angles_deg, *vels_deg, now))
        if self._last_t is not None and now <= self._last_t:
            valid = False
        if not valid:
            self._last_t = None
            out = dict(self._last or dict(
                grav=[0, 0, -1], gyro_rad=[0] * 3,
                angles_deg=[0] * 15, vels_deg=[0] * 15, imu_fusion_ready=False))
            out['imu_fusion_updated'] = False
            return out
        dt = FEEDBACK_PERIOD if self._last_t is None else now - self._last_t
        if self._last_t is None:
            self._imu = ImuFilter(self.gyro_alpha, self.vel_alpha)
        self._last_t = now
        # QMI firmware already outputs policy body axes. Invert the ESP32 Python
        # mapping [z,-x,-y] here so ImuFilter applies it exactly once overall.
        st = State([-acc[1], -acc[2], acc[0]], [-gyro[1], -gyro[2], gyro[0]],
                   list(angles_deg), list(vels_deg))
        st = self._imu.update(st, dt)
        out = dict(angles_deg=list(angles_deg), vels_deg=list(st.vels),
                   grav=st.grav, gyro_rad=st.gyro_rad,
                   imu_fusion_ready=True, imu_fusion_updated=True)
        self._last = out
        return out
