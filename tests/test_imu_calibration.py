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

    def test_peaks_measure_deviations_in_individual_frames(self):
        rows = samples()
        for i, sign in enumerate((1, -1)):
            rows[i] = (rows[i][0], [1. + sign * .25, 2., -9.], [.8, -.5 + sign * 2., 1.2])
            rows[i+2] = (rows[i+2][0], [1., 2. + sign * .5, -9.], [.8, -.5, 1.2 + sign * 3.])
        m = estimate(rows)['measurement']
        self.assertEqual(m['acc_axis_peak_ms2'], [.25, .5, 0.])
        self.assertEqual(m['acc_peak_ms2'], .5)
        self.assertEqual(m['gyro_axis_peak_dps'], [0., 2., 3.])
        self.assertEqual(m['gyro_peak_dps'], 3.)

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

    def test_noisy_but_stationary_gyro_axis_is_accepted(self):
        # Reported board: stationary, gyro X std 1.43 deg/s, other axes about 0.12 deg/s.
        import random
        rng = random.Random(7)
        bias = [-1.7852, -12.6111, 9.1111]
        sd = [1.4299, .1142, .1472]
        rows = [(stamp, acc, [b + rng.gauss(0, s) for b, s in zip(bias, sd)])
                for stamp, acc, _ in samples()[:151]]
        record = estimate(rows)
        for measured, expected in zip(record['gyro_zero_dps'], bias):
            self.assertAlmostEqual(measured, expected, delta=.5)

    def test_large_stationary_gyro_offset_from_third_customer_is_accepted(self):
        bias = [1.25, 3.375, 29.1875]  # reported /api/status gyro, robot at rest
        rows = [(stamp, acc, list(bias)) for stamp, acc, _ in samples()[:151]]
        record = estimate(rows)
        self.assertEqual(record['gyro_zero_dps'], bias)

    def test_rotating_board_reports_movement_before_gyro_offset(self):
        rows = [(stamp, acc, [150. + (i % 2) * 20., 0., 0.])
                for i, (stamp, acc, _) in enumerate(samples()[:151])]
        with self.assertRaisesRegex(ValueError, 'Movement detected'):
            estimate(rows)

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
        moving[75] = (stamp, acc, [gyro[0], gyro[1] + 12., gyro[2]])
        with self.assertRaisesRegex(ValueError, 'Movement detected'):
            estimate(moving)
        with self.assertRaisesRegex(ValueError, 'zero offset is too large'):
            estimate([(stamp, acc, [1.9, 120., 7.3]) for stamp, acc, _ in rows])

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
            self.supply_feedback(app, samples()[0])
            with patch('controller.threading.Thread') as worker:
                self.assertTrue(app.start_imu_calibration()['active'])
                with self.assertRaisesRegex(ValueError, 'calibration is in progress'):
                    app.cal_center(0)
            job = worker.call_args.kwargs

            steady = samples()[0][1:]
            self.run_worker(app, job, lambda retry, frame: steady)
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

    def test_single_spike_discards_the_whole_window_and_saves_only_the_fresh_one(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'imu-calibration.json'
            previous = estimate(samples())
            save(file, previous)
            app = AppController(Mock(), data_dir=directory)
            self.supply_feedback(app, (0., [0., 0., -30.], [0.]*3))  # Click-time transient.
            job = self.start_job(app)

            def reading(retry, frame):
                if retry == 0:
                    return [2., 0., -9.8], [12. if frame == 70 else 0., 0., 0.]
                if frame == 1:
                    self.assertEqual(load(file), previous)
                    self.assertEqual(app._imu_record, previous)
                return [-1., 0., -9.8], [1., 2., 3.]

            with patch('controller.estimate_imu', wraps=estimate) as estimator, \
                    patch('controller.save_imu', wraps=save) as persist, patch('builtins.print') as log:
                rounds = self.run_worker(app, job, reading)
            self.assertEqual([s['retry'] for s in rounds], [0, 1])
            self.assertTrue(all(s['phase'] == 'settling' and s['samples'] == 0 and s['progress'] == 0
                                for s in rounds))
            first, second = [call.args[0] for call in estimator.call_args_list]
            self.assertGreater(first[0][0], .5)
            self.assertGreater(second[0][0] - first[-1][0], .5)
            for rows in (first, second):
                self.assertGreaterEqual(rows[-1][0] - rows[0][0], 1.5)
                self.assertEqual(len(set(row[0] for row in rows)), len(rows))
            for metric in ('acc_std_ms2=', 'gyro_std_dps=', 'acc_peak_ms2=', 'gyro_peak_dps=', 'exceeded=gyro_peak_dps'):
                self.assertIn(metric, log.call_args_list[0].args[0])
            prefix = '[imu-calibration] saved window 2: '
            success = log.call_args_list[1].args[0]
            self.assertTrue(success.startswith(prefix))
            self.assertEqual(json.loads(success[len(prefix):]), load(file)['measurement'])
            persist.assert_called_once()
            self.assertEqual(app.status()['imu_calibration']['state'], 'saved')
            self.assertEqual(load(file)['acc_offset_ms2'], [1., 0., 0.])
            self.assertEqual(load(file)['gyro_zero_dps'], [1., 2., 3.])
            self.assertEqual(load(file.with_suffix('.previous.json')), previous)

    def test_tenth_retry_can_pass_and_exhaustion_keeps_previous_calibration(self):
        for last_passes in (True, False):
            with self.subTest(last_passes=last_passes), tempfile.TemporaryDirectory() as directory:
                file = Path(directory) / 'imu-calibration.json'
                previous = estimate(samples())
                save(file, previous)
                before = file.read_bytes()
                app = AppController(Mock(), data_dir=directory)
                self.supply_feedback(app, samples()[0])
                job = self.start_job(app)

                def reading(retry, frame):
                    spike = frame == 60 and (retry < 10 or not last_passes)
                    return [0., 0., -9.8], [12. if spike else 0., 0., 0.]

                with patch('controller.save_imu', wraps=save) as persist, patch('builtins.print'):
                    rounds = self.run_worker(app, job, reading)
                self.assertEqual([s['retry'] for s in rounds], list(range(11)))
                status = app.status()['imu_calibration']
                self.assertFalse(status['active'])
                self.assertEqual(status['retry'], status['max_retries'])
                if last_passes:
                    persist.assert_called_once()
                    self.assertEqual(status['state'], 'saved')
                else:
                    persist.assert_not_called()
                    self.assertEqual(status['state'], 'error')
                    self.assertIn('after 10 automatic retries', status['message'])
                    self.assertEqual(file.read_bytes(), before)
                    self.assertEqual(app._imu_record, previous)

    def test_pose_sensor_and_feedback_failures_do_not_retry(self):
        cases = [
            ('upright', lambda n: ([9.8, 0., 0.], [0.]*3)),
            ('zero offset is too large', lambda n: ([0., 0., -9.8], [120., 0., 0.])),
            ('IMU contains nonfinite', lambda n: ([0., 0., -9.8], [float('nan'), 0., 0.])),
            ('interrupted', lambda n: None if 40 <= n <= 51 else ([0., 0., -9.8], [0.]*3)),
            ('MCU feedback is stale', lambda n: None),
        ]
        for reason, reading in cases:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                app = AppController(Mock(), data_dir=directory)
                self.supply_feedback(app, samples()[0])
                job = self.start_job(app)
                rounds = self.run_worker(app, job, lambda retry, frame: reading(frame))
                self.assertEqual(len(rounds), 1)
                status = app.status()['imu_calibration']
                self.assertEqual(status['state'], 'error')
                self.assertIn(reason, status['message'])
                self.assertFalse((Path(directory) / 'imu-calibration.json').exists())

    def test_exiting_during_a_retry_does_not_overwrite_a_new_job(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'imu-calibration.json'
            save(file, estimate(samples()))
            before = file.read_bytes()
            app = AppController(Mock(), data_dir=directory)
            app.set_mode('calibrate')
            self.supply_feedback(app, samples()[0])
            job = self.start_job(app)

            def reading(retry, frame):
                if retry == 1 and frame == 2:
                    app.set_mode('shadow')
                    self.assertEqual(app.status()['imu_calibration']['state'], 'cancelled')
                    self.start_job(app)
                return [0., 0., -9.8], [12. if frame == 60 else 0., 0., 0.]

            with patch('builtins.print'):
                self.run_worker(app, job, reading)
            status = app.status()['imu_calibration']
            self.assertTrue(status['active'])
            self.assertEqual((status['retry'], status['samples']), (0, 0))
            self.assertEqual(file.read_bytes(), before)

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

    def start_job(self, app):
        with patch('controller.threading.Thread') as worker:
            first = app.start_imu_calibration()
            self.assertEqual(app.start_imu_calibration(), first)
            worker.assert_called_once()
        return worker.call_args.kwargs

    def run_worker(self, app, job, reading):
        # Advance the host clock on each wait and supply distinct MCU readings at 100 Hz.
        start = time.monotonic()
        elapsed_ms = frame = 0
        rounds = []

        def advance(delay):
            nonlocal elapsed_ms, frame
            elapsed_ms += round(delay * 1000)
            if delay == .5:
                rounds.append(app._imu_status_locked())
                self.assertLessEqual(len(rounds), 11)
                frame = 0
                data = ([0., 0., -30.], [100., 0., 0.])  # Settling samples must be discarded.
            elif elapsed_ms % 10 == 0:
                frame += 1
                data = reading(len(rounds) - 1, frame)
            else:
                return False
            if data is not None:
                self.supply_feedback(app, (elapsed_ms / 1000, *data))
            return False

        with patch('controller.time.monotonic', side_effect=lambda: start + elapsed_ms / 1000), \
                patch.object(app.stop_event, 'wait', side_effect=advance):
            job['target'](*job['args'])
        return rounds

    @staticmethod
    def supply_feedback(app, row):
        stamp, acc, gyro = row
        app.feedback = Feedback(1, ALL_MASK, 1, 0, 0, 0, 0, 10000, 0, 0, 0, 0,
                                acc, gyro, [0.] * 15, [0.] * 15, [0.] * 15)
        app._imu_source_time = stamp
        app.rx_time = time.monotonic()


if __name__ == '__main__':
    unittest.main()
