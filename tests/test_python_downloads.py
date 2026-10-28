import importlib.util
from pathlib import Path
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('python_downloads', ROOT / 'tools/configure-ai-python.py')
downloads = importlib.util.module_from_spec(spec)
spec.loader.exec_module(downloads)


class PythonDownloadsTest(unittest.TestCase):
    def test_region_defaults_are_local_and_never_change_chat_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data').mkdir()
            config = root / 'data/voice_config.json'
            original = b'{"cloud_region":"cn","volume":73}\n'
            config.write_bytes(original)
            for region in ('cn', 'global'):
                downloads.configure(root, region)
                value = tomllib.loads((root / 'uv.toml').read_text())
                self.assertEqual(value['index'][0]['url'], downloads.INDEXES[region])
                self.assertTrue(value['index'][0]['default'])
                self.assertEqual(config.read_bytes(), original)

    def test_existing_manual_or_explicit_index_survives_setup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'uv.toml'
            manual = '[[index]]\nurl = "https://packages.example.test/simple/"\ndefault = true\n'
            path.write_text(manual)
            downloads.configure(root, 'cn')
            self.assertEqual(path.read_text(), manual)
            downloads.configure(root, 'cn', 'https://explicit.example.test/simple')
            explicit = path.read_text()
            downloads.configure(root, 'global')
            self.assertEqual(path.read_text(), explicit)
            self.assertEqual((root / '.uv.toml.before-xgoduck').read_text(), manual)

    def test_unsafe_index_does_not_replace_valid_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloads.configure(root, 'cn')
            before = (root / 'uv.toml').read_bytes()
            for url in ('http://packages.example.test/simple', 'https://user:secret@example.test/simple',
                        'https://example.test/simple?token=secret', 'https://example.test/\nsetting=true'):
                with self.subTest(url=url), self.assertRaises(ValueError) as failure:
                    downloads.configure(root, 'cn', url)
                self.assertNotIn('secret', str(failure.exception))
                self.assertEqual((root / 'uv.toml').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
