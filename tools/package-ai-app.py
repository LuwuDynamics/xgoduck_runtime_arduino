#!/usr/bin/env python3
"""Create a clean, separately importable App Lab ZIP from this checkout."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
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
        sha256={str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})
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
            archive.write(path, str(path.relative_to(root)))
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


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', type=Path, default=ROOT / 'dist/xgoduck_ai.zip')
    p.add_argument('--toolchain-archive', type=Path, help='Bundle the official Linux ARM64 toolchain; size and SHA256 are verified')
    args = p.parse_args()
    package(args.output.resolve(), toolchain_archive=args.toolchain_archive)
