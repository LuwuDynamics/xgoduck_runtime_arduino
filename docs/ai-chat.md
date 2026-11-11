# XGO Duck AI App

> 首次配网并绑定后，小鸭可以自动选区。也可在驾驶舱的「AI 语音交互绑定」窗口点击国内版或国际版，保存选区并重连语音。

## 0.2.36 AEC 播放余量与打断停声

**实机验收尚未通过：** 12 dB 余量改善了单独播放的测试录音，但真实聊天仍把自身回答识别为用户说话。用户试听后认可声卡 100%、无额外数字放大的原声；当前调试基准为 `output_gain_db: 0`、`aec_output_headroom_db: 0`。不要把余量参数当成已完成的 AEC 修复。


新增 `aec_output_headroom_db`（0–18 dB，默认 0），只在实际 AEC 已启用、采集流已打开且正在收音时，给聊天输出预留数字幅度。它会让这段播放变小声；例如原 `output_gain_db: 6` 加上 `aec_output_headroom_db: 12`，有效数字增益为 -6 dB。不开启时保留旧音量；关闭 AEC、待机唤醒反馈和原网页媒体继续使用原音量。不要把它理解为可恢复已经削波的麦克风数据。

余量与原音量/软件增益合并后只限幅一次，消回声参考仍取实际送往扬声器的 PCM。某双麦硬件在同一段测试人声下，纯播放段能量降低从约 3 dB 改善到约 26 dB；这是特定摆放和素材的实测，不保证所有音量、距离或同时说话场景。硬件采集增益虽能读回变化，但实测仍饱和，未将其固化为默认值。可以在板上设置后重连聊天：

```bash
python3 tools/configure-ai.py --aec-output-headroom-db 12
```

板端区分已验证云端的打断停止包和普通完成包：打断立即清掉解码、PCM、声卡尾音及嘴型；普通结束仍等整段声音播完。收到服务端打断后不再重复发送 abort 或 listen:start，以免重置正在收集的插话。旧回答的等待任务不能切换或清空新回答。服务器、服务器 VAD/ASR、运动、ONNX、STM32 和 9527 网页不变。

首次升级请先停止旧 App，再安装并启动新版，让两个进程加载新配置结构；确认新版运行后，才执行上面的余量设置命令。不要在旧进程仍运行时先写入新字段，旧版会把不认识的配置视为无效并可能回写默认值。之后调整该参数只需聊天自动重连。

## 0.2.35 本地 AEC 参考连续性

本地 AEC 沿用现有 WebRTC 后端，由小程序的“自动打断” / `self.set_aec` 控制并持久保存；切换配置允许重连聊天。开启后，播放期间持续消回声并上传，服务器继续使用原有 VAD/ASR 与打断保护，不增加本地 VAD。

正常 TTS 开始和播放结束切回倾听时，保留连续的播放参考，供仍在麦克风采集缓存中的回声使用；显式打断与播放超时仍清空音频和参考、停止嘴型。`/api/voice/status` 的 `audio_playback` 增加 `aec_enabled`、`aec_backend`、`aec_frames` 和 `aec_reference_resets`，区分开启配置与实际处理帧数。音量、缓存时长、双麦选择和原网页不变。此修补针对参考时序；实际回声抑制与 USB 断连仍需分别验收。

## 0.2.34 短会话提示音

发送与回答完成提示音参考本地 RIG-Omni 的 `message_send.ogg` / `over.ogg`，均为 0.30 秒，额外衰减 6 dB，并加 5/8 ms 淡入/淡出。相较原素材，发送音 RMS 降低约 7.8 dB，结束音降低约 11.3 dB。输出为 16 kHz、单声道、16 位 PCM，来源与参数见 `provenance/audio-cues.json`。

只替换这两个本地 WAV；提示音继续走不驱动嘴型的本地队列。播放器在每次播放时读取文件，可热更新，无需重新烧录 STM32。

相较于 0.2.34 的音效，发送提示音的 PCM 振幅为 2 倍（+6.02 dB），回复结束音为 3 倍（+9.54 dB）。两者都保留 0.30 秒时长和淡入淡出。

> **当前控制层：** 运动部分同步 master [`b401d85`](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/commit/b401d85d20bb5bfd0237242a8a5ab6847fc962a2)，包括 8 个运动模型、配套网页/MCU 和 Bridge 重连处理。集成语音动作、手机遥控、手柄控制权、嘴型互斥、硬件中点迁移与 ToF 独立任务；普通行走前进/后退上限为 0.2/0.18 m/s。验证范围见[检查记录](ai-validation.md)。

以下 0.2.33 小节记录上一次稳定包集成；当前来源以 `provenance/motion.json` 为准，硬件中点迁移流程继续适用。

## 0.2.33 配套网页与硬件中点校准

- 稳定包的 8 个网页文件原样复制。`policy.py`、`proc.py`、`rl_core.py`、`recovery.py`、五个运动模型、`wire.py` 和配套 MCU 头文件也与来源逐字节一致。Controller 仅补入已有 AI/手柄控制权字段和命令来源门控，30 个其余方法与稳定包 AST 一致。`sketch.ino` 仅保留独立 ToF 的 include/start 两行，其余与稳定包逐字节一致。来源见 `provenance/motion.json`。
- 需要成套更新 App 并通过 Run 编译/上传配套 STM32；只替换网页或 Python 会造成协议不匹配。部署后强制刷新浏览器，确保新网页与后端匹配。普通 Walk 默认开启对应模型；Roller 默认关闭，仅用于对应的轮滑硬件。
- 现在中点保存在各舵机中，编码器中心固定为 2047；Calibration 页面进入后全部卸力，逐关节手动摆机械中点，再点 **Set center & hold**，最后退出。支持 Reset Mid、读取 KP/KD、全总线保护开关和独立保护电流设置。
- `data/zero_pos.json` 保留备份但不再加载。如果旧软件零位不是全部 2047（或文件损坏），App 会拒绝 Hold / Policy / 语音起身，提示先完成新版 15 关节校准。仅对收到成功 MCU 回执的关节记入 `data/hardware-centers.json`；全部完成且记录落盘后解除迁移限制。旧文件不会被重写，失败/部分校准、旧回执、记录写入失败均不能误报完成。
- 已按硬件中点校准且旧文件全为 2047 的机器保持兼容；不含旧文件的新安装遵循稳定包原有首次校准流程。不要为了跳过迁移把旧零位强行改成 2047，也不要运行下方历史版本的软件零位迁移命令。
- Roller 开关会取消旧语音/手机待执行动作并转交网页控制，避免旧动作在换模型后继续执行。有效语音动作保留 10 秒原截止时间与输入心跳续期；聊天和媒体接口保留，新增网页按稳定包布局，不添加聊天标签。

0.2.33 已完成 UNO Q 目标编译、配套 STM32 烧录与 App 启动，8 个网页文件通过板端 HTTP 哈希核对。实体机械校准和站立/行走尚未验收；部署中出现过 USB Hub 重枚举及命令通道停滞，不能将启动成功当作这两个问题已修复。详细结果见检查记录。

