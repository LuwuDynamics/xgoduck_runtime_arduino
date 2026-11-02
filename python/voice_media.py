"""Original media HTTP contract, hosted as threads in the chat process."""
import asyncio
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import wave
from app_paths import ROOT, PRIVATE
from audio_devices import prepare_capture, select_audio, usb_audio_cards

BENCH_DIR = ROOT / 'tools/device-test'
sys.path.insert(0, str(BENCH_DIR))
spec = importlib.util.spec_from_file_location('duck_media_bench', BENCH_DIR / 'server.py')
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class AudioGate:
    """Legacy audio has priority; wait for chat to release ALSA before opening it."""
    def __init__(self, runtime):
        self.runtime = runtime
        self.lock = threading.Lock()
        self.tokens = set()

    def acquire(self):
        token = object()
        with self.lock:
            self.tokens.add(token)
        future = asyncio.run_coroutine_threadsafe(self.runtime.pause_for_media(), self.runtime.loop)
        try:
            future.result(timeout=10)
        except Exception:
            future.cancel()
            self.release(token)
            raise ValueError('Chat is releasing audio; please retry.') from None
        return token

    def release(self, token):
        with self.lock:
            self.tokens.discard(token)

    def busy(self):
        with self.lock:
            return bool(self.tokens)


class MediaBench(legacy.Bench):
    def __init__(self, config, volume, gate):
        self.config, self.product_volume, self.gate = config, volume, gate
        self.audio_tokens = {}
        super().__init__()

    def camera_path(self):
        selected = self.config.get()['camera_device']
        return selected if selected and Path(selected).exists() else super().camera_path() if not selected else None

    def audio_card(self):
        return select_audio(usb_audio_cards(), 'output', self.config.get()['output_device'])['card']

    def read_volume(self, card):
        self.volume = self.config.get()['volume']
        return self.volume

    def set_volume(self, volume):
        self.product_volume.apply(volume)
        with self.lock:
            self.volume, self.volume_error = volume, ''

    def playback_file(self, path):
        if self.product_volume.software_gain == 100:
            return path
        with wave.open(str(path), 'rb') as src:
            params = src.getparams()
            data = self.product_volume.pcm(src.readframes(src.getnframes()))
        output = self.dir / 'gain-playback.wav'
        with wave.open(str(output), 'wb') as dest:
            dest.setparams(params)
            dest.writeframes(data)
        return output

    def audio_start(self, kind, volume=None, seconds=5, source='music', cancel=None):
        token = self.gate.acquire()  # never hold the Bench lock across chat shutdown
        try:
            with self.lock:
                if self.closed or (cancel is not None and cancel.is_set()):
                    raise legacy.doubao.Cancelled()
                if self.audio or self.tts_busy:
                    raise ValueError('Audio is busy. Stop playback or recording first.')
                direction = 'input' if kind in ('recording', 'listening') else 'output'
                selected = select_audio(usb_audio_cards(), direction, self.config.get()[direction + '_device'])
                card = selected['card']
                if direction == 'input':
                    prepare_capture(selected, self.config.get().get('input_auto_gain'))
                if kind == 'listening':
                    cmd = ['arecord', '-q', '-D', f'plughw:{card},0', '-t', 'raw', '-f', 'S16_LE',
                           '-r', '24000', '-c', '1', '--buffer-time=80000', '--period-time=20000', '-']
                elif kind == 'recording':
                    seconds = int(seconds)
                    if not 1 <= seconds <= 30:
                        raise ValueError('Recording duration must be 1 to 30 seconds.')
                    self.recording = None
                    cmd = ['arecord', '-D', f'plughw:{card},0', '-f', 'S16_LE', '-r', '48000',
                           '-c', '1', '-d', str(seconds), str(self.dir / 'recording.wav')]
                elif kind == 'playing':
                    names = {'music': 'music.wav', 'upload': 'upload.wav', 'recording': 'recording.wav', 'tts': 'tts.wav'}
                    sources = {name: self.dir / filename for name, filename in names.items()}
                    sources['duck'] = ROOT / 'python/assets/speaker-test.wav'
                    path = sources.get(source)
                    if path is None or not path.is_file():
                        raise ValueError('The selected audio source is not available.')
                    self.set_volume(self.config.get()['volume'] if volume is None else volume)
                    cmd = ['aplay', '-D', f'plughw:{card},0', str(self.playback_file(path))]
                else:
                    raise ValueError('Unknown audio operation.')
                log = tempfile.TemporaryFile()
                try:
                    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE if kind == 'listening' else subprocess.DEVNULL,
                                            stderr=log, bufsize=0)
                except Exception:
                    log.close()
                    raise
                self.audio, self.audio_kind, self.audio_error = proc, kind, ''
                self.audio_started = time.monotonic()
                self.audio_tokens[proc] = token
                threading.Thread(target=self.finish_audio, args=(proc, kind, log), daemon=True).start()
                return proc
        except Exception:
            self.gate.release(token)
            raise

    def finish_audio(self, proc, kind, log):
        try:
            super().finish_audio(proc, kind, log)
        finally:
            with self.lock:
                token = self.audio_tokens.pop(proc, None)
            if token is not None:
                self.gate.release(token)

    def tts_worker(self, text, volume, cancel):
        # Keep the original synthesis API, but do not hold its mutex while
        # requesting the chat process's asynchronous audio handoff.
        try:
            data = legacy.doubao.synthesize(self.tts_config, text, cancel)
            info = legacy.wav_info(data)
            with self.lock:
                if cancel.is_set() or self.closed:
                    return
                (self.dir / 'tts.wav').write_bytes(data)
                self.tts_audio = dict(info, version=time.time_ns())
                self.tts_busy = False
            self.audio_start('playing', volume=volume, source='tts', cancel=cancel)
        except legacy.doubao.Cancelled:
            pass
        except Exception as exc:
            with self.lock:
                self.tts_error = str(exc).replace(self.tts_config.get('api_key', '\0'), '[redacted]')[:400]
        finally:
            with self.lock:
                self.tts_busy = False


def start_media(bench, port=9528):
    server = legacy.ThreadingHTTPServer(('0.0.0.0', port), legacy.Handler)
    server.daemon_threads = True
    server.bench = bench
    thread = threading.Thread(target=server.serve_forever, name='legacy-media-http', daemon=True)
    thread.start()
    return server, thread


def vision_type():
    from vision import VisionClient
    from PIL import Image
    import io

    class USBVision(VisionClient):
        def __init__(self, device_id, client_id, bench):
            # OTA signs lowercase device IDs; the saved factory MAC stays intact.
            self.device_id, self.client_id, self.bench = device_id.lower(), client_id, bench
            self.explain_url = self.explain_token = ''

        @property
        def camera_available(self):
            return bool(self.bench.camera_path())

        def capture(self):
            self.bench.camera_start()
            with self.bench.frame_ready:
                before = self.bench.frames
                ok = self.bench.frame_ready.wait_for(lambda: self.bench.frames > before or self.bench.closed, timeout=5)
                if not ok or not self.bench.jpeg:
                    raise ValueError('USB camera did not produce a fresh frame.')
                jpeg = self.bench.jpeg
            return jpeg, Image.open(io.BytesIO(jpeg)).convert('RGB')
    return USBVision
