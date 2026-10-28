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
                     'Target Linux ARM64 audio SDK check')
class AudioPlaybackTest(unittest.TestCase):
    def test_local_call_requires_idle_output_and_tracks_reset_without_new_stream(self):
        self.check('''
from unittest.mock import Mock, patch
import time
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec()
try:
    codec._running = True
    codec.output_stream = Mock(active=True)
    pcm = b'\\x10\\x01' * 960
    ticket = codec.queue_local_pcm(pcm)
    assert codec.local_pcm_current(ticket)
    assert not codec.is_playback_idle()
    try:
        codec.queue_local_pcm(pcm)
        raise AssertionError('second call must not overlap')
    except ValueError:
        pass
    output = bytearray(1920)
    codec._output_callback(output, 960, Mock(outputBufferDacTime=10.54, currentTime=10.), None)
    assert bytes(output) == pcm
    assert not codec.is_playback_idle(), 'DAC tail is still audible'
    codec.clear_play_queue()
    assert not codec.local_pcm_current(ticket)
    assert codec.is_playback_idle()
    for invalid in (b'', b'x', bytes(16000 * 4 + 2)):
        try:
            codec.queue_local_pcm(invalid)
            raise AssertionError('invalid PCM accepted')
        except ValueError:
            pass
    codec.output_stream.active = False
    try:
        codec.queue_local_pcm(pcm)
        raise AssertionError('disconnected stream accepted')
    except ValueError:
        pass
finally:
    codec.close()
''')

    def check(self, source):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, LUWU_ROOT=str(VENDOR), DUCK_DATA_DIR=directory,
                       DUCK_VOICE_STATE_DIR=directory + '/private',
                       PYTHONPATH=os.pathsep.join(map(str, (ROOT / 'python', VENDOR / 'apps/voice_chat', VENDOR))))
            result = subprocess.run([sys.executable, '-W', 'error::ResourceWarning', '-c', source],
                                    env=env, capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_requested_hardware_latency_reaches_output_stream(self):
        self.check('''
from unittest.mock import Mock, patch
import asyncio
from audio_codec import AudioCodec
from application import Application
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec(output_latency=.5, prebuffer_ms=1000)
try:
    with patch('audio_codec.sd.RawOutputStream') as factory:
        codec.start()
        assert factory.call_args.kwargs['latency'] == .5
        assert factory.call_args.kwargs['blocksize'] == 960
finally:
    codec.close()
class Boundary(Exception): pass
async def check():
    app = Application({'enable_robot':False, 'enable_camera':False,
                       'audio':{'output_latency':.5, 'prebuffer_ms':1000}})
    with patch('application.AudioCodec', side_effect=Boundary) as factory:
        try: await app.start()
        except Boundary: pass
        else: raise AssertionError('Audio construction was not reached')
        assert factory.call_args.kwargs['output_latency'] == .5
        assert factory.call_args.kwargs['prebuffer_ms'] == 1000
asyncio.run(check())
''')

    def test_real_sdk_tts_start_then_mcp_quack_plays_before_the_deferred_opus_reply(self):
        self.check('''
import asyncio
from types import SimpleNamespace
from unittest.mock import Mock, patch
import opuslib
from application import Application
from audio_codec import AudioCodec
from voice_application import application_type
async def check():
    with patch('audio_codec.LocalAEC'):
        audio = AudioCodec(prebuffer_ms=60)
    audio._running = True
    audio.aec_enabled = False
    audio.output_stream = Mock(active=True, latency=.5)
    runtime = SimpleNamespace(config=SimpleNamespace(get=lambda:{'output_gain_db':0}), mouth=None)
    app = application_type(Application, runtime)({'enable_robot':False, 'enable_camera':False})
    app.audio, app.loop, app._duck_session = audio, asyncio.get_running_loop(), 1
    app.duck_sounds.render = lambda *_: b'\\x10\\x01' * 1600
    task = None
    try:
        app._handle_tts({'state':'start'})
        assert app.state == 'speaking' and audio.is_playback_idle()
        task = asyncio.create_task(asyncio.to_thread(app.mcp_quack, 'chirp'))
        deadline = app.loop.time() + 2
        while not app.duck_sounds.playing and not task.done() and app.loop.time() < deadline:
            await asyncio.sleep(.002)
        assert app.duck_sounds.playing, 'MCP was falsely rejected as speaking/busy'
        packet = opuslib.Encoder(16000,1,opuslib.APPLICATION_AUDIO).encode(bytes(1920),960)
        app._on_incoming_audio(packet, 60)
        assert audio.play_queue.empty(), 'server reply must wait for the local sound'
        out = bytearray(3200)
        audio._output_callback(out,1600,None,None)
        assert bytes(out) == b'\\x10\\x01' * 1600
        result = await asyncio.wait_for(task,2)
        assert result['sound']['state'] == 'completed'
        assert audio.play_queue.qsize() == 1, 'deferred server Opus was lost'
        assert audio.play_queue.get_nowait()[1] == packet
    finally:
        if task is not None and not task.done():
            app.duck_sounds.cancel('test cleanup')
            await asyncio.gather(task, return_exceptions=True)
        await app.shutdown()
asyncio.run(check())
''')

    def test_realtime_callback_never_waits_for_console_output(self):
        self.check('''
from unittest.mock import patch
import time
import sounddevice as sd
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec(output_latency=.24)
try:
    codec._running = True
    pcm = b'\\x01\\x00' * 960
    codec._play_pcm_buf = pcm
    codec._opus_fed = True
    codec._underrun_start = time.time() - .2
    output = bytearray(1920)
    with patch('builtins.print', side_effect=AssertionError('Console I/O on real-time callback')):
        codec._output_callback(output, 960, None, sd.CallbackFlags(4))
    assert bytes(output) == pcm
    state = codec.playback_status()
    assert state['output_underflows'] == 1 and state['pcm_starvations'] == 1
    assert state['prebuffer_ms'] == 300 and state['output_callbacks'] == 1
    with patch('builtins.print') as log:
        codec._drain_stream_events()
        assert log.call_count == 2
finally:
    codec.close()
''')

    def test_playback_boost_reaches_speaker_and_aec_reference_without_callback_io(self):
        self.check('''
from pathlib import Path
from unittest.mock import Mock, patch
import array, os
from audio_codec import AudioCodec
from audio_devices import Volume
from voice_config import VoiceConfig
config = VoiceConfig(Path(os.environ['DUCK_DATA_DIR']) / 'voice.json')
config.update({'volume':100, 'input_gain_db':6, 'output_gain_db':6.020599913279624})
cards = [dict(card='1', output=True, input=True, key='usb:test', id='Test')]
volume = Volume(config, cards=lambda:cards, run=Mock())
volume.apply(100)
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec(output_latency=.5, prebuffer_ms=1000)
try:
    codec.output_transform = volume.pcm
    codec._running = True
    pcm = array.array('h', [1000,-1000,20000,-20000] * 240).tobytes()
    codec._play_pcm_buf = pcm
    out = bytearray(1920)
    with patch.object(config, 'get', side_effect=AssertionError('Config I/O in callback')), \\
         patch('subprocess.run', side_effect=AssertionError('Mixer I/O in callback')):
        codec._output_callback(out, 960, None, None)
    expected = [2000,-2000,32767,-32768] * 240
    assert list(array.array('h', out)) == expected
    assert codec._far_pending[-1][-1] == bytes(out)
    assert config.get()['input_gain_db'] == 6
    assert codec.prebuffer_frames == 17 and codec.output_latency == .5
finally:
    codec.close()
''')

    def test_stream_diagnostics_are_bounded_and_count_suppressed_errors(self):
        self.check('''
from unittest.mock import patch
import sounddevice as sd
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec()
try:
    for _ in range(100):
        codec._status_log_ts = 0
        codec._log_stream_status('输出', sd.CallbackFlags(4))
    codec._log_stream_status('采集', sd.CallbackFlags(2))
    state = codec.playback_status()
    assert state['output_underflows'] == 100 and state['input_overflows'] == 1
    assert len(codec._stream_events) <= 32
    codec.clear_play_queue()
    assert codec.playback_status()['output_underflows'] == 100
    assert codec.is_playback_idle()
finally:
    codec.close()
''')

    def test_product_mouth_receives_actual_output_and_dac_time_outside_callback(self):
        self.check('''
from types import SimpleNamespace
from unittest.mock import Mock, patch
import array
from audio_codec import AudioCodec
from speech_mouth import SpeechMouthPublisher
send = Mock(return_value={'ok':True})
mouth = SpeechMouthPublisher(send, clock=lambda:100.)
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec(output_latency=.5, prebuffer_ms=1000)
try:
    codec.output_observer = mouth.observe
    codec.output_reset_observer = mouth.stop
    codec.output_transform = lambda pcm: bytes(len(pcm))  # A muted speaker.
    codec._running = True
    pcm = array.array('h', [2000,-2000] * 480).tobytes()
    codec._play_pcm_buf = pcm
    mouth.begin(101)
    out = bytearray(1920)
    with patch('audio_codec.time.monotonic', return_value=100.), \\
         patch('speech_mouth.MouthEnvelope.analyse', side_effect=AssertionError('math in callback')):
        codec._output_callback(out, 960,
            SimpleNamespace(outputBufferDacTime=10.54, currentTime=10.), None)
    send.assert_not_called()
    assert bytes(out) == codec._far_pending[-1][-1] == bytes(1920)
    mouth.process_once()
    points = send.call_args.args[0]['points']
    assert [round(t,2) for t,_ in points] == [100.54,100.56,100.58]
    assert all(v == 0 for _,v in points)
    mouth.frames.clear()
    codec._output_callback(out, 960, None, None)  # Underflow is not speech.
    assert not mouth.frames
finally:
    codec.close()
''')

    def test_send_cue_surviving_tts_start_stays_audible_without_mouth_motion(self):
        self.check('''
import asyncio, array
from types import SimpleNamespace
from unittest.mock import Mock, patch
from application import Application
from audio_codec import AudioCodec
from speech_mouth import SpeechMouthPublisher
from voice_application import application_type
async def check():
    mouth = SpeechMouthPublisher(Mock(return_value={'ok':True}), clock=lambda:100.)
    with patch('audio_codec.LocalAEC'):
        audio = AudioCodec()
    runtime = SimpleNamespace(config=SimpleNamespace(get=lambda:{'output_gain_db':0}), mouth=mouth)
    app = application_type(Application, runtime)({'enable_robot':False, 'enable_camera':False})
    app.audio, app.loop, app._duck_session = audio, asyncio.get_running_loop(), 101
    audio._running = True
    audio.output_observer, audio.output_reset_observer = mouth.observe, mouth.stop
    try:
        app.state = 'listening'  # Do not open real capture hardware.
        app._on_incoming_json({'type':'stt', 'text':'test'})
        cue = audio._play_pcm_buf
        assert any(cue), 'the real send.wav was not queued'
        app._handle_tts({'state':'start'})
        assert app.state == 'speaking' and audio._play_pcm_buf == cue
        voice = array.array('h', [12000,-12000] * 1920).tobytes()
        audio._play_pcm_buf += voice
        speaker = cue + voice
        observed = bytes(len(cue)) + voice
        angles = []
        for offset in range(0, len(speaker), 1920):
            out = bytearray(1920)
            with patch('audio_codec.time.monotonic', return_value=100.+offset/32000):
                audio._output_callback(out, 960,
                    SimpleNamespace(outputBufferDacTime=10.54, currentTime=10.), None)
            assert bytes(out) == speaker[offset:offset+1920].ljust(1920, b'\\0')
            assert audio._far_pending[-1][-1] == bytes(out), 'AEC must hear the actual cue'
            assert mouth.frames[-1][1] == observed[offset:offset+1920].ljust(1920, b'\\0'), \\
                'cue samples reached the TTS mouth observer'
            mouth.process_once()
            angles.extend(mouth.send.call_args.args[0]['points'])
        cue_end = 100.54 + len(cue)/32000
        assert all(a == 0 for t,a in angles if t+.02 <= cue_end)
        assert any(a > 0 for t,a in angles if t >= cue_end), 'speech no longer moves the mouth'
    finally:
        await app.shutdown()
asyncio.run(check())
''')

    def test_cue_boundaries_mask_only_their_samples_across_output_callbacks(self):
        self.check('''
import array, tempfile, wave
from unittest.mock import Mock, patch
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec()
try:
    codec._running = True
    codec.output_observer = Mock()
    before = array.array('h', [1000,-1000,1000,-1000,1000]).tobytes()
    cue = array.array('h', [9000,-9000] * 5 + [9000]).tobytes()
    after = array.array('h', [2000,-2000] * 4 + [2000]).tobytes()
    with tempfile.NamedTemporaryFile(suffix='.wav') as f:
        with wave.open(f.name,'wb') as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(cue)
        codec._play_pcm_buf = before
        codec.play_wav(f.name)
    codec._play_pcm_buf += after
    speaker = before + cue + after
    mouth = before + bytes(len(cue)) + after
    for offset in range(0,len(speaker),16):
        out = bytearray(16)
        codec._output_callback(out,8,None,None)
        assert bytes(out) == speaker[offset:offset+16].ljust(16,b'\\0')
        assert codec._far_pending[-1][-1] == bytes(out)
        assert codec.output_observer.call_args.args[0] == mouth[offset:offset+16].ljust(16,b'\\0')
finally:
    codec.close()
''')

    def test_local_call_and_flush_never_leak_cue_mouth_targets_into_speech(self):
        self.check('''
from unittest.mock import Mock, patch
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec()
try:
    codec._running = True
    codec.output_stream = Mock(active=True)
    codec.output_observer = Mock()
    codec.queue_local_pcm(b'\\x10\\x01' * 1920)
    out = bytearray(1920)
    codec._output_callback(out,960,None,None)
    assert any(out) and codec.output_observer.call_args.args[0] == bytes(1920)
    codec.clear_play_queue(clear_pcm=False)
    codec._output_callback(out,960,None,None)
    assert any(out) and codec.output_observer.call_args.args[0] == bytes(1920)
    codec.clear_play_queue()
    codec.queue_local_pcm(b'\\x10\\x01' * 1920)
    codec._output_callback(out,960,None,None)
    codec.clear_play_queue()  # Discard the remaining cue and its mask together.
    speech = b'\\x20\\x02' * 960
    codec._play_pcm_buf = speech
    codec._output_callback(out,960,None,None)
    assert bytes(out) == codec.output_observer.call_args.args[0] == speech
finally:
    codec.close()
''')

    def test_clear_pcm_cancels_future_mouth_but_tts_start_preserves_it(self):
        self.check('''
from unittest.mock import Mock, patch
from audio_codec import AudioCodec
from speech_mouth import SpeechMouthPublisher
mouth = SpeechMouthPublisher(Mock())
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec()
try:
    codec.output_reset_observer = mouth.stop
    mouth.begin(101)
    mouth.observe(bytes(1920), 16000, 100.54)
    codec.clear_play_queue(clear_pcm=False)
    assert mouth.context is not None and len(mouth.frames) == 1
    codec.clear_play_queue()
    assert mouth.context is None and not mouth.frames
    mouth.process_once()
    assert mouth.send.call_args.args[0]['points'] == []
finally:
    codec.close()
''')

    def test_invalid_latency_fails_before_opening_audio(self):
        self.check('''
from unittest.mock import patch
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC') as aec:
    for value in (True, False, float('nan'), float('inf'), .01, .61, 'high'):
        try: AudioCodec(output_latency=value)
        except ValueError: pass
        else: raise AssertionError(value)
    aec.assert_not_called()
''')

    def test_one_second_prebuffer_and_short_answer_flush(self):
        self.check('''
from unittest.mock import patch
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec(prebuffer_ms=1000)
try:
    packet = codec.opus_encoder.encode(b'\\0'*1920, 960)
    for _ in range(16): codec.write_opus(packet)
    assert codec.play_queue.empty() and not codec.is_playback_idle()
    codec.write_opus(packet)
    assert codec.play_queue.qsize() == 17
    assert codec.playback_status()['prebuffer_ms'] == 1020
    codec.clear_play_queue()
    codec.write_opus(packet); codec.write_opus(packet)
    codec.stop_plc()
    assert codec.play_queue.qsize() == 2 and codec._prebuf is None
    codec.clear_play_queue()
    codec.update_server_params(frame_duration=20)
    assert codec.prebuffer_frames == 50
finally:
    codec.close()
''')

    def test_completion_waits_for_hardware_tail_and_stop_flushes_it(self):
        self.check('''
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sounddevice as sd
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec(output_latency=.5)
try:
    codec._running = True
    codec.output_stream = Mock()
    codec.output_stream.latency = .5
    codec._play_pcm_buf = b'\\x01\\0'*960
    with patch('audio_codec.time.monotonic', return_value=100.):
        codec._output_callback(bytearray(1920),960,
            SimpleNamespace(outputBufferDacTime=14.5,currentTime=14.),sd.CallbackFlags())
        assert not codec.is_playback_idle()
        assert codec.playback_status()['hardware_tail_ms'] == 560.
        codec.clear_play_queue(clear_pcm=False)
        codec.output_stream.abort.assert_not_called()
        codec.clear_play_queue()
        codec.output_stream.abort.assert_called_once()
        assert codec.is_playback_idle()
finally:
    codec.close()
''')

    def test_decoder_finishing_after_stop_cannot_restore_old_audio(self):
        self.check('''
from unittest.mock import patch
import threading,time
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec(prebuffer_ms=60)
entered,release=threading.Event(),threading.Event()
def decode(*_):
    entered.set()
    assert release.wait(2)
    return b'\\x01\\0'*960
codec.opus_decoder.decode=decode
try:
    codec._running=True
    worker=threading.Thread(target=codec._play_loop)
    worker.start()
    codec.write_opus(b'old-packet')
    assert entered.wait(2)
    assert not codec.is_playback_idle()
    codec.clear_play_queue();release.set()
    until=time.monotonic()+2
    while codec._decode_active and time.monotonic()<until:time.sleep(.01)
    assert codec._play_pcm_buf==b'' and codec.is_playback_idle()
finally:
    release.set();codec._running=False;worker.join(2);codec.close()
''')

    def test_prebuffer_and_stop_are_safe_when_called_concurrently(self):
        self.check('''
from unittest.mock import patch
import threading
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec(prebuffer_ms=1000)
errors=[]
def feed():
    try:
        for _ in range(150):codec.write_opus(b'packet')
    except Exception as error:errors.append(error)
def stop():
    try:
        for _ in range(150):codec.stop_plc();codec.clear_play_queue()
    except Exception as error:errors.append(error)
try:
    workers=[threading.Thread(target=fn) for fn in (feed,stop)]
    for worker in workers:worker.start()
    for worker in workers:worker.join(3);assert not worker.is_alive()
    assert not errors, errors
    codec.clear_play_queue();assert codec.is_playback_idle()
finally:
    codec.close()
''')

    def test_invalid_prebuffer_fails_before_opening_audio(self):
        self.check('''
from unittest.mock import patch
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC') as aec:
    for value in (True, float('nan'), float('inf'), 1, 1501, '1000'):
        try: AudioCodec(prebuffer_ms=value)
        except ValueError: pass
        else: raise AssertionError(value)
    aec.assert_not_called()
''')


if __name__ == '__main__':
    unittest.main()
