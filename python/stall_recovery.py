"""Confirm a stalled or thermally overloaded joint and supervise one return to the default stance."""
from collections import deque
import math

from recovery import Recovery
from rl_core import MOUTH_INDEX
from wire import (ALL_MASK, NO_SERVO_ERRORS, PROTECTION_OVERCURRENT,
                  PROTECTION_OVERHEAT, PROTECTION_VOLTAGE, elapsed_us)


class StallRecovery:
    CONFIRM_US = 600_000
    MAX_GAP_US = 80_000
    MIN_ERROR_DEG = 12.
    STILL_DEG = 1.5
    # 1 servo speed LSB is 4.39 deg/s (2 LSB = 8.79 deg/s); use filtered velocity
    # so 1-2 frame quantization jitter cannot reset stall confirmation.
    STILL_SPEED = 9.
    GETUP_ACTIVE_GYRO_RAD_S = math.radians(20.)
    PEAK_SPEED_DEG_S = 90.
    MAX_RETURN_S = 4.
    OVERCURRENT_NO_PROGRESS_US = 1_500_000
    OTHER_ALARM_US = 800_000
    HARD_FAULT_US = 800_000
    HARD_FAULT_MASK = PROTECTION_OVERHEAT | PROTECTION_VOLTAGE
    ALARM_CLEAR_GRACE_US = 100_000
    RETREAT_MISSING_US = 30_000
    MONITOR_RESET_US = 45_000_000
    MAX_THERMAL_US = 10_000_000
    PROGRESS_DEG = 1.
    SETTLE_US = 200_000
    POSE_TOLERANCE_DEG = 5.
    SINGLE_JOINT_TOLERANCE_DEG = 10.
    SETTLE_SPEED_DEG_S = 12.
    BODY_SETTLE_RAD_S = math.radians(25.)
    # Mouth gripping an object must never unload the legs and collapse the duck.
    IGNORED_INDICES = (MOUTH_INDEX,)

    # Per-activity (stall_confirm_us, thermal_limit_us):
    # - stall_confirm_us: locked-rotor overcurrent threshold (None = do not trip on pure 0x08 stall).
    # - thermal_limit_us: cumulative overcurrent leaky-bucket fallback regardless of angle error
    #                     (None = accumulate heat without interrupting short bounded actions).
    ACTIVITY_PROFILES = {
        'walk': (600_000, 5_000_000),
        'sit': (1_500_000, 5_000_000),
        'pick': (1_500_000, 5_000_000),
        'getup': (2_000_000, 6_000_000),
        'dynamic': (None, None),
        'roller': (None, 8_000_000),
    }

    def __init__(self):
        self.reset()

    def reset(self):
        self.phase = 'idle'
        self.clear_candidates()
        self.indices = ()
        self.started = None
        self.invalid = False
        self.home = ()
        self.overcurrent_progress = {}
        self.other_alarm_us = {}
        self.persistent_alarms = {}
        self.missing_since = None
        self.posture = None
        self.posture_since = None
        self.resume_phase = None
        self.monitor_clean_since = None

    def clear_candidates(self):
        self.candidates = {}
        self.stall_heat = {}
        self.thermal_heat = {}
        self.hard_fault_us = {}
        self.qualified = ()
        self.trigger_reason = None
        self.last_sample = None

    def finish_retreat(self):
        self.phase = 'monitor'
        self.invalid = False
        self.overcurrent_progress = {}
        self.other_alarm_us = {}
        self.persistent_alarms = {}
        self.missing_since = None
        self.posture = None
        self.posture_since = None
        self.resume_phase = None
        self.monitor_clean_since = None
        self.candidates = {}
        self.stall_heat = {}
        self.thermal_heat = {}
        self.hard_fault_us = {}
        self.qualified = ()
        self.trigger_reason = None

    def observe(self, fb, filtered, *, activity='walk', overcurrent_warning_only=False):
        """Use MCU samples so repeated control ticks cannot confirm a stall or a release."""
        if overcurrent_warning_only and activity == 'walk':
            activity = 'roller'
        stamp = fb.sample_us
        prev = self.last_sample
        dt_us = 0 if prev is None else elapsed_us(stamp, prev)
        gap = prev is not None and dt_us > self.MAX_GAP_US
        self.last_sample = stamp
        if self.phase == 'retreat':
            if gap or fb.servo_errors is None or not fb.imu_ok:
                self.invalid = True
            elif fb.mask != ALL_MASK:
                if self.missing_since is None:
                    self.missing_since = stamp
                elif elapsed_us(stamp, self.missing_since) > self.RETREAT_MISSING_US:
                    self.invalid = True
                self.posture = None
                self.posture_since = None
                self.resume_phase = None
                if not self.invalid:
                    self._track_return_alarms(fb, dt_us)
            else:
                self.missing_since = None
                self._observe_return(fb, filtered, dt_us)
            return
        if self.phase == 'disarmed':
            return
        if gap:
            self.candidates.clear()
            self.stall_heat.clear()
            self.monitor_clean_since = None
            dt_us = 0
        errors = fb.servo_errors
        if errors is None:
            self.candidates.clear()
            self.stall_heat.clear()
            self.qualified = ()
            self.trigger_reason = None
            self.monitor_clean_since = None
            return

        stall_limit_us, thermal_limit_us = self.ACTIVITY_PROFILES.get(activity, self.ACTIVITY_PROFILES['walk'])
        vels = filtered['vels_deg'] if filtered and 'vels_deg' in filtered else fb.velocities
        gyro_rad = filtered['gyro_rad'] if filtered and 'gyro_rad' in filtered else (0., 0., 0.)
        getup_moving = activity == 'getup' and math.hypot(*gyro_rad) > self.GETUP_ACTIVE_GYRO_RAD_S
        cool_us = dt_us // 2

        stall_hits = []
        thermal_hits = []
        fault_hits = []
        for i, code in enumerate(errors):
            if i in self.IGNORED_INDICES:
                continue
            online = bool(fb.mask & (1 << i))
            has_oc = bool(code & PROTECTION_OVERCURRENT)
            extra_fault = bool(code & ~PROTECTION_OVERCURRENT)
            hard_fault = bool(code & self.HARD_FAULT_MASK)

            # Channel C: hardware hard faults (overheat 0x04 / voltage 0x01), even on error-only packets.
            if hard_fault:
                if i not in self.hard_fault_us:
                    self.hard_fault_us[i] = 0
                else:
                    self.hard_fault_us[i] += dt_us
                if self.hard_fault_us[i] >= self.HARD_FAULT_US:
                    fault_hits.append(i)
            elif i in self.hard_fault_us:
                remaining = self.hard_fault_us[i] - cool_us
                if remaining <= 0:
                    self.hard_fault_us.pop(i)
                else:
                    self.hard_fault_us[i] = remaining

            # Channel B: cumulative overcurrent thermal leaky bucket (also catches < 12 deg static overload).
            if has_oc:
                if i not in self.thermal_heat:
                    self.thermal_heat[i] = 0
                else:
                    self.thermal_heat[i] = min(self.MAX_THERMAL_US, self.thermal_heat[i] + dt_us)
                if thermal_limit_us is not None and self.thermal_heat[i] >= thermal_limit_us:
                    thermal_hits.append(i)
            elif i in self.thermal_heat:
                remaining = self.thermal_heat[i] - cool_us
                if remaining <= 0:
                    self.thermal_heat.pop(i)
                else:
                    self.thermal_heat[i] = remaining

            # Channel A: locked-rotor stall confirmation with glitch/jitter tolerance.
            effective_stall_limit = self.CONFIRM_US if (stall_limit_us is None and extra_fault) else stall_limit_us
            if not online:
                if i in self.stall_heat:
                    remaining = self.stall_heat[i] - cool_us
                    if remaining <= 0:
                        self.stall_heat.pop(i)
                        self.candidates.pop(i, None)
                    else:
                        self.stall_heat[i] = remaining
                continue

            angle = fb.angles[i]
            error = fb.targets[i] - angle
            moved = False
            if i in self.candidates:
                low, high, direction = self.candidates[i]
                low, high = min(low, angle), max(high, angle)
                if high - low > self.STILL_DEG or direction * error <= 0:
                    moved = True
                else:
                    self.candidates[i] = (low, high, direction)

            if moved or abs(error) < self.MIN_ERROR_DEG:
                self.candidates.pop(i, None)
                self.stall_heat.pop(i, None)
                continue

            stalled_sample = (has_oc and effective_stall_limit is not None
                              and abs(vels[i]) <= self.STILL_SPEED)
            if stalled_sample:
                if i not in self.candidates:
                    self.candidates[i] = (angle, angle, error)
                    self.stall_heat[i] = 0
                else:
                    charge = dt_us // 4 if getup_moving else dt_us
                    self.stall_heat[i] = self.stall_heat.get(i, 0) + charge
                if self.stall_heat[i] >= effective_stall_limit:
                    stall_hits.append(i)
            elif i in self.stall_heat:
                remaining = self.stall_heat[i] - cool_us
                if remaining <= 0:
                    self.stall_heat.pop(i)
                    self.candidates.pop(i, None)
                else:
                    self.stall_heat[i] = remaining

        if fault_hits:
            self.qualified = tuple(fault_hits)
            self.trigger_reason = 'persistent hardware servo fault'
        elif stall_hits:
            self.qualified = tuple(stall_hits)
            self.trigger_reason = 'sustained overcurrent without motion'
        elif thermal_hits:
            self.qualified = tuple(thermal_hits)
            self.trigger_reason = 'sustained overcurrent thermal load'
        else:
            self.qualified = ()
            self.trigger_reason = None

        if self.phase == 'monitor':
            clean = (errors == NO_SERVO_ERRORS and not self.stall_heat
                     and not self.thermal_heat and not self.hard_fault_us)
            if not clean:
                self.monitor_clean_since = None
            elif self.monitor_clean_since is None:
                self.monitor_clean_since = stamp
            elif elapsed_us(stamp, self.monitor_clean_since) >= self.MONITOR_RESET_US:
                self.phase = 'idle'
                self.monitor_clean_since = None

    def start(self, fb, home, now):
        self.phase = 'retreat'
        self.indices = self.qualified
        self.anchor = tuple(fb.angles)
        self.home = tuple(home)
        self.delta = tuple(goal - actual for goal, actual in zip(self.home, self.anchor))
        # Smoothstep has a peak slope of 1.5; all joints finish together.
        self.duration = max(.3, 1.5 * max(map(abs, self.delta)) / self.PEAK_SPEED_DEG_S)
        self.started = now
        self.deadline = now + self.MAX_RETURN_S
        self.missing_since = None
        self._track_return_alarms(fb, 0)
        self.candidates.clear()
        self.stall_heat.clear()
        self.thermal_heat.clear()
        self.hard_fault_us.clear()
        self.qualified = ()

    def target(self, now):
        progress = max(0., (now - self.started) / self.duration)
        if progress >= 1.:
            return list(self.home)
        blend = progress * progress * (3. - 2. * progress)
        return [pos + blend * delta for pos, delta in zip(self.anchor, self.delta)]

    def _overcurrent_without_progress(self, i, stamp, distance, dt_us):
        if i not in self.overcurrent_progress:
            self.overcurrent_progress[i] = [0, deque([(stamp, distance)]), None]
            return False
        entry = self.overcurrent_progress[i]
        entry[0] += dt_us
        entry[2] = None
        window = entry[1]
        while window and elapsed_us(stamp, window[0][0]) > self.OVERCURRENT_NO_PROGRESS_US:
            window.popleft()
        # Keep the largest remaining error in the recent window at the front.
        # A real move towards home still counts if the joint first recoiled.
        while window and window[-1][1] <= distance:
            window.pop()
        window.append((stamp, distance))
        return entry[0] >= self.OVERCURRENT_NO_PROGRESS_US and window[0][1] - distance < self.PROGRESS_DEG

    def _track_return_alarms(self, fb, dt_us):
        stamp = fb.sample_us
        cool_us = dt_us // 2
        for i, code in enumerate(fb.servo_errors):
            if i in self.IGNORED_INDICES:
                continue
            online = bool(fb.mask & (1 << i))
            stop_bits = 0
            if online:
                if code & PROTECTION_OVERCURRENT:
                    distance = abs(self.home[i] - fb.angles[i])
                    if self._overcurrent_without_progress(i, stamp, distance, dt_us):
                        stop_bits |= PROTECTION_OVERCURRENT
                elif i in self.overcurrent_progress:
                    entry = self.overcurrent_progress[i]
                    if entry[2] is None:
                        entry[2] = stamp
                    entry[0] = max(0, entry[0] - cool_us)
                    if elapsed_us(stamp, entry[2]) >= self.ALARM_CLEAR_GRACE_US or entry[0] == 0:
                        self.overcurrent_progress.pop(i, None)

            other_bits = code & ~PROTECTION_OVERCURRENT
            if other_bits:
                if i not in self.other_alarm_us:
                    self.other_alarm_us[i] = (0, None)
                else:
                    accum, _ = self.other_alarm_us[i]
                    accum += dt_us
                    self.other_alarm_us[i] = (accum, None)
                    if accum >= self.OTHER_ALARM_US:
                        stop_bits |= other_bits
            elif online and i in self.other_alarm_us:
                accum, clear_since = self.other_alarm_us[i]
                if clear_since is None:
                    clear_since = stamp
                accum = max(0, accum - cool_us)
                if elapsed_us(stamp, clear_since) >= self.ALARM_CLEAR_GRACE_US or accum == 0:
                    self.other_alarm_us.pop(i, None)
                else:
                    self.other_alarm_us[i] = (accum, clear_since)

            if stop_bits:
                # The next control tick must see this fault even if a clear frame follows.
                self.persistent_alarms[i] = self.persistent_alarms.get(i, 0) | stop_bits

    def _at_default_stance(self, angles):
        errors = [abs(goal - actual) for goal, actual in zip(self.home, angles)]
        return (max(errors) <= self.SINGLE_JOINT_TOLERANCE_DEG
                and sum(error > self.POSE_TOLERANCE_DEG for error in errors) <= 1)

    def _observe_return(self, fb, filtered, dt_us):
        self._track_return_alarms(fb, dt_us)
        phase = None
        if (fb.servo_errors == NO_SERVO_ERRORS and filtered['imu_fusion_ready']
                and all(abs(goal - sent) < .05 for goal, sent in zip(self.home, fb.targets))
                and self._at_default_stance(fb.angles)
                and all(abs(v) <= self.SETTLE_SPEED_DEG_S for v in fb.velocities)
                and math.hypot(*filtered['gyro_rad']) <= self.BODY_SETTLE_RAD_S):
            gx, gy, gz = filtered['grav']
            norm = math.hypot(gx, gy, gz)
            if math.isfinite(norm) and norm > 1e-6:
                if -gz / norm > math.cos(math.radians(Recovery.STAND_DEG)):
                    phase = 'walk'
                elif (abs(math.degrees(math.atan2(gx, -gz))) > Recovery.FALL_DEG or
                      abs(math.degrees(math.atan2(-gy, -gz))) > Recovery.FALL_ROLL_DEG):
                    phase = 'getup'
        if phase != self.posture:
            self.posture = phase
            self.posture_since = fb.imu_us
        self.resume_phase = phase if phase and elapsed_us(fb.imu_us, self.posture_since) >= self.SETTLE_US else None
