"""MCP exposes Duck capabilities only. No UART or Bridge access in this process."""
import time
from iot.thing import Thing, Parameter, ValueType
from local_ipc import request
from motion_access import command_link_ready
from motion_feedback import feedback_ready, feedback_problem

MCP_DEFAULT_LINEAR_SPEED = .1
MCP_MAX_LINEAR_SPEED = .15


class DuckThing(Thing):
    def __init__(self, port, token, settings=None, session=None, clock=time.monotonic, sound_player=None):
        super().__init__('Duck', 'XGO Duck 双足机器鸭：站立、坐下、行走、滚轮、转向、左右踢球、头部、嘴部、抓取和翻滚')
        self.port, self.token = port, token
        self.session = session
        self.clock = clock
        p = lambda name, desc, kind=ValueType.NUMBER, required=True: Parameter(name, desc, kind, required)
        if sound_player is not None:
            self.add_method('Quack', '播放本地鸭叫：greet打招呼、inquire疑问、alarm警觉、peck啄叫、chirp轻鸣、coo低鸣、wheee兴奋；不启动运动',
                            [p('kind', 'greet/inquire/alarm/peck/chirp/coo/wheee', ValueType.STRING)],
                            lambda a: sound_player(a['kind'].get_value()))
        if settings is not None:
            def enable(args):
                value = args['enabled'].get_value()
                if not isinstance(value, bool):
                    raise ValueError('enabled must be boolean')
                settings.update({'voice_motion_enabled': value})
                return {'status': 'success', 'voice_motion_enabled': value, 'message': 'This does not arm motors.'}
            self.add_method('EnableVoiceMotion', '仅按用户明确要求更改语音运动权限；不启动舵机',
                            [p('enabled', 'true 开启，false 关闭', ValueType.BOOLEAN)], enable)
            def enable_remote(args):
                value = args['enabled'].get_value()
                if not isinstance(value, bool):
                    raise ValueError('enabled must be boolean')
                settings.update({'remote_motion_enabled': value})
                return {'status': 'success', 'remote_motion_enabled': value, 'message': 'This does not arm motors.'}
            self.add_method('EnableRemoteMotion', '开启或关闭手机 MCP 遥控权限；开启本身不会启动舵机',
                            [p('enabled', 'true 开启，false 关闭', ValueType.BOOLEAN)], enable_remote)
        self.add_method('RemoteMove', '手机遥控续租；先查movement_ready。只移动已站稳的鸭子，不自动起身；stop停止该控制器',
            [p('direction', 'forward/backward/left/right/stop', ValueType.STRING), p('speed', '0–100 百分比'),
             p('seconds', '0.1–1 秒租约'), p('controller_id', '手机控制器会话 ID', ValueType.STRING),
             p('sequence', '会话内严格递增整数，停止也必须递增')],
            lambda a: self.remote_move(**{key: a[key].get_value()
                for key in ('direction', 'speed', 'seconds', 'controller_id', 'sequence')}))
        self.add_method('GetStatus', '查询当前/排队/最近动作、模型及避障；接受不等于完成。按action_feedback_ready/head_feedback_ready判断所需关节；feedback_warning不阻止所有动作', [], lambda _: self.state())
        self.add_method('Stand', '站起来/起立/站稳：进入零速平衡；坐下后通过坐站模型起立。先查GetStatus；权限关闭时请用户开启EnableVoiceMotion',
                        [], lambda _: self.action(kind='stand'))
        self.add_method('DefaultPose', '仅在明确要求默认姿势时使用：使能并保持预设关节角（hold），需要运动权限和有效反馈',
                        [], lambda _: self.action(kind='default_pose'))
        self.add_method('Forward', '普通前进/往前走用normal，默认0.1 m/s；用户明确快点/大点步/最快时用fast，最高0.15 m/s，不能用0.2/0.4。'
                        '明确慢走或指定数值用Move。默认3秒，最长10秒；accepted只代表接受，需GetStatus查结果，'
                        'ToF可减速或正常拦停，不自动重试；起立用Stand',
                        [p('seconds', '0.1–10秒，省略默认3秒，不含排队', required=False),
                         p('pace', 'normal默认0.1；fast仅明确快走时用0.15', ValueType.STRING, required=False)],
                        lambda a: self.forward(a['seconds'].get_value(), a['pace'].get_value()))
        self.add_method('Move', '用于明确慢走、指定速度、后退或侧移；普通前进/快点/大点步/最快用Forward。'
                        '各方向默认0.1 m/s，语音线速度最高0.15 m/s，滚轮模式也不能超过，不使用0.2/0.4。'
                        '动作排队，到时归零，ToF可减速或返回正常blocked，不说工具错误、不自动重试；GetStatus确认结果',
                        [p('direction', 'forward/backward/left/right', ValueType.STRING),
                         p('speed', 'm/s：省略默认0.1，所有方向0–0.15，快走也不可超过', required=False),
                         p('seconds', '0.1–10秒，不含排队')],
                        lambda a: self.move(a['direction'].get_value(), a['speed'].get_value(), a['seconds'].get_value()))
        self.add_method('Turn', '转向后归零，可排队；普通左转+1、右转-1 rad/s，默认2–3秒；Roller限±0.25。查GetStatus，不代表测得角度',
                        [p('yaw_rate', '-1–1 rad/s；默认左转 +1、右转 -1，正值左转'), p('seconds', '0.1–10 秒，排队等待不计入')],
                        lambda a: self.action(kind='turn', twist=[0, 0, a['yaw_rate'].get_value()], duration=a['seconds'].get_value()))
        self.add_method('SetHead', '保持头颈目标，弧度；左右扭头用head3；省略轴保持原值，不会自动回中，勿同时设四轴极值',
                        [p('head1', '颈部俯仰，±0.5 rad', required=False),
                         p('head2', '头部俯仰，±0.5 rad', required=False),
                         p('head3', '左右转头 yaw，±0.5 rad', required=False),
                         p('head4', '头部侧倾 roll，±0.5 rad', required=False)],
                        lambda a: self.action(kind='head', head=[a['head'+str(i)].get_value() for i in range(1, 5)], duration=1))
        self.add_method('SetMouth', '设置嘴部开合角度', [p('degrees', '0–30 度')],
                        lambda a: self.action(kind='mouth', mouth=a['degrees'].get_value(), duration=1))
        self.add_method('Pick', '抓取约4秒；若处于坐姿，板端会先起立再抓取', [], lambda _: self.action(kind='pick'))
        self.add_method('Roulade', '翻滚一次；若处于坐姿会先起立，后续动作等重新站稳，查GetStatus', [], lambda _: self.action(kind='roulade'))
        self.add_method('Sit', '站稳后坐下并保持扭矩，支持默认姿态保持；起立用Stand',
                        [], lambda _: self.action(kind='sit'))
        self.add_method('LeftKick', '左脚踢球约0.4秒；若处于坐姿会先起立，站稳后踢，查GetStatus', [], lambda _: self.action(kind='left_kick'))
        self.add_method('RightKick', '右脚踢球约0.4秒；若处于坐姿会先起立，站稳后踢，查GetStatus', [], lambda _: self.action(kind='right_kick'))
        self.add_method('SetWalkingMode', '选择walk普通脚或roller滚轮脚；roller需用户确认有滚轮。停移动并清队列，不启动舵机；再用Stand/Forward/Move/Turn。语音线速度≤0.15；Roller转向±0.25，不侧移；坐站/踢腿时不能切换',
                        [p('mode', 'walk 普通脚；roller 滚轮脚', ValueType.STRING)],
                        lambda a: self.action(kind='walking_mode', walking_mode=a['mode'].get_value()))
        self.add_method('GetToF', '读取前方ToF距离和8×8测距阵列，单位mm；报告有效性、帧号和帧龄。'
                        '测距不可用时说明原因，不把空值当0；只能测距离，不能据此识别物体，不启动运动',
                        [], lambda _: self.read_tof())
        self.add_method('Stop', '立即停止当前动作并清空排队，速度归零，保留平衡控制', [], lambda _: self.action(kind='stop'))
        self.add_method('TorqueOff', '释放所有舵机，双足机器人可能倒下；仅在明确要求卸力时使用', [],
                        lambda _: self.action(kind='torque_off'))

    def state(self):
        data = request(self.port, self.token, '/state')
        motion = data['motion']
        control = dict(data['control'])
        # Xiaozhi treats a top-level `action` as its reply-control enum.
        # Keep our lease state nested under a distinct domain-specific name.
        motion_action = control.pop('action', None)
        link_ready = command_link_ready(motion)
        bypass = motion.get('timeout_checks_disabled') is True
        problems = [('' if bypass else feedback_problem(motion, ())), motion.get('last_error', '') or
                    ('' if link_ready else '运动命令未及时到达 STM32，请检查控制通信。')]
        return dict(control, motion_action=motion_action, status='success', mode=motion['mode'],
                    ready=bool(feedback_ready(motion, ()) and link_ready),
                    full_robot_ready=bool(motion.get('full_robot_ready')),
                    feedback_warning=feedback_problem(motion),
                    enabled=bool(motion.get('enabled', False)), command_link_ready=link_ready,
                    fault_hold=bool(motion.get('fault_hold')), fault_hold_supported=bool(motion.get('fault_hold_supported')),
                    timeout_checks_disabled=bypass, timeout_warning=motion.get('timeout_warning', ''),
                    mcu_command_age_us=motion.get('mcu_command_age_us'),
                    imu_ok=motion.get('imu_ok'), missing_joint_ids=list(motion.get('missing_joint_ids') or []),
                    feedback_age_ms=motion.get('feedback_age_ms'),
                    recovery=motion.get('recovery', {}), feedback_hz=motion.get('feedback_hz', 0),
                    active_model=motion.get('active_model'), roller=motion.get('roller', {}),
                    sitstand=motion.get('sitstand', {}), left_kick=motion.get('left_kick', {}),
                    right_kick=motion.get('right_kick', {}), pick=motion.get('pick', {}),
                    roulade=motion.get('roulade', {}), obstacle_avoidance=motion.get('obstacle_avoidance', {}),
                    error=' '.join(problem for problem in problems if problem))

    def read_tof(self):
        data = request(self.port, self.token, '/tof')
        sensor, front = data['sensor'], data['front']
        available = bool(front.get('available'))
        distance = front.get('front_distance_mm') if available else None
        message = '前方测距可用。' if available else '当前没有有效的前方测距。'
        return dict(status='success', unit='mm', available=available,
                    front_distance_mm=distance,
                    front_distance_cm=round(distance/10, 1) if distance is not None else None,
                    front=front,
                    sensor={k: sensor.get(k) for k in ('state', 'fresh', 'seq', 'age_ms', 'valid_zones',
                            'center_mm', 'min_mm', 'max_mm', 'resolution', 'frequency_hz', 'grid_mm')},
                    message=front.get('blocking_reason') or sensor.get('error_message') or message)

    def action(self, **body):
        result = request(self.port, self.token, '/action', dict(body, session=self.session, issued_at=self.clock()))
        if result.get('reason'):
            return dict(status='success', execution=result, message=result['reason'])
        return {'status': 'success', 'execution': result,
                'message': 'Queued; not started yet. Use GetStatus for completion.' if result.get('status') == 'queued' else
                           'Request accepted. Use GetStatus to check completion; physical success is not evaluated.'}

    def forward(self, seconds=None, pace=None):
        pace = 'normal' if pace is None else pace
        if not isinstance(pace, str) or pace not in ('normal', 'fast'):
            raise ValueError('pace must be normal (0.1 m/s) or fast (0.15 m/s)')
        speed = MCP_DEFAULT_LINEAR_SPEED if pace == 'normal' else MCP_MAX_LINEAR_SPEED
        return self.move('forward', speed, 3. if seconds is None else seconds)

    def move(self, direction, speed, seconds):
        directions = dict(forward=(1, 0), backward=(-1, 0), left=(0, 1), right=(0, -1))
        if direction not in directions:
            raise ValueError('invalid direction')
        x, y = directions[direction]
        if speed is None:
            speed = MCP_DEFAULT_LINEAR_SPEED
        if isinstance(speed, bool):
            raise ValueError('speed must be numeric')
        speed = float(speed)
        limit = MCP_MAX_LINEAR_SPEED
        if not 0 <= speed <= limit:
            raise ValueError(f'speed must be 0–{limit:g} m/s for {direction}')
        return self.action(kind='move', twist=[x*speed, y*speed, 0], duration=seconds)

    def remote_move(self, direction, speed, seconds, controller_id, sequence):
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0 <= speed <= 100:
            raise ValueError('remote speed must be 0–100 percent')
        if direction not in ('forward', 'backward', 'left', 'right', 'stop'):
            raise ValueError('invalid remote direction')
        fraction = speed / 100
        twist = dict(forward=[fraction * .15, 0, 0], backward=[-fraction * .15, 0, 0],
                     left=[0, 0, fraction], right=[0, 0, -fraction], stop=[0, 0, 0])[direction]
        return self.action(kind='stop' if direction == 'stop' else 'move', twist=twist, duration=seconds,
                           remote=True, controller_id=controller_id, sequence=sequence)
