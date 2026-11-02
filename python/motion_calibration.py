"""Keep legacy software-center files without applying them to hardware centers.

The stable web/sketch calibrates each servo itself. A robot with nondefault
software zeros must complete that workflow before the App enables motion.
Optional per-robot IMU calibration adds a cached rotation and gyro-zero
subtraction before the unchanged receive-side filter.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading

from app_paths import DATA
from controller import Controller
from rl_core import MOTOR_IDS
from wire import SERVO_CENTER_POS
from imu_calibration import CalibratedSignalFilter, load as load_imu_calibration
from runtime_gains import RuntimeGainsReader
from forward_avoidance import GuardedWalkingPolicy


class AppController(Controller):
    def __init__(self, bridge, *, data_dir=None):
        super().__init__(bridge)
        self.run_gains = RuntimeGainsReader(self)
        self.forward_avoidance = None
        self._forward_release = set()
        self._imu_path = Path(DATA if data_dir is None else data_dir) / 'imu-calibration.json'
        self._imu_record, self._imu_error = None, ''
        try:
            self._imu_record = load_imu_calibration(self._imu_path)
            if self._imu_record is not None:
                self.filter = CalibratedSignalFilter(self._imu_record)
                print('[imu-calibration] Loaded saved gravity direction and gyro zero.', flush=True)
        except (OSError, ValueError, TypeError) as exc:
            self._imu_error = 'IMU 标定文件无效，未应用；请检查 data/imu-calibration.json。'
            print('[imu-calibration]', self._imu_error, type(exc).__name__, flush=True)
        self._center_lock = threading.Lock()
        self._center_path = Path(DATA if data_dir is None else data_dir) / 'hardware-centers.json'
        self._legacy_path = self._center_path.with_name('zero_pos.json')
        self._legacy_digest = None
        self._legacy_present = False
        self._needs_centering = False
        self._centered_ids = frozenset()
        try:
            raw = self._legacy_path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            self._legacy_present = self._needs_centering = True
            self._legacy_digest = 'unreadable'
        else:
            self._legacy_present = True
            self._legacy_digest = hashlib.sha256(raw).hexdigest()
            try:
                old = json.loads(raw)
                zeros = old['zero_pos']
                valid = (isinstance(zeros, list) and len(zeros) == len(MOTOR_IDS)
                         and all(type(v) is int and 0 <= v <= 4095 for v in zeros)
                         and old.get('ids', MOTOR_IDS) == MOTOR_IDS)
                self._needs_centering = not valid or any(v != SERVO_CENTER_POS for v in zeros)
            except (TypeError, ValueError, KeyError):
                self._needs_centering = True
        if self._needs_centering:
            try:
                marker = json.loads(self._center_path.read_text())
                ids = marker['centered_ids']
                if (marker.get('version') == 1 and marker.get('encoder_center') == SERVO_CENTER_POS
                        and marker.get('legacy_sha256') == self._legacy_digest
                        and isinstance(ids, list) and all(type(i) is int and i in MOTOR_IDS for i in ids)):
                    self._centered_ids = frozenset(ids)
                    self._needs_centering = self._centered_ids != frozenset(MOTOR_IDS)
            except (OSError, TypeError, ValueError, KeyError):
                pass

    def _center_message(self):
        remaining = [sid for sid in MOTOR_IDS if sid not in self._centered_ids]
        return ('旧版软件零位不能直接用于当前硬件中点协议。请先在 9527 校准页将各关节'
                '手动摆到机械中点，再逐个点击 Set center & hold；尚未完成的 ID：'
                + ', '.join(map(str, remaining)) + '。原零位文件已保留，不会自动套用。')

    def set_mode(self, mode):
        if mode in ('hold', 'policy') and self._imu_error:
            raise ValueError(self._imu_error)
        if mode in ('hold', 'policy') and self._needs_centering:
            raise ValueError(self._center_message())
        return super().set_mode(mode)

    def set_sitstand(self, action):
        if self._imu_error:
            raise ValueError(self._imu_error)
        if self._needs_centering:
            raise ValueError(self._center_message())
        return super().set_sitstand(action)

    def _remember_center(self, sid):
        centered = self._centered_ids | {sid}
        self._center_path.parent.mkdir(parents=True, exist_ok=True)
        marker = dict(version=1, encoder_center=SERVO_CENTER_POS,
                      legacy_sha256=self._legacy_digest, centered_ids=sorted(centered))
        fd, name = tempfile.mkstemp(prefix='.hardware-centers-', dir=self._center_path.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                json.dump(marker, output, indent=2)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self._center_path)
        finally:
            Path(name).unlink(missing_ok=True)
        # A failed write never marks the migration complete in memory.
        self._centered_ids = centered
        self._needs_centering = self._needs_centering and centered != frozenset(MOTOR_IDS)

    def cal_center(self, index):
        with self._center_lock:
            reply = super().cal_center(index)
            try:
                self._remember_center(reply['id'])
            except OSError as exc:
                raise ValueError('舵机中点指令已执行，但校准记录保存失败；请检查板端存储后重新校准。') from exc
            return reply

    def servo_op(self, action, target_id=None, *args, **kwargs):
        if action == 'read_runtime_gains':
            return self.run_gains.read(target_id)
        with self._center_lock:
            reply = super().servo_op(action, target_id, *args, **kwargs)
            sid = reply.get('target_id')
            if action == 'reset_mid' and reply.get('ok') and sid in MOTOR_IDS:
                try:
                    self._remember_center(sid)
                except OSError as exc:
                    raise ValueError('舵机中点指令已执行，但校准记录保存失败；请检查板端存储后重新校准。') from exc
            return reply

    def attach_forward_avoidance(self, guard):
        # Installed before the controller worker starts; no live model reload.
        if self.forward_avoidance is not None:
            raise ValueError('Forward avoidance is already attached')
        self.forward_avoidance = guard
        self.walk_policy = GuardedWalkingPolicy(self.walk_policy, guard)
        self.roller_policy = GuardedWalkingPolicy(self.roller_policy, guard)
        self.policy = self.roller_policy if self.roller_mode else self.walk_policy

    def _stop_forward_locked(self, decision):
        self.forward_avoidance.stopped(self.twist[0], self.input_source, decision)
        self._forward_release.add(self.input_source)
        self.twist[0] = 0.
        self.generation += 1  # Drop an obsolete positive action still in ONNX.

    def command(self, twist=None, head=None, mouth=None, source='web'):
        with self.lock:
            result = super().command(twist, head, mouth, source)
            if 'ignored' in result or twist is None or self.forward_avoidance is None:
                return result
            # A held joystick/key must return to neutral before forward travel
            # can resume. Voice cancellation sends zero and retires its job.
            if float(twist[0]) <= 0:
                self._forward_release.discard(source)
            elif self.mode == 'policy' and self.twist[0] > 0:
                decision = self.forward_avoidance.preview(self.twist[0])
                if source in self._forward_release:
                    self.twist[0] = 0.
                    return dict(result, forward_blocked=True,
                                reason='前进已停止，请先松开方向键/摇杆，再重新发出前进指令。')
                if not decision['forward_ready']:
                    self._stop_forward_locked(decision)
                    return dict(result, forward_blocked=True, reason=decision['blocking_reason'])
            return result

    def step(self):
        with self.lock:
            guard = self.forward_avoidance
            if guard is not None and self.mode == 'policy' and self.twist[0] > 0:
                decision = guard.preview(self.twist[0])
                if not decision['forward_ready']:
                    self._stop_forward_locked(decision)
        # Do not hold the controller lock across ONNX or invert policy_lock.
        return super().step()

    def status(self):
        result = super().status()
        result['runtime_gains'] = self.run_gains.status()
        if self.forward_avoidance is not None:
            result['obstacle_avoidance'] = self.forward_avoidance.status()
            with self.lock:
                result['obstacle_avoidance']['awaiting_new_command_sources'] = sorted(self._forward_release)
        record = self._imu_record or {}
        result['gravity_calibration'] = dict(
            state='invalid' if self._imu_error else 'loaded' if self._imu_record else 'not_calibrated',
            file=str(self._imu_path), rotation_deg=record.get('rotation_deg'),
            gyro_zero_dps=record.get('gyro_zero_dps'), calibrated_at=record.get('calibrated_at'),
            measurement=record.get('measurement'), error=self._imu_error)
        if self._imu_record:
            result['deployment']['imu_bias_deg'] = list(record['rotation_deg'])
            result['deployment']['gyro_zero_dps'] = list(record['gyro_zero_dps'])
            if result.get('gyro'):
                result['gravity_calibration']['corrected_gyro_dps'] = self.filter.rotate(
                    [g-z for g, z in zip(result['gyro'], self.filter.zero)])
        if self._imu_error:
            result['last_error'] = self._imu_error
        result['calibration'] = dict(mode='hardware_center', encoder_center=SERVO_CENTER_POS,
                                    legacy_software_file=self._legacy_present,
                                    requires_centering=self._needs_centering,
                                    centered_ids=sorted(self._centered_ids))
        if self._needs_centering and not result['last_error']:
            result['last_error'] = self._center_message()
        return result
