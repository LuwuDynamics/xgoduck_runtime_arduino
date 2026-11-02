#!/usr/bin/env python3
"""One-time static gravity/gyro calibration; no actuator commands or auto-recalibration."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from imu_calibration import estimate, save


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:9527')
    parser.add_argument('--seconds', type=float, default=30.)
    parser.add_argument('--samples', type=Path, help='Use an already recorded JSON sample array')
    parser.add_argument('--output', type=Path, help='Save once; without this flag, only print the estimate')
    parser.add_argument('--device', default='')
    args = parser.parse_args()
    if args.output and args.output.exists():
        parser.error('Calibration file already exists. Keep it or back it up before recalibrating.')
    if args.samples:
        samples = json.loads(args.samples.read_text())
    else:
        if not 20 <= args.seconds <= 120:
            parser.error('Use 20–120 seconds of stationary sampling')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        samples, deadline = [], time.monotonic() + args.seconds
        print('Keep Default Pose stationary on a level surface; recording...', file=sys.stderr)
        while time.monotonic() < deadline:
            data = json.load(opener.open(args.url.rstrip('/') + '/api/status', timeout=3))
            samples.append(data)
            time.sleep(.075)
    result = estimate(samples, device=args.device)
    if args.output:
        save(args.output, result)
        print('Saved; the motion process reads this file on its next start.', file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
