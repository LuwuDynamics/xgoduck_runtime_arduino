# XGO Duck AI 更新记录 / Changelog

## 0.3.9

站立 IMU 标定的陀螺仪单轴零偏上限从 20 °/s 调整为 100 °/s，以支持静止读数偏高的板子。标定先检查采样波动，再检查零偏；检测到移动时会重新采样。

Standing IMU calibration now accepts a per-axis gyroscope bias of up to 100 °/s (previously 20 °/s). Calibration checks sample stability before evaluating bias and retries when movement is detected.

## 0.3.8

- 站立标定的陀螺仪合成标准差和峰值上限分别调整为 3.0 °/s 和 10.0 °/s。标定成功后，日志和标定文件记录加速度计与陀螺仪的三轴标准差、各轴峰值和合成峰值。
- Standing calibration now accepts gyroscope noise up to 3.0 °/s combined standard deviation and 10.0 °/s combined peak deviation. Successful calibrations log and save per-axis standard deviations, per-axis peaks and the combined peak for both sensors.

## 0.3.7

### 简体中文

#### 轮滑、行走与起身

- 优化轮滑起步和停车。前后滑行时速度逐渐升高，松开摇杆后速度指令归零，轮滑模型继续维持平衡。系统根据腿部位置、关节速度和机身姿态判断是否已收稳，最多等待 2 秒。期间可重新推杆调整方向。驾驶舱与 9527 控制页使用相同的轮滑速度范围。
- 轮滑默认动作幅度为 0.6，腿部和头部动作滤波系数分别为 0.4 和 0.5。普通行走和轮滑沿用 0.3.6 的 ONNX 模型，本次更新了跌倒起身和坐站两个模型。
- 前后倾超过 55° 或左右侧倾超过 35°，持续 150 毫秒后开始起身。机身回到直立 15° 以内并保持 1 秒后恢复行走。从坐姿站起时沿用上一帧的动作历史，站起过程为 1.2 秒，腿部和头部滤波系数均为 0.2，动作倍率为 1.1。
- 左右脚踢球时长均为 0.4 秒，腿部和头部滤波系数均为 0.2，动作倍率为 1.0。

#### 舵机设置与标定

- 舵机设置新增可旋转的 3D 安装图。点选舵机 ID 会高亮对应关节，可在图下方启动「单舵机运动控制」，轻拖滑条，对照实机确认关节位置和运动方向。
- 修改 ID 时会把当前位置设为中位，开启电压保护和 2 A 过流保护，并写入 KP 5、KD 10。页面分别显示写入和回读结果，KP/KD 区域可直接查看、修改并重新读取保存值和运行值。
- 扫描通过读取 ID 寄存器确认舵机，单个地址最多尝试 10 次，减少串口回显和漏包造成的误判。停止扫描会保留已发现的 ID。若一个都没找到，可对照可放大的实拍图检查电池和接线。
- 统一标定后可单独校准 30 号颈部舵机。默认姿势与参考照片不符时，可从检查步骤进入舵机设置核对安装位置，也可展开「单舵机标定」，按实拍和中位图例逐个校准。在同一标定会话中，从 30 号切到 31 号时，已标好的关节可以继续保持。 [标定图解](calibration-guide.md)

#### 驾驶舱、语音与摄像头

- 驾驶舱新增「前向避障」开关，默认关闭，选择会保存到设备。开启后，前方障碍物过近时会限制前进。关闭后仍可查看 ToF 实时测距。 [前向避障](forward-avoidance.md)
- 驾驶舱顶部新增「控制调试中心」入口。控制、标定和舵机设置三页统一了导航和内容宽度。控制页说明了电压取自在线舵机供电读数的中间值，并提醒用户在用手机遥控前关闭电脑上的控制页，避免走走停停。
- 语音指令「打开补光灯」会先启动摄像头，等画面就绪再开灯。「关闭补光灯」可以单独执行，「关闭摄像头」会同时熄灯并结束预览。网页预览上限为每秒 15 帧，拍照保留完整 JPEG 帧。支持处理器调度的设备会在语音进程启动时降低其优先级，并尽量让唤醒词和语音处理使用独立于运动控制的 CPU 核心。 [摄像头说明](usb-camera.md)

