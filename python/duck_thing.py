"""MCP exposes Duck capabilities only. No UART or Bridge access in this process."""
import time
from iot.thing import Thing, Parameter, ValueType
from local_ipc import request
from motion_access import command_link_ready, VOICE_TWIST_LIMITS
from motion_feedback import feedback_ready, feedback_problem


class DuckThing(Thing):
    def __init__(self, port, token, settings=None, session=None, clock=time.monotonic, sound_player=None):
        super().__init__('Duck', 'XGO Duck 双足机器鸭：站立、有限时移动、转向、头部、嘴部、抓取和翻滚')
        self.port, self.token = port, token
        self.session = session
        self.clock = clock
        p = lambda name, desc, kind=ValueType.NUMBER, required=True: Parameter(name, desc, kind, required)
        if sound_player is not None:
            self.add_method('Quack', '本地合成鸭叫，不是朗读“嘎嘎”；普通叫/打招呼用greet，疑问inquire，'
                            '警觉alarm，啄叫peck，轻鸣chirp，低鸣coo，兴奋wheee。播放一次后返回，不启动运动',
                            [p('kind', 'greet/inquire/alarm/peck/chirp/coo/wheee', ValueType.STRING)],
                            lambda a: sound_player(a['kind'].get_value()))
        if settings is not None:
            def enable(args):
                value = args['enabled'].get_value()
                if not isinstance(value, bool):
                    raise ValueError('enabled must be boolean')
                settings.update({'voice_motion_enabled': value})
                return {'status': 'success', 'voice_motion_enabled': value, 'message': 'This does not arm motors.'}
            self.add_method('EnableVoiceMotion', '仅在用户明确要求开启/关闭语音运动权限时调用；开启本身不会启动舵机',
                            [p('enabled', 'true 开启，false 关闭', ValueType.BOOLEAN)], enable)
            def enable_remote(args):
                value = args['enabled'].get_value()
                if not isinstance(value, bool):
                    raise ValueError('enabled must be boolean')
                settings.update({'remote_motion_enabled': value})
                return {'status': 'success', 'remote_motion_enabled': value, 'message': 'This does not arm motors.'}
            self.add_method('EnableRemoteMotion', '开启或关闭手机 MCP 遥控权限；开启本身不会启动舵机',
                            [p('enabled', 'true 开启，false 关闭', ValueType.BOOLEAN)], enable_remote)
        self.add_method('RemoteMove', '手机 MCP 遥控续租；先 GetStatus 确认 movement_ready。'
                        '方向键只移动已经使能并站稳的鸭子，不启动舵机或自动起身；stop 随时停止该控制器',
            [p('direction', 'forward/backward/left/right/stop', ValueType.STRING), p('speed', '0–100 百分比'),
             p('seconds', '0.1–1 秒租约'), p('controller_id', '手机控制器会话 ID', ValueType.STRING),
             p('sequence', '会话内严格递增整数，停止也必须递增')],
            lambda a: self.remote_move(**{key: a[key].get_value()
                for key in ('direction', 'speed', 'seconds', 'controller_id', 'sequence')}))
        self.add_method('GetStatus', '查询运动反馈、当前动作 motion_action、排队 queued_actions 和最近结果 recent_actions；'
                        '按 action_feedback_ready/head_feedback_ready 判断所需关节，feedback_warning 不代表所有动作不可用；'
                        '接受/排队不代表完成', [], lambda _: self.state())
        self.add_method('Stand', '站起来/起立/站稳/stand up：进入零速度平衡站立，沿用原起身策略；'
                        '先查 GetStatus，语音运动未开启时先请用户开启 EnableVoiceMotion；此工具不会向前走',
                        [], lambda _: self.action(kind='stand'))
        self.add_method('Move', '前后左右行走后速度归零；通常前后 0.2、侧移 0.15 m/s，持续 2–3 秒，与网页方向键一致。'
                        '走10秒请一次传 seconds=10；连续动作在板端排队，查 GetStatus 确认完成，不能声称测得位移；起立用 Stand',
                        [p('direction', 'forward/backward/left/right', ValueType.STRING),
                         p('speed', '前后 0–0.4、侧移 0–0.3 m/s'), p('seconds', '0.1–10 秒，排队等待不计入')],
                        lambda a: self.move(a['direction'].get_value(), a['speed'].get_value(), a['seconds'].get_value()))
        self.add_method('Turn', '按序转向后归零；普通左转使用 +1 rad/s，普通右转使用 -1 rad/s，默认持续 2–3 秒。'
                        '只有用户明确要求较慢速度时才用更小值；当前动作未结束时排队，查 GetStatus 确认结果，不能据此宣称已转到某个角度',
                        [p('yaw_rate', '-1–1 rad/s；默认左转 +1、右转 -1，正值左转'), p('seconds', '0.1–10 秒，排队等待不计入')],
                        lambda a: self.action(kind='turn', twist=[0, 0, a['yaw_rate'].get_value()], duration=a['seconds'].get_value()))
        self.add_method('SetHead', '设置并保持模型的四个头颈目标，单位弧度；左右扭头调整 head3，'
                        '只传要改变的轴，省略的轴保持原目标；不要把四轴同时设为极值；不会自动回中',
                        [p('head1', '颈部俯仰，±0.5 rad', required=False),
                         p('head2', '头部俯仰，±0.5 rad', required=False),
                         p('head3', '左右转头 yaw，±0.5 rad', required=False),
                         p('head4', '头部侧倾 roll，±0.5 rad', required=False)],
                        lambda a: self.action(kind='head', head=[a['head'+str(i)].get_value() for i in range(1, 5)], duration=1))
        self.add_method('SetMouth', '设置嘴部开合角度', [p('degrees', '0–30 度')],
                        lambda a: self.action(kind='mouth', mouth=a['degrees'].get_value(), duration=1))
        self.add_method('Pick', '运行一次四秒抓取策略，需机器人已就绪', [], lambda _: self.action(kind='pick'))
        self.add_method('Roulade', '翻滚一次/滚一圈/roll over：先开启语音运动并站稳，运行原翻滚策略约1.9秒；'
                        '后续动作等待重新站稳，查GetStatus确认执行结果', [], lambda _: self.action(kind='roulade'))
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
                    error=' '.join(problem for problem in problems if problem))

    def action(self, **body):
        result = request(self.port, self.token, '/action', dict(body, session=self.session, issued_at=self.clock()))
        return {'status': 'success', 'execution': result,
                'message': 'Queued; not started yet. Use GetStatus for completion.' if result.get('status') == 'queued' else
                           'Request accepted. Use GetStatus to check completion; physical success is not evaluated.'}

    def move(self, direction, speed, seconds):
        directions = dict(forward=(1, 0), backward=(-1, 0), left=(0, 1), right=(0, -1))
        if direction not in directions:
            raise ValueError('invalid direction')
        x, y = directions[direction]
        if isinstance(speed, bool):
            raise ValueError('speed must be numeric')
        speed = float(speed)
        limit = VOICE_TWIST_LIMITS[0 if x else 1]
        if not 0 <= speed <= limit:
            raise ValueError(f'speed must be 0–{limit:g} m/s for {direction}')
        return self.action(kind='move', twist=[x*speed, y*speed, 0], duration=seconds)

    def remote_move(self, direction, speed, seconds, controller_id, sequence):
        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0 <= speed <= 100:
            raise ValueError('remote speed must be 0–100 percent')
        if direction not in ('forward', 'backward', 'left', 'right', 'stop'):
            raise ValueError('invalid remote direction')
        twist = dict(forward=[speed * .001, 0, 0], backward=[-speed * .001, 0, 0],
                     left=[0, 0, speed * .005], right=[0, 0, -speed * .005], stop=[0, 0, 0])[direction]
        return self.action(kind='stop' if direction == 'stop' else 'move', twist=twist, duration=seconds,
                           remote=True, controller_id=controller_id, sequence=sequence)
