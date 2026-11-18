# XGO Duck AI 更新记录 / Changelog

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
