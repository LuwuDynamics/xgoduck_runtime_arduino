import array
import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
import wave
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'python/vendor/luwu_os/apps/voice_chat')]
from voice_config import VoiceConfig
from audio_devices import CaptureGain, Volume, prepare_capture, select_audio, usb_audio_cards, aec_playback_transform
from voice_application import application_type
from voice_media import AudioGate, MediaBench
from mcp_server import McpServer
from iot.thing import ThingManager

CARDS = [dict(card='2', id='Mic', key='usb:mic', input=True, output=False),
         dict(card='3', id='Speaker', key='usb:speaker', input=False, output=True)]
REAL_POPEN = subprocess.Popen


class AudioSettingsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.config = VoiceConfig(Path(temp.name) / 'settings.json')

    def test_aec_output_headroom_is_opt_in_bounded_and_preserves_other_gains(self):
        self.assertEqual(self.config.get()['aec_output_headroom_db'], 0)
        self.config.update({'volume':100, 'output_gain_db':6, 'input_gain_db':3})
        for value in (0, 6, 12, 18, 3.5):
            self.config.update({'aec_output_headroom_db':value})
            saved = VoiceConfig(self.config.path).get()
            self.assertEqual(saved['aec_output_headroom_db'], value)
            self.assertEqual((saved['volume'], saved['output_gain_db'], saved['input_gain_db']), (100,6,3))
        for value in (True, None, -1, 18.1, '6', float('nan'), float('inf'), [], {}):
            with self.assertRaises(ValueError):
                self.config.update({'aec_output_headroom_db':value})
            with self.assertRaises(ValueError):
                aec_playback_transform(Mock(), value, lambda:True)
        self.assertEqual(self.config.get()['aec_output_headroom_db'], 3.5)

    def test_aec_headroom_combines_with_boost_before_clipping_and_leaves_other_audio_alone(self):
        self.config.update({'volume':100, 'output_gain_db':6.020599913279624})
        volume = Volume(self.config, cards=lambda:CARDS, run=Mock())
        volume.apply(100)
        state = dict(active=True)
        transform = aec_playback_transform(volume, 12.041199826559248, lambda:state['active'])
        raw = array.array('h', [-30000,-1000,0,1000,30000]).tobytes()
        self.assertEqual(list(array.array('h', transform(raw))), [-15000,-500,0,500,15000])
        self.assertEqual(list(array.array('h', volume.pcm(raw))), [-32768,-2000,0,2000,32767])
        state['active'] = False
        self.assertEqual(transform(raw), volume.pcm(raw))
        state['active'] = True
        self.assertEqual(aec_playback_transform(volume,0,lambda:True)(raw), volume.pcm(raw))
        self.assertEqual(self.config.get()['volume'], 100)
        self.assertEqual(self.config.get()['output_gain_db'], 6.020599913279624)
        self.assertEqual(list(array.array('h',raw)), [-30000,-1000,0,1000,30000])

    def test_aec_headroom_respects_live_mute_and_software_mixer_fallback(self):
        self.config.update({'output_gain_db':6.020599913279624})
        volume = Volume(self.config, cards=lambda:CARDS, run=Mock(side_effect=FileNotFoundError('amixer')))
        transform = aec_playback_transform(volume,12.041199826559248,lambda:True)
        raw = array.array('h',[-20000,0,20000]).tobytes()
        volume.apply(25)
        self.assertEqual(list(array.array('h',transform(raw))),[-2500,0,2500])
        volume.apply(0)
        self.assertEqual(transform(raw), bytes(len(raw)))

    def test_capture_auto_gain_opt_in_and_unsupported_control(self):
        run = Mock()
        self.assertIsNone(prepare_capture(CARDS[0], None, run))
        run.assert_not_called()
        self.assertTrue(prepare_capture(CARDS[0], True, run))
        self.assertEqual(run.call_args.args[0],
                         ['amixer', '-c', '2', 'sset', 'Auto Gain Control', 'on'])
        self.assertFalse(prepare_capture(CARDS[0], False, run))
        self.assertEqual(run.call_args.args[0][-1], 'off')
        for error in (FileNotFoundError('amixer'), subprocess.CalledProcessError(1, 'amixer')):
            self.assertIsNone(prepare_capture(CARDS[0], True, Mock(side_effect=error)))

    def test_capture_gain_configuration_is_typed_and_persistent(self):
        self.assertIsNone(self.config.get()['input_auto_gain'])
        for value in (True, False, None):
            self.config.update({'input_auto_gain': value})
            self.assertIs(VoiceConfig(self.config.path).get()['input_auto_gain'], value)
        self.config.update({'input_auto_gain': True})
        for value in (0, 1, 'on', [], {}):
            with self.assertRaises(ValueError):
                self.config.update({'input_auto_gain': value})
            self.assertTrue(self.config.get()['input_auto_gain'])

    def test_software_capture_gain_is_bounded_persistent_and_preserves_settings(self):
        self.assertEqual(self.config.get()['input_gain_db'], 0)
        self.config.update({'volume': 100, 'voice_motion_enabled': True})
        for value in (0, 3.5, 6, 12):
            self.config.update({'input_gain_db': value})
            saved = VoiceConfig(self.config.path).get()
            self.assertEqual(saved['input_gain_db'], value)
            self.assertEqual(saved['volume'], 100)
            self.assertTrue(saved['voice_motion_enabled'])
        for value in (True, None, '6', -1, 12.1, 10**400, float('nan'), float('inf'), [], {}):
            with self.assertRaises(ValueError):
                self.config.update({'input_gain_db': value, 'volume': 30})
            with self.assertRaises(ValueError):
                CaptureGain(value)
            self.assertEqual(self.config.get()['input_gain_db'], 12)
            self.assertEqual(self.config.get()['volume'], 100)
        self.assertEqual(self.config.path.stat().st_mode & 0o777, 0o600)

    def test_software_capture_gain_saturates_without_wrap_or_mutation(self):
        import numpy as np
        gain = CaptureGain(6.020599913279624)  # Exactly twice the amplitude.
        raw = np.array([-32768, -20000, -1000, 0, 1000, 20000, 32767], dtype='<i2')
        pcm, expected = raw.tobytes(), [-32768, -32768, -2000, 0, 2000, 32767, 32767]
        self.assertEqual(list(array.array('h', gain.pcm(pcm))), expected)
        floats = raw.astype(np.float32) / 32768
        floats.flags.writeable = False
        np.testing.assert_array_equal(gain.floats(floats), np.array(expected, dtype=np.float32) / 32768)
        np.testing.assert_array_equal(floats, raw.astype(np.float32) / 32768)
        self.assertEqual(pcm, raw.tobytes())
        self.assertEqual(gain.pcm(b''), b'')
        self.assertEqual(gain.floats(np.array([], dtype=np.float32)).size, 0)
        unity = CaptureGain()
        self.assertIs(unity.pcm(pcm), pcm)
        self.assertIs(unity.floats(floats), floats)

    def test_separate_usb_input_output_and_stable_device_selection(self):
        self.assertEqual(select_audio(CARDS, 'input')['card'], '2')
        self.assertEqual(select_audio(CARDS, 'output', 'usb:speaker')['card'], '3')
        self.assertEqual(select_audio([dict(CARDS[1], card='8')], 'output', 'usb:speaker')['card'], '8')
        with self.assertRaises(ValueError):
            select_audio(CARDS, 'input', 'Speaker')

    def test_detect_capture_and_playback_directions_from_proc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for number, label in ((2, 'Mic'), (3, 'Speaker')):
                folder = root / ('card' + str(number))
                folder.mkdir()
                (folder / 'id').write_text(label)
                (folder / 'usbid').write_text('1234:5678')
            (root / 'pcm').write_text('02-00: USB Audio : USB Audio : capture 1\n03-00: USB Audio : USB Audio : playback 1\n')
            cards = usb_audio_cards(root, root / 'sys')
            self.assertEqual([(c['input'], c['output']) for c in cards], [(True, False), (False, True)])

    def test_masked_proc_discovers_usb_sysfs_directions_and_stable_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            sysroot = base / 'class/sound'
            sysroot.mkdir(parents=True)
            (sysroot / 'card0').mkdir()  # Internal sound must remain excluded.
            for number, label, direction in ((2, 'Mic', 'c'), (3, 'Speaker', 'p')):
                usb = base / 'devices' / f'1-{number}'
                interface = usb / f'1-{number}:1.0'
                card = interface / 'sound' / f'card{number}'
                card.mkdir(parents=True)
                (usb / 'idVendor').write_text('1234')
                (usb / 'idProduct').write_text('5678')
                (usb / 'serial').write_text(label + '-serial')
                (card / 'id').write_text(label)
                (card / 'device').symlink_to(interface, target_is_directory=True)
                (sysroot / f'card{number}').symlink_to(card, target_is_directory=True)
                (sysroot / f'pcmC{number}D0{direction}').touch()
            cards = usb_audio_cards(base / 'masked-proc', sysroot)
            self.assertEqual([(c['input'], c['output']) for c in cards], [(True, False), (False, True)])
            mic = select_audio(cards, 'input')
            (sysroot / 'card2').rename(sysroot / 'card7')
            (sysroot / 'pcmC2D0c').rename(sysroot / 'pcmC7D0c')
            moved = select_audio(usb_audio_cards(base / 'masked-proc', sysroot), 'input', mic['key'])
            self.assertEqual(moved['card'], '7')
            self.assertEqual(moved['key'], 'usb:1234:5678:Mic-serial')

    def test_partial_disappeared_usb_card_does_not_break_enumeration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'card2').mkdir()
            (root / 'card2/usbid').write_text('1234:5678')
            self.assertEqual(usb_audio_cards(root, root / 'sys'), [])

    def test_mcp_volume_reaches_shared_setting_and_software_pcm(self):
        run = Mock(side_effect=FileNotFoundError('amixer'))
        volume = Volume(self.config, cards=lambda: CARDS, run=run)
        runtime = SimpleNamespace(config=self.config, volume=volume)
        app = application_type(type('SDK', (), {'__init__': lambda self, cfg: None}), runtime)({})
        mcp = McpServer(ThingManager(), self_provider=app)
        reply = mcp.handle_message({'id': 1, 'method': 'tools/call',
            'params': {'name': 'self.audio_speaker.set_volume', 'arguments': {'volume': 25}}})
        self.assertFalse(reply['result']['isError'])
        self.assertEqual(self.config.get()['volume'], 25)
        pcm = array.array('h', [-32768, -1000, 0, 1000, 32767]).tobytes()
        self.assertEqual(list(array.array('h', volume.pcm(pcm))), [-8192, -250, 0, 250, 8191])
        self.assertEqual(self.config.path.stat().st_mode & 0o777, 0o600)
        self.assertTrue(mcp._is_blocking_tool('self.audio_speaker.set_volume'))

    def test_hardware_volume_does_not_double_scale(self):
        volume = Volume(self.config, cards=lambda: CARDS, run=Mock())
        volume.apply(40)
        self.assertEqual(volume.software_gain, 100)
        self.assertEqual(volume.pcm(b'\x00\x40'), b'\x00\x40')
        for value in (True, None, -1, 101, 1.5, '60'):
            if value is None:
                continue  # None intentionally means use the saved volume.
            with self.assertRaises(ValueError):
                volume.apply(value)

    def test_playback_boost_preserves_microphone_gain_and_rejects_bad_values(self):
        self.assertEqual(self.config.get()['output_gain_db'], 0)
        self.config.update({'input_gain_db': 6, 'volume': 100, 'output_gain_db': 6})
        saved = VoiceConfig(self.config.path).get()
        self.assertEqual(saved['output_gain_db'], 6)
        self.assertEqual(saved['input_gain_db'], 6)
        for value in (True, None, '6', -1, 12.1, 10**400, float('nan'), float('inf'), [], {}):
            with self.assertRaises(ValueError):
                self.config.update({'output_gain_db': value, 'input_gain_db': 0})
            self.assertEqual(self.config.get()['input_gain_db'], 6)
            self.assertEqual(self.config.get()['output_gain_db'], 6)

    def test_playback_boost_saturates_combines_fallback_volume_and_keeps_mute(self):
        self.config.update({'output_gain_db': 6.020599913279624})
        pcm = array.array('h', [-32768, -20000, -1000, 0, 1000, 20000, 32767]).tobytes()
        original = bytes(pcm)
        volume = Volume(self.config, cards=lambda: CARDS, run=Mock())
        volume.apply(100)
        self.assertEqual(list(array.array('h', volume.pcm(pcm))),
                         [-32768, -32768, -2000, 0, 2000, 32767, 32767])
        volume.apply(40)
        self.assertEqual(volume.software_gain, 200)  # Mixer already applies 40%.
        volume.apply(0)
        self.assertEqual(volume.pcm(pcm), b'\0' * len(pcm))
        volume.run = Mock(side_effect=FileNotFoundError('amixer'))
        volume.apply(25)
        self.assertEqual(volume.software_gain, 50)
        self.assertEqual(list(array.array('h', volume.pcm(pcm))),
                         [-16384, -10000, -500, 0, 500, 10000, 16383])
        volume.apply(0)
        self.assertEqual(volume.pcm(pcm), b'\0' * len(pcm))
        self.assertEqual(pcm, original)

    def test_mcp_volume_is_saved_without_a_usb_speaker(self):
        run = Mock()
        volume = Volume(self.config, cards=lambda: [], run=run)
        runtime = SimpleNamespace(config=self.config, volume=volume)
        app = application_type(type('SDK', (), {'__init__': lambda self, cfg: None}), runtime)({})
        self.assertTrue(app.mcp_set_volume(33))
        self.assertEqual(VoiceConfig(self.config.path).get()['volume'], 33)
        self.assertEqual(volume.software_gain, 33)
        run.assert_not_called()

    def test_sdk_mqtt_refresh_uses_onboarding_instead_of_language_cache_and_ignores_closed_app(self):
        class SDK:
            def __init__(self, config):
                self.config, self._closing = config, False
        onboard = SimpleNamespace(bound=True, refresh_credentials=AsyncMock(return_value={'endpoint': 'fixture'}))
        app = application_type(SDK, SimpleNamespace(onboarding=onboard))({'ota_url': 'https://fixture.invalid/ota/'})
        self.assertEqual(asyncio.run(app._refresh_credentials()), {'endpoint': 'fixture'})
        onboard.refresh_credentials.assert_awaited_once_with('https://fixture.invalid/ota/')
        self.assertTrue(app.bound)
        app._closing = True
        onboard.refresh_credentials.reset_mock()
        self.assertIsNone(asyncio.run(app._refresh_credentials()))
        onboard.refresh_credentials.assert_not_called()

    def test_slow_mixer_command_does_not_block_realtime_pcm(self):
        entered, release, rendered = threading.Event(), threading.Event(), threading.Event()
        def slow_mixer(*args, **kwargs):
            entered.set()
            release.wait(3)
        volume = Volume(self.config, cards=lambda: CARDS, run=slow_mixer)
        apply = threading.Thread(target=volume.apply, args=(30,))
        result = []
        def callback():
            result.append(volume.pcm(array.array('h', [1000]).tobytes()))
            rendered.set()
        apply.start()
        self.assertTrue(entered.wait(1))
        render = threading.Thread(target=callback)
        render.start()
        try:
            self.assertTrue(rendered.wait(.3), 'Mixer I/O blocked the PortAudio playback callback')
            self.assertEqual(list(array.array('h', result[0])), [600])
        finally:
            release.set()
            apply.join(2)
            render.join(2)

    def test_config_validation_is_atomic(self):
        self.config.update({'volume': 20})
        for changes in ({'volume': 30, 'enabled': 'true'}, {'input_device': 1}, {'ota_url': 'http://wrong'}):
            with self.assertRaises(ValueError):
                self.config.update(changes)
            self.assertEqual(self.config.get()['volume'], 20)


