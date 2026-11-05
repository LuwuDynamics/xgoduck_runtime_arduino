"""Two-second standing reference: fixed additive accelerometer and gyro offsets."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile

CALIBRATION_PATH = Path(__file__).resolve().parents[1] / '.cache' / 'imu-calibration.json'
SAMPLE_SECONDS = 2.0
MIN_SAMPLES = 120
REFERENCE_G = 9.8
FRAME = 'qmi-policy-body-offset-v2'


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
        if max(abs(v) for v in self.zero) > 8:
            raise ValueError('Invalid stationary gyro zero')

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
    acc_peak = max(math.dist(row, mean_acc) for row in acc)
    gyro_peak = max(math.dist(row, mean_gyro) for row in gyro)
    if (math.hypot(*acc_std) > .30 or math.hypot(*gyro_std) > .75
            or acc_peak > 1.0 or gyro_peak > 3 or max(map(abs, mean_gyro)) > 8):
        raise ValueError('Movement detected. Hold the board still and try again.')
    check_standing(mean_acc)
    reference = [0., 0., -REFERENCE_G]
    record = dict(version=2, frame=FRAME, method='offset',
                  calibrated_at=datetime.now(timezone.utc).isoformat(),
                  acc_offset_ms2=[target-measured for target, measured in zip(reference, mean_acc)],
                  gyro_zero_dps=mean_gyro, reference_acc_ms2=reference,
                  measurement=dict(samples=n, duration_s=rows[-1][0]-rows[0][0],
                                   mean_acc_ms2=mean_acc, mean_gyro_dps=mean_gyro,
                                   acc_std_ms2=acc_std, gyro_std_dps=gyro_std))
    ImuCorrection(record)
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
