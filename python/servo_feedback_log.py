"""Record transitions in the raw servo reply mask without doing disk I/O on the control loop."""
import json
import logging
from logging.handlers import RotatingFileHandler
import queue
import threading
from collections import deque
from datetime import datetime, timezone

from rl_core import MOTOR_IDS
from wire import ALL_MASK, NO_SERVO_ERRORS, elapsed_us


def ids(mask):
    return [servo_id for index, servo_id in enumerate(MOTOR_IDS) if mask & (1 << index)]

def elapsed_ms(now, start):
    return None if start is None else round((now - start) * 1000, 1)

def reply(fb, i):
    bit = 1 << i
    if fb.servo_errors is None:
        return ('ok' if fb.mask & bit else 'unknown'), None
    error = fb.servo_errors[i]
    if fb.mask & bit:
        return ('ok_with_alarm' if error else 'ok'), error
    if fb.invalid_position_mask & bit:
        return 'invalid_position', error
    if error:
        return 'servo_error', error
    if fb.unexpected_length_mask & bit:
        return 'unexpected_length', 0
    if fb.bad_checksum_mask & bit:
        return 'bad_checksum', None
    return 'no_reply_in_window', None


def format_history(frames, motor_mask):
    end = frames[-1].sample_us
    result = {}
    for i, motor_id in enumerate(MOTOR_IDS):
        if not motor_mask & (1 << i):
            continue
        samples = []
        for fb in frames:
            valid = fb.mask & (1 << i)
            kind, code = reply(fb, i)
            samples.append([-elapsed_us(end, fb.sample_us) / 1000, fb.seq, round(fb.targets[i], 2),
                            round(fb.angles[i], 2) if valid else None,
                            round(fb.velocities[i], 2) if valid else None, code, kind])
        result[str(motor_id)] = samples
    return result


