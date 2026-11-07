"""Check that all servo-reference assets ship together with traceable sources."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from rl_core import MOTOR_IDS


class CalibrationPageTest(unittest.TestCase):
    def test_guide_covers_every_actual_motor_in_the_correct_anatomical_zone(self):
        guide = json.loads((ROOT / 'assets/calibrate-joints.json').read_text())
        self.assertEqual([z['key'] for z in guide['zones']], ['head', 'left', 'right'])
        expected = {'head': [30, 31, 32, 33, 34], 'left': [10, 11, 12, 13, 14], 'right': [20, 21, 22, 23, 24]}
        for zone in guide['zones']:
            self.assertEqual(zone['ids'], expected[zone['key']])
            self.assertIn(zone['photo'], guide['photos'])
            for language in ('zh', 'en'):
                self.assertTrue(zone['title'][language])
                self.assertTrue(zone['note'][language])
        ids = [sid for zone in guide['zones'] for sid in zone['ids']]
        self.assertEqual(sorted(ids), list(MOTOR_IDS))
        self.assertEqual(len(set(ids)), 15)
        self.assertEqual(set(guide['joints']), {str(i) for i in MOTOR_IDS})
        for sid in MOTOR_IDS:
            info = guide['joints'][str(sid)]
            for language in ('zh', 'en'):
                self.assertTrue(info['name'][language])
                self.assertTrue(info['pose'][language])
            svg = ET.fromstring((ROOT / 'assets' / info['image']).read_bytes())
            ns = {'s': 'http://www.w3.org/2000/svg'}
            self.assertEqual(svg.attrib['viewBox'], '0 0 340 330')
            self.assertIn(str(sid), svg.find('s:title', ns).text)
            # Every drawing must contain the actual render, not just a placeholder frame.
            image = svg.find('.//s:image', ns).attrib['href']
            prefix, data = image.split(',', 1)
            self.assertEqual(prefix, 'data:image/webp;base64')
            raster = base64.b64decode(data, validate=True)
            self.assertEqual(raster[:4], b'RIFF')
            self.assertEqual(raster[8:12], b'WEBP')
            self.assertGreater(len(raster), 1000)
            self.assertIsNone(svg.find('.//s:script', ns))

    def test_photos_are_real_webp_files_and_all_guide_assets_are_packaged(self):
        guide = json.loads((ROOT / 'assets/calibrate-joints.json').read_text())
        for photo in guide['photos'].values():
            data = (ROOT / 'assets' / photo['src']).read_bytes()
            self.assertEqual(data[:4], b'RIFF')
            self.assertEqual(data[8:12], b'WEBP')
            self.assertGreater(len(data), 10000)
        # Run the actual package selector; no ZIP write or private-state access.
        import importlib.util
        spec = importlib.util.spec_from_file_location('calibration_packager', ROOT / 'tools/package-ai-app.py')
        packager = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packager)
        selected = {str(p.relative_to(ROOT)) for p in packager.selected(ROOT)}
        manifest = json.loads((ROOT / 'provenance/calibration-guide.json').read_text())
        self.assertTrue(set(manifest['sha256']).issubset(selected))

    def test_provenance_pins_the_shipped_assets_and_declares_the_visual_overlay(self):
        manifest = json.loads((ROOT / 'provenance/calibration-guide.json').read_text())
        self.assertEqual(manifest['canonical_motor_ids'], list(MOTOR_IDS))
        for name, expected in manifest['sha256'].items():
            with self.subTest(file=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)
        self.assertIn('no articulated jaw', manifest['urdf']['mouth'])
        motion = json.loads((ROOT / 'provenance/motion.json').read_text())
        self.assertEqual(motion['calibration_guide_overlay']['provenance'], 'provenance/calibration-guide.json')
        for name in ('assets/calibrate.html', 'assets/calibrate.js'):
            self.assertNotIn(name, motion['stable_exact_files'])
            self.assertEqual(motion['sha256'][name], manifest['sha256'][name])


if __name__ == '__main__':
    unittest.main()
