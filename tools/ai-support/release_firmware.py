#!/usr/bin/env python3
"""Build and recover the STM32 firmware in an Arduino App CLI release."""

import argparse
import fcntl
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

MANIFEST = Path('provenance/stm32-firmware.json')
FIRMWARE = Path('.cache/sketch.fw')
RECOVERY_MANIFEST = Path('.cache/xgoduck-stm32.json')


def sha256(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def firmware_inputs(root):
    files = [Path('provenance/toolchain.json')]
    files.extend(Path('tools', name) for name in (
        'prepare-ai-sketch.py', 'prepare-bridge.py', 'bridge_patch.py', 'verify-elf.py',
    ))
    for folder in ('sketch', 'tools/patches'):
        files.extend(path.relative_to(root) for path in (root / folder).rglob('*')
                     if path.is_file() and not any(part.startswith('.') for part in path.relative_to(root).parts))
    return sorted(files)


def firmware_manifest(root, firmware_sha256):
    inputs = {path.as_posix(): sha256(root / path) for path in firmware_inputs(root)}
    return {'firmware_sha256': firmware_sha256, 'inputs': inputs}


def build_archive(source, archive, *, label=None, toolchain_archive=None):
    prepare = [sys.executable, str(source / 'tools/prepare-ai-sketch.py')]
    toolchain = ['--toolchain-archive', str(toolchain_archive)] if toolchain_archive else []
    subprocess.run([*prepare, '--dependencies-only', *toolchain], check=True)
    command = ['arduino-app-cli', 'app', 'build', str(source), '--target', 'unoq', '--output', str(archive)]
    if label:
        command.extend(('--release-label', label))
    subprocess.run(command, check=True)
    subprocess.run([*prepare, '--verify-build', str(source / '.cache/sketch')], check=True)


def archive_firmware(archive, release):
    member = release.getmember(f'{archive.stem}/prebuild/sketch.fw')
    if not member.isfile() or not member.size:
        raise ValueError('Arduino release did not contain a valid sketch.fw file.')
    return release.extractfile(member)


def stamp_release(source, archive, output):
    """Record the source inputs and firmware checksum in the native .ard layout."""
    with tarfile.open(archive, 'r:gz') as release:
        with archive_firmware(archive, release) as firmware:
            manifest = firmware_manifest(source, hashlib.file_digest(firmware, 'sha256').hexdigest())
        content = (json.dumps(manifest, indent=2) + '\n').encode()
        name = f'{archive.stem}/src/{MANIFEST.as_posix()}'
        with tarfile.open(output, 'w:gz', compresslevel=6) as stamped:
            for member in release.getmembers():
                if member.name != name:
                    stamped.addfile(member, release.extractfile(member) if member.isfile() else None)
            member = tarfile.TarInfo(name)
            member.size = len(content)
            stamped.addfile(member, io.BytesIO(content))


def matches_manifest(path, expected):
    try:
        return json.loads(path.read_text()) == expected
    except (FileNotFoundError, ValueError):
        return False


def firmware_is_current(root, firmware):
    try:
        if not firmware.stat().st_size:
            return False
        expected = firmware_manifest(root, sha256(firmware))
        return any(matches_manifest(root / manifest, expected) for manifest in (MANIFEST, RECOVERY_MANIFEST))
    except OSError:
        return False


def ensure_firmware(root):
    """Reuse a checked firmware file or regenerate it without uploading to STM32."""
    firmware = root / FIRMWARE
    firmware.parent.mkdir(parents=True, exist_ok=True)
    with (firmware.parent / '.xgoduck-stm32.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if firmware_is_current(root, firmware):
            print('STM32 precompiled firmware verified. Skipping sketch compilation.', flush=True)
            return

        # Do not leave a rejected artifact at the path App CLI will upload.
        firmware.unlink(missing_ok=True)
        print('STM32 release firmware missing or invalid. Rebuilding from the installed sketch.', flush=True)
        with tempfile.TemporaryDirectory(prefix='xgoduck-stm32-', dir=firmware.parent) as temp:
            work = Path(temp)
            source = work / 'source'
            for relative in firmware_inputs(root):
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / relative, target)

            # App CLI emits .fw when building a release. A minimal Python app lets
            # it do that without rebuilding or replacing the real App environment.
            (source / 'app.yaml').write_text('name: XGO Duck STM32 recovery\n')
            (source / 'python').mkdir()
            (source / 'python/main.py').write_text('pass\n')
            archive = work / 'xgoduck-stm32.ard'
            build_archive(source, archive)

            candidate = work / FIRMWARE.name
            with tarfile.open(archive, 'r:gz') as release:
                with archive_firmware(archive, release) as bundled, candidate.open('wb') as target:
                    shutil.copyfileobj(bundled, target)
            manifest = work / RECOVERY_MANIFEST.name
            manifest.write_text(json.dumps(firmware_manifest(source, sha256(candidate)), indent=2) + '\n')
            manifest.replace(root / RECOVERY_MANIFEST)
            candidate.replace(firmware)
        print('STM32 firmware rebuilt and verified. App CLI will upload it at start.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app_dir', type=Path)
    parser.add_argument('--if-default', action='store_true', help='Check only when this release is the default App')
    args = parser.parse_args()
    root = args.app_dir.resolve()
    if not (root / 'release.yaml').is_file():
        return 0
    try:
        if args.if_default:
            result = subprocess.check_output(['arduino-app-cli', 'properties', 'get', 'default', '--format', 'json'], text=True)
            default = json.loads(result).get('app') or {}
            if Path(default.get('FullPath') or '/').resolve() != root:
                return 0
        ensure_firmware(root)
    except (OSError, ValueError, KeyError, EOFError, tarfile.TarError, subprocess.CalledProcessError) as error:
        print(f'STM32 release firmware preparation failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
