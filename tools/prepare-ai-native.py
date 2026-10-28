#!/usr/bin/env python3
"""Run on UNO Q: stage Debian 13 native media tools in this App's data dir.

The official App container mounts the App, not the host's /usr/bin. Keep this
runtime bundle outside the export ZIP and leave Arduino's base image untouched.
"""
import hashlib
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess


CORE = re.compile(r'^(ld-linux|lib(c|m|pthread|dl|rt|resolv)\.so)')


def prepare(root):
    if platform.system() != 'Linux':
        raise SystemExit('Run this installer on the UNO Q Debian 13 ARM64 host, outside the App container.')
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if platform.system() != 'Linux' or platform.machine() != 'aarch64' or release.get('VERSION_ID', '').strip('"') != '13':
        raise SystemExit('Run this installer on the UNO Q Debian 13 ARM64 host, outside the App container.')
    destination = root / 'data/native'
    temporary = destination.with_name('native.new')
    shutil.rmtree(temporary, ignore_errors=True)
    (temporary / 'bin').mkdir(parents=True)
    (temporary / 'lib').mkdir()
    files = {}

    def copy(source, relative):
        source = Path(source).resolve(strict=True)
        target = temporary / relative
        shutil.copy2(source, target)
        target.chmod(0o755 if relative.startswith('bin/') else 0o644)
        files[relative] = dict(source=str(source), sha256=hashlib.sha256(target.read_bytes()).hexdigest())

    pending = []
    for tool in ('arecord', 'aplay', 'amixer', 'v4l2-ctl'):
        source = shutil.which(tool)
        if not source:
            raise SystemExit('Missing host utility: ' + tool)
        copy(source, 'bin/' + tool)
        pending.append(Path(source))
    cache = subprocess.check_output(['/sbin/ldconfig', '-p'], text=True)
    for library in ('libopus.so.0', 'libportaudio.so.2'):
        candidates = [line.rsplit(' => ', 1)[-1] for line in cache.splitlines() if line.strip().startswith(library + ' ')]
        if not candidates:
            raise SystemExit('Missing host library: ' + library)
        copy(candidates[0], 'lib/' + library)
        pending.append(Path(candidates[0]))
    seen = set()
    while pending:
        source = pending.pop().resolve()
        if source in seen:
            continue
        seen.add(source)
        linked = subprocess.check_output(['ldd', str(source)], text=True)
        if 'not found' in linked:
            raise SystemExit('Unresolved host dependency for ' + str(source))
        for line in linked.splitlines():
            match = re.match(r'\s*(\S+) => (/\S+)', line)
            if not match or CORE.match(match[1]):
                continue
            copy(match[2], 'lib/' + match[1])
            pending.append(Path(match[2]))
    (temporary / 'manifest.json').write_text(json.dumps(dict(architecture='aarch64', distribution='Debian 13', files=files), indent=2) + '\n')
    # Native files are generated on this board. They are never included in an
    # App export, and a future rerun replaces only this generated directory.
    shutil.rmtree(destination, ignore_errors=True)
    temporary.rename(destination)
    print('Prepared', len(files), 'native files in', destination)


if __name__ == '__main__':
    prepare(Path(__file__).resolve().parents[1])
