"""Servo gains belong to the user; runtime diagnostics are read-only."""
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'python'))
import wire
from runtime_gains import RuntimeGainsReader
from voice_supervisor import VoiceSupervisor
from rl_core import MOTOR_IDS


class RuntimeGainsTest(unittest.TestCase):
    def setup_controller(self, mode='policy', missing=False):
        c = SimpleNamespace(lock=threading.RLock(), servo_op_lock=threading.Lock(),
            servo_reply_event=threading.Event(), mode=mode, enabled=True,
            servo_reply=None, servo_expected_reply=None, commands=[], generation=12)
        c.user_gains = {sid: (3+sid%5, 12+sid%17) for sid in MOTOR_IDS}
        def notify(name, payload):
            self.assertEqual(name, 'duck_servo')
            command = wire.SERVO.unpack(payload)
            self.assertEqual(command[1], wire.SERVO_READ_RUN_GAINS)
            c.commands.append(command)
        def wait(op, target_id=None):
            self.assertEqual(c.servo_expected_reply, (op,target_id))
            if missing:raise TimeoutError('MCU unavailable')
            kp,kd = c.user_gains.get(target_id,(7,19))
            raw = wire.SERVO_REPLY.pack(b'DQS3',op,target_id,1,0,0,2047,kp,kd)
            return wire.decode_servo_reply(raw)
        c.bridge=SimpleNamespace(notify=notify)
        c._wait_servo_reply=wait
        return c,RuntimeGainsReader(c)

    def test_construction_and_status_never_reapply_gains(self):
        c,reader=self.setup_controller()
        before=dict(c.user_gains)
        for _ in range(3):
            self.assertEqual(reader.status(),dict(state='manual',auto_apply=False))
        self.assertEqual(c.commands,[])
        self.assertEqual(c.user_gains,before)
        self.assertFalse(hasattr(reader,'prepare'))

    def test_runtime_reads_return_each_users_values_without_requiring_a_fixed_profile(self):
        c,reader=self.setup_controller()
        before=dict(c.user_gains)
        for sid in MOTOR_IDS:
            result=reader.read(sid)
            self.assertEqual((result['kp'],result['kd']),before[sid])
            self.assertTrue(result['runtime'])
        self.assertEqual([x[1] for x in c.commands],[wire.SERVO_READ_RUN_GAINS]*15)
        self.assertTrue(all(x[4:6]==(0,0) for x in c.commands))
        self.assertEqual(c.user_gains,before)
        self.assertEqual((c.mode,c.enabled,c.generation),('policy',True,12))
        self.assertIsNone(c.servo_expected_reply)

    def test_exclusive_modes_and_pending_exits_are_not_overwritten_by_diagnostics(self):
        for mode in ('calibrate','servo_debug'):
            c,reader=self.setup_controller(mode=mode)
            with self.assertRaises(ValueError):reader.read(34)
            self.assertEqual(c.commands,[])
            self.assertEqual(c.mode,mode)
        for flag in ('calibrating','servo_debugging'):
            c,reader=self.setup_controller(mode='shadow')
            c.feedback=SimpleNamespace(**{flag:True})
            with self.assertRaises(ValueError):reader.read(34)
            self.assertEqual(c.commands,[])

    def test_missing_mcu_fails_the_read_without_writing_a_profile_or_changing_body_state(self):
        c,reader=self.setup_controller(missing=True)
        with self.assertRaises(TimeoutError):reader.read(34)
        self.assertEqual(len(c.commands),1)
        self.assertIsNone(c.servo_expected_reply)
        self.assertEqual((c.mode,c.enabled,c.generation),('policy',True,12))

    def test_runtime_read_rejects_reserved_and_invalid_ids(self):
        c,reader=self.setup_controller()
        for sid in [True,-1,254,255,'34',1.5]:
            with self.assertRaises(ValueError):reader.read(sid)
        self.assertEqual(c.commands,[])

    def test_retired_fixed_profile_is_rejected_in_both_packet_directions(self):
        with self.assertRaises(ValueError):
            wire.encode_servo(wire.SERVO_APPLY_RUN_GAINS,254,kp=5,kd=10)
        for ok in (0,1):
            packet=wire.SERVO_REPLY.pack(b'DQS3',wire.SERVO_APPLY_RUN_GAINS,254,ok,0,0,2047,5,10)
            with self.assertRaises(ValueError):wire.decode_servo_reply(packet)

    def test_explicit_manual_saved_parameters_keep_the_users_kp_kd(self):
        packet=wire.encode_servo(wire.SERVO_SET_PERM_KP_KD,33,kp=7,kd=19)
        self.assertEqual(wire.SERVO.unpack(packet)[1:6],(wire.SERVO_SET_PERM_KP_KD,33,0,7,19))

    def test_voice_spawn_and_respawn_need_no_motion_or_gain_preparation(self):
        supervisor=VoiceSupervisor('test-token')
        with patch('voice_supervisor.subprocess.Popen',return_value=SimpleNamespace(pid=123)) as spawn:
            supervisor._spawn();supervisor._spawn()
        self.assertEqual(spawn.call_count,2)
        self.assertNotIn('before_spawn',vars(supervisor))
        self.assertEqual(supervisor.restarts,0)


if __name__=='__main__':unittest.main()