## 历史：0.2.31–0.2.32 软件零位迁移（不适用 0.2.33）

Python 控制器、`wire.py` 和 STM32 sketch 必须成套更新；仅覆盖 Python 或模型不能完成本次恢复。首次 Run 会编译并上传匹配的 STM32 程序。校准恢复为软件零位：`data/zero_pos.json` 保存每只机器独立的 15 个原始编码器中点。

- 已使用该分支的软件零位：保留当前机器的 `data/zero_pos.json`。
- 从 0.2.29/0.2.30 迁移，且已按该版本完成全部 15 个舵机的硬件中点校准：在 App 停止时，在本 App 根目录运行 `python3 tools/migrate-hardware-centers.py --from-hardware-centers`，生成 15 个均为 2047 的软件零位，使角度换算继续使用已校准的硬件中点。脚本只创建文件，不驱动舵机；若软件零位文件已存在则拒绝覆盖，先核对文件来源。
- 无法确认此前的中点方式：在恢复后的 Calibration 网页按旧流程逐关节重新校准。不要使用另一只鸭子的零位，也不要将未校准新机器的默认工厂值当作实测结果。

首次实测先支撑鸭子检查 Default pose，再验证 Walk / get up。代码恢复和软件测试不能证明实体已经站稳。



[English guide](ai-chat-en.md) · [中文 README](../README_CN.md) · [检查记录](ai-validation.md)

这是给用户自行购买的 **Arduino UNO Q** 安装的开源 App，需要先用电脑完成一次安装。它不是预装系统镜像。安装完成后，开机自动运行，手机通过蓝牙配 Wi-Fi、绑定账号；遥控通过云端 MCP。配套稳定版 9527 网页、9528 音视频接口、Audio/豆包页面、校准和舵机设置保留，没有新增聊天标签。

## 1. 先选择服务器和客户端

小鸭未保存选区时，会在 Wi-Fi 接通后通过两边的 `/ota/activate` 查询绑定。国内有记录就优先尝试国内，国内确认无记录且国际有记录时尝试国际。两边都确认无记录才提示绑定。结果不明确时显示重试。前一分钟约每 5 秒查询，之后约每 30 秒查询，遇到限流按 `Retry-After` 等待。常规 OTA 确认绑定后保存选区。已保存的区域或自定义服务优先。

| 板端配置 | 唤醒词 | 服务器 | 手机客户端 |
| --- | --- | --- | --- |
| `cloud_region: cn` | 小鸭小鸭 | `https://xl-api.xgorobot.com/xiaolu/ota/` | 国内微信小程序 |
| `cloud_region: global` | Hey Ducky | `https://xl-api.luwudynamics.ai/xiaolu/ota/` | 国际版 Android / iOS App |