#### 保护、诊断与安装

- 堵转保护结合关节角度、速度、持续告警时间和当前动作判断。轮滑中的短时过流只提示，持续热负载达到阈值时会尝试回到默认姿势，回位受阻时卸力。低压保护触发时，若姿态和反馈允许，会先尝试坐下再卸力。日志增加跌倒前的关节反馈、告警及卸力原因，方便排查突然倒地。
- Linux 与 STM32 使用 Arduino 可靠串口协议，握手后协商到 2 Mbaud。IMU 六轴读数连续 10 帧全零时，STM32 卸力并重新初始化传感器。连续 3 帧有效后恢复反馈，需重新发出起立或上力指令才能继续运动。
- 开机连上 Wi-Fi 后，UNO Q 点阵向右滚动显示完整 IPv4 地址，包括分隔点，30 秒后熄灭。运动前的硬件检查增加实拍图，标明 V1.1 及更早扩展板的绝缘位置和后部保护壳的固定方式。
- GitHub 和 ModelScope 均提供带版本号的 0.3.7 全量 ZIP 和预编译 ARD，可用配套 SHA-256 核对下载。首次安装或跨版本升级推荐全量包。升级或回退时，使用与目标 STM32 固件配套的 Router。 [安装与回退](deployment.md)

### English

#### Skating, walking and recovery

- Roller mode ramps up forward and reverse speed. Releasing the stick sets the velocity command to zero while the roller policy continues balancing. Duck uses leg angles, joint speeds and body attitude to check for a settled stance for up to 2 seconds. Moving the stick again resumes manual input. The cockpit and port 9527 controls use the same roller speed limits.
- Roller mode defaults to an action scale of 0.6, with smoothing coefficients of 0.4 for the legs and 0.5 for the head. The walk and roller ONNX models carry over from 0.3.6. This release updates the fall-recovery and sit/stand models.
- Duck starts getting up after pitch exceeds 55° or roll exceeds 35° for 150 ms. Walking resumes after Duck stays within 15° of upright for 1 second. Standing from sitting carries over the last seated action and runs for 1.2 seconds, with leg and head smoothing of 0.2 and an action scale of 1.1.
- Left and right kicks each last 0.4 seconds, with leg and head smoothing of 0.2 and an action scale of 1.0.

#### Servo setup and calibration

- Servo setup adds a rotatable 3D installation diagram. Select an ID to highlight its joint. Use the single-servo control below the diagram to move the joint slightly and check its location and direction on the robot.
- Changing an ID sets the current position as the center, enables voltage and 2 A overcurrent protection, and writes KP 5 and KD 10. The page reports which settings it could verify. The gain controls stay visible so you can edit the values and read back both saved and active gains.
- Scanning reads each servo’s ID register, with up to 10 attempts per address to handle missed replies and distinguish servos from serial echoes. Stopping a scan keeps the IDs already found. If the list is empty, a troubleshooting dialog includes an enlargeable battery and wiring photo.
- After calibrating all servos, you can center neck servo 30 separately. If the default pose differs from the reference photo, open Servo setup to verify installation or expand single-servo calibration for photos and center-position diagrams. Within the same session, servo 30 can keep holding its calibrated position while you center servo 31. [Calibration guide](calibration-guide.md)

#### Cockpit, voice and camera

- The cockpit adds a Forward avoidance switch. It defaults to off and remembers the selection across restarts. When enabled, a nearby obstacle limits forward motion. Live ToF distances remain available with the switch off. [Forward avoidance](forward-avoidance.md)
- The control and debugging center is accessible from the top of the cockpit. The control, calibration and servo setup pages share the same navigation and content width. The control page explains that voltage is the median of the reporting servos’ supply readings. It also reminds phone users to close the computer’s control page to avoid intermittent stops.
- Asking Duck to turn on the fill light starts the camera and waits for video before switching on the light. The light can be switched off independently. Asking Duck to close the camera turns off the light and ends the preview. The web preview is capped at 15 fps, while photos use the full JPEG frame. On supported systems the voice process starts at lower priority and is assigned to a subset of CPU cores to leave capacity for motion control. [Camera guide](usb-camera.md)

