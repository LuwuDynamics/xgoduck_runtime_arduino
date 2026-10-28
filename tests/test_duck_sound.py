"""Synth bounds, real MCP routing and local-sound/chat concurrency."""
import asyncio
import json
import threading
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'python/vendor/luwu_os/apps/voice_chat')]
from duck_sound import DuckSoundPlayer, KINDS, MAX_DEFERRED_BYTES
from duck_thing import DuckThing
from iot.thing import ThingManager
from mcp_server import McpServer
from voice_application import application_type

try:
    import numpy as np
except ImportError:
    np = None


@unittest.skipIf(np is None, 'NumPy is installed in the target userspace')
class DuckSynthTest(unittest.TestCase):
    def test_all_variants_are_finite_bounded_and_faded(self):
        from vendor.microduck_voice import render, VARIANTS, SR
        for seed in (0, 100, 3405691582):
            for kind in KINDS:
                for variant in range(VARIANTS[kind]):
                    with self.subTest(seed=seed, kind=kind, variant=variant):
                        samples = render(kind, seed, variant)
                        self.assertEqual(samples.dtype, np.float32)
                        self.assertTrue(np.isfinite(samples).all())
                        self.assertTrue(1000 < len(samples) <= 2 * SR)
                        self.assertTrue(.3 < float(np.abs(samples).max()) < .72)
                        self.assertEqual(float(samples[0]), 0)
                        self.assertEqual(float(samples[-1]), 0)

    def test_device_voice_and_variants_are_repeatable_and_distinct(self):
        from vendor.microduck_voice import render, seed_from_id
        self.assertEqual(seed_from_id('test-serial'), 0xc96f1146)
        a = render('greet', seed_from_id('duck-a'), 0)
        np.testing.assert_array_equal(a, render('greet', seed_from_id('duck-a'), 0))
        for other in (render('greet', seed_from_id('duck-b'), 0), render('greet', seed_from_id('duck-a'), 1)):
            self.assertFalse(np.array_equal(a, other))

    def test_resampling_rejects_aliases_and_reserves_output_gain_headroom(self):
        from vendor.microduck_voice import pcm16, render, SR
        t = np.arange(SR) / SR
        low = np.frombuffer(pcm16(.6 * np.sin(2 * np.pi * 1000 * t), 16000), '<i2').astype(float)
        high = np.frombuffer(pcm16(.6 * np.sin(2 * np.pi * 12000 * t), 16000), '<i2').astype(float)
        self.assertEqual(len(low), 16000)
        self.assertLess(np.std(high[100:-100]), np.std(low[100:-100]) * .01)
        for rate in (8000, 16000, 24000, 48000):
            for gain in (0, 6, 12):
                raw = render('greet', 100)
                pcm = np.frombuffer(pcm16(raw, rate, gain), '<i2').astype(float)
                self.assertLessEqual(abs(len(pcm) - len(raw) * rate / SR), 1)
                self.assertLess(float(np.max(np.abs(pcm))) * 10 ** (gain / 20), 23200)

    def test_invalid_tags_variants_and_rates_do_not_render(self):
        from vendor.microduck_voice import render, pcm16
        for kind in ('unknown', '../greet', '', None, [], 1):
            with self.assertRaises(ValueError):
                render(kind, 100)
        for variant in (-1, True, 12, 1.5):
            with self.assertRaises(ValueError):
                render('greet', 100, variant)
        with self.assertRaises(ValueError):
            pcm16(np.zeros(480), 44100)


class FakeAudio:
    def __init__(self):
        self.output_rate = 16000
        self.aec_enabled = False
        self._capture_enabled = True
        self.generation = 0
        self.idle = True
        self.queued = []
        self.clear_count = 0

    def is_playback_idle(self):
        return self.idle

    def set_capture_enabled(self, value):
        self._capture_enabled = value

    def queue_local_pcm(self, pcm):
        self.queued.append(pcm)
        self.idle = False
        return self.generation

    def local_pcm_current(self, generation):
        return generation == self.generation

    def clear_play_queue(self):
        self.generation += 1
        self.clear_count += 1
        self.idle = True


