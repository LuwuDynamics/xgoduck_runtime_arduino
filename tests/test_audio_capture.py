"""Real SDK capture scheduling, backpressure and near/far ordering without hardware."""
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
class AudioCaptureTest(unittest.TestCase):
    def test_dual_microphone_is_reduced_before_aec_and_opus_stays_mono(self):
        self.check('''
from unittest.mock import Mock, patch
import numpy as np
from audio_codec import AudioCodec
from capture_channels import CaptureChannels
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec(input_channels=2, input_latency=.32)
try:
    codec._running = codec._capture_enabled = True
    codec.input_stream = Mock()
    codec.input_downmix = CaptureChannels(2, 'right').pcm
    codec.aec = Mock(available=True, sub_frame_size=160)
    codec.aec_enabled = True
    codec.aec.process.side_effect = lambda near, far: near
    codec.opus_encoder.encode = Mock(return_value=b'opus')
    raw = np.column_stack((np.full(960, 100), np.full(960, 200))).astype('<i2').tobytes()
    codec._input_callback(raw, 960, None, None)
    codec.on_audio_frame = lambda _: setattr(codec, '_running', False)
    codec._capture_loop()
    mono = np.full(960, 200, dtype='<i2').tobytes()
    assert codec.aec.process.call_args.args[0] == mono
    codec.opus_encoder.encode.assert_called_once_with(mono, 960)
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

    def test_input_latency_is_optional_validated_and_reported_from_the_actual_stream(self):
        self.check('''
from unittest.mock import Mock, patch
from audio_codec import AudioCodec
for latency in (None, .2):
    with patch('audio_codec.LocalAEC'):
        codec = AudioCodec(input_latency=latency, output_latency=.5, prebuffer_ms=1000)
    try:
        with patch('audio_codec.sd.RawInputStream') as factory:
            factory.return_value.latency = .24
            codec.open_input()
            assert factory.call_args.kwargs['latency'] == latency
            assert factory.call_args.kwargs['blocksize'] == 960
            assert codec.playback_status()['input_latency_ms'] == 240.
            assert codec.prebuffer_frames == 17 and codec.output_latency == .5
    finally:
        codec.close()
for value in (True, 'high', -.1, .01, .61, float('nan'), float('inf')):
    with patch('audio_codec.LocalAEC') as aec:
        try: AudioCodec(input_latency=value)
        except ValueError as exc: assert 'input_latency' in str(exc)
        else: raise AssertionError('invalid input latency accepted')
        aec.assert_not_called()
''')

    def test_capture_callback_only_copies_bounded_blocks_even_when_worker_is_behind(self):
        self.check('''
from unittest.mock import Mock, patch
import sounddevice as sd
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec()
try:
    codec._running = codec._capture_enabled = True
    codec.aec = Mock(available=True)
    codec._far_seq = 100
    codec._far_pending.extend((i, 0, b'far') for i in range(1, 101))
    raw = bytearray(b'\\x01\\0' * 960)
    with patch.object(codec, '_drain_far', side_effect=AssertionError('draining on callback')), \\
         patch.object(codec, '_dump', side_effect=AssertionError('file I/O on callback')), \\
         patch.object(codec, '_push_aec_reference', side_effect=AssertionError('AEC on callback')), \\
         patch('builtins.print', side_effect=AssertionError('console I/O on callback')):
        for _ in range(40): codec._input_callback(raw, 960, None, sd.CallbackFlags(2))
    raw[:] = bytes(len(raw))
    assert len(codec._near_q) == 30 and len(codec._far_pending) == 100
    assert codec._near_q[0][1] == b'\\x01\\0' * 960
    state = codec.playback_status()
    assert state['input_callbacks'] == state['input_overflows'] == 40
    assert state['capture_queue_drops'] == 10 and state['capture_queue_peak_ms'] == 1800
    codec.aec.pop_reference.assert_not_called()
    codec.aec.calibrate.assert_not_called()
    with patch('builtins.print') as log:
        codec._drain_stream_events()
        assert 'capture=' in log.call_args.args[0]
finally:
    codec.close()
''')

    def test_worker_pairs_far_watermarks_in_order_and_preserves_future_reference(self):
        self.check('''
from unittest.mock import Mock, patch
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec()
events = []
try:
    codec._running = codec._capture_enabled = True
    codec.input_stream = Mock()
    codec.aec = Mock(available=True, sub_frame_size=160)
    codec.aec_enabled = True
    codec.aec.process.side_effect = lambda near, far: near
    codec._push_aec_reference = lambda pcm: events.append(('far', pcm[:2]))
    codec.opus_encoder.encode = lambda pcm, n: pcm
    def send(pcm):
        events.append(('near', pcm[:2]))
        if sum(tag == 'near' for tag, _ in events) == 2: codec._running = False
    codec.on_audio_frame = send
    for value in (1, 2, 3):
        codec._play_pcm_buf = bytes([value, 0]) * 960
        codec._output_callback(bytearray(1920), 960, None, None)
        if value != 3: codec._input_callback(bytes([value + 10, 0]) * 960, 960, None, None)
    codec._capture_loop()
    assert events == [('far', b'\\x01\\0'), ('near', b'\\x0b\\0'),
                      ('far', b'\\x02\\0'), ('near', b'\\x0c\\0')], events
    assert codec._far_held[0] == 3
    assert codec.aec.calibrate.call_count == 1 and codec.aec.process.call_count == 2
finally:
    codec.close()
''')

    def test_hardware_overflow_and_queue_loss_reanchor_aec_at_the_next_retained_block(self):
        self.check('''
from unittest.mock import Mock, patch
import sounddevice as sd
from audio_codec import AudioCodec
for loss in ('hardware', 'queue'):
    with patch('audio_codec.LocalAEC'):
        codec = AudioCodec()
    try:
        codec._running = codec._capture_enabled = True
        codec.input_stream = Mock()
        codec.aec = Mock(available=True, sub_frame_size=160)
        codec.aec_enabled = True
        codec.aec.process.side_effect = lambda near, far: near
        codec.opus_encoder.encode = lambda pcm, n: pcm
        raw = b'\\x01\\0' * 960
        codec._input_callback(raw, 960, None, None)
        codec.on_audio_frame = lambda _: setattr(codec, '_running', False)
        codec._capture_loop()
        assert codec.aec.calibrate.call_count == 1
        codec._running = True
        if loss == 'hardware':
            codec._input_callback(raw, 960, None, sd.CallbackFlags(2))
        else:
            for _ in range(35): codec._input_callback(raw, 960, None, None)
            assert codec.playback_status()['capture_queue_drops'] == 5
        codec._capture_loop()
        assert codec.aec.calibrate.call_count == 2, 'first retained frame must re-anchor'
    finally:
        codec.close()
''')

    def test_paused_or_closed_capture_cannot_send_an_inflight_old_turn(self):
        self.check('''
from unittest.mock import Mock, patch
from audio_codec import AudioCodec
for interruption in ('pause_resume', 'close'):
    with patch('audio_codec.LocalAEC'):
        codec = AudioCodec()
    try:
        codec.aec = Mock(available=False)
        codec._running = codec._capture_enabled = True
        codec.input_stream = Mock()
        codec.on_audio_frame = Mock()
        codec._input_callback(b'\\x01\\0' * 960, 960, None, None)
        def encode(*_):
            if interruption == 'close': codec.close_input()
            else:
                codec.set_capture_enabled(False)
                codec.set_capture_enabled(True)
            return b'old-opus'
        codec.opus_encoder.encode = encode
        with patch('audio_codec.time.sleep', side_effect=lambda _: setattr(codec, '_running', False)):
            codec._capture_loop()
        codec.on_audio_frame.assert_not_called()
    finally:
        codec.close()
''')

    def test_slow_encoder_does_not_hold_the_input_callback_and_queue_stays_bounded(self):
        self.check('''
from unittest.mock import Mock, patch
import threading, time
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec()
entered, release = threading.Event(), threading.Event()
def encode(*_):
    entered.set()
    assert release.wait(2)
    return b'opus'
worker = None
try:
    codec.aec = Mock(available=False)
    codec._running = codec._capture_enabled = True
    codec.input_stream = Mock()
    codec.on_audio_frame = Mock()
    codec.opus_encoder.encode = encode
    raw = b'\\x01\\0' * 960
    codec._input_callback(raw, 960, None, None)
    worker = threading.Thread(target=codec._capture_loop)
    worker.start()
    assert entered.wait(2)
    started = time.monotonic()
    for _ in range(35): codec._input_callback(raw, 960, None, None)
    assert time.monotonic() - started < .5
    assert len(codec._near_q) == 30 and codec._capture_queue_drops == 5
    codec.set_capture_enabled(False)
    codec._running = False
    release.set()
    worker.join(2)
    assert not worker.is_alive()
    codec.on_audio_frame.assert_not_called()
finally:
    codec._running = False
    release.set()
    if worker is not None: worker.join(2)
    codec.close()
''')

    def test_muted_capture_keeps_reference_processing_in_the_worker(self):
        self.check('''
from unittest.mock import Mock, patch
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec()
try:
    codec._running = True
    codec._capture_enabled = False
    codec.input_stream = Mock()
    codec.on_audio_frame = Mock()
    codec._play_pcm_buf = b'\\x01\\0' * 960
    codec._output_callback(bytearray(1920), 960, None, None)
    codec._input_callback(b'\\x02\\0' * 960, 960, None, None)
    assert codec._near_q[0][1] is None
    seen = []
    def reference(pcm):
        seen.append(pcm)
        codec._running = False
    codec._push_aec_reference = reference
    codec._capture_loop()
    assert seen == [b'\\x01\\0' * 960]
    codec.on_audio_frame.assert_not_called()
finally:
    codec.close()
''')

    def test_audio_reset_discards_held_reference_and_input_open_failure_closes_stream(self):
        self.check('''
from unittest.mock import Mock, patch
from audio_codec import AudioCodec
with patch('audio_codec.LocalAEC'):
    codec = AudioCodec()
try:
    codec._running = True
    codec._push_aec_reference = Mock()
    for full_reset in (False, True):
        codec._output_callback(bytearray(1920), 960, None, None)
        codec._drain_far(through=codec._far_seq - 1)
        assert codec._far_held is not None
        codec.clear_play_queue(clear_pcm=full_reset)
        codec._drain_far()
        codec._push_aec_reference.assert_not_called()
    with patch('audio_codec.sd.RawInputStream') as factory:
        factory.return_value.start.side_effect = RuntimeError('USB disconnected')
        codec.open_input()
        assert codec.input_stream is None
        factory.return_value.close.assert_called_once()
    assert codec.playback_status()['input_latency_ms'] is None
finally:
    codec.close()
''')


if __name__ == '__main__':
    unittest.main()
