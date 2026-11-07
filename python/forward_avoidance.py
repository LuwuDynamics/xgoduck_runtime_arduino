"""Cached forward-only ToF guard. No hardware I/O in policy/control calls."""
import copy
import json
import math
from pathlib import Path
import threading
import time

# Accept the former clearance and coverage settings in existing board files.
DEFAULTS = dict(enabled=True, stop_mm=100., slow_mm=150., clear_margin_mm=50.,
                max_age_ms=600., rows=[2, 6], columns=[2, 6], min_valid_zones=3)


def load_settings(path):
    try:
        values = json.loads(Path(path).read_text())
    except FileNotFoundError:
        values = {}
    return validate_settings(values)


def validate_settings(values):
    settings = copy.deepcopy(DEFAULTS)
    if not isinstance(values, dict) or set(values)-set(settings):
        raise ValueError('Unknown obstacle avoidance settings')
    settings.update(values)
    if type(settings['enabled']) is not bool:
        raise ValueError('enabled must be boolean')
    for key in ('stop_mm', 'slow_mm', 'clear_margin_mm', 'max_age_ms'):
        if isinstance(settings[key], bool) or not isinstance(settings[key], (int, float)) or not math.isfinite(settings[key]):
            raise ValueError('Avoidance distances and age must be finite numbers')
    if not (100 <= settings['stop_mm'] < settings['slow_mm'] <= 2000
            and 0 <= settings['clear_margin_mm'] <= 500 and 200 <= settings['max_age_ms'] <= 1000):
        raise ValueError('Obstacle avoidance settings are outside supported limits')
    for key in ('rows', 'columns'):
        v = settings[key]
        if not isinstance(v, list) or len(v) != 2 or any(type(n) is not int for n in v) or not 0 <= v[0] < v[1] <= 8:
            raise ValueError('ToF region must be an interval within the 8x8 grid')
    area = (settings['rows'][1]-settings['rows'][0])*(settings['columns'][1]-settings['columns'][0])
    if type(settings['min_valid_zones']) is not int or not 1 <= settings['min_valid_zones'] <= area:
        raise ValueError('Invalid minimum ToF zone coverage')
    return settings


class ForwardAvoidance:
    def __init__(self, monitor, *, settings=None, settings_path=None, clock=time.monotonic):
        self.monitor, self.clock = monitor, clock
        self.settings = copy.deepcopy(DEFAULTS)
        self.config_error = ''
        try:
            if settings_path is not None:
                self.settings = load_settings(settings_path)
            if settings is not None:
                self.settings = validate_settings(dict(self.settings, **settings))
        except (OSError, ValueError, TypeError) as exc:
            self.config_error = str(exc)
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.frame, self.received = {}, self.clock()
        self.distance, self.valid = None, 0
        self.speed = .2
        self.stop_count, self.last_stop = 0, None

    def _thresholds(self):
        # Give faster Roller travel an additional distance margin.
        extra = max(0., self.speed-.2)*750.
        return self.settings['stop_mm']+extra, self.settings['slow_mm']+extra

    def update(self, frame):
        now = self.clock()
        with self.lock:
            self.frame, self.received = dict(frame), now
            values = []
            grid = frame.get('grid_mm')
            if isinstance(grid, list) and len(grid) == 8 and all(isinstance(r, list) and len(r) == 8 for r in grid):
                for row in grid[self.settings['rows'][0]:self.settings['rows'][1]]:
                    for value in row[self.settings['columns'][0]:self.settings['columns'][1]]:
                        if not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
                            values.append(float(value))
            self.valid, self.distance = len(values), min(values) if values else None

    def _available(self, age):
        seq = self.frame.get('seq')
        return bool(not self.config_error and self.frame.get('state') == 'ranging'
                    and self.frame.get('fresh') is True and type(seq) is int and seq > 0
                    and not isinstance(age, bool) and isinstance(age, (int, float))
                    and math.isfinite(age) and 0 <= age <= self.settings['max_age_ms']
                    and self.distance is not None)

    def _snapshot(self, speed=None):
        if speed is not None and speed > 0:
            self.speed = float(speed)
        stop, slow = self._thresholds()
        age = self.frame.get('age_ms')
        age = (float(age)+(self.clock()-self.received)*1000
               if not isinstance(age, bool) and isinstance(age, (int, float)) and math.isfinite(age) else None)
        enabled = self.settings['enabled']
        available = self._available(age)
        reason, scale, state = '', 1., 'clear'
        sensor_state = self.frame.get('state')
        if not enabled:
            state = 'disabled'
        elif self.config_error:
            scale, state = 0., 'unavailable'
            reason = '避障配置无效：'+self.config_error
        elif sensor_state in ('absent', 'offline'):
            state = sensor_state
        elif not available:
            state = 'unavailable'
        elif self.distance <= stop:
            scale, state = 0., 'blocked'
            reason = f'前方约 {self.distance/10:.0f} 厘米有障碍，已停止前进并保持平衡。可以后退或原地转向。'
        elif self.distance < slow:
            scale, state = max(0., min(1., (self.distance-stop)/(slow-stop))), 'slowing'
        return dict(enabled=enabled, available=available, state=state, scale=scale,
                    forward_ready=scale > 0,
                    front_distance_mm=self.distance if available else None,
                    valid_front_zones=self.valid, age_ms=age, frame_seq=self.frame.get('seq'),
                    stop_distance_mm=stop, slow_distance_mm=slow, blocking_reason=reason,
                    roi_rows=list(self.settings['rows']), roi_columns=list(self.settings['columns']),
                    stop_count=self.stop_count, last_stop=dict(self.last_stop) if self.last_stop else None)

    def status(self):
        with self.lock:
            return self._snapshot()

    def preview(self, vx):
        with self.lock:
            return self._snapshot(vx)

    def limit(self, twist):
        values = list(twist) if twist is not None else [0., 0., 0.]
        if values[0] > 0:
            values[0] *= self.preview(values[0])['scale']
        return values

    def stopped(self, requested_vx, source, decision):
        with self.lock:
            self.stop_count += 1
            self.last_stop = dict(sequence=self.stop_count, requested_vx=requested_vx,
                source=source, reason=decision['blocking_reason'], distance_mm=decision['front_distance_mm'],
                time_monotonic=self.clock())
        print('[forward-avoidance]', self.last_stop['reason'], flush=True)

    def start(self):
        if not self.settings['enabled']:
            return
        def run():
            while not self.stop_event.is_set():
                try:
                    # status renews the monitor lease. Its worker alone calls
                    # the MCU at 5 Hz, even with all browser pages closed.
                    self.update(self.monitor.status())
                except Exception as exc:
                    self.update(dict(state='unavailable', fresh=False, error_message=str(exc)))
                self.stop_event.wait(.05)
        self.thread = threading.Thread(target=run, name='forward-tof', daemon=True)
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)


class GuardedWalkingPolicy:
    """Only walking/roller command input is guarded; model math stays unchanged."""
    def __init__(self, policy, guard):
        object.__setattr__(self, '_policy', policy)
        object.__setattr__(self, '_guard', guard)

    def __getattr__(self, name):
        return getattr(self._policy, name)

    def __setattr__(self, name, value):
        setattr(self._policy, name, value)

    def infer(self, grav, gyro_rad, angles_deg, vels_deg, cmd=None, head=None):
        return self._policy.infer(grav, gyro_rad, angles_deg, vels_deg,
                                  self._guard.limit(cmd), head)
