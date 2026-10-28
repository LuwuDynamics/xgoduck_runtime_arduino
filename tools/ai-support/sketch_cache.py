#!/usr/bin/env python3
"""Discard interrupted ARM object builds before the default App is compiled.

The App daemon may reuse an empty .o together with its empty dependency file.
Only invalid objects and their dependency file are removed. Sources, firmware
images, Python environments, settings and valid cached objects are preserved.
Runs as the arduino user via the App daemon's ExecStartPre.
"""
import argparse
from pathlib import Path
import struct

ELF32 = struct.Struct('<16sHHIIIIIHHHHHH')


def valid_arm_object(path):
    try:
        with path.open('rb') as source:
            header = source.read(ELF32.size)
        if len(header) != ELF32.size:
            return False
        h = ELF32.unpack(header)
        return (h[0][:7] == b'\x7fELF\x01\x01\x01' and h[1] == 1 and h[2] == 40
                and h[8] == ELF32.size and h[11] == 40 and h[12] > 0
                and h[6] >= ELF32.size and h[6]+h[11]*h[12] <= path.stat().st_size)
    except (OSError, struct.error):
        return False


def repair(root):
    root = Path(root).resolve()
    cache = root / '.cache' / 'sketch'
    if not cache.exists():
        return []
    if cache.is_symlink() or cache.parent.is_symlink():
        raise ValueError('Refusing a symlinked sketch cache')
    cache.resolve().relative_to(root)
    removed = []
    for path in sorted(cache.rglob('*.o')):
        if path.is_symlink() or not path.is_file():
            continue
        dependency = path.with_suffix('.d')
        empty_dependency = (dependency.is_file() and not dependency.is_symlink()
                            and dependency.stat().st_size == 0)
        if valid_arm_object(path) and not empty_dependency:
            continue
        path.unlink()
        if dependency.is_file() and not dependency.is_symlink():
            dependency.unlink()
        removed.append(str(path.relative_to(cache)))
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app_root', type=Path)
    args = parser.parse_args()
    try:
        removed = repair(args.app_root)
    except (OSError, ValueError) as exc:
        print(f'[sketch-cache] Could not check build cache: {exc}', flush=True)
        return 1
    if removed:
        print(f'[sketch-cache] Removed {len(removed)} interrupted object builds: '
              + ', '.join(removed), flush=True)
    else:
        print('[sketch-cache] Build cache is ready.', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
