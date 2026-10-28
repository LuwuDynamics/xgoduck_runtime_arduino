import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from capture_channels import CaptureChannels, hardware_channels
from voice_config import VoiceConfig
from audio_devices import prepare_portaudio
sys.path.insert(0, str(ROOT / 'tools/ai-support'))
from audio_caps import capture_devices


class CaptureChannelsTest(unittest.TestCase):
    def test_host_descriptor_reports_capture_only_and_handles_hot_unplug(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            card = root/'card1'
            card.mkdir()
            (card/'id').write_text('MIC')
            (card/'usbid').write_text('0c45:636b')
            (card/'stream0').write_text('Playback:\n Channels: 8\n Rates: 96000\nCapture:\n Channels: 2\n Rates: 16000, 48000\n')
            self.assertEqual(capture_devices(root), [dict(card='1',id='MIC',usbid='0c45:636b',channels=[2],rates=[16000,48000])])
            (card/'usbid').unlink()
            self.assertEqual(capture_devices(root), [])
    def test_antiphase_microphones_do_not_cancel_in_auto(self):
        x = np.sin(np.arange(1280) * .13).astype(np.float32) * .2
        c = CaptureChannels(2)
        for _ in range(30):
            np.testing.assert_array_equal(c.floats(np.column_stack((x, -x))), x)
        self.assertEqual(c.switches, 0)

    def test_silent_channel_switches_after_hysteresis_and_does_not_flap(self):
        x = np.sin(np.arange(1280) * .13).astype(np.float32) * .2
        c = CaptureChannels(2)
        for _ in range(6):
            np.testing.assert_array_equal(c.floats(np.column_stack((x*0, x))), x*0)
        np.testing.assert_array_equal(c.floats(np.column_stack((x*0, x))), x)
        for _ in range(20):
            c.floats(np.column_stack((x, x*.8)))
        self.assertEqual(c.status()['selected_channel'], 2)
        self.assertEqual(c.switches, 1)

    def test_pcm_mono_identity_and_explicit_channel_selection(self):
        x = np.array([-32768, -2000, 0, 32767], dtype='<i2')
        self.assertEqual(CaptureChannels().pcm(x.tobytes()), x.tobytes())
        stereo = np.column_stack((x, x//2)).astype('<i2').tobytes()
        self.assertEqual(CaptureChannels(2, 'right').pcm(stereo), (x//2).astype('<i2').tobytes())
        out = CaptureChannels(2, 'average').pcm(stereo)
        self.assertEqual(len(out), len(x)*2)

    def test_detects_hardware_not_virtual_plug_channel_limit(self):
        devices = [dict(name='duck_input', max_input_channels=128),
                   dict(name='Webcam (hw:1,0)', max_input_channels=2),
                   dict(name='Mono (hw:2,0)', max_input_channels=1)]
        self.assertEqual(hardware_channels(devices, {'card':'1'}), 2)
        self.assertEqual(hardware_channels(devices, {'card':'2'}), 1)
        self.assertEqual(hardware_channels(devices, {'card':'3'}), 1)

    def test_capture_configuration_is_persistent_and_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = VoiceConfig(Path(folder)/'voice.json')
            cfg.update({'input_channel_mode':'right','input_sample_rate':48000})
            self.assertEqual(VoiceConfig(cfg.path).get()['input_channel_mode'], 'right')
            for setting in ({'input_channel_mode':'beamform'}, {'input_sample_rate':True},
                            {'input_sample_rate':44100}):
                with self.assertRaises(ValueError): cfg.update(setting)

    def test_native_rate_and_channels_are_only_set_on_input_alias(self):
        with tempfile.TemporaryDirectory() as folder, patch('audio_devices.private_directory', return_value=Path(folder)):
            p = prepare_portaudio({'card':'1'}, {'card':'2'}, 48000, 2)
            source = p.read_text().splitlines()
            self.assertIn('rate 48000 channels 2', source[1])
            self.assertNotIn('rate ', source[2])
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
