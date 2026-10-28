# -*- coding: utf-8 -*-
"""机器人控制 Thing (修复并精简自 xiaozhi_test/dog_test.py)

修复的原版问题:
  - "turn" 属性重复注册
  - motor_41/42/43 误调用 motor_31/32/33 的实现
  - _gait 未实际调用 dog.gait()
  - action_1~5 五份重复代码合并为一个 action 方法
  - time.sleep(move_time) 阻塞消息循环 → 改为后台线程执行, 立即返回
"""

import threading
import time

from .thing import Thing, Parameter, ValueType


class RobotThing(Thing):
    """XGO 系列机器人 (xgolib)"""

    def __init__(self, port="/dev/ttyAMA0"):
        super().__init__("Robot", "XGO机器人, 支持移动/转向/动作/姿态/机械臂控制")
        self.dog = None
        self.dog_type = "M"
        self._init_robot(port)

        # 状态
        self.is_moving = False
        self.direction = "stop"
        self.speed = 0
        self.is_turn = False
        self.turn = "stop"
        self.turn_speed = 0
        self.gait_type = "trot"
        self.pace = "normal"
        self.claw = 0
        self.last_action = ""
        self.busy = False  # 动作执行中
        self.translation = [0, 0, 75]
        self.attitude = [0, 0, 0]
        self.arm = [0, 0]
        self._cam = None  # 视觉抓球用的 Picamera2 (惰性, 用时才开)

        self._long_task = {"task": "", "state": "idle", "result": ""}  # 长任务状态(抓球/寻人/放球), 供 QueryTask 轮询

        # 预设动作时长表 (索引=动作id)
        self._action_times = {
            1: 3.5, 2: 3.5, 3: 5.5, 4: 5.5, 5: 4.5, 6: 4.5, 7: 4.5, 8: 4.5,
            9: 4.5, 10: 7.5, 11: 7.5, 12: 5.5, 13: 7.5, 14: 10.5, 15: 6.5,
            16: 6.5, 17: 6.5, 18: 6.5, 19: 10.5, 20: 9.5, 21: 8.5, 22: 8.5,
            23: 6.5, 24: 7.5, 128: 10.5, 129: 10.5, 130: 10.5,
        }
        self._action_names = [
            "趴下", "站起", "匍匐前进", "转圈", "原地踏步", "蹲起",
            "沿x转动", "沿y转动", "沿z转动", "三轴转动", "撒尿", "坐下",
            "招手", "伸懒腰", "波浪运动", "摇摆运动", "求食", "找食物",
            "握手", "展示机械臂", "俯卧撑", "张望", "跳舞", "调皮",
        ]

        self._register_properties()
        self._register_methods()

    def _init_robot(self, port):
        try:
            from libs.robot_type import detect_robot_type, get_xgolib_version
            robot_type = detect_robot_type(port)
            version = get_xgolib_version(port)
            # XGO 工厂, 不传 baud 走 SCAN_BAUDS 自适应探测 (xgolib>=1.1.9 支持指定
            # port 时自适应), 兼容固件默认 115200 与 RL 切换的高速波特率(如 2000000)。
            from xgolib import XGO
            self.dog = XGO(port=port, version=version)
            self.dog_type = robot_type
            print(f"[iot] 机器人初始化: 机型={robot_type} xgolib={version}")
        except Exception as e:
            print(f"[iot] 机器人初始化失败(降级): {e}")
            self.dog = None

    # ---------- 属性注册 ----------
    def _register_properties(self):
        self.add_property("is_moving", "机器人是否正在移动", lambda: self.is_moving)
        self.add_property("direction", "移动方向(stop/forward/backward/left/right)", lambda: self.direction)
        self.add_property("speed", "移动速度", lambda: self.speed)
        self.add_property("is_turn", "机器人是否正在转向", lambda: self.is_turn)
        self.add_property("turn", "转向方向(stop/left/right)", lambda: self.turn)
        self.add_property("turn_speed", "转向速度(度/秒)", lambda: self.turn_speed)
        self.add_property("gait", "步态(trot/walk/high_walk/slow_trot)", lambda: self.gait_type)
        self.add_property("pace_mode", "迈步频率(slow/normal/high)", lambda: self.pace)
        self.add_property("claw", "夹爪开合(0张开-255闭合)", lambda: self.claw)
        self.add_property("busy", "是否正在执行动作", lambda: self.busy)
        self.add_property("last_action", "最近执行的动作", lambda: self.last_action)
        self.add_property("task_state", "长任务状态(running/done/error/idle)", lambda: self._long_task.get("state", "idle"))
        self.add_property("task_name", "当前/最近长任务名", lambda: self._long_task.get("task", ""))
        self.add_property("task_result", "长任务结果文本", lambda: self._long_task.get("result", ""))

    # ---------- 方法注册 ----------
    def _register_methods(self):
        # 基础方法 + 按机型/驱动能力动态注册的扩展运动方法
        self._register_base_methods()
        self._register_extra_methods()

    def _api_ok(self, *apis):
        """底层驱动是否提供任一指定 API (能力门控, 对齐 apps/ai/robot_tools 口径)"""
        return self.dog is not None and any(hasattr(self.dog, a) for a in apis)

    # 视觉类长任务(找球/抓球/寻人)硬性超时上限(秒), 防止 LLM 传大 max_search_time 拖死机器狗
    _MAX_TASK_SECONDS = 29

    # Rider/Ranger(轮式) 动作表, 与四足 1-24/128-130 不同 (对齐 apps/ai/robot_tools)
    _RIDER_ACTION_NAMES = {1: "左右摇摆", 2: "高低起伏", 3: "前进后退",
                           4: "四方蛇形", 5: "升降旋转", 6: "圆周晃动", 255: "重置"}
    _RIDER_ACTION_TIMES = {1: 3, 2: 4, 3: 3, 4: 4, 5: 6, 6: 5, 255: 1}

    def _register_base_methods(self):
        ok = self._api_ok
        # RA(Ranger, 轮式) 不注册四足运动类工具, 由 RaMove/RaTurn/RaAction/RaPerform 替代
        is_ra = self.dog_type == "ranger"

        if not is_ra:
            self.add_method("Move", "机器人移动", [
                Parameter("direction", "forward/backward/left/right", ValueType.STRING),
                Parameter("speed", "0-25(左右平移0-18)", ValueType.NUMBER),
                Parameter("move_time", "秒", ValueType.NUMBER),
            ], lambda p: self._move(p["direction"].get_value(),
                                    p["speed"].get_value(), p["move_time"].get_value()))

            self.add_method("TurnLeft", "机器人左转", [
                Parameter("turn_speed", "0-30°/s", ValueType.NUMBER),
                Parameter("turn_time", "秒", ValueType.NUMBER),
            ], lambda p: self._timed_turn(p["turn_speed"].get_value(), p["turn_time"].get_value(), "left"))

            self.add_method("TurnRight", "机器人右转", [
                Parameter("turn_speed", "0-30°/s", ValueType.NUMBER),
                Parameter("turn_time", "秒", ValueType.NUMBER),
            ], lambda p: self._timed_turn(p["turn_speed"].get_value(), p["turn_time"].get_value(), "right"))

            if self.dog_type == "rider":
                action_desc = "1摇摆2起伏3前后4蛇形5升降旋转6晃动255重置"
            else:
                action_desc = ("1趴下2站起3匍匐4转圈5踏步6蹲起7x转8y转9z转10三轴"
                               "11撒尿12坐13招手14伸懒腰15波浪16摇摆17求食18找食19握手20展臂"
                               "21俯卧撑22张望23跳舞24调皮128上抓129中抓130下抓")
            self.add_method("Action", "执行预设动作", [
                Parameter("action", action_desc, ValueType.NUMBER),
            ], lambda p: self._action(p["action"].get_value()))

            self.add_method("Perform", "表演模式", [
                Parameter("perform", "0关1开", ValueType.NUMBER),
            ], lambda p: self._perform(p["perform"].get_value()))

        self.add_method("Stop", "停止所有动作", [], lambda p: self._stop())

        if ok("gait", "gait_type"):
            self.add_method("SetGait", "设置步态", [
                Parameter("gait", "1trot 2walk 3high_walk 4slow_trot", ValueType.NUMBER),
            ], lambda p: self._gait(p["gait"].get_value()))

        if ok("pace"):
            self.add_method("SetPace", "迈步频率", [
                Parameter("pace_mode", "1慢2正常3快", ValueType.NUMBER),
            ], lambda p: self._pace(p["pace_mode"].get_value()))

        if ok("claw"):
            self.add_method("SetClaw", "夹爪开合", [
                Parameter("claw", "0张开-255闭合", ValueType.NUMBER),
            ], lambda p: self._claw(p["claw"].get_value()))

        self.add_method("Translation", "机身平移", [
            Parameter("translation_x", "前后mm(-35~35)", ValueType.NUMBER),
            Parameter("translation_y", "左右mm(-18~18)", ValueType.NUMBER),
            Parameter("translation_z", "上下mm(75~115)", ValueType.NUMBER),
        ], lambda p: self._translation(p["translation_x"].get_value(),
                                       p["translation_y"].get_value(),
                                       p["translation_z"].get_value()))

        self.add_method("Attitude", "机身姿态", [
            Parameter("attitude_r", "滚转度(-20~20)", ValueType.NUMBER),
            Parameter("attitude_p", "俯仰度(-15~15)", ValueType.NUMBER),
            Parameter("attitude_y", "偏航度(-11~11)", ValueType.NUMBER),
        ], lambda p: self._attitude(p["attitude_r"].get_value(),
                                    p["attitude_p"].get_value(),
                                    p["attitude_y"].get_value()))

        if ok("arm"):
            self.add_method("Arm", "机械臂位置", [
                Parameter("arm_x", "x mm(-80~155)", ValueType.NUMBER),
                Parameter("arm_z", "z mm(-95~155)", ValueType.NUMBER),
            ], lambda p: self._arm(p["arm_x"].get_value(), p["arm_z"].get_value()))

    # ---------- 内部实现 (动作统一放后台线程, 不阻塞消息循环) ----------
    _MOVE_TABLE = {
        "forward": ("x", 1), "backward": ("x", -1),
        "left": ("y", 1), "right": ("y", -1),
    }

    def _move(self, direction, speed, move_time):
        """按方向分派到 _timed_move (forward/backward 走 x 轴, left/right 走 y 轴)"""
        entry = self._MOVE_TABLE.get(str(direction).lower())
        if not entry:
            return {"status": "error", "message": f"无效方向: {direction}, 应为 forward/backward/left/right"}
        axis, sign = entry
        # Rider 轮式仅支持前后, 不支持左右平移 (对齐 apps/ai/robot_tools)
        if self.dog_type == "rider" and axis == "y":
            return {"status": "error", "message": "Rider轮式不支持左右平移, 可用转弯代替"}
        return self._timed_move(axis, sign * speed, move_time, direction.lower())

    def _ok(self, message):
        return {"status": "success", "message": message}

    def _stop(self):
        self.is_moving = False
        self.direction = "stop"
        self.speed = 0
        self.is_turn = False
        self.turn = "stop"
        self.turn_speed = 0
        if self.dog:
            self.dog.stop()
        return self._ok("机器人已停止")

    def _timed_move(self, axis, speed, move_time, direction):
        if self.busy:
            return {"status": "error", "message": "正在执行其他动作"}
        self.is_moving = True
        self.direction = direction
        self.speed = speed

        def run():
            self.busy = True
            try:
                if self.dog_type == "rider" and hasattr(self.dog, "rider_move_x"):
                    # Rider 轮式: ±0.5 档速, step 为步数 (对齐 apps/ai/robot_tools)
                    s = 0.5 if speed >= 0 else -0.5
                    self.dog.rider_move_x(s, max(1, int(move_time)))
                else:
                    self.dog.move(axis, speed)
                    time.sleep(max(0, move_time))
                    self.dog.stop()
            finally:
                self.is_moving = False
                self.direction = "stop"
                self.busy = False

        if self.dog:
            threading.Thread(target=run, daemon=True).start()
        return self._ok(f"机器人以速度{abs(speed)}向{direction}移动{move_time}秒")

    def _timed_turn(self, turn_speed, turn_time, direction):
        if self.busy:
            return {"status": "error", "message": "正在执行其他动作"}
        self.is_turn = True
        self.turn = direction
        self.turn_speed = turn_speed
        sign = 1.5 if direction == "left" else -1.5

        def run():
            self.busy = True
            try:
                if self.dog_type == "rider" and hasattr(self.dog, "rider_turn"):
                    # Rider 轮式: (角速度°/s, 时长秒) 自带计时 (对齐 apps/ai/robot_tools)
                    self.dog.rider_turn(sign * turn_speed, max(0, int(turn_time)))
                else:
                    self.dog.turn(sign * turn_speed)
                    time.sleep(max(0, turn_time))
                    self.dog.stop()
            finally:
                self.is_turn = False
                self.turn = "stop"
                self.busy = False

        if self.dog:
            threading.Thread(target=run, daemon=True).start()
        return self._ok(f"机器人以{turn_speed}度/秒向{direction}转{turn_time}秒")

    def _action(self, action):
        action = int(action)
        # 按机型分流: Ranger(RA固件)→ra_action, Rider→rider_action, 其余四足 action
        use_ra = self.dog_type == "ranger" and \
            self.dog is not None and hasattr(self.dog, "ra_action")
        use_rider = (not use_ra and self.dog_type in ("rider", "ranger")
                     and self.dog is not None and hasattr(self.dog, "rider_action"))
        if use_ra or use_rider:
            name = self._RIDER_ACTION_NAMES.get(action, str(action))
            duration = self._RIDER_ACTION_TIMES.get(action, 3)
        else:
            name = (self._action_names[action - 1] if 1 <= action <= 24
                    else {128: "向上抓取", 129: "向中抓取", 130: "向下抓取"}.get(action, str(action)))
            duration = self._action_times.get(action, 5)
        self.last_action = name

        def run():
            self.busy = True
            try:
                if use_ra:
                    self.dog.ra_action(action, wait=True)
                elif use_rider:
                    self.dog.rider_action(action, wait=True)
                else:
                    self.dog.action(action)
                time.sleep(duration)
            finally:
                self.busy = False

        if self.dog:
            threading.Thread(target=run, daemon=True).start()
        return self._ok(f"机器人执行动作: {name} (约{duration}秒)")

    def _gait(self, gait_mode):
        gait_list = ["trot", "walk", "high_walk", "slow_trot"]
        gait_mode = int(gait_mode)
        if not 1 <= gait_mode <= 4:
            return {"status": "error", "message": "步态参数超出范围"}
        self.gait_type = gait_list[gait_mode - 1]
        if self.dog:
            # XGO_DOG 驱动的方法名是 gait_type (旧代码误调不存在的 gait, 必炸)
            fn = getattr(self.dog, "gait", None) or getattr(self.dog, "gait_type", None)
            if fn:
                fn(self.gait_type)
        return self._ok(f"步态已调整为{self.gait_type}")

    def _pace(self, pace_mode):
        pace_list = ["slow", "normal", "high"]
        pace_mode = int(pace_mode)
        if not 1 <= pace_mode <= 3:
            return {"status": "error", "message": "步频参数超出范围"}
        self.pace = pace_list[pace_mode - 1]
        if self.dog:
            self.dog.pace(self.pace)
        return self._ok(f"迈步频率已调整为{self.pace}")

    def _claw(self, claw):
        self.claw = max(0, min(255, int(claw)))
        if self.dog:
            self.dog.claw(self.claw)
        return self._ok(f"夹爪开合度调整为{self.claw}")

    def _translation(self, x, y, z):
        self.translation = [x, y, z]
        if self.dog:
            self.dog.attitude(["x", "y", "z"], [x, y, z])
        return self._ok("机身平移完成")

    def _attitude(self, r, p, y):
        self.attitude = [r, p, y]
        if self.dog:
            self.dog.attitude(["r", "p", "y"], [r, p, y])
        return self._ok("姿态调整完成")

    def _arm(self, arm_x, arm_z):
        self.arm = [arm_x, arm_z]
        if self.dog:
            self.dog.arm(arm_x, arm_z)
        return self._ok("机械臂调整完成")

    def _perform(self, perform):
        if self.dog:
            self.dog.perform(int(perform))
        return self._ok(f"表演模式: {'开启' if int(perform) else '关闭'}")

    # ============ 扩展运动方法 (对齐 apps/ai/robot_tools.py, 按机型+底层能力注册) ============
    def _spin(self, fn):
        """后台线程执行运动命令, 避免阻塞 asyncio 事件循环 (与 _timed_move/_action 一致)"""
        threading.Thread(target=fn, daemon=True).start()

    def _register_extra_methods(self):
        dog = self.dog

        def ok(*apis):
            return dog is not None and any(hasattr(dog, a) for a in apis)

        # ---- 四足扩展 (mini / lite / miniw 等支持时) ----
        if ok("read_roll", "read_pitch", "read_yaw"):
            self.add_method("ReadImu", "读IMU姿态角", [
                Parameter("axis", "roll/pitch/yaw", ValueType.STRING),
            ], lambda p: self._read_imu(p["axis"].get_value()))
        if ok("imu"):
            self.add_method("Imu", "IMU自稳开关", [
                Parameter("mode", "0关1开", ValueType.NUMBER),
            ], lambda p: self._imu(p["mode"].get_value()))
        if ok("leg"):
            self.add_method("Leg", "单腿位置", [
                Parameter("leg_id", "1左前2右前3右后4左后", ValueType.NUMBER),
                Parameter("x", "mm", ValueType.NUMBER),
                Parameter("y", "mm", ValueType.NUMBER),
                Parameter("z", "mm", ValueType.NUMBER),
            ], lambda p: self._leg(p["leg_id"].get_value(), p["x"].get_value(),
                                   p["y"].get_value(), p["z"].get_value()))
        if ok("motor"):
            self.add_method("Motor", "单舵机角度", [
                Parameter("motor_id", "11-13左前21-23右前31-33右后41-43左后51臂", ValueType.NUMBER),
                Parameter("angle", "角度", ValueType.NUMBER),
            ], lambda p: self._motor(p["motor_id"].get_value(), p["angle"].get_value()))
        if ok("mark_time"):
            self.add_method("MarkTime", "原地踏步", [
                Parameter("step", "抬腿高mm(10-35)", ValueType.NUMBER),
            ], lambda p: self._mark_time(p["step"].get_value()))
        if ok("periodic_tran"):
            self.add_method("PeriodicTran", "周期往复平移", [
                Parameter("axis", "x/y/z", ValueType.STRING),
                Parameter("period", "周期秒1.5-8", ValueType.NUMBER),
                Parameter("wait_time", "时长秒", ValueType.NUMBER),
            ], lambda p: self._periodic_tran(p["axis"].get_value(), p["period"].get_value(),
                                            p["wait_time"].get_value()))
        if ok("periodic_rot"):
            self.add_method("PeriodicRot", "周期往复旋转", [
                Parameter("axis", "r/p/y", ValueType.STRING),
                Parameter("period", "周期秒1.5-8", ValueType.NUMBER),
                Parameter("wait_time", "时长秒", ValueType.NUMBER),
            ], lambda p: self._periodic_rot(p["axis"].get_value(), p["period"].get_value(),
                                           p["wait_time"].get_value()))

        # ---- Rider 专属 (R 固件 → xgorider 驱动; Ranger 走 ra_* 接口不重复注册) ----
        if self.dog_type == "rider" and ok("rider_roll"):
            self.add_method("RiderRoll", "调整Rider横滚角", [
                Parameter("angle", "角度[-17,17]", ValueType.NUMBER),
            ], lambda p: self._rider_roll(p["angle"].get_value()))
        if self.dog_type == "rider" and ok("rider_height"):
            self.add_method("RiderHeight", "调整Rider身高", [
                Parameter("height", "高度[60,120]mm", ValueType.NUMBER),
            ], lambda p: self._rider_height(p["height"].get_value()))
        if self.dog_type == "rider" and ok("rider_led"):
            self.add_method("RiderLed", "控制Rider LED颜色", [
                Parameter("index", "LED编号 0-5", ValueType.NUMBER),
                Parameter("r", "红 0-255", ValueType.NUMBER),
                Parameter("g", "绿 0-255", ValueType.NUMBER),
                Parameter("b", "蓝 0-255", ValueType.NUMBER),
            ], lambda p: self._rider_led(p["index"].get_value(), p["r"].get_value(),
                                         p["g"].get_value(), p["b"].get_value()))
        if self.dog_type == "rider" and ok("rider_turn"):
            self.add_method("RiderTurn", "Rider原地旋转", [
                Parameter("speed", "角速度[-360,360]°/s 正左负右", ValueType.NUMBER),
                Parameter("runtime", "时长(秒)", ValueType.NUMBER),
            ], lambda p: self._rider_turn(p["speed"].get_value(), p["runtime"].get_value()))
        if self.dog_type == "rider" and ok("rider_perform"):
            self.add_method("RiderPerform", "Rider循环表演", [
                Parameter("mode", "0=关闭 1=开启", ValueType.NUMBER),
            ], lambda p: self._rider_perform(p["mode"].get_value()))
        if self.dog_type == "rider" and ok("rider_periodic_roll"):
            self.add_method("RiderPeriodicRoll", "Rider周期性Roll摇摆", [
                Parameter("period", "周期[1,2]秒 0停止", ValueType.NUMBER),
                Parameter("wait_time", "时长(秒)", ValueType.NUMBER),
            ], lambda p: self._rider_periodic_roll(p["period"].get_value(), p["wait_time"].get_value()))
        if self.dog_type == "rider" and ok("rider_periodic_z"):
            self.add_method("RiderPeriodicZ", "Rider周期性Z轴升降", [
                Parameter("period", "周期[1,2]秒 0停止", ValueType.NUMBER),
                Parameter("wait_time", "时长(秒)", ValueType.NUMBER),
            ], lambda p: self._rider_periodic_z(p["period"].get_value(), p["wait_time"].get_value()))
        if self.dog_type == "rider" and ok("rider_balance_roll"):
            self.add_method("RiderBalanceRoll", "Rider Roll轴自平衡", [
                Parameter("mode", "0=关闭 1=开启", ValueType.NUMBER),
            ], lambda p: self._rider_balance_roll(p["mode"].get_value()))
        if self.dog_type == "rider" and ok("rider_reset_odom"):
            self.add_method("RiderResetOdom", "重置Rider里程计", [],
                            lambda p: self._rider_reset_odom())
        if self.dog_type == "rider" and ok("rider_calibration"):
            self.add_method("RiderCalibration", "校准Rider", [
                Parameter("state", "start/end", ValueType.STRING),
            ], lambda p: self._rider_calibration(p["state"].get_value()))

        # ---- RA 专属 (RA 固件 → Ranger 机型, XGO_RIDER 驱动的 ra_* 接口) ----
        if self.dog_type == "ranger" and ok("ra_action"):
            self.add_method("RaAction", "RA预设动作", [
                Parameter("action", "1摇摆2起伏3前后4蛇形5升降旋转6晃动255重置", ValueType.NUMBER),
            ], lambda p: self._action(p["action"].get_value()))
        if self.dog_type == "ranger" and ok("ra_move_x"):
            self.add_method("RaMove", "RA前后移动", [
                Parameter("direction", "forward/backward", ValueType.STRING),
                Parameter("speed", "速度[-87,87]", ValueType.NUMBER),
            ], lambda p: self._ra_move(p["direction"].get_value(), p["speed"].get_value()))
        if self.dog_type == "ranger" and ok("ra_turn"):
            self.add_method("RaTurn", "RA原地旋转", [
                Parameter("direction", "left/right", ValueType.STRING),
                Parameter("speed", "速度[-87,87]", ValueType.NUMBER),
            ], lambda p: self._ra_turn(p["direction"].get_value(), p["speed"].get_value()))
        if self.dog_type == "ranger" and ok("ra_roll"):
            self.add_method("RaRoll", "调整RA横滚角", [
                Parameter("angle", "角度[-87,87]", ValueType.NUMBER),
            ], lambda p: self._ra_roll(p["angle"].get_value()))
        if self.dog_type == "ranger" and ok("ra_height"):
            self.add_method("RaHeight", "调整RA身高", [
                Parameter("height", "[0,100]", ValueType.NUMBER),
            ], lambda p: self._ra_height(p["height"].get_value()))
        if self.dog_type == "ranger" and ok("ra_pitch"):
            self.add_method("RaPitch", "调整RA俯仰角", [
                Parameter("angle", "角度[-87,87]", ValueType.NUMBER),
            ], lambda p: self._ra_pitch(p["angle"].get_value()))
        if self.dog_type == "ranger" and ok("ra_mode"):
            self.add_method("RaMode", "切换RA运动模式", [
                Parameter("mode", "0=轮足 1=小车", ValueType.NUMBER),
            ], lambda p: self._ra_mode(p["mode"].get_value()))
        if self.dog_type == "ranger" and ok("ra_perform"):
            self.add_method("RaPerform", "RA循环表演", [
                Parameter("mode", "0=关闭 1=开启", ValueType.NUMBER),
            ], lambda p: self._ra_perform(p["mode"].get_value()))
        if self.dog_type == "ranger" and ok("ra_imu"):
            self.add_method("RaImu", "RA自稳定", [
                Parameter("mode", "0=关闭 1=开启", ValueType.NUMBER),
            ], lambda p: self._ra_imu(p["mode"].get_value()))
        if self.dog_type == "ranger" and ok("ra_led"):
            self.add_method("RaLed", "控制RA LED颜色", [
                Parameter("index", "LED编号 1-4", ValueType.NUMBER),
                Parameter("r", "红 0-255", ValueType.NUMBER),
                Parameter("g", "绿 0-255", ValueType.NUMBER),
                Parameter("b", "蓝 0-255", ValueType.NUMBER),
            ], lambda p: self._ra_led(p["index"].get_value(), p["r"].get_value(),
                                      p["g"].get_value(), p["b"].get_value()))
        if self.dog_type == "ranger" and ok("ra_reset"):
            self.add_method("RaReset", "重置RA到初始状态", [], lambda p: self._ra_reset())

        # ---- 电池 (读电量) ----
        if ok("read_battery", "rider_read_battery"):
            self.add_method("ReadBattery", "读取电池电量%", [],
                            lambda p: self._read_battery())

        # ---- 视觉抓球 / 寻找人类 (需摄像头; 运动由后台线程执行, 立即返回) ----
        # 抓球仅支持有机械臂的四足机型 (Mini/Lite/Mini3W/Mini2SW), 且机器人已连接
        if self.dog is not None and self._ball_supported():
            self.add_method("FindBall", "寻找小球,返回方位距离", [
                Parameter("color", "red/green/blue", ValueType.STRING),
                Parameter("max_search_time", "搜索秒数", ValueType.NUMBER),
            ], lambda p: self._find_ball(p["color"].get_value(),
                                        p["max_search_time"].get_value()))
            self.add_method("CatchBall", "抓取小球,抓后调ReleaseBall放下", [
                Parameter("color", "red/green/blue", ValueType.STRING),
                Parameter("max_search_time", "搜索秒数", ValueType.NUMBER),
                Parameter("max_grab_attempts", "尝试次数", ValueType.NUMBER),
            ], lambda p: self._catch_ball(p["color"].get_value(),
                                         p["max_search_time"].get_value(),
                                         p["max_grab_attempts"].get_value()))
            self.add_method("ReleaseBall", "放下小球并站起复位", [],
                            lambda p: self._release_ball())
        if self.dog is not None:
            self.add_method("FindPerson", "寻找人脸", [
                Parameter("max_search_time", "搜索秒数", ValueType.NUMBER),
            ], lambda p: self._find_person(p["max_search_time"].get_value()))

        # ---- 手势识别 (本地原子视觉, 识别画面自动上屏; 描述类场景由 self.camera.take_photo 走服务端 VLM, 体验更好) ----
        if self._atom_vision_available():
            self.add_method("RecognizeGesture", "识别手势:Good/1-5/Stone/OK/Rock", [
                Parameter("wait_time", "等待秒数", ValueType.NUMBER),
            ], lambda p: self._recognize_gesture(p["wait_time"].get_value()))

        # ---- 长任务状态查询 (抓球/寻人/放球为后台异步执行, 立即返回) ----
        self.add_method("QueryTask", "查询长任务(抓球/寻人)状态与结果", [],
                        lambda p: self._query_task())

    # ============ 长任务统一执行 (后台线程 + 可查询状态) ============
    def _run_long_task(self, task, fn, ok_msg):
        """启动一个后台长任务, 立即返回; 状态/结果记入 self._long_task 供 QueryTask 查询。

        fn: 无参可调用, 返回结果字符串; 抛异常则记录为 error。
        """
        self._long_task = {"task": task, "state": "running", "result": ""}

        def wrapped():
            try:
                self.busy = True   # 长任务期间拒绝其它动作
                result = fn()
                self._long_task["state"] = "done"
                self._long_task["result"] = result or ""
            except Exception as e:
                self._long_task["state"] = "error"
                self._long_task["result"] = f"执行异常: {e}"
            finally:
                self.busy = False

        threading.Thread(target=wrapped, daemon=True).start()
        return self._ok(ok_msg)

    def _query_task(self):
        t = self._long_task
        return {"status": "success", "task": t.get("task", ""),
                "state": t.get("state", "idle"), "result": t.get("result", "")}

    # ---- 四足扩展 handler ----
    def _read_imu(self, axis):
        if self.dog is None:
            return {"status": "error", "message": "机器人不可用"}
        fn = {"roll": "read_roll", "pitch": "read_pitch", "yaw": "read_yaw"}.get(axis or "")
        if fn is None or not hasattr(self.dog, fn):
            return {"status": "error", "message": f"不支持的轴向: {axis}"}
        try:
            return self._ok(f"{axis}: {getattr(self.dog, fn)()}°")
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _imu(self, mode):
        if self.dog is None or not hasattr(self.dog, "imu"):
            return {"status": "error", "message": "IMU自稳不可用"}
        try:
            self.dog.imu(int(mode))
            return self._ok("IMU自稳已" + ("开启" if int(mode) else "关闭"))
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _leg(self, leg_id, x, y, z):
        if self.dog is None or not hasattr(self.dog, "leg"):
            return {"status": "error", "message": "单腿控制不可用"}
        leg_id = int(leg_id)
        if leg_id not in (1, 2, 3, 4):
            return {"status": "error", "message": "腿编号必须为1-4"}
        try:
            self.dog.leg(leg_id, [int(x), int(y), int(z)])
            return self._ok(f"腿{leg_id}位置设置完成")
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _motor(self, motor_id, angle):
        if self.dog is None or not hasattr(self.dog, "motor"):
            return {"status": "error", "message": "舵机控制不可用"}
        motor_id = int(motor_id)
        if motor_id not in (11, 12, 13, 21, 22, 23, 31, 32, 33, 41, 42, 43, 51):
            return {"status": "error", "message": f"无效舵机编号: {motor_id}"}
        try:
            self.dog.motor(motor_id, int(angle))
            return self._ok(f"舵机{motor_id}角度={angle}°")
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _mark_time(self, step):
        if self.dog is None or not hasattr(self.dog, "mark_time"):
            return {"status": "error", "message": "原地踏步不可用"}
        def run():
            try:
                self.dog.mark_time(int(step))
                time.sleep(3)
                self.dog.reset()
            except Exception as e:
                print(f"[iot] mark_time 异常: {e}")
        self._spin(run)
        return self._ok(f"开始原地踏步({step}mm)")

    def _periodic_tran(self, axis, period, wait_time):
        if self.dog is None or not hasattr(self.dog, "periodic_tran"):
            return {"status": "error", "message": "周期性平移不可用"}
        axis = (axis or "z").lower()
        def run():
            try:
                self.dog.periodic_tran(axis, float(period))
                if float(wait_time or 0) > 0:
                    time.sleep(float(wait_time))
                    self.dog.reset()
            except Exception as e:
                print(f"[iot] periodic_tran 异常: {e}")
        self._spin(run)
        return self._ok("开始周期性平移")

    def _periodic_rot(self, axis, period, wait_time):
        if self.dog is None or not hasattr(self.dog, "periodic_rot"):
            return {"status": "error", "message": "周期性旋转不可用"}
        axis = (axis or "r").lower()
        def run():
            try:
                self.dog.periodic_rot(axis, float(period))
                if float(wait_time or 0) > 0:
                    time.sleep(float(wait_time))
                    self.dog.reset()
            except Exception as e:
                print(f"[iot] periodic_rot 异常: {e}")
        self._spin(run)
        return self._ok("开始周期性旋转")

    # ---- Rider handler ----
    def _rider_roll(self, angle):
        if self.dog is None or not hasattr(self.dog, "rider_roll"):
            return {"status": "error", "message": "仅Rider机型支持"}
        self._spin(lambda: self.dog.rider_roll(float(angle)))
        return self._ok(f"Rider Roll调整至{angle}°")

    def _rider_height(self, height):
        if self.dog is None or not hasattr(self.dog, "rider_height"):
            return {"status": "error", "message": "仅Rider机型支持"}
        self._spin(lambda: self.dog.rider_height(float(height)))
        return self._ok(f"Rider身高调整至{height}mm")

    def _rider_led(self, index, r, g, b):
        if self.dog is None or not hasattr(self.dog, "rider_led"):
            return {"status": "error", "message": "仅Rider机型支持"}
        self._spin(lambda: self.dog.rider_led(int(index), [int(r), int(g), int(b)]))
        return self._ok(f"LED{index}颜色设置为RGB({r},{g},{b})")

    def _rider_turn(self, speed, runtime):
        if self.dog is None or not hasattr(self.dog, "rider_turn"):
            return {"status": "error", "message": "仅Rider机型支持"}
        self._spin(lambda: self.dog.rider_turn(float(speed), int(runtime or 0)))
        return self._ok("Rider旋转(角速度" + str(speed) + "°/s)")

    def _rider_perform(self, mode):
        if self.dog is None or not hasattr(self.dog, "rider_perform"):
            return {"status": "error", "message": "仅Rider机型支持"}
        mode = int(mode)
        if mode not in (0, 1):
            return {"status": "error", "message": "mode必须为0/1"}
        self._spin(lambda: self.dog.rider_perform(mode))
        return self._ok("Rider表演" + ("开启" if mode else "关闭"))

    def _rider_periodic_roll(self, period, wait_time):
        if self.dog is None or not hasattr(self.dog, "rider_periodic_roll"):
            return {"status": "error", "message": "仅Rider机型支持"}
        def run():
            try:
                self.dog.rider_periodic_roll(float(period))
                if float(wait_time or 0) > 0:
                    time.sleep(float(wait_time))
                    self.dog.rider_periodic_roll(0)
            except Exception as e:
                print(f"[iot] rider_periodic_roll 异常: {e}")
        self._spin(run)
        return self._ok("Rider周期性Roll摇摆")

    def _rider_periodic_z(self, period, wait_time):
        if self.dog is None or not hasattr(self.dog, "rider_periodic_z"):
            return {"status": "error", "message": "仅Rider机型支持"}
        def run():
            try:
                self.dog.rider_periodic_z(float(period))
                if float(wait_time or 0) > 0:
                    time.sleep(float(wait_time))
                    self.dog.rider_periodic_z(0)
            except Exception as e:
                print(f"[iot] rider_periodic_z 异常: {e}")
        self._spin(run)
        return self._ok("Rider周期性Z轴升降")

    def _rider_balance_roll(self, mode):
        if self.dog is None or not hasattr(self.dog, "rider_balance_roll"):
            return {"status": "error", "message": "仅Rider机型支持"}
        mode = int(mode)
        if mode not in (0, 1):
            return {"status": "error", "message": "mode必须为0/1"}
        self._spin(lambda: self.dog.rider_balance_roll(mode))
        return self._ok("Rider Roll自平衡" + ("开启" if mode else "关闭"))

    def _rider_reset_odom(self):
        if self.dog is None or not hasattr(self.dog, "rider_reset_odom"):
            return {"status": "error", "message": "仅Rider机型支持"}
        self._spin(lambda: self.dog.rider_reset_odom())
        return self._ok("Rider里程计已重置")

    def _rider_calibration(self, state):
        if self.dog is None or not hasattr(self.dog, "rider_calibration"):
            return {"status": "error", "message": "仅Rider机型支持"}
        state = (state or "start").lower()
        if state not in ("start", "end"):
            return {"status": "error", "message": "state必须为start/end"}
        self._spin(lambda: self.dog.rider_calibration(state))
        return self._ok("Rider校准" + ("开始" if state == "start" else "结束"))

    # ---- RA handler ----
    def _ra_move(self, direction, speed):
        if self.dog is None or not hasattr(self.dog, "ra_move_x"):
            return {"status": "error", "message": "仅RA机型支持"}
        if direction == "forward":
            self._spin(lambda: self.dog.ra_move_x(abs(float(speed)), 1))
        else:
            self._spin(lambda: self.dog.ra_move_x(-abs(float(speed)), 1))
        return self._ok(f"RA {direction} 移动")

    def _ra_turn(self, direction, speed):
        if self.dog is None or not hasattr(self.dog, "ra_turn"):
            return {"status": "error", "message": "仅RA机型支持"}
        sp = abs(float(speed))
        if direction == "left":
            self._spin(lambda: self.dog.ra_turn(sp, 1))
        else:
            self._spin(lambda: self.dog.ra_turn(-sp, 1))
        return self._ok("RA " + ("左转" if direction == "left" else "右转"))

    def _ra_roll(self, angle):
        if self.dog is None or not hasattr(self.dog, "ra_roll"):
            return {"status": "error", "message": "仅RA机型支持"}
        self._spin(lambda: self.dog.ra_roll(float(angle)))
        return self._ok(f"RA Roll调整至{angle}°")

    def _ra_height(self, height):
        if self.dog is None or not hasattr(self.dog, "ra_height"):
            return {"status": "error", "message": "仅RA机型支持"}
        self._spin(lambda: self.dog.ra_height(float(height)))
        return self._ok(f"RA身高调整至{height}")

    def _ra_pitch(self, angle):
        if self.dog is None or not hasattr(self.dog, "ra_pitch"):
            return {"status": "error", "message": "仅RA机型支持"}
        self._spin(lambda: self.dog.ra_pitch(float(angle)))
        return self._ok(f"RA Pitch调整至{angle}°")

    def _ra_mode(self, mode):
        if self.dog is None or not hasattr(self.dog, "ra_mode"):
            return {"status": "error", "message": "仅RA机型支持"}
        mode = int(mode)
        if mode not in (0, 1):
            return {"status": "error", "message": "mode必须为0(轮足)/1(小车)"}
        self._spin(lambda: self.dog.ra_mode(mode))
        return self._ok("RA已切换至" + ("轮足模式" if mode == 0 else "小车模式"))

    def _ra_perform(self, mode):
        if self.dog is None or not hasattr(self.dog, "ra_perform"):
            return {"status": "error", "message": "仅RA机型支持"}
        mode = int(mode)
        if mode not in (0, 1):
            return {"status": "error", "message": "mode必须为0/1"}
        self._spin(lambda: self.dog.ra_perform(mode))
        return self._ok("RA表演" + ("开启" if mode else "关闭"))

    def _ra_imu(self, mode):
        if self.dog is None or not hasattr(self.dog, "ra_imu"):
            return {"status": "error", "message": "仅RA机型支持"}
        mode = int(mode)
        if mode not in (0, 1):
            return {"status": "error", "message": "mode必须为0/1"}
        self._spin(lambda: self.dog.ra_imu(mode))
        return self._ok("RA自稳定" + ("开启" if mode else "关闭"))

    def _ra_led(self, index, r, g, b):
        if self.dog is None or not hasattr(self.dog, "ra_led"):
            return {"status": "error", "message": "仅RA机型支持"}
        self._spin(lambda: self.dog.ra_led(int(index), [int(r), int(g), int(b)]))
        return self._ok(f"LED{index}颜色设置为RGB({r},{g},{b})")

    def _ra_reset(self):
        if self.dog is None or not hasattr(self.dog, "ra_reset"):
            return {"status": "error", "message": "仅RA机型支持"}
        self._spin(lambda: self.dog.ra_reset())
        return self._ok("RA已重置到初始状态")

    # ============ 电池 / 视觉抓球 / 寻人 (对齐 apps/ai/robot_tools.py) ============
    def _ball_model(self):
        """把 libs.robot_type 标准化机型转为 ball_catch_core 的机型串 (供 is_ball_catch_supported)"""
        return {"mini": "xgomini", "lite": "xgolite",
                "miniw": "xgomini2sw"}.get(self.dog_type)

    def _ball_supported(self) -> bool:
        """当前机型是否支持抓球 (有机械臂的四足机型)"""
        if self.dog is None:
            return False
        try:
            from libs.ball_catch_core import is_ball_catch_supported
            return is_ball_catch_supported(self._ball_model() or "")
        except Exception:
            return False

    @staticmethod
    def _normalize_color(color):
        c = (color or "").strip().lower()
        if c in ("r", "red"):
            return "red"
        if c in ("g", "green"):
            return "green"
        if c in ("b", "blue"):
            return "blue"
        return None

    # ---- 摄像头 (Picamera2, 与 vision.py 同源) ----
    def _open_cam(self):
        # 抓球独占相机前, 先释放原子视觉的常驻相机 (物理摄像头只有一个)
        try:
            from libs.atom_vision import get_vision
            get_vision().close_cam()
        except Exception:
            pass
        from picamera2 import Picamera2
        cam = Picamera2()
        cam.configure(cam.create_preview_configuration(
            main={"format": "RGB888", "size": (320, 240)}))
        cam.start()
        time.sleep(0.5)
        return cam

    def _close_cam(self):
        if self._cam is not None:
            try:
                self._cam.stop()
            except Exception:
                pass
            try:
                self._cam.close()
            except Exception:
                pass
            self._cam = None

    def _grab_frame(self):
        if self._cam is None:
            self._cam = self._open_cam()
        try:
            return self._cam.capture_array()   # 树莓派 RGB888 实际输出 BGR
        except Exception:
            return None

    def _read_battery(self):
        if self.dog is None:
            return {"status": "error", "message": "机器人不可用"}
        try:
            if hasattr(self.dog, "rider_read_battery"):
                v = self.dog.rider_read_battery()
            else:
                v = self.dog.read_battery()
            return self._ok(f"电量: {v}%")
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def _find_ball(self, color, max_search_time):
        if self.dog is None:
            return {"status": "error", "message": "机器人不可用"}
        if self.busy:
            return {"status": "error", "message": "正在执行其他任务"}
        ck = self._normalize_color(color)
        if ck is None:
            return {"status": "error", "message": f"不支持的颜色: {color}, 支持 red/green/blue"}

        done = threading.Event()
        result_box = {}

        def run():
            self.busy = True
            try:
                from libs.ball_catch_core import detect_ball, calc_distance, COLOR_NAMES
                self._close_cam()
                self._cam = self._open_cam()
                start = time.time()
                timeout = min(float(max_search_time or 30), self._MAX_TASK_SECONDS)
                while time.time() - start < timeout:
                    frame = self._grab_frame()
                    if frame is None:
                        time.sleep(0.1)
                        continue
                    x, y, r, _ = detect_ball(frame, ck)
                    if r > 0:
                        d = int(calc_distance(r))
                        print(f"[iot] find_ball: 找到{COLOR_NAMES[ck]}球 位置({x},{y}) 半径{r} 距离约{d}cm")
                        result_box["result"] = self._ok(f"找到{COLOR_NAMES[ck]}小球, 距离约{d}cm")
                        break
                    time.sleep(0.1)
                else:
                    print(f"[iot] find_ball: 搜索超时, 未找到{COLOR_NAMES[ck]}球")
                    result_box["result"] = {"status": "error", "message": f"搜索超时, 未找到{COLOR_NAMES[ck]}球"}
            except Exception as e:
                print(f"[iot] find_ball 异常: {e}")
                result_box["result"] = {"status": "error", "message": str(e)}
            finally:
                self.busy = False
                done.set()          # 先让结果返回给 MCP, 相机清理再执行(libcamera stop 可能慢)
                self._close_cam()

        self._spin(run)
        # 等后台任务完成再返回, 让 LLM 拿到真实结果 (配合 mcp_server 的 executor)
        done.wait(timeout=self._MAX_TASK_SECONDS + 8)
        return result_box.get("result", {"status": "error", "message": "找球任务未正常完成"})

    def _catch_ball(self, color, max_search_time, max_grab_attempts):
        if self.dog is None:
            return {"status": "error", "message": "机器人不可用"}
        if self.busy:
            return {"status": "error", "message": "正在执行其他任务"}
        ck = self._normalize_color(color)
        if ck is None:
            return {"status": "error", "message": f"不支持的颜色: {color}, 支持 red/green/blue"}

        done = threading.Event()
        result_box = {}

        def run():
            self.busy = True
            try:
                from libs.ball_catch_core import (
                    detect_ball, calc_distance, execute_catch_sequence,
                    prepare_catch_pose, release_catch, stand_up,
                    get_motion_params, EMA_ALPHA, COLOR_NAMES, CAM_W)
                dog = self.dog
                dog_type = {"xgolite": "L",
                            "xgomini3w": "W", "xgomini2sw": "W"}.get(self._ball_model(), "M")
                params = get_motion_params(dog_type)
                mintime_yaw, x_speed_far, x_speed_mid, turn_speed = (
                    params["mintime_yaw"], params["x_speed_far"],
                    params["x_speed_mid"], params["turn_speed"])
                x_center = CAM_W // 2
                x_distance = 22.0
                catch_offset = 20
                y1, y2 = 20, 25

                self._close_cam()
                self._cam = self._open_cam()
                prepare_catch_pose(dog, pitch_angle=15)

                start = time.time()
                timeout = min(float(max_search_time or 30), self._MAX_TASK_SECONDS)
                grab_attempts = 0
                grabbed = False
                mx = my = mr = 0.0
                uncircle = 0
                close_mode = False

                while time.time() - start < timeout:
                    frame = self._grab_frame()
                    if frame is None:
                        time.sleep(0.05)
                        continue
                    ball_x, ball_y, ball_r, _ = detect_ball(frame, ck)
                    if ball_r > 0:
                        uncircle = 0
                        if mx == 0 and my == 0 and mr == 0:
                            mx, my, mr = float(ball_x), float(ball_y), float(ball_r)
                        else:
                            mx = EMA_ALPHA * ball_x + (1 - EMA_ALPHA) * mx
                            my = EMA_ALPHA * ball_y + (1 - EMA_ALPHA) * my
                            mr = EMA_ALPHA * ball_r + (1 - EMA_ALPHA) * mr
                        distance = calc_distance(mr)
                        yaw_err = -(mx - x_center) / distance

                        if distance < x_distance + 5:
                            if not close_mode:
                                close_mode = True
                                dog.move_x(0); dog.turn(0)
                                dog.attitude("p", 22)
                                time.sleep(0.2)
                        else:
                            if close_mode:
                                close_mode = False
                                dog.attitude("y", 0)
                                time.sleep(0.1)

                        catch_dist = x_distance + 5 if close_mode else x_distance
                        if distance < catch_dist and -y1 / distance <= yaw_err <= y1 / distance:
                            dog.attitude("y", 5 * yaw_err)
                            dog.translation("x", distance - catch_offset)
                            time.sleep(0.5)
                            grabbed = execute_catch_sequence(dog, check_success=True)
                            grab_attempts += 1
                            if grabbed:
                                break
                            elif grab_attempts >= int(max_grab_attempts or 3):
                                release_catch(dog)
                                break
                            time.sleep(1)
                            continue
                        if distance < catch_dist and -y2 <= mx - x_center <= y2:
                            dog.attitude("y", 0.25 * (x_center - mx))
                            dog.translation("x", distance - catch_offset)
                            time.sleep(0.5)
                            grabbed = execute_catch_sequence(dog, check_success=True)
                            grab_attempts += 1
                            if grabbed:
                                break
                            elif grab_attempts >= int(max_grab_attempts or 3):
                                release_catch(dog)
                                break
                            time.sleep(1)
                            continue

                        if close_mode:
                            if distance >= x_distance:
                                dog.translation("x", min((distance - x_distance) * 1.5, 15))
                                time.sleep(0.2)
                            if abs(yaw_err) > y1 / distance:
                                dog.attitude("y", max(-10, min(10, 3 * yaw_err)))
                                time.sleep(0.15)
                        else:
                            if yaw_err > y1 / distance:
                                dog.gait_type("slow_trot")
                                dog.turn(turn_speed)
                                time.sleep(min(abs(0.4 * yaw_err) / 8 + mintime_yaw, 1.5))
                                dog.turn(0)
                            elif yaw_err < -y1 / distance:
                                dog.gait_type("slow_trot")
                                dog.turn(-turn_speed)
                                time.sleep(min(abs(0.4 * yaw_err) / 8 + mintime_yaw, 1.5))
                                dog.turn(0)
                            else:
                                step = 0.3 if distance >= x_distance + 3 else 0.2
                                speed = x_speed_far if distance >= x_distance + 10 else x_speed_mid
                                dog.gait_type("slow_trot")
                                dog.move_x(speed)
                                time.sleep(step)
                                dog.move_x(0)
                            time.sleep(0.15)
                    else:
                        uncircle += 1
                        if uncircle >= (15 if close_mode else 12):
                            uncircle = 0
                            if close_mode:
                                dog.translation("x", 0); dog.attitude("y", 0)
                            else:
                                dog.move_x(0); dog.turn(0)
                            time.sleep(0.4)
                    time.sleep(0.05)

                if grabbed:
                    dog.action(2)
                    time.sleep(3)
                    print(f"[iot] catch_ball: 抓取{COLOR_NAMES[ck]}成功, 尝试{grab_attempts}次")
                    result_box["result"] = self._ok(f"抓取{COLOR_NAMES[ck]}小球成功, 尝试{grab_attempts}次")
                else:
                    dog.reset()
                    time.sleep(0.5)
                    stand_up(dog)
                    print(f"[iot] catch_ball: 未抓取到{COLOR_NAMES[ck]}, 尝试{grab_attempts}次")
                    result_box["result"] = {"status": "error", "message": f"未抓取到{COLOR_NAMES[ck]}小球, 尝试{grab_attempts}次"}
            except Exception as e:
                print(f"[iot] catch_ball 异常: {e}")
                try:
                    self.dog.reset()
                except Exception:
                    pass
                result_box["result"] = {"status": "error", "message": str(e)}
            finally:
                self.busy = False
                done.set()          # 先让结果返回给 MCP, 相机清理再执行(libcamera stop 可能慢)
                self._close_cam()

        self._spin(run)
        # 等后台任务完成再返回, 让 LLM 拿到真实结果 (配合 mcp_server 的 executor)
        done.wait(timeout=self._MAX_TASK_SECONDS + 8)
        return result_box.get("result", {"status": "error", "message": "抓球任务未正常完成"})

    def _release_ball(self):
        if self.dog is None:
            return {"status": "error", "message": "机器人不可用"}
        if self.busy:
            return {"status": "error", "message": "正在执行其他任务"}
        try:
            from libs.ball_catch_core import release_catch
        except Exception as e:
            return {"status": "error", "message": str(e)}

        done = threading.Event()
        result_box = {}

        def run():
            self.busy = True
            try:
                release_catch(self.dog)
                self.dog.action(2)
                time.sleep(3)
                result_box["result"] = self._ok("小球已放下, 机器人已站起")
            except Exception as e:
                print(f"[iot] release_ball 异常: {e}")
                result_box["result"] = {"status": "error", "message": str(e)}
            finally:
                self.busy = False
                done.set()

        self._spin(run)
        # 等后台任务完成再返回, 让 LLM 拿到真实结果 (配合 mcp_server 的 executor)
        done.wait(timeout=self._MAX_TASK_SECONDS + 8)
        return result_box.get("result", {"status": "error", "message": "放球任务未正常完成"})

    # ============ 原子视觉 (libs/atom_vision, 单帧识别, 画面直写 framebuffer 上屏) ============
    # 自建轻量视觉 (零 edulib/Qt 依赖 —— XGOEDU 在 MCP 后台进程初始化会因 Qt 死锁):
    # 每次识别抓帧→推理→标注→镜像后直写 /dev/fb-spi, 用户能看见摄像头画面 (安全感)。

    def _atom_vision_available(self) -> bool:
        try:
            from libs.atom_vision import get_vision
            return get_vision().available()
        except Exception:
            return False

    def _recognize_gesture(self, wait_time):
        """限时窗口内循环单帧识别, 连续 2 帧相同手势即确认 (防抖)。
        先预热相机/模型再计时, 窗口最短 5s (用户说完话到比出手势需要时间);
        超时属观测结果, 返回 success 让 LLM 自然回应。"""
        if self.busy:
            return {"status": "error", "message": "正在执行其他任务"}
        self.busy = True
        try:
            from libs.atom_vision import get_vision, hold_screen, release_screen
            vision = get_vision()
            hold_screen()  # 识别画面占屏, 丢弃表情帧防交替闪烁
            vision.grab_frame()  # 预热: 相机启动+模型加载不吃识别窗口
            timeout = max(5.0, min(float(wait_time or 6), self._MAX_TASK_SECONDS))
            start = time.time()
            prev, stable = None, 0
            while time.time() - start < timeout:
                text = None
                try:
                    r = vision.gesture_frame()  # (手势, 'left'/'right') 或 None
                    if r:
                        side = "右" if r[1] == "right" else "左"
                        text = f"{side}手比 {r[0]}"
                except Exception as e:
                    print(f"[iot] gesture 单帧异常: {e}")
                if text and text == prev:
                    stable += 1
                    if stable >= 2:
                        print(f"[iot] gesture: 识别到 {text}")
                        return self._ok(text)
                elif text:
                    prev, stable = text, 1
                # text 为空时保留 prev, 短暂丢帧不重置稳定计数
                time.sleep(0.2)
            return self._ok(f"{int(timeout)}秒内未识别到手势, 可能没看清画面, 可让用户再比一次")
        except Exception as e:
            print(f"[iot] gesture 异常: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            try:
                release_screen()  # 表情动画下一帧自动恢复
            except Exception:
                pass
            self.busy = False

    def _find_person(self, max_search_time):
        if self.dog is None:
            return {"status": "error", "message": "机器人不可用"}
        if self.busy:
            return {"status": "error", "message": "正在执行其他任务"}

        done = threading.Event()
        result_box = {}

        def run():
            self.busy = True
            try:
                from libs.atom_vision import get_vision, hold_screen, release_screen
                vision = get_vision()
                if not vision.available():
                    result_box["result"] = {"status": "error", "message": "人脸检测不可用"}
                    return
                hold_screen()  # 搜索画面占屏, 丢弃表情帧防交替闪烁
                start = time.time()
                timeout = min(float(max_search_time or 45), self._MAX_TASK_SECONDS)
                while time.time() - start < timeout:
                    rect = vision.face_frame()
                    if rect is not None:
                        x, y, w, h = rect
                        print(f"[iot] find_person: 找到人脸 ({int(x)},{int(y)}) {int(w)}x{int(h)}")
                        result_box["result"] = self._ok(f"找到人脸, 位置({int(x)},{int(y)}) 尺寸{int(w)}x{int(h)}")
                        break
                    time.sleep(0.1)
                else:
                    print("[iot] find_person: 搜索超时, 未找到人脸")
                    # 超时是观测结果而非故障, 报 error 会触发服务端'工具调用错误'
                    result_box["result"] = self._ok("搜索超时, 画面中未找到人脸")
            except Exception as e:
                print(f"[iot] find_person 异常: {e}")
                result_box["result"] = {"status": "error", "message": str(e)}
            finally:
                try:
                    release_screen()
                except Exception:
                    pass
                self.busy = False
                done.set()
                # 不关原子视觉相机 (常驻复用; 抓球开自己相机时会自动先释放)

        self._spin(run)
        # 等后台任务完成再返回, 让 LLM 拿到真实结果 (配合 mcp_server 的 executor)
        done.wait(timeout=self._MAX_TASK_SECONDS + 8)
        return result_box.get("result", {"status": "error", "message": "寻人任务未正常完成"})
