"""Detect falls from directional tilt and run the get-up policy until upright."""
import math


class Recovery:
    FALL_DEG, FALL_SECONDS = 55.0, 0.15
    FALL_ROLL_DEG = 35.0
    STAND_DEG, STAND_SECONDS = 15.0, 1.0
    HOLD_SECONDS = 0.0

    def __init__(self, period_s=0.02):
        self.PERIOD = period_s
        self.reset()

    def reset(self):
        self.phase = 'walk'
        self.invalidate()
        self.transitions = 0

    def invalidate(self):
        self.fall_elapsed = self.stand_elapsed = 0.0
        self.angle_deg = self.pitch_deg = self.roll_deg = None

    def update(self, gravity, now, valid=True):
        norm = math.sqrt(sum(v*v for v in gravity))
        if not valid or not math.isfinite(norm) or norm < 1e-6:
            self.invalidate()
            return False
        gx, gy, gz = gravity
        self.angle_deg = math.degrees(math.acos(max(-1.0, min(1.0, -gz/norm))))
        # Match the body's forward and lateral tilt convention in rl_core.
        self.pitch_deg = math.degrees(math.atan2(gx, -gz))
        self.roll_deg = math.degrees(math.atan2(-gy, -gz))
        before = self.phase
        if self.phase == 'walk':
            if abs(self.pitch_deg) > self.FALL_DEG or abs(self.roll_deg) > self.FALL_ROLL_DEG:
                self.fall_elapsed += self.PERIOD
                if self.fall_elapsed >= self.FALL_SECONDS:
                    self.phase = 'getup'
                    self.stand_elapsed = 0.0
            else:
                self.fall_elapsed = 0.0
        elif self.phase == 'getup':
            if self.angle_deg < self.STAND_DEG:
                self.stand_elapsed += self.PERIOD
                if self.stand_elapsed >= self.STAND_SECONDS:
                    self.phase = 'walk'
                    self.fall_elapsed = 0.0
            else:
                self.stand_elapsed = 0.0
        changed = before != self.phase
        self.transitions += int(changed)
        return changed

    def status(self):
        return dict(phase=self.phase, angle_deg=self.angle_deg,
                    pitch_deg=self.pitch_deg, roll_deg=self.roll_deg,
                    fall_elapsed_s=self.fall_elapsed, stand_elapsed_s=self.stand_elapsed,
                    hold_remaining_s=0.0, transitions=self.transitions,
                    thresholds=dict(fall_deg=self.FALL_DEG, fall_pitch_deg=self.FALL_DEG,
                                    fall_roll_deg=self.FALL_ROLL_DEG, fall_s=self.FALL_SECONDS,
                                    default_pose_s=self.HOLD_SECONDS,
                                    stand_deg=self.STAND_DEG, stand_s=self.STAND_SECONDS))
