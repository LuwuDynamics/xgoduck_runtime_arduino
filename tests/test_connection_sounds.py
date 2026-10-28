import asyncio
from pathlib import Path
import struct
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'python'))
from connection_sounds import ConnectionSounds
from voice_config import VoiceConfig
from voice_process import Runtime
from duck_sound import amplify_sound


class ConnectionSoundTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.now = 10.
        self.play = AsyncMock(return_value=True)
        self.cues = ConnectionSounds(self.play, clock=lambda: self.now)
        self.addAsyncCleanup(self.cues.close)

    async def poll(self, network, voice):
        self.cues.poll(network, voice)
        if self.cues.task: await self.cues.task

    async def test_real_success_is_ordered_once_per_runtime_and_not_repeated_on_poll_or_reconnect(self):
        await self.poll(False, False)
        await self.poll(None, False)
        self.play.assert_not_called()
        await self.poll(True, False)
        await self.poll(True, True)
        for _ in range(5):
            await self.poll(False, False)
            await self.poll(True, True)
        self.assertEqual(self.play.call_args_list, [unittest.mock.call('network', 'greet', 3.),
                                                   unittest.mock.call('voice_service', 'wheee', 3.)])
        self.assertEqual(self.cues.status()['completed'], ['network', 'voice_service'])

    async def test_confirmed_cloud_can_prove_network_but_not_reverse(self):
        await self.poll(None, True)
        await self.poll(None, True)
        self.assertEqual([c.args[0] for c in self.play.call_args_list], ['network', 'voice_service'])

    async def test_missing_audio_retries_later_without_claiming_success(self):
        self.play.return_value = False
        await self.poll(True, True)
        self.assertEqual(self.cues.status()['completed'], [])
        self.assertEqual(self.cues.status()['state'], 'waiting_audio')
        await self.poll(True, True)
        self.assertEqual(self.play.call_count, 1)
        self.now += 5.
        self.play.return_value = True
        await self.poll(True, True)
        await self.poll(True, True)
        self.assertEqual(self.cues.status()['completed'], ['network', 'voice_service'])

    async def test_failed_playback_does_not_become_a_success_cue(self):
        self.play.side_effect = OSError('no device')
        await self.poll(True, True)
        self.assertEqual(self.cues.status()['completed'], [])
        self.assertEqual(self.cues.status()['error'], 'OSError')
        self.now += 5
        self.play.side_effect = None
        await self.poll(False, False)
        self.assertEqual(self.play.call_count, 1)

    async def test_slow_audio_is_single_owner_and_cancellable(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def slow(*args):
            entered.set()
            await release.wait()
        self.cues.play = slow
        self.cues.poll(True, True)
        await entered.wait()
        first = self.cues.task
        for _ in range(10): self.cues.poll(True, True)
        self.assertIs(self.cues.task, first)
        self.assertTrue(self.cues.network_busy)
        await self.cues.close()
        self.assertTrue(first.cancelled())
        self.assertEqual(self.cues.status()['completed'], [])


class OfflineConnectionSoundTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cfg = VoiceConfig(Path(self.temp.name) / 'voice.json')
        cfg.update({'output_gain_db': 6})
        self.runtime = Runtime('test', 0, config=cfg, onboarding=SimpleNamespace(ready=False))
        self.runtime.gate = Mock()
        self.runtime.gate.busy.return_value = False
        self.runtime.bench = SimpleNamespace(lock=threading.Lock(), closed=False, audio=None, tts_busy=False)
        self.runtime.volume = Mock()
        self.runtime.volume.pcm.side_effect = lambda b: b
        self.cards = [dict(card='3', key='speaker', id='USB', output=True, input=False)]

    async def test_network_works_before_binding_with_only_a_usb_speaker_and_releases_audio_gate(self):
        pcm = struct.pack('<3h', 1000, -1000, 0)
        with patch('voice_process.usb_audio_cards', return_value=self.cards), \
             patch('duck_sound.sound_pcm', return_value=pcm) as render, \
             patch('voice_process.subprocess.run') as run:
            self.assertTrue(await self.runtime.play_connection_sound('network', 'greet', 3.))
        self.assertIsNone(self.runtime.app)
        self.assertEqual(render.call_args.args[0], 'greet')
        self.assertEqual(struct.unpack('<3h', run.call_args.kwargs['input']), (3000, -3000, 0))
        self.assertIn('plughw:3,0', run.call_args.args[0])
        self.runtime.gate.release.assert_called_once_with(self.runtime.gate.acquire.return_value)

    async def test_occupied_web_audio_and_no_speaker_never_open_another_stream(self):
        self.runtime.gate.busy.return_value = True
        with patch('voice_process.subprocess.run') as run:
            self.assertFalse(await self.runtime.play_connection_sound('network', 'greet', 3.))
            run.assert_not_called()
        self.runtime.gate.busy.return_value = False
        with patch('voice_process.usb_audio_cards', return_value=[]), patch('voice_process.subprocess.run') as run:
            with self.assertRaises(ValueError):
                await self.runtime.play_connection_sound('network', 'greet', 3.)
            run.assert_not_called()
        self.runtime.gate.acquire.assert_not_called()

    async def test_voice_success_does_not_start_an_offline_stream(self):
        with patch('voice_process.subprocess.run') as run:
            self.assertFalse(await self.runtime.play_connection_sound('voice_service', 'wheee', 3.))
            run.assert_not_called()


class CueGainTest(unittest.TestCase):
    def test_tripled_samples_saturate_with_existing_gain_headroom_and_never_wrap(self):
        raw = struct.pack('<5h', 1000, -1000, 30000, -30000, 0)
        out = struct.unpack('<5h', amplify_sound(raw, 3., 6.))
        self.assertEqual(out[:2], (3000, -3000))
        self.assertTrue(0 < out[2] * 10 ** .3 <= 32767)
        self.assertTrue(-32768 <= out[3] * 10 ** .3 < 0)
        self.assertEqual(out[4], 0)
        self.assertEqual(amplify_sound(raw), raw)
        for gain in (False, 0, 4, float('nan')):
            with self.assertRaises(ValueError): amplify_sound(raw, gain)


if __name__ == '__main__': unittest.main()
