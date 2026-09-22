"""Raspberry Pi recovery logic: 50 Hz condition counters and wall-clock hold."""
import math


class Recovery:
    FALL_DEG, FALL_SECONDS = 55.0, 0.15
    STAND_DEG, STAND_SECONDS = 15.0, 1.0
    HOLD_SECONDS = 1.0
    PERIOD = 1.0 / 50.0

    def __init__(self):
        self.reset()

    def reset(self):
        self.phase = 'walk'
        self.angle_deg = None
        self.phase_since = None
        self.fall_elapsed = self.stand_elapsed = self.hold_elapsed = 0.0
        self.transitions = 0

    def invalidate(self):
        self.fall_elapsed = self.stand_elapsed = 0.0
        self.angle_deg = None
        if self.phase == 'default_pose':
            self.phase_since = None
            self.hold_elapsed = 0.0

    def update(self, gravity, now, valid=True):
        norm = math.sqrt(sum(v*v for v in gravity))
        if not valid or not math.isfinite(norm) or norm < 1e-6:
            self.invalidate()
            return False
        self.angle_deg = math.degrees(math.acos(max(-1.0, min(1.0, -gravity[2]/norm))))
        before = self.phase
        if self.phase == 'walk':
            if self.angle_deg > self.FALL_DEG:
                self.fall_elapsed += self.PERIOD
                if self.fall_elapsed >= self.FALL_SECONDS:
                    self.phase = 'default_pose'
                    self.phase_since = now
                    self.hold_elapsed = 0.0
            else:
                self.fall_elapsed = 0.0
        elif self.phase == 'default_pose':
            if self.phase_since is None:
                self.phase_since = now
            self.hold_elapsed = now - self.phase_since
            if now >= self.phase_since + self.HOLD_SECONDS:
                self.phase = 'getup'
                self.stand_elapsed = 0.0
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
                    fall_elapsed_s=self.fall_elapsed, stand_elapsed_s=self.stand_elapsed,
                    hold_remaining_s=max(0.0, self.HOLD_SECONDS-self.hold_elapsed)
                        if self.phase == 'default_pose' else 0.0,
                    transitions=self.transitions,
                    thresholds=dict(fall_deg=self.FALL_DEG, fall_s=self.FALL_SECONDS,
                                    default_pose_s=self.HOLD_SECONDS,
                                    stand_deg=self.STAND_DEG, stand_s=self.STAND_SECONDS))
