#!/usr/bin/env python3
"""Create an App Lab ZIP, or an Arduino release with precompiled STM32 firmware."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ('assets', 'python', 'sketch', 'tools', 'docs', 'provenance', 'tests')
FILES = ('app.yaml', 'README.md', 'README_CN.md', 'LICENSE', 'version.txt', '.gitignore', '.dockerignore')
IGNORED = {'.git', '.cache', '.build', '.venv', '__pycache__', 'dist', 'data', 'offline'}


def selected(root):
    paths = [root / name for name in FILES if (root / name).is_file()]
    for folder in FOLDERS:
        paths.extend(path for path in (root / folder).rglob('*')
                     if path.is_file() and not path.is_symlink()
                     and not any(part in IGNORED for part in path.relative_to(root).parts)
                     and path.suffix != '.pyc' and path.name not in ('.env', '.DS_Store'))
    return sorted(paths)


def package(output, root=ROOT, toolchain_archive=None):
    files = selected(root)
    manifest = dict(app='XGO Duck AI', version=(root / 'version.txt').read_text().strip(),
        source_commit=subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
        source_branch=subprocess.check_output(['git', '-C', str(root), 'branch', '--show-current'], text=True).strip(),
        local_changes=bool(subprocess.check_output(['git', '-C', str(root), 'status', '--porcelain'], text=True)),
        sha256={path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})
    toolchain_name = None
    if toolchain_archive is not None:
        info = json.loads((ROOT / 'provenance/toolchain.json').read_text())
        digest = hashlib.sha256()
        with toolchain_archive.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
        if toolchain_archive.stat().st_size != info['size'] or digest.hexdigest() != info['sha256']:
            raise ValueError('Only the checksum-verified official Linux ARM64 toolchain can be bundled.')
        toolchain_name = 'tools/offline/' + info['archiveFileName']
        manifest['bundled_toolchain'] = info
        manifest['sha256'][toolchain_name] = info['sha256']
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
        if toolchain_name is not None:
            # The upstream xz archive is already compressed. Keep it unchanged,
            # including licenses, and avoid a second expensive compression pass.
            archive.write(toolchain_archive, toolchain_name, compress_type=zipfile.ZIP_STORED)
        archive.writestr('data/.gitkeep', '')
        archive.writestr('build-manifest.json', json.dumps(manifest, indent=2) + '\n')
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + '.sha256').write_text(checksum + '  ' + output.name + '\n')
    print(output)
    print(len(manifest['sha256']), 'packaged files;', output.stat().st_size, 'bytes;', 'SHA256', checksum)
    return manifest


def package_release(output, root=ROOT, toolchain_archive=None):
    sys.path.insert(0, str(ROOT / 'tools/ai-support'))
    from release_firmware import build_archive, sha256, stamp_release

    if output.suffix != '.ard':
        raise ValueError('The Arduino release output must have the .ard extension.')
    # App CLI uses the filename stem as the installed release directory name.
    if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_ -]*', output.stem) is None:
        raise ValueError('Use letters, digits, spaces, underscores or hyphens before .ard.')
    if shutil.which('arduino-app-cli') is None:
        raise ValueError('Build the .ard on a UNO Q with Arduino App CLI release support.')
    output.parent.mkdir(parents=True, exist_ok=True)
    version = (root / 'version.txt').read_text().strip()
    with tempfile.TemporaryDirectory(prefix='.xgoduck-release-', dir=output.parent) as temporary:
        work = Path(temporary)
        source = work / 'source'
        for path in selected(root):
            target = source / path.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        archive = work / 'raw' / output.name
        archive.parent.mkdir()
        build_archive(source, archive, label=version, toolchain_archive=toolchain_archive)
        stamped = work / output.name
        stamp_release(source, archive, stamped)
        checksum = sha256(stamped)
        checksum_file = work / 'release.sha256'
        checksum_file.write_text(checksum + '  ' + output.name + '\n')
        stamped.replace(output)
        checksum_file.replace(output.with_suffix('.ard.sha256'))
    print(output)
    print(output.stat().st_size, 'bytes;', 'SHA256', checksum)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--release', action='store_true', help='Build a precompiled .ard on UNO Q')
    p.add_argument('--output', type=Path, help='Output archive path')
    p.add_argument('--toolchain-archive', type=Path, help='Verified Linux ARM64 toolchain archive to bundle in ZIP or use for release builds')
    args = p.parse_args()
    if args.release:
        version = (ROOT / 'version.txt').read_text().strip()
        version_slug = re.sub(r'[^A-Za-z0-9]+', '-', version).strip('-')
        output = args.output or ROOT / f'dist/xgoduck-ai-{version_slug}-unoq.ard'
        try:
            package_release(output.resolve(), toolchain_archive=args.toolchain_archive)
        except (OSError, ValueError, KeyError, EOFError, tarfile.TarError, subprocess.CalledProcessError) as error:
            p.exit(1, f'Could not build Arduino release: {error}\n')
    else:
        output = args.output or ROOT / 'dist/xgoduck_ai.zip'
        package(output.resolve(), toolchain_archive=args.toolchain_archive)
