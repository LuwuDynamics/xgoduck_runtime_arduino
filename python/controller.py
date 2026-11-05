"""100Hz receive/filter callback and independent 50Hz ONNX loop."""
import collections
import math
import threading
import time
from statistics import median
from policy import Policy
from recovery import Recovery
from proc import SignalFilter
from standing_imu import (CALIBRATION_PATH, SAMPLE_SECONDS, ImuCorrection,
                          check_standing, estimate as estimate_imu, load as load_imu,
                          save as save_imu, disable as disable_imu)
from rl_core import (MOTOR_IDS, POLICY_IDS, IMU_BIAS_DEG, MOUTH_INDEX, MOUTH_MAX_DEG,
                     ROLLER_LEG_DEFAULT_DEG)
from wire import *

EXCLUSIVE_MODES = ('calibrate', 'servo_debug')
PICK_PERIOD_S = 4.0
PICK_MOUTH_CLOSE_PHI = 0.4
ROULADE_S = 1.7
ROULADE_ALPHA = 0.05
ROULADE_START_TIMEOUT_S = 0.5
SITSTAND_STAND_S = 1.0
KICK_S = 0.4
CONTROL_PERIOD = 1.0 / 50.0
ACTION_PREPARE_S = 0.2
ACTION_PREPARE_TIMEOUT_S = 0.5
ROLLER_YAW_LIMIT = 0.45  # rad/s, symmetric in Roller mode.
COMMAND_ACC_X_BIAS_MS2 = 1.5  # Temporary IMU trim magnitude; set to 0 to disable.
VOLTAGE_TIMEOUT_S = 3.0

