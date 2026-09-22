"""Policy/filter math synchronized with rl/raspi_deploy infer.py and duck.py."""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import List, Optional, Tuple
import numpy as np


GYRO_ALPHA = 0.5

VEL_ALPHA = 0.4

FEEDBACK_PERIOD = 0.01  # Firmware sendState: 100 Hz; protocol has no sample timestamp.

GRAVITY_TAU = 0.3

# Static IMU attitude when the robot is standing level, in the policy body frame
# (degrees). Stand still and read grav: pitch = atan2(gx, -gz), roll = atan2(-gy, -gz).
# Those values go here; both accelerometer and gyro are rotated so standing gravity
# becomes [0, 0, -1]. Yaw is unobservable from gravity; leave it 0 unless the board
# is also twisted. Example: pitch inherently 5° → (0.0, 5.0, 0.0).
IMU_BIAS_DEG = (0.0, 0.0, 0.0)  # roll, pitch, yaw

MOTOR_IDS = [
    10, 11, 12, 13, 14,
    20, 21, 22, 23, 24,
    30, 31, 32, 33, 34,
]

q = 24.0
DEFAULT_DEG = [
    0.0, -5.0, -q, 0.0, q,
    0.0, 5.0, q, 0.0, -q,
    20.0, 20.0, 0.0, 0.0, 0.0,
]

POLICY_IDS = [
    10, 11, 12, 13, 14,
    30, 31, 32, 33,
    20, 21, 22, 23, 24,
]

POLICY_INDEX = np.array([MOTOR_IDS.index(i) for i in POLICY_IDS])

HOME_DEG = np.array(DEFAULT_DEG, dtype=np.float32)[POLICY_INDEX]

MOUTH_INDEX = MOTOR_IDS.index(34)

MOUTH_MAX_DEG = 30.0

OBS_DIM = 61

ACTION_DIM = 14

ACTION_SCALE = 1.0

ACTION_ALPHA = 0.45

ACTION_ALPHA_HEAD = 0.45

@dataclass
class State:
    acc: List[float]
    gyro: List[float]
    angles: List[float]  # MOTOR_IDS order
    vels: List[float]
    grav: List[float] = field(default_factory=lambda: [0.0, 0.0, -1.0])
    gyro_rad: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])

def _rotate_imu_bias(vec: List[float]) -> List[float]:
    """Rotate an IMU vector to cancel IMU_BIAS_DEG. Identity when the bias is zero."""
    roll, pitch, yaw = IMU_BIAS_DEG
    if roll == 0.0 and pitch == 0.0 and yaw == 0.0:
        return vec
    r, p, y = math.radians(roll), math.radians(pitch), math.radians(yaw)
    x, yv, z = vec
    if y:
        cy, sy = math.cos(y), math.sin(y)
        x, yv = x * cy - yv * sy, x * sy + yv * cy
    if r:
        cr, sr = math.cos(r), math.sin(r)
        yv, z = yv * cr - z * sr, yv * sr + z * cr
    if p:
        cp, sp = math.cos(p), math.sin(p)
        x, z = x * cp + z * sp, -x * sp + z * cp
    return [x, yv, z]


class ImuFilter:
    """Body-frame gravity: gyro prediction plus accel-magnitude-weighted correction. No bias estimate."""

    def __init__(self, gyro_alpha: float = GYRO_ALPHA, vel_alpha: float = VEL_ALPHA) -> None:
        self.gyro_alpha = gyro_alpha
        self.vel_alpha = vel_alpha
        self._gravity: Optional[List[float]] = None
        self._gyro: Optional[List[float]] = None
        self._vel: Optional[List[float]] = None

    def update(self, st: State, dt: float = FEEDBACK_PERIOD) -> State:
        acc_n = _rotate_imu_bias([st.acc[2], -st.acc[0], -st.acc[1]])
        gyro_n = _rotate_imu_bias([st.gyro[2], -st.gyro[0], -st.gyro[1]])
        norm = math.sqrt(sum(v * v for v in acc_n))
        acc_unit = [v / norm for v in acc_n] if norm > 1e-6 else None
        # Large gaps cannot be reconstructed from a single angular-rate sample.
        reset = self._gravity is None or dt > 0.1
        dt = max(0.001, min(dt, 0.1))
        if reset:
            self._gravity = acc_unit if acc_unit is not None else [0.0, 0.0, -1.0]
            self._gyro = gyro_n
            self._vel = list(st.vels)
        else:
            gx, gy, gz = self._gravity
            wx, wy, wz = [math.radians(v) for v in gyro_n]
            # A world-fixed vector in rotating body coordinates: dg/dt = -omega x g.
            predicted = [gx + (wz * gy - wy * gz) * dt,
                         gy + (wx * gz - wz * gx) * dt,
                         gz + (wy * gx - wx * gy) * dt]
            pnorm = math.sqrt(sum(v * v for v in predicted))
            predicted = [v / pnorm for v in predicted]
            trust = max(0.0, 1.0 - abs(norm / 9.8 - 1.0) / 0.2)
            gain = (1.0 - math.exp(-dt / GRAVITY_TAU)) * trust
            if acc_unit is not None:
                predicted = [(1 - gain) * p + gain * a for p, a in zip(predicted, acc_unit)]
            pnorm = math.sqrt(sum(v * v for v in predicted))
            self._gravity = [v / pnorm for v in predicted]
            # Preserve the previous smoothing at 100 Hz, also when USB delivers batches.
            ga = self.gyro_alpha ** (dt / FEEDBACK_PERIOD)
            va = self.vel_alpha ** (dt / FEEDBACK_PERIOD)
            self._gyro = [ga * o + (1 - ga) * n for o, n in zip(self._gyro, gyro_n)]
            self._vel = [va * o + (1 - va) * n for o, n in zip(self._vel, st.vels)]
        st.grav = list(self._gravity)
        st.gyro_rad = [v * math.pi / 180.0 for v in self._gyro]
        st.vels = list(self._vel)
        return st

def policy_from_state(st: State) -> Tuple[np.ndarray, np.ndarray]:
    """Absolute joint degrees minus the home pose, then converted to radians."""
    q_deg = np.asarray(st.angles, dtype=np.float32)[POLICY_INDEX]
    v_deg = np.asarray(st.vels, dtype=np.float32)[POLICY_INDEX]
    return np.deg2rad(q_deg - HOME_DEG), np.deg2rad(v_deg)

def build_obs(
    st: State,
    prev_action: np.ndarray,
    twist: np.ndarray,
    head: np.ndarray,
    out: np.ndarray | None = None,
) -> np.ndarray:
    obs = np.empty(OBS_DIM, dtype=np.float32) if out is None else out
    obs[0:3] = st.gyro_rad
    obs[3:6] = st.grav
    obs[6:20], obs[20:34] = policy_from_state(st)
    obs[34:48] = prev_action
    obs[48:51] = twist
    obs[51:55] = head
    obs[55:61] = 0.0
    return obs

def action_to_deg(action: np.ndarray, mouth_deg: float = 0.0) -> List[float]:
    """Policy output is radians relative to home. Convert to degrees and add the home pose."""
    q_deg = HOME_DEG + np.rad2deg(ACTION_SCALE * action)
    targets = np.zeros(len(MOTOR_IDS), dtype=np.float32)
    targets[POLICY_INDEX] = q_deg
    targets[MOUTH_INDEX] = float(np.clip(mouth_deg, 0.0, MOUTH_MAX_DEG))
    return targets.tolist()