class ServoFeedbackLog:
    def __init__(self, path):
        self.events = queue.Queue(maxsize=128)
        self.frames = deque(maxlen=128)
        self.skipped = 0
        self.started = self.requested = self.released = None
        self.lowest_mv = None
        self.affected = 0
        self.epoch_start = 0.
        self.file = RotatingFileHandler(path, maxBytes=8 * 1024 * 1024, backupCount=2,
                                        encoding='utf-8', delay=True)
        self.writer = threading.Thread(target=self._write, name='servo-feedback-log', daemon=True)
        self.writer.start()

    def _write(self):
        while (event := self.events.get()) is not None:
            if 'frames' in event:
                frames, mask = event.pop('frames')
                event['history_columns'] = ['offset_ms', 'seq', 'target_deg', 'actual_deg',
                                            'velocity_deg_s', 'raw_error', 'reply']
                event['history_by_id'] = format_history(frames, mask)
            record = logging.LogRecord('servo-feedback', logging.INFO, __file__, 0,
                                       json.dumps(event, separators=(',', ':')), (), None)
            self.file.handle(record)
        self.file.close()

    def close(self):
        self.events.put(None)
        self.writer.join()

    def record(self, event, fb, mode, now, voltage_mv, voltage_at, **fields):
        age = now - voltage_at if voltage_at and voltage_at >= self.epoch_start else None
        row = dict(time=datetime.now(timezone.utc).isoformat(timespec='milliseconds'), event=event,
                   seq=fb.seq, mode=mode, torque_enabled=fb.enabled, online=fb.mask.bit_count(),
                   missing_ids=ids(ALL_MASK ^ fb.mask),
                   bus_voltage_v=voltage_mv / 1000 if voltage_mv and age is not None and 0 <= age <= .4 else None,
                   voltage_age_ms=round(age * 1000, 1) if age is not None else None, **fields)
        row['reply_diagnostics'] = fb.servo_errors is not None
        row['replies'] = {}
        for i, motor_id in enumerate(MOTOR_IDS):
            bit = 1 << i
            if not (fb.mask & bit) or (fb.servo_errors and fb.servo_errors[i]) or (
                    (fb.bad_checksum_mask | fb.unexpected_length_mask | fb.invalid_position_mask) & bit):
                kind, code = reply(fb, i)
                row['replies'][str(motor_id)] = dict(kind=kind, raw_error=code,
                    bad_checksum=bool(fb.bad_checksum_mask & bit),
                    unexpected_length=bool(fb.unexpected_length_mask & bit),
                    invalid_position=bool(fb.invalid_position_mask & bit))
        if self.skipped:
            row['skipped_log_events'] = self.skipped
        try:
            self.events.put_nowait(row)
            self.skipped = 0
        except queue.Full:
            self.skipped += 1

    def request_off(self, fb, mode, now, voltage_mv, voltage_at, feedback_at):
        if self.started is not None and self.requested is None and fb is not None:
            self.requested = now
            self.record('unload_requested', fb, mode, now, voltage_mv, voltage_at,
                         feedback_age_ms=round((now - feedback_at) * 1000, 1))

    def sample_voltage(self, mv):
        if self.started is not None and mv and (self.lowest_mv is None or mv < self.lowest_mv):
            self.lowest_mv = mv

    def observe(self, fb, previous, mode, now, voltage_mv, voltage_at, *, reset=False):
        if reset:
            self.frames.clear()
            self.started = self.requested = self.released = None
            self.affected = 0
            self.lowest_mv = None
            self.epoch_start = now
            previous = None
        while self.frames and elapsed_us(fb.sample_us, self.frames[0].sample_us) > 500_000:
            self.frames.popleft()
        self.frames.append(fb)
        if previous is not None:
            reply_changed = (fb.servo_errors != previous.servo_errors
                             or fb.bad_checksum_mask != previous.bad_checksum_mask
                             or fb.unexpected_length_mask != previous.unexpected_length_mask
                             or fb.invalid_position_mask != previous.invalid_position_mask)
            if not reply_changed and previous.mask == fb.mask and previous.enabled == fb.enabled:
                return
        missing = ALL_MASK ^ fb.mask
        codes = fb.servo_errors or NO_SERVO_ERRORS
        old_codes = previous.servo_errors if previous and previous.servo_errors else NO_SERVO_ERRORS
        if codes != old_codes:
            alarms = {str(motor_id): code for motor_id, code in zip(MOTOR_IDS, codes) if code}
            new_online_alarms = sum(1 << i for i, code in enumerate(codes) if code and not old_codes[i]) & fb.mask
            alarm_history = {'frames': (tuple(self.frames), new_online_alarms)} if new_online_alarms else {}
            self.record('servo_alarm_changed', fb, mode, now, voltage_mv, voltage_at,
                         alarm_codes=alarms, **alarm_history)
        if missing and self.started is None:
            self.started = now
        self.affected |= missing

        newly_missing = missing if previous is None else missing & previous.mask
        history = {'frames': (tuple(self.frames), newly_missing)} if newly_missing else {}
        if previous is None:
            self.record('mcu_reset' if reset else 'monitor_started', fb, mode, now, voltage_mv, voltage_at, **history)
        elif missing and previous.mask != fb.mask:
            self.record('missing', fb, mode, now, voltage_mv, voltage_at, **history)
        elif missing and reply_changed:
            self.record('reply_changed', fb, mode, now, voltage_mv, voltage_at)
        if self.started is None:
            return
        if previous is not None and previous.enabled and not fb.enabled and self.released is None:
            self.released = now
            self.record('torque_off', fb, mode, now, voltage_mv, voltage_at)
        if not missing:
            self.record('all_replied', fb, mode, now, voltage_mv, voltage_at,
                         affected_ids=ids(self.affected), missing_ms=elapsed_ms(now, self.started),
                         min_bus_voltage_v=self.lowest_mv / 1000 if self.lowest_mv is not None else None,
                         since_unload_request_ms=elapsed_ms(now, self.requested),
                         since_torque_off_ms=elapsed_ms(now, self.released))
            self.started = self.requested = self.released = None
            self.lowest_mv = None
            self.affected = 0
