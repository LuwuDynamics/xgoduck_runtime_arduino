"""Standing reference: fixed additive accelerometer and gyro offsets."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile

CALIBRATION_PATH = Path(__file__).resolve().parents[1] / '.cache' / 'imu-calibration.json'
SAMPLE_SECONDS = 1.5
MIN_SAMPLES = 90
REFERENCE_G = 9.8
FRAME = 'qmi-policy-body-offset-v2'
# Sanity bound only: the offset is subtracted, the QMI8658 range here is +-1024 dps, and field boards
# show 12-30 dps (datasheet initial tolerance is +-10). Stability is judged by MOTION_LIMITS.
MAX_GYRO_ZERO_DPS = 100.0
GYRO_ZERO_ERROR = 'Gyroscope zero offset is too large. Check the IMU and try again.'
# Gyro limits only reject real board motion. The walk policy tolerates far more gyro noise than
# this (simulation: no falls up to 0.8 rad/s white noise), and the calibration mean error is
# std/sqrt(n) (~0.1 deg/s), so noisy-but-stationary QMI8658 units must still pass.
MOTION_LIMITS = dict(acc_std_ms2=.30, gyro_std_dps=3.0, acc_peak_ms2=1.0, gyro_peak_dps=10.0)


class ImuMovementError(ValueError):
    def __init__(self, metrics, exceeded):
        super().__init__('Movement detected. Hold the board still and try again.')
        self.metrics = metrics
        self.exceeded = exceeded


def vector(values):
    if not isinstance(values, (list, tuple)) or len(values) != 3:
        raise ValueError('IMU vector must have three values')
    result = [float(v) for v in values]
    if not all(math.isfinite(v) for v in result):
        raise ValueError('IMU contains nonfinite values')
    return result


def check_standing(acc):
    x, y, z = vector(acc)
    norm = math.sqrt(x*x + y*y + z*z)
    if not 7.0 <= norm <= 12.5:
        raise ValueError('Unexpected acceleration. Keep the board still and try again.')
    if -z / norm < math.cos(math.radians(45)):
        raise ValueError('Hold the board upright with the battery connector pointing up.')
    return norm


class ImuCorrection:
    def __init__(self, record):
        if (not isinstance(record, dict) or type(record.get('version')) is not int
                or record.get('version') != 2 or record.get('frame') != FRAME):
            raise ValueError('This calibration is from an older version. Calibrate again.')
        self.offset = vector(record['acc_offset_ms2'])
        if max(abs(v) for v in self.offset) > 15:
            raise ValueError('Invalid accelerometer offset')
        self.zero = vector(record['gyro_zero_dps'])
        if max(abs(v) for v in self.zero) > MAX_GYRO_ZERO_DPS:
            raise ValueError(GYRO_ZERO_ERROR)

    def apply(self, acc, gyro):
        return ([v+offset for v, offset in zip(acc, self.offset)],
                [g-z for g, z in zip(gyro, self.zero)])


def estimate(rows):
    """Rows contain unique MCU source time, raw body acceleration and gyro."""
    if len(rows) < MIN_SAMPLES or rows[-1][0]-rows[0][0] < SAMPLE_SECONDS-.02:
        raise ValueError('Not enough IMU readings. Keep the board still and try again.')
    if any(not 0 < b[0]-a[0] <= .1 for a, b in zip(rows, rows[1:])):
        raise ValueError('IMU readings were interrupted. Try again.')
    acc = [vector(row[1]) for row in rows]
    gyro = [vector(row[2]) for row in rows]
    n = len(rows)
    mean_acc = [math.fsum(row[i] for row in acc)/n for i in range(3)]
    mean_gyro = [math.fsum(row[i] for row in gyro)/n for i in range(3)]
    acc_std = [math.sqrt(math.fsum((row[i]-mean_acc[i])**2 for row in acc)/n) for i in range(3)]
    gyro_std = [math.sqrt(math.fsum((row[i]-mean_gyro[i])**2 for row in gyro)/n) for i in range(3)]
    check_standing(mean_acc)
    reference = [0., 0., -REFERENCE_G]
    record = dict(version=2, frame=FRAME, method='offset',
                  calibrated_at=datetime.now(timezone.utc).isoformat(),
                  acc_offset_ms2=[target-measured for target, measured in zip(reference, mean_acc)],
                  gyro_zero_dps=mean_gyro, reference_acc_ms2=reference,
                  measurement=dict(samples=n, duration_s=rows[-1][0]-rows[0][0],
                                   mean_acc_ms2=mean_acc, mean_gyro_dps=mean_gyro,
                                   acc_std_ms2=acc_std, gyro_std_dps=gyro_std))
    metrics = dict(acc_std_ms2=math.hypot(*acc_std), gyro_std_dps=math.hypot(*gyro_std),
                   acc_peak_ms2=max(math.dist(row, mean_acc) for row in acc),
                   gyro_peak_dps=max(math.dist(row, mean_gyro) for row in gyro))
    exceeded = [name for name, value in metrics.items() if value > MOTION_LIMITS[name]]
    if exceeded:
        raise ImuMovementError(metrics, exceeded)
    ImuCorrection(record)  # Report real movement before an out-of-range offset.
    record['measurement'].update(
        acc_axis_peak_ms2=[max(abs(row[i]-mean_acc[i]) for row in acc) for i in range(3)],
        gyro_axis_peak_dps=[max(abs(row[i]-mean_gyro[i]) for row in gyro) for i in range(3)],
        acc_peak_ms2=metrics['acc_peak_ms2'], gyro_peak_dps=metrics['gyro_peak_dps'])
    return record


def load(path):
    try:
        record = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return None
    ImuCorrection(record)
    return record


def _atomic_write(path, data):
    fd, name = tempfile.mkstemp(prefix='.imu-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def save(path, record):
    ImuCorrection(record)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        previous = path.read_bytes()
    except FileNotFoundError:
        previous = None
    if previous is not None:
        _atomic_write(path.with_suffix('.previous.json'), previous)
    _atomic_write(path, (json.dumps(record, ensure_ascii=False, indent=2,
                                  allow_nan=False)+'\n').encode('utf-8'))


def disable(path):
    path = Path(path)
    if path.exists():
        os.replace(path, path.with_suffix('.disabled.json'))
