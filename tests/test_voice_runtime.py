import asyncio
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from local_ipc import JsonServer
from voice_config import VoiceConfig
from voice_process import Runtime
import voice_media

CARDS = [dict(card='2', id='Mic', key='usb:mic', input=True, output=False),
         dict(card='3', id='Speaker', key='usb:speaker', input=False, output=True)]
REAL_POPEN = subprocess.Popen


class ReadyOnboarding:
    def __init__(self):
        self.ready, self.bound, self.generation = True, True, 0
        self.mqtt = {'endpoint': 'mock'}
        self.stop = asyncio.Event()

    def snapshot(self):
        return dict(state='ready' if self.ready else 'awaiting_binding', ready=self.ready, bound=self.bound)

    async def run(self):
        await self.stop.wait()


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


async def eventually(check, timeout=8):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        value = check()
        if value:
            return value
        await asyncio.sleep(.02)
    raise AssertionError('Chat lifecycle condition timed out')


class VoiceRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = VoiceConfig(Path(self.temp.name) / 'voice.json')
        self.onboarding = ReadyOnboarding()
        self.runtime = Runtime('lifecycle-test', 0, self.config, onboarding=self.onboarding)
        self.cards = list(CARDS)
        self.instances, self.open_sessions = [], 0
        self.beats = []
        self.ipc = JsonServer(0, 'lifecycle-test', lambda method, path, body: self.heartbeat(body))
        self.ipc.start()
        self.runtime.motion_port = self.ipc.server.server_port
        self.runtime.volume.cards = lambda: self.cards
        self.runtime.volume.run = Mock()
        self.signals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
        runtime, test = self.runtime, self
        class Chat:
            def __init__(self):
                self._duck_session = time.monotonic_ns()
                self._closing, self.bound = False, True
                test.instances.append(self)
            async def start(self):
                test.open_sessions += 1
                test.assertEqual(test.open_sessions, 1)
                return True
            async def shutdown(self):
                await asyncio.sleep(.01)
                self._closing = True
                test.open_sessions -= 1
        def audio(cmd, **kwargs):
            self.assertEqual(self.open_sessions, 0, 'Legacy ALSA opened before chat released its streams')
            self.assertTrue(runtime.gate.busy())
            script = 'import time; time.sleep(.05)'
            if cmd[0] == 'arecord':
                script = 'import os,time; os.write(1,b"\\0\\1"*480); time.sleep(5)'
            return REAL_POPEN([sys.executable, '-u', '-c', script], **kwargs)
        self.patches = [patch.dict(os.environ, DUCK_MEDIA_PORT=str(free_port()), DUCK_VOICE_PORT=str(free_port())),
            patch('voice_process.usb_audio_cards', side_effect=lambda: self.cards),
            patch('voice_media.usb_audio_cards', side_effect=lambda: self.cards),
            patch.object(runtime, 'build_application', side_effect=lambda cards: Chat()),
            patch.object(voice_media.legacy.doubao, 'load_config', return_value={}),
            patch('voice_media.subprocess.Popen', side_effect=audio)]
        for item in self.patches:
            item.start()
        self.task = asyncio.create_task(runtime.run())
        await eventually(lambda: runtime.app is not None and self.open_sessions == 1)

    def heartbeat(self, body):
        self.beats.append(body.get('session'))
        return {'ok': True}

    async def asyncTearDown(self):
        self.runtime.stop.set()
        try:
            await asyncio.wait_for(self.task, 5)
            self.assertEqual(self.open_sessions, 0)
            self.assertFalse(self.runtime.gate.busy())
        finally:
            for item in reversed(self.patches):
                item.stop()
            for sig, handler in self.signals.items():
                asyncio.get_running_loop().remove_signal_handler(sig)
                signal.signal(sig, handler)
            await asyncio.to_thread(self.ipc.close)
            self.temp.cleanup()

    async def test_repeated_web_playback_releases_and_resumes_chat_without_overlap(self):
        for source in ['music', 'duck'] * 3:
            previous = self.runtime.app
            proc = await asyncio.to_thread(self.runtime.bench.audio_start, 'playing', source=source)
            self.assertTrue(previous._closing)
            await asyncio.to_thread(proc.wait, 2)
            await eventually(lambda: self.runtime.app is not None and self.runtime.app is not previous and self.open_sessions == 1)
            self.assertFalse(self.runtime.gate.busy())
        self.assertEqual(len(self.instances), 7)
        self.assertIn(None, self.beats)

    async def test_connection_sounds_require_live_mqtt_not_cached_binding_and_preserve_heartbeats(self):
        self.runtime.connection_sounds.play = AsyncMock(return_value=True)
        self.onboarding.snapshot = lambda: dict(state='ready', ready=True, bound=True, wifi_connected=True)
        await eventually(lambda: self.runtime.connection_sounds.completed == {'network'})
        self.assertEqual(self.runtime.connection_sounds.play.call_count, 1)
        before = len(self.beats)
        self.runtime.app.protocol = SimpleNamespace(_mqtt_connected=True)
        await eventually(lambda: self.runtime.connection_sounds.completed == {'network', 'voice_service'})
        await eventually(lambda: len(self.beats) > before)
        self.assertEqual(self.runtime.connection_sounds.play.call_count, 2)
        self.assertEqual(self.open_sessions, 1)

    async def test_concurrent_listen_requests_preserve_single_owner_and_release_on_stop(self):
        results = await asyncio.gather(*(asyncio.to_thread(self.runtime.bench.audio_start, 'listening') for _ in range(2)), return_exceptions=True)
        errors = [r for r in results if isinstance(r, Exception)]
        procs = [r for r in results if not isinstance(r, Exception)]
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], ValueError)
        self.assertEqual(len(procs), 1)
        await asyncio.to_thread(self.runtime.bench.audio_stop)
        procs[0].stdout.close()
        await eventually(lambda: self.open_sessions == 1 and not self.runtime.gate.busy())

    async def test_usb_audio_removal_and_replug_rebuilds_chat_session(self):
        previous = self.runtime.app
        self.cards[:] = CARDS[:1]
        await eventually(lambda: previous._closing and self.open_sessions == 0, timeout=4)
        self.cards[:] = CARDS
        await eventually(lambda: self.runtime.app is not None and self.runtime.app is not previous and self.open_sessions == 1)
        self.assertNotEqual(previous._duck_session, self.runtime.app._duck_session)

    async def test_unbinding_stops_chat_and_withdraws_motion_then_binding_resumes(self):
        previous = self.runtime.app
        self.runtime.connection_sounds.play = AsyncMock(return_value=True)
        cached = True
        snapshot = self.onboarding.snapshot
        self.onboarding.snapshot = lambda: dict(snapshot(), wifi_connected=True, cached=cached)
        self.onboarding.ready = self.onboarding.bound = False
        self.onboarding.generation += 1
        await eventually(lambda: previous._closing and self.open_sessions == 0
                         and self.runtime.status()['state'] == 'awaiting_binding')
        self.assertEqual(self.runtime.status()['state'], 'awaiting_binding')
        self.assertIn(None, self.beats)
        await eventually(lambda: 'network' in self.runtime.connection_sounds.completed)
        self.assertNotIn('binding_required', self.runtime.connection_sounds.completed)
        cached = False
        await eventually(lambda: 'binding_required' in self.runtime.connection_sounds.completed)
        self.onboarding.ready = self.onboarding.bound = True
        self.onboarding.generation += 1
        await eventually(lambda: self.runtime.app is not None and self.open_sessions == 1)

    async def test_missing_audio_keeps_cloud_session_then_replug_reopens_audio(self):
        previous = self.runtime.app
        self.cards.clear()
        await eventually(lambda: self.runtime.app is not None and self.runtime.app is not previous
                         and self.runtime.status()['state'] == 'waiting_audio', timeout=10)
        self.assertEqual(self.open_sessions, 1, 'MCP must stay connected without USB sound devices')
        self.assertFalse(self.runtime.audio_ready)
        self.cards[:] = CARDS
        await eventually(lambda: self.runtime.audio_ready and self.open_sessions == 1, timeout=10)

    async def test_removed_selected_audio_device_keeps_mcp_available(self):
        previous = self.runtime.app
        self.config.update({'input_device': 'usb:removed-microphone'})
        await eventually(lambda: self.runtime.app is not None and self.runtime.app is not previous
                         and self.runtime.status()['state'] == 'waiting_audio')
        self.assertEqual(self.open_sessions, 1)
        self.assertFalse(self.runtime.audio_ready)

    async def test_motion_permission_changes_do_not_reconnect_chat_or_mcp(self):
        previous = self.runtime.app
        self.config.update({'remote_motion_enabled': True, 'voice_motion_enabled': True})
        await asyncio.sleep(.7)
        self.assertIs(self.runtime.app, previous)
        self.assertEqual(len(self.instances), 1)
        self.config.update({'remote_motion_enabled': False, 'voice_motion_enabled': False})
        await asyncio.sleep(.7)
        self.assertIs(self.runtime.app, previous)
        self.assertEqual(self.open_sessions, 1)


if __name__ == '__main__':
    unittest.main()