class Controller:
    def __init__(self, bridge, *, imu_calibration_path=None):
        self.bridge = bridge
        self.lock = threading.RLock()
        self.servo_op_lock = threading.Lock()
        self.cal_op_lock = threading.Lock()
        self.policy_lock = threading.Lock()
        self.policy = Policy()
        ok, message = self.policy.load()
        if not ok:
            raise RuntimeError(message)
        self.model_message = message
        self.walk_policy = self.policy
        self.roller_policy = Policy()
        ok, message = self.roller_policy.load('xgoduck_roller.onnx', use_model_home=True)
        if not ok:
            raise RuntimeError(message)
        # One home is shared by default pose, relative joint observations and
        # action offsets. Only leg values override the ONNX metadata.
        for index, motor_id in enumerate(POLICY_IDS):
            if motor_id in ROLLER_LEG_DEFAULT_DEG:
                self.roller_policy.home_deg[index] = ROLLER_LEG_DEFAULT_DEG[motor_id]
        self.roller_policy.action_scale = 0.7
        self.roller_policy.set_action_alphas(leg=0.4, head=0.5)
        self.model_message += '; ' + message
        self.roller_mode = False
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
        self.sitstand_policy = Policy()
        ok, message = self.sitstand_policy.load('xgoduck_sitstand.onnx')
        if not ok:
            raise RuntimeError(message)
        self.model_message += '; ' + message
        self.right_kick_policy = Policy()
        ok, message = self.right_kick_policy.load('xgoduck_ballkick_right.onnx')
        if not ok:
            raise RuntimeError(message)
        self.model_message += '; ' + message
        self.left_kick_policy = Policy()
        ok, message = self.left_kick_policy.load('xgoduck_ballkick_left.onnx')
        if not ok:
            raise RuntimeError(message)
        self.model_message += '; ' + message
        self.kick_policies = {'left': self.left_kick_policy, 'right': self.right_kick_policy}
        for model in self.kick_policies.values():
            model.action_scale = 0.8
            model.set_action_alphas(leg=0.0, head=0.0)
        self.recovery = Recovery(period_s=CONTROL_PERIOD)
        self.policy_needs_reset = True
        self.pick_active = False
        self.pick_phi = 0.0
        self.roulade_until = 0.0
        self.roulade_pending = False
        self._roulade_requested_at = 0.0
        self._roulade_started_at = None
        self._roulade_frames = collections.deque(maxlen=128)
        self._roulade_timing = dict(state='idle', startup_ms=None, elapsed_s=0.0)
        self.sitstand_phase = 'idle'
        self.sitstand_until = 0.0
        self.kick_until = 0.0
        self.kick_side = None
        self._roulade_alpha = None
        self._warm_index = 0
        self.action_preparation = None
        self._action_first_target = None
        self._imu_path = CALIBRATION_PATH if imu_calibration_path is None else imu_calibration_path
        self._imu_record = None
        self._imu_job_token = 0
        self._imu_job = dict(state='idle', active=False, progress=0., samples=0,
                             message='Not calibrated. Hold the board upright and still.')
        try:
            self._imu_record = load_imu(self._imu_path)
            if self._imu_record is not None:
                self._imu_job.update(state='saved', message='Saved IMU calibration loaded.')
        except (OSError, ValueError, TypeError, KeyError):
            self._imu_job.update(state='error', message='Calibration could not be loaded. Calibrate again or reset it.')
        self.filter = SignalFilter(ImuCorrection(self._imu_record) if self._imu_record else None)
        self.feedback = self.filtered = None
        self.voltage_packet = None
        self.voltage_rx_time = 0.0
        self.rx_time = 0.0
        self._imu_source_us = None
        self._imu_source_time = 0.0
        self.diagnostics = {}
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
        self.raw_pos = [SERVO_CENTER_POS]*15
        self.cal_done = [False]*15
        self.cal_reply = None
        self.cal_seq = 0
        self.cal_reply_event = threading.Event()
        self.cal_expected_index = None
        self.raw_mask = 0
        self.servo_reply = None
        self.servo_reply_event = threading.Event()
        self.servo_expected_reply = None

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
            self.raw_pos = pkt['pos']
            self.raw_mask = pkt['mask']
            self.cal_done = [self.mode == 'calibrate' and bool(pkt['held_mask'] & (1 << i))
                             for i in range(15)]

    def receive_cal_reply(self, raw):
        try:
            reply = decode_cal_reply(raw)
        except ValueError:
            return
        with self.lock:
            if self.cal_expected_index != (reply['seq'], reply['index']):
                return
            self.cal_reply = reply
            self.cal_reply_event.set()

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

    def receive_diagnostics(self, raw):
        try:
            values = decode_diagnostics(raw)
        except ValueError:
            return
        with self.lock:
            previous = self.diagnostics.get('bridge_ready_us')
            if previous is not None and previous != values['bridge_ready_us']:
                self.voltage_packet = None; self.voltage_rx_time = 0.0
                self.mode = 'shadow'; self.generation += 1; self.arm_pending = False
                self.filter.reset(); self.recovery.reset(); self.policy_needs_reset = True
                self._imu_source_us = None
                self._cancel_actions_locked()
            self.diagnostics = values

    def receive_voltage(self, raw):
        try:
            packet = decode_voltage(raw)
        except ValueError:
            return
        with self.lock:
            if self.voltage_packet and packet['seq'] == self.voltage_packet['seq']:
                return
            if self.feedback:
                age = elapsed_us(self.feedback.sample_us, packet['sample_us'])
                if age < 0x80000000 and age > VOLTAGE_TIMEOUT_S*1e6:
                    return
                if age >= 0x80000000 and elapsed_us(packet['sample_us'], self.feedback.sample_us) > 100000:
                    return
            self.voltage_packet = packet
            self.voltage_rx_time = time.monotonic()

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
                    self.voltage_packet = None; self.voltage_rx_time = 0.0
                    self.mode = 'shadow'; self.generation += 1
                    self.arm_pending = False; self.filter.reset()
                    self._imu_source_us = None
                    self.recovery.reset(); self.policy_needs_reset = True
                    self._cancel_actions_locked()
                    self.cal_done = [False]*15
                    self.cal_expected_index = None
                    self.cal_reply_event.set()
                else:
                    self.gaps += step-1
                self.rx_intervals.append((now-self.rx_time)*1000)
            if self._imu_source_us is None:
                self._imu_source_time = 0.0
            else:
                imu_step = elapsed_us(fb.imu_us, self._imu_source_us)
                if imu_step > 0x80000000:
                    self.filter.reset(); self._imu_source_time = 0.0
                else:
                    self._imu_source_time += imu_step / 1e6
            self._imu_source_us = fb.imu_us
            command_acc_x_bias = (COMMAND_ACC_X_BIAS_MS2 if self.twist[0] > 0.0 else
                                  -COMMAND_ACC_X_BIAS_MS2 if self.twist[0] < 0.0 or self.twist[2] != 0.0 else 0.)
            self.filter.command_acc_x_bias_ms2 = command_acc_x_bias if (
                self._imu_record is not None and self.mode in ('shadow', 'policy')
                and not self.roller_mode
                and self.recovery.phase == 'walk' and not self.pick_active
                and not (self._roulade_on(now) and self.action_preparation is None)
                and self.sitstand_phase == 'idle'
                and not self.kick_until and not self._imu_job['active']) else 0.
            self.filtered = self.filter.update(fb.acc, fb.gyro, fb.angles, fb.velocities,
                timestamp=now, imu_timestamp=self._imu_source_time, imu_ok=fb.imu_ok)
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
        self.bridge.notify('duck_command', encode_command(self.seq, sample, DISARM, self.policy.default_deg))

    def _check_imu_idle_locked(self):
        if self._imu_job['active']:
            raise ValueError('IMU calibration is in progress. Wait for it to finish or exit calibration.')

    def _imu_status_locked(self):
        record = self._imu_record or {}
        return dict(self._imu_job, calibrated=bool(self._imu_record), seconds=SAMPLE_SECONDS,
                    method='offset', acc_offset_ms2=record.get('acc_offset_ms2'),
                    gyro_zero_dps=record.get('gyro_zero_dps'),
                    calibrated_at=record.get('calibrated_at'), measurement=record.get('measurement'))

    def start_imu_calibration(self):
        with self.lock:
            if self._imu_job['active']:
                return self._imu_status_locked()
            if self.mode not in ('off', 'shadow', 'hold', 'calibrate'):
                raise ValueError('Stop motion and hold the board upright and still.')
            if self.mode == 'calibrate' and (any(self.cal_done) or self.cal_expected_index is not None):
                raise ValueError('Unload all joints before calibrating the IMU.')
            if (self.action_preparation or self.pick_active or self._roulade_on(time.monotonic())
                    or self.sitstand_phase != 'idle' or self.kick_until):
                raise ValueError('Wait for the current action to finish before calibrating.')
            fb = self._require_feedback(0)
            if elapsed_us(fb.sample_us, fb.imu_us)/1e6 > IMU_STALE_S:
                raise ValueError('IMU readings are stale. Check the connection and try again.')
            check_standing(fb.acc)
            self.twist, self.head, self.mouth = [0.]*3, [0.]*4, 0.
            self.generation += 1
            self._imu_job_token += 1
            token = self._imu_job_token
            self._imu_job = dict(state='sampling', active=True, progress=0., samples=0,
                                 message=f'Keep the board still. Calibrating for {SAMPLE_SECONDS:g} seconds…')
            threading.Thread(target=self._sample_imu_calibration, args=(token, self.generation),
                             name='imu-calibration', daemon=True).start()
            return self._imu_status_locked()

    def _sample_imu_calibration(self, token, generation):
        rows = []
        begin = time.monotonic()
        try:
            while not self.stop_event.wait(.005):
                with self.lock:
                    if token != self._imu_job_token or not self._imu_job['active']:
                        return
                    if generation != self.generation:
                        raise ValueError('Calibration cancelled because the control mode changed.')
                    fb = self._require_feedback(0)
                    if elapsed_us(fb.sample_us, fb.imu_us)/1e6 > IMU_STALE_S:
                        raise ValueError('IMU readings were interrupted. Try again.')
                    stamp = self._imu_source_time
                    if rows and stamp < rows[-1][0]:
                        raise ValueError('The IMU restarted. Try again.')
                    if not rows or stamp > rows[-1][0]:
                        rows.append((stamp, list(fb.acc), list(fb.gyro)))
                        span = stamp-rows[0][0]
                        self._imu_job.update(samples=len(rows), progress=min(1., span/SAMPLE_SECONDS))
                        if span >= SAMPLE_SECONDS:
                            break
                if time.monotonic()-begin > SAMPLE_SECONDS+2:
                    raise ValueError('Not enough IMU readings. Check the connection and try again.')
            else:
                raise ValueError('Calibration cancelled because the app stopped.')
            record = estimate_imu(rows)
            correction = ImuCorrection(record)
            with self.lock:
                if token != self._imu_job_token or not self._imu_job['active']:
                    return
                if generation != self.generation:
                    raise ValueError('Calibration cancelled because the control mode changed.')
                # A cancelled job must never publish a file or apply its result.
                save_imu(self._imu_path, record)
                self._imu_record = record
                self.filter.set_correction(correction)
                self.filtered = None
                self.recovery.reset()
                self.policy_needs_reset = True
                self.generation += 1
                self._imu_job.update(state='saved', active=False, progress=1.,
                                     message='IMU calibrated and saved.')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            with self.lock:
                if token == self._imu_job_token and self._imu_job['active']:
                    self._imu_job.update(state='error', active=False,
                                         message='Previous calibration kept. '+str(exc))

    def reset_imu_calibration(self):
        with self.lock:
            self._check_imu_idle_locked()
            if self.mode not in ('off', 'shadow', 'hold', 'calibrate'):
                raise ValueError('Stop motion before resetting IMU calibration.')
            try:
                disable_imu(self._imu_path)
            except OSError as exc:
                raise ValueError('Could not reset calibration. Previous calibration kept.') from exc
            self._imu_record = None
            self.filter.set_correction(None)
            self.filtered = None
            self.recovery.reset()
            self.policy_needs_reset = True
            self.generation += 1
            self._imu_job = dict(state='idle', active=False, progress=0., samples=0,
                                 message='IMU calibration reset. Previous calibration backed up.')
            return self._imu_status_locked()

    def set_mode(self, mode):
        if mode not in ('shadow', 'off', 'hold', 'policy', 'calibrate', 'servo_debug'):
            raise ValueError('unknown mode')
        with self.lock:
            if self._imu_job['active']:
                if mode == 'off' or (self.mode == 'calibrate' and mode == 'shadow'):
                    self._imu_job_token += 1
                    self._imu_job.update(state='cancelled', active=False, message='Calibration cancelled. Previous calibration kept.')
                elif mode != self.mode or mode == 'calibrate':
                    self._check_imu_idle_locked()
            if mode == 'policy' and self.sitstand_phase != 'idle':
                raise ValueError('stand up before switching to walk')
            if mode == 'policy' and self.kick_until:
                raise ValueError('wait for kick to finish before switching to walk')
            if mode == self.mode and mode != 'calibrate':
                self._cancel_preparation_locked()
                self.web_time = time.monotonic()
                return {'mode': self.mode}
            if mode in ('hold', 'policy') and self.mode in EXCLUSIVE_MODES:
                raise ValueError('exit calibrate/servo_debug first')
            fb = None
            if mode in ('hold', 'policy'):
                fb = self._require_feedback(0)
            previous = self.mode
            self.generation += 1; self.mode = mode
            self.recovery.reset(); self.policy_needs_reset = True
            self._cancel_actions_locked()
            self.last_error = ''
            self.web_time = time.monotonic()
            self.twist, self.head, self.mouth = [0.0]*3, [0.0]*4, 0.0
            self.arm_pending = fb is not None and not fb.enabled
            if previous == 'calibrate' and mode != 'calibrate':
                self.cal_done = [False]*15
                self.cal_expected_index = None
                self.cal_reply_event.set()
                self.bridge.notify('duck_cal', encode_cal(CAL_EXIT))
            if previous == 'servo_debug' and mode != 'servo_debug':
                self.bridge.notify('duck_servo', encode_servo(SERVO_EXIT, 10))
            if mode == 'calibrate':
                self._disarm_now()
                self.cal_done = [False]*15
                self.cal_expected_index = None
                self.cal_reply_event.set()
                self.bridge.notify('duck_cal', encode_cal(CAL_ENTER))
            elif mode == 'servo_debug':
                # Opening setup stops motion before the MCU takes the servo bus.
                self._disarm_now()
                self.bridge.notify('duck_servo', encode_servo(SERVO_ENTER, 10))
            elif previous in EXCLUSIVE_MODES:
                self._disarm_now()
        with self.policy_lock:
            for model in self._models():
                model.reset_action_filter()
        return {'mode': mode}

    def set_roller_mode(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('roller enabled must be true or false')
        with self.lock:
            if enabled != self.roller_mode:
                self._check_imu_idle_locked()
                if self.action_preparation:
                    raise ValueError('wait for the current action to finish preparing')
            if enabled != self.roller_mode and self.kick_until:
                raise ValueError('wait for kick to finish before switching roller mode')
            if enabled != self.roller_mode and self.sitstand_phase != 'idle':
                raise ValueError('stand up before switching roller mode')
            if enabled != self.roller_mode and self._roulade_on(time.monotonic()):
                raise ValueError('wait for roulade to finish before switching roller mode')
            if enabled != self.roller_mode:
                with self.policy_lock:
                    self.policy = self.roller_policy if enabled else self.walk_policy
                    self.policy.reset_action_filter()
                    self.policy.warm(1)
                self.roller_mode = enabled
                # Drop any action computed by the previously selected walk model.
                self.generation += 1
                if (self.recovery.phase == 'walk' and not self.pick_active
                        and not self._roulade_on(time.monotonic())):
                    self.policy_needs_reset = True
                self.twist = [0.0]*3
                self.web_time = time.monotonic()
            return dict(enabled=self.roller_mode, model=self.policy.file,
                        action_scale=self.policy.action_scale, action_alpha=self.policy.action_alpha,
                        action_alpha_head=self.policy.action_alpha_head)

    def _models(self):
        return (*self._standard_models(), self.roller_policy, *self.kick_policies.values())

    def _standard_models(self):
        return (self.walk_policy, self.getup_policy, self.pick_policy, self.roulade_policy,
                self.sitstand_policy)

    def _apply_alphas_locked(self, leg, head):
        for model in self._standard_models():
            model.set_action_alphas(leg=leg, head=head)

    def set_action_alphas(self, **values):
        with self.lock:
            with self.policy_lock:
                if self.roller_mode:
                    return self.roller_policy.set_action_alphas(**values)
                leg, head = self.walk_policy.set_action_alphas(**values)
                if self._roulade_alpha is not None:
                    self._roulade_alpha = (leg, head)
                    self._apply_alphas_locked(ROULADE_ALPHA, ROULADE_ALPHA)
                else:
                    self._apply_alphas_locked(leg, head)
                return leg, head

    def set_imu_filters(self, **values):
        with self.lock:
            return self.filter.set_imu_filters(**values)

    def set_action_scale(self, scale):
        scale = float(scale)
        if not math.isfinite(scale) or not 0 <= scale <= 2:
            raise ValueError('action scale must be within [0,2]')
        with self.lock:
            with self.policy_lock:
                models = (self.roller_policy,) if self.roller_mode else self._standard_models()
                for model in models:
                    model.action_scale = scale
        return {'scale': scale}

    def _stop_pick_locked(self):
        self.pick_active = False
        self.pick_phi = 0.0

    def _cancel_preparation_locked(self, reason='cancelled'):
        pending = self.action_preparation
        self.action_preparation = None
        if pending and pending['name'] == 'roulade':
            self._stop_roulade_locked(reason)

    def _cancel_actions_locked(self):
        self._cancel_preparation_locked()
        self._action_first_target = None
        self._stop_pick_locked()
        self._stop_roulade_locked()
        self._stop_sitstand_locked()
        self._stop_kick_locked()

    def _queue_action_locked(self, name, policy, now):
        if self.action_preparation:
            raise ValueError('wait for the current action to finish preparing')
        self.action_preparation = dict(name=name, policy=policy, requested_at=now,
                                       ready_at=now+ACTION_PREPARE_S, warm_passes=0)
        self.web_time = now
        self.last_error = ''

    def _advance_preparation_locked(self, now, valid):
        pending = self.action_preparation
        if not pending:
            return
        if now-pending['requested_at'] >= ACTION_PREPARE_TIMEOUT_S:
            self._cancel_preparation_locked('start_timeout')
            self.last_error = 'Action preparation timed out; action cancelled.'
            return
        if not valid or now < pending['ready_at'] or not pending['warm_passes']:
            return
        name = pending['name']
        self.action_preparation = None
        self.generation += 1
        self.policy_needs_reset = name != 'stand'
        if name == 'pick':
            self.pick_active = True
            self.pick_phi = 0.0
            self.mouth = MOUTH_MAX_DEG
        elif name == 'roulade':
            self._roulade_alpha = (self.walk_policy.action_alpha, self.walk_policy.action_alpha_head)
            with self.policy_lock:
                self._apply_alphas_locked(ROULADE_ALPHA, ROULADE_ALPHA)
            self._roulade_timing['state'] = 'pending'
        elif name in ('sit', 'stand'):
            if name == 'sit':
                self.mode = 'policy'
                self.arm_pending = not self.feedback.enabled
                self.head = [0.0]*4
            self.sitstand_phase = name
            self.sitstand_until = now+SITSTAND_STAND_S if name == 'stand' else 0.0
            self.twist = [0.0]*3
            self._action_first_target = name
        else:
            self.kick_side = name.removesuffix('_kick')
            self.kick_until = now+KICK_S
            self.twist, self.head, self.mouth = [0.0]*3, [0.0]*4, 0.0
            self._action_first_target = name

    def _stop_roulade_locked(self, reason='cancelled'):
        if self.roulade_pending or self.roulade_until:
            elapsed = (time.monotonic()-self._roulade_started_at
                       if self._roulade_started_at is not None else 0.0)
            self._roulade_timing.update(state=reason, elapsed_s=elapsed)
        self.roulade_pending = False
        self._roulade_started_at = None
        self.roulade_until = 0.0
        saved = self._roulade_alpha
        self._roulade_alpha = None
        if saved is not None:
            with self.policy_lock:
                self._apply_alphas_locked(*saved)

    def _roulade_on(self, now):
        return self.roulade_pending or now < self.roulade_until

    def _expire_roulade_locked(self, now):
        if self.action_preparation and self.action_preparation['name'] == 'roulade':
            return False
        timeout = self.roulade_pending and now-self._roulade_requested_at >= ROULADE_START_TIMEOUT_S
        if not timeout and not (self.roulade_until and now >= self.roulade_until):
            return False
        self._stop_roulade_locked('start_timeout' if timeout else 'completed')
        if timeout:
            self.last_error = 'Roulade start timed out; action cancelled.'
        self.recovery.fall_elapsed = 0.0
        self.policy_needs_reset = True
        return True

    def _record_roulade_frame_locked(self, submitted, inference_ms, wait_ms, step_ms):
        # Start the action clock only after submitting its first actual target.
        if self.roulade_pending:
            self.roulade_pending = False
            self._roulade_started_at = submitted
            self.roulade_until = submitted + ROULADE_S
            self._roulade_timing.update(state='running',
                startup_ms=(submitted-self._roulade_requested_at)*1000)
        t_ms = (submitted-self._roulade_started_at)*1000
        interval = t_ms-self._roulade_frames[-1]['t_ms'] if self._roulade_frames else None
        self._roulade_frames.append(dict(t_ms=t_ms, inference_ms=inference_ms,
            policy_wait_ms=wait_ms, step_ms=step_ms, interval_ms=interval,
            missed_periods=max(0, round(interval/(CONTROL_PERIOD*1000))-1)
                if interval is not None else 0))

    def _roulade_timing_locked(self, now, include_frames=False):
        frames = list(self._roulade_frames)
        result = dict(self._roulade_timing)
        if self._roulade_started_at is not None:
            result['elapsed_s'] = now-self._roulade_started_at
        result.update(frames=len(frames), target_hz=1.0/CONTROL_PERIOD,
            actual_hz=(len(frames)-1)*1000/frames[-1]['t_ms']
                if len(frames)>1 and frames[-1]['t_ms']>0 else None,
            missed_periods=sum(f['missed_periods'] for f in frames))
        for name in ('inference_ms', 'policy_wait_ms', 'step_ms', 'interval_ms'):
            values = [f[name] for f in frames if f[name] is not None]
            result[name] = dict(mean=sum(values)/len(values), max=max(values)) if values else {}
        if include_frames:
            result['samples'] = [dict(f) for f in frames]
        return result

    def roulade_timing(self):
        with self.lock:
            return self._roulade_timing_locked(time.monotonic(), include_frames=True)

    def _stop_sitstand_locked(self):
        if self._action_first_target in ('sit', 'stand'):
            self._action_first_target = None
        if self.sitstand_phase != 'idle':
            self.twist = [0.0]*3
            self.policy_needs_reset = True
        self.sitstand_phase = 'idle'
        self.sitstand_until = 0.0

    def _sitstand_status_locked(self, now):
        return dict(phase=self.sitstand_phase, active=self.sitstand_phase != 'idle',
                    remaining_s=max(0.0, self.sitstand_until-now) if self.sitstand_phase == 'stand' else 0.0,
                    stand_seconds=SITSTAND_STAND_S, model=self.sitstand_policy.file,
                    action_scale=self.sitstand_policy.action_scale)

    def _stop_kick_locked(self):
        if self._action_first_target in ('left_kick', 'right_kick'):
            self._action_first_target = None
        if self.kick_until:
            self.twist, self.head, self.mouth = [0.0]*3, [0.0]*4, 0.0
            self.policy_needs_reset = True
        self.kick_until = 0.0
        self.kick_side = None

    def _kick_status_locked(self, now, side):
        remain = max(0.0, self.kick_until-now) if self.kick_side == side else 0.0
        model = self.kick_policies[side]
        return dict(active=remain > 0, side=side, remaining_s=remain, seconds=KICK_S,
                    model=model.file, action_scale=model.action_scale,
                    action_alpha=model.action_alpha, action_alpha_head=model.action_alpha_head)

    def start_kick(self, side):
        if side not in self.kick_policies:
            raise ValueError('kick side must be left or right')
        with self.lock:
            self._check_imu_idle_locked()
            now = time.monotonic()
            if self.mode not in ('shadow', 'policy'):
                raise ValueError('kick requires shadow/policy mode')
            fb = self._require_feedback(0)
            if now-self.rx_time + elapsed_us(fb.sample_us, fb.imu_us)/1e6 > IMU_STALE_S:
                raise ValueError('IMU feedback is stale')
            if (self.recovery.phase != 'walk' or self.pick_active
                    or self._roulade_on(now) or self.sitstand_phase != 'idle'
                    or self.kick_until):
                raise ValueError('kick only while walking upright with no other action')
            self._queue_action_locked(side+'_kick', self.kick_policies[side], now)
            return dict(self._kick_status_locked(now, side), preparing=True)

    def set_sitstand(self, action):
        if action not in ('sit', 'stand'):
            raise ValueError('sitstand action must be sit or stand')
        with self.lock:
            self._check_imu_idle_locked()
            now = time.monotonic()
            if self.kick_until:
                raise ValueError('wait for kick to finish before sit/stand')
            if self.mode in EXCLUSIVE_MODES:
                raise ValueError('exit calibrate/servo_debug before sit/stand')
            fb = self._require_feedback(0)
            if now-self.rx_time + elapsed_us(fb.sample_us, fb.imu_us)/1e6 > IMU_STALE_S:
                raise ValueError('IMU feedback is stale')
            if self.recovery.phase != 'walk':
                raise ValueError('wait for get-up recovery before sit/stand')
            if action == 'sit':
                if self.sitstand_phase != 'idle':
                    raise ValueError('sit/stand is already active')
                if self.pick_active or self._roulade_on(now):
                    raise ValueError('wait for pick/roulade to finish before sitting')
            else:
                if self.sitstand_phase != 'sit':
                    raise ValueError('stand is only available after sit')
            self._queue_action_locked(action, self.sitstand_policy, now)
            return dict(self._sitstand_status_locked(now), preparing=True)

    def start_pick(self):
        with self.lock:
            self._check_imu_idle_locked()
            if self.kick_until:
                raise ValueError('wait for kick to finish before pick')
            if self.sitstand_phase != 'idle':
                raise ValueError('stand up before starting pick')
            if self.mode not in ('shadow', 'policy'):
                raise ValueError('pick requires shadow/policy mode')
            if self.recovery.phase != 'walk' or self.pick_active or self._roulade_on(time.monotonic()):
                raise ValueError('pick only while walking upright')
            self._require_feedback(0)
            self._queue_action_locked('pick', self.pick_policy, time.monotonic())
        return {'ok': True, 'preparing': True, 'phi': 0.0, 'model': self.pick_policy.file}

    def start_roulade(self):
        with self.lock:
            self._check_imu_idle_locked()
            now = time.monotonic()
            if self.kick_until:
                raise ValueError('wait for kick to finish before roulade')
            if self.sitstand_phase != 'idle':
                raise ValueError('stand up before starting roulade')
            if self.mode not in ('shadow', 'policy'):
                raise ValueError('roulade requires shadow/policy mode')
            if self.recovery.phase != 'walk' or self.pick_active:
                raise ValueError('roulade only while walking')
            if self._roulade_on(now):
                raise ValueError('wait for roulade to finish')
            self._require_feedback(0)
            if not self.filtered or not self.filtered['imu_fusion_ready']:
                raise ValueError('wait for IMU feedback before roulade')
            self._queue_action_locked('roulade', self.roulade_policy, now)
            self.roulade_pending = True
            self.roulade_until = 0.0
            self._roulade_requested_at = now
            self._roulade_started_at = None
            self._roulade_frames.clear()
            self._roulade_timing = dict(state='preparing', startup_ms=None, elapsed_s=0.0)
        return {'ok': True, 'preparing': True, 'model': self.roulade_policy.file, 'seconds': ROULADE_S}

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
            if self._imu_job['active']:
                self.web_time = time.monotonic()
                return {'ok': True, 'ignored': 'imu_calibration'}
            if self.mode in EXCLUSIVE_MODES:
                raise ValueError('motion commands disabled in calibrate/servo_debug')
            if t is not None and not self.roller_mode and not -.2 <= t[0] <= .2:
                raise ValueError('walking vx must be between -0.2 and 0.2')
            if t is not None and self.roller_mode and abs(t[2]) > ROLLER_YAW_LIMIT:
                raise ValueError(f'roller wz must be between {-ROLLER_YAW_LIMIT} and {ROLLER_YAW_LIMIT}')
            if t is not None and not self.pick_active and self.sitstand_phase == 'idle' and not self.kick_until: self.twist = t
            if h is not None and not self.pick_active and not self.kick_until: self.head = h
            if m is not None and not self.pick_active and not self.kick_until: self.mouth = m
            self.web_time = time.monotonic()
        return {'ok': True}

    def cal_heartbeat(self):
        with self.lock:
            if self.mode != 'calibrate':
                raise ValueError('not in calibrate mode')
            self.web_time = time.monotonic()
        return {'ok': True}

    def cal_center(self, index):
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 15:
            raise ValueError('joint index out of range')
        with self.cal_op_lock:
            with self.lock:
                self._check_imu_idle_locked()
                if self.mode != 'calibrate':
                    raise ValueError('not in calibrate mode')
                if not self.feedback or time.monotonic()-self.rx_time > .15:
                    raise ValueError('MCU feedback is stale')
                generation = self.generation
                self.cal_seq = (self.cal_seq + 1) & 0xffffffff
                seq = self.cal_seq
                self.web_time = time.monotonic()
                self.cal_reply = None
                self.cal_reply_event.clear()
                self.cal_expected_index = (seq, index)
            try:
                self.bridge.notify('duck_cal', encode_cal(CAL_CENTER_ONE, index=index, seq=seq))
                if not self.cal_reply_event.wait(.75):
                    raise ValueError('centering acknowledgement timeout')
                with self.lock:
                    if self.mode != 'calibrate' or self.generation != generation:
                        raise ValueError('calibration session changed')
                    if not self.cal_reply or not self.cal_reply['ok']:
                        raise ValueError(f'No servo replied at ID {MOTOR_IDS[index]}; centering was not started')
                    self.web_time = time.monotonic()
                return {'ok': True, 'index': index, 'id': MOTOR_IDS[index], 'hold_pos': CAL_HOLD_POS}
            finally:
                with self.lock:
                    self.cal_expected_index = None

    def _require_servo_debug(self):
        if self.mode != 'servo_debug':
            raise ValueError('not in servo_debug mode')
        self.web_time = time.monotonic()

    def servo_op(self, action, target_id=None, new_id=None, kp=5, kd=20, *, protection_enabled=True, current_a=1.0):
        with self.servo_op_lock:
            if action != 'set_id':
                return self._servo_op(action, target_id, new_id, kp, kd,
                                      protection_enabled=protection_enabled, current_a=current_a)
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
            reply = self._servo_op(action, target_id, new_id, kp, kd)
            time.sleep(0.05)
            if not self._servo_op('ping', new_id)['ok']:
                raise ValueError(f'ID write sent, but ID {new_id} did not reply. Scan again')
            if self._servo_op('ping', target_id)['ok']:
                raise ValueError('Both old and new IDs replied. Connect only one servo and scan again')
            return dict(reply, new_id=new_id)

    def _servo_op(self, action, target_id, new_id=None, kp=5, kd=20, *, protection_enabled=True, current_a=1.0):
        if action in ('set_protection', 'set_protection_current'):
            if target_id is not None and target_id != BROADCAST_ID:
                raise ValueError('protection applies to all servos; target must be broadcast')
            target_id = BROADCAST_ID
        else:
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
            elif action == 'reset_mid':
                op, payload = SERVO_RESET_MID, encode_servo(SERVO_RESET_MID, target_id)
            elif action == 'set_gains':
                kp, kd = int(kp), int(kd)
                if not 0 <= kp <= 255 or not 0 <= kd <= 255:
                    raise ValueError('kp/kd out of range')
                op = SERVO_SET_PERM_KP_KD
                payload = encode_servo(SERVO_SET_PERM_KP_KD, target_id, kp=kp, kd=kd)
            elif action == 'set_protection':
                op = SERVO_SET_PROTECTION
                payload = encode_servo_protection(protection_enabled)
            elif action == 'set_protection_current':
                op = SERVO_SET_PROTECTION_CURRENT
                payload = encode_servo_protection_current(current_a)
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
        step_started = time.monotonic()
        with self.lock:
            fb, filt, generation, mode = self.feedback, self.filtered, self.generation, self.mode
            age = time.monotonic()-self.rx_time
            if mode in ('hold','policy'):
                lost = None
                if not fb or age>.15 or not fb.imu_ok or age + elapsed_us(fb.sample_us, fb.imu_us)/1e6 > IMU_STALE_S:
                    lost = 'sensor feedback lost; disarmed'
                elif time.monotonic()-self.web_time > 1.0:
                    # Browser inactivity stops travel without releasing torque.
                    self.twist = [0.0]*3
                if lost:
                    self.mode = mode = 'shadow'; self.generation += 1
                    self.arm_pending = False; self.last_error = lost
                    generation = self.generation
                    self.recovery.reset(); self.policy_needs_reset = True
                    self._cancel_actions_locked()
            elif mode in EXCLUSIVE_MODES:
                if time.monotonic()-self.web_time > 2.0:
                    previous = mode
                    self.mode = mode = 'shadow'; self.generation += 1
                    self.last_error = 'calibrate/servo_debug heartbeat lost; exited'
                    generation = self.generation
                    if previous == 'calibrate':
                        self.cal_done = [False]*15
                        self.cal_expected_index = None
                        self.cal_reply_event.set()
                        self.bridge.notify('duck_cal', encode_cal(CAL_EXIT))
                    else:
                        self.bridge.notify('duck_servo', encode_servo(SERVO_EXIT, 10))
                    self._disarm_now()
            valid = bool(fb and age < .15 and fb.imu_ok and filt
                         and filt['imu_fusion_ready']
                         and age + elapsed_us(fb.sample_us, fb.imu_us)/1e6 <= IMU_STALE_S)
            now = time.monotonic()
            if self.sitstand_phase == 'stand' and now >= self.sitstand_until:
                self._stop_sitstand_locked()
            if self.kick_until and now >= self.kick_until:
                self._stop_kick_locked()
            self._expire_roulade_locked(now)
            rolling = self._roulade_on(now) and self.action_preparation is None
            if mode in ('shadow', 'policy') and not rolling:
                changed = self.recovery.update(filt['grav'] if filt else [0,0,0],
                                               now, valid)
                self.policy_needs_reset |= changed
                if changed and self.recovery.phase != 'walk':
                    self._cancel_actions_locked()
            phase = self.recovery.phase
            if phase == 'walk':
                self._advance_preparation_locked(now, valid)
            elif self.action_preparation:
                self._cancel_preparation_locked()
            mode, generation = self.mode, self.generation
            twist, head, mouth = list(self.twist), list(self.head), self.mouth
            rolling = self._roulade_on(now) and self.action_preparation is None
            if rolling and phase != 'walk':
                self._stop_roulade_locked()
                rolling = False
            picking = self.pick_active and phase == 'walk' and not rolling
            sitting = self.sitstand_phase != 'idle' and phase == 'walk'
            kicking = bool(self.kick_until) and phase == 'walk'
            if kicking:
                twist, head, mouth = [0.0]*3, [0.0]*4, 0.0
                selected = self.kick_policies[self.kick_side]
            elif sitting:
                twist = [1.0 if self.sitstand_phase == 'sit' else 0.0, 0.0, 0.0]
                selected = self.sitstand_policy
            elif rolling:
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
            with self.lock:
                if self.generation == generation and self.mode in EXCLUSIVE_MODES:
                    self._disarm_now()  # Keep the MCU-side exclusive-mode watchdog alive.
            return
        target = self.policy.default_deg
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
                acquired = time.monotonic()
                if reset_policy:
                    selected.reset_action_filter()
                action = selected.infer(filt['grav'], filt['gyro_rad'], filt['angles_deg'], filt['vels_deg'], twist, head)
                target = selected.action_to_deg(action)
            finish = time.monotonic()
            with self.lock:
                self.inferred += 1; self.infer_times.append(finish)
                self.infer_ms.append((finish-begin)*1000)
        with self.lock:
            # A stop arriving during ONNX always wins; never send its obsolete action.
            if self.generation != generation:
                return
            mode = self.mode
            if mode in EXCLUSIVE_MODES:
                return
            if rolling and self._expire_roulade_locked(time.monotonic()):
                return  # An inference finishing late must not submit an expired action.
            command_mode = {'shadow':DISARM,'off':DISARM,'hold':HOLD,'policy':POLICY}[mode]
            if self.arm_pending:
                command_mode = ARM
                # Initial enable always uses measured angles.
                target = list(fb.angles)
                self.arm_pending = False
                if inferring and (rolling or picking or sitting or kicking):
                    self.policy_needs_reset = True
            else:
                target = list(target)
                target[MOUTH_INDEX] = mouth
            if fb is None: return
            self.last_action = list(target)
            self.seq = (self.seq+1)&0xffffffff
            payload = encode_command(self.seq, fb.sample_us, command_mode, target)
            self.bridge.notify('duck_command', payload)
            self.sent += 1
            if inferring and command_mode in (DISARM, POLICY):
                submitted = time.monotonic()
                if rolling:
                    self._record_roulade_frame_locked(submitted, (finish-begin)*1000,
                        (acquired-begin)*1000, (submitted-step_started)*1000)
                if picking:
                    self.pick_phi += CONTROL_PERIOD / PICK_PERIOD_S
                    if self.pick_phi >= 1.0:
                        self._stop_pick_locked()
                        self.policy_needs_reset = True
                if sitting and self._action_first_target == 'stand':
                    self.sitstand_until = submitted+SITSTAND_STAND_S
                if kicking and self._action_first_target == self.kick_side+'_kick':
                    self.kick_until = submitted+KICK_S
                self._action_first_target = None

    def _keep_warm_idle(self):
        """Run one dummy pass on an idle session so all policy sessions stay hot."""
        with self.lock:
            phase = self.recovery.phase
            pending = self.action_preparation
            if pending:
                idle = pending['policy']
            else:
                if (self._roulade_on(time.monotonic()) or self.pick_active
                        or self.kick_until or self.sitstand_phase == 'stand'):
                    return
                active = self.sitstand_policy if self.sitstand_phase == 'sit' else (
                    self.policy if phase == 'walk' else self.getup_policy)
                candidates = [p for p in self._models() if p is not active]
                self._warm_index = (self._warm_index + 1) % len(candidates)
                idle = candidates[self._warm_index]
            if not self.policy_lock.acquire(blocking=False):
                return
        try:
            idle.warm(1)
        finally:
            self.policy_lock.release()
        if pending:
            with self.lock:
                if self.action_preparation is pending:
                    pending['warm_passes'] += 1

    def run(self):
        deadline = time.monotonic()
        while not self.stop_event.is_set():
            try:
                self.step()
            except Exception as exc:
                with self.lock:
                    self.last_error = str(exc); self.mode = 'off'; self.generation += 1; self.arm_pending = False
                    self._cancel_actions_locked()
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
            if self._imu_job['active']:
                self._imu_job_token += 1
                self._imu_job.update(state='cancelled', active=False, message='App stopped. Calibration cancelled.')
            previous = self.mode
            self.mode = 'off'; self.generation += 1; self.arm_pending = False
            self._cancel_actions_locked()
            if previous == 'calibrate':
                self.cal_done = [False]*15
                self.cal_expected_index = None
                self.cal_reply_event.set()
                self.bridge.notify('duck_cal', encode_cal(CAL_EXIT))
            elif previous == 'servo_debug':
                self.bridge.notify('duck_servo', encode_servo(SERVO_EXIT, 10))
            self.seq += 1
            self.bridge.notify('duck_command', encode_command(self.seq, 0, DISARM, self.policy.default_deg))

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
                raw_pos=list(self.raw_pos), encoder_center=SERVO_CENTER_POS,
                cal_done=list(self.cal_done), cal_hold_pos=CAL_HOLD_POS, raw_mask=self.raw_mask)
            result['control_ready']=bool(fb and fb.imu_ok and now-self.rx_time<.15 and self.mode not in EXCLUSIVE_MODES)
            result['servo_scan_version'] = 2
            pending = self.action_preparation
            result['action_preparation'] = dict(active=bool(pending),
                action=pending['name'] if pending else None,
                remaining_s=max(0.0, pending['ready_at']-now) if pending else 0.0,
                warm_passes=pending['warm_passes'] if pending else 0)
            voltage_age = (now-self.voltage_rx_time)*1000 if self.voltage_packet else None
            voltage_fresh = voltage_age is not None and 0 <= voltage_age <= VOLTAGE_TIMEOUT_S*1000
            volts = self.voltage_packet['volts'] if voltage_fresh else []
            valid_volts = [v for v in volts if v is not None]
            result['voltage_v'] = median(valid_volts) if valid_volts else None
            result['voltage_age_ms'] = voltage_age
            result['servo_voltage_v'] = {str(MOTOR_IDS[i]): v for i, v in enumerate(volts) if v is not None}
            result['imu_calibration'] = self._imu_status_locked()
            result['roller'] = dict(enabled=self.roller_mode, model=self.roller_policy.file,
                                    walk_model=self.policy.file, default_deg=self.roller_policy.default_deg,
                                    yaw_limit=ROLLER_YAW_LIMIT,
                                    action_scale=self.roller_policy.action_scale,
                                    action_alpha=self.roller_policy.action_alpha,
                                    action_alpha_head=self.roller_policy.action_alpha_head)
            result['deployment'] = dict(reference='rl/raspi_deploy', control_hz=1.0 / CONTROL_PERIOD,
                home_deg=self.policy.home_deg.tolist(), default_deg=self.policy.default_deg,
                action_scale=self.policy.action_scale, action_alpha=self.policy.action_alpha,
                action_alpha_head=self.policy.action_alpha_head,
                gyro_alpha=self.filter.gyro_alpha, velocity_alpha=self.filter.vel_alpha,
                gravity_tau_s=self.filter.gravity_tau_s,
                imu_bias_deg=[0., 0., 0.] if self._imu_record else list(IMU_BIAS_DEG),
                imu_acc_offset_ms2=self._imu_record['acc_offset_ms2'] if self._imu_record else [0., 0., 0.],
                command_acc_x_bias_ms2=self.filter.command_acc_x_bias_ms2,
                filter_clock='mcu_imu', snapshot='latest',
                command_timeout_enabled=False, imu_stale_ms=IMU_STALE_S*1000)
            result['mcu_runtime'] = dict(self.diagnostics)
            if self.diagnostics.get('imu_period_us'):
                result['mcu_runtime']['imu_hz'] = 1e6/self.diagnostics['imu_period_us']
            result['recovery'] = self.recovery.status()
            result['recovery']['active'] = self.mode in ('shadow','policy')
            result['pick'] = dict(active=self.pick_active, phi=self.pick_phi,
                                 period_s=PICK_PERIOD_S, mouth_close_phi=PICK_MOUTH_CLOSE_PHI,
                                 model=self.pick_policy.file)
            remain = ROULADE_S if self.roulade_pending else max(0.0, self.roulade_until - now)
            result['roulade'] = dict(active=self._roulade_on(now), remaining_s=remain,
                                    seconds=ROULADE_S, model=self.roulade_policy.file,
                                    timing=self._roulade_timing_locked(now))
            result['sitstand'] = self._sitstand_status_locked(now)
            result['right_kick'] = self._kick_status_locked(now, 'right')
            result['left_kick'] = self._kick_status_locked(now, 'left')
            if self.mode in ('shadow','policy'):
                if self.kick_until and self.recovery.phase == 'walk':
                    result['active_model'] = self.kick_policies[self.kick_side].file
                elif self.sitstand_phase != 'idle' and self.recovery.phase == 'walk':
                    result['active_model'] = self.sitstand_policy.file
                elif remain > 0 and not pending and self.recovery.phase == 'walk':
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
                acc_corrected, gyro_corrected = self.filter.correct_imu(fb.acc, fb.gyro)
                result.update(servo_ids=[i for j,i in enumerate(MOTOR_IDS) if fb.mask & (1<<j)],
                    imu_ok=fb.imu_ok, enabled=fb.enabled, mcu_watchdog=bool(fb.flags&4),
                    mcu_calibrating=fb.calibrating, mcu_servo_debug=fb.servo_debugging,
                    mcu_seq=fb.seq, mcu_period_us=fb.period_us, mcu_work_us=fb.work_us,
                    mcu_overruns=fb.overruns, mcu_report_drops=fb.report_drops,
                    mcu_invalid_commands=fb.invalid_commands, mcu_command_seq=fb.command_seq,
                    mcu_command_age_us=fb.command_age_us, angles=fb.angles,
                    velocities=fb.velocities, targets=fb.targets, acc=fb.acc, gyro=fb.gyro,
                    acc_corrected=acc_corrected, gyro_corrected=gyro_corrected,
                    gravity=self.filtered['grav'] if self.filtered else None,
                    missing_joint_ids=[i for j,i in enumerate(MOTOR_IDS) if not fb.mask&(1<<j)])
            return result
