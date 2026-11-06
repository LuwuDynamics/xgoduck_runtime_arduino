"""Capture boost through the real Linux SDK worker and product wiring."""
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'python/vendor/luwu_os'


@unittest.skipUnless(platform.system() == 'Linux' and platform.machine() == 'aarch64',
                     'Target Linux ARM64 capture SDK check')
class CaptureGainSDKTest(unittest.TestCase):
    def check(self, source):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, LUWU_ROOT=str(VENDOR), DUCK_DATA_DIR=directory,
                       DUCK_VOICE_STATE_DIR=directory + '/private', DUCK_DEVICE_ID='02:00:00:00:00:01',
                       PYTHONPATH=os.pathsep.join(map(str, (ROOT / 'python', VENDOR / 'apps/voice_chat',
                                                           VENDOR / 'apps/ai', VENDOR))))
            result = subprocess.run([sys.executable, '-W', 'error::ResourceWarning', '-c', source],
                                    env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_chat_boost_occurs_once_after_aec_in_worker_not_callback(self):
        self.check('''
from unittest.mock import Mock, patch
import numpy as np
from audio_codec import AudioCodec
from audio_devices import CaptureGain

for aec_enabled in (False, True):
    with patch('audio_codec.LocalAEC'):
        codec = AudioCodec()
    try:
        raw = np.full(960, 1000, dtype='<i2').tobytes()
        cleaned = np.full(960, 500, dtype='<i2').tobytes()
        gain = CaptureGain(6.020599913279624)
        codec.input_transform = Mock(side_effect=gain.pcm)
        codec.aec = Mock(available=aec_enabled, sub_frame_size=160)
        codec.aec_enabled = aec_enabled
        codec.aec.process.return_value = cleaned
        codec._running = codec._capture_enabled = True
        with patch.object(codec, '_drain_far'):
            codec._input_callback(raw, 960, None, None)
        codec.input_transform.assert_not_called()
        codec.opus_encoder = Mock()
        def done(opus): codec._running = False
        codec.on_audio_frame = done
        codec.input_stream = Mock()
        codec._capture_loop()
        codec.input_transform.assert_called_once_with(cleaned if aec_enabled else raw)
        pcm = codec.opus_encoder.encode.call_args.args[0]
        np.testing.assert_array_equal(np.frombuffer(pcm, dtype='<i2'),
                                      np.full(960, 1000 if aec_enabled else 2000))
        if aec_enabled: codec.aec.process.assert_called_once_with(raw, codec.aec.pop_reference.return_value)
        else: codec.aec.process.assert_not_called()
    finally:
        codec.close()
''')

    def test_wake_worker_boosts_model_input_once_and_preserves_device_samples(self):
        self.check('''
from unittest.mock import Mock, patch
import numpy as np
from audio_devices import CaptureGain
from wakeword_manager import WakeWordManager, STRIDE_SAMPLES, CLIP_SAMPLES

gain = CaptureGain(6.020599913279624)
transform = Mock(side_effect=gain.floats)
manager = WakeWordManager(input_transform=transform)
manager._running = True
raw = np.full((STRIDE_SAMPLES, 1), .125, dtype=np.float32)
raw.flags.writeable = False
seen = []
class Stream:
    def __init__(self, **kw): self.callback = kw['callback']
    def __enter__(self):
        for _ in range(4): self.callback(raw, STRIDE_SAMPLES, None, None)
        transform.assert_not_called()  # No gain work on PortAudio's thread.
    def __exit__(self, *args): pass
def predict(samples):
    seen.append(samples.copy())
    manager._running = False
    return 0.
with patch('sounddevice.InputStream', Stream), patch('sounddevice.query_devices', return_value={}), \\
     patch.object(manager, '_load_model', return_value=True), patch.object(manager, '_predict', side_effect=predict):
    manager._listen_loop()
assert len(seen) == 1 and transform.call_count == 4
np.testing.assert_array_equal(seen[0], np.full(CLIP_SAMPLES, .25, dtype=np.float32))
np.testing.assert_array_equal(raw, np.full((STRIDE_SAMPLES, 1), .125, dtype=np.float32))
''')

    def test_product_wires_same_gain_to_chat_and_wake_preserving_playback_buffers(self):
        self.check('''
import asyncio, os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from voice_config import VoiceConfig
from voice_process import Runtime

async def check():
    config = VoiceConfig(Path(os.environ['DUCK_DATA_DIR']) / 'voice.json')
    config.update({'input_gain_db':6, 'volume':100, 'cloud_region':'cn'})
    runtime = Runtime('test', 0, config, onboarding=SimpleNamespace(mqtt={}, bound=True, snapshot=lambda:{}))
    runtime.audio_ready = True
    runtime.volume.apply = Mock()
    cards = [dict(card='1', input=True, output=True, key='usb:test', id='Test')]
    devices = [dict(name='duck_input', max_input_channels=1, max_output_channels=0),
               dict(name='duck_output', max_input_channels=0, max_output_channels=1)]
    with patch('sounddevice.query_devices', return_value=devices), patch('sounddevice._terminate'), \\
         patch('sounddevice._initialize'), patch('voice_process.prepare_capture'), \\
         patch('voice_process.prepare_portaudio', return_value=Path(os.environ['DUCK_DATA_DIR']) / 'alsa.conf'):
        app = runtime.build_application(cards)
    audio = Mock()
    app.config['audio_factory'] = Mock(return_value=audio)
    class Boundary(Exception): pass
    audio.start.side_effect = Boundary
    try:
        try: await app.start()
        except Boundary: pass
        else: raise AssertionError('Audio start was not reached')
        assert abs(audio.input_transform.__self__.gain - 10**.3) < 1e-8
        assert app.config['wakeword']['shared_capture'] is True
        args = app.config['audio_factory'].call_args.kwargs
        assert args['prebuffer_ms'] == 1000 and args['output_latency'] == .5
        assert args['input_latency'] == .32 and args['input_device'] == 0
        with patch.object(runtime.volume, 'pcm', return_value=b'processed') as volume:
            assert audio.output_transform(b'input') == b'processed'
            volume.assert_called_once_with(b'input', attenuation=1.)
        assert audio.output_observer.__self__ is runtime.mouth
        assert audio.output_reset_observer.__self__ is runtime.mouth
        assert app.config['tts_mouth_enabled'] is True
        with patch('application.WakeWordManager') as factory:
            factory.return_value.is_available.return_value = False
            app._init_wakeword()
            assert factory.call_args.kwargs['shared_input'] is True
            assert Path(factory.call_args.kwargs['model_path']).name == 'xiaoya.onnx'
            assert factory.call_args.kwargs['threshold'] == .5
            assert factory.call_args.kwargs['input_scale'] == 1.
            assert factory.call_args.kwargs['stride_duration'] == .08
            assert factory.call_args.kwargs['cooldown_sec'] == 2.
        assert runtime.status()['input_gain_db'] == 6
    finally:
        await app.shutdown()
        runtime.mouth_sender.close()
asyncio.run(check())
''')


if __name__ == '__main__':
    unittest.main()
