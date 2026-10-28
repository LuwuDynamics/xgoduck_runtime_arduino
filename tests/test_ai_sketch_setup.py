import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ai_sketch_setup', ROOT / 'tools/prepare-ai-sketch.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class SketchSetupTest(unittest.TestCase):
    def fixture(self, directory):
        base = Path(directory) / 'arduino15'
        info = setup.metadata()
        payload = b'official archive fixture\0\xff'
        info = dict(info, size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        archive = Path(directory) / 'download.tar.xz'
        archive.write_bytes(payload)
        return base, info, archive

    def test_bundled_archive_is_verified_and_staged_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            base, info, archive = self.fixture(directory)
            cached = base / 'staging/packages' / info['archiveFileName']
            cached.parent.mkdir(parents=True)
            cached.write_bytes(b'interrupted download')
            setup.prepare_cache(base, info, archive)
            self.assertEqual(cached.read_bytes(), archive.read_bytes())
            self.assertEqual(list(cached.parent.glob('.xgoduck-toolchain-*')), [])
            with patch.object(setup.shutil, 'copyfileobj', side_effect=AssertionError('valid cache was copied again')):
                setup.prepare_cache(base, info, archive)

    def test_wrong_payload_never_replaces_valid_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            base, info, archive = self.fixture(directory)
            setup.prepare_cache(base, info, archive)
            cached = base / 'staging/packages' / info['archiveFileName']
            before = cached.read_bytes()
            archive.write_bytes(b'wrong CPU architecture or truncated archive')
            with self.assertRaisesRegex(ValueError, 'Wrong or incomplete'):
                setup.prepare_cache(base, info, archive)
            self.assertEqual(cached.read_bytes(), before)

    def test_factory_toolchain_cannot_satisfy_pinned_version(self):
        with tempfile.TemporaryDirectory() as directory:
            base, info, _ = self.fixture(directory)
            old = base / 'packages/zephyr/tools/arm-zephyr-eabi/0.16.8/bin/arm-zephyr-eabi-g++'
            old.parent.mkdir(parents=True)
            old.write_text('factory compiler must remain unchanged')
            with patch.object(setup.subprocess, 'run') as run:
                setup.prepare_cache(base, info)
            run.assert_not_called()
            self.assertFalse(setup.tool_paths(base, info)[0].exists())
            self.assertEqual(old.read_text(), 'factory compiler must remain unchanged')

    def test_only_exact_installed_version_is_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            base, info, _ = self.fixture(directory)
            internal, installed = setup.tool_paths(base, info)
            compiler = installed / 'bin/arm-zephyr-eabi-g++'
            compiler.parent.mkdir(parents=True)
            compiler.write_text('exact compiler fixture')
            with patch.object(setup.subprocess, 'run') as run:
                setup.prepare_cache(base, info)
            self.assertEqual(run.call_args.args[0][0], str(compiler))
            self.assertEqual(internal.resolve(), installed.resolve())

    def test_failed_dependency_resolution_stops_before_bridge_and_build(self):
        with patch.object(setup.subprocess, 'run', side_effect=subprocess.CalledProcessError(9, 'download failed')) as run:
            with self.assertRaises(subprocess.CalledProcessError):
                setup.compile_sketch('/usr/bin/arduino-cli', Path('/unused'), setup.metadata())
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertIn('--only-compilation-database', command)
            self.assertNotIn('--upload', command)
            self.assertNotIn('-u', command)

    def test_metadata_remains_consistent_with_sketch_platform_pin(self):
        info = setup.metadata()
        profile = (ROOT / 'sketch/sketch.yaml').read_text()
        self.assertIn(f"platform: {info['platform']} ({info['platform_version']})", profile)
        self.assertEqual(len(info['sha256']), 64)


if __name__ == '__main__':
    unittest.main()
