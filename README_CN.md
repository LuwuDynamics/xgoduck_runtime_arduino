# XGO Duck AI

[English](README.md) · 简体中文

在 Arduino UNO Q 上运行小鸭的语音聊天、运动控制、USB 音视频和 ToF 测距。日常操作使用浏览器，不需要给小鸭连接屏幕。

## 安装前准备

- Arduino UNO Q，使用官方 Debian 13 系统；先在 [Arduino App Lab](https://www.arduino.cc/en/uno-q) 中完成 Wi-Fi 和 Linux 账户设置，确认可以 `ssh arduino@<板子IP>`。
- 按[硬件装配指南](https://github.com/LuwuDynamics/xgoduck_hardware/blob/master/README_CN.md)完成载板、电源及舵机接线。运动前先完成舵机 ID 和中点校准。
- 聊天需要 USB 麦克风和扬声器，可通过有供电能力的 USB Hub 连接；摄像头和板载 Qwiic 接口上的 VL53L5CX ToF 为可选设备。使用 USB 外设时，鸭子需独立供电。
- 首次安装需要联网下载系统包、编译工具和 Python/容器依赖。

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
- **运动：**先按硬件指南完成舵机设置和校准，支撑好小鸭，再测试默认姿态和起立。桌面控制页在 `http://<板子IP>:9527/`；手柄和校准也可从驾驶舱设置进入。KP/KD 由用户自己调整，启动时不会统一覆盖。
- **ToF：**接入后显示 8×8 实时距离。默认前向避障在 15 cm 内减速、10 cm 停止；数据缺失或过期会暂停前进。不接 ToF 仍可聊天，关闭避障的方法见[避障说明](docs/forward-avoidance.md)。

## 更新和排查

- 更新前先扶稳小鸭并卸力；**Run 可能重新编译和刷写 STM32**。
- 同一只鸭子更新时保留原 App 的 `data/`，里面有绑定身份、音量、校准和手柄设置。不要复制另一只鸭子的身份或校准文件。App 目录改变或硬件支持脚本更新时，按首次初始化步骤重新准备。
- 首次 Run 报下载错误，先检查板子的互联网连接；没有声音，先在驾驶舱设置中测试扬声器和麦克风。
- 详细安装、迁移和工具链问题见[安装说明](docs/ai-chat.md)，装配与校准见[硬件指南](https://github.com/LuwuDynamics/xgoduck_hardware/blob/master/README_CN.md)。

需要自行打包时，在电脑上的仓库目录执行 `python3 tools/package-ai-app.py`，生成的 `dist/xgoduck_ai.zip` 可按相同步骤导入 App Lab。

本项目基于 Pollen Robotics 的 [Microduck](https://github.com/pollen-robotics/microduck)。[硬件](https://github.com/LuwuDynamics/xgoduck_hardware) · [训练代码](https://github.com/LuwuDynamics/xgoduck_rl)
