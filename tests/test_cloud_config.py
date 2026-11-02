import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from voice_config import VoiceConfig
from onboarding import ota_url


class LocalCloudConfigTest(unittest.TestCase):
    def run_cli(self, folder, *args):
        return subprocess.run([sys.executable, str(ROOT / 'tools/configure-ai.py'),
            '--data-dir', str(folder), *args], capture_output=True, text=True, timeout=5)

    def test_headroom_cli_preserves_cloud_url_language_volume_and_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = VoiceConfig(folder / 'voice_config.json')
            config.update({'cloud_region':'cn', 'ota_url':'https://fixture.invalid/ota/',
                           'language':'en', 'volume':100, 'output_gain_db':6})
            state = folder / 'voice-state'
            state.mkdir()
            (state/'device-id').write_text('02:12:34:56:AB:CD')
            result = self.run_cli(folder, '--aec-output-headroom-db', '12')
            self.assertEqual(result.returncode,0,result.stderr)
            saved = config.get()
            self.assertEqual(saved['aec_output_headroom_db'],12)
            for name,value in [('cloud_region','cn'),('ota_url','https://fixture.invalid/ota/'),
                               ('language','en'),('volume',100),('output_gain_db',6)]:
                self.assertEqual(saved[name],value)
            self.assertEqual((state/'device-id').read_text(),'02:12:34:56:AB:CD')
            before = config.path.read_bytes()
            self.assertEqual(self.run_cli(folder,'--show').returncode,0)
            self.assertEqual(config.path.read_bytes(),before)
            self.assertEqual(self.run_cli(folder,'--show','--aec-output-headroom-db','6').returncode,2)
            self.assertEqual(config.path.read_bytes(),before)

    def test_invalid_headroom_cli_leaves_saved_settings_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = VoiceConfig(folder/'voice_config.json')
            config.update({'cloud_region':'global','aec_output_headroom_db':6})
            before = config.path.read_bytes()
            for value in ('-1','18.1','nan','inf','bad'):
                result = self.run_cli(folder,'--aec-output-headroom-db',value)
                self.assertEqual(result.returncode,2,result.stderr)
                self.assertEqual(config.path.read_bytes(),before)

    def test_cli_persists_manual_choice_without_touching_identity_calibration_or_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = VoiceConfig(folder / 'voice_config.json')
            config.update(dict(volume=23, remote_motion_enabled=True, input_device='usb:saved'))
            (folder / 'zero_pos.json').write_text('calibration-fixture')
            state = folder / 'voice-state'
            state.mkdir()
            (state / 'device-id').write_text('02:12:34:56:AB:CD')
            self.assertEqual(ota_url(config.get()), '')
            result = self.run_cli(folder, '--region', 'global', '--language', 'cn')
            self.assertEqual(result.returncode, 0, result.stderr)
            restored = VoiceConfig(config.path).get()
            self.assertEqual(restored['cloud_region'], 'global')
            self.assertEqual(restored['language'], 'cn')
            self.assertEqual(restored['volume'], 23)
            self.assertTrue(restored['remote_motion_enabled'])
            self.assertEqual(restored['input_device'], 'usb:saved')
            self.assertIn('xl-api.luwudynamics.ai', ota_url(restored))
            self.assertEqual((folder / 'zero_pos.json').read_text(), 'calibration-fixture')
            self.assertEqual((state / 'device-id').read_text(), '02:12:34:56:AB:CD')
            before = config.path.read_bytes()
            self.assertEqual(self.run_cli(folder, '--show').returncode, 0)
            self.assertEqual(config.path.read_bytes(), before)

    def test_invalid_settings_fail_closed_and_cli_refuses_to_replace_them(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = VoiceConfig(folder / 'voice_config.json')
            for content in ('broken json', json.dumps({'cloud_region': 'auto'})):
                config.path.write_text(content)
                self.assertEqual(ota_url(config.get()), '')
                result = self.run_cli(folder, '--region', 'cn')
                self.assertEqual(result.returncode, 2)
                self.assertEqual(config.path.read_text(), content)

    def test_explicit_region_clears_custom_url_without_changing_language(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            config = VoiceConfig(folder / 'voice_config.json')
            config.update({'ota_url': 'https://fixture.invalid/ota/', 'language': 'en'})
            self.assertEqual(self.run_cli(folder, '--region', 'cn').returncode, 0)
            self.assertEqual(config.get()['language'], 'en')
            self.assertIn('xl-api.xgorobot.com', ota_url(config.get()))
