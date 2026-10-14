#!/usr/bin/env python3
"""Standalone UNO Q USB device bench. Python stdlib + v4l2-utils + alsa-utils."""
import argparse
import array
import glob
import io
import json
import math
import os
import re
import select
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
import wave
from collections import deque
import doubao

ROOT = Path(__file__).resolve().parent
MAX_UPLOAD = 12 * 1024 * 1024


def melody(path):
    rate = 48000
    pcm = array.array('h')
    # Original short arpeggio, stereo, with smooth attacks and releases.
    for _ in range(3):
        for hz in (261.63, 329.63, 392, 523.25, 440, 392, 329.63, 293.66):
            for i in range(int(rate * .32)):
                t = i / rate
                env = max(0, min(1, t / .015, (.29 - t) / .07))
                v = int(32767 * .55 * env * (math.sin(2 * math.pi * hz * t)
                        + .2 * math.sin(4 * math.pi * hz * t)) / 1.2)
                pcm.extend((v, v))
    if sys.byteorder != 'little':
        pcm.byteswap()
    with wave.open(str(path), 'wb') as w:
        w.setparams((2, 2, rate, 0, 'NONE', 'not compressed'))
        w.writeframes(pcm.tobytes())


def wav_info(data):
    with wave.open(io.BytesIO(data), 'rb') as w:
        channels, width, rate, frames = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        if channels not in (1, 2) or width != 2 or not 8000 <= rate <= 96000:
            raise ValueError('Use 16-bit PCM WAV with one or two channels.')
        if not 0 < frames <= rate * 60:
            raise ValueError('Audio duration must be between 0 and 60 seconds.')
        raw = w.readframes(frames)
        if len(raw) != frames * channels * width:
            raise ValueError('Incomplete WAV file.')
    pcm = array.array('h', raw)
    if sys.byteorder != 'little':
        pcm.byteswap()
    peak = max(abs(v) for v in pcm) / 32768
    rms = math.sqrt(sum(v * v for v in pcm) / len(pcm)) / 32768
    return dict(duration=round(frames / rate, 2), rate=rate, channels=channels,
                peak=round(peak, 4), rms=round(rms, 4),
                dbfs=round(20 * math.log10(max(rms, 1e-6)), 1),
                clipped=sum(abs(v) >= 32767 for v in pcm))


