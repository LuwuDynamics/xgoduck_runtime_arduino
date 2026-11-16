"""Delivered classifier preprocessing, cadence and fixed product wake feedback."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'python/vendor/luwu_os'
MODEL = ROOT / 'python/models/xiaoya.onnx'


@unittest.skipUnless(importlib.util.find_spec('numpy') and importlib.util.find_spec('onnxruntime'),
                     'Wake ONNX runtime is installed in the target userspace')
class XiaoyaModelTest(unittest.TestCase):
    def test_capture_gap_does_not_score_stitched_speech_and_stale_audio_cannot_wake(self):
        import numpy as np
        for stale in (False, True):
            with self.subTest(stale=stale):
                m = self.manager_type(stride_duration=.08, on_wakeup=Mock())
                m._load_model = Mock(return_value=True)
                m._predict = Mock(return_value=1.)
                m._running = True
                def stream(**cfg):
                    context = Mock()
                    def enter():
                        for i in range(26):
                            cfg['callback'](np.full((1280, 1), .1, np.float32), 1280, None,
                                            SimpleNamespace(input_overflow=(i == 24 and not stale)))
                        return SimpleNamespace(latency=.32)
                    context.__enter__ = Mock(side_effect=enter)
                    context.__exit__ = Mock(return_value=False)
                    return context
                fake = SimpleNamespace(InputStream=stream, default=SimpleNamespace(device=None))
                # Stop once the worker drains the test stream. No wall-clock sleep.
                clock = [0.] * 26 + [1. if stale else 0.] * 26
                with patch.dict(sys.modules, sounddevice=fake), \
                     patch('time.monotonic', side_effect=clock), \
                     patch('time.sleep', side_effect=lambda _: setattr(m, '_running', False)):
                    m._listen_loop()
                m._predict.assert_not_called()
                m._on_wakeup.assert_not_called()
                self.assertEqual(m._capture_stats['stale_blocks'], 26 if stale else 0)
                self.assertEqual(m._capture_stats['discontinuities'], 0 if stale else 1)

    def test_stereo_callback_is_bounded_and_never_logs_or_runs_audio_work(self):
        import numpy as np
        m = self.manager_type(input_channels=2, stride_duration=.08, on_audio=Mock())
        m._load_model = Mock(return_value=True)
        m._running = True
        raw = np.full((1280, 2), .1, np.float32)
        def stream(**cfg):
            self.assertEqual(cfg['channels'], 2)
            self.assertEqual(cfg['latency'], .32)
            context = Mock()
            def enter():
                with patch('builtins.print', side_effect=AssertionError('logging in audio callback')):
                    for _ in range(100):
                        cfg['callback'](raw, 1280, None, SimpleNamespace(input_overflow=True))
                m._running = False
                return SimpleNamespace(latency=.4)
            context.__enter__ = Mock(side_effect=enter)
            context.__exit__ = Mock(return_value=False)
            return context
        fake = SimpleNamespace(InputStream=stream, default=SimpleNamespace(device=None))
        with patch.dict(sys.modules, sounddevice=fake):
            m._listen_loop()
        self.assertEqual(m._capture_stats['input_overflows'], 100)
        self.assertEqual(m._capture_stats['queue_drops'], 73)
        self.assertEqual(m._capture_stats['input_latency_ms'], 400)
        m._on_audio.assert_not_called()

    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('xiaoya_wake_test', VENDOR / 'apps/ai/wakeword_manager.py')
        module = importlib.util.module_from_spec(spec)
        with patch.dict(os.environ, LUWU_ROOT=str(VENDOR)):
            spec.loader.exec_module(module)
        cls.manager_type = module.WakeWordManager

    def test_delivered_final_model_and_fixed_waveform_match_their_manifest(self):
        import hashlib, wave
        import onnxruntime as ort
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        manifests = [json.loads((ROOT / 'provenance' / name).read_text())
                     for name in ('xiaoya-wakeword.json', 'hey-ducky-wakeword.json')]
        for manifest in manifests:
            with self.subTest(phrase=manifest['wake_phrase']):
                entry = manifest['classifier']
                path = ROOT / entry['path']
                self.assertEqual(path.stat().st_size, entry['size'])
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), entry['sha256'])
                session = ort.InferenceSession(str(path), options, providers=['CPUExecutionProvider'])
                self.assertEqual(session.get_inputs()[0].shape[1:], [16, 96])
                self.assertEqual(session.get_outputs()[0].shape[1:], [1])
        cue = manifests[0]['cue']
        path = ROOT / cue['path']
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), cue['sha256'])
        with wave.open(str(path), 'rb') as w:
            self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (16000, 1, 2))
            self.assertAlmostEqual(w.getnframes() / 16000, .9169375)

    def test_normalized_audio_matches_training_frontend_and_scalar_reference_embeddings(self):
        import numpy as np
        m = self.manager_type(model_path=str(MODEL), input_scale=1., threshold=.8,
                              stride_duration=.08, cooldown_sec=2.)
        self.assertTrue(m._load_model())
        t = np.arange(32000, dtype=np.float32) / 16000
        rng = np.random.default_rng(74)
        for audio in (np.zeros(32000, np.float32), (.05*np.sin(2*np.pi*220*t)).astype(np.float32),
                      rng.normal(0, .01, 32000).astype(np.float32)):
            before = audio.copy()
            # Training library: normalized waveform, then 76-frame windows at
            # stride eight. It runs embedding windows independently.
            mel = m._model['mel_session'].run(None, {m._model['mel_input']:audio[None, :]})[0].squeeze()/10+2
            windows = [mel[i:i+76] for i in range(0, len(mel)-75, 8)]
            embeddings = np.concatenate([m._model['emb_session'].run(None, {
                m._model['emb_input']:w[None, :, :, None].astype(np.float32)})[0].reshape(1, 96)
                for w in windows], axis=0)
            reference = m._model['cls_session'].run(None, {
                m._model['cls_input']:embeddings[-16:][None, :, :]})[0][0, 0]
            np.testing.assert_allclose(m._compute_mel(audio), mel, rtol=1e-5, atol=1e-6)
            self.assertAlmostEqual(m._predict(audio), float(reference), places=5)
            np.testing.assert_array_equal(audio, before)
        legacy = self.manager_type()
        self.assertEqual((legacy._input_scale, legacy._stride_samples, legacy._cooldown_sec),
                         (32768., 8000, 1.5))

    def test_xiaoya_stream_uses_80ms_blocks_and_waits_for_two_seconds_before_scoring(self):
        import numpy as np
        woke = Mock()
        m = self.manager_type(model_path=str(MODEL), input_scale=1., threshold=.5,
                              stride_duration=.08, cooldown_sec=2., on_wakeup=woke)
        m._load_model = Mock(return_value=True)
        m._predict = Mock(side_effect=[.49, .50])
        m._running = True
        def stream(**cfg):
            self.assertEqual(cfg['blocksize'], 1280)
            context = Mock()
            def enter():
                for _ in range(26):
                    cfg['callback'](np.full((1280, 1), .01, np.float32), 1280, None, None)
            context.__enter__ = Mock(side_effect=enter)
            context.__exit__ = Mock(return_value=False)
            return context
        fake = SimpleNamespace(InputStream=stream, default=SimpleNamespace(device=None))
        with patch.dict(sys.modules, sounddevice=fake):
            m._listen_loop()
        self.assertEqual(m._predict.call_count, 2)
        self.assertEqual(m._predict.call_args.args[0].shape, (32000,))
        woke.assert_called_once()

    def test_reused_embeddings_match_full_graph_across_sliding_windows_and_level_changes(self):
        import numpy as np
        m = self.manager_type(model_path=str(MODEL), input_scale=1.)
        self.assertTrue(m._load_model())
        audio = np.random.default_rng(47).normal(0, .01, 32000+12*1280).astype(np.float32)
        audio[38000:] *= 8  # New context must never reuse embeddings by position alone.
        for start in range(0, 12*1280+1, 1280):
            chunk = audio[start:start+32000]
            mel = m._compute_mel(chunk)
            windows = np.stack([mel[i:i+76] for i in range(0, len(mel)-75, 8)])
            full = m._model['emb_session'].run(None, {
                m._model['emb_input']:windows[..., None].astype(np.float32)})[0].squeeze(axis=(1, 2))
            cached = m._compute_embeddings(mel)
            np.testing.assert_allclose(cached, full, rtol=1e-5, atol=1e-5)
            expected = m._model['cls_session'].run(None, {
                m._model['cls_input']:full[-16:][None, :, :]})[0][0, 0]
            self.assertAlmostEqual(m._predict(chunk), float(expected), places=5)
            self.assertLessEqual(len(m._embedding_cache), 32)
        self.assertGreater(m._embedding_cache_hits, 0)
        self.assertLess(m._embedding_cache_misses, 13*16/2)

    def test_invalid_model_profiles_fail_before_opening_capture(self):
        for key, values in [('input_scale', [True, 0, 2, float('nan'), '1']),
                            ('stride_duration', [True, .001, .51, float('inf'), 'fast']),
                            ('cooldown_sec', [False, 0, 6, float('nan'), '2'])]:
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.manager_type(**{key:value})


@unittest.skipUnless(platform.system() == 'Linux' and platform.machine() == 'aarch64',
                     'Target Linux ARM64 product SDK check')
class XiaoyaProductTest(unittest.TestCase):
    def check(self, source):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, LUWU_ROOT=str(VENDOR), DUCK_DATA_DIR=directory,
                       DUCK_VOICE_STATE_DIR=directory + '/voice-state',
                       DUCK_LANGUAGE_FILE=directory+'/voice-state/language.ini',
                       PYTHONPATH=os.pathsep.join(map(str, (ROOT/'python', VENDOR, VENDOR/'apps/voice_chat'))))
            r = subprocess.run([sys.executable, '-W', 'error::ResourceWarning', '-c', source],
                               env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(r.returncode, 0, r.stdout+r.stderr)

    def test_shared_microphone_sends_preroll_and_live_audio_in_order_while_cue_is_queued(self):
        self.check('''
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import numpy as np
from application import Application, WakeWordManager
from voice_application import application_type

async def until(condition):
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(.004)

async def check():
    runtime = SimpleNamespace(mouth=None)
    app = application_type(Application, runtime)(dict(
        enable_robot=False, enable_camera=False, ambient_sound={'enabled':False},
        provisioned_mqtt={'endpoint':'test'}, wakeword={'shared_capture':True,'stride_duration':.08},
        input_transform=lambda pcm: (np.frombuffer(pcm, dtype='<i2') * 2).tobytes()))
    protocol = Mock(session_id='test-session')
    protocol.start, protocol.shutdown = AsyncMock(), AsyncMock()
    opened = False
    protocol.is_audio_channel_opened.side_effect = lambda: opened
    connecting, connect, starting, start = (asyncio.Event() for _ in range(4))
    replaying, replay = asyncio.Event(), asyncio.Event()
    sent = []

    async def open_channel():
        nonlocal opened
        connecting.set()
        await connect.wait()
        opened = True
        return True

    async def start_listening(mode, *, wait_published):
        assert opened and mode == 'auto' and wait_published
        starting.set()
        await start.wait()
        return True

    async def send(frame, timestamp):
        assert app.state == 'listening' and start.is_set() and timestamp == 0
        if not sent:
            replaying.set()
            await replay.wait()
        sent.append(int.from_bytes(frame, 'little') // 2)
        return True

    protocol.open_audio_channel = AsyncMock(side_effect=open_channel)
    protocol.send_start_listening = AsyncMock(side_effect=start_listening)
    protocol.send_audio = AsyncMock(side_effect=send)
    with (
        patch('application.MqttUdpProtocol', return_value=protocol),
        patch('audio_codec.sd.RawOutputStream'),
        patch('audio_codec.sd.RawInputStream') as capture,
        patch('sounddevice.InputStream') as separate_capture,
        patch('audio_codec.opuslib.Encoder') as encoder,
        patch.object(WakeWordManager, '_load_model', return_value=True),
        patch.object(WakeWordManager, '_predict', return_value=.95) as predict,
    ):
        encode = encoder.return_value.encode
        encode.side_effect = lambda pcm, size: pcm[:2]
        try:
            assert await app.start()
            await until(lambda: app.wakeword._feed_queue is not None)
            callback = capture.call_args.kwargs['callback']

            async def feed(first, last):
                for value in range(first, last + 1):
                    callback(value.to_bytes(2, 'little') * 960, 960, None, None)
                    await until(lambda: encode.call_count == value)

            await feed(1, 34)
            await asyncio.wait_for(connecting.wait(), 3)
            assert app.state == 'connecting' and not app.audio.is_playback_idle()
            waveform = np.repeat(np.arange(1, 35, dtype=np.float32), 960)[:32000] / 16384
            np.testing.assert_array_equal(predict.call_args.args[0], waveform)
            await feed(35, 40)
            protocol.send_audio.assert_not_awaited()
            connect.set()
            await asyncio.wait_for(starting.wait(), 3)
            await feed(41, 43)
            protocol.send_audio.assert_not_awaited()
            start.set()
            await asyncio.wait_for(replaying.wait(), 3)
            await feed(44, 47)
            assert not app.audio.is_playback_idle()  # Listening did not clear the wake cue.
            replay.set()
            await until(lambda: app._wake_buffer is None)
            await feed(48, 49)
            await until(lambda: sent[-1] == 49)
            assert sent[0] in (28, 29), sent
            assert sent == list(range(sent[0], 50)), sent
            app._back_to_idle()
            capture.assert_called_once()
            capture.return_value.close.assert_not_called()
            separate_capture.assert_not_called()
        finally:
            await app.shutdown()
asyncio.run(check())
''')

    def test_unsent_wake_audio_is_discarded_on_publish_failure_overflow_and_shutdown(self):
        self.check('''
import asyncio
from unittest.mock import AsyncMock, Mock
from application import Application, WAKE_BUFFER_FRAMES

async def check(scenario):
    app = Application({'wakeword':{'shared_capture':True}})
    app.loop = asyncio.get_running_loop()
    app.audio = Mock(aec_enabled=False)
    app.wakeword = Mock(_running=False)
    starting, release = asyncio.Event(), asyncio.Event()
    protocol = app.protocol = Mock()
    protocol.is_audio_channel_opened.return_value = True
    protocol.close_audio_channel = AsyncMock(side_effect=app._on_audio_channel_closed)
    protocol.shutdown = AsyncMock()
    protocol.send_audio = AsyncMock()

    async def start_listening(*args, **kwargs):
        starting.set()
        await release.wait()
        return scenario == 'overflow'

    protocol.send_start_listening = AsyncMock(side_effect=start_listening)
    app._on_capture_frame(b'preroll')
    task = app._spawn(app._handle_wakeword())
    try:
        await asyncio.wait_for(starting.wait(), 1)
        app._on_capture_frame(b'while-connecting')
        if scenario == 'overflow':
            for _ in range(WAKE_BUFFER_FRAMES + 1):
                app._on_capture_frame(b'overflow')
            assert len(app._wake_buffer) == WAKE_BUFFER_FRAMES
        if scenario == 'shutdown':
            await app.shutdown()
            assert task.cancelled()
        else:
            release.set()
            await task
            assert app.state == 'idle'
            if scenario == 'publish-failed':
                app.audio.clear_play_queue.assert_not_called()  # Keep the wake acknowledgement.
            app.wakeword.start.assert_called_once()
        assert app._wake_buffer is None and not app._wake_history
        protocol.send_audio.assert_not_awaited()
    finally:
        if not app._closing:
            await app.shutdown()

async def main():
    for scenario in ('publish-failed', 'overflow', 'shutdown'):
        await check(scenario)
asyncio.run(main())
''')

    def test_product_wake_profile_follows_region_without_changing_language(self):
        self.check('''
import asyncio, os
from pathlib import Path
from types import SimpleNamespace
from voice_config import VoiceConfig
from voice_process import Runtime
async def check():
    config = VoiceConfig(Path(os.environ['DUCK_DATA_DIR'])/'voice.json')
    runtime = Runtime('test',0,config,onboarding=SimpleNamespace(mqtt={},bound=True,snapshot=lambda:{}))
    try:
        for language in ('cn', 'en'):
            custom = ('xiaoya.onnx', '小鸭小鸭') if language == 'cn' else (None, 'Hi Luka')
            for region, endpoint, (model, phrase) in (
                ('cn', '', ('xiaoya.onnx', '小鸭小鸭')),
                ('global', '', ('hey_ducky.onnx', 'Hey Ducky')),
                ('global', 'https://custom.invalid/ota/', custom),
            ):
                saved = config.update({'language':language, 'cloud_region':region, 'ota_url':endpoint})
                app = runtime.build_application([])
                try:
                    profile = app.config['wakeword']
                    assert profile['shared_capture'] is True
                    assert app._wake_word() == config.region_status()['wake_phrase'] == phrase
                    if model is None:
                        assert 'model_path' not in profile and 'input_scale' not in profile
                    else:
                        path = Path(profile['model_path'])
                        assert path.name == model and path.is_file()
                        assert (profile['threshold'], profile['input_scale'], profile['stride_duration'],
                                profile['cooldown_sec']) == (.5, 1., .08, 2.)
                    assert config.get() == saved
                finally: await app.shutdown()
    finally: runtime.mouth_sender.close()
asyncio.run(check())
''')

    def test_fixed_duck_cue_repeats_without_rotating_mcp_variants_and_handles_wake_interrupt(self):
        self.check('''
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, call
from application import Application
from voice_application import application_type, WAKE_CUE
async def check():
    runtime = SimpleNamespace(config=SimpleNamespace(get=lambda:{'output_gain_db':0}),mouth=None)
    app = application_type(Application,runtime)({'enable_robot':False,'enable_camera':False})
    app.audio = Mock()
    try:
        variants = dict(app.duck_sounds.variants)
        app._play_wake_sound(); app._play_wake_sound()
        app.state = 'speaking'
        app.abort_speaking = AsyncMock()
        await app._handle_wakeword()
        assert app.audio.play_wav.call_args_list == [call(str(WAKE_CUE))]*3
        assert app.duck_sounds.variants == variants
        app.abort_speaking.assert_awaited_once()
    finally: await app.shutdown()
asyncio.run(check())
''')

    def test_actual_wake_cue_reaches_speaker_and_aec_but_not_mouth(self):
        self.check('''
from unittest.mock import Mock, patch
from audio_codec import AudioCodec
from voice_application import WAKE_CUE
with patch('audio_codec.LocalAEC'):
    audio = AudioCodec()
try:
    audio._running = True
    audio.output_observer = Mock()
    audio.play_wav(str(WAKE_CUE))
    expected = audio._play_pcm_buf
    assert any(expected)
    for offset in range(0,len(expected),1920):
        out = bytearray(1920)
        audio._output_callback(out,960,None,None)
        assert bytes(out) == expected[offset:offset+1920].ljust(1920,b'\\0')
        assert audio._far_pending[-1][-1] == bytes(out)
        assert audio.output_observer.call_args.args[0] == bytes(1920)
finally: audio.close()
''')
