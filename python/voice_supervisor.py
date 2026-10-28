"""The motion process supervises one voice child; no third supervisor process."""
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from local_ipc import request


class VoiceSupervisor:
    def __init__(self, token, motion_port=9531, voice_port=9530, on_exit=lambda: None, env=None, prepare=lambda: None):
        self.token, self.motion_port, self.voice_port = token, motion_port, voice_port
        self.on_exit = on_exit
        self.prepare = prepare
        self.env = env or {}
        self.process = None
        self.restarts = 0
        self.stop_event = threading.Event()
        self.thread = None
        self.lock = threading.Lock()

    @staticmethod
    def signal_group(process, sig):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass

    def _spawn(self):
        env = dict(os.environ, DUCK_IPC_TOKEN=self.token, DUCK_MOTION_PORT=str(self.motion_port),
                   DUCK_VOICE_PORT=str(self.voice_port), PYTHONUNBUFFERED='1',
                   DUCK_PARENT_PID=str(os.getpid()),
                   OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
        env.update(self.env)
        from app_paths import DATA
        native = Path(env.get('DUCK_DATA_DIR', str(DATA))) / 'native'
        env['PATH'] = str(native / 'bin') + os.pathsep + env.get('PATH', '')
        env['LD_LIBRARY_PATH'] = str(native / 'lib') + os.pathsep + env.get('LD_LIBRARY_PATH', '')
        env['DUCK_NATIVE_LIB_DIR'] = str(native / 'lib')
        process = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('voice_process.py'))],
                                   env=env, start_new_session=True)
        with self.lock:
            self.process = process
        print('[supervisor] voice PID', process.pid, flush=True)
        return process

    def start(self):
        def run():
            try:
                self.prepare()  # once per App start, not on every voice restart
            except Exception as exc:
                print('[supervisor] Hardware preparation failed:', type(exc).__name__, flush=True)
            backoff = 1.0
            while not self.stop_event.is_set():
                try:
                    process = self._spawn()
                except OSError as exc:
                    print('[supervisor] Unable to start voice:', type(exc).__name__, flush=True)
                    if self.stop_event.wait(backoff):
                        break
                    backoff = min(15.0, backoff * 2)
                    continue
                started = time.monotonic()
                while not self.stop_event.wait(.2) and process.poll() is None:
                    pass
                if self.stop_event.is_set():
                    break
                self.signal_group(process, signal.SIGTERM)
                try:
                    self.on_exit()
                except Exception as exc:
                    print('[supervisor] Motion cancellation failed:', type(exc).__name__, flush=True)
                if time.monotonic() - started >= 30:
                    backoff = 1.0
                self.restarts += 1
                print('[supervisor] voice exited', process.returncode, '; restart in', backoff, 's', flush=True)
                if self.stop_event.wait(backoff):
                    break
                self.signal_group(process, signal.SIGKILL)
                backoff = min(15.0, backoff * 2)
        self.thread = threading.Thread(target=run, name='voice-supervisor', daemon=True)
        self.thread.start()

    def status(self):
        with self.lock:
            process = self.process
        base = dict(pid=process.pid if process else None, running=bool(process and process.poll() is None),
                    restarts=self.restarts, motion_pid=os.getpid())
        try:
            return dict(base, **request(self.voice_port, self.token, '/status', timeout=.4))
        except (OSError, ValueError) as exc:
            return dict(base, state='starting' if base['running'] else 'unavailable', error=str(exc))

    def call(self, path, body=None):
        return request(self.voice_port, self.token, path, body, timeout=2)

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=11)
        with self.lock:
            process = self.process
        if process and process.poll() is None:
            self.signal_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                self.signal_group(process, signal.SIGKILL)
                process.wait(timeout=2)
        if process:
            self.signal_group(process, signal.SIGKILL)
        if self.thread:
            self.thread.join(timeout=2)
