"""Feedback scope must be local to an action, not a whole-robot veto."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from motion_feedback import SERVO_IDS, feedback_ready, feedback_problem, feedback_mask


class MotionFeedbackTest(unittest.TestCase):
    def status(self, missing):
        return dict(full_robot_ready=False, imu_ok=True, feedback_age_ms=5.,
                    servo_ids=[i for i in SERVO_IDS if i != missing], missing_joint_ids=[missing])

    def test_every_missing_id_blocks_itself_but_no_other_individual_servo(self):
        for missing in SERVO_IDS:
            for requested in SERVO_IDS:
                with self.subTest(missing=missing, requested=requested):
                    self.assertEqual(feedback_ready(self.status(missing), (requested,)), requested != missing)
                    message = feedback_problem(self.status(missing), (requested,))
                    self.assertEqual(bool(message), requested == missing)
                    if requested == missing:
                        self.assertIn(f'{missing} 号舵机', message)

    def test_invalid_or_stale_sensors_block_even_an_empty_joint_scope(self):
        for change in ({'imu_ok': False}, {'feedback_age_ms': None},
                       {'feedback_age_ms': 150.}, {'feedback_age_ms': float('nan')}):
            with self.subTest(change=change):
                self.assertFalse(feedback_ready(self.status(33) | change, ()))

    def test_partial_and_older_status_providers_preserve_known_feedback_guards(self):
        self.assertTrue(feedback_ready({'full_robot_ready': True}, (32,)))
        self.assertFalse(feedback_ready({'full_robot_ready': False}, (32,)))
        self.assertTrue(feedback_ready(dict(full_robot_ready=False, imu_ok=True,
            feedback_age_ms=5., missing_joint_ids=[33]), (32,)))
        self.assertFalse(feedback_ready(self.status(33) | {'full_robot_ready': True}, (33,)))

    def test_mask_uses_the_existing_packet_motor_order(self):
        self.assertEqual(feedback_mask(SERVO_IDS), 0x7fff)
        self.assertEqual(feedback_mask((32,)), 1 << 12)
        self.assertEqual(feedback_mask((33,)), 1 << 13)
        self.assertEqual(feedback_mask((34,)), 1 << 14)
        self.assertEqual(feedback_mask(()), 0)


if __name__ == '__main__':
    unittest.main()