#### Protection, diagnostics and installation

- Stall detection considers joint position, speed, alarm duration and the current activity. Brief overcurrent during skating raises a warning. Sustained thermal load triggers an attempt to return to the default stance, with torque released if the joints cannot make progress. On low voltage, Duck attempts to sit before releasing torque when posture and feedback permit. Diagnostic logs include joint feedback leading up to a fall, servo alarms and the reason for torque release.
- Linux and STM32 use Arduino’s reliable serial protocol and negotiate 2 Mbaud after the handshake. After 10 consecutive all-zero IMU samples, STM32 releases torque and reinitializes the sensor. Three consecutive valid samples restore feedback. A fresh stand or arm command is required to resume motion.
- Once Wi-Fi connects at startup, the UNO Q LED matrix scrolls the complete IPv4 address, including the dots, to the right for 30 seconds. The pre-motion hardware check includes photos showing where to insulate expansion boards up to V1.1 and how to secure the rear cover.
- GitHub and ModelScope offer the versioned 0.3.7 full ZIP and precompiled ARD with matching SHA-256 files. Use the full ZIP for a first install or cross-version upgrade. Use the Router matched to the target STM32 firmware when upgrading or rolling back. [Deployment guide](deployment.md)

## 0.3.6

### 简体中文

#### 遥控与手柄

- 手机驾驶舱的横屏和竖屏布局都保留双摇杆。左杆控制前后和侧移，右杆转向。旋转手机会释放当前摇杆输入。驾驶页新增轮滑开关，小鸭在保持姿势模式下站稳时也能直接点「坐下」。
- 「视图」可选显示全部控件、只保留常用遥控或只看摄像头。小屏可单独打开 ToF 深度图和设备工具。支持补光的摄像头出图后，可在工具里开关补光灯并查看实际状态。
- 蓝牙手柄可单独绑定坐下、切换轮滑、左脚踢、右脚踢，以及七种鸭叫。「行走 / 起身」也能让坐着的小鸭站起来。已有按键映射会保留，手柄设置页随驾驶舱切换中英文。[手柄设置](gamepad.md)
- 驾驶舱和 9527 控制页在第一次发出遥控动作前提醒安装后部保护壳。确认后需要重新发出动作指令，两页在同一板子地址下共用确认记录。

#### 语音、联网与绑定

- 小鸭正在回答时，可以再次说「小鸭小鸭」或 “Hey Ducky” 打断。小鸭先停止本地播放并发出回应音，再收下一句话。唤醒与对话共用麦克风，首句缓存从回应音结束后开始，避免把唤醒词尾音或提示音传给语音服务。
- 连上 Wi-Fi 后，UNO Q 板载 LED 点阵显示 IP 地址的最后一段约一分钟。需要绑定时，小鸭可用中文或英文逐位播报完整地址三遍。配网页面也能正确显示中文 Wi-Fi 名称。

#### 标定与运动控制

- 标定页改为三个步骤。先按四张参考图摆位，在全部舵机在线、卸力后统一标定 15 个舵机（含 30 号颈部舵机），再标定 IMU，最后点「默认姿势」对照实拍检查。批量写入等待 STM32 确认，失败时只重试当前阶段。[标定图解](calibration-guide.md)
- 默认姿势检查在离开页面或切到后台时卸力，心跳中断后最多保持两秒。单舵机安装检查的「停止」可以打断仍在等待回复的角度调整，读回舵机扭矩已关闭后才显示停止成功。
- 运动控制允许单个舵机短暂漏回反馈，容差上限为 15 帧、150 毫秒。Bridge 可分别识别状态反馈中断和指令回执停滞。通信恢复时先确认 MCU 保留的目标，已经卸力的小鸭会保持卸力，移动需要重新发令。
- 驾驶舱分别显示实时电压和低电压运动保护状态。供电恢复后，电压读数恢复正常颜色，仍生效的运动锁定继续显示。

