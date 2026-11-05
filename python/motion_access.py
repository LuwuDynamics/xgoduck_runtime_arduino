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
        self.lock = controller.lock
        self.voice_seen = float('-inf')
        self.voice_session = None
        self.voice_epoch = 0
        self.action = None
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
        return (self.controller.input_source == 'voice' and self.action is not None
                and self.action['state'] == 'running' and self.action['kind'] in ('move', 'turn'))

    def movement_state(self, status, *, ongoing=False, required_ids=LEG_IDS, local_action=False):
        """Confirm standing before entry; allow normal gait tilt during a lease."""
        now = self.clock()
        recovery = status.get('recovery') or {}
        angle = recovery.get('angle_deg')
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
        elif self.controller.mode != 'policy' or status.get('enabled') is not True:
            reason = '鸭子尚未启动平衡站立，请先明确要求“站起来”，或在原网页开启 Policy。'
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
            self.speech_mouth.override('manual mode takeover', restore=previous_mode == mode)
            if self.action and self.action.get('remote_controller'):
                self.remember_remote(self.action['remote_controller'], 2**53)
            self.cancel('manual takeover')
            return result

    def web_action(self, name, *args):
        with self.lock:
            if self.controller.input_source == 'voice':
                raise ValueError('Select a motion mode to take over voice control first.')
            self.speech_mouth.override('special action owns mouth')
            return getattr(self.controller, 'start_' + name)(*args)

    def web_sitstand(self, action):
        with self.lock:
            if self.controller.input_source == 'voice':
                raise ValueError('Select a motion mode to take over voice control first.')
            self.speech_mouth.override('special action owns mouth')
            return self.controller.set_sitstand(action)

    def web_roller(self, enabled):
        with self.lock:
            result = self.controller.set_roller_mode(enabled)
            self.speech_mouth.override('manual walking-model takeover')
            if self.action and self.action.get('remote_controller'):
                self.remember_remote(self.action['remote_controller'], 2**53)
            self.cancel('manual walking-model takeover')
            return result

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
            if kind == 'stop':
                if remote:
                    self.remote_forward_stopped.discard(controller_id)
                    self.remember_remote(controller_id, sequence)
                    if (self.pending or (self.action and self.action['state'] == 'running'
                                         and self.action.get('remote_controller') != controller_id)):
                        return {'status': 'completed', 'message': 'Retired this remote controller; current owner unchanged.'}
                elif self.action and self.action.get('remote_controller'):
                    self.remember_remote(self.action['remote_controller'], 2**53)
                self.cancel('stopped by voice')
                return {'status': 'completed', 'message': 'Voice motion stopped.'}
            if kind == 'torque_off':
                if self.action and self.action.get('remote_controller'):
                    self.remember_remote(self.action['remote_controller'], 2**53)
                self.cancel('torque off')
                self.controller.set_mode('off')
                return {'status': 'completed', 'message': 'Torque off.'}
            if kind not in ('stand', 'move', 'turn', 'head', 'mouth', 'pick', 'roulade',
                            'sit', 'left_kick', 'right_kick', 'walking_mode', 'default_pose'):
                raise ValueError('unsupported duck action')
            if getattr(self.controller, 'battery_phase', 'normal') != 'normal':
                raise ValueError('低电压保护中，请充电后重新使能。 Low battery protection is active.')
            if not (self.remote_allowed() if remote else self.allowed()):
                raise ValueError('Remote motion disabled; enable it in the control page.' if remote else
                                 'Voice motion disabled. 语音运动未开启，请先说“开启语音运动”；此开关本身不启动舵机。')
            if self.controller.input_source == 'gamepad':
                raise ValueError('Gamepad owns motion; disable gamepad control first.')
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
            renewing = (remote and self.action and self.action['state'] == 'running'
                        and self.action.get('remote_controller') == controller_id)
            busy = bool(self.pending or (self.action and self.action['state'] == 'running' and not renewing))
            if busy and remote:
                if self.action and self.action['state'] == 'running' and self.action['kind'] == 'stand':
                    raise ValueError('Standing in progress. 正在确认起身和站稳，请稍后再移动。')
                raise ValueError('A voice action is running; stop it first.')
            if busy and (self.controller.input_source != 'voice'
                         or (self.action and self.action.get('remote_controller'))):
                raise ValueError('A remote action is running; stop it before voice motion.')
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
                duration = 5.
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
            if busy:
                remaining = max(0., self.action['deadline'] - self.clock()) if self.action['state'] == 'running' else 0.
                if len(self.pending) >= MAX_QUEUED_ACTIONS:
                    raise ValueError('Voice action queue is full. 最多排队 4 个动作，请等待或先停止。')
                if remaining + sum(item['duration_s'] for item, _ in self.pending) + duration > MAX_QUEUED_SECONDS:
                    raise ValueError('Voice action queue exceeds 30 seconds. 待执行动作累计不能超过 30 秒。')
                self.pending.append((job, payload))
                self._log_action(job)
                return dict(status='queued', action_id=job['id'], duration_s=duration, queue_position=len(self.pending))
            try:
                self._begin_action(job, payload, ongoing=bool(renewing and self._locomotion_running()))
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
            return dict(status='accepted', action_id=job['id'], duration_s=duration)

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
        if self.controller.input_source == 'gamepad' or self.controller.mode in ('calibrate', 'servo_debug'):
            raise ValueError('Manual control owns motion.')
        status = self.controller.status()
        kind = job['kind']
        required_ids = self._required_ids(kind, payload)
        self._require_feedback(status, required_ids=required_ids, resume_hold=kind == 'stand')
        avoidance = status.get('obstacle_avoidance') or {}
        if kind != 'stand':
            movement = self.movement_state(status, ongoing=ongoing, required_ids=required_ids,
                                           local_action=kind in ('head', 'mouth'))
            if not movement['movement_ready']:
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
        previous = self.controller.input_source
        try:
            if kind in ('mouth', 'pick', 'roulade', 'stand', 'sit', 'left_kick', 'right_kick'):
                self.speech_mouth.override('explicit action owns mouth')
            self.controller.input_source = 'voice'
            if kind == 'stand':
                self.controller._stop_pick_locked()
                self.controller._stop_roulade_locked()
                self.controller._stop_kick_locked()
                # Finish sitting through the same model/history. The upstream
                # controller rejects switching to walk during this transition.
                if self.controller.sitstand_phase == 'sit':
                    self.controller.set_sitstand('stand')
                elif self.controller.sitstand_phase != 'stand':
                    self.controller.set_mode('policy')
            else:
                self.controller.set_mode('policy')
            actual = dict(payload)
            if kind == 'head':
                actual['head'] = [old if value is None else value
                                  for old, value in zip(self.controller.head, payload['head'])]
                job['command_head'] = list(actual['head'])
            reply = self.controller.command(**actual, source='voice')
            if reply.get('forward_blocked'):
                raise ForwardBlocked(reply['reason'], self.controller.status().get('obstacle_avoidance'))
            if kind == 'pick':
                self.controller.start_pick()
            elif kind == 'roulade':
                self.controller.start_roulade()
            elif kind == 'sit':
                self.controller.set_sitstand('sit')
            elif kind in ('left_kick', 'right_kick'):
                self.controller.start_kick('left' if kind == 'left_kick' else 'right')
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
            self.cancel('previous action did not complete')
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
                   'command_twist', 'command_head', 'command_mouth', 'required_joint_ids', 'reason') if key in job}
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
            self.remote_forward_stopped.discard(next(iter(self.remote_sequences)))
            self.remote_sequences.pop(next(iter(self.remote_sequences)))

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

    def cancel(self, reason):
        with self.lock:
            self.cancelled_at = self.clock()
            if self.action and self.action['state'] == 'running':
                self._finish_action('cancelled', reason=reason)
            self._cancel_pending(reason)
            if self.controller.input_source == 'voice':
                self.controller._stop_pick_locked()
                self._stop_roulade()
                self.controller._stop_kick_locked()
                try:
                    if self.controller.mode not in ('calibrate', 'servo_debug'):
                        self.controller.command(twist=[0, 0, 0], mouth=0, source='voice')
                finally:
                    self.controller.input_source = 'web'
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
            running = self.action and self.action['state'] == 'running'
            required_ids = self.action.get('required_joint_ids', LEG_IDS) if running else ()
            if speech:
                self._tick_speech_locked(status)
            movement = self.movement_state(status, ongoing=self._locomotion_running())
            if self.controller.input_source != 'voice':
                if self.pending or (self.action and self.action['state'] == 'running'):
                    self.cancelled_at = self.clock()
                if self.action and self.action['state'] == 'running':
                    self._finish_action('cancelled', reason='control source changed')
                    if self.action.get('remote_controller'):
                        self.remember_remote(self.action['remote_controller'], 2**53)
                self._cancel_pending('control source changed')
                return
            allowed = self.remote_allowed() if self.action and self.action.get('remote_controller') else self.allowed()
            if not allowed or self.voice_session is None or self.clock() - self.voice_seen > .8:
                self.cancel('voice unavailable or motion disabled')
            elif self.controller.mode != 'policy':
                self.cancel('controller left policy mode')
            elif (status.get('fault_hold') or not feedback_ready(status, required_ids) or
                  (not command_link_ready(status) and not
                   (status.get('arming') and self.action and self.action['state'] == 'running'
                    and self.action['kind'] == 'stand'))):
                if self.action and self.action.get('remote_controller'):
                    self.remember_remote(self.action['remote_controller'], 2**53)
                self.cancel('Motion paused with last targets held.' if status.get('fault_hold') else
                            'Motion feedback / MCU command link lost. ' + feedback_problem(status, required_ids))
            elif (self.action and self.action['state'] == 'running' and self.action['kind'] != 'stand'
                  and status.get('enabled') is not True):
                self.cancel('Motor enable feedback lost.')
            elif (self.action and self.action['state'] == 'running' and self.action['kind'] in ('move', 'turn')
                  and not movement['movement_ready']):
                if self.action.get('remote_controller'):
                    self.remember_remote(self.action['remote_controller'], 2**53)
                self.cancel(movement['movement_block_reason'])
            elif (self.action and self.action['state'] == 'running' and self.action['kind'] == 'stand'
                  and movement['movement_ready']):
                self._finish_action('completed', result='Balance feedback ready: motors enabled and upright for one second.')
            elif self.action and self.action['state'] == 'running' and self.clock() >= self.action['deadline']:
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
            self.speech_mouth.reset()
