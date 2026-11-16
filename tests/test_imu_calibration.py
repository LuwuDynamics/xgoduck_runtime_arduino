"""Upstream standing offsets through the AI App's persistent calibration path."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))

from motion_calibration import AppController
from standing_imu import ImuCorrection, disable, estimate, load, save
from wire import ALL_MASK, COMMAND, DISARM, Feedback


def samples():
    return [(i * .01, [1., 2., -9.], [.8, -.5, 1.2]) for i in range(151)]


class ImuCalibrationTest(unittest.TestCase):
    def test_stationary_offsets_align_gravity_and_keep_relative_motion(self):
        record = estimate(samples())
        self.assertEqual(record['version'], 2)
        self.assertEqual(record['measurement']['samples'], 151)
        correction = ImuCorrection(record)
        acc, gyro = correction.apply([2., 3., -8.], [2.8, -.5, 1.2])
        for actual, expected in zip(acc + gyro, [1., 1., -8.8, 2., 0., 0.]):
            self.assertAlmostEqual(actual, expected)

    def test_motion_interruption_and_wrong_orientation_are_rejected(self):
        rows = samples()
        with self.assertRaisesRegex(ValueError, 'Not enough'):
            estimate(rows[:89])
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            estimate(rows[:70] + rows[90:])
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            estimate(rows[:70] + [rows[69]] + rows[70:])
        moving = list(rows)
        moving[60] = (moving[60][0], [1., 2., -11.], moving[60][2])
        with self.assertRaisesRegex(ValueError, 'Movement'):
            estimate(moving)
        with self.assertRaisesRegex(ValueError, 'upright'):
            estimate([(t, [9.8, 0., 0.], g) for t, _, g in rows])

    def test_stationary_customer_gyro_offset_is_saved_and_loaded(self):
        bias = [1.9, 14., 7.3]
        rows = [(stamp, acc, [value + (.25 if i % 2 else -.25) for value in bias])
                for i, (stamp, acc, _) in enumerate(samples()[:150])]
        rows.append((1.5, rows[-1][1], bias))
        record = estimate(rows)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'imu-calibration.json'
            save(path, record)
            correction = ImuCorrection(load(path))
            _, gyro = correction.apply(rows[-1][1], bias)
            for value in gyro:
                self.assertAlmostEqual(value, 0.)
        moving = list(rows)
        stamp, acc, gyro = moving[75]
        moving[75] = (stamp, acc, [gyro[0], gyro[1] + 5., gyro[2]])
        with self.assertRaisesRegex(ValueError, 'Movement detected'):
            estimate(moving)
        with self.assertRaisesRegex(ValueError, 'zero offset is too large'):
            estimate([(stamp, acc, [1.9, 25., 7.3]) for stamp, acc, _ in rows])

    def test_save_preserves_previous_and_reset_disables_calibration(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'imu-calibration.json'
            first = estimate(samples())
            second = estimate([(t, [0., 1., -9.], g) for t, _, g in samples()])
            save(file, first)
            save(file, second)
            self.assertEqual(load(file), second)
            self.assertEqual(load(file.with_suffix('.previous.json')), first)
            disable(file)
            self.assertIsNone(load(file))
            self.assertEqual(load(file.with_suffix('.disabled.json')), second)

    def test_startup_reloads_offset_instead_of_reapplying_it_to_the_saved_record(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'imu-calibration.json'
            save(file, estimate(samples()))
            before = file.read_bytes()
            for _ in range(2):
                app = AppController(Mock(), data_dir=directory)
                self.assertTrue(app.status()['imu_calibration']['calibrated'])
                acc, gyro = app.filter.correct_imu(samples()[0][1], samples()[0][2])
                for actual, expected in zip(acc + gyro, [0., 0., -9.8, 0., 0., 0.]):
                    self.assertAlmostEqual(actual, expected)
                self.assertEqual(file.read_bytes(), before)

    def test_corrupt_and_old_records_block_arming_until_calibration_or_reset(self):
        for content in ('{broken', json.dumps({'version': 1, 'rotation_deg': [1., 2., 0.]})):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                file = Path(directory) / 'imu-calibration.json'
                file.write_text(content)
                app = AppController(Mock(), data_dir=directory)
                self.assertEqual(app.status()['imu_calibration']['state'], 'error')
                for mode in ('hold', 'policy'):
                    with self.assertRaisesRegex(ValueError, 'IMU 标定文件无效'):
                        app.set_mode(mode)
                with self.assertRaisesRegex(ValueError, 'IMU 标定文件无效'):
                    app.set_sitstand('sit')
                self.assertEqual(file.read_text(), content)
                app.reset_imu_calibration()
                self.assertEqual(file.with_suffix('.disabled.json').read_text(), content)
                self.assertEqual(app.status()['imu_calibration']['state'], 'idle')

    def test_web_sampling_replaces_legacy_record_without_a_restart_or_arming(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'imu-calibration.json'
            legacy = '{"version": 1}'
            file.write_text(legacy)
            app = AppController(Mock(), data_dir=directory)
            app.set_mode('calibrate')
            rows = iter(samples())
            self.supply_feedback(app, samples()[0])
            with patch('controller.threading.Thread') as worker:
                self.assertTrue(app.start_imu_calibration()['active'])
                with self.assertRaisesRegex(ValueError, 'calibration is in progress'):
                    app.cal_center(0)
            job = worker.call_args.kwargs

            def next_sample(_interval):
                self.supply_feedback(app, next(rows))
                return False

            with patch.object(app.stop_event, 'wait', side_effect=next_sample):
                job['target'](*job['args'])
            self.assertEqual(app.status()['imu_calibration']['state'], 'saved')
            self.assertEqual(app.filter.correct_imu([1., 2., -9.], [.8, -.5, 1.2])[0],
                             [0., 0., -9.8])
            self.assertEqual(file.with_suffix('.previous.json').read_text(), legacy)
            self.assertEqual(load(file)['version'], 2)
            self.assertEqual(app.mode, 'calibrate')
            commands = [COMMAND.unpack(call.args[1])[4] for call in app.bridge.notify.call_args_list
                        if call.args[0] == 'duck_command']
            self.assertTrue(commands)
            self.assertEqual(set(commands), {DISARM})

    def test_exiting_calibration_prevents_a_pending_sample_from_saving(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'imu-calibration.json'
            save(file, estimate(samples()))
            before = file.read_bytes()
            app = AppController(Mock(), data_dir=directory)
            app.set_mode('calibrate')
            self.supply_feedback(app, samples()[0])
            with patch('controller.threading.Thread') as worker:
                app.start_imu_calibration()
            app.set_mode('shadow')
            job = worker.call_args.kwargs
            job['target'](*job['args'])
            self.assertEqual(app.status()['imu_calibration']['state'], 'cancelled')
            self.assertEqual(file.read_bytes(), before)

    @staticmethod
    def supply_feedback(app, row):
        stamp, acc, gyro = row
        app.feedback = Feedback(1, ALL_MASK, 1, 0, 0, 0, 0, 10000, 0, 0, 0, 0,
                                acc, gyro, [0.] * 15, [0.] * 15, [0.] * 15)
        app._imu_source_time = stamp
        app.rx_time = time.monotonic()


if __name__ == '__main__':
    unittest.main()
