# XGO Duck AI

[English](README.md) · 简体中文

在 Arduino UNO Q 上运行小鸭的语音聊天、运动控制、USB 音视频和 ToF 测距。日常操作使用浏览器，不需要给小鸭连接屏幕。

## 安装前准备

- Arduino UNO Q，使用官方 Debian 13 系统；先在 [Arduino App Lab](https://www.arduino.cc/en/uno-q) 中完成 Wi-Fi 和 Linux 账户设置，确认可以 `ssh arduino@<板子IP>`。
- 按[硬件装配指南](https://github.com/LuwuDynamics/xgoduck_hardware/blob/master/README_CN.md)完成载板、电源及舵机接线。运动前先完成舵机 ID 和中点校准。
- 聊天需要 USB 麦克风和扬声器，可通过有供电能力的 USB Hub 连接；摄像头和板载 Qwiic 接口上的 VL53L5CX ToF 为可选设备。使用 USB 外设时，鸭子需独立供电。
- 首次安装需要联网下载系统包、编译工具和 Python/容器依赖。

官网资料：[XGO Duck 新手入门](https://www.xgorobot.com/duck.html) · [XGO Duck 开发者指南](https://www.xgorobot.com/duck-developer-guide/index.html)

装配前可下载[中英双语装配图解（PPTX，约 69 MB）](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/assembly/xgo-duck-assembly-zh-en-20261016.pptx)，按图核对舵机编号、安装朝向、走线和螺丝规格。

## 让 AI 帮你安装

硬件装好、UNO Q 已联网后，把下面整段复制给能执行终端命令的 AI 助手，并填入板子 IP。按步骤手动安装也可以，见下方第 1～3 节。

```text
请在我的 Arduino UNO Q 上安装 XGO Duck AI，并逐步执行和核对结果。
板子 IP：[填写]。服务区域：[国内 cn / 国际 global]。安装方式：[首次安装 / 更新这只鸭子]。
必要信息没填时先问我。SSH 用户为 arduino，密码由我在登录或 sudo 提示中输入。

先读取我提供的装配 PPTX，或从 GitHub 下载：
https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/assembly/xgo-duck-assembly-zh-en-20261016.pptx
用第 1 页核对舵机 ID，第 3～15 页核对底座、腿和脚掌的方向与走线，第 16～25 页核对上部组件与电路板装配。
请我对照实物确认有疑问的地方。无法读取插图时，请我提供对应页的截图。舵机中点标定按第 7 步的官方教程。

1. 通过 Wi-Fi SSH 登录板子的 Debian 13 ARM64 主机，确认能重新连上、有可用空间，并用
   arduino-app-cli app list 找出已有 App 和实际路径。记录 App CLI、Arduino CLI、Router
   及可查到的 App Lab 和 Python 基础镜像版本。实机对照记录是 Router 0.10.0、
   App CLI 0.13.0、Python 基础镜像 0.12.0。复用兼容的板载组件，遇到版本差异先核对兼容性。
2. 下载并用旁边的 .sha256 文件校验 App Lab 安装包：
   https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip
   板子下载失败时在电脑下载并校验，再用 SCP 传到板上。先检查 arduino-app-cli app import --help，
   支持时以 arduino 用户执行 arduino-app-cli app import <ZIP路径>，否则通过 App Lab 的
   Apps → Create new app → Import App → Import from computer 导入。重新列出 App，确认实际目录。
   如果是同一只鸭子更新，先请我扶稳并确认已卸力，再停止旧 App，备份原 App 和 data/，
   按包内 docs/ai-chat.md 的更新说明迁移，
   保留本机绑定身份、校准和配置。重复执行时复用已核对的安装，避免重复导入。
3. 进入实际 App 目录。以包内 sketch/sketch.yaml、provenance/toolchain.json 和
   python/requirements.txt 为准。本版锁定 Arduino Zephyr 1.0.0、arm-zephyr-eabi 1.0.1、
   Arduino_RouterBridge 0.4.3、Arduino_RPClite 0.3.1、MsgPack 0.4.2，其余库按清单解析。
   国内下载 Python 依赖时，可先运行下面这条命令配置本 App 的 uv.toml。保留用户已有的自定义源，
   如需换源先征得同意。这个镜像只用于 Python 包下载，服务区域单独设置。
   python3 tools/configure-ai-python.py --index-url https://pypi.tuna.tsinghua.edu.cn/simple/
4. 请我先扶稳小鸭并卸力，确认其他正在运行的 App 可以停。保持 Wi-Fi SSH 连接，停止所有 App，
   依次运行 bash tools/install-ai-support.sh 和 sudo bash tools/router-2m.sh。
   第一个脚本安装主机依赖、准备 USB Host、驾驶舱入口和开机自启，并预编译 MCU；
   第二个将共享 Router 的 Bridge 串口速率设为 2 Mbps（2000000），会重启 Router。
   前一步失败就先查日志并解决，再继续。保留 Router 脚本生成的备份。
5. 核对 Router 的实际 ExecStart 和默认 App，再以 arduino 用户在 App 目录运行
   arduino-app-cli app start "$PWD"。首次运行可能上传 STM32 程序，等构建和启动完成后，
   访问 http://<板子IP>/，读取板端 9527 的 /api/status 和 /api/voice/status。
   核对反馈是否更新、舵机是否保持卸力，以及聊天是在线、等待绑定还是报错。
6. 引导我在驾驶舱点「AI 绑定」、选定区域并打开对应平台。登录、蓝牙授权和账号绑定由我完成。
   绑定后核对语音在线，请我说「小鸭小鸭」验收。等我确认可以重启后验证开机自启，
   请我听联网成功和语音就绪两段鸭叫。汇报安装目录、实际组件版本、2 Mbps 配置和待完成的步骤。
7. 首次调试或排查走不稳时，请我支撑好鸭子，按下面顺序验收。已有有效标定的同一只鸭子可复用记录。
   a. 舵机标定：按官方指南二选一。有标定架就将鸭子固定在架上，无标定架就对照正视图和侧视图手动标定。
      https://www.xgorobot.com/duck-developer-guide/07-calibration-control.html
      确认 15 个舵机 ID 均在线，在 9527 打开 Calibration。我负责逐个摆到机械中点，
      点击对应的 Set center & hold，助手逐项核对。标定完退出页面，使用了标定架就先取下鸭子。
   b. 重力方向标定：将鸭子置于水平面，在 Default Pose 下保持身体端正静止约 30 秒。
      按 docs/imu-calibration.md 在实际 App 的 Python 环境运行 tools/calibrate-imu.py，
      将结果保存到这只鸭子的 data/imu-calibration.json。已有文件需先核对，重标前先备份。
   c. 陀螺仪零偏标定：同一次静止采样会同时计算零偏。请我支撑好后重启运动进程以加载标定，
      确认端正静止时 /api/status 的 gravity 接近 [0, 0, -1]，修正后的角速度接近 0。
   d. 检查 KP/KD：保持卸力，在 Servo setup 对 15 个舵机逐个读回。以 KP=5、KD=10 为基准，
      有差异时经我确认，用 Set KpKd 修改对应 ID，再读回验证。
   四项通过后，请我留出平坦空间并确认可以试走。先检查 Default Pose 和原地站稳，
   再短距离直走，建议从 0.10 m/s 开始，试走线速度不超过 0.15 m/s。抖动或失稳时立即停止，
   回查标定和增益，再继续试走。
```

## 1. 在 App Lab 导入聊天应用

1. **[下载 App Lab 安装包 `xgoduck_ai.zip`](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip)**，保留 ZIP 格式。
2. 在 App Lab 连接板子，选择 **Apps → Create new app → Import App → Import from computer**，导入这个 ZIP。
3. 导入后先停止所有正在运行的 Duck App，再完成下一节的一次性初始化。

<img src="docs/images/applab-import.jpg" alt="Arduino App Lab：Import an App，可导入本地 ZIP 文件" width="760">

这个安装包包含应用、STM32 sketch、模型、网页和安装脚本；不包含任何人的账号、Wi-Fi、密钥或校准数据。GitHub 的 **Code → Download ZIP** 是源码包，App Lab 请使用上面的专用安装包。

## 2. 首次初始化并启动

下面命令在 **UNO Q 主机终端**执行，可通过 App Lab 的板端终端或电脑 SSH 登录。假设导入目录为 `~/ArduinoApps/xgoduck_ai`；若 App Lab 使用了其他目录名，请改成实际路径，可用 `arduino-app-cli app list` 查看。

```sh
cd ~/ArduinoApps/xgoduck_ai

bash tools/install-ai-support.sh
sudo bash tools/router-2m.sh
```

初始化会请求 Linux 密码，准备硬件支持、USB Host、网页入口和开机自启。切换 USB Host 后，连接电脑的 USB/ADB 会断开，**请通过 Wi-Fi/SSH 操作**。首次 Run 后在网页中选择服务区域；以后也可在网页中手动切换。手机配网和对话语言不会自动替你选择区域。

回到 App Lab，通过 **Network** 连接板子，打开 **XGO Duck AI** 并点击 **Run**。首次会下载依赖、编译并上传 STM32 程序，需要等待几分钟。成功运行一次后，后续开机会自动启动。

后续通电会自动准备 USB Host，启动网页、聊天和运动服务，**无需每次打开 App Lab 或点击 Run**。完成配网、账号绑定并接好 USB 音频设备后，会依次听到两段启动鸭叫：

| 顺序 | 含义 | 示例音效 |
| --- | --- | --- |
| ① | 联网成功 | [试听第一段鸭叫](assets/startup-network.wav?raw=true) |
| ② | 语音服务已连接，聊天就绪 | [试听第二段鸭叫](assets/startup-voice.wav?raw=true) |

听到两段提示音后，喊 **「小鸭小鸭」** 唤醒，听到唤醒鸭叫后就可以说话。驾驶舱 **AI 绑定 → 开机自启与唤醒** 也可试听；示例在当前手机/电脑播放，实际启动音由小鸭扬声器播放。

## 3. 绑定账号，选择智能体

1. 电脑或手机与小鸭连接同一网络，打开 **`http://<板子IP>/`**，无需端口和页面后缀。

   <img src="docs/images/cockpit-cn.jpg" alt="驾驶舱首页示例，顶部为 AI 绑定入口" width="380">

2. 点击顶部 **AI 绑定**，点击 **国内版** 或 **国际版**即可保存选区并重连语音。窗口会显示当前服务区域和绑定状态，再打开对应的平台：

   | 设备选区 | 绑定与管理平台 |
   | --- | --- |
   | `cn` 国内版 | [agent.xgorobot.com/devices](https://agent.xgorobot.com/devices) |
   | `global` 国际版 | [agent.luwudynamics.ai](https://agent.luwudynamics.ai/) |

3. 登录平台，在「设备」中点击「添加设备」。按提示开启蓝牙，选择 `XGO-Duck` 开头的设备，完成配网和账号绑定，再选择要使用的智能体。
4. 回到驾驶舱打开 AI 绑定，可查看设备服务器、绑定及语音连接状态；显示语音在线后，说 **「小鸭小鸭」** 开始聊天。

<img src="docs/images/ai-binding-cn.jpg" alt="驾驶舱中的 AI 绑定窗口，提供国内版和国际版平台入口" width="760">

网页蓝牙需使用支持 Web Bluetooth 的 Chrome / Edge。iPhone / iPad 可用对应的小程序或 App 完成添加：

| 国内微信小程序 | 国际版 App |
| --- | --- |
| <img src="docs/images/qr_miniprogram.png" alt="国内微信小程序二维码" width="150"> | <img src="docs/images/qr_app_intl.png" alt="国际版 App 下载二维码" width="150"> |
| 用微信扫码 | [打开 App 下载页](https://agent.luwudynamics.ai/download) |

国内、国际账号和设备绑定相互独立。切换服务器会保存选区并重连语音，保持对话语言、音量和运动设置；首次切到另一侧需要在那里单独绑定。只是点击平台链接不会切换服务器。设备已绑定其他账号时，应先在原账号中解绑。

## 4. 日常使用

- **界面语言：**每次打开或刷新时跟随访问设备的浏览器系统语言，中文显示中文，其余显示英文；顶部切换按钮仅临时生效。
- **音频测试：**首页「实时音频」中，左侧「测试扬声器」播放约 3 秒合成鸭叫，播放时可点击停止；右侧「实时监听」收听小鸭麦克风。
- **聊天：**说「小鸭小鸭」唤醒；在平台中管理智能体。音量在驾驶舱「设置 → 扬声器」调整。
- **音视频：**驾驶舱可开启/关闭摄像头、控制补光灯、保存照片和实时监听麦克风。
- **走稳调试：**用标定架或手动标定舵机机械中点，任选一种即可。再标定[重力方向和陀螺仪静止零偏](docs/imu-calibration.md)，最后逐个核对舵机的 KP = 5、KD = 10。先在支撑下测试默认姿态和起立，站稳后以 0.10 m/s 短距离试走，试走线速度不超过 0.15 m/s。桌面控制和标定入口在 `http://<板子IP>:9527/`。 [官方舵机标定教程](https://www.xgorobot.com/duck-developer-guide/07-calibration-control.html)
- **ToF：**接入后显示 8×8 实时距离。默认前向避障在 15 cm 内减速、10 cm 停止；数据缺失或过期会暂停前进。不接 ToF 仍可聊天，关闭避障的方法见[避障说明](docs/forward-avoidance.md)。

## 更新和排查

- 更新前先扶稳小鸭并卸力；**Run 可能重新编译和刷写 STM32**。
- 同一只鸭子更新时保留原 App 的 `data/`，里面有绑定身份、音量、校准和手柄设置。不要复制另一只鸭子的身份或校准文件。App 目录改变或硬件支持脚本更新时，按首次初始化步骤重新准备。
- 首次 Run 报下载错误，先检查板子的互联网连接；没有声音，先在驾驶舱设置中测试扬声器和麦克风。
- 详细安装、迁移和工具链问题见[安装说明](docs/ai-chat.md)，装配与校准见[硬件指南](https://github.com/LuwuDynamics/xgoduck_hardware/blob/master/README_CN.md)。

需要自行打包时，在电脑上的仓库目录执行 `python3 tools/package-ai-app.py`，生成的 `dist/xgoduck_ai.zip` 可按相同步骤导入 App Lab。

本项目基于 Pollen Robotics 的 [Microduck](https://github.com/pollen-robotics/microduck)。[硬件](https://github.com/LuwuDynamics/xgoduck_hardware) · [训练代码](https://github.com/LuwuDynamics/xgoduck_rl)
