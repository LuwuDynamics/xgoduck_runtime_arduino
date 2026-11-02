"""Read servo RAM gains on request without overriding user-selected values."""
from wire import SERVO_READ_RUN_GAINS, encode_servo


class RuntimeGainsReader:
    def __init__(self, controller):
        self.controller = controller

    def status(self):
        return dict(state='manual', auto_apply=False)

    def _exchange(self, sid):
        op = SERVO_READ_RUN_GAINS
        c = self.controller
        with c.lock:
            # The UI mode can change before the MCU acknowledges leaving its
            # exclusive bus mode. Do not overwrite that pending exit request.
            fb = getattr(c, 'feedback', None)
            if (c.mode in ('calibrate', 'servo_debug')
                    or getattr(fb, 'calibrating', False)
                    or getattr(fb, 'servo_debugging', False)):
                raise ValueError('Runtime gain reads wait until calibration/servo setup closes')
            c.servo_reply_event.clear()
            c.servo_reply = None
            c.servo_expected_reply = (op, sid)
        try:
            payload = encode_servo(op, sid, kp=0, kd=0)
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
