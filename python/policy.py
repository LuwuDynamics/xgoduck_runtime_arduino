"""ONNX policy adapter matching the Raspberry Pi deployment math."""
from __future__ import annotations
import math
from pathlib import Path
from typing import Optional
import numpy as np
from rl_core import (HOME_DEG, ACTION_SCALE, ACTION_ALPHA, ACTION_ALPHA_HEAD,
                     OBS_DIM, ACTION_DIM, State, build_obs as make_obs,
                     action_to_deg as deg_from_action)
HOME_RAD = np.deg2rad(HOME_DEG)
PYTHON_DIR = Path(__file__).resolve().parent

def default_onnx() -> Optional[Path]:
    p = PYTHON_DIR / 'xgoduck_walk.onnx'
    return p if p.is_file() else None

class Policy:
    def __init__(self):
        self.action_scale = ACTION_SCALE
        self.action_alpha = ACTION_ALPHA
        self.action_alpha_head = ACTION_ALPHA_HEAD
        self.unload()

    @property
    def loaded(self):
        return self.session is not None

    def unload(self):
        self.session = None
        self.in_name = self.file = ''
        self.home_rad = HOME_RAD.copy()
        self._obs = np.empty(OBS_DIM, dtype=np.float32)
        self.reset_action_filter()

    def load(self, path=None):
        p = Path(path) if path else default_onnx()
        if p is None:
            return False, 'default model xgoduck_walk.onnx was not found'
        if not p.is_file():
            p = PYTHON_DIR / p.name
        if not p.is_file():
            return False, f'not found: {p}'
        try:
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.intra_op_num_threads = 1
            options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
            sess = ort.InferenceSession(str(p), sess_options=options, providers=['CPUExecutionProvider'])
            inp, out = sess.get_inputs()[0], sess.get_outputs()[0]
            if len(inp.shape) != 2 or inp.shape[-1] != OBS_DIM:
                raise ValueError(f'expected observation shape [1,61], got {inp.shape}')
            if len(out.shape) != 2 or out.shape[-1] != ACTION_DIM:
                raise ValueError(f'expected action shape [1,14], got {out.shape}')
            self.session, self.in_name = sess, inp.name
            self._outputs = [out.name]
            self.file = p.name
            self._feed = {self.in_name: self._obs.reshape(1, OBS_DIM)}
            # Touch all graph arenas before the control loop so the first live
            # switch does not pay cold-session latency.
            result = self.warm(12)
            if result.shape != (1, ACTION_DIM) or not np.isfinite(result).all():
                raise ValueError('model must emit a finite [1,14] action')
        except Exception as exc:
            self.unload()
            return False, str(exc)
        self.reset_action_filter()
        return True, f'{self.file} in=61 out=14 threads=1'

    def warm(self, runs: int = 1):
        if not self.loaded:
            return None
        self._obs.fill(0)
        last = None
        for _ in range(max(1, runs)):
            last = self.session.run(self._outputs, self._feed)[0]
        return last

    def build_obs(self, grav, gyro_rad, angles_deg, vels_deg, cmd=None, head=None):
        st = State([0.0] * 3, [0.0] * 3, list(angles_deg), list(vels_deg),
                   list(grav), list(gyro_rad))
        twist = np.zeros(3, dtype=np.float32) if cmd is None else cmd
        head_cmd = np.zeros(4, dtype=np.float32) if head is None else head
        obs = make_obs(st, self.prev_action, twist, head_cmd, out=self._obs)
        if not np.isfinite(obs).all():
            raise ValueError('observation contains NaN or Inf')
        return obs

    def infer(self, grav, gyro_rad, angles_deg, vels_deg, cmd=None, head=None):
        if not self.loaded:
            return None
        self.build_obs(grav, gyro_rad, angles_deg, vels_deg, cmd, head)
        action = self.session.run(self._outputs, self._feed)[0].reshape(ACTION_DIM)
        if not np.isfinite(action).all():
            raise ValueError('model output contains NaN or Inf')
        self.prev_action[:] = action
        # Keep the reference script's exact slices, including ID 20 in [5:10].
        for sl, alpha in ((slice(0, 5), self.action_alpha),
                          (slice(5, 10), self.action_alpha_head),
                          (slice(10, 14), self.action_alpha)):
            self._act_filt[sl] *= alpha
            self._act_filt[sl] += action[sl] * (1 - alpha)
        return self._act_filt.copy()

    def set_action_alphas(self, *, leg=None, head=None):
        # Validate the complete update first so a bad head value cannot change leg.
        leg = self.action_alpha if leg is None else float(leg)
        head = self.action_alpha_head if head is None else float(head)
        if any(not math.isfinite(v) or not 0 <= v <= 1 for v in (leg, head)):
            raise ValueError('action alpha must be within [0,1]')
        self.action_alpha, self.action_alpha_head = leg, head
        return leg, head

    def reset_action_filter(self):
        self.prev_action = np.zeros(ACTION_DIM, dtype=np.float32)
        self._act_filt = np.zeros(ACTION_DIM, dtype=np.float32)

    def action_to_deg(self, action):
        return deg_from_action(np.asarray(action, dtype=np.float32))
