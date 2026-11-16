"""On-demand USB camera preview, isolated from the robot control process."""

import base64
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time


class USBCamera:
    IDLE_TIMEOUT = 15.0
    START_TIMEOUT = 12.0
    FRAME_TIMEOUT = 5.0

    def __init__(self):
        self._lock = threading.Lock()
        self._thread = None
        self._stop = threading.Event()
        self._state = 'off'
        self._error = ''
        self._jpeg = None
        self._device = ''
        self._width = self._height = 0
        self._last_view = 0.0

    def _status(self):
        return dict(state=self._state, error=self._error, device=self._device,
                    width=self._width, height=self._height, fps=10)

    def status(self):
        with self._lock:
            return self._status()

    def start(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self._status()
            self._stop = threading.Event()
            self._state, self._error, self._jpeg = 'starting', '', None
            self._device = ''
            self._width = self._height = 0
            self._last_view = time.monotonic()
            self._thread = threading.Thread(target=self._run, name='usb-camera', daemon=True)
            self._thread.start()
            return self._status()

    def stop(self):
        with self._lock:
            self._stop.set()
            self._jpeg = None
            self._error = ''
            self._state = 'stopping' if self._thread and self._thread.is_alive() else 'off'
            return self._status()

    def close(self):
        self.stop()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def frame(self):
        with self._lock:
            self._last_view = time.monotonic()
            return self._jpeg if self._state == 'on' else None

    def _run(self):
        process = None
        error = ''
        started = last_frame = time.monotonic()
        received_frame = False
        try:
            # Keep JPEG work out of the policy's GIL. Killing this process also
            # releases the device if a USB driver blocks inside VideoCapture.
            process = subprocess.Popen(
                [sys.executable, '-u', str(Path(__file__).with_name('camera_capture.py'))],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                env={**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1'})
            pending = b''
            while not self._stop.is_set():
                now = time.monotonic()
                with self._lock:
                    idle = now - self._last_view > self.IDLE_TIMEOUT
                if idle:
                    break
                if not received_frame and now - started > self.START_TIMEOUT:
                    raise RuntimeError('Camera startup timed out. Check the USB connection or other camera apps.')
                if received_frame and now - last_frame > self.FRAME_TIMEOUT:
                    raise RuntimeError('Camera stopped sending frames. Check the USB connection.')
                ready, _, _ = select.select([process.stdout], [], [], 0.2)
                if not ready:
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise RuntimeError('Camera capture stopped unexpectedly. Try turning it on again.')
                pending += chunk
                while b'\n' in pending:
                    line, pending = pending.split(b'\n', 1)
                    packet = json.loads(line)
                    if 'error' in packet:
                        raise RuntimeError(packet['error'])
                    jpeg = base64.b64decode(packet['jpeg'], validate=True)
                    with self._lock:
                        if not self._stop.is_set():
                            self._jpeg, self._state = jpeg, 'on'
                            self._device = packet['device']
                            self._width, self._height = packet['width'], packet['height']
                    last_frame = time.monotonic()
                    received_frame = True
                if len(pending) > 4 * 1024 * 1024:
                    raise RuntimeError('Invalid camera frame received.')
        except Exception as exc:
            error = str(exc)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                process.stdout.close()
            with self._lock:
                self._jpeg = None
                self._error = '' if self._stop.is_set() else error
                self._state = 'error' if self._error else 'off'
