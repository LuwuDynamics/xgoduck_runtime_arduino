import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/ai-support'))
import release_firmware

spec = importlib.util.spec_from_file_location('ai_packager', ROOT / 'tools/package-ai-app.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


def write(root, relative, content):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def make_archive(source, path, **options):
    def add_file(archive, name, content):
        entry = tarfile.TarInfo(f'{path.stem}/{name}')
        entry.size = len(content)
        archive.addfile(entry, io.BytesIO(content))

    with tarfile.open(path, 'w:gz') as archive:
        add_file(archive, 'release.yaml', b'target: unoq\n')
        add_file(archive, 'prebuild/sketch.fw', b'compiled STM32 firmware')
        for path_in_app in packager.selected(source):
            add_file(archive, f'src/{path_in_app.relative_to(source).as_posix()}', path_in_app.read_bytes())
        python_link = tarfile.TarInfo(f'{path.stem}/prebuild/.venv/bin/python')
        python_link.type = tarfile.SYMTYPE
        python_link.linkname = '/usr/local/bin/python'
        archive.addfile(python_link)


class ReleaseTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        write(self.root, 'app.yaml', b'name: XGO Duck AI\n')
        write(self.root, 'release.yaml', b'target: unoq\n')
        write(self.root, 'version.txt', b'0.3.5\n')
        write(self.root, 'sketch/sketch.ino', b'void setup() {}\n')
        write(self.root, 'sketch/sketch.yaml', b'default_profile: default\n')
        for filename in ('prepare-ai-sketch.py', 'prepare-bridge.py', 'verify-elf.py'):
            write(self.root, 'tools/' + filename, filename.encode())
        write(self.root, 'provenance/toolchain.json', b'{}')
        self.link_source = 'sketch/libraries/Arduino_RPClite/src/LinkTransport.cpp'
        write(self.root, self.link_source, b'bundled link transport')
        write(self.root, 'data/voice-state/device-id', b'private state')
        self.firmware = self.root / release_firmware.FIRMWARE

    def seed_firmware(self):
        write(self.root, release_firmware.FIRMWARE, b'precompiled')
        manifest = release_firmware.firmware_manifest(self.root, release_firmware.sha256(self.firmware))
        write(self.root, release_firmware.MANIFEST, json.dumps(manifest).encode())

    def test_ard_contains_matching_firmware_metadata_and_no_device_data(self):
        output = self.root / 'dist/duck.ard'
        with patch.object(packager.shutil, 'which', return_value='/usr/bin/arduino-app-cli'), \
             patch.object(release_firmware, 'build_archive', side_effect=make_archive) as build:
            packager.package_release(output, self.root)
        self.assertEqual(build.call_args.kwargs['label'], '0.3.5')
        with tarfile.open(output, 'r:gz') as archive:
            self.assertFalse(any('/data/' in name for name in archive.getnames()))
            manifest = json.load(archive.extractfile('duck/src/provenance/stm32-firmware.json'))
            write(self.root, release_firmware.FIRMWARE, archive.extractfile('duck/prebuild/sketch.fw').read())
            self.assertEqual(manifest, release_firmware.firmware_manifest(self.root, release_firmware.sha256(self.firmware)))
            self.assertTrue(archive.getmember('duck/prebuild/.venv/bin/python').issym())
        self.assertEqual(output.with_suffix('.ard.sha256').read_text(), f'{release_firmware.sha256(output)}  duck.ard\n')

    def test_rejects_release_name_that_app_cli_would_not_install(self):
        with self.assertRaisesRegex(ValueError, 'hyphens'):
            packager.package_release(self.root / 'duck-0.3.5.ard', self.root)

    def test_matching_firmware_skips_compile(self):
        self.seed_firmware()
        with patch.object(release_firmware, 'build_archive') as build:
            release_firmware.ensure_firmware(self.root)
        build.assert_not_called()

    def test_legacy_manifest_still_verifies_with_current_boot_guard(self):
        (self.root / self.link_source).unlink()
        write(self.root, 'tools/bridge_patch.py', b'legacy patcher')
        write(self.root, 'tools/patches/rpc.h', b'legacy transport patch')
        legacy = ('provenance/toolchain.json', 'sketch/sketch.ino', 'sketch/sketch.yaml',
                  'tools/prepare-ai-sketch.py', 'tools/prepare-bridge.py', 'tools/verify-elf.py',
                  'tools/bridge_patch.py', 'tools/patches/rpc.h')
        write(self.root, release_firmware.FIRMWARE, b'legacy precompiled firmware')
        manifest = {'firmware_sha256': release_firmware.sha256(self.firmware),
                    'inputs': {p: release_firmware.sha256(self.root / p) for p in legacy}}
        write(self.root, release_firmware.MANIFEST, json.dumps(manifest).encode())
        self.assertTrue(release_firmware.firmware_is_current(self.root, self.firmware))

    def test_corrupt_missing_and_stale_firmware_rebuild_and_are_reused(self):
        for problem in ('corrupt', 'missing', 'source changed', 'vendored transport changed'):
            with self.subTest(problem=problem):
                self.seed_firmware()
                if problem == 'corrupt':
                    self.firmware.write_bytes(b'corrupt')
                elif problem == 'missing':
                    self.firmware.unlink()
                elif problem == 'vendored transport changed':
                    write(self.root, self.link_source, b'updated transport')
                else:
                    write(self.root, 'sketch/sketch.ino', b'void setup() { changed(); }\n')
                with patch.object(release_firmware, 'build_archive', side_effect=make_archive) as build:
                    release_firmware.ensure_firmware(self.root)
                    self.assertEqual(self.firmware.read_bytes(), b'compiled STM32 firmware')
                    release_firmware.ensure_firmware(self.root)
                build.assert_called_once()
                self.assertEqual((self.root / 'data/voice-state/device-id').read_bytes(), b'private state')

    def test_failed_recovery_leaves_no_invalid_firmware_for_app_cli(self):
        self.seed_firmware()
        self.firmware.write_bytes(b'corrupt')
        with patch.object(release_firmware, 'build_archive', side_effect=subprocess.CalledProcessError(1, 'build')):
            with self.assertRaises(subprocess.CalledProcessError):
                release_firmware.ensure_firmware(self.root)
        self.assertFalse(self.firmware.exists())
        self.assertEqual(list(self.firmware.parent.glob('xgoduck-stm32-*')), [])


if __name__ == '__main__':
    unittest.main()
