"""Bounded MCP duck calls on the chat codec; never open a second sound device."""
import asyncio
import array
from collections import deque
import concurrent.futures
from functools import lru_cache
import json
import math
import os
import sys
import time

KINDS = ('alarm', 'greet', 'inquire', 'peck', 'chirp', 'coo', 'wheee')
MAX_DEFERRED_BYTES = 256 * 1024


@lru_cache(maxsize=24)
def sound_pcm(kind, identity, variant, rate, gain_db):
    from vendor.microduck_voice import render, pcm16, seed_from_id
    return pcm16(render(kind, seed_from_id(identity), variant), rate, max(0, gain_db))


def amplify_sound(pcm, gain=1., headroom_db=0.):
    """Multiply quiet PCM, saturating before the existing product output gain."""
    if isinstance(gain, bool) or not isinstance(gain, (int, float)) or not math.isfinite(gain) or not 1 <= gain <= 3:
        raise ValueError('sound gain must be 1–3')
    if gain == 1:
        return pcm
    samples = array.array('h')
    samples.frombytes(pcm)
    if sys.byteorder != 'little': samples.byteswap()
    reserve = 10 ** (-max(0., headroom_db) / 20)
    low, high = math.ceil(-32768 * reserve), math.floor(32767 * reserve)
    out = array.array('h', (max(low, min(high, round(x * gain))) for x in samples))
    if sys.byteorder != 'little': out.byteswap()
    return out.tobytes()


