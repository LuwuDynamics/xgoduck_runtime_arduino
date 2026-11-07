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
            # Reading RAM gains is allowed in confirmed servo setup. Do not
            # overwrite a calibration operation or a pending setup enter/exit.
            fb = getattr(c, 'feedback', None)
            if (c.mode == 'calibrate'
                    or getattr(fb, 'calibrating', False)
                    or (c.mode == 'servo_debug') != bool(getattr(fb, 'servo_debugging', False))):
                raise ValueError('Runtime gain reads wait until calibration or servo setup transitions finish')
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