#### 安装与更新

- 发布包提供三种安装方式，均附 SHA-256 校验文件。全量 ZIP 为新板备齐运行镜像、系统依赖和上传工具。预编译 `.ard` 自带 STM32 固件和 Python 环境。App Lab 源码 ZIP 适合编辑源码并在板上编译。
- 预编译版启动前会校验 STM32 固件及其构建输入。固件缺失、损坏或输入有变更时，会尝试在板上重新构建。安装脚本也修复了原生音频库目录权限，普通 App 用户可以加载 `libopus`。
- 安装指南补充了已有安装的识别、同一台机器的标定与绑定数据迁移，以及整机重启后的自启动验收。首装时可先验收运动控制，语音待绑定或接上 USB 音频设备后再验收。中英文 README 都提供了官方 Wiki 入口。[部署指南](deployment.md)

### English

#### Remote control and gamepads

- Both portrait and landscape phone layouts keep the two joysticks on screen. The left stick drives and strafes, and the right stick turns. Rotating the phone releases the current input. The cockpit adds a Roller switch and lets Duck sit directly from Hold when upright.
- The View menu offers all controls, driving controls only, or a camera-only view. Small screens can open the ToF depth grid and device tools separately. Once a supported camera is streaming, the tools let you switch its light on or off and see the state reported by the camera.
- Gamepad buttons can now be assigned to Sit, Roller mode, left kick, right kick, and each of the seven duck calls. Walk / get up also stands Duck up from sitting. Existing bindings carry over, and the setup panel follows the cockpit language. [Gamepad setup](gamepad.md)
- The cockpit and port 9527 controls show a rear-cover reminder before the first driving action. After acknowledging it, issue the command again. Both pages share the acknowledgement when opened at the same board address.

#### Voice, Wi-Fi and binding

- Say “Hey Ducky” or “小鸭小鸭” again to interrupt a spoken reply. Duck stops local playback, plays its acknowledgement cue and listens for the next sentence. Wake detection and chat share one microphone. Buffering starts after the cue to keep the wake-word tail and cue out of the audio sent to the voice service.
- After Wi-Fi connects, the UNO Q LED matrix displays the last part of its IP address for about one minute. When binding is required, Duck can read the full address digit by digit three times in English or Chinese. Wi-Fi setup also displays Chinese network names correctly.

#### Calibration and motion control

- The calibration page guides you through three steps. Position Duck using four reference photos, calibrate all 15 unloaded servos together (including neck servo 30), then calibrate the IMU and check Default pose against a photo. Each servo write stage waits for STM32 confirmation. A failed stage is retried on its own. [Calibration guide](calibration-guide.md)
- The default-pose check releases torque when you leave or hide the page, or within two seconds if the heartbeat stops. In the single-servo installation check, Stop interrupts a pending angle request and reports success only after the servo confirms torque is off.
- Motion control tolerates brief gaps in one servo’s feedback, up to 15 frames and 150 ms. Bridge recovery distinguishes missing state feedback from stalled command acknowledgements. On reconnection it checks the MCU’s retained target. Duck stays unloaded if torque was released, and travel requires a fresh command.
- The cockpit reports live voltage separately from the low-voltage motion lock. When the supply recovers, the voltage readout returns to its normal color while any active motion lock stays visible.

#### Installation and updates

- All three packages have a matching SHA-256 checksum file. The full ZIP bundles the runtime image, system dependencies and upload tools for a new board. The precompiled `.ard` includes STM32 firmware and the Python environment. Use the App Lab source ZIP to edit the source and build on the board.
- The precompiled release checks its STM32 firmware and build inputs at startup. If the firmware is missing, damaged or out of date, it attempts a rebuild on the board. The installer also fixes native audio library directory permissions so the app user can load `libopus`.
- The installation guide covers identifying an existing installation, migrating calibration and binding data for the same Duck, and verifying autostart after a board reboot. On a first install, you can check motion control before binding a voice account or connecting USB audio. Both READMEs link to the official Wiki. [Deployment guide](deployment.md)