class FakeSDK:
    def __init__(self, config):
        self.config, self._closing = config, False
        self.state, self.audio = 'listening', FakeAudio()
        self.wakeword = Mock(_running=False)
        self.loop = asyncio.get_running_loop()
        self.events = []

    def _set_state(self, state):
        self.state = state

    def _handle_tts(self, data):
        self.events.append(('tts', data['state']))
        if data['state'] == 'start':
            self._set_state('speaking')

    def _on_incoming_audio(self, payload, timestamp):
        self.events.append(('audio', payload, timestamp))

    def _on_incoming_json(self, data):
        self.events.append(('json', data))

    async def abort_speaking(self):
        self.audio.clear_play_queue()
        self._set_state('listening')

    async def shutdown(self):
        self._closing = True
        self.audio.clear_play_queue()


class DuckSoundPlaybackTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = Mock()
        self.runtime.config.get.return_value = {'output_gain_db': 6}
        self.app = application_type(FakeSDK, self.runtime)({'audio_available': True})
        self.app._duck_session = 101
        self.player = self.app.duck_sounds
        self.player.render = Mock(return_value=b'\0\1' * 1600)

    async def playing(self, kind='greet', callback=None):
        task = asyncio.create_task(self.player.play(kind) if callback is None else asyncio.to_thread(callback, kind))
        for _ in range(500):
            if self.player.playing:
                return task
            if task.done():
                await task  # Surface the actual setup error.
            await asyncio.sleep(.002)
        self.fail('local sound did not start')

    async def test_executor_mcp_waits_for_dac_completion_and_uses_existing_gain(self):
        with patch.dict('os.environ', DUCK_DEVICE_ID='stable-factory-id'):
            task = await self.playing(callback=self.app.mcp_quack)
        self.assertFalse(self.app.audio._capture_enabled)
        self.assertFalse(task.done())
        self.player.render.assert_called_once_with('greet', 'stable-factory-id', 0, 16000, 6)
        self.app.audio.idle = True
        result = await task
        self.assertEqual(result['status'], 'success')
        self.assertEqual(result['sound']['state'], 'completed')
        self.assertTrue(self.app.audio._capture_enabled)
        self.assertEqual(self.app.audio.clear_count, 0)
        self.assertIsNone(self.player.task)

    async def test_connection_cue_tripled_gain_reuses_codec_and_capture_guard(self):
        task = asyncio.create_task(self.player.play('wheee', software_gain=3.))
        for _ in range(200):
            if self.player.playing: break
            await asyncio.sleep(.002)
        self.assertTrue(self.player.playing)
        self.assertFalse(self.app.audio._capture_enabled)
        self.assertEqual(int.from_bytes(self.app.audio.queued[0][:2], 'little', signed=True), 768)
        self.app.audio.idle = True
        result = await task
        self.assertEqual(result['sound']['software_gain'], 3.)
        self.assertTrue(self.app.audio._capture_enabled)

    async def test_early_tts_and_audio_resume_in_order_after_call_and_then_animate_mouth(self):
        task = await self.playing()
        self.runtime.mouth.begin.assert_not_called()
        self.app._handle_tts({'state': 'start'})
        self.app._on_incoming_audio(b'first', 1)
        self.app._handle_tts({'state': 'stop'})
        self.assertEqual(self.app.events, [])
        self.assertEqual(self.app.state, 'listening')
        self.app.audio.idle = True
        result = await task
        self.assertEqual(result['sound']['state'], 'completed')
        self.assertEqual(self.app.events, [('tts', 'start'), ('audio', b'first', 1), ('tts', 'stop')])
        self.runtime.mouth.begin.assert_called_once_with(101)
        self.assertEqual(self.app.state, 'speaking')
        self.assertFalse(self.app.audio._capture_enabled)
        self.assertEqual(self.app.audio.clear_count, 0)

    async def test_duplicate_call_is_rejected_without_touching_first_playback(self):
        task = await self.playing()
        with self.assertRaisesRegex(ValueError, 'busy'):
            await self.player.play('coo')
        self.assertEqual(len(self.app.audio.queued), 1)
        self.app.audio.idle = True
        await task
        self.assertEqual(self.app.audio.clear_count, 0)

    async def test_early_aec_reply_from_idle_keeps_its_new_capture_state(self):
        self.app.state = 'idle'
        self.app.audio._capture_enabled = False
        self.app.audio.aec_enabled = True
        original = self.app._set_state
        def state_with_aec(state):
            original(state)
            if state == 'speaking':
                self.app.audio.set_capture_enabled(True)
        self.app._set_state = state_with_aec
        task = await self.playing()
        self.app._handle_tts({'state': 'start'})
        self.app.audio.idle = True
        await task
        self.assertEqual(self.app.state, 'speaking')
        self.assertTrue(self.app.audio._capture_enabled)

    async def test_existing_answer_is_never_cleared_or_overlapped(self):
        self.app.state, self.app.audio.idle = 'speaking', False
        with self.assertRaisesRegex(ValueError, 'busy'):
            await self.player.play('greet')
        self.assertFalse(self.app.audio.queued)
        self.assertEqual(self.app.audio.clear_count, 0)

    async def test_mcp_during_speaking_without_audio_plays_and_replays_the_server_reply(self):
        # The server sends tts:start before tools/call, then waits for its result.
        self.app._handle_tts({'state': 'start'})
        self.app.audio._capture_enabled = False
        task = await self.playing(callback=self.app.mcp_quack)
        self.assertEqual(self.app.state, 'speaking')
        self.app._on_incoming_audio(b'reply', 30)
        self.app._handle_tts({'state': 'stop'})
        self.assertEqual(self.app.events, [('tts', 'start')])
        self.app.audio.idle = True
        result = await task
        self.assertEqual(result['sound']['state'], 'completed')
        self.assertEqual(self.app.events, [('tts', 'start'), ('audio', b'reply', 30), ('tts', 'stop')])
        self.assertFalse(self.app.audio._capture_enabled)
        self.assertEqual(self.app.audio.clear_count, 0)

    async def test_quack_waits_for_actual_audio_to_drain_in_speaking_state(self):
        self.app.state, self.app.audio.idle = 'speaking', False
        task = asyncio.create_task(self.player.play('chirp'))
        await asyncio.sleep(.05)
        self.assertFalse(self.app.audio.queued)
        self.app.audio.idle = True
        for _ in range(250):
            if self.player.playing or task.done(): break
            await asyncio.sleep(.002)
        self.assertTrue(self.player.playing)
        self.app.audio.idle = True
        self.assertEqual((await task)['sound']['state'], 'completed')
        self.assertEqual(self.app.audio.clear_count, 0)

    async def test_normal_chat_transition_while_rendering_does_not_cancel_pending_quack(self):
        entered, release = threading.Event(), threading.Event()
        def render(*_):
            entered.set()
            if not release.wait(2): raise RuntimeError('renderer not released')
            return b'\0\1' * 1600
        self.player.render = render
        self.app.state = 'speaking'
        task = asyncio.create_task(self.player.play('chirp'))
        try:
            for _ in range(250):
                if entered.is_set(): break
                await asyncio.sleep(.002)
            self.assertTrue(entered.is_set())
            self.app._set_state('listening')
            release.set()
            for _ in range(250):
                if self.player.playing or task.done(): break
                await asyncio.sleep(.002)
            self.assertTrue(self.player.playing)
            self.app.audio.idle = True
            self.assertEqual((await task)['sound']['state'], 'completed')
        finally:
            release.set()
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def test_session_ending_while_rendering_still_cancels_without_playing(self):
        entered, release = threading.Event(), threading.Event()
        def render(*_):
            entered.set()
            if not release.wait(2): raise RuntimeError('renderer not released')
            return b'\0\1' * 1600
        self.player.render = render
        task = asyncio.create_task(self.player.play('chirp'))
        try:
            for _ in range(250):
                if entered.is_set(): break
                await asyncio.sleep(.002)
            self.assertTrue(entered.is_set())
            self.app._set_state('idle')
            release.set()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertFalse(self.app.audio.queued)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_no_usb_and_bad_kind_return_errors_before_rendering(self):
        self.app.config['audio_available'] = False
        with self.assertRaisesRegex(ValueError, 'USB'):
            await self.player.play('greet')
        for kind in (None, [], 'not-a-tag'):
            with self.assertRaisesRegex(ValueError, 'kind'):
                await self.player.play(kind)
        self.player.render.assert_not_called()

    async def test_reset_and_timeout_are_not_reported_as_completed(self):
        task = await self.playing()
        self.app.audio.clear_play_queue()
        with self.assertRaisesRegex(ValueError, 'reset'):
            await task
        self.assertEqual(self.player.last['state'], 'error')
        task = await self.playing()
        with self.assertRaisesRegex(ValueError, '超时'):
            await task
        self.assertEqual(self.player.last['state'], 'error')
        self.assertEqual(self.app.audio.clear_count, 2)

    async def test_abort_or_new_user_speech_drops_deferred_reply(self):
        for action in ('abort', 'stt'):
            with self.subTest(action=action):
                task = await self.playing()
                self.app._handle_tts({'state': 'start'})
                self.app._on_incoming_audio(b'stale', 1)
                if action == 'abort':
                    await self.app.abort_speaking()
                else:
                    self.app._on_incoming_json({'type': 'stt', 'text': 'stop'})
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(self.player.deferred)
                self.assertNotIn(('audio', b'stale', 1), self.app.events)
                self.assertFalse(self.player.playing)

    async def test_shutdown_cancels_local_audio_without_reopening_capture_or_wake(self):
        self.app.state, self.app.wakeword._running = 'idle', True
        task = await self.playing()
        await self.app.shutdown()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.app.wakeword.stop.assert_called_once()
        self.app.wakeword.start.assert_not_called()
        self.assertFalse(self.app.audio._capture_enabled)

    async def test_idle_call_pauses_wake_and_restores_it_after_playback(self):
        self.app.state, self.app.wakeword._running = 'idle', True
        task = await self.playing()
        self.app.wakeword.stop.assert_called_once()
        self.app.wakeword.start.assert_not_called()
        self.app.audio.idle = True
        await task
        self.app.wakeword.start.assert_called_once()

    async def test_deferred_speech_has_a_hard_memory_bound_and_takes_over_on_overflow(self):
        task = await self.playing()
        self.app._handle_tts({'state': 'start'})
        self.app._on_incoming_audio(b'x' * MAX_DEFERRED_BYTES, 1)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.player.playing)
        self.assertEqual(self.player.deferred_size, 0)
        self.assertEqual(self.app.events[0], ('tts', 'start'))
        self.assertEqual(self.app.events[-1][0], 'audio')
        self.assertEqual(self.app.state, 'speaking')


