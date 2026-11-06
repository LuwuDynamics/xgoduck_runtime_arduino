"""Bounded voice requests; Bridge remains exclusively in the motion process."""
import json
import math
import re
from collections import deque
import threading
import time
import uuid
from recovery import Recovery
from speech_mouth import OUTPUT_HZ, SpeechMouthMotion
from motion_feedback import (LEG_IDS, HEAD_IDS, MOUTH_ID, SERVO_IDS,
                             feedback_ready, feedback_problem)

VOICE_TWIST_LIMITS = (.4, .3, 1.)  # Absolute ceiling; ordinary walking has tighter vx limits.
WALK_VX_LIMITS = (-.18, .2)
REMOTE_TWIST_LIMITS = (.15, .1, 1.)
COCKPIT_TWIST_LIMITS = (.15, .3, 1.)
COCKPIT_LEASE_S = .4
COCKPIT_ACTIONS = ('stand', 'sit', 'pick', 'roulade', 'left_kick', 'right_kick')
WALK_ACTIONS = ('move', 'turn', 'pick', 'roulade', 'left_kick', 'right_kick')
STAND_TIMEOUT_S = 5.
VOICE_ACTION_SECONDS = 10.
MAX_QUEUED_ACTIONS = 4
MAX_QUEUED_SECONDS = 30.
QUEUED_STAND_WAIT_SECONDS = 5.


class ForwardBlocked(Exception):
    """Expected refusal, converted to a normal action outcome at the IPC boundary."""
    def __init__(self, reason, avoidance=None):
        super().__init__(reason)
        self.avoidance = dict(avoidance or {})


def command_link_ready(status):
    """Sensor feedback alone does not prove Linux commands still reach the MCU."""
    age = status.get('mcu_command_age_us')
    bypass = status.get('timeout_checks_disabled') is True
    return (status.get('mcu_watchdog') is False and not isinstance(age, bool)
            and isinstance(age, (int, float)) and math.isfinite(age)
            and 0 <= age < 0xffffffff and (bypass or age <= 150000))