class AudioHandoffTest(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.paused = threading.Event()
        async def pause():
            await asyncio.sleep(.02)
            self.paused.set()
        self.runtime = SimpleNamespace(loop=self.loop, pause_for_media=pause)
        self.gate = AudioGate(self.runtime)
        self.temp = tempfile.TemporaryDirectory()
        self.config = VoiceConfig(Path(self.temp.name) / 'settings.json')
        self.volume = Volume(self.config, cards=lambda: CARDS, run=Mock())
        self.bench = MediaBench(self.config, self.volume, self.gate)

    def tearDown(self):
        self.bench.close()
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(2)
        self.loop.close()
        self.temp.cleanup()

    def test_audio_opens_after_chat_releases_and_completion_releases_gate(self):
        self.config.update({'input_auto_gain': True})
        prepare = Mock()
        def capture(cmd, **kwargs):
            self.assertTrue(self.paused.is_set())
            self.assertEqual(cmd[cmd.index('-D') + 1], 'plughw:2,0')
            prepare.assert_called_once_with(CARDS[0], True)
            return REAL_POPEN([sys.executable, '-c', 'import time; time.sleep(.05)'], **kwargs)
        with patch('voice_media.usb_audio_cards', return_value=CARDS), \
             patch('voice_media.prepare_capture', prepare), \
             patch('voice_media.subprocess.Popen', side_effect=capture):
            proc = self.bench.audio_start('listening')
            self.assertTrue(self.gate.busy())
            proc.wait(timeout=2)
            until = time.monotonic() + 2
            while self.gate.busy() and time.monotonic() < until:
                time.sleep(.01)
            self.assertFalse(self.gate.busy())
            proc.stdout.close()

    def test_web_playback_boost_preserves_wav_format_and_original_file(self):
        self.config.update({'output_gain_db': 6.020599913279624})
        self.volume.apply(100)
        self.bench.dir = Path(self.temp.name) / 'media'
        self.bench.dir.mkdir()
        source = self.bench.dir / 'source.wav'
        pcm = array.array('h', [1000, -1000, 25000, -25000]).tobytes()
        with wave.open(str(source), 'wb') as dest:
            dest.setnchannels(2)
            dest.setsampwidth(2)
            dest.setframerate(48000)
            dest.writeframes(pcm)
        original = source.read_bytes()
        target = self.bench.playback_file(source)
        self.assertNotEqual(source, target)
        with wave.open(str(target), 'rb') as src:
            self.assertEqual((src.getnchannels(), src.getsampwidth(), src.getframerate()), (2, 2, 48000))
            self.assertEqual(list(array.array('h', src.readframes(src.getnframes()))), [2000, -2000, 32767, -32768])
        self.assertEqual(source.read_bytes(), original)

    def test_failed_open_or_cancelled_tts_releases_gate(self):
        with patch('voice_media.usb_audio_cards', return_value=[]):
            with self.assertRaises(ValueError):
                self.bench.audio_start('recording')
        self.assertFalse(self.gate.busy())
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(Exception):
            self.bench.audio_start('playing', cancel=cancel)
        self.assertFalse(self.gate.busy())


if __name__ == '__main__':
    unittest.main()
