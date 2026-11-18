# XGO Duck AI 更新记录 / Changelog

## 0.3.6

### 简体中文

- 标定页可一次标定全部 15 个舵机的硬件中位，包括 30 号舵机。完成后标定 IMU，再检查默认姿势。
- 提供三种安装包，每个包都附有 SHA-256 校验文件。新板首装可用全量 ZIP，环境已准备好的板子可用预编译 `.ard`，在 App Lab 中编辑源码可用源码 ZIP。
- 安装验收增加了整机重启检查，用于确认默认 App 能自动启动。首次安装时可以先验证运动控制，稍后再绑定语音账号或连接 USB 音频设备。
- `master` 的普通步行基准角保持为左膝 −2°、左踝 +20°、右膝 +2°、右踝 −20°。已发布的 `v0.3.6` 安装包在这四个关节上依次使用 0°、+22°、0°、−22°。
- 仓库的中英文 README 新增了 XGO Duck 官方 Wiki 入口，可查阅装配、标定和故障排查指南。

### English

- The calibration page sets the hardware centers for all 15 servos in one operation, including servo 30. Follow this with IMU calibration and a check of the default pose.
- Three packages are available, each with a SHA-256 checksum. Use the full ZIP for a new board, the precompiled `.ard` for a board with its runtime installed, or the source ZIP to edit the app in App Lab.
- Installation checks now include rebooting the board to confirm that the default app starts automatically. On a first install, you can verify motion control before binding a voice account or connecting USB audio.
- The ordinary walking pose on `master` keeps the left knee at −2°, left ankle at +20°, right knee at +2° and right ankle at −20°. The published `v0.3.6` packages use 0°, +22°, 0° and −22° at those joints, respectively.
- Both repository READMEs now link to the official XGO Duck Wiki for assembly, calibration and troubleshooting guides.

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
