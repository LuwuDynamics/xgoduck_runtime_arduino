"""100Hz receive/filter callback and independent 50Hz ONNX loop."""
import collections
import math
import os
import secrets
import threading
import time
from statistics import median
from policy import Policy
from runtime_gains import RECOMMENDED_GAINS
from recovery import Recovery
from stall_recovery import StallRecovery
from proc import SignalFilter
from heading import HeadingHold
from standing_imu import (CALIBRATION_PATH, SAMPLE_SECONDS, ImuCorrection, ImuMovementError,
                          estimate as estimate_imu, load as load_imu,
                          save as save_imu, disable as disable_imu)
from rl_core import (MOTOR_IDS, POLICY_IDS, IMU_BIAS_DEG, MOUTH_INDEX, MOUTH_MAX_DEG, MOUTH_MIN_DEG,
                     ROLLER_LEG_DEFAULT_DEG)
from wire import *

EXCLUSIVE_MODES = ('calibrate', 'servo_debug')
PICK_PERIOD_S = 4.0
PICK_MOUTH_CLOSE_PHI = 0.4
ROULADE_S = 1.7
ROULADE_ALPHA = 0.05
ROULADE_START_TIMEOUT_S = 0.5
SITSTAND_STAND_S = 1.2
SITSTAND_STAND_ALPHAS = (0.2, 0.2)
SITSTAND_STAND_ACTION_SCALE = 1.1
KICK_S = 0.4
CONTROL_PERIOD = 1.0 / 50.0
IMU_CALIBRATION_SETTLE_S = .5
IMU_CALIBRATION_MAX_RETRIES = 10
SITTING_CALIBRATION_RETRIES = 3
SITTING_CALIBRATION_RETRY_DELAY_S = .35
SITTING_CALIBRATION_ACK_TIMEOUT_S = 2.5  # 15 writes with 100 ms spacing, plus feedback and settling.
LOW_BATTERY_MV = 6000
BATTERY_REARM_MV = 7600
BATTERY_SAMPLE_MAX_AGE_S = .4
LOW_BATTERY_CONFIRM_S = 5.
BATTERY_REARM_CONFIRM_S = 2.
BATTERY_WARNING_TIMEOUT_S = 12.0
BATTERY_SIT_SETTLE_S = 1.5
LEG_FEEDBACK_MASK = 0x3ff  # The first ten bits of the hardware servo order.
SERVO_FEEDBACK_GRACE_FRAMES = 15
SERVO_FEEDBACK_GRACE_US = 150_000
FEEDBACK_HOLD_S = .15
FEEDBACK_HOLD_REASON = 'STM32 feedback timed out; motion paused, holding last targets'
IMU_DISARM_REASON = 'sensor feedback lost; disarmed'
BRIDGE_STALL_S = 1.
BRIDGE_RECOVERY_REASON = 'STM32 Bridge is reconnecting. 运动通信恢复中，请稍后重试。'
ACTION_PREPARE_S = 0.2
ACTION_PREPARE_TIMEOUT_S = 0.5
ROLLER_YAW_LIMIT = 0.45  # rad/s, symmetric in Roller mode.
ROLLER_VX_ACCEL_LIMIT = 0.50  # Soft-start accel slew rate (units/s); zero delay on release/brake.
ROLLER_HIPS = tuple(MOTOR_IDS.index(i) for i in (11, 21))
ROLLER_CAPTURE_DEG = 8.
MANUAL_INPUT_TIMEOUT_S = 1.
COMMAND_ACC_X_BIAS_MS2 = 1.5  # Temporary IMU trim magnitude; set to 0 to disable.
VOLTAGE_TIMEOUT_S = 3.0
SERVO_ALARM_DISPLAY_S = 2.0
STALL_RETREAT = 'Servo stalled; returning to the default stance.'
STALL_DISARMED = 'Stall recovery stopped; torque off. Support the duck and select Walk / get up.'

