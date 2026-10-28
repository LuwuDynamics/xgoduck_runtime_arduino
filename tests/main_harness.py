"""Run the actual App entry point with simulated MCU/USB and real ONNX/FastAPI.

All Bridge writes are captured in memory. No hardware or backend is accessed.
"""
import contextlib
import inspect
import json
import os
from pathlib import Path
import runpy
import signal
import socketserver
import sys
import threading
import time
from types import ModuleType
from http.server import BaseHTTPRequestHandler
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
import wire


def wait_for(check, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(.03)
    raise AssertionError('App integration condition timed out')


class HardwareHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_snapshot()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        if self.path == '/prepare-usb':
            assert body == {}
            assert self.server.host
            self.server.checks += 1
        else:
            assert self.path == '/provision' and isinstance(body['enabled'], bool)
        self.send_snapshot()

    def send_snapshot(self):
        raw = json.dumps(dict(bluetooth=dict(available=False, devices=[], error='Simulated'),
            input=dict(connected=False, devices=[]),
            provisioning=dict(network=dict(connected=False, available=True), advertising=False, name='XGO-Duck0001'),
            usb=dict(available=True, host=self.server.host, mode='host' if self.server.host else 'device', error=''))).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class HardwareServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    host, switches, checks = True, 0, 0


class Bridge:
    callbacks, commands = {}, []

    @classmethod
    def provide(cls, name, callback):
        assert ACTIVE_HARDWARE.host, 'Bridge opened before USB preparation completed'
        cls.callbacks[name] = callback

    @classmethod
    def notify(cls, name, payload):
        if name == 'duck_command':
            cls.commands.append(wire.COMMAND.unpack(payload)[4])


class WebUI:
    instance = None

    def __init__(self, **kwargs):
        self.app = FastAPI()
        WebUI.instance = self

    def expose_api(self, method, path, callback):
        self.app.add_api_route(path, callback, methods=[method])


def main():
    global ACTIVE_HARDWARE
    report = Path(sys.argv[1])
    mode = sys.argv[2]
    helper = HardwareServer(os.environ['DUCK_GAMEPAD_SOCKET'], HardwareHandler)
    ACTIVE_HARDWARE = helper
    threading.Thread(target=helper.serve_forever, daemon=True).start()
    stop_feedback = threading.Event()
    def feedback():
        seq = 0
        while not stop_feedback.wait(.01):
            seq += 1
            raw = wire.STATE.pack(b'DQS1', 1, 1, wire.ALL_MASK,
                seq, seq * 10000, seq * 10000, 0, 0, 10000, 500, 0, 0, 0,
                0, 0, -16384, 0, 0, 0, *([0.] * 45))
            Bridge.callbacks['duck_state'](raw)

    class App:
        @staticmethod
        def run():
            state = inspect.currentframe().f_back.f_globals
            feed = threading.Thread(target=feedback, daemon=True)
            feed.start()
            try:
                with TestClient(WebUI.instance.app) as client:
                    wait_for(lambda: state['controller'].inferred > 5)
                    voice = wait_for(lambda: (s if (s := client.get('/api/voice/status').json()).get('state') == 'disabled' else None))
                    assert helper.host and helper.switches == 0 and helper.checks == 1
                    assert client.get('/api/status').json()['full_robot_ready']
                    media = client.get('/api/media/status')
                    assert media.status_code == 200 and set(media.json()) == {'camera', 'audio', 'tts'}
                    if mode == 'orphan':
                        report.write_text(json.dumps(dict(voice_pid=voice['pid'])))
                        time.sleep(30)
                        raise AssertionError('Orphan test failed to kill the simulated motion process')
                    assert client.post('/api/mode', json={'mode': 'off'}).status_code == 200
                    assert client.post('/api/mode', json={'mode': 'shadow'}).status_code == 200
                    before = state['controller'].inferred
                    os.kill(voice['pid'], signal.SIGKILL)
                    wait_for(lambda: state['controller'].inferred > before + 10)
                    after = wait_for(lambda: (s if (s := client.get('/api/voice/status').json()).get('state') == 'disabled'
                                             and s.get('pid') != voice['pid'] else None))
                    assert helper.switches == 0 and helper.checks == 1
                    assert after['restarts'] == 1
                    assert set(Bridge.commands) == {wire.DISARM}, 'Simulation unexpectedly armed motors'
                    report.write_text(json.dumps(dict(inferred=state['controller'].inferred,
                        restarts=after['restarts'], voice_pid=after['pid'], host_switches=helper.switches,
                        host_checks=helper.checks)))
            finally:
                stop_feedback.set()
                feed.join(2)

    modules = {name: ModuleType(name) for name in ('arduino', 'arduino.app_utils', 'arduino.app_bricks', 'arduino.app_bricks.web_ui')}
    modules['arduino.app_utils'].App, modules['arduino.app_utils'].Bridge = App, Bridge
    modules['arduino.app_bricks.web_ui'].WebUI = WebUI
    sys.modules.update(modules)
    try:
        result = runpy.run_path(str(ROOT / 'python/main.py'), run_name='__main__')
        assert not result['worker'].is_alive()
        assert not result['access'].thread.is_alive()
        assert not result['voice'].thread.is_alive()
        assert result['voice'].process.poll() is not None
    finally:
        helper.shutdown()
        helper.server_close()


if __name__ == '__main__':
    main()