| 国内微信小程序：微信扫一扫或识别小程序码 | 国际版 App：扫码下载 |
| --- | --- |
| <img src="images/qr_miniprogram.png" width="180" alt="国内微信小程序码"> | <img src="images/qr_app_intl.png" width="180" alt="国际版 App 下载二维码"> |
| 对应 `cn` | 对应 `global`；[直接打开下载页面](https://agent.luwudynamics.ai/download) |

二维码复用 Luwu-OS AI Chat Pro 的现有资源，来源和 SHA256 见 [onboarding-assets.json](../provenance/onboarding-assets.json)。App 码解码地址是上面的下载页面；小程序码需要用微信识别。

两边的账号、设备绑定和智能体配置分别属于各自服务器。请选择与日常使用客户端一致的一侧。中文对话可以用国际服务器，英文对话也可以用国内服务器；`language` 与 `cloud_region` 独立。MCP 遥控需要发布包含 `XGO-Duck / Duck.RemoteMove` 适配的客户端版本，二维码入口本身不代表本地客户端改动已经上线。

## 2. 用 USB 完成 UNO Q 初始设置

1. 安装 [Arduino App Lab](https://www.arduino.cc/en/uno-q)，用 USB-C **数据线**连接裸板和电脑，按 [Arduino UNO Q 用户手册](https://docs.arduino.cc/tutorials/uno-q/user-manual/)完成板子命名、Wi-Fi 和 Linux 账户密码设置。首次安装需要板子能联网下载系统包、Arduino 依赖、容器和 Python 依赖。
2. 记录板子局域网 IP，在电脑终端验证 `ssh arduino@<板子IP>`。密码使用你自己在 App Lab 中设置的密码；本项目没有统一出厂密码。App Lab 的 Network 模式也应能连接。
3. 准备能供电的 USB-C Host Hub，后续接 USB 麦克风、USB 扬声器和可选摄像头。**安装时 USB 探测可能中断电脑的 USB/ADB 连接**，请通过 Wi-Fi/SSH 安装。无需显示器。
4. 装配、供电、舵机 ID 和校准按 [硬件指南](https://github.com/LuwuDynamics/xgoduck_hardware/blob/master/README_CN.md)及主 README 完成。首次软件检查先保持舵机扭矩关闭。

## 3. 获取 App，并保留旧机器人的数据

推荐在电脑打包，通过 App Lab **导入为新 App**：

```sh
git clone --branch ai-chat-audio-video-tof https://github.com/LuwuDynamics/xgoduck_runtime_arduino.git
cd xgoduck_runtime_arduino
python3 tools/package-ai-app.py
```

导入生成的 `dist/xgoduck_ai.zip`，使用新目录名 `xgoduck_ai`。以下命令假定板端目录为 `/home/arduino/ArduinoApps/xgoduck_ai`，若 App Lab 使用了其他名字，以实际目录为准。

也可通过 SSH 在板子上直接拉取这个分支，App Lab 会从 ArduinoApps 目录发现应用：

```sh
mkdir -p ~/ArduinoApps
git clone --branch ai-chat-audio-video-tof https://github.com/LuwuDynamics/xgoduck_runtime_arduino.git ~/ArduinoApps/xgoduck_ai
cd ~/ArduinoApps/xgoduck_ai
```

停止已有 Duck App，避免共用 Bridge、舵机、9527 和 9528。旧 App 可以保留。要沿用这只鸭子的校准和手柄映射，在两个 App 都停止时，按需保留同一只鸭子的 `data/gamepad-bindings.json`；若要带上历史 `data/zero_pos.json`，本版会据此要求完成硬件中点迁移。已经使用本版校准的同一只鸭子可保留其 `data/hardware-centers.json` 及对应旧零位文件。不要复制其他机器人的校准或绑定身份。ZIP 排除了所有运行数据，不会自动迁移。

## 4. 安装硬件支持

在 UNO Q 的 **Debian 13 ARM64 主机终端**中，以 `arduino` 用户运行。首次安装想自动选区时，直接运行安装脚本。想提前选区时，先取消注释对应的一行。

```sh
cd ~/ArduinoApps/xgoduck_ai

# 国内微信小程序
# python3 tools/configure-ai.py --region cn --language cn

# 或国际版 App
# python3 tools/configure-ai.py --region global --language en

python3 tools/configure-ai.py --show
bash tools/install-ai-support.sh
```

`configure-ai.py` 原子保存 `data/voice_config.json`，保留音量、声卡、遥控权限等已有设置。显式选择区域会清除高级自定义 `ota_url` 覆盖。配置损坏时脚本会报错，要求先修复文件，不会覆盖它。

安装脚本会请求 sudo，先以 `arduino` 身份自动准备 MCU 编译环境，再安装音视频、BlueZ、NetworkManager 和必要的系统 Python 依赖；从当前 Debian 13 主机准备 `data/native/`，供 App 容器使用。硬件辅助代码安装到 root 所有的 `/usr/local/lib/xgoduck-ai-support/`，服务名为 `xgoduck-ai-hardware.service`，通过本 App 的 `data/gamepad.sock` 通信。它负责 USB 模式、原手柄功能和 BluFi 配网，不开放蓝牙运动指令或新的 TCP 服务。

脚本还会：

- 为当前板子创建并保留稳定设备 ID，优先使用永久 WLAN MAC；没有可用永久 MAC 时生成一次随机本地 ID。身份保存在 `data/voice-state/device-id`，不使用容器 MAC 或扫描时随机 MAC。
- 通过官方 `arduino-app-cli properties set default <App目录>` 设置开机默认 App，并启用 `arduino-app-cli.service`；启动顺序在硬件辅助服务之后。
- 关闭旧 `duck-device-test.service` 的用户级自启，避免抢占 9528 和 USB 音频。有旧豆包配置时仅首次复制一份，保留原文件。
- 安装并启用 nginx 网页入口，打开 `http://<板子IP>/` 即进入移动驾驶页，无需端口号和页面后缀。已有安装可单独运行 `sudo bash tools/install-mobile-web.sh`；该步骤可在 App 运行时执行。
- 停用旧手柄和 USB 角色服务的自启，包括此前的 `xgoduck-ai-usb-host.service`。新服务接管后，旧 unit 文件仍可用于回退。

脚本可重复执行；App 目录改变或硬件辅助代码升级后需重跑。**先停止所有 App，通过 Wi-Fi/SSH 执行。** 安装器运行并启用 `xgoduck-ai-usb-startup.service`，需要时重启共享 router，App 启动和 MCU 上传由后续的 Run 完成。

## 5. 设置 Bridge 速率，首次运行一次

沿用原工程的 STM32 sketch、2 Mbps Bridge、15 舵机和 IMU 接线：

```sh
sudo bash tools/router-2m.sh
arduino-app-cli app start "$PWD"
```

也可准备后在 App Lab 通过 Network 连接点击 **Run**。上一步的安装脚本已调用 `tools/prepare-ai-sketch.py`：解析 `sketch/sketch.yaml` 的固定依赖、修补 Bridge/RPClite 缓存、仅编译并检查 ELF，无需先 Run 失败一次再 Stop 修缓存。若只需重建 MCU 缓存，以 `arduino` 用户运行 `python3 tools/prepare-ai-sketch.py`。首次准备期间保持扭矩关闭，App 保持停止。库缓存清空后需重新准备；已有正确环境不必每次重启 router。

**Run 会构建并启动整个 App。** 0.3.5 的单舵机安装检查需要更新 STM32 sketch，请在舵机卸力后运行。换用其他 Bridge 速率的 App 前，按技术参考调整全局的 2 Mbps Router 设置。 [技术参考](technical_reference.md#running)

USB 探测在 **App 容器创建之前** 完成。开机先开 Host，最多等待 5 秒识别 USB 摄像头或音频接口。检测到音视频设备就保留 Host，否则切回 Device，包括只接了空 Hub 的情况。切换角色前，开机服务暂停 USB 串口监视器、代理及 Arduino router。确定角色后恢复原本运行的 router。当前 UNO Q 内核在 USB/FunctionFS 切换期间可能扰动 UART，重启 router 又会更换容器绑定的 socket、重置 MCU。App 入口只检查当前 USB 角色。驾驶舱手动切换后，单独重启 App 会沿用所选模式，下次整机开机重新探测。

切回 Device 后恢复原本启用或运行的 ADB 和 USB 串口监视器，不改变其 enable/mask 设置。只写 USB mode 而保留 ADB 运行，会让 ADB 在 Host 模式下反复重启。不要同时运行旧的 Host 启动脚本，也不要在运动中切换 USB 模式；本次修补隔离 App 启动时的切换，不替换系统内核。安装支持只补装缺少的依赖，不主动升级已经安装的系统包。

第一次保持联网，等待依赖安装和两个业务进程进入运行状态。安装了默认 App 后，后续开机不需要再在电脑点击 Run。先验证一次正常重启；再验证 Wi-Fi 不可用时仍能被手机发现。系统重刷、删除依赖缓存或删除 App 后，需要重新完成相应准备。

### 工具链与含工具链安装包

工具链是在 UNO Q 的 Linux 上把 STM32 C/C++ 程序编译成固件的编译器、链接器等构建工具，不是聊天或运动进程。出厂系统可能带有 `arm-zephyr-eabi 0.16.8`；本工程固定的 Arduino Zephyr core `1.0.0` 需要 `arm-zephyr-eabi 1.0.1`。两者版本号体系也不同于 App 版本。安装脚本负责匹配，保留原有旧版本，不会拿 `0.16.8` 冒充所需版本。

默认 App ZIP 不带工具链，首次由 Arduino CLI 下载并缓存。可选的含工具链版把官方 **Linux ARM64** 原始压缩包放入 `tools/offline/`，安装脚本自动校验大小和 SHA256 后放入 Arduino 下载缓存，再由 Arduino CLI 安装。压缩包约 97.3 MB，解压约 774 MB；含工具链的 App ZIP 约 107 MB。这里只省去该工具链的下载，其他 Arduino 库、系统包、容器与 Python 依赖仍需联网。已有正确版本时复用本机缓存，后续更新 App 可用普通小包。

在电脑上制作含工具链版（电脑是 Mac 也必须下载 **linux-aarch64**，供板子使用）：

```sh
curl -fL --retry 3 -o /tmp/xgoduck-toolchain-1.0.1.tar.xz \
  https://github.com/zephyrproject-rtos/sdk-ng/releases/download/v1.0.1/toolchain_gnu_linux-aarch64_arm-zephyr-eabi.tar.xz
python3 tools/package-ai-app.py \
  --toolchain-archive /tmp/xgoduck-toolchain-1.0.1.tar.xz \
  --output dist/xgoduck_ai_with_toolchain.zip
```

来源、版本和校验值见 [`provenance/toolchain.json`](../provenance/toolchain.json)。打包器会拒绝校验不符的文件；原压缩包和其中 `share/licenses` 完整保留，大文件不加入 Git 源码。

若普通包首次安装遇到 GitHub `unexpected EOF`，先恢复网络再重跑安装脚本；也可用电脑下载上面的官方文件，通过 SCP 传到板子，并在 App 目录执行：

```sh
python3 tools/prepare-ai-sketch.py --toolchain-archive /tmp/xgoduck-toolchain-1.0.1.tar.xz
bash tools/install-ai-support.sh
```

这个准备脚本没有上传、烧写或启动 App 的步骤。`/bin/arm-zephyr-eabi-g++: no such file or directory` 通常是前面工具下载失败的后续错误，应先修复前面的依赖下载，再编译。

### Python 依赖下载失败

安装支持时会在当前 App 根目录生成本机 `uv.toml`：`cn` 默认使用清华 PyPI 镜像，`global` 默认使用官方 PyPI。只影响 Python 包下载，不改变聊天服务器、账号绑定或区域。已有手写配置和显式指定的镜像会保留；该文件不进入分发 ZIP。

如果 Run 日志先出现 `Failed to fetch .../simple/...` 或下载超时，随后才提示 `No module named onnxruntime`，应先修复依赖下载。在板端 App 目录执行 `python3 tools/configure-ai-python.py` 后重新 Run。也可用 `--index-url https://<可访问的镜像>/simple/` 显式指定 HTTPS 镜像。首次成功运行仍需要下载容器和其他依赖；含工具链 ZIP 不包含 Python 虚拟环境。

## 6. 手机发现、配网、绑定

1. 开机后，未联网、未绑定或云连接检查失败时，板子广播 **`XGO-DuckABCD`** 这样的名字；末四位来自稳定设备 ID。麦克风、扬声器和屏幕都不是广播的前提。使用第 1 步对应的小程序或 App 登录账号，进入“添加设备”，授予蓝牙及系统要求的权限，搜索这只鸭子。
2. 连接后扫描 Wi-Fi，选择网络并提交密码。协议沿用生态现有 BluFi（FFFF / FF01 / FF02），发送的是 Wi-Fi 参数，不修改服务器。当前支持开放网络与 WPA/WPA2 Personal 密码；企业网络和需要网页登录的热点需另行在 Linux 主机配置。
3. Wi-Fi 成功后，板子发送同一个稳定设备 ID；客户端把它绑定到**当前客户端所在服务器**的账号，并选择智能体。之后板端检查所选服务器的绑定状态，取得 MQTT 凭据并启动聊天/MCP。
4. 已联网且绑定检查成功后结束配网广播；已经连接的配网会话先完成。后续 Wi-Fi 失联会重新广播以便更换网络。云服务暂时不可达时保留同一区域已知绑定并重试，不把网络错误当作“已解绑”。
5. USB 麦克风或扬声器缺失时，云端 MCP 和配网仍可运行，语音状态显示 `waiting_audio`。设备接入后自动重新枚举。语音就绪后，按驾驶舱「AI 语音交互绑定」里显示的唤醒词开始聊天。

如果选错客户端，Wi-Fi 仍可能配置成功，但板子检查的另一套服务器仍是未绑定。换用正确客户端完成绑定，或按下一节在板端明确换区；手机不会替你切区。

## 7. 配置、换区和升级

常用配置文件为 `data/voice_config.json`：

```json
{
  "enabled": true,
  "cloud_region": "cn",
  "language": "cn",
  "volume": 60,
  "enable_wakeword": true,
  "enable_aec": false,
  "enable_camera": true,
  "remote_motion_enabled": false,
  "input_device": null,
  "input_auto_gain": null,
  "input_gain_db": 0,
  "output_gain_db": 0,
  "tts_mouth_enabled": true,
  "output_device": null,
  "camera_device": null,
  "ota_url": ""
}
```

缺失字段使用默认值，服务器需要明确选择。声卡使用枚举的 `usb:VID:PID:serial` 或 ALSA card ID，摄像头使用 `/dev/v4l/by-id/` 路径。板端显式填写 HTTPS `ota_url` 可接入自建服务器。自建服务器仍按对话语言选择唤醒词，中文用「小鸭小鸭」，英文用「Hi Luka」。

需要换区时，在板端停止 App 后修改并重新运行：

```sh
cd ~/ArduinoApps/xgoduck_ai
arduino-app-cli app stop "$PWD"
python3 tools/configure-ai.py --region global --language en
arduino-app-cli app start "$PWD"
```

换回国内用 `--region cn`。然后在目标区域客户端添加/绑定并配置智能体。旧区域的账号绑定不会自动删除；设备 ID、校准、手柄映射和音量保留。绑定缓存按 OTA 地址和设备 ID 校验，目标区域检查成功前不会使用旧区域凭据。语言单独改变也不会换区。

升级前停止 App、备份整个 `data/`，然后更新代码/模型；Git 安装可在这个分支拉取，ZIP 安装应只更新程序文件，保留 `data/`、已有依赖和本机设置。辅助服务代码更新后重跑安装脚本；随后 Run，并检查默认 App 路径。ZIP 不含身份、Wi-Fi 密码、云凭据、豆包密钥及校准数据。

云绑定缓存、设备 ID、客户端 ID、AEC 和豆包设置位于权限 0700 的 `data/voice-state/`，私有文件权限 0600。不要把已绑定的 `data/` 作为给其他板子安装的模板。

OTA 上报 MAC 统一为小写，以匹配管理端的在线状态和 MCP 查询；本地持久身份与 BluFi 绑定 MAC 不改变。由 OTA 返回重新签名的 MQTT 凭据，不能直接改写其 client ID。升级前版本若出现“已绑定、MQTT 已连接但小程序离线”，更新到 `0.2.3` 后重新启动 App 获取一致格式的凭据，无需解绑。

### 切回保留的旧 Duck App

先修改下面的旧目录变量。停止新 App、还原默认 App 并移除本安装器专属的启动依赖；两个 App 的文件和 `data/` 都保留：

```sh
duck_previous_app="/home/arduino/ArduinoApps/你的旧Duck目录"
cd ~/ArduinoApps/xgoduck_ai
arduino-app-cli app stop "$PWD"
arduino-app-cli properties set default "$duck_previous_app"
sudo systemctl disable --now xgoduck-ai-hardware.service xgoduck-ai-usb-startup.service
sudo rm -f /etc/systemd/system/arduino-app-cli.service.d/xgoduck-ai.conf
sudo systemctl daemon-reload
```

按旧安装实际使用过的服务，恢复 `sudo systemctl enable --now duck-gamepad.service` 和 `systemctl --user enable --now duck-device-test.service`。如果以前使用开机 USB Device 默认服务，恢复 `sudo systemctl enable duck-usb-device.service`，正常重启一次后生效。然后用 App Lab 或 `arduino-app-cli app start "$duck_previous_app"` 启动旧 App。若原来没有默认 App，可用 `arduino-app-cli properties set default none` 恢复为不自动启动。旧 Duck 同样使用 2 Mbps Bridge，无需反复改变 router；切其他速率应用时再按技术参考处理。

如需移除新增的 80 端口入口，删除 `/etc/nginx/sites-enabled/xgoduck-mobile`，运行 `sudo nginx -t && sudo systemctl reload nginx`。原站点配置备份在 `/var/backups/xgoduck-mobile-web/`。只有确定 nginx 没有其他用途时，才停用其服务。

## 日常网页、聊天和遥控

两个常驻业务进程：

| 进程 | 工作 |
| --- | --- |
| `python/main.py` | 原 50 Hz ONNX、Bridge、STM32 状态和运动控制、9527 网页；监督聊天子进程 |
| `python/voice_process.py` | 无屏 AI Chat Pro、USB 音频/摄像头、唤醒词、音量、绑定检查、MCP；线程内承接原 9528 音视频接口 |

系统另有前述 root 硬件辅助服务，采集时还会按需调用 `arecord/aplay/v4l2-ctl`。业务进程通过仅容器回环地址的 9530/9531 通信，每次 App 启动使用临时令牌。聊天不直接打开 Bridge/UART。聊天进程故障会撤回动作并重启，运动主进程退出时清理聊天及录音子进程。

网页仍在 `http://<板子IP>:9527/`，原诊断接口在 9528。网页录音、监听、播放和对讲使用音频时先释放聊天声卡，结束后恢复聊天；摄像头共享，原页面结构不变。

语音可以通过 `self.audio_speaker.set_volume` 设置 0–100% 音量，与网页共用持久值。AEC 默认关闭，可用 `self.set_aec` 开启，实际效果需 USB 音频验收。

国内版使用「小鸭小鸭」（`python/models/xiaoya.onnx`），国际版使用「Hey Ducky」（`python/models/hey_ducky.onnx`）。切换预设区域时，语音服务重连并加载对应模型。两个模型都用归一化的 16 kHz 单声道音频，检测阈值为 **0.50**，窗口为 2 秒，步长为 80 ms，冷却时间为 2 秒。只有完全相同的 mel 窗口才会复用 embedding，缓存最多 32 项。[小鸭小鸭模型和固定唤醒音](../provenance/xiaoya-wakeword.json) · [Hey Ducky 模型](../provenance/hey-ducky-wakeword.json)

待机识别到唤醒词后，扬声器播放固定鸭叫（约 0.92 秒），同时建立云端对话。提示音共用上述音量设置，播放时嘴部保持原姿态。鸭子会缓存首句，包含检测到唤醒前最多 360 ms 的音频，等语音通道就绪后按顺序补传。

**双麦采集（0.2.28）。** 宿主机硬件辅助服务读取实际 USB 采集通道数，避免把容器中的虚拟声卡通道数当成硬件能力。单麦仍用一路；双麦保留两路采集，默认 `"input_channel_mode": "auto"`，稳定选择一路送入唤醒、AEC 和 Opus。另一通道能量持续 0.5 秒高出约 6 dB 才切换，避免左右频繁跳动；这是通道选择，不是波束成形。可显式选择 `left`、`right` 或 `average`，但未确认两路极性前不建议平均，以免抵消语音。

对 USB ID `0c45:636b` 且声明支持 48 kHz 的麦克风，默认以 48 kHz 采集，由 ALSA 转换为模型需要的 16 kHz。其他设备保留自动采样率；`input_sample_rate` 可显式设为 `16000`、`48000` 或 `null`（自动）。唤醒和聊天请求 320 ms 输入缓冲；唤醒回调只复制数据和统计，工作线程丢弃超过 500 ms 的积压块，并在丢块/溢出后重新预热，避免拼接不连续语音。`/api/voice/status` 的 `microphone` 报告通道电平/选择，`wake_capture` 与 `audio_playback` 分别报告唤醒和聊天采集计数。升级旧安装时需同步更新硬件辅助服务；原网页和 STM32 无需改动。输入缓冲不能修复整个 USB Hub 掉线，遇到这种情况仍需结合内核日志排查。

USB 麦克风声音太弱时，可在 `data/voice_config.json` 设置 `"input_auto_gain": true` 后重启 App，启用声卡提供的自动增益。`false` 关闭，默认 `null` 保留设备自身设置；不支持该控制的 USB 麦克风继续正常使用。设置应用于聊天/唤醒及原网页录音，音量滑块仍只控制扬声器。

硬件采集音量已到上限后，可设置 `"input_gain_db": 6`，额外放大唤醒与聊天的采集幅度约 2 倍。范围为 0–12 dB，默认 0（不额外放大）；设置持久保存，运行中的聊天自动重新打开音频会话。聊天在 AEC 后、Opus 编码前放大，唤醒在模型推理前放大；两条路径各处理一次，超出 PCM 范围的样本饱和限幅，避免整数溢出。软件增益也会放大底噪，近距离大声说话若失真，应降低增益。原网页录音和扬声器音量不使用此软件增益。当前值可从 `/api/voice/status` 的 `input_gain_db` 读取。

扬声器硬件音量达到 100% 后，可设置 `"output_gain_db": 6`，把聊天、唤醒提示音和原网页播放的数字幅度额外放大约 2 倍。范围 0–12 dB，默认 0；与麦克风增益独立，运行中的聊天自动重新打开会话并应用。0–100 音量控制仍有效，0 始终数字静音；硬件调音量成功时不重复衰减，失败时用软件音量。放大后饱和限幅，AEC 参考使用实际输出样本；原本已接近满幅的声音会削波，应按听感降低增益。实际声压提升受扬声器和功放限制。状态接口同时报告 `output_gain_db`。

聊天播放使用 **1000 ms 网络音频内容预缓冲、请求 500 ms 声卡缓冲**。按当前 60 ms/帧，网络预缓冲实际攒 17 帧即 1020 ms；不是固定等待一秒，超短回答在 TTS 结束时立即放行已有帧。声卡实际接受的时长由驱动决定，可从 `/api/voice/status` 的 `audio_playback.output_latency_ms` 读取。更大的缓冲会增加播放延迟。

播放结束会等待最后提交给声卡的样本实际排空，再进入下一轮；打断会清空软件队列并丢弃尚未播放的声卡缓冲，迟到的解码/PLC/提示音不能恢复已取消的内容。实时音频回调不再直接打印日志。`audio_playback` 分别报告 `prebuffer_ms`、声卡缓冲、PCM/Opus 水位、声卡欠载 `output_underflows`、输入溢出和 PCM 断粮 `pcm_starvations`；声卡欠载不能仅凭网络缓存量判断。原网页音频播放接口与页面保持不变。

**当前说话口型算法（2026-11-05）。** Linux 工作线程每 10 ms 计算 25/120/300 ms 去直流 RMS，50 Hz 输出。基础开度 B=clip(Em/max(El,0.015),0,1) 改用中期能量，短期突增 P=clip((Es-Em)/(0.7*max(Em,0.015)),0,1) 的增益为 0.7；增加中期起伏 M=clip((Em-El)/max(El,0.015),-1,1)，增益 0.35。V=clip((Es-0.006)/0.012,0,1)，u=V*clip(0.08+0.27B+0.7P+0.35M,0,1)，角度为 -3°+33°*u。张开/闭合滤波 25/70 ms，限速 150°/秒，范围 -3–30°。正常语音结束后，原本为 0°的静止目标恢复为 -3°；用户明确选定的其他嘴部角度保留原有优先级。手动嘴部接口接受 -3–30°，口型目标相对 DAC 播放时间提前 100 ms；其他关节、音频播放/解码/AEC/音量/缓冲及 DAC 时钟不变。

**嘴型波动（0.2.20）。** 在原说话动嘴能力上，每 20 ms 叠加当前、80 ms 和 320 ms RMS 能量，权重为 0.6/0.3/0.1；短/中/长能量差补充音节变化，3.2 Hz 与 5.1 Hz 的混合节奏让持续元音也有开合。在节奏变化之外，随有效语音能量补充最多 2°张口，避免平均开口变小。攻击/释放平滑为 35/60 ms，保持 0–12° 和 90°/秒限制；当前音频静音时目标立即归零并平滑闭嘴，不由长窗口维持张嘴。跨 60 ms 音频帧连续计算，保留原 DAC 同步、队列时效和身体已使能检查。`tts_mouth.envelope` 显示各窗口 RMS、节奏和目标角度，便于观察。

**说话动嘴（0.2.15）。** 默认 `"tts_mouth_enabled": true`。从实际送给扬声器的 PCM 每 20 ms 提取去直流的 RMS 音量，加少量过零率变化，平滑开合并限幅到 0–12°，不是音素口型识别。按声卡 DAC 时间排队，补偿现有约 540 ms 的实际输出延迟，网络预缓冲不会让嘴提前动；实时音频回调只入有界队列，计算和 IPC 在聊天进程的工作线程完成。相邻音频帧合并发送；IPC 较慢时保留最近两帧并丢弃积压/过期数据，避免嘴落后声音。状态中的 `last_lead_ms`、`dropped_frames`、`ipc_errors` 和 `last_error` 可用于诊断。

仅在 Policy/Hold、身体舵机已使能、反馈/命令链路正常且不在起身、抓取或翻滚时更新嘴部；嘴型只改变嘴部目标，身体舵机由运动控制管理。显式嘴部动作和手动嘴部控制优先。播放结束、打断、失联、退出或约 180 ms 的嘴部租约超时会清掉未执行目标，并恢复说话前的嘴部目标（通常为闭嘴）；旧回合的迟到数据不能重启动画。卸力时仍正常聊天。设为 `false` 可关闭；状态从 `/api/voice/status` 的 `tts_mouth_enabled`、`tts_mouth` 读取。说话动嘴沿用原网页和运动模型；保扭矩暂停另需配套 STM32 更新。

App 以仅推理模式启动，舵机处于卸力状态。完成舵机设置和标定后，可直接发出语音动作指令。手机方向遥控 `Duck.RemoteMove` 使用独立开关，默认关闭。

| 工具 | 行为 |
| --- | --- |
| `Duck.EnableRemoteMotion` | 授权手机 MCP 遥控，开启本身不启动舵机 |
| `Duck.RemoteMove` | 手机前后/转向，租约 0.1–1 秒；每个控制器的递增序号拒绝过期指令，松手发送 stop |
| `Duck.Stand` | 明确请求起立，以零方向速度进入原平衡/起身策略；反馈确认已使能并直立稳定后才允许移动 |
| `Duck.DefaultPose` | 使用原控制页的 `hold` 模式使能并保持预设关节角。执行前检查 MCU 和 IMU 反馈。回执确认模式切换 |
| `Duck.Move / Duck.Turn` | 语音默认建议：前后 0.2 m/s、侧移 0.15 m/s，左转 +1 rad/s、右转 -1 rad/s；范围与网页摇杆一致：前后 ±0.4 m/s、侧移 ±0.3 m/s、转向 ±1 rad/s，每次 0.1–10 秒 |
| `Duck.SetHead / Duck.SetMouth / Duck.Pick` | 头颈每轴 ±0.5 rad、嘴部 0–30°、原 4 秒抓取策略 |
| `Duck.Roulade` | 站稳后运行一次翻滚模型，首个目标发出后计时约 1.7 秒；无方向/时长参数，后续动作等待恢复站稳 |
| `Duck.Quack` | 本地合成一声鸭叫；`kind` 为 `greet/inquire/alarm/peck/chirp/coo/wheee` |
| `Duck.GetStatus` | 反馈、控制权、当前动作 `motion_action`、排队 `queued_actions` 和最近结果 `recent_actions` |
| `Duck.Stop` | 停止当前动作并清空排队，速度归零、保留平衡；`Duck.TorqueOff` 才明确卸力 |

**按动作报告反馈问题（0.2.21）。** 不再把某个 ID 缺反馈作为全部 MCP 动作的拒绝条件。行走/转向检查十个腿部舵机，头颈检查本次改变的轴，嘴部和 TTS 嘴型检查 34 号；抓取涉及全部舵机，翻滚涉及腿部和头颈，以及需要回零的嘴部。Stand 还检查本次要回零的头颈/嘴部。其他 ID 缺反馈在 `GetStatus.feedback_warning` 中提示，不取消无关动作。`action_feedback_ready` 和 `head_feedback_ready` 分别给出动作和头颈各轴的反馈可用性；`full_robot_ready` 只保留为全部反馈齐全的诊断指标。动作仍检查控制权、使能、姿态和 IMU/通信时效。

排队入口校验会话、参数、控制权和队列容量，不读取舵机反馈；立即执行和出队共用一处执行前检查。执行中使用已有状态流监测本次所需关节，不增加串口轮询、回包等待或全舵机拦截。拒绝或中止时说明具体缺失 ID、IMU 无效、STM32 反馈中断/尚未收到等可观测问题。现有固件不区分设备未回包与报错包被丢弃，因此不能把缺反馈擅自解释成过热、过载、断线或损坏，也不能只等待一个可能永远不会返回的设备错误码。

模型头颈命令的顺序是颈部俯仰、头部俯仰、左右转头、头部侧倾，分别为 `head1`–`head4`；默认装配对应 ID 30–33，独立嘴部为 ID 34。实际重新编过 ID 的机器需单独核实。`SetHead` 至少传一个轴，省略的轴保留执行时的原目标；单独左右转头只传 `head3`，不要为了补齐参数把其他轴设为零或同时设为极值。保持已设定的同一目标不要求这个轴重新就绪；改变或回零才检查该轴。`SetHead` 保持目标，需要额外指令回中，并不是一次完整摇头动作。TTS 嘴型只更新原嘴部目标，由同一运动进程发送和读取全部舵机，不因正在说话而独占头部或停止嘴部反馈。

**连接成功提示音（0.2.18）。** App 每次启动，确认联网后播放 `greet`，确认已绑定且语音服务 MQTT 实际在线后播放 `wheee`；复用 `Duck.Quack` 的本地合成器。这两个提示音额外乘 3 倍软件增益，在叠加已有扬声器增益前限幅；普通 MCP 鸭叫和 TTS 增益保留。联网提示不等待账号绑定：聊天尚未启动时短暂使用同一音频占用锁播放，释放声卡后才启动聊天；聊天已启动则沿用原输出流。无 USB 扬声器或音频被网页/TTS 占用时不阻塞启动，稍后补播；启动内成功过的提示不因轮询、断线或配置刷新重复播放。只有真实播放完成才记录成功；状态见 `/api/voice/status` 的 `connection_sounds`，不使能或驱动舵机。

**鸭叫（0.2.16）。** 例如“像鸭子一样叫一声”（`greet`）、“疑惑地叫一声”（`inquire`）、“开心地叫一声”（`wheee`）。`alarm` 为警觉叫，`peck` 为啄叫，`chirp` 为轻鸣，`coo` 为低鸣。工具本地合成一次，声音长短和音色由设备身份与轮换变体决定，每声最多 2 秒；不朗读“嘎嘎”，不下载新 ONNX，也不调用云端 TTS。合成器移植自 [MicroDuck](https://github.com/pollen-robotics/microduck/tree/d7560ef059c73fab4177eb247c1774013f6feb9d/sounds) 的 Apache-2.0 Rust 源码，许可、修改说明与源码校验记录随 ZIP 分发。

沿用聊天进程当前 USB 输出流、音量和扬声器增益，不另开声卡。当前回答未结束时最多等待一秒，仍忙则返回具体错误；同一时刻只播一声。鸭叫期间暂停唤醒/上行收音，短暂保留提早到达的 TTS，播完再按顺序恢复；不会把自己的叫声送去识别。工具等 PCM 与声卡尾部排空后才返回 `completed`，这表示音频播放链完成，不保证外部扬声器有电或音量非零。打断、新用户语音、音频重置、断连/退出或播放超时会中止；状态和最近原因可从 `/api/voice/status` 的 `duck_sound` 读取。原网页和 TTS 说话嘴型保持原行为；单纯鸭叫不使能或驱动嘴部舵机。

`0.2.11` 起，普通语音左右转默认使用 ±1 rad/s、2–3 秒；用户明确要求较慢时仍可指定较小值。速度上限保持 ±1 rad/s，手机遥控仍为原先 ±0.5 rad/s，原网页 Q/E 参数未改。

手机遥控路径为 **小程序/App → 对应云服务 → MCP → 聊天进程 → 运动进程**，手机蓝牙只用于配网；原蓝牙手柄功能保留。要求舵机/IMU 就绪，校准、Servo setup 和手柄占用时不抢控制权。网页模式按钮可手动接管并作废旧遥控续租；租约到期、聊天失联超过 0.8 秒或遥控权限关闭会停止速度并保留平衡。

首次运动时，放好鸭子并留出起身空间，直接说“站起来”。`Duck.Stand` 使用零方向速度运行原起身和平衡策略。也可以在控制页选择 Policy 启动平衡。起立请求返回“已接受”不代表已经站好：`Duck.GetStatus` 的 `movement_ready` 才表示移动条件已经满足，起立五秒内未确认就报告失败。


**STM32 反馈暂时中断。** 保持默认姿态或运行运动模型时，Linux 在 150 ms 内没收到新的 STM32 反馈，就暂停发送新目标。IMU 和 Bridge 正常时，STM32 继续保持上一组舵机目标。有效反馈恢复且舵机仍已使能时，小鸭自动回到原控制模式，坐着的小鸭继续保持坐姿。中断前的移动速度清零，已取消的动作需要重新发出。若 STM32 已卸力，处理故障后重新使能。标定和舵机设置有独立的超时保护。

**路由器兼容（0.2.41）。** STM32 每 2 秒重新注册一次自己的 4 个 Bridge 路由，防止 Router 丢失它们。Router 0.5.4 到 0.10.x 在路由仍然存在时会回答 `RouteAlreadyExists`，0.2.37–0.2.40 把它当成失败：Bridge 每 2.26 秒重连一次，舵机设置、标定、Hold / Policy 被打断，舵机扫描只检查几个 ID 就中止。0.2.41 把这个回答视为“已注册”（Router 0.11.0 返回成功），所以 Router 0.8.0 至 0.11.0 表现一致。已在 Router 0.9.0 的板子上验证：没有重连，254 个 ID 的扫描用时 119 秒完成。请用 Run 同时更新 App 和配套的 MCU 固件。

高频嘴部目标改走 App 私有目录中权限为 0600 的 Unix datagram socket，含每次 App 启动的临时令牌、会话/回合/序号校验。发送不等待 HTTP 回复，仍有界、过期丢弃、按 DAC 时间执行；音频回调只入队。MCP/普通控制命令仍使用原有认证的回环 HTTP。嘴部在身体未使能、姿态不适合或反馈异常时停止，不会自行使能身体。

**按住方向键时，小鸭可先起身再移动。** 从仅推理、卸力、默认姿态或坐姿出发时，板端复用 `Duck.Stand` 的零速起身流程。移动要求 Policy 模式、舵机已使能、所需反馈有效、恢复阶段为 `walk`，并且身体倾角小于 15° 连续一秒。起身期间保持零方向速度，站稳后只响应仍按住时发送的新租约。已使能且保持坐姿时可转头，起立和踢球期间仍由对应动作控制。坐下仍要求先站稳。

站稳检查用于开始一段新的移动。已经开始的 Move/Turn 及同一手机控制器的连续续租，允许正常步态的身体倾斜，不再要求行走中仍连续满足“小于 15° 一秒”；仍要求使能、反馈和命令新鲜、恢复阶段为 `walk`、姿态已知且倾角小于原恢复策略的 55° 跌倒阈值。停止后发起新动作或更换手机控制器，仍须重新通过站稳检查。手机遥控仍保留原先 0.1 m/s、0.5 rad/s 的限速。

方向移动期间再次倾倒、进入起身恢复或通信丢失时，方向速度归零，由原平衡和起身策略继续处理姿态。恢复后，先松开手机方向键，再重新按下。工具错误会说明当前的权限、姿态或通信问题。

**10 秒与连续动作（0.2.9）。** “往前走 10 秒”使用一次 `Duck.Move(seconds=10)`，从真正开始执行时计时。当前语音动作未结束时，后续语音动作按接收顺序排队，返回 `execution.status=queued`，不会覆盖正在执行的动作。最多等待 4 个动作，当前动作剩余时间与排队动作时长合计最多 30 秒；额外的站稳等待不计入这项动作时长上限。`GetStatus` 区分当前动作、排队列表和最近 4 项结果；“已接受/已排队”都不表示实体动作完成。

连续 Move/Turn 沿用正常步态检查；其他排队动作在执行前重新检查站稳，最多等待 5 秒。起立失败、非主动翻滚期间的跌倒/恢复、通信或使能丢失、聊天断开、手柄/网页接管及 App 退出都会取消待执行动作；恢复后不会自行接着走。Stop/TorqueOff 清空整条队列，停止前已发出但延迟到达的请求也不能重新启动运动。队列只在内存中保存；手机遥控仍使用独立短租约，不加入语音队列。

坐姿保持时收到踢球、抓取、翻滚或行走请求，板端先运行 9527 页面使用的起立过渡，确认站稳后再执行目标动作。默认姿态已使能且站稳时，可以直接坐下。遥控移动时可用语音转头。腿部动作冲突时按顺序执行，手机方向键松开只结束对应的遥控指令。腿部动作结束后，仍按住的方向键可以用新指令继续控制。

**语音翻滚（0.2.14）。** 站稳后说“翻滚一次”或“滚一圈”，调用无参数 `Duck.Roulade`，使用 `xgoduck_roulade.onnx`，预热后从首个目标发出时计时约 1.7 秒。翻滚前要求站稳、有效反馈和控制权；执行期间沿用原控制器的倾角处理，通信、使能或聊天会话丢失时中止。结束或 Stop 会退出翻滚、恢复平滑参数并复位动作滤波，保留原平衡/起身策略。排队的后续动作可等待这次主动翻滚的恢复和站稳，最多 5 秒；超时清队列，不自动重试翻滚。`completed` 仅表示指令窗口结束，不代表已经测得翻过一整圈。原网页按钮和手机方向遥控保持原行为。

**拍照认证（0.2.9）。** 摄像头请求的 `Device-Id` 与 OTA 签发凭据时统一使用小写格式，避免“设备 ID 与 token 不匹配”。持久设备身份、绑定关系、Client-Id 和签名 token 保持原值；更新后无需重新绑定。摄像头缺失、网络或模型服务错误仍会报告具体原因。

`Duck.GetStatus.motion_action` 和 `[motion-access]` 日志记录请求速度、动作状态、实际指令持续时长及中止原因。`completed` 只表示限时指令已结束，板子没有测量真实行走距离；不能仅凭工具返回“已接受”就宣称已经走了几步。旧版语音平移最高仅 0.1 m/s，模型可能只做很小的姿态调整；普通“往前走”建议从网页 W 键同样的 0.2 m/s、2–3 秒开始对照。

`Duck.GetStatus` 在 MCP 中使用 `motion_action` 表示当前动作/租约，空闲时为 `null`。顶层 `action` 是现有云端的回复类型保留字段，不能用来放机器人动作。若旧版在查询状态后播报 “None/null”，更新到 `0.2.7` 并重启聊天进程或 App；保持整个 `data/`，无需重新绑定。

## 状态与排错

在板端查看：

```sh
arduino-app-cli properties get default
systemctl is-enabled xgoduck-ai-hardware.service arduino-app-cli.service
systemctl is-active xgoduck-ai-hardware.service
sudo journalctl -u xgoduck-ai-hardware.service -b -n 80 --no-pager
curl -fsS http://127.0.0.1:9527/api/voice/status | python3 -m json.tool
```

| 状态/现象 | 检查 |
| --- | --- |
| `awaiting_network` | 手机蓝牙配网；扫描失败时同时查看板端硬件服务日志和手机错误码 |
| `awaiting_binding` | 服务确认暂无记录。检查客户端区域和账号绑定的设备 ID。 |
| `checking_binding` / `cloud_error` | 正在检查绑定或等待重试。检查 DNS、时间和服务可达性，也可在驾驶舱手动选区。 |
| `hardware_error` / identity mismatch | 核对当前 App 路径和身份，重跑硬件支持安装；不要删除已绑定身份 |
| `waiting_audio` | Host 模式、Hub 供电、USB capture/playback 声卡及持久选择；MCP 仍可在线 |
| 手机找不到设备 | 硬件服务是否启动、是否已联网并绑定而停止广播、蓝牙是否可用；原系统/驱动故障需单独排查 |
| 开机不运行 | 默认 App 是否指向这个目录，首次联网 Run 是否完成，官方 daemon 日志和 App Lab 控制台 |
| 安装后 USB 连接消失 | 用 Wi-Fi/SSH 查看 `journalctl -u xgoduck-ai-usb-startup.service -b`。如当前为 Host，可在驾驶舱关闭 Host 后连接电脑 |
| 重启后 9528 被占用 | 检查并关闭旧 `duck-device-test.service` 自启 |

硬件服务可独立于聊天和 USB 音频广播。若首次离线启动时 App CLI 在进入 Python 前因缺少依赖失败，成功配网后只对**当前仍是默认的本 App**尝试恢复一次；已经运行过又被手动 Stop 的 App 不会被这个恢复逻辑擅自重启。建议首次依赖完整缓存后再做离线冷启动验收。

## 开发与验证

本地测试环境需先安装 `python/requirements.txt`、`dbus-next==0.2.3` 和 `fastapi==0.118.0`；ARM64 Docker 镜像已准备这些测试依赖。

```sh
python3 -m unittest discover -s tests -v
docker build --platform linux/arm64 -f tools/ai-test.Dockerfile -t xgoduck-ai-sdk-test:0.2.0 .
docker run --rm --platform linux/arm64 --network none -v "$PWD:/app" xgoduck-ai-sdk-test:0.2.0
docker run --rm --platform linux/arm64 --network none -v "$PWD:/app" xgoduck-ai-sdk-test:0.2.0 python -W error::ResourceWarning -m unittest discover -s tests -v
python3 tools/package-ai-app.py
```

测试覆盖两进程生命周期、绑定/掉网/换区、真实私有 D-Bus GATT、BluFi 校验与分片、Wi-Fi 失败回滚、MCP 租约、USB 音频缺失和网页音频交接。见 [检查记录](ai-validation.md)。

本地和 Linux 容器测试不替代实机验收。第二只 UNO Q 已执行支持安装并验证正常重启自启、Host、设备枚举和运动通信；仍需验证断电/离线冷启动、真实手机配网绑定、实际聊天/AEC 与物理运动，详见检查记录。本项目的配网服务不会修复系统无线驱动故障。

官方依据：[UNO Q 初始设置](https://docs.arduino.cc/tutorials/uno-q/user-manual/)、[SSH](https://docs.arduino.cc/tutorials/uno-q/ssh/)、[App ZIP 导入](https://blog.arduino.cc/2026/02/04/introducing-the-latest-arduino-app-lab-release/)、[App 规范](https://github.com/arduino/arduino-app-cli/blob/main/docs/app-specification.md)、[默认 App 命令](https://github.com/arduino/arduino-app-cli/blob/main/cmd/arduino-app-cli/properties/properties.go)。