class RollerStopPose:
    """Monitor closed-loop RL deceleration on zero twist without open-loop leg overrides."""
    MIN_DECEL_S = 0.35
    GLIDE_TIMEOUT_S = 2.0
    HIP_TOLERANCE_DEG = 6.
    LEG_TOLERANCE_DEG = 8.

    def __init__(self, owner, feedback, home, now, *, forward_stride=False):
        self.phase = 'gliding'
        self.owner = owner
        self.seen = self.phase_started = now
        self.forward_stride = bool(forward_stride)
        self.home = list(home[:len(ROLLER_LEG_DEFAULT_DEG)])
        self.confirmed = False
        self.stable_since = None
        self.last_sample = None

    def expired(self, now):
        if now - self.phase_started >= self.GLIDE_TIMEOUT_S:
            return 'glide_timeout'
        return None

    def observe(self, fb, upright, gyro_rad, now):
        if self.confirmed:
            return
        if now - self.phase_started < self.MIN_DECEL_S:
            self.stable_since = None
            return
        if self.last_sample is not None and elapsed_us(fb.sample_us, self.last_sample) > StallRecovery.MAX_GAP_US:
            self.stable_since = None
        self.last_sample = fb.sample_us
        stable = (upright
                  and math.hypot(*gyro_rad) <= StallRecovery.BODY_SETTLE_RAD_S
                  and all(abs(fb.angles[i] - self.home[i]) <= self.HIP_TOLERANCE_DEG for i in ROLLER_HIPS)
                  and all(abs(fb.angles[i] - goal) <= self.LEG_TOLERANCE_DEG
                          and abs(fb.velocities[i]) <= StallRecovery.SETTLE_SPEED_DEG_S
                          for i, goal in enumerate(self.home)))
        if not stable:
            self.stable_since = None
        elif self.stable_since is None:
            self.stable_since = fb.sample_us
        elif elapsed_us(fb.sample_us, self.stable_since) >= StallRecovery.SETTLE_US:
            self.confirmed = True

    def apply(self, target, now):
        # Never overwrite or clamp leg targets open-loop; the roller policy stays in
        # closed-loop balance control on all 14 joints.
        return


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
        self.roller_policy.action_scale = 0.6
        self.roller_policy.set_action_alphas(leg=0.4, head=0.5)
        self.model_message += '; ' + message
        self.roller_mode = False
        self._roller_pose = None
        self._roller_glide_result = None
        self._roller_stride = None
        self._roller_vx_cmd = 0.0
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
            model.action_scale = 1.0
            model.set_action_alphas(leg=0.2, head=0.2)
        self.recovery = Recovery(period_s=CONTROL_PERIOD)
        self.stall = StallRecovery()
        self._last_policy_file = None
        self.heading = HeadingHold()
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
        self._imu_load_failed = False
        self._imu_job_token = 0
        self._imu_job = dict(state='idle', active=False, progress=0., samples=0,
                             message='Not calibrated. Hold the board upright and still.')
        try:
            self._imu_record = load_imu(self._imu_path)
            if self._imu_record is not None:
                self._imu_job.update(state='saved', message='Saved IMU calibration loaded.')
        except (OSError, ValueError, TypeError, KeyError):
            self._imu_load_failed = True
            self._imu_job.update(state='error', message='Calibration could not be loaded. Calibrate again or reset it.')
        self.filter = SignalFilter(ImuCorrection(self._imu_record) if self._imu_record else None)
        self.feedback = self.filtered = None
        self.servo_feedback_log = None
        self._servo_alarm_seen = {}
        self._servo_feedback_history = collections.deque(maxlen=SERVO_FEEDBACK_GRACE_FRAMES + 1)
        self.voltage_packet = None
        self.voltage_rx_time = 0.0
        self.rx_time = 0.0
        self._imu_source_us = None
        self._imu_source_time = 0.0
        self.diagnostics = {}
        self.diagnostic_time = 0.
        self.battery_phase = 'normal'
        self.battery_since = None
        self.battery_deadline = 0.
        self.battery_sit_since = None
        self.battery_warning = None
        self.battery_warning_done = threading.Event()
        self.mode = 'shadow'
        self.fault_hold = False
        self.bridge_recovery = None
        self._bridge_confirmation_seq = None
        self._command_tx_at = self._command_tx_since = None
        self._motion_needs_neutral = set()
        self.generation = 0
        self.twist, self.head, self.mouth = [0.0]*3, [0.0]*4, 0.0
        self.web_time = 0.0
        self.input_source = 'web'
        self.arm_pending = False
        self.seq = self.received = self.inferred = self.sent = self.bad_frames = self.gaps = 0
        self.last_error = ''
        self.stop_event = threading.Event()
        self.rx_times = collections.deque(maxlen=1000)
        self.infer_times = collections.deque(maxlen=500)
        self.infer_ms = collections.deque(maxlen=500)
        self.rx_intervals = collections.deque(maxlen=1000)
        self.loop_missed = 0
        self._fall_last_loop_missed = 0
        self._last_fall_event = None
        self._warm_tick = 0
        self.last_action = [0.0]*15
        self.started = time.monotonic()
        self.boot_pd = {}
        self.raw_pos = [SERVO_CENTER_POS]*15
        self.cal_done = [False]*15
        self.cal_reply = None
        self.cal_seq = 0
        self.cal_reply_event = threading.Event()
        self.cal_expected_index = None
        self.sitting_calibration_retry = None
        self.raw_mask = 0
        self.servo_reply = None
        self.servo_reply_event = threading.Event()
        self.servo_expected_reply = None
        self.guarded_servo_test = None

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
            expected = (pkt['op'], pkt['target_id'])
            if pkt['op'] in SERVO_TEST_OPS:
                expected += (pkt['session'],)
            if self.servo_expected_reply != expected:
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
            now = time.monotonic()
            if now - self.diagnostic_time > BATTERY_SAMPLE_MAX_AGE_S:
                self.battery_since = None
            self.diagnostics, self.diagnostic_time = values, now
            if self.servo_feedback_log:
                self.servo_feedback_log.sample_voltage(values.get('servo_bus_voltage_mv'))

    def _battery_voltage(self, now):
        if now - self.diagnostic_time > BATTERY_SAMPLE_MAX_AGE_S:
            return None
        return self.diagnostics.get('servo_bus_voltage_mv') or None

    def _require_battery(self):
        if self.battery_phase != 'normal':
            raise ValueError('低电压保护中，请充电后重新使能。 Low battery protection is active.')

    def _require_motion_available(self):
        self._require_battery()
        if self.stall.phase in ('retreat', 'disarmed'):
            raise ValueError(STALL_RETREAT if self.stall.phase == 'retreat' else STALL_DISARMED)
        if self.bridge_recovery:
            raise ValueError(BRIDGE_RECOVERY_REASON)
        if self.fault_hold:
            raise ValueError(FEEDBACK_HOLD_REASON)

    def _battery_step_locked(self, now):
        voltage = self._battery_voltage(now)
        phase = self.battery_phase
        if phase in ('normal', 'locked'):
            crossed = voltage is not None and (
                voltage < LOW_BATTERY_MV if phase == 'normal' else voltage >= BATTERY_REARM_MV)
            if not crossed:
                self.battery_since = None
                return
            if self.battery_since is None:
                self.battery_since = now
                return
            duration = LOW_BATTERY_CONFIRM_S if phase == 'normal' else BATTERY_REARM_CONFIRM_S
            if now - self.battery_since < duration:
                return
            self.battery_since = None
            if phase == 'locked':
                self.battery_phase = 'normal'
                return
            self.battery_deadline = now + BATTERY_WARNING_TIMEOUT_S
            self.battery_sit_since = None
            self._cancel_preparation_locked()
            # The ordinary sit entry point enforces calibration, recovery and action interlocks.
            if (self.mode in ('hold', 'policy') and self.feedback and self.feedback.enabled
                    and self.sitstand_phase == 'idle'):
                try:
                    self.set_sitstand('sit')
                except ValueError as exc:
                    self.last_error = str(exc)
            self.battery_phase = 'warning'
            self._stop_pick_locked()
            self._stop_roulade_locked()
            self._stop_kick_locked()
            self.twist, self.head, self.mouth = [0.]*3, [0.]*4, 0.
            self.input_source = 'web'
            self.arm_pending = False
            self.generation += 1
            self.battery_warning_done = threading.Event()
            if self.battery_warning is not None:
                threading.Thread(target=self._play_battery_warning, args=(self.battery_warning_done,),
                                 name='battery-warning', daemon=True).start()
            else:
                self.battery_warning_done.set()
        fb, filt = self.feedback, self.filtered
        age = now - self.rx_time
        ready = (self.mode in ('hold', 'policy') and fb and fb.enabled and age < .15
                 and fb.imu_ok and age + elapsed_us(fb.sample_us, fb.imu_us)/1e6 <= IMU_STALE_S
                 and not fb.flags & 4 and fb.command_age_us <= 250000
                 and fb.mask & LEG_FEEDBACK_MASK == LEG_FEEDBACK_MASK and filt and filt['imu_fusion_ready']
                 and self.recovery.phase == 'walk')
        if (ready and not self.fault_hold and not self.bridge_recovery
                and self.stall.phase not in ('retreat', 'disarmed') and now < self.battery_deadline):
            if self.action_preparation and self.action_preparation['name'] == 'sit':
                return
            if self.sitstand_phase == 'sit':
                if self.battery_sit_since is None:
                    self.battery_sit_since = now
                if now - self.battery_sit_since < BATTERY_SIT_SETTLE_S or not self.battery_warning_done.is_set():
                    return
        self.battery_phase = 'locked'
        self.set_mode('off')
        self._disarm_now()

    def _play_battery_warning(self, done):
        try:
            self.battery_warning()
        finally:
            done.set()

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
            mcu_reset = False
            if old:
                step = elapsed_us(fb.seq, old.seq)
                if step == 0: return
                if step > 0x80000000:
                    mcu_reset = True
                    self._servo_feedback_history.clear()
                    self._servo_alarm_seen.clear()
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
            if self.filtered['imu_fusion_updated']:
                self.heading.observe(self._heading_motion_locked(now), self.filtered['grav'],
                                     self.filtered['gyro_rad'], self._imu_source_time)
            # STM32 retains the last measured angle when a servo does not reply.
            # Only real replies enter the history used by the motion readiness check.
            self._servo_feedback_history.append((fb.seq, fb.sample_us, now, fb.mask))
            if self.mode == 'policy' and fb.enabled and not mcu_reset:
                self.stall.observe(fb, self.filtered, activity=self._stall_activity_locked(now))
            elif self.stall.phase == 'retreat':
                self.stall.invalid = True
            elif self.stall.phase == 'monitor' and self.stall.last_sample is not None:
                self.stall.clear_candidates()
            elif self.stall.phase == 'idle' and self.stall.last_sample is not None:
                self.stall.reset()
            if fb.servo_errors and fb.servo_errors != NO_SERVO_ERRORS:
                for index, code in enumerate(fb.servo_errors):
                    if code:
                        self._servo_alarm_seen[index] = (code, now)
            if self.servo_feedback_log:
                self.servo_feedback_log.observe(fb, old, self.mode, now, self.diagnostics.get('servo_bus_voltage_mv'),
                                               self.diagnostic_time, reset=mcu_reset)
            self.feedback, self.rx_time = fb, now
            self._check_roller_glide_locked(now)
            self.received += 1; self.rx_times.append(now)

    def _require_feedback(self, mask):
        fb = self.feedback
        if not fb or time.monotonic()-self.rx_time > .15:
            raise ValueError('MCU feedback is stale')
        if not fb.imu_ok or (fb.mask & mask) != mask:
            raise ValueError('required servo feedback / IMU missing')
        return fb

    def _heading_motion_locked(self, now):
        return (self.mode == 'policy'
                and self.recovery.phase == 'walk' and not self.pick_active
                and not self._roulade_on(now) and self.sitstand_phase == 'idle'
                and not self.kick_until and not self.action_preparation
                and not self._imu_job['active'])

    def set_heading_hold(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('heading hold enabled must be true or false')
        with self.lock:
            self.heading.enabled = enabled
            self.heading.release()
            self.generation += 1
            return self.heading.status()

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
        status = dict(self._imu_job, calibrated=bool(record), seconds=SAMPLE_SECONDS,
                      method='offset', acc_offset_ms2=record.get('acc_offset_ms2'),
                      gyro_zero_dps=record.get('gyro_zero_dps'),
                      calibrated_at=record.get('calibrated_at'), measurement=record.get('measurement'))
        if status['active']:
            status['phase'] = 'sampling' if status['samples'] else 'settling'
        return status

    def _imu_calibration_feedback_locked(self):
        fb = self._require_feedback(0)
        if time.monotonic() - self.rx_time + elapsed_us(fb.sample_us, fb.imu_us)/1e6 > IMU_STALE_S:
            raise ValueError('IMU readings are stale. Check the connection and try again.')
        return fb

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
            self._imu_calibration_feedback_locked()
            self.twist, self.head, self.mouth = [0.]*3, [0.]*4, 0.
            self.generation += 1
            self._imu_job_token += 1
            token = self._imu_job_token
            self._imu_job = dict(state='sampling', active=True, progress=0., samples=0,
                                 retry=0, max_retries=IMU_CALIBRATION_MAX_RETRIES,
                                 message='Hold the board still before sampling.')
            threading.Thread(target=self._sample_imu_calibration, args=(token, self.generation),
                             name='imu-calibration', daemon=True).start()
            return self._imu_status_locked()

    def _check_imu_job_locked(self, token, generation):
        if self.stop_event.is_set():
            raise ValueError('Calibration cancelled because the app stopped.')
        if token != self._imu_job_token or not self._imu_job['active'] or generation != self.generation:
            raise ValueError('Calibration cancelled because the control mode changed.')

    def _collect_imu_window(self, token, generation):
        if self.stop_event.wait(IMU_CALIBRATION_SETTLE_S):
            raise ValueError('Calibration cancelled because the app stopped.')
        with self.lock:
            self._check_imu_job_locked(token, generation)
            # The first accepted frame must arrive after this window's settling period.
            last_stamp = self._imu_source_time
        rows = []
        deadline = time.monotonic() + SAMPLE_SECONDS + 2
        while not self.stop_event.wait(.005):
            with self.lock:
                self._check_imu_job_locked(token, generation)
                fb = self._imu_calibration_feedback_locked()
                if time.monotonic() > deadline:
                    raise ValueError('Not enough IMU readings. Check the connection and try again.')
                stamp = self._imu_source_time
                if stamp < last_stamp:
                    raise ValueError('The IMU restarted. Try again.')
                if stamp == last_stamp:
                    continue
                last_stamp = stamp
                if not rows:
                    self._imu_job['message'] = f'Keep the board still. Calibrating for {SAMPLE_SECONDS:g} seconds…'
                rows.append((stamp, list(fb.acc), list(fb.gyro)))
                span = stamp - rows[0][0]
                self._imu_job.update(samples=len(rows), progress=min(1., span/SAMPLE_SECONDS))
                if span >= SAMPLE_SECONDS:
                    return rows
        raise ValueError('Calibration cancelled because the app stopped.')

    def _sample_imu_calibration(self, token, generation):
        retry = 0
        try:
            while True:
                rows = self._collect_imu_window(token, generation)
                try:
                    record = estimate_imu(rows)
                    break
                except ImuMovementError as exc:
                    values = ' '.join(f'{name}={value:.4f}' for name, value in exc.metrics.items())
                    print(f'[imu-calibration] rejected window {retry + 1}, samples={len(rows)} '
                          f'{values} exceeded={",".join(exc.exceeded)}', flush=True)
                    with self.lock:
                        self._check_imu_job_locked(token, generation)
                        if retry == IMU_CALIBRATION_MAX_RETRIES:
                            raise ValueError(f'No stable IMU readings after {retry} automatic retries. '
                                             'Support the board and try again.') from exc
                        retry += 1
                        self._imu_job.update(retry=retry, samples=0, progress=0.,
                                             message=f'Movement detected. Retrying ({retry}/{IMU_CALIBRATION_MAX_RETRIES}).')
            correction = ImuCorrection(record)
            with self.lock:
                self._check_imu_job_locked(token, generation)
                # A cancelled job must never publish a file or apply its result.
                save_imu(self._imu_path, record)
                self._imu_record = record
                self._imu_load_failed = False
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
                    cancelled = self.stop_event.is_set() or generation != self.generation
                    prefix = 'Previous calibration kept. ' if self._imu_record else ''
                    self._imu_job.update(state='cancelled' if cancelled else 'error', active=False,
                                         message=prefix + str(exc))

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
            self._imu_load_failed = False
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
            if self.stall.phase == 'retreat' and mode not in ('off', 'shadow'):
                raise ValueError(STALL_RETREAT)
            if self.stall.phase == 'disarmed' and mode == 'hold':
                raise ValueError(STALL_DISARMED)
            if mode not in ('off', 'shadow'):
                self._require_battery()
                if self.bridge_recovery:
                    raise ValueError(BRIDGE_RECOVERY_REASON)
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
            if mode == 'off' and self.servo_feedback_log:
                self.servo_feedback_log.request_off(self.feedback, self.mode, time.monotonic(),
                    self.diagnostics.get('servo_bus_voltage_mv'), self.diagnostic_time, self.rx_time)
            if mode == self.mode and mode != 'calibrate' and not self.fault_hold:
                self._finish_roller_glide_locked('interrupted')
                self._cancel_preparation_locked()
                self.web_time = time.monotonic()
                return {'mode': self.mode}
            if mode in ('hold', 'policy') and self.mode in EXCLUSIVE_MODES:
                raise ValueError('exit calibrate/servo_debug first')
            fb = None
            if mode in ('hold', 'policy'):
                fb = self._require_feedback(0)
                if self.stall.phase == 'disarmed' and fb.enabled:
                    raise ValueError(STALL_DISARMED)
            if self.stall.phase == 'retreat':
                self._log_stall_locked('stall_cancelled', time.monotonic(), 'manual stop')
                self.stall.phase = 'disarmed'
                self._disarm_now()
            elif self.stall.phase == 'monitor' and mode in ('policy', 'hold'):
                self.stall.clear_candidates()
            elif mode == 'policy' or self.stall.phase != 'disarmed':
                self.stall.reset()
            previous = self.mode
            self._roller_stride = None
            self._finish_roller_glide_locked('interrupted')
            self.generation += 1; self.mode = mode
            self.fault_hold = False
            self.heading.reset()
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
            if self.stall.phase == 'retreat':
                raise ValueError(STALL_RETREAT)
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
                self.stall.clear_candidates()  # Start the new mode with fresh alarm evidence.
                self._roller_stride = None
                self._finish_roller_glide_locked('interrupted')
                self.heading.kp = self.heading.SKATE_KP if enabled else self.heading.KP
                self.heading.reset()
                # Drop any action computed by the previously selected walk model.
                self.generation += 1
                if (self.recovery.phase == 'walk' and not self.pick_active
                        and not self._roulade_on(time.monotonic())):
                    self.policy_needs_reset = True
                self.twist = [0.0]*3
                self.web_time = time.monotonic()
            if enabled:
                self.filter.command_acc_x_bias_ms2 = 0.0
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
                    stand_action_alpha=SITSTAND_STAND_ALPHAS[0],
                    stand_action_alpha_head=SITSTAND_STAND_ALPHAS[1],
                    stand_action_scale=SITSTAND_STAND_ACTION_SCALE,
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
            self._require_motion_available()
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
            self._require_motion_available()
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
            self._require_motion_available()
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
            self._require_motion_available()
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

    def command(self, twist=None, head=None, mouth=None, source='web', roller_release=False):
        def values(seq, limits):
            if len(seq)!=len(limits): raise ValueError('wrong command dimension')
            seq = [float(v) for v in seq]
            if any(not math.isfinite(v) or abs(v)>limit for v,limit in zip(seq,limits)):
                raise ValueError('command exceeds bounds')
            return seq
        t = values(twist, [.4,.3,1.0]) if twist is not None else None
        if type(roller_release) is not bool or (roller_release and (
                t != [0.] * 3 or source not in ('web', 'cockpit', 'gamepad'))):
            raise ValueError('Roller release requires a neutral manual drive command.')
        h = values(head, [1.0]*4) if head is not None else None
        m = None
        if mouth is not None:
            m = float(mouth)
            if not math.isfinite(m) or not MOUTH_MIN_DEG <= m <= MOUTH_MAX_DEG:
                raise ValueError('command exceeds bounds')
        with self.lock:
            if self.stall.phase in ('retreat', 'disarmed'):
                return {'ok': True, 'ignored': STALL_RETREAT if self.stall.phase == 'retreat' else STALL_DISARMED}
            if self.battery_phase != 'normal':
                return {'ok': True, 'ignored': 'low battery protection'}
            if t is not None and source == self.input_source and source in ('web', 'gamepad'):
                if not any(t):
                    self._motion_needs_neutral.discard(source)
                elif self.fault_hold or self.bridge_recovery:
                    self._motion_needs_neutral.add(source)
                elif source in self._motion_needs_neutral:
                    return {'ok': True, 'ignored': 'Release controls before resuming motion.'}
            if self.bridge_recovery:
                return {'ok': True, 'ignored': BRIDGE_RECOVERY_REASON}
            if self.fault_hold:
                return {'ok': True, 'ignored': FEEDBACK_HOLD_REASON}
            if source != self.input_source:
                return {'ok': True, 'ignored': 'inactive input source'}
            if self._imu_job['active']:
                self.web_time = time.monotonic()
                return {'ok': True, 'ignored': 'imu_calibration'}
            if self.mode in EXCLUSIVE_MODES:
                raise ValueError('motion commands disabled in calibrate/servo_debug')
            if t is not None and not self.roller_mode and not -.2 <= t[0] <= .2:
                raise ValueError('walking vx must be between -0.2 and 0.2')
            if t is not None and self.roller_mode and abs(t[2]) > ROLLER_YAW_LIMIT:
                raise ValueError(f'roller wz must be between {-ROLLER_YAW_LIMIT} and {ROLLER_YAW_LIMIT}')
            if t is not None and not self.pick_active and self.sitstand_phase == 'idle' and not self.kick_until:
                self._roller_command_locked(t, roller_release, source, time.monotonic())
                self.heading.command(self._heading_motion_locked(time.monotonic()), self.twist)
            if h is not None and not self.pick_active and not self.kick_until: self.head = h
            if m is not None and not self.pick_active and not self.kick_until: self.mouth = m
            self.web_time = time.monotonic()
            if roller_release or self._roller_pose:
                return {'ok': True, 'roller_gliding': bool(self._roller_pose and self._roller_pose.phase != 'holding')}
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
                self._require_battery()
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

    def cal_sitting(self, positions):
        with self.cal_op_lock:
            with self.lock:
                self._require_battery()
                self._check_imu_idle_locked()
                if self.mode != 'calibrate':
                    raise ValueError('not in calibrate mode')
                generation = self.generation
                positions = tuple(positions)
                encode_cal_positions(positions, 0)  # Validate before resetting any centers.
                stages = (
                    ('2048', lambda seq: encode_cal(CAL_CENTER_ALL, seq=seq)),
                    ('target positions', lambda seq: encode_cal_positions(positions, seq)),
                )
                self.web_time = time.monotonic()
                self.sitting_calibration_retry = 0
                # Reserve the calibration bus throughout the operation, including retry waits.
                self.cal_expected_index = ((self.cal_seq + 1) & 0xffffffff, CAL_ALL_JOINTS)
            try:
                retries = 0
                for stage, encode in stages:
                    while True:
                        failure = self._try_sitting_calibration_stage(generation, stage, encode)
                        if failure is None:
                            break
                        if retries == SITTING_CALIBRATION_RETRIES:
                            message = f'Sitting calibration failed after {retries} retries. {failure}'
                            print(f'[calibration] {message}', flush=True)
                            raise ValueError(message)
                        retries += 1
                        with self.lock:
                            self.sitting_calibration_retry = retries
                        print(f'[calibration] {stage}: {failure} Retrying ({retries}/{SITTING_CALIBRATION_RETRIES}).',
                              flush=True)
                        time.sleep(SITTING_CALIBRATION_RETRY_DELAY_S)
                return {'ok': True, 'positions': list(positions)}
            finally:
                with self.lock:
                    self.sitting_calibration_retry = None
                    self.cal_expected_index = None

    def _try_sitting_calibration_stage(self, generation, stage, encode):
        with self.lock:
            self._require_battery()
            self._check_imu_idle_locked()
            if self.mode != 'calibrate' or self.generation != generation:
                raise ValueError('calibration session changed')
            fb = self.feedback
            if not fb or time.monotonic() - self.rx_time > .15:
                return 'MCU feedback is stale.'
            if fb.enabled or any(self.cal_done):
                raise ValueError('Unload all joints before sitting calibration.')
            if not fb.calibrating:
                return 'STM32 has not entered calibration mode.'
            if fb.mask != ALL_MASK:
                return 'All 15 servos must be online for sitting calibration.'
            self.cal_seq = (self.cal_seq + 1) & 0xffffffff
            self.cal_reply = None
            self.cal_reply_event.clear()
            self.cal_expected_index = (self.cal_seq, CAL_ALL_JOINTS)
            try:
                self.bridge.notify('duck_cal', encode(self.cal_seq))
            except OSError as error:
                return f'Could not send calibration to STM32: {error}'
        self.cal_reply_event.wait(SITTING_CALIBRATION_ACK_TIMEOUT_S)
        with self.lock:
            self._require_battery()
            if self.mode != 'calibrate' or self.generation != generation:
                raise ValueError('calibration session changed')
            if self.cal_reply is None:
                return f'Sitting calibration timed out at {stage}.'
            if not self.cal_reply['ok']:
                return f'Sitting calibration failed at {stage}.'
        return None

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
                reply = dict(ok=True, op=SERVO_SET_ID, target_id=target_id)
            else:
                if self._servo_op('ping', new_id)['ok']:
                    raise ValueError(f'ID {new_id} is already in use; choose another ID')
                reply = self._servo_op(action, target_id, new_id, kp, kd)
                time.sleep(0.05)
                if not self._servo_op('ping', new_id)['ok']:
                    raise ValueError(f'ID write sent, but ID {new_id} did not reply. Scan again')
                if self._servo_op('ping', target_id)['ok']:
                    raise ValueError('Both old and new IDs replied. Connect only one servo and scan again')
            try:
                self._servo_op('reset_mid', new_id)
            except ValueError as exc:
                raise ValueError(f'ID {new_id} is set. Scan again and retry with this ID.') from exc
            try:
                with self.lock:
                    self._require_servo_debug()
                op = SERVO_INIT_ID_PROTECTION
                protection = self._exchange_servo(op, new_id, encode_servo(op, new_id, kp=0, kd=0))
                if not (protection['voltage_protection'] and protection['overcurrent_protection']
                        and protection['current_raw'] == round(2.0 / PROTECTION_CURRENT_UNIT_A)):
                    raise ValueError('unexpected protection readback')
            except (OSError, ValueError) as exc:
                raise ValueError(f'ID {new_id} is set and the midpoint command was sent. '
                                 'Protection settings were not confirmed. Keep the servo at its midpoint '
                                 'and retry using the same ID.') from exc
            try:
                kp, kd = RECOMMENDED_GAINS
                # The write acknowledgement checks both the EEPROM and runtime registers.
                gains = self._servo_op('set_gains', new_id, kp=kp, kd=kd)
                if (gains['kp'], gains['kd']) != (kp, kd):
                    raise ValueError('unexpected gain readback')
            except (OSError, ValueError) as exc:
                raise ValueError(f'ID {new_id} is set. Protection verified, but KP/KD were not confirmed. '
                                 'Scan for the new ID and check its gains.') from exc
            return dict(reply, new_id=new_id, protection=protection, gains=gains)

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
        return self._exchange_servo(op, target_id, payload)

    def servo_test(self, action, target_id, session=None, offset_deg=0):
        ops = {'start': SERVO_TEST_START, 'move': SERVO_TEST_MOVE, 'stop': SERVO_TEST_STOP}
        if action not in ops or type(target_id) is not int or target_id not in MOTOR_IDS:
            raise ValueError('Select one of Duck’s 15 servo IDs for the test.')
        op = ops[action]
        if op == SERVO_TEST_START:
            session = secrets.randbelow(65535) + 1
        payload = encode_servo_test(op, target_id, session, offset_deg)
        if op == SERVO_TEST_STOP:
            with self.lock:
                # Free the exchange slot even if the in-flight angle update lost its reply.
                if self.servo_expected_reply == (SERVO_TEST_MOVE, target_id, session):
                    self.servo_expected_reply = None
                    self.servo_reply_event.set()
        with self.servo_op_lock:
            with self.lock:
                if op != SERVO_TEST_STOP:
                    self._require_battery()
                    fb = self.feedback
                    now = time.monotonic()
                    if (self.mode != 'servo_debug' or not fb or not fb.servo_debugging
                            or fb.enabled or now - self.rx_time >= .15):
                        raise ValueError('Servo setup is not ready for the position test.')
                    # START reads the encoder on the MCU before enabling torque.
                    # Only legacy MOVE sessions need the Linux feedback-mask check.
                    if (op == SERVO_TEST_MOVE and self.guarded_servo_test != (target_id, session)
                            and not fb.mask & (1 << MOTOR_IDS.index(target_id))):
                        raise ValueError(f'No servo replied at ID {target_id}.')
                    self.web_time = now
            try:
                reply = self._exchange_servo(op, target_id, payload, session=session)
            except ValueError as error:
                with self.lock:
                    failed = self.servo_reply
                if op == SERVO_TEST_MOVE and failed and not failed['ok'] and failed['session'] == session:
                    if seconds := failed.get('idle_timeout_s'):
                        raise ValueError(f'Servo unloaded after {seconds} seconds without an angle change.') from error
                    if limit := failed.get('feedback_limit'):
                        raise ValueError(f'Servo ID {target_id} missed more than {limit} consecutive feedback frames.') from error
                raise
            if reply['session'] != session:
                raise ValueError('Unexpected servo test reply.')
            if op == SERVO_TEST_START:
                self.guarded_servo_test = (target_id, session) if reply.get('feedback_limit') else None
            elif op == SERVO_TEST_STOP and self.guarded_servo_test == (target_id, session):
                self.guarded_servo_test = None
            return reply

    def _exchange_servo(self, op, target_id, payload, session=None):
        with self.lock:
            self.servo_reply_event.clear()
            self.servo_reply = None
            self.servo_expected_reply = (op, target_id) if session is None else (op, target_id, session)
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
            if op == SERVO_TEST_MOVE and self.servo_expected_reply is None:
                raise ValueError('Servo test angle update cancelled.')
            reply = self.servo_reply
        if not reply or reply['op'] != op:
            raise ValueError('unexpected servo reply')
        if target_id is not None and reply['target_id'] != target_id:
            raise ValueError('unexpected servo reply ID')
        if op == SERVO_TEST_STOP and not reply.get('torque_off'):
            raise ValueError('STM32 did not confirm that torque is off.')
        if not reply['ok'] and op != SERVO_PING:
            raise ValueError('servo operation failed')
        return reply

    def _pause_for_feedback_locked(self, age, *, command_stalled=False):
        if self.fault_hold:
            return
        self._finish_roller_glide_locked('interrupted')
        self.fault_hold = True
        self.generation += 1  # Drop an ONNX result computed before the pause.
        self.arm_pending = False
        self.last_error = BRIDGE_RECOVERY_REASON if command_stalled else FEEDBACK_HOLD_REASON
        if self.input_source in ('web', 'gamepad') and any(self.twist):
            self._motion_needs_neutral.add(self.input_source)
        self.twist = [0.0]*3
        if self.sitstand_phase == 'sit':
            self._cancel_preparation_locked()  # Keep the seated posture.
        else:
            self._cancel_actions_locked()
        self.heading.reset()
        lane = 'command echo' if command_stalled else 'state feedback'
        print(f'[motion] Holding last targets: STM32 {lane} age={age*1000:.0f} ms', flush=True)

    def _command_stalled_locked(self, now, seconds):
        fb = self.feedback
        return bool(fb and now - self.rx_time < FEEDBACK_HOLD_S
                    and self.mode not in EXCLUSIVE_MODES and not (fb.calibrating or fb.servo_debugging)
                    and self._command_tx_at is not None and now - self._command_tx_at < FEEDBACK_HOLD_S
                    and now - self._command_tx_since >= seconds
                    and fb.command_age_us >= seconds * 1e6)

    def _begin_bridge_recovery_locked(self, fault, now):
        if self.bridge_recovery != fault:
            self.bridge_recovery = fault
            self._bridge_confirmation_seq = None
        if self.mode in ('hold', 'policy'):
            if fault == 'command':
                self._pause_for_feedback_locked(self.feedback.command_age_us / 1e6, command_stalled=True)
            else:
                self._pause_for_feedback_locked(now - self.rx_time)

    def bridge_stall(self):
        """Called by the watchdog. An advancing MCU state can still have a stale command echo."""
        with self.lock:
            now = time.monotonic()
            last_feedback = self.rx_time if self.feedback else self.started
            if now - last_feedback >= BRIDGE_STALL_S:
                self._begin_bridge_recovery_locked('feedback', now)
                return 'feedback'
            if self._command_stalled_locked(now, BRIDGE_STALL_S):
                if self.bridge_recovery is None:
                    self._begin_bridge_recovery_locked('command', now)
                return 'command'
            return None

    def _send_motion_locked(self, fb, command_mode, target):
        self.seq = (self.seq+1)&0xffffffff
        self.bridge.notify('duck_command', encode_command(self.seq, fb.sample_us, command_mode, target))
        self.sent += 1
        now = time.monotonic()
        if self._command_tx_at is None or now - self._command_tx_at >= FEEDBACK_HOLD_S:
            self._command_tx_since = now
        self._command_tx_at = now

    def _confirm_bridge_recovery_locked(self, fb, mode):
        if mode not in EXCLUSIVE_MODES and not (fb.calibrating or fb.servo_debugging):
            first = self._bridge_confirmation_seq
            if (first is None or fb.command_age_us >= FEEDBACK_HOLD_S * 1e6
                    or elapsed_us(fb.command_seq, first) > elapsed_us(self.seq, first)):
                # Verify command acceptance using the MCU's retained target.
                # This packet cannot enable torque or continue a queued action.
                hold = mode in ('hold', 'policy') and fb.enabled
                self._send_motion_locked(fb, HOLD if hold else DISARM, fb.targets)
                if first is None:
                    self._bridge_confirmation_seq = self.seq
                return False
        # The MCU ignores normal command sequences during calibration and setup.
        self.bridge_recovery = None
        self._bridge_confirmation_seq = None
        print('[bridge-recovery] Motion Bridge recovered', flush=True)
        return True

    def _log_stall_locked(self, event, now, reason):
        fb = self.feedback
        if fb is not None and self.servo_feedback_log:
            self.servo_feedback_log.record(event, fb, self.mode, now,
                self.diagnostics.get('servo_bus_voltage_mv'), self.diagnostic_time,
                servo_ids=[MOTOR_IDS[i] for i in self.stall.indices],
                alarm_ids=[MOTOR_IDS[i] for i, code in enumerate(fb.servo_errors or ()) if code],
                sustained_alarm_ids=[MOTOR_IDS[i] for i in self.stall.persistent_alarms],
                default_error_deg={MOTOR_IDS[i]: round(abs(goal - fb.angles[i]), 2)
                    for i, goal in enumerate(self.stall.home) if fb.mask & (1 << i)},
                outside_default_ids=[MOTOR_IDS[i] for i, goal in enumerate(self.stall.home)
                    if fb.mask & (1 << i) and abs(goal - fb.angles[i]) > self.stall.POSE_TOLERANCE_DEG],
                elapsed_ms=round((now - self.stall.started) * 1000),
                policy=self._last_policy_file, recovery_phase=self.recovery.phase,
                gravity=[float(v) for v in self.filtered['grav']] if self.filtered else None, reason=reason)

    def _disarm_stall_locked(self, now, reason):
        self._log_stall_locked('stall_disarmed', now, reason)
        self.stall.phase = 'disarmed'
        self.mode = 'off'
        self.generation += 1
        self.policy_needs_reset = True
        self._cancel_actions_locked()
        self.twist, self.head, self.mouth = [0.]*3, [0.]*4, 0.
        self._motion_needs_neutral.update(('web', 'gamepad'))
        self.input_source = 'web'
        self.last_error = STALL_DISARMED
        self._disarm_now()

    def _stall_activity_locked(self, now):
        if self.recovery.phase != 'walk':
            return 'getup'
        if self.kick_until and now < self.kick_until:
            return 'dynamic'
        if self._roulade_on(now) and self.action_preparation is None:
            return 'dynamic'
        if self.sitstand_phase == 'stand' and now < self.sitstand_until:
            return 'dynamic'
        if self.sitstand_phase == 'sit':
            return 'sit'
        if self.pick_active:
            return 'pick'
        if self.roller_mode:
            return 'roller'
        return 'walk'

    def _step_stall_locked(self, fb, filt, now, age):
        stall = self.stall
        if stall.phase == 'disarmed':
            return False
        if stall.phase != 'retreat' and not stall.qualified:
            return False
        mask_ok = bool(fb and (fb.mask == ALL_MASK if stall.phase != 'retreat' else not stall.invalid))
        valid = (self.mode == 'policy' and self.battery_phase == 'normal' and fb and fb.enabled
                 and fb.imu_ok and mask_ok and fb.servo_errors is not None
                 and age < StallRecovery.MAX_GAP_US / 1e6 and filt and filt['imu_fusion_ready']
                 and age + elapsed_us(fb.sample_us, fb.imu_us) / 1e6 <= IMU_STALE_S
                 and not self.fault_hold and not self.bridge_recovery
                 and not self._command_stalled_locked(now, FEEDBACK_HOLD_S))
        if (not valid or stall.invalid or (stall.phase == 'retreat' and
                self._command_tx_at is not None and now - self._command_tx_at > FEEDBACK_HOLD_S)):
            if self.mode == 'policy' and fb and fb.enabled and stall.qualified:
                # Other servos can still prove a stall even if one encoder is invalid.
                # Without a full measured pose there is no safe retreat anchor.
                stall.indices = stall.qualified
                if stall.started is None:
                    stall.started = now
                if not stall.home:
                    stall.home = tuple(self.policy.default_deg)
                self._disarm_stall_locked(now, 'confirmed stall without a valid retreat pose')
                return True
            if stall.phase == 'retreat':
                self._disarm_stall_locked(now, 'feedback, power or command link unavailable')
                return True
            stall.clear_candidates()
            return False
        if stall.phase == 'monitor':
            stall.indices = stall.qualified
            stall.started = now
            stall.home = tuple(self.policy.default_deg)
            self._disarm_stall_locked(now, 'stalled again after the default stance recovery')
            return True
        if stall.phase == 'idle':
            reason = stall.trigger_reason or 'sustained overcurrent without motion'
            stall.start(fb, self.policy.default_deg, now)
            self._log_stall_locked('stall_retreat', now, reason)
            self.generation += 1
            self._cancel_actions_locked()
            self.arm_pending = False
            self.twist, self.head, self.mouth = [0.]*3, [0.]*4, 0.
            self._motion_needs_neutral.update(('web', 'gamepad'))
            self.input_source = 'web'
            self.heading.reset()
            self.last_error = STALL_RETREAT
        if stall.persistent_alarms:
            other_fault = any(bits & ~PROTECTION_OVERCURRENT for bits in stall.persistent_alarms.values())
            reason = ('persistent servo fault during return to default stance' if other_fault else
                      'overcurrent without measured progress toward default stance')
            self._disarm_stall_locked(now, reason)
            return True
        if now >= stall.deadline:
            self._disarm_stall_locked(now, 'default stance or stable body attitude not confirmed before deadline')
            return True
        if stall.resume_phase and now >= stall.started + stall.duration:
            self.recovery.phase = stall.resume_phase
            self.recovery.invalidate()
            self.policy_needs_reset = True
            self.last_error = ''
            self._log_stall_locked('stall_released', now, 'default stance and stable body attitude confirmed')
            stall.finish_retreat()
            # Run the selected policy with fresh observations on the next tick.
        target = stall.target(now)
        self.last_action = target
        self._send_motion_locked(fb, HOLD, target)
        return True

    def _roller_transition_locked(self, phase, reason=None):
        fb = self.feedback
        if self.servo_feedback_log and fb:
            extra = {}
            if reason == 'protection':
                extra['pitch_deg'] = None if self.recovery.pitch_deg is None else round(self.recovery.pitch_deg, 2)
                extra['roll_deg'] = None if self.recovery.roll_deg is None else round(self.recovery.roll_deg, 2)
                extra['loop_missed'] = self.loop_missed
            self.servo_feedback_log.record('roller_pose', fb, self.mode, time.monotonic(),
                self.diagnostics.get('servo_bus_voltage_mv'), self.diagnostic_time,
                phase=phase, reason=reason, twist=list(self.twist),
                hips_deg=[round(fb.angles[i], 2) for i in ROLLER_HIPS], **extra)

    def _record_fall_blackbox_locked(self, event, fb, mode, now):
        p, r, a = self.recovery.pitch_deg, self.recovery.roll_deg, self.recovery.angle_deg
        if p is None or r is None:
            axis = 'unknown'
        elif abs(r) / Recovery.FALL_ROLL_DEG >= abs(p) / Recovery.FALL_DEG:
            axis = 'roll_right' if r > 0 else 'roll_left'
        else:
            axis = 'pitch_fwd' if p > 0 else 'pitch_bwd'
        loop_missed = getattr(self, 'loop_missed', 0)
        delta_missed = loop_missed - getattr(self, '_fall_last_loop_missed', 0)
        self._fall_last_loop_missed = loop_missed
        recent_infer = list(getattr(self, 'infer_ms', ()))[-25:]
        recent_rx = list(getattr(self, 'rx_intervals', ()))[-50:]
        angles = getattr(fb, 'angles', None) if fb is not None else None
        diagnostics = getattr(self, 'diagnostics', None) or {}
        voltage_mv = diagnostics.get('servo_bus_voltage_mv')
        fields = dict(
            fall_axis=axis,
            pitch_deg=None if p is None else round(p, 2),
            roll_deg=None if r is None else round(r, 2),
            angle_deg=None if a is None else round(a, 2),
            twist=[round(v, 3) for v in self.twist],
            roller_vx_cmd=round(getattr(self, '_roller_vx_cmd', 0.0), 3),
            hips_deg=[round(angles[i], 2) for i in ROLLER_HIPS] if angles is not None else None,
            neck_pitch_deg=round(angles[10], 2) if angles is not None and len(angles) > 10 else None,
            roller_mode=getattr(self, 'roller_mode', False),
            policy=getattr(self, '_last_policy_file', None),
            loop_missed=loop_missed,
            loop_missed_delta=delta_missed,
            infer_ms_recent_max=round(max(recent_infer), 2) if recent_infer else None,
            rx_ms_recent_max=round(max(recent_rx), 2) if recent_rx else None,
        )
        if event == 'fall_detected':
            self._last_fall_event = dict(fields, mode=mode, voltage_v=round((voltage_mv or 0) / 1000.0, 2))
            print(f'[fall_blackbox] axis={axis} pitch={fields["pitch_deg"]}° roll={fields["roll_deg"]}° '
                  f'twist={fields["twist"]} hips={fields["hips_deg"]} neck={fields["neck_pitch_deg"]}° '
                  f'V={self._last_fall_event["voltage_v"]} missed=+{delta_missed} '
                  f'infer_max={fields["infer_ms_recent_max"]}ms rx_max={fields["rx_ms_recent_max"]}ms', flush=True)
        servo_log = getattr(self, 'servo_feedback_log', None)
        if servo_log and fb:
            extra = {}
            frames = getattr(servo_log, 'frames', None)
            if event == 'fall_detected' and isinstance(frames, (collections.deque, list, tuple)) and frames:
                extra['frames'] = (tuple(frames), LEG_FEEDBACK_MASK | (1 << 10))
            servo_log.record(event, fb, mode, now, voltage_mv, getattr(self, 'diagnostic_time', 0.0), **fields, **extra)

    def _finish_roller_glide_locked(self, reason, *, keep_hold=False):
        if not self._roller_pose:
            return
        self._roller_pose = None
        self._roller_glide_result = reason
        self._roller_stride = None
        self.twist = [0.] * 3
        if reason in ('interrupted', 'protection'):
            self.policy_needs_reset = True
        self.generation += 1
        self._roller_transition_locked('idle', reason)

    def _check_roller_glide_locked(self, now):
        pose = self._roller_pose
        if not pose:
            return
        fb = self.feedback
        expired = pose.expired(now)
        if now - pose.seen >= MANUAL_INPUT_TIMEOUT_S:
            reason = 'input_timeout'
        elif expired:
            reason = expired
        elif (self.input_source != pose.owner
              or not self.roller_mode or self.mode != 'policy' or self.arm_pending
              or self.action_preparation or self.pick_active or self.kick_until
              or self.sitstand_phase != 'idle' or self._roulade_on(now)):
            reason = 'interrupted'
        elif (not fb or not fb.enabled or not fb.imu_ok or now - self.rx_time >= FEEDBACK_HOLD_S
              or now - self.rx_time + elapsed_us(fb.sample_us, fb.imu_us) / 1e6 > IMU_STALE_S
              or fb.mask & LEG_FEEDBACK_MASK != LEG_FEEDBACK_MASK or fb.servo_errors is None
              or any(code & ~PROTECTION_OVERCURRENT for code in fb.servo_errors)
              or self.fault_hold or self.bridge_recovery or self.battery_phase != 'normal'
              or self.stall.phase in ('retreat', 'disarmed') or self.recovery.phase != 'walk'
              or not self.filtered or not self.filtered['imu_fusion_ready']
              or self.recovery.angle_deg is None or self.recovery.fall_elapsed):
            reason = 'protection'
        else:
            pose.observe(fb, self.recovery.angle_deg <= Recovery.STAND_DEG, self.filtered['gyro_rad'], now)
            if pose.confirmed:
                self._finish_roller_glide_locked('stance_confirmed')
            return
        self._finish_roller_glide_locked(reason)

    def _roller_command_locked(self, t, roller_release, source, now):
        pose = self._roller_pose
        if not pose and roller_release:
            stride = self._roller_stride
            if (not self.roller_mode or not self.feedback or not stride or source != stride[0]
                    or self.twist[0] * stride[2][0] <= 0
                    or now - stride[1] >= FEEDBACK_HOLD_S or now - self.web_time >= MANUAL_INPUT_TIMEOUT_S):
                self.twist = [0.] * 3
                self._roller_stride = None
                self.generation += 1
                return
            self.twist = [0.] * 3
            self._roller_pose = pose = RollerStopPose(
                source, self.feedback, self.roller_policy.default_deg, now,
                forward_stride=stride[2][0] > 0)
            self._roller_glide_result = None
            self.generation += 1
            self._roller_transition_locked('gliding')
        if pose:
            if any(t):
                # Manual joystick/key input always regains control immediately.
                self._finish_roller_glide_locked('manual_input')
            elif source == pose.owner and roller_release:
                # Check the prior heartbeat and phase timeout before allowing renewal.
                self._check_roller_glide_locked(now)
                if self._roller_pose is pose:
                    pose.seen = now
                return
            else:
                self._finish_roller_glide_locked('stopped')
        self.twist = t
        if not t[0]:
            self._roller_stride = None

    def step(self):
        step_started = time.monotonic()
        with self.lock:
            now = time.monotonic()
            self._battery_step_locked(now)
            fb, filt, generation, mode = self.feedback, self.filtered, self.generation, self.mode
            age = now - self.rx_time
            if self._step_stall_locked(fb, filt, now, age):
                self._finish_roller_glide_locked('interrupted')
                return
            if mode in ('hold','policy'):
                if not fb or age >= FEEDBACK_HOLD_S:
                    self._pause_for_feedback_locked(age)
                    # In normal mode the MCU keeps the last target until it
                    # receives another one or its own IMU/Bridge guard trips.
                    return
                lost = None
                if fb.enabled and not self.bridge_recovery and self._command_stalled_locked(now, FEEDBACK_HOLD_S):
                    self._begin_bridge_recovery_locked('command', now)
                imu_age = age + elapsed_us(fb.sample_us, fb.imu_us) / 1e6
                imu_timed_out = imu_age > IMU_STALE_S
                if not fb.imu_ok or imu_timed_out:
                    lost = IMU_DISARM_REASON
                    if self.servo_feedback_log:
                        # The MCU also clears imu_ok on stale samples. Keep the flag and
                        # classify by age first so a known timeout remains distinguishable.
                        self.servo_feedback_log.record('imu_feedback_disarm', fb, mode, now,
                            self.diagnostics.get('servo_bus_voltage_mv'), self.diagnostic_time,
                            reason='imu_timeout' if imu_timed_out else 'imu_invalid',
                            imu_ok=fb.imu_ok, imu_age_ms=round(imu_age * 1000, 2),
                            feedback_age_ms=round(age * 1000, 2),
                            feedback_interval_ms=round(self.rx_intervals[-1], 2) if self.rx_intervals else None,
                            mcu_period_ms=fb.period_us / 1000)
                elif self.fault_hold:
                    if not fb.enabled:
                        lost = 'MCU released torque during the feedback pause'
                    elif not self.bridge_recovery:
                        if mode == 'policy' and not (filt and filt['imu_fusion_ready']):
                            return
                        self.fault_hold = False
                        if self.last_error in (FEEDBACK_HOLD_REASON, BRIDGE_RECOVERY_REASON):
                            self.last_error = ''
                        print(f'[motion] STM32 feedback restored; resuming {mode}', flush=True)
                if lost:
                    self.mode = mode = 'shadow'; self.generation += 1
                    self.arm_pending = False; self.last_error = lost
                    generation = self.generation
                    self.recovery.reset(); self.policy_needs_reset = True
                    self._cancel_actions_locked()
                elif time.monotonic()-self.web_time > MANUAL_INPUT_TIMEOUT_S:
                    # Browser inactivity stops travel without releasing torque.
                    self.twist = [0.0]*3
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
            if mode not in ('hold', 'policy'):
                self.fault_hold = False
            if self.bridge_recovery and fb and age < FEEDBACK_HOLD_S:
                if not self._confirm_bridge_recovery_locked(fb, mode) or self.fault_hold:
                    return
            valid = bool(fb and age < FEEDBACK_HOLD_S and fb.imu_ok and filt
                         and filt['imu_fusion_ready']
                         and age + elapsed_us(fb.sample_us, fb.imu_us)/1e6 <= IMU_STALE_S)
            if valid and self.last_error == IMU_DISARM_REASON:
                self.last_error = ''
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
                    self._record_fall_blackbox_locked('fall_detected', fb, mode, now)
                    self._cancel_actions_locked()
                    if self.battery_phase == 'warning':
                        self._battery_step_locked(now)
                        return
                elif changed and self.recovery.phase == 'walk':
                    self._record_fall_blackbox_locked('fall_recovered', fb, mode, now)
            phase = self.recovery.phase
            if phase == 'walk':
                self._advance_preparation_locked(now, valid)
            elif self.action_preparation:
                self._cancel_preparation_locked()
            self._check_roller_glide_locked(now)
            mode, generation = self.mode, self.generation
            drive_source = self.input_source
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
                if selected is self.roller_policy:
                    head = [0.0]*4
                    target_vx = twist[0]
                    if mode != 'policy' or target_vx == 0.0:
                        self._roller_vx_cmd = 0.0
                    else:
                        prev_vx = 0.0 if self.policy_needs_reset else getattr(self, '_roller_vx_cmd', 0.0)
                        if prev_vx * target_vx < 0.0:
                            prev_vx = 0.0
                        if abs(target_vx) <= abs(prev_vx):
                            self._roller_vx_cmd = target_vx
                        else:
                            max_step = ROLLER_VX_ACCEL_LIMIT * CONTROL_PERIOD
                            self._roller_vx_cmd = (min(target_vx, prev_vx + max_step) if target_vx > prev_vx
                                                   else max(target_vx, prev_vx - max_step))
                    twist[0] = self._roller_vx_cmd
                else:
                    self._roller_vx_cmd = 0.0
            if selected is not self.roller_policy:
                self._roller_vx_cmd = 0.0
            standing = selected is self.sitstand_policy and self.sitstand_phase == 'stand'
            drive_twist = tuple(twist)
            twist[2] = self.heading.command(valid and self._heading_motion_locked(now), twist)
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
        if inferring:
            if phase == 'getup':
                twist, head = [0.0]*3, [0.0]*4
            begin = time.monotonic()
            with self.policy_lock:
                acquired = time.monotonic()
                if reset_policy:
                    selected.reset_action_filter()
                observations = (filt['grav'], filt['gyro_rad'], filt['angles_deg'], filt['vels_deg'], twist, head)
                if standing:
                    action = selected.infer(*observations, action_alphas=SITSTAND_STAND_ALPHAS)
                    target = selected.action_to_deg(action, action_scale=SITSTAND_STAND_ACTION_SCALE)
                else:
                    action = selected.infer(*observations)
                    target = selected.action_to_deg(action)
            finish = time.monotonic()
            with self.lock:
                self.inferred += 1; self.infer_times.append(finish)
                self.infer_ms.append((finish-begin)*1000)
        with self.lock:
            self._check_roller_glide_locked(time.monotonic())
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
                if self._roller_pose and selected is self.roller_policy:
                    self._roller_pose.apply(target, time.monotonic())
                target[MOUTH_INDEX] = mouth
            if fb is None: return
            self.last_action = list(target)
            if inferring and command_mode == POLICY:
                self._last_policy_file = selected.file
            self._send_motion_locked(fb, command_mode, target)
            if (inferring and command_mode == POLICY and selected is self.roller_policy
                    and self._roller_pose is None):
                self._roller_stride = (drive_source, time.monotonic(), drive_twist)
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
        # Pin the 50 Hz balance control thread to core 0 (isolated from voice_process on cores 2..3).
        try:
            cpus = sorted(os.sched_getaffinity(0))
            if len(cpus) >= 2:
                os.sched_setaffinity(0, {cpus[0]})
        except (AttributeError, OSError):
            pass
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
                # Use spare budget to keep inactive ONNX sessions warm; throttle during active roller skating
                # so background dummy passes don't steal L1/L2 cache or push 50 Hz ticks over budget.
                self._warm_tick += 1
                roller_busy = self.roller_mode and self.mode == 'policy' and self.action_preparation is None
                need_warm = (not roller_busy and deadline - now > 0.008) or (
                    roller_busy and (self._warm_tick % 10 == 0) and deadline - now > 0.012
                )
                if need_warm:
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
                uptime_s=now-self.started, last_error=self.last_error, command=self.twist, input_source=self.input_source,
                fault_hold=self.fault_hold and self.mode in ('hold', 'policy'), fault_hold_supported=True,
                bridge_recovery=self.bridge_recovery,
                head=self.head, mouth=self.mouth, policy_targets=self.last_action, motors=MOTOR_IDS, boot_pd=self.boot_pd,
                full_robot_ready=bool(fb and fb.imu_ok and fb.mask==ALL_MASK and now-self.rx_time<.15),
                raw_pos=list(self.raw_pos), encoder_center=SERVO_CENTER_POS,
                cal_done=list(self.cal_done), cal_hold_pos=CAL_HOLD_POS, raw_mask=self.raw_mask)
            result['control_ready'] = bool(fb and fb.imu_ok and now-self.rx_time < FEEDBACK_HOLD_S
                                           and self.stall.phase != 'retreat'
                                           and not self.bridge_recovery and self.mode not in EXCLUSIVE_MODES)
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
            if self.sitting_calibration_retry is not None and self.mode == 'calibrate':
                result['sitting_calibration'] = dict(retry=self.sitting_calibration_retry,
                                                     max_retries=SITTING_CALIBRATION_RETRIES)
            result['heading_hold'] = self.heading.status()
            result['roller'] = dict(enabled=self.roller_mode, model=self.roller_policy.file,
                                    stop_pose_active=bool(self._roller_pose and self._roller_pose.phase != 'holding'),
                                    stop_phase=self._roller_pose.phase if self._roller_pose else 'idle',
                                    glide_result=self._roller_glide_result,
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
            voltage = self._battery_voltage(now)
            # The protection can stay latched after the measured voltage recovers.
            result['battery'] = dict(voltage_v=voltage / 1000 if voltage is not None else None,
                                     below_threshold=voltage is not None and voltage < LOW_BATTERY_MV,
                                     low=self.battery_phase != 'normal',
                                     phase=self.battery_phase)
            if self.diagnostics.get('imu_period_us'):
                result['mcu_runtime']['imu_hz'] = 1e6/self.diagnostics['imu_period_us']
            result['recovery'] = self.recovery.status()
            result['last_fall'] = getattr(self, '_last_fall_event', None)
            result['stall_recovery'] = dict(phase=self.stall.phase,
                servo_ids=[MOTOR_IDS[i] for i in self.stall.indices])
            if self.stall.phase == 'retreat':
                result['recovery']['phase'] = 'retreat'
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
            if self.mode in ('shadow','policy') and self.stall.phase != 'retreat':
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
                result['servo_alarms'] = {
                    str(MOTOR_IDS[index]): dict(code=code, active=bool(
                        now - self.rx_time < FEEDBACK_HOLD_S and fb.servo_errors and fb.servo_errors[index]))
                    for index, (code, seen) in self._servo_alarm_seen.items()
                    if now - seen < SERVO_ALARM_DISPLAY_S}
                motion_mask = fb.mask
                if motion_mask != ALL_MASK:
                    for seq, sample_us, received_at, mask in self._servo_feedback_history:
                        if (elapsed_us(fb.seq, seq) <= SERVO_FEEDBACK_GRACE_FRAMES
                                and elapsed_us(fb.sample_us, sample_us) <= SERVO_FEEDBACK_GRACE_US
                                and now <= received_at + SERVO_FEEDBACK_GRACE_US / 1e6):
                            motion_mask |= mask
                acc_corrected, gyro_corrected = self.filter.correct_imu(fb.acc, fb.gyro)
                result.update(servo_ids=[i for j,i in enumerate(MOTOR_IDS) if fb.mask & (1<<j)],
                    motion_servo_ids=[i for j,i in enumerate(MOTOR_IDS) if motion_mask & (1<<j)],
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