class Bench:
    def __init__(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='duck-device-web-')
        self.dir = Path(self.tmp.name)
        melody(self.dir / 'music.wav')
        self.lock = threading.RLock()
        self.frame_ready = threading.Condition(self.lock)
        self.frame_times = deque(maxlen=90)
        self.camera = None
        self.jpeg = None
        self.frames = 0
        self.last_frame = 0
        self.last_view = 0
        self.camera_error = ''
        self.camera_device = ''
        self.audio = None
        self.audio_kind = ''
        self.audio_error = ''
        self.audio_started = 0
        self.volume = None
        self.volume_card = None
        self.volume_checked = 0
        self.volume_error = ''
        self.recording = None
        self.uploaded = None
        self.tts_config = doubao.load_config()
        self.tts_busy = False
        self.tts_cancel = threading.Event()
        self.tts_error = ''
        self.tts_audio = None
        self.closed = False
        threading.Thread(target=self.watchdog, daemon=True).start()

    @staticmethod
    def camera_path():
        paths = sorted(glob.glob('/dev/v4l/by-id/*-video-index0'))
        return paths[0] if paths else None

    @staticmethod
    def audio_card():
        for card in sorted(Path('/proc/asound').glob('card[0-9]*')):
            if (card / 'usbid').exists():
                return card.name[4:]
        raise ValueError('No USB audio device found. Check the USB connection and Host mode.')

    @staticmethod
    def halt(proc, recording=False):
        if proc and proc.poll() is None:
            proc.duck_stopping = True
            proc.send_signal(signal.SIGINT if recording else signal.SIGTERM)
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)

    def camera_start(self):
        with self.lock:
            self.last_view = time.monotonic()
            if self.camera and self.camera.poll() is None:
                return
            device = self.camera_path()
            if not device:
                raise ValueError('No USB camera found. Check the USB connection and Host mode.')
            self.jpeg = None
            self.frames = 0
            self.frame_times.clear()
            self.last_frame = time.monotonic()
            self.camera_error = ''
            self.camera_device = os.path.realpath(device)
            log = tempfile.TemporaryFile()
            try:
                proc = subprocess.Popen(['v4l2-ctl', '-d', device,
                    '--set-fmt-video=width=640,height=480,pixelformat=MJPG',
                    '--set-parm=30', '--stream-mmap=4', '--stream-to=-'],
                    stdout=subprocess.PIPE, stderr=log, bufsize=0)
            except Exception:
                log.close()
                raise
            self.camera = proc
            threading.Thread(target=self.read_camera, args=(proc, log), daemon=True).start()

    def read_camera(self, proc, log):
        buf = bytearray()
        try:
            while chunk := proc.stdout.read(65536):
                buf.extend(chunk)
                if len(buf) > 8 * 1024 * 1024:
                    raise ValueError('Invalid camera frame format.')
                while True:
                    start = buf.find(b'\xff\xd8')
                    if start < 0:
                        buf[:] = buf[-1:]
                        break
                    end = buf.find(b'\xff\xd9', start + 2)
                    if end < 0:
                        del buf[:start]
                        break
                    jpeg = bytes(buf[start:end + 2])
                    del buf[:end + 2]
                    with self.lock:
                        if self.camera is not proc:
                            return
                        self.jpeg = jpeg
                        self.frames += 1
                        self.last_frame = time.monotonic()
                        self.frame_times.append(self.last_frame)
                        self.frame_ready.notify_all()
            proc.wait(timeout=2)
            log.seek(0)
            err = log.read().decode(errors='replace')[-500:]
            with self.lock:
                if self.camera is proc:
                    self.camera_error = 'Camera capture interrupted: ' + err
        except Exception as exc:
            with self.lock:
                if self.camera is proc:
                    self.camera_error = str(exc)
        finally:
            self.halt(proc)
            proc.stdout.close()
            log.close()

    def camera_stop(self):
        with self.lock:
            proc, self.camera = self.camera, None
            self.jpeg = None
            self.frame_ready.notify_all()
        self.halt(proc)

    @staticmethod
    def mixer_volume(output):
        values = re.findall(r'\[(\d+)%\]', output)
        if not values:
            raise ValueError('Speaker volume is not available on this device.')
        return int(values[0])

    def read_volume(self, card):
        if card != self.volume_card or time.monotonic() - self.volume_checked > 5:
            self.volume_card = card
            self.volume_checked = time.monotonic()
            try:
                result = subprocess.run(['amixer', '-c', card, 'sget', 'Speaker'],
                    check=True, capture_output=True, text=True, timeout=4)
                self.volume = self.mixer_volume(result.stdout)
                self.volume_error = ''
            except (subprocess.SubprocessError, OSError, ValueError):
                self.volume = None
                self.volume_error = 'Unable to read the USB speaker volume.'
        return self.volume

    def set_volume(self, volume):
        if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
            raise ValueError('Volume must be an integer from 0 to 100.')
        with self.lock:
            card = self.audio_card()
            result = subprocess.run(['amixer', '-c', card, 'sset', 'Speaker', f'{volume}%', 'unmute'],
                check=True, capture_output=True, text=True, timeout=4)
            self.volume = self.mixer_volume(result.stdout)
            self.volume_card = card
            self.volume_checked = time.monotonic()
            self.volume_error = ''

    def audio_start(self, kind, volume=None, seconds=5, source='music'):
        with self.lock:
            if self.audio or self.tts_busy:
                raise ValueError('Audio device is busy. Stop playback or recording first.')
            card = self.audio_card()
            if kind == 'listening':
                cmd = ['arecord', '-q', '-D', f'plughw:{card},0', '-t', 'raw',
                       '-f', 'S16_LE', '-r', '24000', '-c', '1',
                       '--buffer-time=80000', '--period-time=20000', '-']
            elif kind == 'recording':
                seconds = int(seconds)
                if not 1 <= seconds <= 30:
                    raise ValueError('Recording duration must be 1 to 30 seconds.')
                self.recording = None
                cmd = ['arecord', '-D', f'plughw:{card},0', '-f', 'S16_LE',
                       '-r', '48000', '-c', '1', '-d', str(seconds), str(self.dir / 'recording.wav')]
            else:
                names = {'music': 'music.wav', 'upload': 'upload.wav', 'recording': 'recording.wav', 'tts': 'tts.wav'}
                if source not in names or (source == 'recording' and not self.recording) or (source == 'upload' and not self.uploaded) or (source == 'tts' and not self.tts_audio):
                    raise ValueError('The selected audio source is not available.')
                if volume is not None:
                    self.set_volume(volume)
                cmd = ['aplay', '-D', f'plughw:{card},0', str(self.dir / names[source])]
            log = tempfile.TemporaryFile()
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE if kind == 'listening'
                                        else subprocess.DEVNULL, stderr=log, bufsize=0)
            except Exception:
                log.close()
                raise
            self.audio = proc
            self.audio_kind = kind
            self.audio_started = time.monotonic()
            self.audio_error = ''
            threading.Thread(target=self.finish_audio, args=(proc, kind, log), daemon=True).start()
            return proc

    def finish_audio(self, proc, kind, log):
        code = proc.wait()
        log.seek(0)
        error = log.read().decode(errors='replace')[-500:]
        log.close()
        with self.lock:
            if self.audio is not proc:
                return
            if code not in (0, -signal.SIGINT, -signal.SIGTERM) and not getattr(proc, 'duck_stopping', False):
                self.audio_error = error
            if kind == 'recording':
                try:
                    self.recording = wav_info((self.dir / 'recording.wav').read_bytes())
                except Exception as exc:
                    self.audio_error = 'Unable to save recording: ' + str(exc)
            self.audio = None
            self.audio_kind = ''

    def audio_stop(self):
        with self.lock:
            self.tts_cancel.set()
            proc, kind = self.audio, self.audio_kind
        self.halt(proc, kind == 'recording')

    def tts_start(self, text, volume):
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 200:
            raise ValueError('Enter 1 to 200 characters.')
        volume = int(volume)
        if not 0 <= volume <= 100:
            raise ValueError('Volume must be 0 to 100%.')
        with self.lock:
            if not self.tts_config:
                raise ValueError('Doubao voice is not configured.')
            if self.audio or self.tts_busy:
                raise ValueError('Audio is busy. Stop the current task first.')
            self.audio_card()
            self.tts_busy = True
            self.tts_cancel = threading.Event()
            self.tts_error = ''
            self.audio_started = time.monotonic()
            threading.Thread(target=self.tts_worker, args=(text.strip(), volume, self.tts_cancel), daemon=True).start()

    def tts_worker(self, text, volume, cancel):
        try:
            data = doubao.synthesize(self.tts_config, text, cancel)
            info = wav_info(data)
            with self.lock:
                if cancel.is_set() or self.closed:
                    return
                (self.dir / 'tts.wav').write_bytes(data)
                self.tts_audio = dict(info, version=time.time_ns())
                self.tts_busy = False
                self.audio_start('playing', volume=volume, source='tts')
        except doubao.Cancelled:
            pass
        except Exception as exc:
            with self.lock:
                self.tts_error = str(exc).replace(self.tts_config.get('api_key', '\0'), '[redacted]')[:400]
        finally:
            with self.lock:
                self.tts_busy = False

    def upload(self, data):
        info = wav_info(data)
        with self.lock:
            if self.audio or self.tts_busy:
                raise ValueError('Stop playback or recording before uploading.')
            (self.dir / 'upload.wav').write_bytes(data)
            self.uploaded = info

    def status(self):
        with self.lock:
            try:
                card = self.audio_card()
            except ValueError:
                card = None
            volume = self.read_volume(card) if card is not None else None
            return dict(camera=dict(available=bool(self.camera_path()),
                running=bool(self.camera and self.camera.poll() is None), ready=self.jpeg is not None,
                device=self.camera_device, frames=self.frames, error=self.camera_error,
                fps=round((len(self.frame_times)-1)/(self.frame_times[-1]-self.frame_times[0]),1)
                    if len(self.frame_times)>1 and time.monotonic()-self.last_frame<2 else 0),
                audio=dict(available=card is not None, card=card, volume=volume, volume_error=self.volume_error,
                state=('cancelling' if self.tts_cancel.is_set() else 'synthesizing') if self.tts_busy else self.audio_kind or 'idle',
                elapsed=round(time.monotonic() - self.audio_started, 1) if self.audio or self.tts_busy else 0,
                error=self.audio_error, recording=self.recording, uploaded=self.uploaded),
                tts=dict(configured=bool(self.tts_config), speaker=self.tts_config.get('speaker', ''),
                         busy=self.tts_busy, error=self.tts_error, audio=self.tts_audio))

    def watchdog(self):
        while not self.closed:
            time.sleep(1)
            with self.lock:
                stale = self.camera and (time.monotonic() - self.last_view > 20 or time.monotonic() - self.last_frame > 10)
                if stale and time.monotonic() - self.last_frame > 10:
                    self.camera_error = 'Camera stopped after 10 seconds without a frame.'
            if stale:
                self.camera_stop()

    def close(self):
        self.closed = True
        self.camera_stop()
        self.audio_stop()
        self.tmp.cleanup()


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(20)

    def send(self, code, data, mime='application/json; charset=utf-8'):
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urlsplit(self.path).path
        b = self.server.bench
        if path == '/':
            self.send(200, (ROOT / 'index.html').read_bytes(), 'text/html; charset=utf-8')
        elif path == '/api/status':
            self.send(200, b.status())
        elif path == '/api/camera/stream':
            self.camera_stream(b)
        elif path == '/api/audio/stream':
            origin = self.headers.get('Origin')
            if (self.headers.get('X-Device-Test') != '1'
                    or (origin and urlsplit(origin).netloc != self.headers.get('Host'))):
                self.send(403, {'error': 'Request origin mismatch. Use the device page.'})
                return
            self.audio_stream(b)
        elif path == '/api/camera/frame':
            with b.lock:
                b.last_view = time.monotonic()
                jpeg = b.jpeg
            self.send(200, jpeg, 'image/jpeg') if jpeg else self.send(503, {'error': 'Camera frame is not ready.'})
        elif path == '/api/recording':
            with b.lock:
                data = (b.dir / 'recording.wav').read_bytes() if b.recording else None
            self.send(200, data, 'audio/wav') if data else self.send(404, {'error': 'No recording available.'})
        elif path == '/api/tts/audio':
            with b.lock:
                data = (b.dir / 'tts.wav').read_bytes() if b.tts_audio else None
            self.send(200, data, 'audio/wav') if data else self.send(404, {'error': 'No generated speech available.'})
        else:
            self.send(404, {'error': 'Not found'})

    def audio_stream(self, b):
        # The HTTP connection owns capture. No WAV/file, duration limit or
        # orphan recorder: disconnecting/closing the stream always stops ALSA.
        try:
            proc = b.audio_start('listening')
        except ValueError as exc:
            self.send(409, {'error': str(exc)})
            return
        except OSError as exc:
            self.send(503, {'error': str(exc)})
            return
        try:
            ready, _, _ = select.select([proc.stdout], [], [], 3)
            first = os.read(proc.stdout.fileno(), 960) if ready else b''
            if not first:
                self.send(503, {'error': 'Duck microphone did not produce audio.'})
                return
            self.connection.settimeout(2)
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 8192)
            self.send_response(200)
            self.send_header('Content-Type', 'application/octet-stream')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Accel-Buffering', 'no')
            self.end_headers()
            chunk = first
            while chunk and not b.closed:
                self.wfile.write(chunk)
                self.wfile.flush()
                ready, _, _ = select.select([proc.stdout], [], [], 2)
                if not ready:
                    break
                chunk = os.read(proc.stdout.fileno(), 960)
        except (OSError, ValueError):
            pass
        finally:
            b.halt(proc)
            proc.stdout.close()

    def camera_stream(self, b):
        with b.lock:
            proc = b.camera
            if not proc or proc.poll() is not None:
                self.send(503, {'error': 'Start the camera first.'})
                return
        self.connection.settimeout(3)
        self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
        self.send_response(200)
        self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=duckframe')
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        last = -1
        try:
            while not b.closed:
                with b.frame_ready:
                    b.frame_ready.wait_for(lambda: b.camera is not proc or b.frames != last, timeout=2)
                    if b.camera is not proc or proc.poll() is not None:
                        break
                    b.last_view = time.monotonic()
                    if not b.jpeg or b.frames == last:
                        # Wait for first frame instead of spinning during camera startup.
                        last = b.frames
                        continue
                    jpeg, last = b.jpeg, b.frames
                self.wfile.write(b'--duckframe\r\nContent-Type: image/jpeg\r\nContent-Length: '
                                 + str(len(jpeg)).encode() + b'\r\n\r\n' + jpeg + b'\r\n')
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass

    def do_POST(self):
        # Only same-origin script requests may operate hardware; no cross-site forms.
        origin = self.headers.get('Origin')
        if (self.headers.get('X-Device-Test') != '1'
                or (origin and urlsplit(origin).netloc != self.headers.get('Host'))):
            self.send(403, {'error': 'Request origin mismatch. Use the device page.'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= MAX_UPLOAD:
                self.send(413, {'error': 'Audio file too large.'})
                return
            data = self.rfile.read(length)
            if len(data) != length:
                raise ValueError('Upload incomplete.')
            path = urlsplit(self.path).path
            b = self.server.bench
            if path == '/api/upload':
                b.upload(data)
            else:
                args = json.loads(data or b'{}')
                if not isinstance(args, dict):
                    raise ValueError('Request parameters must be an object.')
                if path == '/api/camera/start':
                    b.camera_start()
                elif path == '/api/camera/stop':
                    b.camera_stop()
                elif path == '/api/audio/volume':
                    b.set_volume(args.get('volume'))
                elif path == '/api/audio/play':
                    b.audio_start('playing', volume=args.get('volume'), source=args.get('source', 'music'))
                elif path == '/api/audio/record':
                    b.audio_start('recording', seconds=args.get('seconds', 5))
                elif path == '/api/audio/stop':
                    b.audio_stop()
                elif path == '/api/tts/speak':
                    b.tts_start(args.get('text'), args.get('volume', 100))
                else:
                    self.send(404, {'error': 'Not found'})
                    return
            self.send(200, b.status())
        except (ValueError, TypeError, wave.Error, EOFError) as exc:
            self.send(400, {'error': str(exc)})
        except Exception as exc:
            self.send(500, {'error': str(exc)})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='0.0.0.0')
    parser.add_argument('--port', type=int, default=9528)
    args = parser.parse_args()
    bench = Bench()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.bench = bench
    def shutdown(*_):
        threading.Thread(target=server.shutdown, daemon=True).start()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(f'Device test listening on http://{args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        bench.close()


if __name__ == '__main__':
    main()