## 0.3.5

### 简体中文

- 9527 控制台新增自检。15 个舵机全部在线且卸力时核对运行 KP/KD，列出偏离 5 / 10 的舵机。IMU 在线时提示缺失或无效的标定记录。卸力后，过流保护已开启且读回的阈值大于 0、低于 1.6 A 时，列出对应舵机并建议设为 2 A。
- IMU 标定分别判断采样波动和陀螺仪零偏，避免将静止时的固定零偏误判为晃动。零偏过大时会单独提示。
- 舵机设置新增带关节图的单舵机安装检查，滑条可从起始位置调整 ±30°。连续 10 秒未调整目标角度、退出测试、断线或连续超过 10 帧没有位置反馈时自动卸力。
- 舵机设置可分别启用或禁用电压保护和过流保护，并逐台显示保护开关和已保存的过流阈值。电流输入框预填 2 A。
- 小鸭未保存服务区域时，联网后会根据账号绑定自动选区。国内有绑定时优先使用国内服务。已保存的区域或自定义服务优先。
- 驾驶舱入口改为「AI 语音交互绑定」。未选区且检测到摄像头时自动打开选区窗口。9527 顶部新增 GitHub 和 Wiki 链接，「走不稳？」帮助可完整收起。

### English

- The port 9527 control page checks live KP/KD when all 15 servos are online and unloaded, listing values that differ from 5 / 10. It flags missing or invalid calibration when the IMU is online. While unloaded, it recommends 2 A for servos with overcurrent protection enabled and a reported threshold above 0 A and below 1.6 A.
- IMU calibration checks motion and gyroscope bias separately so a steady bias can be calibrated while Duck is stationary. An excessive zero offset has its own message.
- Servo setup adds an illustrated single-joint test with ±30° travel from the starting position. It releases torque after 10 seconds without a new target angle, on exit or disconnect, or after more than 10 consecutive missing position feedback frames.
- Servo setup provides separate voltage and overcurrent protection switches and shows the saved protection settings and current threshold for each servo. The current input starts at 2 A.
- With no service region saved, Duck checks account bindings after connecting to Wi-Fi and gives priority to China when a binding exists there. A saved region or custom service takes precedence.
- The cockpit opens the AI binding region picker automatically when it detects a camera and the region is unset. The port 9527 header links to GitHub and the Wiki, and the walking-help panel collapses fully.

## 0.3.4

### 简体中文

- 按住手机遥控方向键时，小鸭可先起身，站稳后再移动。坐着也可以转头，语音可以直接下达动作指令。
- STM32 反馈短暂中断时，舵机保持上一组目标。反馈恢复且扭矩仍使能时，自动恢复原控制模式，移动需重新发令。
- 驾驶舱和 9527 控制台新增互跳入口和「走不稳？」帮助，可查看舵机安装、舵盘松动、标定和 KP/KD 检查步骤。标定页注明左右腿以机器人的视角为准。
- 驾驶舱新增音量入口，调节后自动保存，并对齐音频上传按钮。网页视频预览最高 15 帧/秒，拍照保留原始分辨率。
- 安装脚本使用两个编译任务。README 补充了 Cursor 代装验证记录。

### English

- Holding a direction on the phone remote can stand Duck up before it moves. Duck can turn its head while seated and accept voice motion commands directly.
- The servos hold their last targets during a brief STM32 feedback gap. When feedback returns and torque is still enabled, Duck resumes the previous control mode. Travel requires a fresh command.
- The cockpit and port 9527 control page link to each other and offer walking help for servo installation, loose servo horns, calibration, and KP/KD. The calibration page labels left and right from Duck's perspective.
- The cockpit adds a speaker volume shortcut, saves volume adjustments automatically, and aligns the audio upload button. Web video previews send up to 15 frames per second. Photos retain the original resolution.
- The installer uses two compile jobs. The README records a successful AI-assisted installation with Cursor.
