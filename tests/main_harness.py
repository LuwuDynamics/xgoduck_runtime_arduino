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
import struct
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
    servo_ops = []
    saved_gains = {}
    held_mask = 0

    @classmethod
    def provide(cls, name, callback):
        assert ACTIVE_HARDWARE.checks == 1, 'Bridge opened before USB role verification completed'
        cls.callbacks[name] = callback

    @classmethod
    def notify(cls, name, payload):
        if name == 'duck_command':
            cls.commands.append(wire.COMMAND.unpack(payload)[4])
        elif name == 'duck_cal':
            magic, seq, op, index = wire.CAL.unpack(payload)
            assert magic == b'DQL2'
            if op == wire.CAL_CENTER_ONE:
                cls.held_mask |= 1 << index
                cls.callbacks['duck_cal_reply'](wire.CAL_REPLY.pack(b'DQA1', seq, index, 1))
            else:
                cls.held_mask = 0
        elif name == 'duck_servo':
            magic, op, sid, new, kp, kd, flags, current = wire.SERVO.unpack(payload)
            assert magic == b'DQV2'
            cls.servo_ops.append(op)
            assert op != wire.SERVO_APPLY_RUN_GAINS, 'Automatic gains must remain disabled'
            if op == wire.SERVO_SET_PERM_KP_KD:
                cls.saved_gains[sid] = (kp, kd)
            raw = (current if op == wire.SERVO_SET_PROTECTION_CURRENT else
                   0 if op in (wire.SERVO_SET_PROTECTION, wire.SERVO_APPLY_RUN_GAINS) else 2047)
            cls.callbacks['duck_servo_reply'](wire.SERVO_REPLY.pack(
                b'DQS3', op, sid, 1, flags, raw, 2047, *cls.saved_gains.get(sid, (5, 10))))


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
    usb_host = sys.argv[3] == 'host'
    helper = HardwareServer(os.environ['DUCK_GAMEPAD_SOCKET'], HardwareHandler)
    helper.host = usb_host
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
            Bridge.callbacks['duck_raw'](wire.RAW.pack(
                b'DQR2', wire.ALL_MASK, Bridge.held_mask, *([2047] * 15)))

    class App:
        @staticmethod
        def run():
            state = inspect.currentframe().f_back.f_globals
            feed = threading.Thread(target=feedback, daemon=True)
            feed.start()
            try:
                with TestClient(WebUI.instance.app) as client:
                    version = (ROOT / 'version.txt').read_text().strip()
                    assert client.get('/api/version').json() == {'version': version}
                    wait_for(lambda: state['controller'].inferred > 5)
                    voice = wait_for(lambda: (s if (s := client.get('/api/voice/status').json()).get('state') == 'disabled' else None))
                    assert Bridge.servo_ops == [], 'Voice startup touched servo parameters'
                    assert client.get('/api/status').json()['runtime_gains'] == dict(state='manual', auto_apply=False)
                    # Exercise real parent -> voice IPC region selection without a cloud or robot.
                    assert client.get('/api/voice/region').json()['cloud_region'] is None
                    for region in ('global', 'cn'):
                        changed = client.post('/api/voice/region', json={'cloud_region': region}, headers={'X-Device-Test': '1'})
                        assert changed.status_code == 200 and changed.json()['changed']
                        assert client.get('/api/voice/region').json()['cloud_region'] == region
                    unchanged = client.post('/api/voice/region', json={'cloud_region': 'cn'}, headers={'X-Device-Test': '1'})
                    assert unchanged.status_code == 200 and not unchanged.json()['changed']
                    assert not Bridge.servo_ops, 'Server selection touched servo parameters'
                    assert helper.host == usb_host and helper.switches == 0 and helper.checks == 1
                    assert client.get('/api/status').json()['full_robot_ready']
                    from local_ipc import request
                    wait_for(lambda: state['tof'].status()['fresh'])
                    if state['avoidance'].settings['enabled']:
                        wait_for(lambda: state['avoidance'].status()['available'])
                    tof = request(state['motion_port'], state['token'], '/tof')
                    assert tof['sensor']['fresh'] and tof['sensor']['seq'] > 0
                    assert tof['sensor']['grid_mm'] == [[1000]*8 for _ in range(8)]
                    assert tof['front']['front_distance_mm'] == 1000
                    assert tof['front']['forward_ready']
                    media = client.get('/api/media/status')
                    assert media.status_code == 200 and set(media.json()) == {'camera', 'audio', 'tts'}
                    if mode == 'orphan':
                        report.write_text(json.dumps(dict(voice_pid=voice['pid'])))
                        time.sleep(30)
                        raise AssertionError('Orphan test failed to kill the simulated motion process')
                    assert client.post('/api/mode', json={'mode': 'off'}).status_code == 200
                    if mode == 'normal':
                        Bridge.callbacks['duck_voltage'](wire.VOLTAGE.pack(
                            b'DQV1', 1, 0, wire.ALL_MASK, *([82] * 15)))
                        motion = client.get('/api/status').json()
                        assert motion['voltage_v'] == 8.2 and len(motion['servo_voltage_v']) == 15
                        assert client.post('/api/heading-hold', json={'enabled': False}).json()['enabled'] is False
                        assert client.post('/api/heading-hold', json={'enabled': True}).json()['enabled'] is True
                        sampling = client.post('/api/imu-calibration', json={'action': 'start'})
                        assert sampling.status_code == 200 and sampling.json()['active']
                        wait_for(lambda: client.get('/api/status').json()['imu_calibration']['state'] == 'saved')
                        saved = json.loads((Path(os.environ['DUCK_DATA_DIR']) / 'imu-calibration.json').read_text())
                        assert saved['version'] == 2 and saved['measurement']['samples'] >= 90
                        assert client.post('/api/imu-calibration', json={'action': 'reset'}).json()['state'] == 'idle'
                    assert client.post('/api/mode', json={'mode': 'shadow'}).status_code == 200
                    cockpit = '/api/cockpit/motion'
                    start = dict(controller_id='a'*24, sequence=1, kind='stand')
                    headers = {'X-Duck-Cockpit': '1', 'Origin': 'http://testserver'}
                    assert client.post(cockpit, json=start).status_code == 403
                    assert client.post(cockpit, json=start, headers=headers | {'Origin': 'http://elsewhere'}).status_code == 403
                    drive = dict(start, kind='begin', twist=[.1, 0, 0])
                    assert client.post(cockpit, json=drive, headers=headers).status_code == 400
                    assert client.get('/api/status').json()['mode'] == 'shadow'
                    off = dict(start, sequence=2, kind='torque_off')
                    assert client.post(cockpit, json=off, headers=headers).json()['mode'] == 'off'
                    assert client.post(cockpit, json=start, headers=headers).status_code == 400
                    assert client.get('/api/status').json()['mode'] == 'off'
                    assert client.post('/api/mode', json={'mode': 'shadow'}).status_code == 200
                    # Exercise the stable ZIP's buttons through the real App
                    # routes and matching MCU ABI, still with no physical I/O.
                    roller = client.post('/api/roller', json={'enabled': True})
                    assert roller.status_code == 200 and roller.json()['enabled']
                    assert client.post('/api/action-scale', json={'scale': .8}).status_code == 200
                    assert client.post('/api/filter', json={'leg': .3, 'head': .6}).status_code == 200
                    assert client.post('/api/imu-filter', json={'gyro_alpha': .2, 'gravity_tau_s': .4}).status_code == 200
                    status = client.get('/api/status').json()
                    assert status['active_model'] == 'xgoduck_roller.onnx'
                    assert status['deployment']['action_scale'] == .8
                    assert client.post('/api/roller', json={'enabled': False}).status_code == 200
                    assert client.post('/api/cal/enter', json={}).status_code == 200
                    assert client.post('/api/cal/heartbeat', json={}).status_code == 200
                    center = client.post('/api/cal/center', json={'index': 0})
                    assert center.status_code == 200 and center.json()['hold_pos'] == 2048
                    assert client.post('/api/cal/center', json={'index': True}).status_code == 400
                    assert client.post('/api/cal/exit', json={}).status_code == 200
                    assert client.post('/api/servo/enter', json={}).status_code == 200
                    read = client.post('/api/servo', json={'action': 'read', 'target_id': 42})
                    assert read.status_code == 200 and read.json()['kp'] == 5
                    tuned = client.post('/api/servo', json={'action': 'set_gains', 'target_id': 42, 'kp': 7, 'kd': 19})
                    assert tuned.status_code == 200
                    assert Bridge.saved_gains[42] == (7, 19)
                    switch = client.post('/api/servo', json={'action': 'set_protection', 'protection_enabled': False})
                    assert switch.status_code == 200 and switch.json()['broadcast'] is True
                    current = client.post('/api/servo', json={'action': 'set_protection_current', 'current_a': 1.5})
                    assert current.status_code == 200 and abs(current.json()['current_a'] - 1.5) < .0065
                    assert client.post('/api/servo/exit', json={}).status_code == 200
                    before = state['controller'].inferred
                    servo_ops_before = list(Bridge.servo_ops)
                    os.kill(voice['pid'], signal.SIGKILL)
                    wait_for(lambda: state['controller'].inferred > before + 10)
                    after = wait_for(lambda: (s if (s := client.get('/api/voice/status').json()).get('state') == 'disabled'
                                             and s.get('pid') != voice['pid'] else None))
                    assert helper.switches == 0 and helper.checks == 1
                    assert after['restarts'] == 1
                    assert Bridge.servo_ops == servo_ops_before, 'Voice respawn touched servo parameters'
                    runtime = client.post('/api/servo', json={'action': 'read_runtime_gains', 'target_id': 42})
                    assert runtime.status_code == 200
                    assert (runtime.json()['kp'], runtime.json()['kd']) == (7, 19)
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
    # Exercise the real ToF monitor/decoder/guard with a simulated private RPC
    # connection; tests must never open the board's Router socket.
    import tof_status
    monitor = tof_status.ToFMonitor
    class ToFClient:
        seq = 0
        def connect(self, timeout): return True
        def disconnect(self): pass
        def call(self, method, timeout):
            assert method == 'duck_tof'
            self.seq += 1
            return struct.pack('<4s8B6I64h64B64B', b'DTF1', 1, 8, 8, 3,
                0x29, 0xf0, 2, 0, self.seq, 100, 10, 2000, 0, 0,
                *([1000]*64), *([5]*64), *([1]*64))
    tof_status.ToFMonitor = lambda **kw: monitor(ToFClient, **kw)
    try:
        result = runpy.run_path(str(ROOT / 'python/main.py'), run_name='__main__')
        assert not result['worker'].is_alive()
        assert not result['access'].thread.is_alive()
        assert not result['voice'].thread.is_alive()
        assert result['avoidance'].thread is None or not result['avoidance'].thread.is_alive()
        assert not result['tof']._thread.is_alive()
        assert result['voice'].process.poll() is not None
    finally:
        helper.shutdown()
        helper.server_close()


if __name__ == '__main__':
    main()