class DuckSoundPlayer:
    def __init__(self, app, settings, render=sound_pcm):
        self.app, self.settings, self.render = app, settings, render
        self.task = None
        self.playing = False
        self.replaying = False
        self.deferred = deque()
        self.deferred_size = 0
        self.variants = dict.fromkeys(KINDS, 0)
        self.last = {'state': 'idle'}

    def call(self, kind):
        """Synchronous MCP/IoT executor entry; audio/state work stays on the app loop."""
        self.validate(kind)
        loop = self.app.loop
        if loop is None or not loop.is_running():
            raise ValueError('Chat audio is not running. 聊天音频尚未就绪。')
        try:
            if asyncio.get_running_loop() is loop:
                raise ValueError('Duck.Quack must run in the MCP executor')
        except RuntimeError:
            pass  # Expected: the MCP executor has no event loop.
        future = asyncio.run_coroutine_threadsafe(self.play(kind), loop)
        try:
            return future.result(timeout=4.5)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise ValueError('Duck sound timed out; playback cancelled. 鸭叫超时已取消。') from None
        except concurrent.futures.CancelledError:
            raise ValueError('Duck sound interrupted. 鸭叫已中止。') from None

    @staticmethod
    def validate(kind):
        if not isinstance(kind, str) or kind not in KINDS:
            raise ValueError('kind must be ' + '/'.join(KINDS))

    def defer(self, event, *args):
        """Temporarily hold early TTS packets in order, with a hard memory bound."""
        if not self.playing:
            return False
        size = len(args[0]) if event == 'audio' else len(json.dumps(args[0]).encode())
        if self.deferred_size + size > MAX_DEFERRED_BYTES or len(self.deferred) >= 512:
            self.cancel('interrupted by incoming speech', replay=True)
            return False
        self.deferred.append((event, args))
        self.deferred_size += size
        return True

    def _release(self, replay):
        self.playing = False
        events, self.deferred = self.deferred, deque()
        self.deferred_size = 0
        if replay and not self.app._closing:
            self.replaying = True
            try:
                for event, args in events:
                    if event == 'tts':
                        self.app._handle_tts(*args)
                    else:
                        self.app._on_incoming_audio(*args)
            finally:
                self.replaying = False

    def cancel(self, reason='interrupted', replay=False):
        if self.replaying:
            return
        if self.task is not None and not self.task.done():
            self.last = dict(self.last, state='cancelled', reason=reason)
            if self.playing:
                self.app.audio.clear_play_queue()
            self.task.cancel()
        self._release(replay)

    async def play(self, kind, *, software_gain=1.):
        self.validate(kind)
        if self.task is not None and not self.task.done():
            raise ValueError('Duck sound busy; try again afterwards. 鸭叫播放中，请稍后再试。')
        app, audio = self.app, self.app.audio
        if app._closing or not app.config.get('audio_available', True) or not audio:
            raise ValueError('USB speaker unavailable. 未连接可用的 USB 扬声器。')
        self.task = asyncio.current_task()
        started = time.monotonic()
        capture = bool(getattr(audio, '_capture_enabled', False))
        play_state = None
        wake = getattr(app, 'wakeword', None)
        wake_running = False
        ticket = None
        completed = False
        self.last = dict(state='preparing', kind=kind)
        try:
            variant = self.variants[kind]
            count = 12 if kind in ('greet', 'chirp') else 6 if kind == 'wheee' else 10
            gain = self.settings.get()['output_gain_db'] if self.settings is not None else 0
            pcm = await asyncio.to_thread(self.render, kind, os.environ.get('DUCK_DEVICE_ID', 'XGO-Duck'),
                                          variant, audio.output_rate, gain)
            pcm = amplify_sound(pcm, software_gain, gain)
            # An existing answer/cue keeps the device. Never clear it to make room.
            until = time.monotonic() + 1.
            # SPEAKING also covers waiting for this MCP result, before the
            # server has sent any speech. Only the codec knows whether PCM,
            # decoding or a DAC tail actually owns the speaker.
            while not audio.is_playback_idle() and time.monotonic() < until:
                await asyncio.sleep(.02)
            if app._closing or app.state not in ('idle', 'listening', 'speaking') or not audio.is_playback_idle():
                raise ValueError('Chat speaker busy; try after the answer. 请等当前语音播放结束再鸭叫。')
            wake_running = bool(wake and getattr(wake, '_running', False))
            if wake_running:
                await asyncio.to_thread(wake.stop)
            capture = bool(getattr(audio, '_capture_enabled', False))
            play_state = app.state
            audio.set_capture_enabled(False)  # Local calls must not become new ASR input.
            self.playing = True
            ticket = audio.queue_local_pcm(pcm)
            self.variants[kind] = (variant + 1) % count
            self.last = dict(state='playing', kind=kind, variant=variant, software_gain=software_gain,
                             duration_s=len(pcm) / (2 * audio.output_rate))
            deadline = min(started + 4., time.monotonic() + self.last['duration_s'] + 1.)
            while True:
                if not audio.local_pcm_current(ticket):
                    raise ValueError('Duck sound interrupted by audio reset. 音频重置，鸭叫已中止。')
                if audio.is_playback_idle():
                    break
                if time.monotonic() > deadline:
                    raise ValueError('USB speaker did not finish playback. USB 扬声器播放超时。')
                await asyncio.sleep(.02)
            completed = True
            self.last = dict(self.last, state='completed', elapsed_s=round(time.monotonic() - started, 3))
            print('[duck-sound]', json.dumps(self.last), flush=True)
            return dict(status='success', sound=dict(self.last), message='Duck call playback drained; not spoken TTS.')
        except asyncio.CancelledError:
            if self.last['state'] != 'cancelled':
                self.last = dict(self.last, state='cancelled', reason='chat stopped or playback interrupted')
            raise
        except Exception as exc:
            self.last = dict(self.last, state='error', reason=str(exc))
            raise
        finally:
            if ticket is not None and not completed and self.playing and audio.local_pcm_current(ticket):
                audio.clear_play_queue()
            self.task = None
            self._release(replay=completed)
            if not app._closing:
                # Replay can already have switched the app to SPEAKING. Do not
                # reopen capture over a non-AEC answer or restart its wake listener.
                if app.state == play_state:
                    audio.set_capture_enabled(capture)
                if wake_running and app.state == 'idle':
                    wake.start()
