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
CONTROL_PERIOD = 0.02

class Controller:
    def __init__(self, bridge):
        self.bridge = bridge
        self.lock = threading.RLock()
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
        self.recovery = Recovery()
        self.policy_needs_reset = True
        self.pick_active = False
        self.pick_phi = 0.0
        self._warm_index = 0
        self.filter = SignalFilter()
        self.feedback = self.filtered = None
        self.rx_time = 0.0
        self.mode = 'shadow'
        self.generation = 0
        self.twist, self.head, self.mouth = [0.0]*3, [0.0]*4, 0.0
        self.web_time = 0.0
        self.arm_pending = False
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
            if old:
                step = elapsed_us(fb.seq, old.seq)
                if step == 0: return
                if step > 0x80000000:
                    self.mode = 'shadow'; self.generation += 1
                    self.arm_pending = False; self.filter.reset()
                    self.recovery.reset(); self.policy_needs_reset = True
                    self._stop_pick_locked()
                    self._zeros_pushed = False
                else:
                    self.gaps += step-1
                self.rx_intervals.append((now-self.rx_time)*1000)
            self.filtered = self.filter.update(fb.acc, fb.gyro, fb.angles, fb.velocities,
                timestamp=now, imu_ok=fb.imu_ok)
            self.feedback, self.rx_time = fb, now
            self.received += 1; self.rx_times.append(now)

    def _require_feedback(self, mask):
        fb = self.feedback
        if not fb or time.monotonic()-self.rx_time > .15:
            raise ValueError('MCU feedback is stale')
        if not fb.imu_ok or (fb.mask & mask) != mask:
            raise ValueError('required servo feedback / IMU missing')
        return fb

    def _disarm_now(self):
        self.arm_pending = False
        self.seq = (self.seq + 1) & 0xffffffff
        sample = self.feedback.sample_us if self.feedback else 0
        self.bridge.notify('duck_command', encode_command(self.seq, sample, DISARM, DEFAULT_DEG))

    def set_mode(self, mode):
        if mode not in ('shadow', 'off', 'hold', 'policy', 'calibrate', 'servo_debug'):
            raise ValueError('unknown mode')
        with self.lock:
            if mode == self.mode:
                self.web_time = time.monotonic()
                return {'mode': self.mode}
            if mode in ('hold', 'policy') and self.mode in EXCLUSIVE_MODES:
                raise ValueError('exit calibrate/servo_debug first')
            if mode in EXCLUSIVE_MODES and self.mode in ('hold', 'policy'):
                raise ValueError('disarm before calibrate/servo_debug')
            fb = None
            if mode in ('hold', 'policy'):
                fb = self._require_feedback(0)
            previous = self.mode
            self.generation += 1; self.mode = mode
            self.recovery.reset(); self.policy_needs_reset = True
            self._stop_pick_locked()
            self.last_error = ''
            self.web_time = time.monotonic()
            self.twist, self.head, self.mouth = [0.0]*3, [0.0]*4, 0.0
            self.arm_pending = fb is not None and not fb.enabled
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
        with self.policy_lock:
            self.policy.reset_action_filter()
            self.getup_policy.reset_action_filter()
            self.pick_policy.reset_action_filter()
        return {'mode': mode}

    def set_action_alphas(self, **values):
        with self.policy_lock:
            leg, head = self.policy.set_action_alphas(**values)
            self.getup_policy.set_action_alphas(leg=leg, head=head)
            self.pick_policy.set_action_alphas(leg=leg, head=head)
            return leg, head

    def _stop_pick_locked(self):
        self.pick_active = False
        self.pick_phi = 0.0

    def start_pick(self):
        with self.lock:
            if self.mode not in ('shadow', 'policy'):
                raise ValueError('pick requires shadow/policy mode')
            if self.recovery.phase != 'walk':
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

    def command(self, twist=None, head=None, mouth=None):
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
        target_id = int(target_id)
        with self.lock:
            self._require_servo_debug()
            if action == 'unlock':
                op, payload = SERVO_UNLOCK, encode_servo(SERVO_UNLOCK, target_id)
            elif action == 'set_id':
                new_id = int(new_id)
                if not 1 <= new_id <= 253:
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
            else:
                raise ValueError('unknown servo action')
            self.servo_reply_event.clear()
            self.bridge.notify('duck_servo', payload)
        return self._wait_servo_reply(op)

    def _wait_servo_reply(self, op, timeout=0.5):
        if not self.servo_reply_event.wait(timeout):
            raise ValueError('servo reply timeout')
        with self.lock:
            reply = self.servo_reply
        if not reply or reply['op'] != op:
            raise ValueError('unexpected servo reply')
        if not reply['ok']:
            raise ValueError('servo operation failed')
        return reply

    def step(self):
        with self.lock:
            fb, filt, generation, mode = self.feedback, self.filtered, self.generation, self.mode
            age = time.monotonic()-self.rx_time
            if mode in ('hold','policy'):
                lost = None
                if not fb or age>.15 or not fb.imu_ok:
                    lost = 'sensor feedback lost; disarmed'
                elif time.monotonic()-self.web_time > 1.0:
                    lost = 'web heartbeat lost; disarmed'
                if lost:
                    self.mode = mode = 'shadow'; self.generation += 1
                    self.arm_pending = False; self.last_error = lost
                    generation = self.generation
                    self.recovery.reset(); self.policy_needs_reset = True
                    self._stop_pick_locked()
            elif mode in EXCLUSIVE_MODES:
                if time.monotonic()-self.web_time > 2.0:
                    previous = mode
                    self.mode = mode = 'shadow'; self.generation += 1
                    self.last_error = 'calibrate/servo_debug heartbeat lost; exited'
                    generation = self.generation
                    if previous == 'calibrate':
                        self.cal_done = [False]*15
                        self.bridge.notify('duck_cal', encode_cal(CAL_EXIT))
                    else:
                        self.bridge.notify('duck_servo', encode_servo(SERVO_EXIT, 10))
                    self._disarm_now()
            twist, head, mouth = list(self.twist), list(self.head), self.mouth
            valid = bool(fb and age < .15 and fb.imu_ok and filt
                         and filt['imu_fusion_ready'] and filt['imu_fusion_updated'])
            if mode in ('shadow', 'policy'):
                changed = self.recovery.update(filt['grav'] if filt else [0,0,0],
                                               time.monotonic(), valid)
                self.policy_needs_reset |= changed
                if changed and self.recovery.phase != 'walk':
                    self._stop_pick_locked()
            phase = self.recovery.phase
            picking = self.pick_active and phase == 'walk'
            if picking:
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
        target = list(DEFAULT_DEG)
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
            payload = encode_command(self.seq, fb.sample_us, command_mode, target)
            self.bridge.notify('duck_command', payload)
            self.sent += 1

    def _keep_warm_idle(self):
        """Run one dummy pass on an idle session so walk/getup/pick stay hot."""
        with self.lock:
            phase = self.recovery.phase
            picking = self.pick_active and phase == 'walk'
            if picking:
                active = self.pick_policy
            elif phase == 'walk':
                active = self.policy
            else:
                active = self.getup_policy
            candidates = [p for p in (self.policy, self.getup_policy, self.pick_policy) if p is not active]
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
                    self.last_error = str(exc); self.mode = 'off'; self.generation += 1; self.arm_pending = False
                    self._stop_pick_locked()
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
            previous = self.mode
            self.mode = 'off'; self.generation += 1; self.arm_pending = False
            if previous == 'calibrate':
                self.cal_done = [False]*15
                self.bridge.notify('duck_cal', encode_cal(CAL_EXIT))
            elif previous == 'servo_debug':
                self.bridge.notify('duck_servo', encode_servo(SERVO_EXIT, 10))
            self.seq += 1
            self.bridge.notify('duck_command', encode_command(self.seq, 0, DISARM, DEFAULT_DEG))

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
                uptime_s=now-self.started, last_error=self.last_error, command=self.twist,
                head=self.head, mouth=self.mouth, policy_targets=self.last_action, motors=MOTOR_IDS, boot_pd=self.boot_pd,
                full_robot_ready=bool(fb and fb.imu_ok and fb.mask==ALL_MASK and now-self.rx_time<.15),
                raw_pos=list(self.raw_pos), zero_pos=list(self.zero_pos),
                factory_zero_pos=list(FACTORY_ZERO), cal_done=list(self.cal_done),
                cal_targets=list(self.cal_targets), raw_mask=self.raw_mask)
            result['control_ready']=bool(fb and fb.imu_ok and now-self.rx_time<.15 and self.mode not in EXCLUSIVE_MODES)
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
            if self.mode in ('shadow','policy'):
                if self.pick_active and self.recovery.phase == 'walk':
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
