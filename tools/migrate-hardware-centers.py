#!/usr/bin/env python3
"""Create software zeros for a robot already calibrated to hardware midpoint 2047.

Use only when migrating a robot centered with App 0.2.29/0.2.30 to the restored
next-audio-video-tof controller. This changes a local file only; no servo commands
are sent. Existing software calibration is never overwritten.
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

IDS = [10,11,12,13,14,20,21,22,23,24,30,31,32,33,34]


def migrate(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(version=1, ids=IDS, zero_pos=[2047] * 15,
                   source='already-calibrated-hardware-centers',
                   saved_at=datetime.now(timezone.utc).isoformat())
    # Exclusive creation preserves any existing per-robot software calibration.
    with path.open('x', encoding='utf-8') as target:
        target.write(json.dumps(payload, indent=2) + '\n')
        target.flush()
        os.fsync(target.fileno())
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-hardware-centers', action='store_true', required=True,
                        help='Confirm all 15 servos were previously centered using the hardware-midpoint workflow')
    parser.add_argument('--output', type=Path,
                        default=Path(__file__).resolve().parents[1] / 'data/zero_pos.json')
    args = parser.parse_args()
    try:
        migrate(args.output)
    except FileExistsError:
        parser.error('Software zeros already exist. Preserve them, or back them up and verify their origin before migrating.')
    print(f'Created {args.output}: 15 software zeros at 2047. No servo commands sent.')


if __name__ == '__main__':
    main()
