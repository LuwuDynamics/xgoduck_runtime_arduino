import asyncio
import importlib.util
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'python/vendor/luwu_os'


@unittest.skipUnless(platform.system() == 'Linux' and platform.machine() == 'aarch64', 'Target Linux ARM64 userspace check')
class LinuxSDKTest(unittest.TestCase):
    def test_wake_mel_matches_pcm16_training_input_for_quiet_capture(self):
        script = '''
import numpy as np
from wakeword_manager import WakeWordManager
m = WakeWordManager()
assert m._load_model()
t = np.arange(32000, dtype=np.float32) / 16000
quiet = (290 + 11*np.sin(2*np.pi*173*t) + 7*np.sin(2*np.pi*1403*t)).astype(np.int16)
rails = np.resize(np.array([-32768,32767], dtype=np.int16), 32000)
for raw in (quiet, rails):
    normalized = raw.astype(np.float32)/32768
    if raw is rails:
        normalized[1::2] = 1.0  # PortAudio positive full-scale must not wrap.
    before = normalized.copy()
    # Reference is upstream AudioFeatures: PCM16 cast to float32, then ONNX.
    reference = m._model['mel_session'].run(None, {
        m._model['mel_input']:raw.astype(np.float32)[None,:]})[0].squeeze()/10 + 2
    actual = m._compute_mel(normalized)
    np.testing.assert_allclose(actual, reference, rtol=1e-5, atol=1e-6)
    np.testing.assert_array_equal(normalized, before)
    assert np.isfinite(actual).all()
'''
        env = dict(os.environ, LUWU_ROOT=str(VENDOR))
        env['PYTHONPATH'] = str(VENDOR / 'apps/ai')
        result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wake_sound_precedes_cloud_and_capture_even_when_cloud_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            script = '''
import asyncio, threading
from unittest.mock import AsyncMock, Mock
from application import Application, DeviceState, DING_WAV

async def check(scenario):
    app = Application({'enable_robot':False, 'enable_camera':False})
    app.loop = asyncio.get_running_loop()
    audio = app.audio = Mock(aec_enabled=False)
    app.wakeword = Mock(_running=False)
    cue_started, cue_finished = threading.Event(), threading.Event()
    cloud_started, cloud_finished = asyncio.Event(), asyncio.Event()
    def drain(timeout):
        assert timeout <= 2
        cue_started.set()
        assert cue_finished.wait(2)
        return True
    audio.wait_playback_done.side_effect = drain
    async def connect():
        audio.play_wav.assert_called_once_with(DING_WAV)
        assert cue_finished.is_set()
        cloud_started.set()
        await cloud_finished.wait()
        return scenario != 'failed'
    app.protocol = Mock()
    app.protocol.is_audio_channel_opened.return_value = False
    app.protocol.open_audio_channel = AsyncMock(side_effect=connect)
    app.protocol.send_start_listening = AsyncMock()
    app.protocol.shutdown = AsyncMock()
    def capture():
        assert cue_finished.is_set() and cloud_finished.is_set()
    audio.open_input.side_effect = capture
    task = app._spawn(app._handle_wakeword())
    try:
        while not cue_started.is_set():
            await asyncio.sleep(.001)
        assert app.state == DeviceState.CONNECTING
        audio.play_wav.assert_called_once_with(DING_WAV)
        app.protocol.open_audio_channel.assert_not_called()
        audio.open_input.assert_not_called()
        if scenario == 'shutdown':
            await app.shutdown()
            cue_finished.set()
            assert task.cancelled()
            app.protocol.open_audio_channel.assert_not_called()
            audio.open_input.assert_not_called()
            return
        cue_finished.set()
        await asyncio.wait_for(cloud_started.wait(), 1)
        assert not task.done()
        audio.open_input.assert_not_called()
        cloud_finished.set()
        await asyncio.wait_for(task, 1)
        audio.play_wav.assert_called_once_with(DING_WAV)
        if scenario == 'failed':
            assert app.state == DeviceState.IDLE
            audio.open_input.assert_not_called()
            app.wakeword.start.assert_called_once()
        else:
            assert app.state == DeviceState.LISTENING
            audio.open_input.assert_called_once()
            app.protocol.send_start_listening.assert_awaited_once()
    finally:
        cue_finished.set()
        cloud_finished.set()
        await app.shutdown()

async def main():
    for scenario in ('connected', 'failed', 'shutdown'):
        await asyncio.wait_for(check(scenario), 5)
asyncio.run(main())
'''
            env = dict(os.environ, LUWU_ROOT=str(VENDOR), DUCK_VOICE_STATE_DIR=directory,
                       DUCK_LANGUAGE_FILE=directory + '/language.ini', DUCK_DEVICE_ID='02:00:00:00:00:01')
            env['PYTHONPATH'] = os.pathsep.join([str(VENDOR), str(VENDOR / 'apps/voice_chat')])
            result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_real_headless_sdk_keeps_mcp_without_audio_and_reports_no_display(self):
        with tempfile.TemporaryDirectory() as directory:
            script = '''
import asyncio, os
from unittest.mock import Mock, patch
from voice_config import VoiceConfig
from voice_process import Runtime
from no_audio import NoAudio
from mcp_server import McpServer
class Onboard:
    bound = True
    mqtt = {'endpoint':'fixture'}
class Protocol:
    def __init__(self, *args):
        self._mqtt_connected = True
        self.open_audio_channel = Mock()
    async def start(self): pass
    async def shutdown(self): pass
    def force_stop_mqtt(self): pass
async def check():
    config = VoiceConfig(os.environ['DUCK_DATA_DIR']+'/config.json')
    config.update({'enable_camera':False})
    runtime = Runtime('fixture', 0, config, onboarding=Onboard())
    app = runtime.build_application([])
    calls = []
    def ipc(port, token, path, body):
        calls.append(body)
        return {'status':'accepted'}
    with patch('application.MqttUdpProtocol', Protocol), patch('audio_codec.sd.RawOutputStream') as output, \\
         patch('audio_codec.sd.RawInputStream') as capture, patch('duck_thing.request', side_effect=ipc):
        assert await app.start()
        assert isinstance(app.audio, NoAudio)
        assert app.bound is True and app.protocol._mqtt_connected
        output.assert_not_called()
        capture.assert_not_called()
        assert app.mcp_screen_get_info()['present'] is False
        info = app.mcp_get_system_info()
        assert info['screen'] == {'present':False,'width':0,'height':0}
        assert info['audio']['available'] is False
        reply = app.mcp.handle_message({'id':1,'method':'tools/call', 'params':{
            'name':'Duck.EnableRemoteMotion','arguments':{'enabled':True}}})
        assert not reply['result']['isError'] and config.get()['remote_motion_enabled']
        assert not config.get()['voice_motion_enabled']
        reply = app.mcp.handle_message({'id':2,'method':'tools/call', 'params':{
            'name':'Duck.RemoteMove','arguments':{'direction':'left','speed':40,'seconds':.7,
                'controller_id':'phone_fixture_A','sequence':1}}})
        assert not reply['result']['isError']
        assert calls[-1]['remote'] is True and calls[-1]['twist'] == [0,0,.4]
        for direction, twist in [('forward', [.15,0,0]), ('backward', [-.15,0,0]),
                                 ('left', [0,0,1.]), ('right', [0,0,-1.])]:
            reply = app.mcp.handle_message({'id':3,'method':'tools/call', 'params':{
                'name':'Duck.RemoteMove','arguments':{'direction':direction,'speed':100,'seconds':.7,
                    'controller_id':'phone_fixture_A','sequence':2}}})
            assert not reply['result']['isError'] and calls[-1]['twist'] == twist
        await app.start_chat()
        app.protocol.open_audio_channel.assert_not_called()
        await app.shutdown()
asyncio.run(check())
'''
            env = dict(os.environ, LUWU_ROOT=str(VENDOR), DUCK_DATA_DIR=directory,
                DUCK_VOICE_STATE_DIR=directory, DUCK_LANGUAGE_FILE=directory + '/language.ini',
                DUCK_DEVICE_ID='02:12:34:56:AB:CD', DUCK_APP_VERSION_FILE=str(ROOT / 'version.txt'))
            env['PYTHONPATH'] = os.pathsep.join([str(ROOT / 'python'), str(VENDOR), str(VENDOR / 'apps/voice_chat')])
            result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_native_media_bundle_and_real_sdk(self):
        spec = importlib.util.spec_from_file_location('native_bundle', ROOT / 'tools/prepare-ai-native.py')
        native = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(native)
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            native.prepare(folder)
            bundle = folder / 'data/native'
            env = dict(os.environ, DUCK_NATIVE_LIB_DIR=str(bundle / 'lib'), LD_LIBRARY_PATH=str(bundle / 'lib'),
                       PATH=str(bundle / 'bin') + os.pathsep + os.environ['PATH'])
            for name in ('arecord', 'aplay', 'amixer', 'v4l2-ctl'):
                result = subprocess.run([str(bundle / 'bin' / name), '--version'], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([sys.executable, str(ROOT / 'tools/check-ai-sdk.py')], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_sdk_session_opens_and_closes_selected_audio_without_cloud(self):
        # Real SDK, Opus and AEC; replace only physical streams and the backend.
        with tempfile.TemporaryDirectory() as directory:
            env = dict(LUWU_ROOT=str(VENDOR), DUCK_VOICE_STATE_DIR=directory,
                       DUCK_LANGUAGE_FILE=directory + '/language.ini', DUCK_DEVICE_ID='02:00:00:00:00:01')
            script = '''
import asyncio, os, sys
from unittest.mock import Mock, patch
from application import Application
class Protocol:
    def __init__(self, *args): pass
    async def start(self): pass
    async def shutdown(self): pass
    def force_stop_mqtt(self): pass
async def check():
    app = Application(dict(enable_robot=False, enable_camera=False, enable_wakeword=False,
        ambient_sound={'enabled':False}, audio={'input_device':11,'output_device':22}))
    with patch('application.OtaManager.provision', return_value={'endpoint':'mock'}), \\
         patch('application.MqttUdpProtocol', Protocol), \\
         patch('audio_codec.sd.RawOutputStream') as output, \\
         patch('audio_codec.sd.RawInputStream') as capture:
        assert await app.start()
        assert output.call_args.kwargs['device'] == 22
        app.audio.open_input()
        assert capture.call_args.kwargs['device'] == 11
        app.state = 'listening'
        app.wakeword = Mock()
        app._listen_timeout_task = app._spawn(app._listen_timeout())
        await asyncio.sleep(0)
        pending = list(app._tasks)
        await app.shutdown()
        assert all(task.done() for task in pending)
        assert app._listen_timeout_task is None
        app._back_to_idle()
        app.wakeword.start.assert_not_called()
        output.return_value.close.assert_called()
        capture.return_value.close.assert_called()
        assert app._ota_stop.is_set()
asyncio.run(check())
'''
            env = dict(os.environ, **env)
            env['PYTHONPATH'] = os.pathsep.join([str(VENDOR), str(VENDOR / 'apps/voice_chat')])
            result = subprocess.run([sys.executable, '-c', script], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
