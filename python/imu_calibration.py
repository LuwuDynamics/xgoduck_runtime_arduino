"""Per-robot static IMU zero, applied before the unchanged upstream filter."""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import tempfile

from proc import SignalFilter

FRAME = 'qmi8658-policy-body'


def vector(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 3
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in value)):
        raise ValueError('IMU calibration requires three finite numeric values')
    return [float(v) for v in value]


def validate(record):
    if (not isinstance(record, dict) or type(record.get('version')) is not int
            or record['version'] != 1 or record.get('frame') != FRAME):
        raise ValueError('Unsupported IMU calibration version or coordinate frame')
    angles = vector(record.get('rotation_deg'))
    zero = vector(record.get('gyro_zero_dps'))
    if max(abs(v) for v in angles[:2]) > 45 or angles[2] != 0:
        raise ValueError('Gravity calibration allows roll/pitch within 45 degrees and no yaw')
    if max(abs(v) for v in zero) > 30:
        raise ValueError('Gyro zero exceeds 30 degrees/second; inspect the IMU')
    return record


def load(path):
    try:
        raw = Path(path).read_text()
    except FileNotFoundError:
        return None
    return validate(json.loads(raw))


def estimate(samples, *, device=''):
    """Only a user-confirmed level, stationary Default Pose is a valid reference.

    Use raw acceleration rather than already fused gravity: gyro zero can bias
    the fused vector. A single pose cannot determine yaw or full accelerometer
    scale/bias, so those are deliberately not fitted here.
    """
    rows, seen = [], set()
    for sample in samples:
        if (sample.get('mode') != 'hold' or sample.get('enabled') is not True
                or sample.get('imu_ok') is not True
                # Upstream status samples its clock before taking the receive
                # lock; a concurrent new frame can give a few negative ms.
                or not -20 <= sample.get('feedback_age_ms', math.inf) < 150):
            raise ValueError('Keep Default Pose enabled with fresh, valid IMU feedback')
        runtime = sample.get('mcu_runtime') or {}
        seq, stamp = runtime.get('imu_seq'), runtime.get('imu_us')
        if type(seq) is not int or type(stamp) is not int:
            raise ValueError('MCU IMU source timestamps are required')
        if seq in seen:
            continue
        seen.add(seq)
        rows.append((stamp, vector(sample.get('acc')), vector(sample.get('gyro'))))
    if len(rows) < 100:
        raise ValueError('Need at least 100 distinct IMU samples')
    gaps = [((b[0] - a[0]) & 0xffffffff) / 1e6 for a, b in zip(rows, rows[1:])]
    duration = sum(gaps)
    if duration < 15 or max(gaps) > .5 or min(gaps) <= 0:
        raise ValueError('Need at least 15 seconds of continuous stationary sampling')
    mean_acc = [statistics.mean(row[1][i] for row in rows) for i in range(3)]
    mean_gyro = [statistics.mean(row[2][i] for row in rows) for i in range(3)]
    acc_std = [statistics.pstdev(row[1][i] for row in rows) for i in range(3)]
    gyro_std = [statistics.pstdev(row[2][i] for row in rows) for i in range(3)]
    norm = math.sqrt(sum(v*v for v in mean_acc))
    if not 7 <= norm <= 12:
        raise ValueError('Acceleration is not near one g; inspect placement and the IMU')
    if math.sqrt(sum(v*v for v in acc_std)) > .18 or max(gyro_std) > 1:
        raise ValueError('Robot moved during sampling; keep it still and sample again')
    x, y, z = mean_acc
    rotation = [math.degrees(math.atan2(-y, -z)),
                math.degrees(math.atan2(x, math.hypot(y, z))), 0.]
    return validate(dict(version=1, frame=FRAME, device=device,
        calibrated_at=datetime.now(timezone.utc).isoformat(), rotation_deg=rotation,
        gyro_zero_dps=mean_gyro, measurement=dict(samples=len(rows), duration_s=duration,
            mean_acc_ms2=mean_acc, acc_std_ms2=acc_std, gyro_std_dps=gyro_std,
            acceleration_norm_ms2=norm, reference='level stationary Default Pose')))


def save(path, record):
    """Publish a complete file atomically; never overwrite an earlier calibration."""
    validate(record)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.imu-calibration-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(record, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.link(name, path)  # Atomic no-clobber publication, including concurrent writers.
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


class CalibratedSignalFilter(SignalFilter):
    """Cached rotation and gyro zero; no file access in the 100 Hz receive loop."""
    def __init__(self, record):
        validate(record)
        r, p, _ = [math.radians(v) for v in record['rotation_deg']]
        cr, sr, cp, sp = math.cos(r), math.sin(r), math.cos(p), math.sin(p)
        self.rotation = ((cp, sp*sr, sp*cr), (0., cr, -sr), (-sp, cp*sr, cp*cr))
        self.zero = tuple(record['gyro_zero_dps'])
        super().__init__()

    def rotate(self, values):
        return [sum(a*b for a, b in zip(row, values)) for row in self.rotation]

    def update(self, acc_ms2, gyro_dps, angles_deg, vels_deg, **timing):
        return super().update(self.rotate(acc_ms2),
            self.rotate([g-z for g, z in zip(gyro_dps, self.zero)]),
            angles_deg, vels_deg, **timing)
