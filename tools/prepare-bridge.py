"""Verify the bundled reliable MCU libraries used by the sketch build profile."""

import hashlib
from pathlib import Path

LIBRARIES = Path(__file__).resolve().parents[1] / 'sketch/libraries'
# Arduino testing.rc, including the Duck changes described in sketch/libraries/README.md.
LIBRARY_SHA256 = {
    'Arduino_RouterBridge': '890d2780f5761d8fc0bf52a64fbfda80f4cc1fdfeaed463c4ad65757326a03a3',
    'Arduino_RPClite': '800b8ff0ba6842010b5bcc38ccd23b5422b6475d912982458c95c24d94dcf641',
}


def library_sha256(directory):
    checksum = hashlib.sha256()
    for path in sorted(directory.rglob('*')):
        if path.is_file() and not any(part.startswith('.') for part in path.relative_to(directory).parts):
            checksum.update(path.relative_to(directory).as_posix().encode() + b'\0')
            checksum.update(hashlib.sha256(path.read_bytes()).digest())
    return checksum.hexdigest()


if __name__ == '__main__':
    for name, expected in LIBRARY_SHA256.items():
        if library_sha256(LIBRARIES / name) != expected:
            raise SystemExit(f'Unexpected contents of sketch/libraries/{name}. Check the source package.')
    print('Verified bundled Arduino_RouterBridge and Arduino_RPClite for reliable transport.')
