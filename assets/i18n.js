(function () {
  'use strict';
  const KEY = 'xgoduck.lang';

  const DICT = {
    zh: {
      'common.nav.control': '控制',
      'common.nav.calibrate': '标定',
      'common.nav.servo': '舵机设置',
      'common.exit': '退出',
      'common.torque_off': '卸力',

      'idx.title': 'XGO Duck',
      'idx.metrics.feedback': '反馈频率',
      'idx.metrics.policy': '策略频率',
      'idx.metrics.servos': '舵机在线',
      'idx.metrics.imu': 'IMU',
      'idx.imu.online': '在线',
      'idx.imu.offline': '离线',
      'idx.mode.title': '模式',
      'idx.mode.shadow': '仅推理',
      'idx.mode.hold': '默认姿势',
      'idx.mode.policy': '行走 / 起身',
      'idx.mode.pick': '抓取',
      'idx.mode.roulade': '翻滚',
      'idx.mode.roller': 'Roller 模式',
      'idx.mode.sit': '坐下',
      'idx.mode.stand': '站起',
      'idx.mode.left_kick': '左脚踢',
      'idx.mode.right_kick': '右脚踢',
      'idx.mode.on': '开',
      'idx.mode.off': '关',
      'idx.hint.mode': '启用 Roller 模式后，“行走 / 起身”使用轮式行走模型；恢复动作始终使用起身模型。命令覆盖全部 15 个 ID；某个 ID 缺失会在此显示，但不阻塞其他关节。默认姿势与行走发送位置目标。离开页面或切到后台会清零运动，并保留当前控制模式。需要卸力或进入标定。抓取仅在直立行走时可用：播放 4 s 的抓取周期后回到行走。翻滚动作持续 1.9 s，扭身和头部归零，腿部/颈部平滑系数为 0.15，之后回到行走和原平滑设置；这 1.9 s 内倾角不会触发起身。坐下使用固定 action scale 1.1 并保持坐姿，按钮变为“站起”。站起运行 1 s 后回到行走。坐/站期间行走、抓取、翻滚不可用。跌倒会取消坐/站并走正常起身恢复。left_kick 和 right_kick 各播放 0.4 s，期间速度和姿态命令为零，随后回到行走。踢球参数固定为 scale 0.8、腿部/头部 α 0，不受滑块影响。跌倒会取消踢球并走正常起身恢复。',
      'idx.recovery.title': '行走与恢复',
      'idx.recovery.model': '当前模型',
      'idx.recovery.tilt': '相对 (0, 0, -1) 的倾角',
      'idx.recovery.timer': '状态计时',
      'idx.recovery.hint': '行走：倾角大于 55° 持续 0.15 s 后切换到起身模型，先保持动作为 0 共 1 s，再执行起身动作。起身：倾角小于 15° 持续 1 s 后回到行走。起身期间扭身和头部命令归零。仅推理模式只预览切换过程，不启用扭矩。抓取期间命令是相位编码；嘴部张开到 30°，当 φ ≥ 0.4 时闭合。',
      'idx.motion.title': '运动',
      'idx.motion.warning': '<strong>注意：</strong>长时间保持外八站姿会让 Roller 电机发热，请定期休息散热。',
      'idx.motion.left': '前后 / 横移',
      'idx.motion.left_roller': '前后 / 前后',
      'idx.motion.right': '偏航',
      'idx.motion.zero': '停止运动',
      'idx.motion.hint': '拖动摇杆，松开后自动回中。每个轴有 15% 中心死区和运动平滑。按键：W/S 前后，A/D 横移，Q/E 偏航，空格卸力。 <span id="motion-limits"></span> 范围随 Roller 模式变化；切换模式会清零运动。',
      'idx.head.title': '头部与嘴',
      'idx.head.hint': '头部 1–4 为弧度（±1）。嘴部是 34 号电机，0–30°，随 50 ms 命令心跳发送，不进入策略观测。',
      'idx.head.slider': '头部 {n}',
      'idx.head.mouth': '嘴部 34 (°)',
      'idx.action.title': '动作设置',
      'idx.action.profile_walk': '普通行走 / 其他模型',
      'idx.action.profile_roller': 'Roller 行走',
      'idx.action.scale': '动作幅度',
      'idx.action.leg': '腿部 α',
      'idx.action.head': '头部 α',
      'idx.action.hint': 'Roller 行走与其他模型分别保存设置。Roller 默认值：幅度 0.7、腿部 α 0.4、头部 α 0.5；其他模型默认值：幅度 1、腿部 α 0.35、头部 α 0.45。踢球固定使用幅度 0.8、腿部/头部 α 0，不受这些滑块影响。坐/站固定使用幅度 1.1，其腿部/头部 α 跟随普通模型滑块。幅度改变相对于 home 姿态的动作偏移。松开滑块后应用；重启 app 会恢复各组默认值。',
      'idx.imu.title': 'IMU 滤波',
      'idx.imu.gyro': '角速度 α',
      'idx.imu.tau': '重力投影 τ (s)',
      'idx.imu.hint': '角速度 α 越大，平滑越强；重力 τ 越大，加速度计修正越慢。修改立即生效；重启 app 会恢复默认值。',
      'idx.telemetry.title': 'IMU 与舵机',
      'idx.telemetry.target': '目标',
      'idx.telemetry.waiting': '等待命令回显…',
      'idx.msg.connecting': '正在连接…',
      'idx.msg.mode_updated': '模式已更新',
      'idx.msg.roller_on': 'Roller 模式已开启 · {model}',
      'idx.msg.roller_off': 'Roller 模式已关闭 · {model}',
      'idx.msg.pick_started': '抓取已开始 · {model}',
      'idx.msg.roulade': '翻滚 · {model}',
      'idx.msg.sit_holding': '坐下 · 保持中',
      'idx.msg.stand_return': '站起 · {seconds} s 后回到行走',
      'idx.msg.kick_return': '{side} · 0.4 s 后回到行走',
      'idx.msg.bridge': 'Bridge {mbps} Mbps · 状态延迟 {age} ms',
      'idx.msg.request_failed': '请求失败',
      'idx.msg.status_failed': '状态读取失败',
      'idx.status.enabled': '已使能',
      'idx.status.torque_off': '已卸力',
      'idx.phase.walk': '行走',
      'idx.phase.default_pose': '起身 · 动作 = 0',
      'idx.phase.getup': '起身',
      'idx.phase.pick': '抓取',
      'idx.phase.roulade': '翻滚',
      'idx.phase.sit': '坐下',
      'idx.phase.stand': '站起',
      'idx.phase.idle': '空闲',
      'idx.timer.roulade': '翻滚 {remaining} / {seconds} s',
      'idx.timer.kick': '{side} 剩余 {remaining} s',
      'idx.timer.sit': '坐下 · 保持中',
      'idx.timer.stand': '站起剩余 {remaining} s',
      'idx.timer.pick': '抓取 φ {phi} / 1.00',
      'idx.timer.hold': '保持剩余 {remaining} s',
      'idx.timer.upright': '直立 {elapsed} / 1.00 s',
      'idx.timer.fallen': '跌倒 {elapsed} / 0.15 s',
      'idx.limits': '限幅：vx -{back} 到 +{forward}，vy {strafe}，ωz ±{yaw}。',
      'idx.health': '已发送 {sent} · MCU 回显 {echo} · 命令延迟 {age} · 缺口 {gaps} · 拒绝 {rejected}',

      'cal.title': '舵机中位标定',
      'cal.entering': '正在进入标定并卸载全部舵机…',
      'cal.procedure': '操作步骤',
      'cal.unload': '全部卸力',
      'cal.hint': '打开此页会停止运动并卸载全部舵机。把机器人放在支撑架上，逐个把关节摆到<b>设计零位</b>（关节角 0°，不是站立姿态），再按该关节的“设置中位并保持”。按下后固件会把当前位置写为该舵机的内部中位，并只让这一个舵机保持位置 2048，其余仍卸力。左右腿必须镜像。全部关节完成后退出；退出会释放所有舵机。',
      'cal.joints': '关节',
      'cal.waiting': '等待反馈',
      'cal.unloaded': '已卸力',
      'cal.centering': '对中中…',
      'cal.holding': '保持 2048',
      'cal.set': '设置中位并保持',
      'cal.setting': '正在设置 ID {id} 的中位…',
      'cal.sent': '中位命令已发送到 ID {id}，当前保持位置 2048。',
      'cal.ready': '全部舵机已卸力。用手转动一个关节到设计零位，再设置它的中位。',
      'cal.closed': '标定已关闭，全部舵机已卸力。',
      'cal.active': '标定进行中',
      'cal.inactive': '标定已关闭',
      'cal.encoder': '编码器 {pos}',
      'cal.no_feedback': '无舵机反馈',
      'cal.status_failed': '状态读取失败',
      'cal.request_failed': '请求失败',

      'srv.title': '舵机设置',
      'srv.id_warning': '修改 ID 时，只连接要修改的那一只舵机。同一总线上的重复 ID 无法区分。',
      'srv.initial': '先扫描舵机，再选择 ID 读取或修改设置。关节中位请统一在“标定”页完成。',
      'srv.scan': '扫描全部 ID',
      'srv.scan_stop': '停止扫描',
      'srv.scan_status_init': '扫描 0–253 号 ID 以查找已连接的舵机。',
      'srv.scan_progress_aria': '扫描进度',
      'srv.scan_results_aria': '已发现的舵机 ID',
      'srv.scan_hint': '选择一个已找到的 ID 读取或修改该舵机的设置。停止扫描会保留已找到的结果。ID 254 和 255 保留不用。',
      'srv.kp_kd': '设置 KP/KD',
      'srv.set_gains': '写入 KpKd',
      'srv.new_id': '修改 ID 为',
      'srv.change_id': '修改 ID',
      'srv.read': '读取',
      'srv.protection_title': '全部舵机电压 / 过流保护',
      'srv.enable': '启用',
      'srv.disable': '禁用',
      'srv.not_applied': '未应用',
      'srv.protection_current_aria': '保护电流，单位 A',
      'srv.set_current': '设置保护电流',
      'srv.protection_hint': '启用 / 禁用只切换保护开关；“设置保护电流”只修改电流阈值。两者都广播到全部舵机，不需要扫描或选择 ID。过热保护保持启用，已存储的电压限制不变。电流分辨率为 6.5 mA。广播写入没有单舵机确认。',
      'srv.selected': '已选择 ID {id}',
      'srv.id_range': 'ID 必须在 0 到 253 之间',
      'srv.no_mcu': '没有 MCU 反馈，舵机设置尚未就绪。',
      'srv.stopping_scan': '正在停止扫描…',
      'srv.closed': '舵机设置已关闭',
      'srv.changing_id': '正在将 ID {id} 改为 {new_id}…',
      'srv.action_gains': '写入 KpKd',
      'srv.action_read': '读取',
      'srv.readout': 'ID {id} · 位置 {pos} · KP {kp} · KD {kd}',
      'srv.params_read': 'ID {id} · 参数已读取',
      'srv.id_changed_scan': 'ID 已修改，请重新扫描后选择更新后的舵机。',
      'srv.verified': 'ID {id} → {new_id} · 已验证',
      'srv.kp_written': 'ID {id} · KP/KD 已写入',
      'srv.preparing_scan': '正在准备扫描…',
      'srv.deploy': '请部署更新后的项目后再使用舵机设置。',
      'srv.scanning_torque_off': '扫描中 · 已卸力',
      'srv.scanning': '正在扫描 ID {id} · 已检查 {checked}/254 · 已找到 {found}',
      'srv.invalid_scan_reply': '扫描应答无效',
      'srv.scan_complete': '扫描完成 · 已检查 254 个 ID · 已找到 {found}',
      'srv.select_found': '选择一个已找到的 ID，读取或修改其设置。',
      'srv.no_servos': '没有舵机应答。请检查舵机电源、总线连接和波特率。',
      'srv.stopped': '已停止',
      'srv.scan_failed': '扫描失败',
      'srv.partial': '已检查 {checked}/254 · 已找到 {found}（部分结果）',
      'srv.scan_stopped_continue': '扫描已停止。选择一个已找到的 ID 继续。',
      'srv.no_mcu_scan_stopped': 'MCU 无应答，扫描已停止。',
      'srv.protection_current_range': '请输入 0 到 425.9775 A 之间的保护电流。',
      'srv.broadcasting': '正在向所有舵机广播保护设置…',
      'srv.invalid_broadcast': '广播应答无效',
      'srv.current_sent': '广播已发送 · {current} A',
      'srv.protection_sent': '广播已发送 · 电压 / 过流保护 {state}',
      'srv.protection_broadcast': '保护设置已广播。单舵机写入未做确认。',
      'srv.request_failed': '请求失败'
    },

    en: {
      'common.nav.control': 'Control',
      'common.nav.calibrate': 'Calibration',
      'common.nav.servo': 'Servo setup',
      'common.exit': 'Exit',
      'common.torque_off': 'Torque off',

      'idx.title': 'XGO Duck',
      'idx.metrics.feedback': 'Feedback',
      'idx.metrics.policy': 'Policy',
      'idx.metrics.servos': 'Servos online',
      'idx.metrics.imu': 'IMU',
      'idx.imu.online': 'online',
      'idx.imu.offline': 'offline',
      'idx.mode.title': 'Mode',
      'idx.mode.shadow': 'Inference only',
      'idx.mode.hold': 'Default pose',
      'idx.mode.policy': 'Walk / get up',
      'idx.mode.pick': 'Pick',
      'idx.mode.roulade': 'Roulade',
      'idx.mode.roller': 'Roller mode',
      'idx.mode.sit': 'Sit',
      'idx.mode.stand': 'Stand',
      'idx.mode.left_kick': 'left_kick',
      'idx.mode.right_kick': 'right_kick',
      'idx.mode.on': 'On',
      'idx.mode.off': 'Off',
      'idx.hint.mode': 'Enable Roller mode to use the roller walking model during Walk / get up. Recovery uses the get-up model. Commands always cover all 15 IDs. A missing ID is shown here and does not block the others. Default pose and walk send position targets. Leaving this page or keeping it in the background zeros motion and preserves the selected control mode. Use Torque off or enter Calibration to unload. Pick runs only while walking upright: it plays xgoduck_pick for one 4 s cycle, then returns to walk. Roulade plays xgoduck_roulade for 1.9 s with twist and head at 0, and leg/neck smoothing at 0.15, then returns to walk and the previous smoothing. Sit uses fixed action scale 1.1 and enables seated holding; the button changes to Stand. Stand runs for 1 s, then returns to walking. Walk, Pick and Roulade are unavailable during sit/stand. A fall cancels sit/stand and uses normal get-up recovery. Tilt does not select get-up during Roulade\'s 1.9 s. left_kick and right_kick each play for 0.4 s with velocity and pose commands at zero, then return to walk. Kick settings are fixed at scale 0.8 and leg/head α 0. A fall cancels the kick and uses normal get-up recovery.',
      'idx.recovery.title': 'Walk and recovery',
      'idx.recovery.model': 'Active model',
      'idx.recovery.tilt': 'Tilt from (0, 0, -1)',
      'idx.recovery.timer': 'Condition timer',
      'idx.recovery.hint': 'Walk: tilt above 55° for 0.15 s selects the get-up model, holds action = 0 for 1 s, then runs the get-up action. Get-up: tilt below 15° for 1 s returns to walk. Twist and head commands are zero during get-up. Inference-only mode previews the switch without enabling torque. During pick the command is a phase encoding; the mouth opens to 30° and closes when φ ≥ 0.4.',
      'idx.motion.title': 'Motion',
      'idx.motion.warning': '<strong>Warning:</strong> Holding a toe-out stance for long periods heats up the roll motors. Take regular breaks to let them cool down.',
      'idx.motion.left': 'Forward / strafe',
      'idx.motion.left_roller': 'Forward / backward',
      'idx.motion.right': 'Yaw',
      'idx.motion.zero': 'Zero motion',
      'idx.motion.hint': 'Drag a pad; release returns it to zero. Each axis has a 15% center dead zone and motion smoothing. Keys: W/S forward, A/D strafe, Q/E yaw. Space turns torque off. <span id="motion-limits"></span> Ranges follow Roller mode; switching modes clears motion.',
      'idx.head.title': 'Head and mouth',
      'idx.head.hint': 'Head 1–4 are radians (±1). The mouth is motor 34, 0–30°, and is sent with the 50 ms command heartbeat. It is not part of the policy observation.',
      'idx.head.slider': 'head {n}',
      'idx.head.mouth': 'Mouth 34 (°)',
      'idx.action.title': 'Action settings',
      'idx.action.profile_walk': 'Walking / other models',
      'idx.action.profile_roller': 'Roller walking',
      'idx.action.scale': 'Action scale',
      'idx.action.leg': 'Leg α',
      'idx.action.head': 'Head α',
      'idx.action.hint': 'Adjust separate settings for roller walking and other models. Roller defaults: scale 0.7, leg α 0.4, head α 0.5. Other defaults: scale 1, leg α 0.35, head α 0.45. Kick uses fixed scale 0.8 and leg/head α 0, independently of these sliders. Sit/Stand uses fixed scale 1.1; its leg/head α follow the ordinary-model sliders. Scale changes action offsets from the home pose. Release a slider to apply; restarting the app restores each group\'s defaults.',
      'idx.imu.title': 'IMU filtering',
      'idx.imu.gyro': 'Angular velocity α',
      'idx.imu.tau': 'Gravity projection τ (s)',
      'idx.imu.hint': 'Higher angular velocity α gives more smoothing; higher gravity τ slows accelerometer correction. Changes apply immediately. Restarting the app restores defaults.',
      'idx.telemetry.title': 'IMU and servos',
      'idx.telemetry.target': 'Target',
      'idx.telemetry.waiting': 'Waiting for command echo…',
      'idx.msg.connecting': 'Connecting…',
      'idx.msg.mode_updated': 'Mode updated',
      'idx.msg.roller_on': 'Roller mode on · {model}',
      'idx.msg.roller_off': 'Roller mode off · {model}',
      'idx.msg.pick_started': 'Pick started · {model}',
      'idx.msg.roulade': 'Roulade · {model}',
      'idx.msg.sit_holding': 'Sit · holding',
      'idx.msg.stand_return': 'Stand · returning to walk after {seconds} s',
      'idx.msg.kick_return': '{side} · returning to walk after 0.4 s',
      'idx.msg.bridge': 'Bridge {mbps} Mbps · state age {age} ms',
      'idx.msg.request_failed': 'request failed',
      'idx.msg.status_failed': 'status read failed',
      'idx.status.enabled': 'enabled',
      'idx.status.torque_off': 'torque off',
      'idx.phase.walk': 'walk',
      'idx.phase.default_pose': 'get-up · action = 0',
      'idx.phase.getup': 'get-up',
      'idx.phase.pick': 'pick',
      'idx.phase.roulade': 'roulade',
      'idx.phase.sit': 'sit',
      'idx.phase.stand': 'stand',
      'idx.phase.idle': 'idle',
      'idx.timer.roulade': 'roulade {remaining} / {seconds} s',
      'idx.timer.kick': '{side} {remaining} s left',
      'idx.timer.sit': 'sit · holding',
      'idx.timer.stand': 'stand {remaining} s left',
      'idx.timer.pick': 'pick φ {phi} / 1.00',
      'idx.timer.hold': 'hold {remaining} s left',
      'idx.timer.upright': 'upright {elapsed} / 1.00 s',
      'idx.timer.fallen': 'fallen {elapsed} / 0.15 s',
      'idx.limits': 'Limits: vx -{back} to +{forward}, vy {strafe}, ωz ±{yaw}.',
      'idx.health': 'sent {sent} · MCU echo {echo} · command age {age} · gaps {gaps} · rejected {rejected}',

      'cal.title': 'Servo centering',
      'cal.entering': 'Entering calibration and unloading all servos…',
      'cal.procedure': 'Procedure',
      'cal.unload': 'Unload all',
      'cal.hint': 'Opening this page stops motion and unloads every servo. Support the robot, turn one joint to its <b>design zero</b> (joint angle 0°, not the standing pose), then press Set center &amp; hold for that joint. The firmware writes the current position as the servo internal center and holds only that servo at 2048; the others stay unloaded. Left and right legs must mirror. Exit when all joints are done; exiting releases every servo.',
      'cal.joints': 'Joints',
      'cal.waiting': 'Waiting for feedback',
      'cal.unloaded': 'Unloaded',
      'cal.centering': 'Centering…',
      'cal.holding': 'Holding 2048',
      'cal.set': 'Set center & hold',
      'cal.setting': 'Setting the center of ID {id}…',
      'cal.sent': 'Center command sent to ID {id}. It is holding position 2048.',
      'cal.ready': 'All servos are unloaded. Turn one joint to its design zero, then set its center.',
      'cal.closed': 'Calibration closed. All servos are unloaded.',
      'cal.active': 'Calibration active',
      'cal.inactive': 'Calibration closed',
      'cal.encoder': 'Encoder {pos}',
      'cal.no_feedback': 'No servo feedback',
      'cal.status_failed': 'status read failed',
      'cal.request_failed': 'request failed',

      'srv.title': 'Servo setup',
      'srv.id_warning': 'When changing an ID, connect only the servo you want to change. Duplicate IDs cannot be distinguished on the same bus.',
      'srv.initial': 'Scan for servos, then select an ID to read or change its settings. Do joint centering on the Calibration page.',
      'srv.scan': 'Scan all IDs',
      'srv.scan_stop': 'Stop scan',
      'srv.scan_status_init': 'Scan IDs 0–253 to find connected servos.',
      'srv.scan_progress_aria': 'Scan progress',
      'srv.scan_results_aria': 'Discovered servo IDs',
      'srv.scan_hint': 'Select a found ID to read or change that servo\'s settings. Stop scan keeps the results found so far. IDs 254 and 255 are reserved.',
      'srv.kp_kd': 'Set KP/KD',
      'srv.set_gains': 'Set KpKd',
      'srv.new_id': 'Change ID to',
      'srv.change_id': 'Change ID',
      'srv.read': 'Read',
      'srv.protection_title': 'All-servo voltage / overcurrent protection',
      'srv.enable': 'Enable',
      'srv.disable': 'Disable',
      'srv.not_applied': 'Not applied',
      'srv.protection_current_aria': 'Protection current in amperes',
      'srv.set_current': 'Set protection current',
      'srv.protection_hint': 'Enable / Disable changes only the protection switch. Set protection current changes only the current threshold. Both operations broadcast to every servo; no scan or ID selection is needed. Overheat protection stays enabled, and stored voltage limits are unchanged. Current resolution is 6.5 mA. Broadcast writes have no per-servo acknowledgement.',
      'srv.selected': 'Selected ID {id}',
      'srv.id_range': 'ID must be between 0 and 253',
      'srv.no_mcu': 'No MCU feedback. Servo setup is not ready.',
      'srv.stopping_scan': 'Stopping scan…',
      'srv.closed': 'Servo setup closed',
      'srv.changing_id': 'Changing ID {id} to {new_id}…',
      'srv.action_gains': 'Set KpKd',
      'srv.action_read': 'Read',
      'srv.readout': 'ID {id} · Pos {pos} · KP {kp} · KD {kd}',
      'srv.params_read': 'ID {id} · parameters read',
      'srv.id_changed_scan': 'ID changed. Scan again to select the updated servo.',
      'srv.verified': 'ID {id} → {new_id} · verified',
      'srv.kp_written': 'ID {id} · KP/KD written',
      'srv.preparing_scan': 'Preparing scan…',
      'srv.deploy': 'Deploy the updated project to enable servo setup.',
      'srv.scanning_torque_off': 'Scanning · torque off',
      'srv.scanning': 'Scanning ID {id} · {checked}/254 checked · {found} found',
      'srv.invalid_scan_reply': 'Invalid scan reply',
      'srv.scan_complete': 'Scan complete · 254 IDs checked · {found} found',
      'srv.select_found': 'Select a found ID to read or change its settings.',
      'srv.no_servos': 'No servos replied. Check servo power, bus connection and baud rate.',
      'srv.stopped': 'Stopped',
      'srv.scan_failed': 'Scan failed',
      'srv.partial': '{checked}/254 checked · {found} found (partial results)',
      'srv.scan_stopped_continue': 'Scan stopped. Select a found ID to continue.',
      'srv.no_mcu_scan_stopped': 'No MCU reply. Scan stopped.',
      'srv.protection_current_range': 'Enter a protection current between 0 and 425.9775 A.',
      'srv.broadcasting': 'Broadcasting protection settings to all servos…',
      'srv.invalid_broadcast': 'Invalid broadcast reply',
      'srv.current_sent': 'Broadcast sent · {current} A',
      'srv.protection_sent': 'Broadcast sent · voltage / overcurrent {state}',
      'srv.protection_broadcast': 'Protection broadcast sent. Individual servo writes are not verified.',
      'srv.request_failed': 'Request failed'
    }
  };

  function initialLang() {
    const q = new URLSearchParams(location.search).get('lang');
    if (q === 'zh' || q === 'en') {
      try { localStorage.setItem(KEY, q); } catch (e) {}
      return q;
    }
    try {
      const saved = localStorage.getItem(KEY);
      if (saved === 'zh' || saved === 'en') return saved;
    } catch (e) {}
    return (navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en';
  }

  let lang = initialLang();

  function t(key, vars) {
    const table = DICT[lang] || DICT.en;
    let text = table[key];
    if (text === undefined) text = DICT.en[key] !== undefined ? DICT.en[key] : key;
    if (vars) {
      text = text.replace(/\{(\w+)\}/g, (m, name) =>
        vars[name] !== undefined ? String(vars[name]) : m);
    }
    return text;
  }

  function applyI18n(root) {
    root = root || document;
    root.querySelectorAll('[data-i18n]').forEach(el => {
      el.textContent = t(el.getAttribute('data-i18n'));
    });
    root.querySelectorAll('[data-i18n-html]').forEach(el => {
      el.innerHTML = t(el.getAttribute('data-i18n-html'));
    });
    root.querySelectorAll('[data-i18n-aria]').forEach(el => {
      el.setAttribute('aria-label', t(el.getAttribute('data-i18n-aria')));
    });
    root.querySelectorAll('[data-i18n-title]').forEach(el => {
      el.setAttribute('title', t(el.getAttribute('data-i18n-title')));
    });
    document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en';
    const toggle = document.getElementById('lang-toggle');
    if (toggle) {
      toggle.textContent = lang === 'zh' ? 'EN' : '中文';
      toggle.setAttribute('aria-label', lang === 'zh' ? 'Switch to English' : '切换到中文');
    }
  }

  function setLang(next) {
    const changed = next !== lang;
    lang = next === 'zh' ? 'zh' : 'en';
    try { localStorage.setItem(KEY, lang); } catch (e) {}
    if (!changed) return;
    applyI18n();
    window.dispatchEvent(new CustomEvent('xgoduck:langchange', { detail: { lang } }));
  }

  document.addEventListener('DOMContentLoaded', () => {
    applyI18n();
    const toggle = document.getElementById('lang-toggle');
    if (toggle) toggle.onclick = () => setLang(lang === 'zh' ? 'en' : 'zh');
  });

  window.t = t;
  window.xgoduckDict = DICT;
  window.applyXgoduckI18n = applyI18n;
  window.xgoduckLang = () => lang;
  window.setXgoduckLang = setLang;
})();
