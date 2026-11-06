"""Static per-device IMU correction, persistence and real filter integration."""
import copy
import math
from pathlib import Path
import sys
import tempfile
from threading import RLock
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from imu_calibration import CalibratedSignalFilter, estimate, load, save, validate
from motion_calibration import AppController


def samples():
    return [dict(mode='hold', enabled=True, imu_ok=True, feedback_age_ms=5.,
                 mcu_runtime=dict(imu_seq=i+1, imu_us=(0xffff0000+i*100000) & 0xffffffff),
                 acc=[-2.6, -.1, -8.35], gyro=[1.1, -11., 17.3]) for i in range(201)]


class ImuCalibrationTest(unittest.TestCase):
    def test_measured_level_vector_and_gyro_zero_feed_real_filter(self):
        record = estimate(samples(), device='test-board')
        filt = CalibratedSignalFilter(record)
        for i in range(100):
            result = filt.update([-2.6, -.1, -8.35], [1.1, -11., 17.3], [0.]*15,
                                 [0.]*15, timestamp=i*.01, imu_timestamp=i*.01)
        for actual, expected in zip(result['grav'], [0., 0., -1.]):
            self.assertAlmostEqual(actual, expected, places=10)
        self.assertEqual(result['gyro_rad'], [0.]*3)
        self.assertAlmostEqual(record['measurement']['duration_s'], 20.)

    def test_rotation_preserves_magnitude_and_real_motion_after_zero_subtraction(self):
        filt = CalibratedSignalFilter(estimate(samples()))
        v = [2., -3., 4.]
        out = filt.rotate(v)
        self.assertAlmostEqual(sum(x*x for x in out), sum(x*x for x in v))
        result = filt.update([-2.6, -.1, -8.35], [3.1, -14., 21.3], [0.]*15,
                             [0.]*15, timestamp=1., imu_timestamp=1.)
        for actual, expected in zip(result['gyro_rad'], out):
            self.assertAlmostEqual(actual, math.radians(expected))

    def test_reset_and_filter_tuning_preserve_calibration_without_file_reads(self):
        filt = CalibratedSignalFilter(estimate(samples()))
        with patch.object(Path, 'read_text', side_effect=AssertionError('Receive loop read a file')):
            filt.set_imu_filters(gyro_alpha=.4, gravity_tau_s=.5)
            filt.reset()
            out = filt.update([-2.6, -.1, -8.35], [1.1, -11., 17.3], [0.]*15,
                              [0.]*15, timestamp=1., imu_timestamp=1.)
        self.assertAlmostEqual(out['grav'][2], -1.)
        self.assertEqual(out['gyro_rad'], [0.]*3)

    def test_unsafe_pose_stale_feedback_and_movement_are_rejected(self):
        for mutation in ['mode', 'enabled', 'stale', 'moving', 'upside_down', 'nan']:
            with self.subTest(mutation=mutation):
                rows = samples()
                if mutation == 'mode': rows[3]['mode'] = 'policy'
                if mutation == 'enabled': rows[3]['enabled'] = False
                if mutation == 'stale': rows[3]['feedback_age_ms'] = 151
                if mutation == 'moving':
                    for i, row in enumerate(rows): row['gyro'][0] = (-1)**i*10.
                if mutation == 'upside_down':
                    for row in rows: row['acc'][2] = 8.35
                if mutation == 'nan': rows[3]['gyro'][0] = float('nan')
                with self.assertRaises(ValueError): estimate(rows)

    def test_duplicate_frames_and_source_gaps_cannot_pass_stationary_sampling(self):
        for rows in [samples()[:99], [samples()[0]]*201,
                     [samples()[0]] + samples()[80:]]:
            with self.assertRaises(ValueError): estimate(rows)

    def test_small_upstream_status_clock_overlap_is_not_stale(self):
        rows = samples()
        rows[1]['feedback_age_ms'] = -2.
        self.assertEqual(estimate(rows)['measurement']['samples'], 201)

    def test_atomic_save_load_and_existing_file_is_never_overwritten(self):
        record = estimate(samples())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'imu-calibration.json'
            self.assertIsNone(load(path))
            save(path, record)
            before = path.read_bytes()
            self.assertEqual(load(path), record)
            with self.assertRaises(FileExistsError): save(path, record)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.glob('.imu-calibration-*')), [])

    def test_corrupt_versions_frames_and_nonfinite_values_are_rejected(self):
        record = estimate(samples())
        for key, value in [('version', True), ('frame', 'different-axes'),
                           ('gyro_zero_dps', [0, 0, float('inf')]),
                           ('rotation_deg', [0, 0, 5])]:
            bad = copy.deepcopy(record)
            bad[key] = value
            with self.assertRaises(ValueError): validate(bad)

    def test_app_restarts_load_same_record_without_accumulating_correction(self):
        record = estimate(samples())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'imu-calibration.json'
            save(path, record)
            before = path.read_bytes()
            for _ in range(2):
                with patch('motion_calibration.Controller.__init__', return_value=None):
                    app = AppController(Mock(), data_dir=directory)
                out = app.filter.update([-2.6, -.1, -8.35], [1.1, -11., 17.3], [0.]*15,
                                        [0.]*15, timestamp=1., imu_timestamp=1.)
                self.assertAlmostEqual(out['grav'][2], -1.)
                self.assertEqual(app._imu_record, record)
                self.assertEqual(path.read_bytes(), before)

    def test_corrupt_saved_file_blocks_arming_but_app_initialization_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / 'imu-calibration.json').write_text('{broken')
            with patch('motion_calibration.Controller.__init__', return_value=None):
                app = AppController(Mock(), data_dir=directory)
            app.lock = RLock()
            for mode in ['hold', 'policy']:
                with self.assertRaisesRegex(ValueError, 'IMU 标定文件无效'): app.set_mode(mode)
            with self.assertRaisesRegex(ValueError, 'IMU 标定文件无效'): app.set_sitstand('sit')


if __name__ == '__main__':
    unittest.main()