class MotionAccess:
    def __init__(self, controller, allowed=lambda: False, clock=time.monotonic, remote_allowed=lambda: False):
        self.controller, self.allowed, self.clock = controller, allowed, clock
        self.remote_allowed = remote_allowed
        self.remote_sequences = {}
        self.remote_forward_stopped = set()
        self.remote_directions = {}
        self.cockpit_sequences = {}
        self.cockpit_driver = None
        self.cockpit_deadline = 0.
        self.lock = controller.lock
        self.voice_seen = float('-inf')
        self.voice_session = None
        self.voice_epoch = 0
        self.action = None
        self.gamepad = None
        self.pending = deque()
        self.recent_actions = deque(maxlen=8)
        self.cancelled_at = float('-inf')
        self.stop_event = threading.Event()
        self.thread = None
        self.speech_thread = None
        self.speech_mouth = SpeechMouthMotion(controller, command_link_ready, clock)
        self.standing_since = None
        self.standing_observed = float('-inf')

    def _locomotion_running(self):
        return (self.controller.input_source == 'voice' and self._action_running()
                and self.action['kind'] in ('move', 'turn'))

    def _action_running(self):
        return self.action is not None and self.action['state'] == 'running'

    def _retire_remote_directions(self):
        for controller_id in self.remote_directions:
            self.remote_directions[controller_id] = 'awaiting_release'
        if self._action_running() and self.action.get('remote_controller'):
            self.remote_directions[self.action['remote_controller']] = 'awaiting_release'

    def _stop_gamepad_travel(self):
        if self.gamepad and self.gamepad.enabled:
            self.gamepad.wait_for_neutral = True
            if self.controller.input_source == 'gamepad':
                self._zero_twist()

    def _zero_twist(self):
        if any(self.controller.twist):
            self.controller.twist = [0.] * 3
            self.controller.generation += 1

    def movement_state(self, status, *, ongoing=False, required_ids=LEG_IDS, local_action=False, allow_hold=False):
        """Confirm standing before entry; allow normal gait tilt during a lease."""
        now = self.clock()
        recovery = status.get('recovery') or {}
        angle = recovery.get('angle_deg')
        holding = self.controller.mode == 'hold'
        if holding:
            gravity = status.get('gravity')
            norm = math.hypot(*gravity) if gravity else 0.
            if norm > 0 and math.isfinite(norm):
                angle = math.degrees(math.acos(max(-1., min(1., -gravity[2] / norm))))
            else:
                angle = None
        angle_known = (not isinstance(angle, bool) and isinstance(angle, (int, float))
                       and math.isfinite(angle) and angle >= 0)
        tilt_limit = Recovery.FALL_DEG if ongoing else Recovery.STAND_DEG
        if status.get('fault_hold'):
            reason = '运动已暂停且保持扭矩，请先说“站起来”恢复，再执行行走或转向。'
        elif status.get('arming'):
            reason = '正在等待 STM32 确认使能，请稍等后再移动。'
        elif not feedback_ready(status, required_ids):
            reason = feedback_problem(status, required_ids)
        elif not command_link_ready(status):
            reason = '运动命令未及时到达 STM32，请先检查控制通信。'
        elif status.get('enabled') is not True:
            reason = '舵机尚未使能，请先让鸭子站起来。'
        elif self.controller.mode not in ('hold', 'policy'):
            reason = '当前为仅推理或卸力模式，请先让鸭子站起来。'
        elif recovery.get('phase') != 'walk':
            reason = '鸭子正在起身或恢复姿态，请等待站稳后再遥控。'
        elif not angle_known or angle >= tilt_limit:
            reason = '鸭子趴倒、倾斜或姿态未知，请先完成站立。'
        elif (status.get('pick') or {}).get('active') or (status.get('roulade') or {}).get('active'):
            reason = '鸭子正在执行抓取或翻滚，请等待动作结束。'
        elif any((status.get(name) or {}).get('active') for name in ('sitstand', 'left_kick', 'right_kick')):
            reason = '鸭子正在执行坐站或踢腿，请等待动作结束。'
        else:
            reason = ''
        if reason or angle >= Recovery.STAND_DEG:
            self.standing_since = None
        elif self.standing_since is None or now - self.standing_observed > .15:
            self.standing_since = now
        self.standing_observed = now
        stable = max(0., now - self.standing_since) if self.standing_since is not None else 0.
        if not reason and holding and not allow_hold:
            reason = '当前为默认姿态保持，行走需要进入平衡模式。'
        ready = not reason and (local_action or ongoing or stable >= Recovery.STAND_SECONDS)
        return dict(movement_ready=bool(ready), standing_stable_s=stable,
                    movement_block_reason=reason or ('' if ready else '正在确认站稳，请稍等一秒后重试。'))

    def heartbeat(self, session=None):
        with self.lock:
            if session is not None and (isinstance(session, bool) or not isinstance(session, int) or session <= 0):
                raise ValueError('invalid voice session')
            # monotonic_ns is shared across processes on this Linux host.
            # A high-water mark prevents retired leases coming back, without
            # keeping an ever-growing set when USB audio is used repeatedly.
            if session is not None and session != self.voice_session and session <= self.voice_epoch:
                return {'ok': False, 'reason': 'voice session ended'}
            if session != self.voice_session:
                self.cancel('voice session changed')
                self.speech_mouth.reset()
                self.remote_sequences.clear()
                self.remote_forward_stopped.clear()
                self.remote_directions.clear()
                self.voice_session = session
                if session is not None:
                    self.voice_epoch = session
            self.voice_seen = self.clock()
        return {'ok': True}

    def state(self):
        with self.lock:
            status = self.controller.status()
            scopes = dict(stand=self._required_ids('stand', {'head': [0.] * 4, 'mouth': 0.}),
                          move=LEG_IDS, turn=LEG_IDS, pick=SERVO_IDS,
                          roulade=self._required_ids('roulade', {'mouth': 0.}), mouth=(MOUTH_ID,),
                          sit=self._required_ids('sit', {}),
                          left_kick=self._required_ids('left_kick', {}),
                          right_kick=self._required_ids('right_kick', {}))
            return dict(owner=self.controller.input_source, voice_motion_enabled=bool(self.allowed()),
                        remote_motion_enabled=bool(self.remote_allowed()),
                        action=dict(self.action) if self.action else None,
                        queued_actions=[dict(job) for job, _ in self.pending],
                        recent_actions=[dict(job) for job in list(self.recent_actions)[-4:]],
                        action_feedback_ready={kind: feedback_ready(status, ids) for kind, ids in scopes.items()},
                        head_feedback_ready={f'head{i+1}': feedback_ready(status, (sid,))
                                             for i, sid in enumerate(HEAD_IDS)},
                        tts_mouth=self.speech_mouth.status(),
                        **self.movement_state(status, ongoing=self._locomotion_running()))

    def _required_ids(self, kind, payload):
        if kind == 'head':
            return tuple(sid for i, sid in enumerate(HEAD_IDS)
                         if payload['head'][i] is not None
                         and payload['head'][i] != self.controller.head[i])
        if kind == 'mouth':
            return (MOUTH_ID,) if payload['mouth'] != self.controller.mouth else ()
        if kind == 'pick':
            return SERVO_IDS
        if kind in ('roulade', 'sit', 'left_kick', 'right_kick'):
            return LEG_IDS + HEAD_IDS + ((MOUTH_ID,) if self.controller.mouth else ())
        ids = LEG_IDS
        if kind == 'stand':
            ids += tuple(sid for sid, value in zip(HEAD_IDS, self.controller.head) if value)
            if self.controller.mouth:
                ids += (MOUTH_ID,)
        return ids

    def web_command(self, **body):
        with self.lock:
            result = self.controller.command(**body, source='web')
            if body.get('mouth') is not None and 'ignored' not in result:
                self.speech_mouth.override(restore=False)
            return result

    def speech(self, session, utterance, sequence, points):
        with self.lock:
            if (isinstance(session, bool) or not isinstance(session, int)
                    or session != self.voice_session or self.clock() - self.voice_seen > .8):
                raise ValueError('voice session ended or heartbeat is stale')
            return self.speech_mouth.submit(session, utterance, sequence, points)

    def web_mode(self, mode):
        if mode not in ('shadow', 'off', 'hold', 'policy', 'calibrate', 'servo_debug'):
            raise ValueError('unknown mode')
        with self.lock:
            previous_mode = self.controller.mode
            result = self.controller.set_mode(mode)
            self._release_cockpit()
            self._stop_gamepad_travel()
            self.speech_mouth.override('manual mode takeover', restore=previous_mode == mode)
            self._retire_remote_directions()
            self.cancel('manual takeover', keep_arm=True)
            return result

    def web_action(self, name, *args):
        with self.lock:
            self.speech_mouth.override('special action owns mouth')
            result = getattr(self.controller, 'start_' + name)(*args)
            self._manual_action_started()
            return result

    def web_sitstand(self, action):
        with self.lock:
            self.speech_mouth.override('special action owns mouth')
            result = self.controller.set_sitstand(action)
            self._manual_action_started()
            return result

    def _manual_action_started(self):
        # The controller has accepted a local button press. Cancel old jobs
        # without stopping the special model the button just started.
        if self.controller.input_source == 'voice':
            self.cancelled_at = self.clock()
            if self._action_running():
                self._finish_action('cancelled', reason='local action selected')
            self._cancel_pending('local action selected')
            self._zero_twist()
            self._restore_input_source()
        self._release_cockpit()

    def web_roller(self, enabled):
        with self.lock:
            result = self.controller.set_roller_mode(enabled)
            self._release_cockpit()
            self.speech_mouth.override('manual walking-model takeover')
            self._retire_remote_directions()
            self.cancel('manual walking-model takeover')
            return result

    def _release_cockpit(self):
        c = self.controller
        if c.input_source == 'cockpit':
            self._zero_twist()
            c.input_source = 'web'
        self.cockpit_driver = None
        self.cockpit_deadline = 0.

    def _stop_motion(self, reason):
        self._retire_remote_directions()
        self._release_cockpit()
        self._stop_gamepad_travel()
        self.cancel(reason, stop_manual=True)

    def _restore_input_source(self):
        if self.controller.input_source == 'voice':
            if self.cockpit_driver and self.clock() < self.cockpit_deadline:
                self.controller.input_source = 'cockpit'
            elif self.gamepad and self.gamepad.enabled:
                self.controller.input_source = 'gamepad'
            else:
                self.controller.input_source = 'web'

    def cockpit_motion(self, controller_id, sequence, kind, twist=None):
        """Local browser control. A held pad needs a live, single-owner lease."""
        if (not isinstance(controller_id, str) or not re.fullmatch(r'[a-f0-9]{24}', controller_id)
                or type(sequence) is not int or not 0 < sequence < 2**53):
            raise ValueError('Invalid cockpit command sequence.')
        if kind not in (*COCKPIT_ACTIONS, 'begin', 'drive', 'release', 'stop', 'torque_off'):
            raise ValueError('Unknown cockpit action.')
        drive = kind in ('begin', 'drive')
        if drive:
            if (not isinstance(twist, list) or len(twist) != 3
                    or any(type(v) not in (int, float) or not -limit <= v <= limit
                           for v, limit in zip(twist, COCKPIT_TWIST_LIMITS))):
                raise ValueError('Cockpit joystick input exceeds bounds.')
        elif twist is not None:
            raise ValueError('Unexpected joystick input.')

        with self.lock:
            if sequence <= self.cockpit_sequences.get(controller_id, 0):
                raise ValueError('Cockpit command retired.')
            self.cockpit_sequences.pop(controller_id, None)
            self.cockpit_sequences[controller_id] = sequence
            if len(self.cockpit_sequences) > 64:
                del self.cockpit_sequences[next(iter(self.cockpit_sequences))]

            c = self.controller
            if kind == 'torque_off':
                return self.web_mode('off')
            if kind == 'release':
                if self.cockpit_driver == controller_id:
                    self._release_cockpit()
                return {'ok': True}
            if (c.input_source == 'gamepad' or self.gamepad and self.gamepad.enabled
                    or c.mode in ('calibrate', 'servo_debug')):
                raise ValueError('Gamepad, calibration or servo setup owns motion.')
            if kind == 'stop':
                self._stop_motion('stopped in cockpit')
                return {'ok': True}
            c._require_battery()
            held = self.cockpit_driver is not None
            if held and self.clock() >= self.cockpit_deadline:
                self._release_cockpit()
                held = False
            if held and self.cockpit_driver != controller_id:
                raise ValueError('Another cockpit is driving Duck.')
            if kind == 'drive' and not held:
                raise ValueError('Joystick timed out. Release it and press again.')
            if drive and (self._action_running() or self.pending):
                self.cockpit_driver = controller_id
                self.cockpit_deadline = self.clock() + COCKPIT_LEASE_S
                return {'ok': True, 'paused': True}

            status = c.status()
            payload = {'twist': [0.] * 3, 'head': [0.] * 4, 'mouth': 0.}
            if kind == 'stand':
                self._require_feedback(status, required_ids=self._required_ids(kind, payload),
                                       resume_hold=True)
            else:
                movement = self.movement_state(
                    status, ongoing=drive and held and c.input_source == 'cockpit' and any(c.twist),
                    required_ids=self._required_ids(kind, payload), allow_hold=kind == 'sit')
                if not movement['movement_ready']:
                    raise ValueError(movement['movement_block_reason'])
            if drive:
                if c.roller_mode:
                    twist = [twist[0], 0., max(-.25, min(.25, twist[2]))]
                c.input_source = 'cockpit'
                self.cockpit_driver = controller_id
                if kind == 'begin':
                    c.command(twist=[0.] * 3, source='cockpit')
                self.cockpit_deadline = self.clock() + COCKPIT_LEASE_S
                return c.command(twist=twist, source='cockpit')
            self._apply_action(kind, payload, source=c.input_source)
            self._manual_action_started()
            return {'ok': True}

    def start_action(self, kind, duration=1., session=None, remote=False, controller_id=None, sequence=None,
                     issued_at=None, **values):
        with self.lock:
            if (isinstance(session, bool) or not isinstance(session, int)
                    or session != self.voice_session or self.clock() - self.voice_seen > .8):
                raise ValueError('voice session ended or heartbeat is stale')
            if not isinstance(remote, bool):
                raise ValueError('remote must be boolean')
            if issued_at is not None:
                if (isinstance(issued_at, bool) or not isinstance(issued_at, (int, float))
                        or not math.isfinite(issued_at) or not 0 <= issued_at <= self.clock()):
                    raise ValueError('invalid motion request timestamp')
                if kind not in ('stop', 'torque_off') and (issued_at < self.cancelled_at or self.clock() - issued_at > 1.):
                    raise ValueError('Motion request expired or predates stop. 旧动作已取消，请重新发出指令。')
            # Validate against the existing cutoff first. A ToF stop observed
            # during this request is a normal block, not an expired IPC request.
            interrupted = self._interrupt_obstructed_forward(self.controller.status())
            if remote:
                if (not isinstance(controller_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,64}', controller_id)
                        or isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 < sequence < 2**53):
                    raise ValueError('invalid remote command sequence')
                if sequence <= self.remote_sequences.get(controller_id, 0):
                    raise ValueError('remote command retired')
                if kind not in ('move', 'turn', 'stop'):
                    raise ValueError('unsupported remote action')
                if kind != 'stop' and self.remote_directions.get(controller_id) == 'awaiting_release':
                    raise ValueError('Remote direction retired; release the direction key and press again.')
            if kind == 'stop':
                if remote:
                    self.remote_forward_stopped.discard(controller_id)
                    self.remote_directions.pop(controller_id, None)
                    self.remember_remote(controller_id, sequence)
                    if self._action_running() and self.action.get('remote_controller') == controller_id:
                        self._zero_twist()
                        self._finish_action('completed', result='Remote direction released.')
                    return {'status': 'completed', 'message': 'Remote direction released.'}
                self._stop_motion('stopped by voice')
                return {'status': 'completed', 'message': 'Voice motion stopped.'}
            if kind == 'torque_off':
                self._retire_remote_directions()
                self.cancel('torque off')
                self.controller.set_mode('off')
                self._release_cockpit()
                self._stop_gamepad_travel()
                return {'status': 'completed', 'message': 'Torque off.'}
            if kind not in ('stand', 'move', 'turn', 'head', 'mouth', 'pick', 'roulade',
                            'sit', 'left_kick', 'right_kick', 'walking_mode', 'default_pose'):
                raise ValueError('unsupported duck action')
            if getattr(self.controller, 'battery_phase', 'normal') != 'normal':
                raise ValueError('低电压保护中，请充电后重新使能。 Low battery protection is active.')
            if not (self.remote_allowed() if remote else self.allowed()):
                raise ValueError('Remote motion disabled; enable it in the control page.' if remote else
                                 'Voice motion disabled. 语音运动未开启，请先说“开启语音运动”；此开关本身不启动舵机。')
            if self.controller.mode in ('calibrate', 'servo_debug'):
                raise ValueError('Exit calibration / servo setup first.')
            if kind == 'default_pose':
                self._require_feedback(self.controller.status(), required_ids=(), resume_hold=True)
                result = self.web_mode('hold')
                return dict(status='completed', mode=result['mode'])
            if kind == 'walking_mode':
                selected = values.get('walking_mode')
                if selected not in ('walk', 'roller'):
                    raise ValueError('walking mode must be walk or roller')
                if self.action and self.action['state'] == 'running' and self.action['kind'] == 'stand':
                    raise ValueError('正在起身，请站稳后再切换行走模式。')
                result = self.controller.set_roller_mode(selected == 'roller')
                self.cancel('walking mode changed by voice')
                return dict(status='completed', walking_mode=selected, roller=result)
            if kind == 'stand' and getattr(self.controller, 'fault_hold', False):
                self.cancel('explicit resume after motion pause')
            renewing = (remote and self._action_running()
                        and self.action.get('remote_controller') == controller_id)
            busy = bool(self.pending or (self._action_running() and not renewing))
            if isinstance(duration, bool):
                raise ValueError('duration must be numeric')
            duration = float(duration)
            if not math.isfinite(duration) or not .1 <= duration <= VOICE_ACTION_SECONDS:
                raise ValueError('duration must be 0.1–10 seconds. 单次动作支持 0.1–10 秒。')
            if remote and duration > 1:
                raise ValueError('remote lease must be at most one second')
            payload = {'twist': [0., 0., 0.]}
            if kind in ('move', 'turn', 'head'):
                key, limits = (('head', [.5] * 4) if kind == 'head' else
                               ('twist', REMOTE_TWIST_LIMITS if remote else VOICE_TWIST_LIMITS))
                v = values.get(key)
                if not isinstance(v, (list, tuple)) or len(v) != len(limits) or any(isinstance(x, bool) for x in v):
                    raise ValueError('wrong command dimension')
                if kind == 'head' and all(x is None for x in v):
                    raise ValueError('Specify at least one head axis. 请指定要调整的头颈轴。')
                v = [None if kind == 'head' and x is None else float(x) for x in v]
                if any(x is not None and (not math.isfinite(x) or abs(x) > limit)
                       for x, limit in zip(v, limits)):
                    raise ValueError('voice command exceeds bounds')
                if (key == 'twist' and getattr(self.controller, 'roller_mode', False) is not True
                        and not WALK_VX_LIMITS[0] <= v[0] <= WALK_VX_LIMITS[1]):
                    raise ValueError('walking vx must be between -0.18 and 0.2')
                payload[key] = v
                if key == 'twist' and getattr(self.controller, 'roller_mode', False) is True:
                    if v[1]:
                        raise ValueError('Roller 模式不支持侧移，请用 Turn 转向。')
                    if remote:
                        v[2] = max(-.25, min(.25, v[2]))
                    elif abs(v[2]) > .25:
                        raise ValueError('Roller 模式转向范围为 ±0.25 rad/s。')
            elif kind == 'mouth':
                mouth = values.get('mouth')
                if isinstance(mouth, bool) or not isinstance(mouth, (int, float)) or not 0 <= mouth <= 30:
                    raise ValueError('mouth must be 0–30 degrees')
                payload['mouth'] = mouth
            elif kind == 'pick':
                duration = 4.
            elif kind == 'roulade':
                duration = 1.9  # The existing Controller.ROULADE_S window.
                payload.update(head=[0.] * 4, mouth=0.)
            elif kind == 'stand':
                duration = STAND_TIMEOUT_S
                payload.update(head=[0.] * 4, mouth=0.)
            elif kind == 'sit':
                duration = 1.
                payload.update(head=[0.] * 4, mouth=0.)
            elif kind in ('left_kick', 'right_kick'):
                duration = .4
                payload.update(head=[0.] * 4, mouth=0.)
            forward_latched = remote and controller_id in self.remote_forward_stopped and payload['twist'][0] > 0
            if remote and not forward_latched:
                self.remote_forward_stopped.discard(controller_id)
            job = dict(id=uuid.uuid4().hex[:12], kind=kind, state='queued', accepted_at=self.clock(),
                       duration_s=duration, command_twist=list(payload['twist']))
            for key in ('head', 'mouth'):
                if key in payload:
                    job['command_' + key] = payload[key]
            if remote:
                job['remote_controller'] = controller_id
            if forward_latched or interrupted and payload['twist'][0] > 0:
                if remote:
                    self.remember_remote(controller_id, sequence)
                return self._blocked_forward(job, ForwardBlocked(
                    ('前进已被 ToF 停止，请先松开方向键再重新前进；仍可后退或原地转向。' if forward_latched else
                     self.action['reason']),
                    self.controller.status().get('obstacle_avoidance')))
            c = self.controller
            if remote and (busy or self.cockpit_driver and self.clock() < self.cockpit_deadline
                           or c.input_source == 'gamepad' and any(c.twist)
                           or c.mode == 'policy' and (c.sitstand_phase == 'stand' or c.pick_active
                                                     or c.roulade_until or c.kick_until)):
                self.remember_remote(controller_id, sequence)
                self.remote_directions[controller_id] = 'paused'
                return dict(status='paused', reason='当前动作占用腿部，暂缓方向控制。')
            manual_driving = (c.input_source != 'voice' or self._action_running()
                              and self.action.get('remote_controller'))
            if kind == 'head' and manual_driving and any(c.twist) and not self.pending:
                return self._set_head_while_driving(job, payload)
            if busy:
                remaining = max(0., self.action['deadline'] - self.clock()) if self._action_running() else 0.
                if len(self.pending) >= MAX_QUEUED_ACTIONS:
                    raise ValueError('Voice action queue is full. 最多排队 4 个动作，请等待或先停止。')
                if remaining + sum(item['duration_s'] for item, _ in self.pending) + duration > MAX_QUEUED_SECONDS:
                    raise ValueError('Voice action queue exceeds 30 seconds. 待执行动作累计不能超过 30 秒。')
                self.pending.append((job, payload))
                self._log_action(job)
                return dict(status='queued', action_id=job['id'], duration_s=duration, queue_position=len(self.pending))
            try:
                started = self._begin_action(job, payload, ongoing=bool(renewing and self._locomotion_running()))
            except ForwardBlocked as blocked:
                if remote:
                    self.remember_remote(controller_id, sequence)
                return self._blocked_forward(job, blocked)
            except ValueError:
                if remote:
                    self.remember_remote(controller_id, sequence)
                raise
            if remote:
                self.remember_remote(controller_id, sequence)
                self.remote_directions.pop(controller_id, None)
            if not started:
                self._log_action(job)
                return dict(status='queued', action_id=job['id'], duration_s=duration,
                            queue_position=1, reason=job['waiting_for'])
            return dict(status='accepted', action_id=job['id'], duration_s=duration)

    def _set_head_while_driving(self, job, payload):
        status = self.controller.status()
        required_ids = self._required_ids('head', payload)
        self._require_feedback(status, required_ids=required_ids)
        movement = self.movement_state(status, ongoing=True, local_action=True, required_ids=required_ids)
        if not movement['movement_ready']:
            raise ValueError(movement['movement_block_reason'])
        c = self.controller
        head = self._head_targets(payload)
        web_time = c.web_time
        c.command(head=head, source=c.input_source)
        c.web_time = web_time
        job.update(command_head=head, required_joint_ids=list(required_ids))
        self._finish_action('completed', job=job, result='Head target applied during driving.')
        return dict(status='completed', action_id=job['id'], reason='已下发转头目标。')

    def _head_targets(self, payload):
        return [old if value is None else value for old, value in zip(self.controller.head, payload['head'])]

    def _require_feedback(self, status, *, required_ids=LEG_IDS, resume_hold=False):
        if not feedback_ready(status, required_ids):
            raise ValueError('Feedback not ready. ' + feedback_problem(status, required_ids))
        can_resume = (resume_hold and status.get('enabled') is True
                      and status.get('fault_hold_supported') is True
                      and status.get('fault_hold') is True)
        if status.get('fault_hold') and status.get('fault_hold_supported') and not can_resume:
            raise ValueError('Motion paused with torque retained. 运动已暂停且保持扭矩；'
                             '请先说“站起来”恢复，再执行行走或转向。')
        if not command_link_ready(status) and not can_resume:
            raise ValueError('MCU command link is stale or watchdog paused motion. '
                             '运动命令未到达 STM32，暂不能启动；请先修复 Bridge 通信。')

    def _begin_action(self, job, payload, *, ongoing=False, queued=False, after_roll=False):
        """One execution gate for immediate and queued actions; never poll hardware."""
        remote = bool(job.get('remote_controller'))
        if not (self.remote_allowed() if remote else self.allowed()):
            raise ValueError('Motion permission removed.')
        c = self.controller
        if c.mode in ('calibrate', 'servo_debug'):
            raise ValueError('Exit calibration / servo setup first.')
        c._require_battery()
        status = c.status()
        kind = job['kind']
        required_ids = self._required_ids(kind, payload)
        self._require_feedback(status, required_ids=required_ids, resume_hold=kind == 'stand')
        avoidance = status.get('obstacle_avoidance') or {}
        if kind != 'stand':
            movement = self.movement_state(status, ongoing=ongoing, required_ids=required_ids,
                                           local_action=kind in ('head', 'mouth'), allow_hold=kind == 'sit')
            if not movement['movement_ready']:
                if not remote and self._needs_stand(kind, status, required_ids):
                    self._start_stand_before(job, payload, queued)
                    return False
                if not queued:
                    raise ValueError('Motion not ready. ' + movement['movement_block_reason'])
                # A deliberate roll may finish in get-up. Its following action
                # can wait for standing; falling locomotion still cancels.
                gait = self.movement_state(status, ongoing=True, required_ids=required_ids)
                angle = (status.get('recovery') or {}).get('angle_deg')
                roll_recovery = (after_roll
                    and self.controller.mode == 'policy' and status.get('enabled') is True
                    and (status.get('recovery') or {}).get('phase') in ('walk', 'default_pose', 'getup')
                    and not isinstance(angle, bool) and isinstance(angle, (int, float))
                    and math.isfinite(angle) and 0 <= angle <= 180)
                if not gait['movement_ready'] and not roll_recovery:
                    raise ValueError(gait['movement_block_reason'])
                since = job.setdefault('waiting_since', self.clock())
                job['waiting_for'] = movement['movement_block_reason']
                if self.clock() - since < QUEUED_STAND_WAIT_SECONDS:
                    return False
                raise ValueError('Queued action could not confirm standing. ' + movement['movement_block_reason'])
        if payload['twist'][0] > 0:
            guard = getattr(self.controller, 'forward_avoidance', None)
            if guard is not None:
                avoidance = guard.preview(payload['twist'][0])
            if avoidance.get('enabled') and not avoidance.get('forward_ready'):
                raise ForwardBlocked(avoidance.get('blocking_reason') or
                                     'ToF 测距不可用，已暂停前进；恢复后请重新发指令。', avoidance)
        previous = c.input_source
        try:
            c.input_source = 'voice'
            if kind in COCKPIT_ACTIONS:
                self._zero_twist()
            actual = dict(payload)
            if kind == 'head':
                actual['head'] = self._head_targets(payload)
                job['command_head'] = list(actual['head'])
            reply = self._apply_action(kind, actual, source='voice')
            if reply.get('forward_blocked'):
                raise ForwardBlocked(reply['reason'], self.controller.status().get('obstacle_avoidance'))
        except ForwardBlocked:
            self.controller.input_source = previous
            self.controller.twist = [0.] * 3
            raise
        except Exception as exc:
            self.controller.input_source = previous
            # Clear AI inputs without changing the restored control loop.
            self.controller.twist = [0.0] * 3
            self.controller.head = [0.0] * 4
            self.controller.mouth = 0.0
            self.controller.last_error = 'Motion command failed: ' + str(exc)
            raise
        started = self.clock()
        job.pop('waiting_for', None)
        job.pop('waiting_since', None)
        job.update(state='running', started_at=started, deadline=started + job['duration_s'],
                   obstacle_stop_count=avoidance.get('stop_count', 0),
                   required_joint_ids=list(required_ids),
                   queue_wait_s=round(max(0., started - job['accepted_at']), 3))
        self.action = job
        self._log_action(job)
        return True

    def _needs_stand(self, kind, status, required_ids):
        if kind not in WALK_ACTIONS or status.get('enabled') is not True or status.get('arming'):
            return False
        c = self.controller
        if c.mode == 'hold':
            return self.movement_state(status, required_ids=required_ids, allow_hold=True)['movement_ready']
        recovery = status.get('recovery') or {}
        angle = recovery.get('angle_deg')
        return (c.mode == 'policy' and c.sitstand_phase in ('sit', 'stand')
                and recovery.get('phase') == 'walk' and type(angle) in (int, float)
                and 0 <= angle < Recovery.FALL_DEG)

    def _start_stand_before(self, job, payload, queued):
        # Use the same verified stand transition and timeout as an explicit
        # Stand call. The requested action still has its own ID and timer.
        now = self.clock()
        stand = dict(id=uuid.uuid4().hex[:12], kind='stand', accepted_at=now,
                     duration_s=STAND_TIMEOUT_S, command_twist=[0.] * 3, prepares_action=job['id'])
        self._begin_action(stand, dict(twist=[0.] * 3, head=[0.] * 4, mouth=0.))
        self.standing_since = None
        job['waiting_for'] = '正在起立或切换到平衡模式，站稳后继续执行。'
        if not queued:
            self.pending.appendleft((job, payload))

    def _apply_action(self, kind, payload, source):
        if kind in ('mouth', *COCKPIT_ACTIONS):
            self.speech_mouth.override('explicit action owns mouth')
        c = self.controller
        if kind == 'stand':
            c._stop_pick_locked()
            c._stop_roulade_locked()
            c._stop_kick_locked()
            # Preserve the seated model's history through sit -> stand.
            if c.sitstand_phase == 'sit':
                c.set_sitstand('stand')
            elif c.sitstand_phase != 'stand':
                c.set_mode('policy')
        elif kind == 'sit':
            c.set_sitstand('sit')
        else:
            c.set_mode('policy')
        reply = c.command(**payload, source=source)
        if reply.get('forward_blocked'):
            return reply
        if kind == 'pick':
            c.start_pick()
        elif kind == 'roulade':
            c.start_roulade()
        elif kind in ('left_kick', 'right_kick'):
            c.start_kick('left' if kind == 'left_kick' else 'right')
        return reply

    def _blocked_forward(self, job, blocked):
        reason, avoidance = str(blocked), blocked.avoidance
        if self.action and self.action['state'] == 'running':
            self._finish_action('blocked', reason=reason)
        self.cancel(reason)
        self.action = job
        self._finish_action('blocked', reason=reason,
                            front_distance_mm=avoidance.get('front_distance_mm'),
                            requires_new_command=True)
        return dict(status='blocked', action_id=job['id'], started=False,
                    reason=reason, front_distance_mm=avoidance.get('front_distance_mm'),
                    tof_available=avoidance.get('available'), requires_new_command=True)

    def _dispatch_pending(self):
        if not self.pending or (self.action and self.action['state'] == 'running'):
            return
        if not self.action or self.action['state'] != 'completed':
            self.cancel((self.action or {}).get('reason') or 'previous action did not complete')
            return
        job, payload = self.pending[0]
        continuing = self.action['kind'] in ('move', 'turn') and job['kind'] in ('move', 'turn')
        try:
            if self._begin_action(job, payload, ongoing=continuing, queued=True,
                                  after_roll=self.action['kind'] == 'roulade'):
                self.pending.popleft()
        except ForwardBlocked as blocked:
            self.pending.popleft()
            self._blocked_forward(job, blocked)
        except Exception as exc:
            if self.pending and self.pending[0][0] is job:
                self.pending.popleft()
            self.action = job
            self._finish_action('failed', reason='Queued action could not start. ' + str(exc))
            self.cancel('queued action failed')

    def _log_action(self, job=None):
        job = self.action if job is None else job
        record = {key: job[key] for key in
                  ('id', 'kind', 'state', 'duration_s', 'elapsed_s', 'queue_wait_s',
                   'command_twist', 'command_head', 'command_mouth', 'required_joint_ids',
                   'prepares_action', 'reason') if key in job}
        record['source'] = 'remote' if job.get('remote_controller') else 'voice'
        print('[motion-access]', json.dumps(record, ensure_ascii=False), flush=True)

    def _finish_action(self, state, *, job=None, **details):
        job = self.action if job is None else job
        elapsed = max(0., self.clock() - job['started_at']) if 'started_at' in job else 0.
        job.update(state=state, elapsed_s=round(elapsed, 3), **details)
        self.recent_actions.append(dict(job))
        self._log_action(job)

    def _cancel_pending(self, reason):
        while self.pending:
            job, _ = self.pending.popleft()
            self._finish_action('cancelled', job=job, reason=reason)

    def remember_remote(self, controller_id, sequence):
        self.remote_sequences[controller_id] = sequence
        if len(self.remote_sequences) > 64:
            retired = next(iter(self.remote_sequences))
            self.remote_forward_stopped.discard(retired)
            self.remote_directions.pop(retired, None)
            self.remote_sequences.pop(retired)

    def _stop_roulade(self):
        was_rolling = bool(self.controller.roulade_until)
        self.controller._stop_roulade_locked()
        if was_rolling:
            # Match Controller.step's normal end-of-roll cleanup even when
            # this supervisor's timer or Stop arrives first.
            self.controller.recovery.fall_elapsed = 0.
            self.controller.policy_needs_reset = True

    def _interrupt_obstructed_forward(self, status):
        job = self.action
        if not (job and job['state'] == 'running' and job['command_twist'][0] > 0
                and self.controller.input_source == 'voice'):
            return False
        avoidance = status.get('obstacle_avoidance') or {}
        stopped = avoidance.get('stop_count', 0) > job.get('obstacle_stop_count', 0)
        if not stopped and (not avoidance.get('enabled') or avoidance.get('forward_ready')):
            return False
        reason = ((avoidance.get('last_stop') or {}).get('reason') if stopped else '')
        reason = reason or avoidance.get('blocking_reason') or 'ToF 前方测距不可用，已停止前进。'
        reason += ' 恢复后请重新发出前进指令。'
        if job.get('remote_controller'):
            # Phone clients reuse a controller ID until leaving the page. Stop
            # forward renewals until release/reverse/turn, not every direction.
            self.remote_forward_stopped.add(job['remote_controller'])
        self._finish_action('blocked', reason=reason)
        self.cancel(reason)
        return True

    def cancel(self, reason, *, stop_manual=False, keep_arm=False):
        with self.lock:
            self.cancelled_at = self.clock()
            c = self.controller
            standing = self._action_running() and self.action['kind'] == 'stand' and c.input_source == 'voice'
            if not keep_arm and (stop_manual or standing):
                cancel_arming = getattr(c, 'cancel_arming', None)
                if cancel_arming:
                    cancel_arming()
            if self._action_running():
                self._finish_action('cancelled', reason=reason)
            self._cancel_pending(reason)
            if c.input_source == 'voice' or stop_manual:
                c.generation += 1
                self._zero_twist()
                if c.pick_active:
                    c.mouth = 0.
                    c.policy_needs_reset = True
                c._stop_pick_locked()
                self._stop_roulade()
                c._stop_kick_locked()
                try:
                    if c.mode not in ('calibrate', 'servo_debug'):
                        c.command(twist=[0, 0, 0], mouth=0, source=c.input_source)
                finally:
                    self._restore_input_source()
            # Keep existing balance and sensor guards: a chat failure must not
            # release a standing biped's torque. Explicit TorqueOff still does.
            return {'ok': True}

    def _tick_speech_locked(self, status):
        if self.voice_session is None or self.clock() - self.voice_seen > .8:
            self.speech_mouth.override('voice unavailable')
        elif (self.action and self.action['state'] == 'running'
              and self.action['kind'] in ('mouth', 'pick', 'roulade', 'stand', 'sit', 'left_kick', 'right_kick')):
            self.speech_mouth.override('explicit action owns mouth')
        else:
            self.speech_mouth.tick(status)

    def tick_speech(self):
        with self.lock:
            if self.speech_mouth.deadline:
                self._tick_speech_locked(self.controller.status())

    def tick(self, *, speech=True):
        with self.lock:
            status = self.controller.status()
            self._interrupt_obstructed_forward(status)
            running = self._action_running()
            required_ids = self.action.get('required_joint_ids', LEG_IDS) if running else ()
            if speech:
                self._tick_speech_locked(status)
            cockpit = self.controller.input_source == 'cockpit'
            movement = self.movement_state(status, ongoing=cockpit or self._locomotion_running())
            if self.cockpit_driver and (self.clock() >= self.cockpit_deadline
                                       or cockpit and not movement['movement_ready']):
                self._release_cockpit()
            if self.controller.input_source != 'voice':
                if self.pending or (self.action and self.action['state'] == 'running'):
                    self.cancelled_at = self.clock()
                    self._retire_remote_directions()
                if self.action and self.action['state'] == 'running':
                    self._finish_action('cancelled', reason='control source changed')
                self._cancel_pending('control source changed')
                return
            allowed = self.remote_allowed if running and self.action.get('remote_controller') else self.allowed
            if (running and not allowed()) or self.voice_session is None or self.clock() - self.voice_seen > .8:
                self.cancel('voice unavailable or motion disabled')
            elif self.controller.mode != 'policy':
                self.cancel(status.get('last_error') or 'controller left policy mode')
            elif (status.get('fault_hold') or not feedback_ready(status, required_ids) or
                  (not command_link_ready(status) and not
                   (status.get('arming') and self.action and self.action['state'] == 'running'
                    and self.action['kind'] == 'stand'))):
                self._retire_remote_directions()
                self.cancel('Motion paused with last targets held.' if status.get('fault_hold') else
                            'Motion feedback / MCU command link lost. ' + feedback_problem(status, required_ids))
            elif (self.action and self.action['state'] == 'running' and self.action['kind'] != 'stand'
                  and status.get('enabled') is not True):
                self.cancel('Motor enable feedback lost.')
            elif (self.action and self.action['state'] == 'running' and self.action['kind'] in ('move', 'turn')
                  and not movement['movement_ready']):
                self._retire_remote_directions()
                self.cancel(movement['movement_block_reason'])
            elif (running and self.action['kind'] in ('pick', 'sit', 'left_kick', 'right_kick')
                  and (status.get('recovery') or {}).get('phase') != 'walk'):
                self._retire_remote_directions()
                self.cancel('动作中断，鸭子正在恢复姿态。')
            elif (self.action and self.action['state'] == 'running' and self.action['kind'] == 'stand'
                  and movement['movement_ready']):
                self._finish_action('completed', result='Balance feedback ready: motors enabled and upright for one second.')
            elif self.action and self.action['state'] == 'running' and self.clock() >= self.action['deadline']:
                self._zero_twist()
                self.controller._stop_pick_locked()
                if self.action['kind'] == 'roulade':
                    self._stop_roulade()
                if self.action['kind'] in ('left_kick', 'right_kick'):
                    self.controller._stop_kick_locked()
                self.controller.command(twist=[0, 0, 0], mouth=0, source='voice')
                if self.action['kind'] == 'stand':
                    self._finish_action('failed', reason='Standing not confirmed. ' + movement['movement_block_reason'])
                else:
                    self._finish_action('completed', result='Timed command ended; physical success not evaluated.')
            elif self.action and self.action['state'] == 'running':
                # The stable controller clears travel after one second without
                # input. Renew only after the session, feedback and action checks
                # above pass; the original action deadline is never extended.
                self.controller.web_time = time.monotonic()
            self._dispatch_pending()
            if not self.pending and not self._action_running():
                self._restore_input_source()

    def start(self):
        def run():
            while not self.stop_event.wait(.05):
                try:
                    self.tick(speech=False)
                except Exception as exc:
                    print('[motion-access]', exc, flush=True)
                    self.cancel('motion control failed')

        def run_speech():
            period = 1. / OUTPUT_HZ
            deadline = time.monotonic()
            while not self.stop_event.is_set():
                try:
                    self.tick_speech()
                except Exception as exc:
                    print('[speech-mouth]', exc, flush=True)
                    with self.lock:
                        self.speech_mouth.override('mouth update failed')
                deadline += period
                now = time.monotonic()
                if deadline <= now:
                    deadline = now + period
                if self.stop_event.wait(deadline - now):
                    break
        self.thread = threading.Thread(target=run, name='voice-motion-lease', daemon=True)
        self.speech_thread = threading.Thread(target=run_speech, name='speech-mouth50', daemon=True)
        self.thread.start()
        self.speech_thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
        if self.speech_thread:
            self.speech_thread.join(timeout=2)
        self.cancel('app stopped')
        with self.lock:
            self._release_cockpit()
            self.speech_mouth.reset()
