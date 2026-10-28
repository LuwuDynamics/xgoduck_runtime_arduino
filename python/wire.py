"""Version 1 binary payloads carried by official Arduino Bridge MessagePack RPC."""
from dataclasses import dataclass
import math
import struct

STATE = struct.Struct('<4sBBH10I6h45f')
COMMAND = struct.Struct('<4sIIIBBH15f')
CAL = struct.Struct('<4sIBB')
CAL_REPLY = struct.Struct('<4sIBB')
SERVO = struct.Struct('<4sBBBBBBH')
RAW = struct.Struct('<4sHH15H')
SERVO_REPLY = struct.Struct('<4sBBBBHHBB')
ALL_MASK = 0x7fff  # hardware order: 10..14,20..24,30..34
DISARM, ARM, POLICY, HOLD = range(4)

CAL_ENTER, CAL_EXIT, CAL_CENTER_ONE = range(3)
SERVO_CENTER_POS = 2047
CAL_HOLD_POS = 2048
SERVO_ENTER, SERVO_EXIT, SERVO_UNLOCK, SERVO_SET_ID = range(4)
SERVO_SET_PERM_KP_KD, SERVO_READ, SERVO_PING, SERVO_RESET_MID = range(5, 9)
SERVO_SET_PROTECTION = 9
SERVO_SET_PROTECTION_CURRENT = 10
BROADCAST_ID = 254
PROTECTION_CURRENT_UNIT_A = 0.0065
PROTECTION_DISABLED, PROTECTION_ENABLED = 0x04, 0x0D
SERVO_OPS = (SERVO_ENTER, SERVO_EXIT, SERVO_UNLOCK, SERVO_SET_ID,
             SERVO_SET_PERM_KP_KD, SERVO_READ, SERVO_PING, SERVO_RESET_MID,
             SERVO_SET_PROTECTION, SERVO_SET_PROTECTION_CURRENT)

assert STATE.size == 240
assert COMMAND.size == 80
assert CAL.size == 10
assert CAL_REPLY.size == 10
assert SERVO.size == 12
assert RAW.size == 38
assert SERVO_REPLY.size == 14

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

def encode_cal(op, index=0, seq=0):
    if op not in range(3):
        raise ValueError('invalid cal op')
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < 15:
        raise ValueError('joint index out of range')
    return CAL.pack(b'DQL2', seq & 0xffffffff, op, index)

def decode_cal_reply(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) != CAL_REPLY.size:
        raise ValueError('expected 10-byte calibration reply')
    magic, seq, index, ok = CAL_REPLY.unpack(data)
    if magic != b'DQA1' or not 0 <= index < 15 or ok not in (0, 1):
        raise ValueError('invalid calibration reply ABI')
    return {'seq': seq, 'index': index, 'ok': bool(ok)}

def encode_servo(op, target_id, new_id=0, kp=5, kd=20, *, protection_flags=0, protection_current_raw=0):
    if op not in SERVO_OPS:
        raise ValueError('invalid servo op')
    if op in (SERVO_SET_PROTECTION, SERVO_SET_PROTECTION_CURRENT):
        if target_id != BROADCAST_ID:
            raise ValueError('protection settings must broadcast')
        if (not isinstance(protection_current_raw, int) or isinstance(protection_current_raw, bool)
                or not 0 <= protection_current_raw <= 65535):
            raise ValueError('protection current out of range')
        if op == SERVO_SET_PROTECTION:
            if protection_flags not in (PROTECTION_DISABLED, PROTECTION_ENABLED) or protection_current_raw:
                raise ValueError('protection switch cannot set current')
        elif protection_flags:
            raise ValueError('protection current cannot change the switch')
        return SERVO.pack(b'DQV2', op, BROADCAST_ID, 0, 0, 0, protection_flags, protection_current_raw)
    if not 0 <= int(target_id) <= 253:
        raise ValueError('invalid target id')
    if op == SERVO_SET_ID and not 0 <= int(new_id) <= 253:
        raise ValueError('invalid new id')
    return SERVO.pack(b'DQV2', op, int(target_id) & 0xff, int(new_id) & 0xff,
                      int(kp) & 0xff, int(kd) & 0xff, 0, 0)

def encode_servo_protection(enabled):
    if not isinstance(enabled, bool):
        raise ValueError('protection_enabled must be true or false')
    flags = PROTECTION_ENABLED if enabled else PROTECTION_DISABLED
    return encode_servo(SERVO_SET_PROTECTION, BROADCAST_ID, protection_flags=flags)

def encode_servo_protection_current(current_a):
    if isinstance(current_a, bool):
        raise ValueError('protection current must be amperes')
    current = float(current_a)
    if not math.isfinite(current) or not 0 <= current <= 65535 * PROTECTION_CURRENT_UNIT_A:
        raise ValueError('protection current out of range')
    raw = round(current / PROTECTION_CURRENT_UNIT_A)
    return encode_servo(SERVO_SET_PROTECTION_CURRENT, BROADCAST_ID, protection_current_raw=raw)

def decode_raw(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) != RAW.size:
        raise ValueError('expected 38-byte raw')
    x = RAW.unpack(data)
    if x[0] != b'DQR2' or (x[1] | x[2]) & ~ALL_MASK:
        raise ValueError('invalid raw ABI')
    return {'mask': x[1], 'held_mask': x[2], 'pos': list(x[3:18])}

def decode_servo_reply(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) != SERVO_REPLY.size:
        raise ValueError('expected 14-byte servo reply')
    x = SERVO_REPLY.unpack(data)
    if x[0] != b'DQS3' or x[1] not in SERVO_OPS or x[3] not in (0, 1):
        raise ValueError('invalid servo reply ABI')
    if x[1] == SERVO_SET_PROTECTION:
        if x[2] != BROADCAST_ID or x[5] or (x[3] and x[4] not in (PROTECTION_DISABLED, PROTECTION_ENABLED)):
            raise ValueError('invalid protection reply ABI')
        return {'op': x[1], 'target_id': x[2], 'ok': bool(x[3]), 'broadcast': True,
                'protection_enabled': x[4] == PROTECTION_ENABLED,
                'protection_flags': x[4]}
    if x[1] == SERVO_SET_PROTECTION_CURRENT:
        if x[2] != BROADCAST_ID or x[4]:
            raise ValueError('invalid protection current reply ABI')
        return {'op': x[1], 'target_id': x[2], 'ok': bool(x[3]), 'broadcast': True,
                'current_raw': x[5], 'current_a': x[5] * PROTECTION_CURRENT_UNIT_A}
    if x[2] > 253:
        raise ValueError('invalid servo reply ID')
    return {'op': x[1], 'target_id': x[2], 'ok': bool(x[3]),
            'raw_pos': x[5], 'center_pos': x[6], 'kp': x[7], 'kd': x[8]}

def elapsed_us(now, before):
    return (now-before) & 0xffffffff
