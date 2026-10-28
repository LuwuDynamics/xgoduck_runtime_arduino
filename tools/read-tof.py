#!/usr/bin/env python3
"""Read the optional UNO Q Qwiic VL53L5CX snapshot. Never controls motors.

Run inside the App container using its Python/Arduino Bridge installation.
"""
import argparse
import json
import statistics
import struct
import time

PACKET = struct.Struct('<4s8B6I64h64B64B')
STATES = ('starting', 'absent', 'initializing', 'ranging', 'error', 'offline')


def decode(data):
    if not isinstance(data, (bytes, bytearray)) or len(data) != PACKET.size:
        raise ValueError('Expected a 292-byte ToF snapshot; update the STM32 sketch first.')
    x = PACKET.unpack(data)
    magic, version, width, height, state, address, device, revision, reserved = x[:9]
    if (magic, version, width, height, address, reserved) != (b'DTF1', 1, 8, 8, 0x29, 0) or state >= len(STATES):
        raise ValueError('Invalid ToF snapshot ABI')
    seq, sample_ms, age_ms, read_us, errors, last_error = x[9:15]
    distances, statuses, targets = list(x[15:79]), list(x[79:143]), list(x[143:207])
    fresh = state == 3 and seq > 0 and age_ms <= 1000
    valid = [fresh and n > 0 and s in (5, 9) and d > 0
             for d, s, n in zip(distances, statuses, targets)]
    good = [d for d, ok in zip(distances, valid) if ok]
    center = [distances[i] for i in (27, 28, 35, 36) if valid[i]]
    message = ''
    if state == 1:
        message = 'No sensor response on Wire1 at 0x29; check Qwiic power/cable and restart the App.'
    elif state in (4, 5):
        stages = {1:'Unexpected device/revision ID', 2:'Sensor initialization failed',
                  3:'Could not select 8x8 resolution', 4:'Could not select 5 Hz',
                  5:'Sensor settings readback mismatch', 6:'Could not start ranging',
                  7:'Distance frame I2C read failed', 8:'Data-ready I2C read failed',
                  9:'No new distance frame for five seconds'}
        message = stages.get(last_error >> 24, 'Sensor error')
        message += f' (driver={(last_error >> 16) & 255}, status={last_error & 65535})'
        if state == 5:
            message += '; ToF is offline; other App functions continue. Restart the App after reconnecting it.'
    elif state == 3 and not fresh:
        message = 'Distance frame is stale; do not use it as a current measurement.'
    return dict(state=STATES[state], address=hex(address), device=hex(device),
                revision=hex(revision), resolution=[width, height], frequency_hz=5,
                seq=seq, sample_ms=sample_ms, age_ms=age_ms, read_us=read_us,
                errors=errors, last_error=last_error, error_message=message, fresh=fresh,
                valid_zones=len(good), min_mm=min(good) if good else None,
                max_mm=max(good) if good else None,
                center_mm=statistics.median(center) if center else None,
                distance_mm=distances, target_status=statuses, target_count=targets,
                grid_mm=[[distances[y*8+x] if valid[y*8+x] else None
                          for x in range(7, -1, -1)] for y in range(8)])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=10)
    parser.add_argument('--json', action='store_true', help='One JSON snapshot per line')
    args = parser.parse_args()
    if not 0 < args.seconds <= 3600:
        parser.error('--seconds must be in (0, 3600]')
    from arduino.app_utils import Bridge
    from arduino.app_utils.bridge import ClientServer
    deadline, previous, have_frame = time.monotonic()+args.seconds, None, False
    try:
        while time.monotonic() < deadline:
            data = decode(Bridge.call('duck_tof', timeout=3))
            key = (data['state'], data['seq'], data['fresh'], data['last_error'])
            if key != previous:
                previous = key
                if args.json:
                    print(json.dumps(data), flush=True)
                else:
                    print(f"{data['state']} frame={data['seq']} age={data['age_ms']}ms "
                          f"valid={data['valid_zones']}/64 center={data['center_mm']}mm "
                          f"range={data['min_mm']}..{data['max_mm']}mm error={data['last_error']} "
                          f"{data['error_message']}", flush=True)
                    for row in data['grid_mm']:
                        print(' '.join(f'{v:5d}' if v is not None else '    -' for v in row))
            have_frame |= data['fresh'] and data['valid_zones'] > 0
            if data['state'] in ('absent', 'error', 'offline'):
                return 1
            time.sleep(0.1)
    finally:
        ClientServer().stop()
    return 0 if have_frame else 1


if __name__ == '__main__':
    raise SystemExit(main())
