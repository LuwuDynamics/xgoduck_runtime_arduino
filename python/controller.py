"""100Hz receive/filter callback and independent 50Hz ONNX loop."""
import collections
import json
import math
import threading
import time
from pathlib import Path
from policy import Policy
from recovery import Recovery
from proc import SignalFilter
from rl_core import MOTOR_IDS, HOME_DEG, DEFAULT_DEG, GRAVITY_TAU, IMU_BIAS_DEG, MOUTH_INDEX, MOUTH_MAX_DEG
from wire import *

FACTORY_ZERO = [
    2236, 2005, 1912, 1866, 1945,
    2127, 2135, 1914, 2228, 2206,
    2550, 2060, 2128, 2034, 2190,
]
ZERO_PATH = Path(__file__).resolve().parents[1] / 'data' / 'zero_pos.json'
EXCLUSIVE_MODES = ('calibrate', 'servo_debug')
PICK_PERIOD_S = 4.0
PICK_MOUTH_CLOSE_PHI = 0.4
ROULADE_S = 1.9
ROULADE_ALPHA = 0.15
CONTROL_PERIOD = 0.02

class Controller:
    def __init__(self, bridge):
        self.bridge = bridge
        self.lock = threading.RLock()
        self.servo_op_lock = threading.Lock()
        self.policy_lock = threading.Lock()
        self.policy = Policy()
        ok, message = self.policy.load()
        if not ok:
            raise RuntimeError(message)
        self.model_message = message
        self.getup_policy = Policy()
        ok, message = self.getup_policy.load('xgoduck_getup.onnx')
        if not ok:
            raise RuntimeError(message)
        self.model_message += '; ' + message
        self.pick_policy = Policy()
        ok, message = self.pick_policy.load('xgoduck_pick.onnx')
        if not ok:
            raise RuntimeError(message)
        self.model_message += '; ' + message
        self.roulade_policy = Policy()
        ok, message = self.roulade_policy.load('xgoduck_roulade.onnx')
        if not ok:
            raise RuntimeError(message)
        self.model_message += '; ' + message
        self.recovery = Recovery()
        self.policy_needs_reset = True
        self.pick_active = False
        self.pick_phi = 0.0
        self.roulade_until = 0.0
        self._roulade_alpha = None
        self._warm_index = 0
        self.filter = SignalFilter()
        self.feedback = self.filtered = None
        self.rx_time = 0.0
        self.mode = 'shadow'
        self.generation = 0
        self.twist, self.head, self.mouth = [0.0]*3, [0.0]*4, 0.0
        self.web_time = 0.0
        self.input_source = 'web'
        self.arm_pending = False
        self._arm_ack_seq = None
        self._arm_ack_deadline = 0.0
        self.fault_hold = False
        self._pause_sent = False
        self.pause_events = collections.deque(maxlen=8)
        self.timeout_events = []
        self.timeout_warning = ''
        self.diagnostic_paused = False
        self.seq = self.received = self.inferred = self.sent = self.bad_frames = self.gaps = 0
        self.last_error = ''
        self.stop_event = threading.Event()
        self.rx_times = collections.deque(maxlen=1000)
        self.infer_times = collections.deque(maxlen=500)
        self.infer_ms = collections.deque(maxlen=500)
        self.rx_intervals = collections.deque(maxlen=1000)
        self.loop_missed = 0
        self.last_action = [0.0]*15
        self.started = time.monotonic()
        self.boot_pd = {}
        self.raw_pos = list(FACTORY_ZERO)
        self.zero_pos = list(FACTORY_ZERO)
        self.cal_done = [False]*15
        self.cal_targets = [2047]*15
        self.raw_mask = 0
        self.servo_reply = None
        self.servo_reply_event = threading.Event()
        self.servo_expected_reply = None
        self._zeros_pushed = False
        self._load_zeros()

    def _load_zeros(self):
        try:
            data = json.loads(ZERO_PATH.read_text(encoding='utf-8'))
            values = [int(v) for v in data['zero_pos']]
            if len(values) != 15 or any(not 0 <= v <= 4095 for v in values):
                return
            self.zero_pos = values
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass

    def _save_zeros(self):
        ZERO_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            'version': 1,
            'ids': list(MOTOR_IDS),
            'zero_pos': list(self.zero_pos),
            'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
        }
        ZERO_PATH.write_text(json.dumps(payload, indent=2), encoding='utf-8')

    def _push_zeros(self):
        self.bridge.notify('duck_cal', encode_cal(CAL_SET_ZEROS, mask=ALL_MASK, raw=self.zero_pos))
        self._zeros_pushed = True

    def receive_boot_pd(self, raw):
        if not isinstance(raw, (bytes, bytearray)) or len(raw)!=32: return
        mask=int.from_bytes(raw[:2],'little')
        with self.lock:
            self.boot_pd={str(id):{'p':raw[2+i],'d':raw[17+i]} for i,id in enumerate(MOTOR_IDS) if mask&(1<<i)}

    def receive_raw(self, raw):
        try:
            pkt = decode_raw(raw)
        except ValueError:
            return
        with self.lock:
            if not self._zeros_pushed:
                self._push_zeros()
            self.raw_pos = pkt['pos']
            self.raw_mask = pkt['mask']
            # After a push settles, trust MCU zeros; until then keep file values.
            if self._zeros_pushed:
                self.zero_pos = pkt['zero']

    def receive_servo_reply(self, raw):
        try:
            pkt = decode_servo_reply(raw)
        except ValueError:
            return
        with self.lock:
            if self.servo_expected_reply != (pkt['op'], pkt['target_id']):
                return
            self.servo_reply = pkt
            self.servo_reply_event.set()

    def receive(self, raw):
        try:
            fb = decode_state(raw)
        except ValueError:
            with self.lock: self.bad_frames += 1
            return
        now = time.monotonic()
        with self.lock:
            old = self.feedback
            if old is None:
                # A restarted Linux App must not disarm an already enabled MCU,
                # and its new command sequence must continue the MCU watermark.
                self.seq = fb.command_seq
                if fb.enabled:
                    self._pause_motion_locked('App attached to enabled motors', adopt=True)
                elif fb.calibrating or fb.servo_debugging:
                    self.mode = 'calibrate' if fb.calibrating else 'servo_debug'
                    self.diagnostic_paused = True
                    self.last_error = 'App attached to a diagnostic pose; positions retained until explicit control.'
            if old:
                step = elapsed_us(fb.seq, old.seq)
                if step == 0: return
                if step > 0x80000000:
                    self._pause_motion_locked('MCU feedback sequence restarted')
                    self.arm_pending = False; self.filter.reset()
                    self.recovery.reset(); self.policy_needs_reset = True
                    self._stop_pick_locked(); self._stop_roulade_locked()
                    self._zeros_pushed = False
                else:
                    self.gaps += step-1
                self.rx_intervals.append((now-self.rx_time)*1000)
            self.filtered = self.filter.update(fb.acc, fb.gyro, fb.angles, fb.velocities,
                timestamp=now, imu_ok=fb.imu_ok)
            self.feedback, self.rx_time = fb, now
            self.received += 1; self.rx_times.append(now)
            waiting_arm = self.arm_pending or getattr(self, '_arm_ack_seq', None) is not None
            if (waiting_arm and not self._timeouts_disabled(fb)
                    and now >= getattr(self, '_arm_ack_deadline', float('inf'))):
                self._pause_motion_locked('MCU enable acknowledgement timed out')
                waiting_arm = False
            ack_seq = getattr(self, '_arm_ack_seq', None)
            if ack_seq is not None and elapsed_us(fb.command_seq, ack_seq) < 0x80000000:
                # Only feedback acknowledging this explicit ARM can end the wait.
                # A queued pre-ARM pause frame must not cancel the new request.
                self._arm_ack_seq = None
                waiting_arm = False
                if not fb.enabled or fb.flags & 4:
                    self._pause_motion_locked('MCU did not confirm enable / resume')
            if fb.enabled and fb.flags & 4 and self.mode in ('hold', 'policy') and not waiting_arm:
                self._pause_motion_locked('MCU command/IMU timeout; torque held')

    def _pause_motion_locked(self, reason, *, adopt=False):
        if self.mode not in ('hold', 'policy') and not adopt:
            return
        if not self.fault_hold:
            fb = self.feedback
            event = dict(reason=reason, at=time.monotonic(),
                         feedback_age_ms=(time.monotonic()-self.rx_time)*1000 if fb else None,
                         imu_ok=fb.imu_ok if fb else None,
                         command_age_us=getattr(fb, 'command_age_us', None),
                         command_seq=getattr(fb, 'command_seq', None))
            self.pause_events.append(event)
            print('[motion-pause]', json.dumps(event), flush=True)
            self.generation += 1
            self._pause_sent = False
        self.fault_hold = True
        self.mode = 'hold'
        self.arm_pending = False
        self._arm_ack_seq = None
        self.last_error = reason + '; motion paused, torque retained; explicitly resume with Stand/Policy.'
        self.twist, self.head, self.mouth = [0.]*3, [0.]*4, 0.
        self._stop_pick_locked(); self._stop_roulade_locked()

    def pause_motion(self, reason='Motion paused'):
        with self.lock:
            self._pause_motion_locked(reason)

    def _timeouts_disabled(self, feedback=None):
        fb = self.feedback if feedback is None else feedback
        return bool(fb and getattr(fb, 'flags', 0) & TIMEOUT_CHECKS_DISABLED)

    def _observe_timeouts_locked(self, now, age):
        if not self._timeouts_disabled():
            return
        fb = self.feedback
        problems = []
        if age > .15:
            problems.append('STM32 feedback older than 150 ms')
        if not fb.imu_ok:
            problems.append('IMU feedback invalid')
        imu_age = None
        if hasattr(fb, 'imu_us'):
            # sample_us is the tick start, before the I2C read. Use the end of
            # reported work so a newer IMU timestamp cannot look 2^32 us old.
            report_us = (fb.sample_us + getattr(fb, 'work_us', 0)) & 0xffffffff
            imu_age = elapsed_us(report_us, fb.imu_us)
            if imu_age > 100000:
                problems.append('MCU IMU sample older than 100 ms')
        command_age = getattr(fb, 'command_age_us', None)
        if command_age == 0xffffffff:
            problems.append('MCU has not accepted a command')
        elif command_age is not None and command_age > 250000:
            problems.append('MCU reported command age above 250 ms')
        if (getattr(self, '_arm_ack_seq', None) is not None
                and now >= self._arm_ack_deadline):
            problems.append('waiting for MCU enable acknowledgement')
        signature = tuple(problems)
        self.timeout_warning = '; '.join(problems)
        previous = getattr(self, '_timeout_signature', ())
        if (signature and now - getattr(self, '_timeout_logged_at', float('-inf')) < 5.) or (not signature and not previous):
            return
        self._timeout_signature, self._timeout_logged_at = signature, now
        event = dict(at=now, problems=problems, feedback_age_ms=age*1000,
                     last_reported_command_age_us=command_age,
                     last_reported_imu_age_us=imu_age, action='log_only')
        if not hasattr(self, 'timeout_events'):
            self.timeout_events = []
        self.timeout_events.append(event)
        del self.timeout_events[:-8]
        print('[motion-timeout-observed]', json.dumps(event), flush=True)

    def _send_pause_locked(self):
        if self._pause_sent or self.feedback is None:
            return
        self.seq = (self.seq + 1) & 0xffffffff
        # PAUSE ignores source age and all targets; the MCU captures its own
        # reliable joint positions. It cannot enable an already disabled motor.
        self.bridge.notify('duck_command', encode_command(self.seq, self.feedback.sample_us, PAUSE, DEFAULT_DEG))
        self._pause_sent = True
        self.sent += 1

    def _require_feedback(self, mask):
        fb = self.feedback
        bypass = self._timeouts_disabled()
        if not fb or (not bypass and time.monotonic()-self.rx_time > .15):
            raise ValueError('MCU feedback is stale')
        if (not bypass and not fb.imu_ok) or (fb.mask & mask) != mask:
            raise ValueError('required servo feedback / IMU missing')
        return fb

    def _disarm_now(self):
        self.arm_pending = False
        self._arm_ack_seq = None
        self.seq = (self.seq + 1) & 0xffffffff
        sample = self.feedback.sample_us if self.feedback else 0
        self.bridge.notify('duck_command', encode_command(self.seq, sample, DISARM, DEFAULT_DEG))

    def set_mode(self, mode, *, feedback_mask=None):
        if mode not in ('shadow', 'off', 'hold', 'policy', 'calibrate', 'servo_debug'):
            raise ValueError('unknown mode')
        with self.lock:
            paused = self.fault_hold
            if mode == self.mode and not (paused and mode in ('hold', 'policy')):
                self.web_time = time.monotonic()
                self.diagnostic_paused = False
                return {'mode': self.mode}
            if mode in ('hold', 'policy') and self.mode in EXCLUSIVE_MODES:
                raise ValueError('exit calibrate/servo_debug first')
            if mode in EXCLUSIVE_MODES and self.mode in ('hold', 'policy'):
                raise ValueError('disarm before calibrate/servo_debug')
            fb = None
            if mode in ('hold', 'policy'):
                required = ALL_MASK if paused else 0
                if feedback_mask is not None:
                    if isinstance(feedback_mask, bool) or not isinstance(feedback_mask, int) or not 0 <= feedback_mask <= ALL_MASK:
                        raise ValueError('invalid feedback mask')
                    required = feedback_mask
                fb = self._require_feedback(required)
                if not fb.enabled and not fb.imu_ok:
                    raise ValueError('IMU is not ready for initial motor enable')
                if not getattr(fb, 'flags', 0) & FAULT_HOLD_CAPABLE:
                    raise ValueError('STM32 firmware lacks torque-preserving pause; upload the matching sketch before enabling motors.')
            previous = self.mode
            self.generation += 1; self.mode = mode
            self.recovery.reset(); self.policy_needs_reset = True
            self._stop_pick_locked(); self._stop_roulade_locked()
            self.last_error = ''
            self.fault_hold = False
            self.diagnostic_paused = False
            self._pause_sent = False
            self.web_time = time.monotonic()
            self.twist, self.head, self.mouth = [0.0]*3, [0.0]*4, 0.0
            self.arm_pending = fb is not None and (not fb.enabled or paused or bool(getattr(fb, 'flags', 0) & 4))
            self._arm_ack_seq = None
            self._arm_ack_deadline = time.monotonic() + .25 if self.arm_pending else 0.0
            if previous == 'calibrate' and mode != 'calibrate':
                self.cal_done = [False]*15
                self.bridge.notify('duck_cal', encode_cal(CAL_EXIT))
            if previous == 'servo_debug' and mode != 'servo_debug':
                self.bridge.notify('duck_servo', encode_servo(SERVO_EXIT, 10))
            if mode == 'calibrate':
                self._disarm_now()
                self.cal_targets = [2047]*15
                self.cal_done = [False]*15
                self.bridge.notify('duck_cal', encode_cal(CAL_ENTER))
            elif mode == 'servo_debug':
                self._disarm_now()
                self.bridge.notify('duck_servo', encode_servo(SERVO_ENTER, 10))
            elif previous in EXCLUSIVE_MODES:
                self._disarm_now()
            elif mode in ('off', 'shadow'):
                self._disarm_now()
        with self.policy_lock:
            self.policy.reset_action_filter()
            self.getup_policy.reset_action_filter()
            self.pick_policy.reset_action_filter()
            self.roulade_policy.reset_action_filter()
        return {'mode': mode}

    def _models(self):
        return (self.policy, self.getup_policy, self.pick_policy, self.roulade_policy)

    def _apply_alphas_locked(self, leg, head):
        for model in self._models():
            model.set_action_alphas(leg=leg, head=head)

    def set_action_alphas(self, **values):
        with self.lock:
            with self.policy_lock:
                leg, head = self.policy.set_action_alphas(**values)
                if self._roulade_alpha is not None:
                    self._roulade_alpha = (leg, head)
                    self._apply_alphas_locked(ROULADE_ALPHA, ROULADE_ALPHA)
                else:
                    self._apply_alphas_locked(leg, head)
                return leg, head

    def _stop_pick_locked(self):
        self.pick_active = False
        self.pick_phi = 0.0

    def _stop_roulade_locked(self):
        self.roulade_until = 0.0
        saved = self._roulade_alpha
        self._roulade_alpha = None
        if saved is not None:
            with self.policy_lock:
                self._apply_alphas_locked(*saved)

    def _roulade_on(self, now):
        return now < self.roulade_until

    def start_pick(self):
        with self.lock:
            if self.mode not in ('shadow', 'policy'):
                raise ValueError('pick requires shadow/policy mode')
            if self.recovery.phase != 'walk' or self._roulade_on(time.monotonic()):
                raise ValueError('pick only while walking upright')
            self.pick_active = True
            self.pick_phi = 0.0
            self.mouth = MOUTH_MAX_DEG
            self.policy_needs_reset = True
            self.web_time = time.monotonic()
            self.last_error = ''
        with self.policy_lock:
            self.pick_policy.reset_action_filter()
            self.pick_policy.warm(2)
        return {'ok': True, 'phi': 0.0, 'model': self.pick_policy.file}

    def start_roulade(self):
        with self.lock:
            if self.mode not in ('shadow', 'policy'):
                raise ValueError('roulade requires shadow/policy mode')
            if self.recovery.phase != 'walk' or self.pick_active:
                raise ValueError('roulade only while walking')
            if self._roulade_alpha is None:
                self._roulade_alpha = (self.policy.action_alpha, self.policy.action_alpha_head)
            self.roulade_until = time.monotonic() + ROULADE_S
            self.policy_needs_reset = True
            self.web_time = time.monotonic()
            self.last_error = ''
            with self.policy_lock:
                self._apply_alphas_locked(ROULADE_ALPHA, ROULADE_ALPHA)
                self.roulade_policy.reset_action_filter()
                self.roulade_policy.warm(2)
        return {'ok': True, 'model': self.roulade_policy.file, 'seconds': ROULADE_S}

    def command(self, twist=None, head=None, mouth=None, source='web'):
        def values(seq, limits):
            if len(seq)!=len(limits): raise ValueError('wrong command dimension')
            seq = [float(v) for v in seq]
            if any(not math.isfinite(v) or abs(v)>limit for v,limit in zip(seq,limits)):
                raise ValueError('command exceeds bounds')
            return seq
        t = values(twist, [.4,.3,1.0]) if twist is not None else None
        h = values(head, [1.0]*4) if head is not None else None
        m = None
        if mouth is not None:
            m = float(mouth)
            if not math.isfinite(m) or not 0 <= m <= MOUTH_MAX_DEG:
                raise ValueError('command exceeds bounds')
        with self.lock:
            if source != self.input_source:
                return {'ok': True, 'ignored': 'inactive input source'}
            if self.fault_hold and any(v for seq in (t, h, [m] if m is not None else None) for v in (seq or [])):
                raise ValueError('Motion paused with torque held. 请明确要求“站起来”或选择 Policy 后再移动。')
            if self.mode in EXCLUSIVE_MODES:
                raise ValueError('motion commands disabled in calibrate/servo_debug')
            if t is not None and not self.pick_active: self.twist = t
            if h is not None and not self.pick_active: self.head = h
            if m is not None and not self.pick_active: self.mouth = m
            self.web_time = time.monotonic()
        return {'ok': True}

    def cal_targets_set(self, targets):
        values = [int(v) for v in targets]
        if len(values) != 15 or any(not 0 <= v <= 4095 for v in values):
            raise ValueError('15 raw targets in 0..4095 required')
        with self.lock:
            if self.mode != 'calibrate':
                raise ValueError('not in calibrate mode')
            self.cal_targets = values
            self.web_time = time.monotonic()
            self.bridge.notify('duck_cal', encode_cal(CAL_SET_RAW, mask=ALL_MASK, raw=values))
        return {'ok': True, 'targets': values}

    def cal_finish(self, index):
        index = int(index)
        if not 0 <= index < 15:
            raise ValueError('joint index out of range')
        with self.lock:
            if self.mode != 'calibrate':
                raise ValueError('not in calibrate mode')
            self.bridge.notify('duck_cal', encode_cal(CAL_FINISH_ONE, index=index))
            # Optimistically store the last reported encoder reading.
            self.zero_pos[index] = int(self.raw_pos[index])
            self.cal_done[index] = True
            self._save_zeros()
            self.web_time = time.monotonic()
            return {'ok': True, 'index': index, 'zero_pos': self.zero_pos[index],
                    'cal_done': list(self.cal_done)}

    def _require_servo_debug(self):
        if self.mode != 'servo_debug':
            raise ValueError('not in servo_debug mode')
        self.web_time = time.monotonic()

    def servo_op(self, action, target_id, new_id=None, kp=5, kd=20, raw_pos=2047):
        with self.servo_op_lock:
            if action != 'set_id':
                return self._servo_op(action, target_id, new_id, kp, kd, raw_pos)
            target_id, new_id = int(target_id), int(new_id)
            if not 0 <= target_id <= 253 or not 0 <= new_id <= 253:
                raise ValueError('ID must be between 0 and 253')
            if not self._servo_op('ping', target_id)['ok']:
                raise ValueError(f'No servo replied at ID {target_id}; ID was not changed')
            if target_id == new_id:
                return dict(ok=True, op=SERVO_SET_ID, target_id=target_id, new_id=new_id)
            if self._servo_op('ping', new_id)['ok']:
                raise ValueError(f'ID {new_id} is already in use; choose another ID')
            # Keep the checks, write and verification in one serialized operation.
            reply = self._servo_op(action, target_id, new_id, kp, kd, raw_pos)
            time.sleep(0.05)
            if not self._servo_op('ping', new_id)['ok']:
                raise ValueError(f'ID write sent, but ID {new_id} did not reply. Scan again')
            if self._servo_op('ping', target_id)['ok']:
                raise ValueError('Both old and new IDs replied. Connect only one servo and scan again')
            return dict(reply, new_id=new_id)

    def _servo_op(self, action, target_id, new_id=None, kp=5, kd=20, raw_pos=2047):
        target_id = int(target_id)
        with self.lock:
            self._require_servo_debug()
            if action == 'unlock':
                op, payload = SERVO_UNLOCK, encode_servo(SERVO_UNLOCK, target_id)
            elif action == 'set_id':
                new_id = int(new_id)
                if not 0 <= new_id <= 253:
                    raise ValueError('invalid new id')
                op, payload = SERVO_SET_ID, encode_servo(SERVO_SET_ID, target_id, new_id=new_id)
            elif action == 'goto':
                raw_pos = int(raw_pos)
                op, payload = SERVO_GOTO, encode_servo(SERVO_GOTO, target_id, raw_pos=raw_pos)
            elif action == 'set_gains':
                kp, kd = int(kp), int(kd)
                if not 0 <= kp <= 255 or not 0 <= kd <= 255:
                    raise ValueError('kp/kd out of range')
                op = SERVO_SET_PERM_KP_KD
                payload = encode_servo(SERVO_SET_PERM_KP_KD, target_id, kp=kp, kd=kd)
            elif action == 'read':
                op, payload = SERVO_READ, encode_servo(SERVO_READ, target_id)
            elif action == 'ping':
                op, payload = SERVO_PING, encode_servo(SERVO_PING, target_id)
            elif action == 'torque_off':
                op, payload = SERVO_ENTER, encode_servo(SERVO_ENTER, target_id)
            else:
                raise ValueError('unknown servo action')
            self.servo_reply_event.clear()
            self.servo_reply = None
            self.servo_expected_reply = (op, target_id)
        try:
            self.bridge.notify('duck_servo', payload)
            return self._wait_servo_reply(op, target_id=target_id)
        finally:
            with self.lock:
                self.servo_expected_reply = None

    def _wait_servo_reply(self, op, timeout=0.5, target_id=None):
        if not self.servo_reply_event.wait(timeout):
            raise ValueError('servo reply timeout')
        with self.lock:
            reply = self.servo_reply
        if not reply or reply['op'] != op:
            raise ValueError('unexpected servo reply')
        if target_id is not None and reply['target_id'] != target_id:
            raise ValueError('unexpected servo reply ID')
        if not reply['ok'] and op != SERVO_PING:
            raise ValueError('servo operation failed')
        return reply

    def step(self):
        with self.lock:
            fb, filt, generation, mode = self.feedback, self.filtered, self.generation, self.mode
            age = time.monotonic()-self.rx_time
            timeout_bypass = self._timeouts_disabled()
            self._observe_timeouts_locked(time.monotonic(), age)
            if mode in ('hold','policy'):
                lost = None
                if not timeout_bypass and (not fb or age>.15 or not fb.imu_ok):
                    lost = 'sensor feedback lost' if not fb or age>.15 else 'IMU feedback invalid'
                if lost:
                    self._pause_motion_locked(lost)
                    mode = self.mode
                    generation = self.generation
                    self.recovery.reset(); self.policy_needs_reset = True
                    self._stop_pick_locked(); self._stop_roulade_locked()
                if self.fault_hold:
                    self._send_pause_locked()
                    return
                if getattr(self, '_arm_ack_seq', None) is not None:
                    if not timeout_bypass and time.monotonic() >= self._arm_ack_deadline:
                        self._pause_motion_locked('MCU enable acknowledgement timed out')
                        self._send_pause_locked()
                    # No policy targets are sent before ARM is acknowledged.
                    return
            elif mode in EXCLUSIVE_MODES:
                if time.monotonic()-self.web_time > 2.0:
                    self.diagnostic_paused = True
                    self.last_error = 'calibrate/servo_debug heartbeat lost; current positions retained until explicit exit.'
            twist, head, mouth = list(self.twist), list(self.head), self.mouth
            valid = bool(fb and age < .15 and fb.imu_ok and filt
                         and filt['imu_fusion_ready'] and filt['imu_fusion_updated'])
            now = time.monotonic()
            if self.roulade_until and now >= self.roulade_until:
                self._stop_roulade_locked()
                self.recovery.fall_elapsed = 0.0
                self.policy_needs_reset = True
            rolling = self._roulade_on(now)
            if mode in ('shadow', 'policy') and not rolling:
                changed = self.recovery.update(filt['grav'] if filt else [0,0,0],
                                               now, valid)
                self.policy_needs_reset |= changed
                if changed and self.recovery.phase != 'walk':
                    self._stop_pick_locked()
            phase = self.recovery.phase
            if rolling and phase != 'walk':
                self._stop_roulade_locked()
                rolling = False
            picking = self.pick_active and phase == 'walk' and not rolling
            if rolling:
                twist, head = [0.0]*3, [0.0]*4
                selected = self.roulade_policy
            elif picking:
                phi = self.pick_phi
                angle = 2.0 * math.pi * phi
                twist = [math.cos(angle), math.sin(angle), 0.0]
                head = [0.0]*4
                mouth = MOUTH_MAX_DEG if phi < PICK_MOUTH_CLOSE_PHI else 0.0
                self.mouth = mouth
                selected = self.pick_policy
            else:
                selected = self.policy if phase == 'walk' else self.getup_policy
            reset_policy = self.policy_needs_reset
            inferring = mode in ('shadow','policy') and valid
            if inferring:
                self.policy_needs_reset = False
            exclusive = mode in EXCLUSIVE_MODES
        if exclusive:
            return
        # Missing/invalid samples do not latch a pause in the diagnostic build.
        # Keep the previous target; never jump to DEFAULT_DEG or run ONNX on an
        # invalid IMU merely because timeout handling was disabled.
        target = (list(getattr(self, 'last_action', fb.angles))
                  if timeout_bypass and mode == 'policy' and not valid else list(DEFAULT_DEG))
        if inferring and phase == 'default_pose':
            # Switch to getup immediately, but force action/history to zero for 1 s.
            with self.policy_lock:
                selected.reset_action_filter()
                target = selected.action_to_deg([0.0]*14)
        elif inferring:
            if phase == 'getup':
                twist, head = [0.0]*3, [0.0]*4
            begin = time.monotonic()
            with self.policy_lock:
                if reset_policy:
                    selected.reset_action_filter()
                    # One warm pass keeps the newly selected session's arenas resident.
                    selected.warm(1)
                action = selected.infer(filt['grav'], filt['gyro_rad'], filt['angles_deg'], filt['vels_deg'], twist, head)
                target = selected.action_to_deg(action)
            finish = time.monotonic()
            with self.lock:
                self.inferred += 1; self.infer_times.append(finish)
                self.infer_ms.append((finish-begin)*1000)
                if picking:
                    self.pick_phi += CONTROL_PERIOD / PICK_PERIOD_S
                    if self.pick_phi >= 1.0:
                        self._stop_pick_locked()
                        self.policy_needs_reset = True
        with self.lock:
            # A stop arriving during ONNX always wins; never send its obsolete action.
            if self.generation != generation:
                return
            mode = self.mode
            if mode in EXCLUSIVE_MODES:
                return
            command_mode = {'shadow':DISARM,'off':DISARM,'hold':HOLD,'policy':POLICY}[mode]
            if self.arm_pending:
                command_mode = ARM
                # Initial enable always uses measured angles.
                target = list(fb.angles)
                self.arm_pending = False
            else:
                target = list(target)
                target[MOUTH_INDEX] = mouth
            if fb is None: return
            self.last_action = list(target)
            self.seq = (self.seq+1)&0xffffffff
            if command_mode == ARM:
                self._arm_ack_seq = self.seq
                self._arm_ack_deadline = time.monotonic() + .15
            payload = encode_command(self.seq, fb.sample_us, command_mode, target)
            self.bridge.notify('duck_command', payload)
            self.sent += 1

    def _keep_warm_idle(self):
        """Run one dummy pass on an idle session so walk/getup/pick/roulade stay hot."""
        with self.lock:
            phase = self.recovery.phase
            rolling = self._roulade_on(time.monotonic()) and phase == 'walk'
            picking = self.pick_active and phase == 'walk' and not rolling
            if rolling:
                active = self.roulade_policy
            elif picking:
                active = self.pick_policy
            elif phase == 'walk':
                active = self.policy
            else:
                active = self.getup_policy
            candidates = [p for p in (self.policy, self.getup_policy, self.pick_policy, self.roulade_policy) if p is not active]
        if not candidates:
            return
        self._warm_index = (self._warm_index + 1) % len(candidates)
        idle = candidates[self._warm_index]
        if self.policy_lock.acquire(blocking=False):
            try:
                idle.warm(1)
            finally:
                self.policy_lock.release()

    def run(self):
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            try:
                self.step()
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc)
                    self._pause_motion_locked('Control loop error: ' + str(exc))
            deadline += CONTROL_PERIOD
            now = time.monotonic()
            if deadline <= now:
                missed = int((now-deadline)/CONTROL_PERIOD)+1
                self.loop_missed += missed; deadline += missed*CONTROL_PERIOD
            else:
                # Use spare budget to keep inactive ONNX sessions warm.
                if deadline - now > 0.008:
                    try:
                        self._keep_warm_idle()
                    except Exception:
                        pass
            self.stop_event.wait(max(0.0,deadline-time.monotonic()))

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self._pause_motion_locked('App stopped')
            if self.fault_hold:
                self._send_pause_locked()

    def status(self):
        now = time.monotonic()
        def hz(times):
            recent = [t for t in times if now-t < 5]
            if len(recent) < 2:
                return 0.0
            span = recent[-1] - recent[0]
            return (len(recent)-1)/span if span > 1e-9 else 0.0
        def summary(samples):
            vals=sorted(samples)
            return {'mean':sum(vals)/len(vals), 'p99':vals[min(len(vals)-1,int(len(vals)*.99))], 'max':vals[-1]} if vals else {}
        with self.lock:
            fb = self.feedback
            result = dict(mode=self.mode, model=self.model_message, bridge_baud=2000000,
                feedback_hz=hz(self.rx_times), inference_hz=hz(self.infer_times),
                received=self.received, inferred=self.inferred, sent=self.sent,
                bad_frames=self.bad_frames, sequence_gaps=self.gaps, loop_missed=self.loop_missed,
                inference_ms=summary(self.infer_ms), receive_interval_ms=summary(self.rx_intervals),
                feedback_age_ms=(now-self.rx_time)*1000 if fb else None,
                uptime_s=now-self.started, last_error=self.last_error, command=self.twist, input_source=self.input_source,
                fault_hold=self.fault_hold, pause_events=list(self.pause_events),
                timeout_checks_disabled=self._timeouts_disabled(),
                timeout_warning=getattr(self, 'timeout_warning', ''),
                timeout_events=list(getattr(self, 'timeout_events', [])),
                arming=self.arm_pending or getattr(self, '_arm_ack_seq', None) is not None,
                diagnostic_paused=self.diagnostic_paused,
                fault_hold_supported=bool(fb and fb.flags & FAULT_HOLD_CAPABLE),
                head=self.head, mouth=self.mouth, policy_targets=self.last_action, motors=MOTOR_IDS, boot_pd=self.boot_pd,
                full_robot_ready=bool(fb and fb.imu_ok and fb.mask==ALL_MASK and now-self.rx_time<.15),
                raw_pos=list(self.raw_pos), zero_pos=list(self.zero_pos),
                factory_zero_pos=list(FACTORY_ZERO), cal_done=list(self.cal_done),
                cal_targets=list(self.cal_targets), raw_mask=self.raw_mask)
            result['control_ready']=bool(fb and (self._timeouts_disabled() or
                (fb.imu_ok and now-self.rx_time<.15)) and self.mode not in EXCLUSIVE_MODES)
            result['servo_scan_version'] = 1
            result['deployment'] = dict(reference='rl/raspi_deploy', control_hz=50,
                home_deg=HOME_DEG.tolist(), default_deg=DEFAULT_DEG,
                action_scale=self.policy.action_scale, action_alpha=self.policy.action_alpha,
                action_alpha_head=self.policy.action_alpha_head,
                gyro_alpha=self.filter.gyro_alpha, velocity_alpha=self.filter.vel_alpha,
                gravity_tau_s=GRAVITY_TAU, imu_bias_deg=list(IMU_BIAS_DEG),
                filter_clock='host_receive', snapshot='latest')
            result['recovery'] = self.recovery.status()
            result['recovery']['active'] = self.mode in ('shadow','policy')
            result['pick'] = dict(active=self.pick_active, phi=self.pick_phi,
                                 period_s=PICK_PERIOD_S, mouth_close_phi=PICK_MOUTH_CLOSE_PHI,
                                 model=self.pick_policy.file)
            remain = max(0.0, self.roulade_until - now)
            result['roulade'] = dict(active=remain > 0, remaining_s=remain,
                                    seconds=ROULADE_S, model=self.roulade_policy.file)
            if self.mode in ('shadow','policy'):
                if remain > 0 and self.recovery.phase == 'walk':
                    result['active_model'] = self.roulade_policy.file
                elif self.pick_active and self.recovery.phase == 'walk':
                    result['active_model'] = self.pick_policy.file
                elif self.recovery.phase == 'walk':
                    result['active_model'] = self.policy.file
                else:
                    result['active_model'] = self.getup_policy.file
            else:
                result['active_model'] = None
            if fb:
                result.update(servo_ids=[i for j,i in enumerate(MOTOR_IDS) if fb.mask & (1<<j)],
                    imu_ok=fb.imu_ok, enabled=fb.enabled, mcu_watchdog=bool(fb.flags&4),
                    mcu_calibrating=fb.calibrating, mcu_servo_debug=fb.servo_debugging,
                    mcu_seq=fb.seq, mcu_period_us=fb.period_us, mcu_work_us=fb.work_us,
                    mcu_overruns=fb.overruns, mcu_report_drops=fb.report_drops,
                    mcu_invalid_commands=fb.invalid_commands, mcu_command_seq=fb.command_seq,
                    mcu_command_age_us=fb.command_age_us, angles=fb.angles,
                    velocities=fb.velocities, targets=fb.targets, acc=fb.acc, gyro=fb.gyro,
                    gravity=self.filtered['grav'] if self.filtered else None,
                    missing_joint_ids=[i for j,i in enumerate(MOTOR_IDS) if not fb.mask&(1<<j)])
            return result
