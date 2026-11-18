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

    def test_shared_wake_keeps_a_contiguous_window_through_a_short_inference_stall(self):
        import numpy as np

        clock = 0.
        windows = []
        woke = Mock()
        manager = self.manager_type(stride_duration=.08, shared_input=True, on_wakeup=woke)
        manager._load_model = Mock(return_value=True)
        manager._running = True

        def feed(sequence, value, captured_at):
            pcm = value.to_bytes(2, 'little') * 960
            manager.feed_pcm(pcm, captured_at, 0, sequence)

        def fill_window():
            for sequence in range(1, 35):
                feed(sequence, 1, clock)

        def predict(window):
            nonlocal clock
            windows.append(np.round(window * 32768).astype(int))
            if len(windows) == 1:
                # Simulate seven callbacks queued while inference is delayed.
                for index in range(1, 8):
                    feed(34 + index, 2 if index <= 5 else 3, index * .06)
                clock = .81
                return 0.
            return 1.

        manager._on_ready = fill_window
        manager._predict = predict
        with patch('time.monotonic', side_effect=lambda: clock), \
             patch('time.sleep', side_effect=lambda _: setattr(manager, '_running', False)):
            manager._listen_loop()

        woke.assert_called_once()
        self.assertEqual(len(windows), 2)
        self.assertEqual(set(windows[1]), {1, 2, 3})
        self.assertEqual(windows[1][-1], 3, 'late blocks alone must not be scored')
        self.assertEqual(manager._capture_stats['catchup_blocks'], 5)
        self.assertEqual(manager._capture_stats['discontinuities'], 0)

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

    def test_shared_microphone_drops_wake_and_cue_audio_then_buffers_the_first_sentence(self):
        self.check('''
import asyncio, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import numpy as np
from application import Application, WakeWordManager
from voice_application import application_type

async def until(condition):
    async with asyncio.timeout(3):
        while not condition():
            await asyncio.sleep(.004)

async def check(fast_connection):
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
    protocol.send_wake_word_detected = AsyncMock()
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
            capture.return_value.latency = .36

            async def feed(first, last, *, encode_frames=False, input_delay=0.):
                clock = None if input_delay is None else SimpleNamespace(
                    currentTime=2., inputBufferAdcTime=2. - input_delay)
                for value in range(first, last + 1):
                    encoded = encode.call_count
                    callback(value.to_bytes(2, 'little') * 960, 960, clock, None)
                    sequence = app.audio._input_callbacks
                    await until(lambda: app.audio._capture_last_seq == sequence)
                    if encode_frames:
                        await until(lambda: encode.call_count > encoded)

            await feed(1, 34)
            await asyncio.wait_for(connecting.wait(), 3)
            assert app.state == 'connecting' and not app.audio.is_playback_idle()
            waveform = np.repeat(np.arange(1, 35, dtype=np.float32), 960)[:32000] / 16384
            np.testing.assert_array_equal(predict.call_args.args[0], waveform)
            if fast_connection:
                connect.set()
                await until(lambda: opened)
            await feed(35, 40)
            protocol.send_start_listening.assert_not_awaited()
            protocol.send_audio.assert_not_awaited()
            with app.audio._play_pcm_lock:
                app.audio._play_pcm_buf = b''
                app.audio._audible_until = time.monotonic() + .05  # Still queued in the DAC.
            assert not app.audio._capture_enabled
            await until(lambda: app.audio._capture_enabled)
            await feed(41, 42, input_delay=.36)
            await feed(43, 43, input_delay=None)
            encode.assert_not_called()

            await feed(44, 45, encode_frames=True)
            connect.set()
            await asyncio.wait_for(starting.wait(), 3)
            await feed(46, 46, encode_frames=True)
            protocol.send_audio.assert_not_awaited()
            start.set()
            await asyncio.wait_for(replaying.wait(), 3)
            await feed(47, 47, encode_frames=True)
            assert app.audio.is_playback_idle()
            replay.set()
            await until(lambda: app._wake_buffer is None)
            await feed(48, 49, encode_frames=True)
            await until(lambda: sent[-1] == 49)
            assert sent == list(range(44, 50)), sent
            protocol.send_wake_word_detected.assert_not_awaited()
            app._back_to_idle()
            capture.assert_called_once()
            capture.return_value.close.assert_not_called()
            separate_capture.assert_not_called()
        finally:
            await app.shutdown()
async def main():
    for fast_connection in (True, False):
        await check(fast_connection)
asyncio.run(main())
''')

    def test_unsent_wake_audio_is_discarded_on_publish_failure_overflow_and_shutdown(self):
        self.check('''
import asyncio
from unittest.mock import AsyncMock, Mock
from application import Application, WAKE_BUFFER_FRAMES

async def check(scenario):
    app = Application({'wakeword':{'shared_capture':True}})
    app.loop = asyncio.get_running_loop()
    app.audio = Mock(aec_enabled=False, wait_playback_done=Mock(return_value=True))
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
    task = app._spawn(app._handle_wakeword())
    try:
        await asyncio.wait_for(starting.wait(), 1)
        app._on_capture_frame(b'after-cue')
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
        assert app._wake_buffer is None
        protocol.send_audio.assert_not_awaited()
    finally:
        if not app._closing:
            await app.shutdown()

async def main():
    for scenario in ('publish-failed', 'overflow', 'shutdown'):
        await check(scenario)
asyncio.run(main())
''')

    def test_playback_wake_keeps_one_mic_and_stops_before_the_network_responds(self):
        self.check('''
import asyncio, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import numpy as np
from application import Application, WakeWordManager
from mqtt_udp_protocol import AbortReason
from voice_config import wake_profile

async def until(condition):
    async with asyncio.timeout(2):
        while not condition():
            await asyncio.sleep(.004)

async def check(auto_interrupt, echo_available):
    abort_started, release_abort, listen_started, release_listen = (asyncio.Event() for _ in range(4))
    protocol = Mock(session_id='channel')
    protocol.is_audio_channel_opened.return_value = True
    protocol.start = AsyncMock()
    protocol.shutdown = AsyncMock()
    protocol.send_audio = AsyncMock(return_value=True)

    async def abort(reason):
        assert reason == AbortReason.WAKE_WORD_DETECTED
        abort_started.set()
        await release_abort.wait()
        return True

    async def listen(mode, *, wait_published):
        assert mode == 'auto' and wait_published
        listen_started.set()
        await release_listen.wait()
        return True

    protocol.send_abort_speaking = AsyncMock(side_effect=abort)
    protocol.send_start_listening = AsyncMock(side_effect=listen)
    _, wakeword = wake_profile({'cloud_region':'cn'})
    app = Application(dict(enable_wakeword=True, enable_robot=False, enable_camera=False,
        enable_aec=auto_interrupt, ambient_sound={'enabled':False},
        provisioned_mqtt={'endpoint':'test'},
        wakeword=dict(wakeword, interrupt_playback=True)))
    app.start_chat = AsyncMock()
    offset = 100 if echo_available else 0
    aec = Mock(available=echo_available, sub_frame_size=160)
    aec.process.side_effect = lambda near, far: (np.frombuffer(near, dtype='<i2') + offset).tobytes()

    def predict(samples):
        # Wake must see the raw near-end; AEC output (+offset) must not score.
        return .319 if round(float(samples[-1]) * 32768) == 50 else 0.

    with (patch('application.MqttUdpProtocol', return_value=protocol),
          patch('audio_codec.LocalAEC', return_value=aec),
          patch('audio_codec.sd.RawOutputStream'),
          patch('audio_codec.sd.RawInputStream') as capture,
          patch('sounddevice.InputStream') as separate_capture,
          patch('audio_codec.opuslib.Encoder') as encoder,
          patch.object(WakeWordManager, '_load_model', return_value=True) as load_model,
          patch.object(WakeWordManager, '_predict', side_effect=predict) as score):
        encode = encoder.return_value.encode
        encode.side_effect = lambda pcm, size: pcm[:2]

        async def feed(*values, encode_frames=True, input_delay=0.):
            clock = SimpleNamespace(currentTime=2., inputBufferAdcTime=2. - input_delay)
            for value in values:
                count = encode.call_count
                callback(value.to_bytes(2, 'little') * 960, 960, clock, None)
                sequence = app.audio._input_callbacks
                await until(lambda: app.audio._capture_last_seq == sequence)
                if encode_frames:
                    await until(lambda: encode.call_count > count)

        def sent():
            return [int.from_bytes(args.args[0], 'little') for args in protocol.send_audio.await_args_list]

        try:
            assert await app.start()
            await until(lambda: app.wakeword._feed_queue is not None)
            callback = capture.call_args.kwargs['callback']
            detector = app.wakeword._thread
            wait_playback = app.audio.wait_playback_done
            app.audio.wait_playback_done = lambda timeout: wait_playback(min(timeout, 2.0))
            await feed(*([50] * 35), encode_frames=False)
            await until(lambda: score.call_count >= 2)
            app.start_chat.assert_not_awaited()
            score.reset_mock()
            app._set_state('listening')
            await feed(*([3] * 35))
            await until(lambda: not app.wakeword._feed_queue)
            score.assert_not_called()
            protocol.send_audio.reset_mock()

            app._handle_tts({'state':'start'})
            with patch.object(app, '_save_aec_state'):
                app.mcp_set_aec(auto_interrupt)
            assert app.audio._capture_enabled and app.audio.aec_enabled == auto_interrupt
            old_context = app._wake_detection_context()
            app._handle_tts({'state':'start'})
            await app._handle_wakeword(old_context)
            protocol.send_abort_speaking.assert_not_awaited()
            app.audio._play_pcm_buf = b'old music'
            app.audio._audible_until = time.monotonic() + 1.
            await feed(10, 11, 12)
            await until(lambda: score.called)
            if echo_available:
                if auto_interrupt:
                    aec.process.assert_called()
                else:
                    aec.process.assert_not_called()
            if auto_interrupt:
                await until(lambda: len(sent()) >= 3)
            assert sent() == ([110, 111, 112] if auto_interrupt else []), sent()

            app._handle_tts({'state':'stop', 'session_id':'channel'})
            await feed(50, 50, encode_frames=False)
            await asyncio.wait_for(abort_started.wait(), 2)
            app.start_chat.assert_not_awaited()
            assert app.state == 'speaking' and app.aborted
            assert app.audio._play_pcm_buf and b'old music' not in app.audio._play_pcm_buf
            app.audio.output_stream.abort.assert_called_once()
            app._on_incoming_audio(b'late old Opus', 0)
            app._handle_tts({'state':'stop', 'session_id':'794ae3f9af8b4948a260fd96951d171e'})
            app._handle_tts({'state':'start'})
            assert app.audio.play_queue.empty() and app.state == 'speaking' and app.aborted
            before_cue = sent()
            await feed(71, 72, encode_frames=False)
            release_abort.set()
            await asyncio.sleep(.02)
            protocol.send_start_listening.assert_not_awaited()
            with app.audio._play_pcm_lock:
                app.audio._play_pcm_buf = b''
                app.audio._audible_until = time.monotonic()
            await asyncio.wait_for(listen_started.wait(), 2)
            await feed(73, 74, encode_frames=False, input_delay=.36)
            await feed(75, 76)
            assert sent() == before_cue
            release_listen.set()
            await until(lambda: app.state == 'listening' and not app.audio.wake_echo_cancel)
            assert sent()[len(before_cue):] == [75 + offset, 76 + offset], sent()
            await feed(77)
            await until(lambda: sent()[-1] == (177 if auto_interrupt else 77))
            protocol.send_abort_speaking.assert_awaited_once()
            protocol.send_start_listening.assert_awaited_once()
            assert app.wakeword._thread is detector and detector.is_alive()
            load_model.assert_called_once()
            capture.assert_called_once()
            separate_capture.assert_not_called()
        finally:
            await app.shutdown()

async def main():
    for auto_interrupt, echo_available in ((False, True), (True, True), (False, False)):
        await check(auto_interrupt, echo_available)
asyncio.run(main())
''')

    def test_playback_wake_returns_to_idle_when_relisten_fails(self):
        self.check('''
import asyncio
from unittest.mock import AsyncMock, Mock
from application import Application

async def main():
    for abort_sent in (True, False):
        app = Application({'wakeword':{'shared_capture':True,'interrupt_playback':True}})
        app.loop = asyncio.get_running_loop()
        app.audio = Mock(aec_enabled=False, wait_playback_done=Mock(return_value=True))
        app.wakeword = Mock(_running=True)
        app.state = 'speaking'
        app.protocol = Mock(session_id='channel', is_audio_channel_opened=Mock(return_value=True),
                            send_abort_speaking=AsyncMock(return_value=abort_sent),
                            send_start_listening=AsyncMock(return_value=False),
                            close_audio_channel=AsyncMock(), send_audio=AsyncMock())
        await app._handle_wakeword(app._wake_detection_context())
        assert app.state == 'idle' and app._wake_buffer is None
        app.protocol.close_audio_channel.assert_awaited_once()
        assert app.protocol.send_start_listening.await_count == int(abort_sent)
        app.protocol.send_audio.assert_not_awaited()
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
                    assert profile.get('playback_threshold') == (.30 if model == 'xiaoya.onnx' else None)
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
