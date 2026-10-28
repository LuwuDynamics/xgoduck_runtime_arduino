import threading
import sys
import os
import secrets
from usb_startup import enable_host

# Host setup runs before the container is created. Verify readiness before
# opening Bridge, constructing the controller or starting either business loop.
enable_host()

from fastapi import HTTPException
from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI
from controller import Controller
from media_proxy import register_media
from gamepad import register_gamepad
from motion_access import MotionAccess
from voice_config import VoiceConfig
from voice_supervisor import VoiceSupervisor
from local_ipc import JsonServer
from app_paths import PRIVATE
from mouth_ipc import MouthReceiver

# Bound GIL handoff latency between the 100 Hz receiver, policy and WebUI.
sys.setswitchinterval(0.001)
controller = Controller(Bridge)
voice_config = VoiceConfig()
access = MotionAccess(controller, allowed=lambda: voice_config.get()['voice_motion_enabled'],
                      remote_allowed=lambda: voice_config.get()['remote_motion_enabled'])
token = secrets.token_urlsafe(32)
mouth_path = PRIVATE / 'mouth.sock'
os.environ['DUCK_MOUTH_SOCKET'] = str(mouth_path)
mouth_receiver = MouthReceiver(mouth_path, token, access.speech)
def voice_dispatch(method, path, body):
    if method == 'GET' and path == '/state':
        return {'motion': controller.status(), 'control': access.state()}
    if method == 'POST' and path == '/heartbeat':
        return access.heartbeat(**body)
    if method == 'POST' and path == '/action':
        return access.start_action(**body)
    if method == 'POST' and path == '/speech-mouth':
        return access.speech(**body)
    if method == 'POST' and path == '/cancel':
        return access.cancel('voice session suspended')
    raise ValueError('Unknown motion IPC route')
motion_port = int(os.environ.get('DUCK_MOTION_PORT', 9531))
voice_port = int(os.environ.get('DUCK_VOICE_PORT', 9530))
media_port = int(os.environ.get('DUCK_MEDIA_PORT', 9528))
ipc = JsonServer(motion_port, token, voice_dispatch)
voice = VoiceSupervisor(token, motion_port=motion_port, voice_port=voice_port,
                        on_exit=lambda: access.heartbeat(None))
os.environ['DUCK_MEDIA_URL'] = f'http://127.0.0.1:{media_port}'
ui = WebUI(port=9527, cors_origins='')
register_media(ui)
gamepad_control = register_gamepad(ui, controller)
Bridge.provide('duck_state', controller.receive)
Bridge.provide('duck_boot_pd', controller.receive_boot_pd)
Bridge.provide('duck_raw', controller.receive_raw)
Bridge.provide('duck_servo_reply', controller.receive_servo_reply)

def mode(body: dict):
    try: return access.web_mode(body['mode'])
    except (KeyError, ValueError) as exc: raise HTTPException(400, str(exc))

def command(body: dict):
    try: return access.web_command(**{k: v for k, v in body.items() if k != 'source'})
    except (TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def filtering(body: dict):
    try:
        leg, head = controller.set_action_alphas(**body)
        return {'leg':leg,'head':head}
    except (TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def pick(_body: dict = None):
    try: return access.web_action('pick')
    except ValueError as exc: raise HTTPException(400, str(exc))

def roulade(_body: dict = None):
    try: return access.web_action('roulade')
    except ValueError as exc: raise HTTPException(400, str(exc))

def cal_enter(_body: dict = None):
    try: return access.web_mode('calibrate')
    except ValueError as exc: raise HTTPException(400, str(exc))

def cal_exit(_body: dict = None):
    try: return access.web_mode('shadow')
    except ValueError as exc: raise HTTPException(400, str(exc))

def cal_targets(body: dict):
    try: return controller.cal_targets_set(body['targets'])
    except (KeyError, TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def cal_finish(body: dict):
    try: return controller.cal_finish(body['index'])
    except (KeyError, TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def servo_enter(_body: dict = None):
    try: return access.web_mode('servo_debug')
    except ValueError as exc: raise HTTPException(400, str(exc))

def servo_exit(_body: dict = None):
    try: return access.web_mode('shadow')
    except ValueError as exc: raise HTTPException(400, str(exc))

def servo_action(body: dict):
    try:
        return controller.servo_op(
            body['action'], body['target_id'],
            new_id=body.get('new_id'), kp=body.get('kp', 5), kd=body.get('kd', 20),
            raw_pos=body.get('raw_pos', 2047))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc))

ui.expose_api('GET', '/api/status', controller.status)
ui.expose_api('GET', '/api/voice/status', voice.status)
ui.expose_api('POST', '/api/mode', mode)
ui.expose_api('POST', '/api/command', command)
ui.expose_api('POST', '/api/filter', filtering)
ui.expose_api('POST', '/api/pick', pick)
ui.expose_api('POST', '/api/roulade', roulade)
ui.expose_api('POST', '/api/cal/enter', cal_enter)
ui.expose_api('POST', '/api/cal/exit', cal_exit)
ui.expose_api('POST', '/api/cal/targets', cal_targets)
ui.expose_api('POST', '/api/cal/finish', cal_finish)
ui.expose_api('POST', '/api/servo/enter', servo_enter)
ui.expose_api('POST', '/api/servo/exit', servo_exit)
ui.expose_api('POST', '/api/servo', servo_action)
worker = threading.Thread(target=controller.run, name='policy50', daemon=True)
worker.start()
ipc.start()
mouth_receiver.start()
access.start()
voice.start()
try:
    App.run()
finally:
    mouth_receiver.close()
    access.close()
    voice.close()
    ipc.close()
    controller.stop()
    worker.join(timeout=2)
