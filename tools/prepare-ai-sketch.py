#!/usr/bin/env python3
"""Resolve pinned MCU dependencies, patch Bridge and compile without uploading.

Run as arduino on the UNO Q host. An optional official Linux ARM64 archive can
be bundled in tools/offline/ or passed with --toolchain-archive. Arduino CLI
still owns dependency installation; this script never extracts a tool manually.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def metadata():
    return json.loads((ROOT / 'provenance/toolchain.json').read_text())


def tool_paths(base, info):
    dependency = f"{info['package']}:{info['tool']}@{info['version']}"
    uid = dependency.replace(':', '_').replace('@', '_') + '_' + hashlib.sha256(dependency.encode()).hexdigest()[:16]
    return (base / 'internal' / uid,
            base / 'packages' / info['package'] / 'tools' / info['tool'] / info['version'])


def verify_archive(path, info):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    if path.stat().st_size != info['size'] or digest.hexdigest() != info['sha256']:
        raise ValueError(f'Wrong or incomplete toolchain archive: {path}. Expected Linux ARM64 {info["version"]}, SHA256 {info["sha256"]}')


def prepare_cache(base, info, archive=None):
    internal, installed = tool_paths(base, info)
    # Never point the profile to a different version such as the factory 0.16.8.
    if archive is not None:
        verify_archive(archive, info)
    for directory in (internal, installed):
        compiler = directory / 'bin/arm-zephyr-eabi-g++'
        if compiler.is_file():
            subprocess.run([str(compiler), '--version'], check=True, stdout=subprocess.DEVNULL)
            if directory == installed and not internal.exists():
                if internal.is_symlink():
                    raise ValueError(f'Broken profile toolchain link: {internal}')
                internal.parent.mkdir(parents=True, exist_ok=True)
                internal.symlink_to(installed, target_is_directory=True)
            print(f'Reusing pinned toolchain {info["version"]}: {directory}', flush=True)
            return
    cached = base / 'staging/packages' / info['archiveFileName']
    if cached.is_file():
        try:
            verify_archive(cached, info)
        except ValueError:
            if archive is None:
                raise
        else:
            print('Verified Arduino toolchain download cache.', flush=True)
            return
    if archive is None:
        print(f'Arduino CLI will download toolchain {info["version"]} and the other pinned dependencies.', flush=True)
        return
    cached.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=cached.parent, prefix='.xgoduck-toolchain-', delete=False) as target:
            temporary = Path(target.name)
            with archive.open('rb') as source:
                shutil.copyfileobj(source, target)
            target.flush()
            os.fsync(target.fileno())
        verify_archive(temporary, info)
        temporary.replace(cached)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    print('Staged verified bundled toolchain for Arduino CLI.', flush=True)


def compile_sketch(cli, base, info):
    sketch = ROOT / 'sketch'
    build = ROOT / '.cache/sketch'
    compile_command = [cli, 'compile', '--profile', 'default', '--jobs', '2']
    # Resolves libraries and tools without uploading or compiling unpatched RPC.
    subprocess.run([*compile_command, '--only-compilation-database',
                    '--build-path', str(ROOT / '.cache/ai-sketch-deps'), str(sketch)], check=True)
    subprocess.run([sys.executable, str(ROOT / 'tools/prepare-bridge.py')], check=True)
    subprocess.run([*compile_command, '--clean',
                    '--build-path', str(build), str(sketch)], check=True)
    for directory in tool_paths(base, info):
        readelf = directory / 'bin/arm-zephyr-eabi-readelf'
        if readelf.is_file():
            subprocess.run([sys.executable, str(ROOT / 'tools/verify-elf.py'),
                            '--readelf', str(readelf), str(build / 'sketch.ino.elf')], check=True)
            break
    else:
        raise ValueError('Pinned toolchain readelf is missing; firmware has not been verified.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--toolchain-archive', type=Path, help='Official Linux ARM64 toolchain archive; checksum is verified')
    args = parser.parse_args()
    if platform.system() != 'Linux' or platform.machine() != 'aarch64' or os.getuid() != 1000:
        parser.error('Run as the arduino user (UID 1000) on the UNO Q Linux ARM64 host.')
    cli = shutil.which('arduino-cli')
    if cli is None:
        parser.error('arduino-cli is missing. Complete UNO Q initial setup first.')
    info = metadata()
    archive = args.toolchain_archive
    bundled = ROOT / 'tools/offline' / info['archiveFileName']
    if archive is None and bundled.is_file():
        archive = bundled
    base = Path.home() / '.arduino15'
    try:
        prepare_cache(base, info, archive)
        compile_sketch(cli, base, info)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'\nMCU build preparation failed: {error}\nNo firmware was uploaded by this script. '
              'Check the preceding Arduino error and retry after restoring network access.\n'
              'For a GitHub toolchain download failure, use the App ZIP with the bundled toolchain, '
              'or pass --toolchain-archive <official-archive>.\n'
              f'Official archive: {info["url"]}\nSHA256: {info["sha256"]}', file=sys.stderr)
        return 1
    print('MCU dependencies and Bridge are ready; compile and ELF verification passed. No firmware uploaded.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
