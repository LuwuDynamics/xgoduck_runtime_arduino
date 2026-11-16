import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ai_packager', ROOT / 'tools/package-ai-app.py')
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PackageTest(unittest.TestCase):
    def test_generated_app_preserves_assets_and_excludes_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ('python', 'assets', 'data/voice-state', '.venv', 'python/__pycache__', 'tools/offline'):
                (root / folder).mkdir(parents=True, exist_ok=True)
            (root / 'app.yaml').write_text('name: XGO Duck AI\n')
            (root / 'version.txt').write_text('0.1.0')
            (root / 'uv.toml').write_text('PRIVATE LOCAL PACKAGE INDEX')
            (root / '.uv.toml.before-xgoduck').write_text('PRIVATE PREVIOUS PACKAGE INDEX')
            (root / 'python/main.py').write_text('print("start")\n')
            (root / 'assets/test.bin').write_bytes(b'\0\xff\x12')
            for name in ('data/voice-state/doubao.json', '.venv/secret', 'python/.env', 'python/__pycache__/a.pyc', 'tools/offline/accidental.tar.xz'):
                (root / name).write_text('PRIVATE TEST VALUE')
            output = root / 'dist/xgoduck_ai.zip'
            with patch.object(packager.subprocess, 'check_output', side_effect=['f' * 40, 'fixture', '']):
                manifest = packager.package(output, root)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read('assets/test.bin'), b'\0\xff\x12')
                self.assertEqual([name for name in archive.namelist() if name.startswith('data/')], ['data/.gitkeep'])
                self.assertFalse(any(name.endswith('.env') or '__pycache__' in name or '.venv' in name for name in archive.namelist()))
                self.assertFalse(any(name.startswith('tools/offline/') for name in archive.namelist()))
                self.assertNotIn('uv.toml', archive.namelist())
                self.assertNotIn('.uv.toml.before-xgoduck', archive.namelist())
                self.assertEqual(json.loads(archive.read('build-manifest.json'))['sha256'], manifest['sha256'])

    def test_optional_toolchain_is_verified_and_listed_in_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'version.txt').write_text('test')
            toolchain = root / 'toolchain.tar.xz'
            toolchain.write_bytes(b'unchanged official archive fixture')
            info = dict(archiveFileName='pinned-linux-arm64.tar.xz', size=toolchain.stat().st_size,
                        sha256=hashlib.sha256(toolchain.read_bytes()).hexdigest())
            provenance = root / 'provenance'
            provenance.mkdir()
            (provenance / 'toolchain.json').write_text(json.dumps(info))
            output = root / 'dist/bundle.zip'
            with patch.object(packager, 'ROOT', root), patch.object(packager.subprocess, 'check_output', side_effect=['f' * 40, 'fixture', '']):
                manifest = packager.package(output, root, toolchain)
            name = 'tools/offline/' + info['archiveFileName']
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(archive.read(name), toolchain.read_bytes())
                self.assertEqual(archive.getinfo(name).compress_type, zipfile.ZIP_STORED)
                self.assertEqual(manifest['sha256'][name], info['sha256'])
            before = output.read_bytes()
            toolchain.write_bytes(b'incomplete or wrong archive')
            with patch.object(packager, 'ROOT', root), patch.object(packager.subprocess, 'check_output', side_effect=['f' * 40, 'fixture', '']):
                with self.assertRaisesRegex(ValueError, 'checksum-verified'):
                    packager.package(output, root, toolchain)
            self.assertEqual(output.read_bytes(), before)

    def test_vendored_sdk_matches_recorded_package_hashes(self):
        manifest = json.loads((ROOT / 'provenance/luwu_os.json').read_text())
        root = ROOT / 'python/vendor/luwu_os'
        for name, expected in manifest['sha256_packaged'].items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), expected)

    def test_microduck_source_attribution_and_license_ship_with_verified_adapter(self):
        manifest = json.loads((ROOT / 'provenance/microduck_voice.json').read_text())
        root = ROOT / 'python/vendor/microduck_voice'
        self.assertEqual(manifest['license'], 'Apache-2.0')
        self.assertIn(manifest['commit'], (root / 'NOTICE.md').read_text())
        self.assertIn('Apache License', (root / 'LICENSE').read_text())
        self.assertEqual(hashlib.sha256((root / 'LICENSE').read_bytes()).hexdigest(), manifest['sources']['LICENSE'])
        for name, expected in manifest['sha256_packaged'].items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), expected)


if __name__ == '__main__':
    unittest.main()
