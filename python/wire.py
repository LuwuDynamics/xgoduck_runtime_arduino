"""Version 1 binary payloads carried by official Arduino Bridge MessagePack RPC."""
from dataclasses import dataclass
import math
import struct

STATE = struct.Struct('<4sBBH10I6h45f')
COMMAND = struct.Struct('<4sIIIBBH15f')
CAL = struct.Struct('<4sBBH15H')
SERVO = struct.Struct('<4sBBBBBBH')
RAW = struct.Struct('<4sH15H15H')
SERVO_REPLY = struct.Struct('<4sBBBBHH')
ALL_MASK = 0x7fff  # hardware order: 10..14,20..24,30..34
DISARM, ARM, POLICY, HOLD = range(4)

CAL_ENTER, CAL_EXIT, CAL_SET_RAW, CAL_FINISH_ONE, CAL_SET_ZEROS = range(5)
SERVO_ENTER, SERVO_EXIT, SERVO_UNLOCK, SERVO_SET_ID, SERVO_GOTO, SERVO_SET_PERM_KP_KD, SERVO_READ = range(7)

assert STATE.size == 240
assert COMMAND.size == 80
assert CAL.size == 38
assert SERVO.size == 12
assert RAW.size == 66
assert SERVO_REPLY.size == 12

@dataclass(frozen=True)
class Feedback:
    flags: int
    mask: int
    seq: int
    sample_us: int
    imu_us: int
    command_seq: int
    command_age_us: int
    period_us: int
    work_us: int
    overruns: int
    report_drops: int
    invalid_commands: int
    acc: tuple
    gyro: tuple
    angles: tuple
    velocities: tuple
    targets: tuple

    @property
    def imu_ok(self):
        return bool(self.flags & 1)

    @property
    def enabled(self):
        return bool(self.flags & 2)

    @property
    def calibrating(self):
        return bool(self.flags & 8)

    @property
    def servo_debugging(self):
        return bool(self.flags & 16)

def decode_state(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) != STATE.size:
        raise ValueError('expected 240-byte state')
    x = STATE.unpack(data)
    if x[0] != b'DQS1' or x[1] != 1 or x[3] & ~ALL_MASK:
        raise ValueError('invalid state ABI')
    if not all(math.isfinite(v) for v in x[20:]):
        raise ValueError('non-finite state')
    return Feedback(x[2], x[3], *x[4:14], tuple(v*9.8/16384 for v in x[14:17]),
                    tuple(v/32 for v in x[17:20]), tuple(x[20:35]),
                    tuple(x[35:50]), tuple(x[50:65]))

def encode_command(seq, sample_us, mode, targets, ttl_us=250000):
    if mode not in range(4):
        raise ValueError('invalid command mode/mask')
    if len(targets) != 15 or not all(math.isfinite(v) for v in targets):
        raise ValueError('15 finite targets required')
    if not 0 < ttl_us <= 250000:
        raise ValueError('invalid command lifetime')
    return COMMAND.pack(b'DQC1', seq & 0xffffffff, sample_us & 0xffffffff,
                        ttl_us, mode, 0, 0, *targets)

def encode_cal(op, index=0, mask=ALL_MASK, raw=None):
    if op not in range(5):
        raise ValueError('invalid cal op')
    values = list(raw or [0] * 15)
    if len(values) != 15:
        raise ValueError('15 raw values required')
    if any(not 0 <= int(v) <= 4095 for v in values):
        raise ValueError('raw out of range')
    return CAL.pack(b'DQL1', op, index & 0xff, mask & 0xffff, *[int(v) & 0xffff for v in values])

def encode_servo(op, target_id, new_id=0, kp=5, kd=20, raw_pos=2047):
    if op not in range(7):
        raise ValueError('invalid servo op')
    if not 1 <= int(target_id) <= 253:
        raise ValueError('invalid target id')
    if not 0 <= int(raw_pos) <= 4095:
        raise ValueError('raw out of range')
    return SERVO.pack(b'DQV1', op, int(target_id) & 0xff, int(new_id) & 0xff,
                      int(kp) & 0xff, int(kd) & 0xff, 0, int(raw_pos) & 0xffff)

def decode_raw(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) != RAW.size:
        raise ValueError('expected 66-byte raw')
    x = RAW.unpack(data)
    if x[0] != b'DQR1' or x[1] & ~ALL_MASK:
        raise ValueError('invalid raw ABI')
    return {'mask': x[1], 'pos': list(x[2:17]), 'zero': list(x[17:32])}

def decode_servo_reply(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) != SERVO_REPLY.size:
        raise ValueError('expected 12-byte servo reply')
    x = SERVO_REPLY.unpack(data)
    if x[0] != b'DQS2':
        raise ValueError('invalid servo reply ABI')
    return {'op': x[1], 'target_id': x[2], 'ok': bool(x[3]), 'raw_pos': x[5], 'zero_pos': x[6]}

def elapsed_us(now, before):
    return (now-before) & 0xffffffff
