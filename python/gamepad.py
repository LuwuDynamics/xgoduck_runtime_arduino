"""Gamepad source arbitration and local-socket API for the firmware UI."""
from contextlib import asynccontextmanager
import os
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit
import httpx
from fastapi import Request
from fastapi.responses import JSONResponse


DEFAULT_BINDINGS = dict(walk='start', hold='south', off='east', pick=None, roulade=None)
BUTTON_NAMES = ('south', 'east', 'north', 'west', 'lb', 'rb', 'back', 'start', 'guide', 'ls', 'rs',
                'lt', 'rt', 'f1', 'f3', 'up', 'down', 'left', 'right')


def motion_from_input(data):
    a = data['axes']
    return [-a['ly'] * .4, -a['lx'] * .3, -a['rx']]


class GamepadControl:
    def __init__(self, controller, client, bindings_path=None):
        self.controller, self.client = controller, client
        self.enabled = False
        self.identity = None
        self.buttons = {}
        self.error = ''
        self.bindings_path = Path(bindings_path) if bindings_path else Path(__file__).resolve().parents[1] / 'data/gamepad-bindings.json'
        self.bindings = dict(DEFAULT_BINDINGS)
        self.learning = None
        self.binding_message = ''
        try:
            saved = json.loads(self.bindings_path.read_text())
            self.validate_bindings(saved)
            self.bindings = saved
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            self.binding_message = 'Saved bindings could not be loaded. Defaults are in use.'
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.run, name='gamepad20', daemon=True)
        self.thread.start()

    def status(self):
        with self.controller.lock:
            return dict(enabled=self.enabled, error=self.error, source=self.controller.input_source,
                        bindings=dict(self.bindings), learning=self.learning['action'] if self.learning else None,
                        binding_message=self.binding_message)

    @staticmethod
    def validate_bindings(bindings):
        if not isinstance(bindings, dict) or set(bindings) != set(DEFAULT_BINDINGS):
            raise ValueError('Unknown binding action.')
        assigned = [v for v in bindings.values() if v is not None]
        if any(v not in BUTTON_NAMES for v in assigned) or len(set(assigned)) != len(assigned):
            raise ValueError('Each button can be assigned to only one action.')
        if bindings['off'] is None:
            raise ValueError('Torque off must have a button assigned.')

    def save_bindings(self, bindings):
        self.validate_bindings(bindings)
        self.bindings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.bindings_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(bindings, indent=2) + '\n')
        temporary.replace(self.bindings_path)
        self.bindings = bindings

    def configure(self, body):
        with self.controller.lock:
            if self.enabled:
                raise ValueError('Disable gamepad control before changing bindings.')
            operation = body.get('operation')
            if operation == 'cancel':
                self.learning = None
                self.binding_message = 'Binding cancelled.'
                return
            if operation == 'reset':
                self.save_bindings(dict(DEFAULT_BINDINGS))
                self.learning = None
                self.binding_message = 'Default bindings restored.'
                return
            action = body.get('action')
            if action not in DEFAULT_BINDINGS:
                raise ValueError('Unknown binding action.')
            if operation == 'clear':
                self.save_bindings(dict(self.bindings, **{action: None}))
                self.learning = None
                self.binding_message = 'Binding cleared.'
                return
            if operation != 'learn':
                raise ValueError('Unknown binding operation.')
            response = self.client.get('/status')
            response.raise_for_status()
            data = response.json()['input']
            if not self.ready(data):
                raise ValueError('Connect a supported gamepad first.')
            self.learning = dict(action=action, id=data['id'], until=time.monotonic()+15,
                                 released=not any(data['buttons'].values()),
                                 counts=dict(data.get('button_presses', {})))
            self.binding_message = 'Release held buttons, then press one button (15 seconds).'

    def capture(self, data):
        learn = self.learning
        if not self.ready(data) or data.get('id') != learn['id']:
            self.learning = None
            self.binding_message = 'Controller disconnected. Select Bind to try again.'
            return
        if time.monotonic() > learn['until']:
            self.learning = None
            self.binding_message = 'Binding timed out. Select Bind to try again.'
            return
        down = {k for k, value in data['buttons'].items() if value}
        counts = data.get('button_presses', {})
        if not learn['released']:
            learn['released'] = not down
            learn['counts'] = dict(counts)
            return
        pressed = down | {k for k, value in counts.items() if value > learn['counts'].get(k, 0)}
        if not pressed:
            return
        self.learning = None
        if len(pressed) != 1 or not pressed.issubset(BUTTON_NAMES):
            self.binding_message = 'Press only one supported button. Select Bind to try again.'
            return
        button = pressed.pop()
        other = next((a for a, b in self.bindings.items() if b == button and a != learn['action']), None)
        if other:
            self.binding_message = f'Button already assigned to {other}. Clear or rebind that action first.'
            return
        try:
            self.save_bindings(dict(self.bindings, **{learn['action']: button}))
            self.binding_message = 'Binding saved.'
        except (OSError, ValueError) as e:
            self.binding_message = f'Could not save binding: {e}'

    @staticmethod
    def ready(data):
        return (data.get('connected') and 0 <= time.monotonic() - data.get('stamp', 0) < .5
                and isinstance(data.get('id'), str))

    def release(self, reason='', stop=True):
        with self.controller.lock:
            active = self.enabled
            self.enabled = False
            self.controller.input_source = 'web'
            self.error = reason
            if active and stop:
                self.controller.set_mode('off')

    def enable(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('enabled must be true or false.')
        if not enabled:
            self.release()
            return
        response = self.client.get('/status')
        response.raise_for_status()
        data = response.json()['input']
        if not self.ready(data):
            raise ValueError('Connect a supported gamepad first.')
        if any(abs(x) > .05 for x in data['axes'].values()) or any(data['buttons'].values()):
            raise ValueError('Center the sticks and release all buttons before enabling control.')
        with self.controller.lock:
            if self.learning:
                raise ValueError('Finish or cancel button binding first.')
            if self.controller.mode in ('calibrate', 'servo_debug'):
                raise ValueError('Exit calibration or servo setup first.')
            self.controller.set_mode('off')
            self.controller.input_source = 'gamepad'
            self.identity, self.buttons = data['id'], dict(data['buttons'])
            self.enabled, self.error = True, ''

    def consume(self, data):
        with self.controller.lock:
            if self.learning:
                self.capture(data)
                return
            if not self.enabled:
                return
            if not self.ready(data) or data.get('id') != self.identity:
                self.release('Controller disconnected. Reconnect and enable control again.')
                return
            if self.controller.mode in ('calibrate', 'servo_debug'):
                self.release('Gamepad control paused for calibration or servo setup.', stop=False)
                return
            buttons = data['buttons']
            pressed = {name for name, down in buttons.items() if down and not self.buttons.get(name)}
            self.buttons = dict(buttons)
            try:
                if buttons.get(self.bindings['off']):
                    self.controller.set_mode('off')
                else:
                    for action in ('walk', 'hold', 'pick', 'roulade'):
                        if self.bindings[action] not in pressed:
                            continue
                        if action in ('walk', 'hold'):
                            self.controller.set_mode('policy' if action == 'walk' else 'hold')
                        elif action == 'pick':
                            self.controller.start_pick()
                        else:
                            self.controller.start_roulade()
                        break
                self.controller.command(twist=motion_from_input(data), source='gamepad')
                self.error = ''
            except ValueError as e:
                self.error = str(e)

    def run(self):
        while not self.stop_event.wait(.05):
            if not self.enabled and not self.learning:
                continue
            try:
                r = self.client.get('/status')
                r.raise_for_status()
                self.consume(r.json()['input'])
            except Exception:
                with self.controller.lock:
                    self.learning = None
                    self.binding_message = 'Gamepad service disconnected.'
                self.release('Gamepad service disconnected. Control stopped.')

    def close(self):
        self.stop_event.set()
        self.thread.join(timeout=2)
        self.release()
        self.client.close()


def register_gamepad(ui, controller):
    path = os.environ.get('DUCK_GAMEPAD_SOCKET', str(Path(__file__).resolve().parents[1] / 'data/gamepad.sock'))
    client = httpx.Client(transport=httpx.HTTPTransport(uds=path), base_url='http://gamepad',
                          timeout=httpx.Timeout(.5, connect=.3), trust_env=False)
    control = GamepadControl(controller, client)
    original = ui.app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original(app) as state:
            try:
                yield state
            finally:
                control.close()
    ui.app.router.lifespan_context = lifespan

    def status():
        try:
            r = client.get('/status')
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError):
            data = dict(bluetooth=dict(available=False, devices=[], error='Gamepad service unavailable.'),
                        input=dict(connected=False, devices=[]))
        data['control'] = control.status()
        return data

    async def action(path: str, request: Request):
        origin = request.headers.get('origin')
        if request.headers.get('x-duck-gamepad') != '1' or (origin and urlsplit(origin).netloc != request.headers.get('host')):
            return JSONResponse({'error': 'Request origin mismatch.'}, status_code=403)
        if path not in ('scan', 'stop-scan', 'connect', 'disconnect', 'forget', 'select', 'control', 'bindings', 'usb'):
            return JSONResponse({'error': 'Not found'}, status_code=404)
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > 4096:
                return JSONResponse({'error': 'Request too large.'}, status_code=413)
        import asyncio
        import json
        def apply():
            try:
                body = json.loads(data or b'{}')
                if not isinstance(body, dict):
                    raise ValueError('Expected an object.')
                if path == 'control':
                    control.enable(body.get('enabled'))
                elif path == 'bindings':
                    control.configure(body)
                else:
                    if path in ('connect', 'disconnect', 'forget', 'select'):
                        control.release()
                    r = client.post('/' + path, json=body, timeout=10)
                    if r.status_code != 200:
                        raise ValueError(r.json().get('error', 'Bluetooth operation failed.'))
                return status()
            except (ValueError, OSError, httpx.HTTPError) as e:
                return JSONResponse({'error': str(e)}, status_code=400)
        return await asyncio.to_thread(apply)

    ui.expose_api('GET', '/api/gamepad/status', status)
    ui.expose_api('POST', '/api/gamepad/{path:path}', action)
    return control
