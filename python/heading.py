"""Walk-only, unwrapped gyro heading and translation heading hold."""
import math


class HeadingHold:
    KP = 1.5  # rad/s per radian of heading error.
    MAX_WZ = 0.3  # rad/s.
    DEADBAND = math.radians(3.0)
    LEVEL_COS = math.cos(math.radians(5.0))

    def __init__(self):
        self.enabled = True
        self.reset()

    def release(self):
        self.target = None
        self.correction = 0.0

    def reset(self):
        self.yaw = None
        self.timestamp = None
        self.release()

    def observe(self, walking, gravity, gyro, timestamp):
        if not walking:
            self.reset()
            return
        if self.yaw is None:
            if -gravity[2] >= self.LEVEL_COS:
                self.yaw, self.timestamp = 0.0, timestamp
            return
        dt = timestamp - self.timestamp
        self.timestamp = timestamp
        denominator = gravity[1]**2 + gravity[2]**2
        if not 0 < dt <= 0.1 or denominator < 0.01:
            self.reset()
            return
        # Euler yaw rate from body gyro and gravity; preserve all full turns.
        self.yaw -= (gyro[1]*gravity[1] + gyro[2]*gravity[2]) / denominator * dt

    def command(self, walking, twist):
        if (not walking or not self.enabled or self.yaw is None
                or twist[2] != 0.0 or (twist[0] == 0.0 and twist[1] == 0.0)):
            self.release()
            return twist[2]
        if self.target is None:
            self.target = self.yaw
        error = self.target - self.yaw
        self.correction = (max(-self.MAX_WZ, min(self.MAX_WZ, self.KP*error))
                           if abs(error) > self.DEADBAND else 0.0)
        return self.correction

    def status(self):
        return dict(enabled=self.enabled, initialized=self.yaw is not None,
                    active=self.target is not None,
                    yaw_deg=math.degrees(self.yaw) if self.yaw is not None else None,
                    target_deg=math.degrees(self.target) if self.target is not None else None,
                    error_deg=math.degrees(self.target-self.yaw) if self.target is not None else 0.0,
                    correction_wz=self.correction, kp=self.KP, max_wz=self.MAX_WZ,
                    deadband_deg=3.0, init_tilt_deg=5.0)
