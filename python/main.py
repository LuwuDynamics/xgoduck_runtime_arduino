import threading
import sys
from fastapi import HTTPException
from arduino.app_utils import App, Bridge
from arduino.app_bricks.web_ui import WebUI
from controller import Controller

# Bound GIL handoff latency between the 100 Hz receiver, policy and WebUI.
sys.setswitchinterval(0.001)
controller = Controller(Bridge)
ui = WebUI(port=9527, cors_origins='')
Bridge.provide('duck_state', controller.receive)
Bridge.provide('duck_boot_pd', controller.receive_boot_pd)
Bridge.provide('duck_raw', controller.receive_raw)
Bridge.provide('duck_servo_reply', controller.receive_servo_reply)

def mode(body: dict):
    try: return controller.set_mode(body['mode'])
    except (KeyError, ValueError) as exc: raise HTTPException(400, str(exc))

def command(body: dict):
    try: return controller.command(**body)
    except (TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def filtering(body: dict):
    try:
        leg, head = controller.set_action_alphas(**body)
        return {'leg':leg,'head':head}
    except (TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def pick(_body: dict = None):
    try: return controller.start_pick()
    except ValueError as exc: raise HTTPException(400, str(exc))

def cal_enter(_body: dict = None):
    try: return controller.set_mode('calibrate')
    except ValueError as exc: raise HTTPException(400, str(exc))

def cal_exit(_body: dict = None):
    try: return controller.set_mode('shadow')
    except ValueError as exc: raise HTTPException(400, str(exc))

def cal_targets(body: dict):
    try: return controller.cal_targets_set(body['targets'])
    except (KeyError, TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def cal_finish(body: dict):
    try: return controller.cal_finish(body['index'])
    except (KeyError, TypeError, ValueError) as exc: raise HTTPException(400, str(exc))

def servo_enter(_body: dict = None):
    try: return controller.set_mode('servo_debug')
    except ValueError as exc: raise HTTPException(400, str(exc))

def servo_exit(_body: dict = None):
    try: return controller.set_mode('shadow')
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
ui.expose_api('POST', '/api/mode', mode)
ui.expose_api('POST', '/api/command', command)
ui.expose_api('POST', '/api/filter', filtering)
ui.expose_api('POST', '/api/pick', pick)
ui.expose_api('POST', '/api/cal/enter', cal_enter)
ui.expose_api('POST', '/api/cal/exit', cal_exit)
ui.expose_api('POST', '/api/cal/targets', cal_targets)
ui.expose_api('POST', '/api/cal/finish', cal_finish)
ui.expose_api('POST', '/api/servo/enter', servo_enter)
ui.expose_api('POST', '/api/servo/exit', servo_exit)
ui.expose_api('POST', '/api/servo', servo_action)
worker = threading.Thread(target=controller.run, name='policy50', daemon=True)
worker.start()
try:
    App.run()
finally:
    controller.stop()
    worker.join(timeout=2)
