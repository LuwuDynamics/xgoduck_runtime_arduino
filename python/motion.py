"""Velocity slew limiting at the policy control rate, independent of HTTP timing."""
import math

class MotionRamp:
    # Base m/s² on translation, rad/s² on yaw.
    base_rates = (0.5, 0.4, 1.5)

    def __init__(self):
        self.factor = 1.0
        self.reset()

    @property
    def rates(self):
        return tuple(rate * self.factor for rate in self.base_rates)

    def configure(self, factor):
        factor = float(factor)
        if not math.isfinite(factor) or not 0.25 <= factor <= 3.0:
            raise ValueError("ramp factor must be between 0.25 and 3.0")
        self.factor = factor

    def reset(self):
        self.value = [0.0, 0.0, 0.0]

    def update(self, target, period):
        self.value = [old + max(-rate*period, min(rate*period, goal-old))
                      for old, goal, rate in zip(self.value, target, self.rates)]
        return list(self.value)