class DuckSoundMcpTest(unittest.TestCase):
    def test_quack_uses_audio_callback_without_motion_permission_or_ipc(self):
        play = Mock(return_value={'status': 'success', 'sound': {'state': 'completed'}})
        settings = Mock()
        settings.get.return_value = {'voice_motion_enabled': False}
        manager = ThingManager()
        manager.add_thing(DuckThing(0, 'unused', settings=settings, sound_player=play))
        mcp = McpServer(manager)
        with patch('duck_thing.request') as motion:
            result = mcp.handle_message({'id': 1, 'method': 'tools/call',
                        'params': {'name': 'Duck.Quack', 'arguments': {'kind': 'greet'}}})['result']
        self.assertFalse(result['isError'])
        self.assertEqual(json.loads(result['content'][0]['text'])['sound']['state'], 'completed')
        motion.assert_not_called()
        play.assert_called_once_with('greet')
        settings.update.assert_not_called()

    def test_mcp_error_is_readable_to_existing_cloud_and_call_runs_in_executor(self):
        manager = ThingManager()
        manager.add_thing(DuckThing(0, 'unused', sound_player=DuckSoundPlayer.validate))
        mcp = McpServer(manager)
        self.assertTrue(mcp._is_blocking_tool('Duck.Quack'))
        result = mcp.handle_message({'id': 1, 'method': 'tools/call',
                    'params': {'name': 'Duck.Quack', 'arguments': {'kind': 'bad'}}})['result']
        self.assertTrue(result['isError'])
        self.assertIn('kind must be', result['error'])
        self.assertIn('greet', result['content'][0]['text'])
