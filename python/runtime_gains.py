"""Read servo parameters on request without overriding user-selected values."""
import time

from rl_core import MOTOR_IDS
from wire import ALL_MASK, SERVO_READ_PROTECTION, SERVO_READ_RUN_GAINS, encode_servo

RECOMMENDED_GAINS = (5, 10)
OVERCURRENT_WARN_BELOW_A = 1.6


class ServoParameterReader:
    def __init__(self, controller):
        self.controller = controller

    def status(self):
        return dict(state='manual', auto_apply=False)

    def _exchange(self, sid, op=SERVO_READ_RUN_GAINS, *, idle_only=False):
        c = self.controller
        payload = encode_servo(op, sid, kp=0, kd=0)
        try:
            with c.lock:
                fb = getattr(c, 'feedback', None)
                if (c.mode == 'calibrate'
                        or getattr(fb, 'calibrating', False)
                        or (c.mode == 'servo_debug') != bool(getattr(fb, 'servo_debugging', False))):
                    raise ValueError('Runtime gain reads wait until calibration or servo setup transitions finish')
                if idle_only and self._idle_feedback() is None:
                    raise ValueError('Servo checks wait for torque off')
                c.servo_reply_event.clear()
                c.servo_reply = None
                c.servo_expected_reply = (op, sid)
                c.bridge.notify('duck_servo', payload)
            return c._wait_servo_reply(op, target_id=sid)
        finally:
            with c.lock:
                c.servo_expected_reply = None

    def read(self, sid):
        if isinstance(sid, bool) or not isinstance(sid, int) or not 0 <= sid <= 253:
            raise ValueError('Servo ID must be between 0 and 253')
        with self.controller.servo_op_lock:
            return dict(self._exchange(sid), runtime=True)

    def _idle_feedback(self):
        c = self.controller
        with c.lock:
            fb = c.feedback
            if (c.mode in ('off', 'shadow') and fb is not None
                    and not c._imu_job['active'] and not (fb.enabled or fb.calibrating or fb.servo_debugging)
                    and time.monotonic() - c.rx_time < .15):
                return fb
            return None

    def check(self):
        """Check all installed joints when extra reads cannot disturb walking."""
        lock = self.controller.servo_op_lock
        if not lock.acquire(blocking=False):
            return {'state': 'waiting'}
        try:
            fb = self._idle_feedback()
            if fb is None or fb.mask != ALL_MASK:
                return {'state': 'waiting'}
            mismatches = []
            for sid in MOTOR_IDS:
                if self._idle_feedback() is None:
                    return {'state': 'waiting'}
                try:
                    reply = self._exchange(sid, idle_only=True)
                except (OSError, ValueError):
                    return {'state': 'unavailable', 'target_id': sid}
                kp, kd = reply['kp'], reply['kd']
                if (kp, kd) != RECOMMENDED_GAINS:
                    mismatches.append({'id': sid, 'kp': kp, 'kd': kd})
            if self._idle_feedback() is None:
                return {'state': 'waiting'}
            return {'state': 'checked', 'mismatches': mismatches}
        finally:
            lock.release()

    def check_overcurrent(self):
        """Read protection thresholds for the online servos while unloaded."""
        lock = self.controller.servo_op_lock
        if not lock.acquire(blocking=False):
            return {'state': 'waiting'}
        try:
            fb = self._idle_feedback()
            if fb is None or not fb.mask:
                return {'state': 'waiting'}
            ids = [sid for index, sid in enumerate(MOTOR_IDS) if fb.mask & (1 << index)]
            checked_ids, low_thresholds = [], []
            # Allow the last 0.5 s Bridge reply wait within the web request's 2 s timeout.
            deadline = time.monotonic() + 1.4
            for sid in ids:
                if time.monotonic() >= deadline or self._idle_feedback() is None:
                    break
                try:
                    reply = self._exchange(sid, SERVO_READ_PROTECTION, idle_only=True)
                except (OSError, ValueError):
                    continue
                checked_ids.append(sid)
                if reply['overcurrent_protection'] and 0 < reply['current_a'] < OVERCURRENT_WARN_BELOW_A:
                    low_thresholds.append({'id': sid, 'current_a': round(reply['current_a'], 4)})
            if self._idle_feedback() is None:
                return {'state': 'waiting'}
            if not checked_ids:
                return {'state': 'unavailable'}
            return {'state': 'checked', 'checked_ids': checked_ids, 'low_thresholds': low_thresholds}
        finally:
            lock.release()
