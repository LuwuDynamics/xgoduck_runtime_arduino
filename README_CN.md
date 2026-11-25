# XGO Duck AI

[English](README.md) · 简体中文 · [XGO Duck 官方 Wiki](https://wiki.xgorobot.com/kb/the-xgo-series-robot/models/duck) · [常见问题](https://wiki.xgorobot.com/xgo/kb/xgo-duck/duck-troubleshooting) · [更新记录](docs/CHANGELOG.md)

在 Arduino UNO Q 上运行小鸭的语音聊天、运动控制、USB 音视频和 ToF 测距。日常操作使用浏览器，不需要给小鸭连接屏幕。

先准备 Arduino UNO Q（官方 Debian 13）、扩展板、USB 数据线、电池和舵机。聊天需要 USB 麦克风和扬声器，摄像头和板载 Qwiic 接口上的 VL53L5CX ToF 可按需安装。

下面按官方开发者指南的七步顺序配置，使用本仓库的 XGO Duck AI 安装包和启动命令。

**下面这段提示词可以让 AI 代你完成第 1～4 步。** 安装验证通过后，直接从第 5 步继续。

请先用**能传文件的手机 USB 数据线**把 UNO Q 接到电脑。仅充电的线无法传输数据。接好后，把下面这段复制给能操作本机终端的 AI。

```text
我的 UNO Q 已通过 USB 接到电脑。请按下面网页的安装说明，帮我安装最新版 XGO Duck AI。
https://github.com/LuwuDynamics/xgoduck_runtime_arduino#ai-assisted-install
```

## 1. 设置并连接 Wi-Fi

用 USB 数据线把 UNO Q 连到电脑，在 Arduino App Lab 完成首次设置。选择键盘布局、设置设备名、连接 Wi-Fi 并设置板端 Linux 登录密码。等待检查和更新完成，断开 USB 后按指南接好扩展板，再重开 App Lab 并连接开发板。 [官方图文步骤](https://www.xgorobot.com/duck-developer-guide/index.html)

## 2. 查看板子的 IP 地址

在 Arduino App Lab 右下角点击 Wi-Fi 图标，打开 **Network Info**，记下板子的 IP 地址。后续安装时，电脑和板子需要连接到同一局域网。

## 3. 导入 XGO Duck AI App

| 安装包 | 什么时候用 | 包内有什么 |
| --- | --- | --- |
| 全量包 · [魔搭 ZIP][duck-full-ms]（[SHA-256][duck-full-ms-sha]）· [GitHub ZIP][duck-full]（[SHA-256][duck-full-sha]） | **首次安装和跨版本更新的默认选择** | 约 470 MB。包含预编译 App、ARM64 Docker 基础镜像、板端依赖和 STM32 上传工具。下载并传到板子后，可在局域网内完成安装。 |
| [预编译 .ard（Release）][duck-ard] · [SHA-256][duck-ard-sha] | **已有安装时更新**，也可在环境已准备好的板子上首装 | 约 80 MB。包含预编译 STM32 固件和 Python 环境。启动时直接上传固件。板上缺少的系统依赖、Docker 镜像或上传工具仍可能需要联网获取。 |
| [App Lab 源码 ZIP（代码仓库）][duck-zip] · [SHA-256][duck-zip-sha] | **板端无法安装 .ard**，或需要编辑源码 | 约 35 MB。沿用原来的 `packages/xgoduck_ai.zip` 下载地址。在 App Lab 导入后，由板子准备依赖并编译 STM32。 |

下载全量包时，中国大陆网络优先选魔搭，其他地区优先选 GitHub。如果下载失败，从另一来源重新取 ZIP 和配套的 SHA-256 文件。需要历史版本时，请从[对应的 GitHub Release][duck-releases] 下载该版本的文件。

### 全量首装 ZIP

完成 UNO Q 的初始设置，通过 Wi-Fi SSH 登录官方 Debian 13 ARM64 系统。参考首装环境预留至少 **2.5 GiB** 可用空间。下载 ZIP 和校验文件，校验后通过 SCP 传到 UNO Q，在板上解压到新目录。若已接舵机，先扶稳小鸭并确认卸力。停止正在运行的 App，以 `arduino` 用户按包内 `INSTALL.txt` 执行 `bash install.sh`。脚本会配置 2 Mbps Bridge、设置开机自启并启动 App。完成后直接做第 4 步的验收。参考系统上，包传到板子后即可断开公网完成安装。账号绑定和云端语音需要互联网。

全量包是应用及依赖安装包，不会重刷系统。解压目录与 App 安装目录不同：0.3.7 固定安装到 `/var/lib/arduino-app-cli/releases/xgoduck-ai-0-3-7-unoq`。该目录已存在时，只有其中的 `.xgoduck-full-install.sha256` 与当前包内 `.ard` 的 SHA-256 完全相同，安装器才会复用；单独安装过同版本 `.ard` 或同版本重新打包后，也可能不满足这一条件。保留已核实可用的安装，或按明确的重装要求处理旧目录及数据后再安装。不要手工伪造收据或直接覆盖目录。[全量包说明](docs/full-install.txt)

需要迁回旧配置时，先按更新流程保存同一只鸭子的持久数据。全量安装器结束后，停止新 App 和 `xgoduck-ai-hardware.service`，迁回配置并保留权限及归属，然后启动硬件服务和新 App，再进行验收。[数据迁移和回退](docs/deployment.md#更新与回退)

### 预编译 .ard

板端需要 **Arduino App CLI 和 daemon 0.14 或更新版本**。使用 App Lab 0.11 或更新版本时，从 **Create new App ＋ → Import or install App** 选择 `.ard`。如果电脑上的 App Lab 较旧，但板端支持 `arduino-app-cli app install`，可通过 Wi-Fi SSH 安装。 [Arduino App Lab 安装说明](https://docs.arduino.cc/software/app-lab/apps/manage-apps)

将 `.ard` 和 `.ard.sha256` 传到板子同一目录，以 `arduino` 用户执行。使用实际下载的文件名。

```sh
sha256sum -c xgoduck-ai-unoq.ard.sha256 && \
  arduino-app-cli app install --yes xgoduck-ai-unoq.ard
```

安装命令会给出新 App 目录。更新时从旧 App 迁移同一只鸭子的配置，再进入第 4 步。板端 CLI 或 daemon 版本过低时，可更新板端组件，或直接使用下方的源码 ZIP。

### App Lab 源码 ZIP

下载并校验 `xgoduck_ai.zip`。在 App Lab 的 **Apps → ＋ → Import App**（新版显示 **Import or install App**）中选择 ZIP。确认导入的 **XGO Duck AI** App 目录，保持 App 停止，进入第 4 步。 [导入界面图解](https://www.xgorobot.com/duck-developer-guide/03-import-app.html)

<img src="docs/images/applab-import.jpg" alt="Arduino App Lab 的 ZIP 导入窗口" width="760">

## 4. 终端配置与启动

**全量首装 ZIP** 已由安装脚本完成本节的配置和启动，直接检查下方的网页和状态。

**预编译 .ard 和源码 ZIP** 通过 Wi-Fi SSH 完成下面的配置。USB 探测期间电脑的 USB/ADB 连接可能中断。

1. 更新时先扶稳小鸭并卸力。停止旧 App，备份旧 App 及 `data/`。保持新旧 App 都停止，再迁移这只鸭子的标定、绑定和服务区域等持久设置。 [数据迁移和回退](docs/deployment.md#更新与回退)
2. 确认其他 App 已停止，保持板子联网，再在实际的新 App 目录以 `arduino` 用户执行下面的命令。用安装输出或 App CLI 列表核对目录。

   ```sh
   bash tools/install-ai-support.sh && \
     sudo bash tools/router-2m.sh && \
     arduino-app-cli app start "$PWD"
   ```

   安装脚本配置硬件支持、驾驶舱和开机自启。第二条命令配置与该包固件配套的共享 Router。当前开发版从 115200 波特率握手，使用可靠协议协商至 **2 Mbaud**。启动会上传配套的 STM32 固件，源码 ZIP 还需要编译，`.ard` 使用随包固件。已完成配置时也可在 App Lab 点击 **Run**。

**验收**时在浏览器打开 `http://<板子IP>/` 和 `http://<板子IP>:9527/`，核对 App 版本和默认启动项。依赖准备及首次启动完成后，保持舵机卸力，执行一次整机重启；重新连接 Wi-Fi SSH，等待网页/API 就绪，确认默认 App 自动运行且版本正确。已接线的板子检查舵机与 IMU 反馈；已有标定和配置应保留。未接线、未标定时记录待完成项。

首次安装可能还未绑定账号或接 USB 麦克风、扬声器，**语音在线不是安装通过的前提**。检查 `/api/voice/status` 和日志：等待联网、绑定或音频设备时，记录对应待完成项；进程崩溃、依赖错误仍需处理。仅在已绑定、已接好音频设备且互联网可用时，检查语音云连接和音频就绪。完成后继续第 5～8 步。断网冷启动可在完成配网及所需配置后另行测试。

<a name="ai-assisted-install"></a>

### 用 AI 配网、安装和验收（第 1～4 步）

源码 ZIP 的安装流程已在 Codex、DeepSeek Harness 和 Cursor 中跑通，首装通常预留 30～60 分钟，实际取决于下载速度。全量包提前准备了依赖，板端耗时主要取决于传输、解压和本地安装。

下面是 AI 执行第 1～4 步时使用的详细说明。需要密码时，请在电脑上弹出的窗口输入。如果已有板子 IP、想指定版本或已有安装包，可以在发给 AI 的消息里补充。

```text
请帮我的 Arduino UNO Q 连上 Wi-Fi，安装或更新 XGO Duck AI，并完成启动和验收。
自动判断是首装还是更新，默认安装最新 AI 版。服务区域首装用自动设置，更新沿用已有设置。
如果我指定了板子 IP、版本、区域或本地安装包，请使用我提供的信息。
项目仓库：https://github.com/LuwuDynamics/xgoduck_runtime_arduino

1. 默认登录用户为 arduino。如果我提供了 IP，先从电脑尝试 SSH 登录，可使用已有密钥。
   登录成功就继续检查板子。需要查 IP 或配网时，用 USB 数据线和 adb devices -l 找到 UNO Q；
   若有多块候选板，让我选序列号。通过 ADB 检查板子的 Wi-Fi 连接，已联网就读取无线网卡的 IPv4 地址。
   需要配网时，显示电脑当前连接的 Wi-Fi 名称，让我确认或改选。读不到名称时让我填写。
   密码由我在可操作的本机窗口隐藏输入（Windows 用 PowerShell，Mac 用“终端”）。
   Windows 经我确认后，若有权限，可在本机复用所选网络已保存的密码，取不到再让我输入；
   Mac 让我输入该网络的密码。开放网络跳过 Wi-Fi 密码。通过 ADB 用板上的 NetworkManager 配网，
   确认连上所选网络并取得无线 IPv4 地址。密码仅在本机交互程序中处理，避免出现在聊天、命令参数、历史或日志里。
   如果无法弹出本机窗口，给我一条在本机终端启动相同交互流程的命令，完成输入后继续。
   新板若尚未设置 arduino 密码，让我设置并再次输入确认；已设置过的板子沿用原密码或已有 SSH 密钥。
   如板子尚未开启 SSH，通过 ADB 执行 arduino-app-cli system network-mode enable。
   从电脑实际登录 arduino@板子IP 后，沿用该 SSH 连接继续安装。
   后续需要 sudo 或重新认证时，也在本机窗口输入。
   核对 UNO Q、官方 Debian 13 ARM64、可用空间、arduino 用户（UID 1000）、App CLI 和 daemon 版本。
   用 arduino-app-cli app list 查看已装 App，用 app details <App ID> 核对实际目录，用 app ps 查看运行状态，
   用 arduino-app-cli properties get default 查看开机默认 App。旧版 CLI 的状态查询以帮助为准。
   先确定目标版本。我选“最新 AI 版”时，限时 5 秒查询 GitHub 最新 Release 的版本号。若查不到，可按第 2 项
   核实魔搭全量包的实际版本，并用该版本做下面的检查，说明尚未确认它是否为最新。若我指定 master 腿姿，
   复用已有 App 前要用目标构建的 build-manifest.json 核对实际目录中 python/rl_core.py 的 SHA-256，两种构建都可能显示 0.3.6。
   已有 App 运行时，只读检查驾驶舱、/api/version 和 /api/status。若运行的是目标版本及所需构建，且状态正常，
   告诉我已是目标版本，给出实际版本、板子 IP、驾驶舱和 9527 控制页的完整地址，到此结束。
   若同版本已安装但运行异常，先查现有 App 和服务日志再决定如何修复。
   需要安装时保持 Wi-Fi SSH，USB 探测可能中断 USB/ADB。

2. 首次安装和跨版本更新默认都用目标版本的全量 ZIP（第 4 项）。优先复用电脑上已通过校验的包。
   - 需要下载全量包时，在实际下载的电脑上沿用当前代理设置，用下面的命令读取公网出口的国家/地区代码。
     curl -fsS --max-time 5 https://www.cloudflare.com/cdn-cgi/trace | sed -n 's/^loc=//p'
     Windows PowerShell 可用 curl.exe -fsS --max-time 5 https://www.cloudflare.com/cdn-cgi/trace | Select-String '^loc='
     CN 优先魔搭，其他已识别的地区优先 GitHub。查询失败或代理分流导致出口不明时，并行短测下面两个
     .sha256 链接，选先返回有效校验文件的来源。板子的局域网 IP 和语音服务区域不参与选源。
     魔搭全量 ZIP 和校验文件
     https://modelscope.cn/models/mingyangbj/xgo-duck-ai/resolve/master/xgoduck-ai-unoq-full.zip
     https://modelscope.cn/models/mingyangbj/xgo-duck-ai/resolve/master/xgoduck-ai-unoq-full.zip.sha256
     GitHub 全量 ZIP 和校验文件
     https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq-full.zip
     https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq-full.zip.sha256
     从选定来源下载这一对文件。若超时、缺文件、校验失败或包内版本不符，换另一来源取目标版本的
     ZIP 和校验文件并重新校验。全量 ZIP 已包含 .ard、运行镜像和依赖。
   - 我明确选择轻量安装，或目标版本缺少全量包时，若板端支持 Release，可用同版本预编译 .ard（第 5 项）。
     https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq.ard
   - 需要编辑源码、板端无法安装 Release，或目标版本只有源码 ZIP 时，走第 6 项。若我指定 master 腿姿，
     用 master 工作区的 packages/xgoduck_ai.zip 和同目录的 .sha256。发布版对应的 AI 分支源码 ZIP 在这里。
     https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip
   Release 链接缺失或需指定版本时，到这里选对应 AI 版本。
   https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases
   GitHub 安装包和 .sha256 要取自同一 Release。源码 ZIP 与其 .sha256 都从对应分支取。
   优先在电脑下载并校验，再用 SCP 传到板上，用 sha256sum -c 再核对。
   校验失败的文件要重新下载并校验。核对包内 version.txt 和 UNO Q 目标。全量包以其中的 .ard 为准。
   指定版本缺包或候选包比已装版本旧时，保留已装版本并告诉我。

3. 已接舵机时，先让我扶稳小鸭并确认卸力。更新时停止旧 App，记下默认 App 和旧目录，备份旧 App 及 data/。
   保留旧 App 用于回退。.ard 和源码 ZIP 装到独立目录，在两个 App 都停止时迁移这只鸭子的持久配置。
   全量包会自动启动，涉及旧数据时按第 4 项停服后迁移；已有目标目录时先处理安装冲突。
   保留 IMU 标定、舵机中位记录、设备身份、绑定、区域和手柄配置。按 docs/deployment.md 的「更新与回退」
   处理历史零位文件，跳过旧 .cache、data/native/ 和运行时 socket。确认其他 App 可停止并停妥，再运行安装器
   或改共享 Router。第 4～6 项只做所选的安装路线。

4. 将全量 ZIP 在板上解压到新目录。参考首装环境预留至少 2.5 GiB，按 INSTALL.txt 以 arduino
   用户运行 bash install.sh，按需提供 sudo。先从 install.sh 核对固定的 app_root；解压到新目录不会改变它。
   目标目录已存在时，核对 .xgoduck-full-install.sha256 是否与包内 .ard 的 SHA-256 完全一致。
   相同才可停掉 App 后重跑安装器。收据缺失或不同时，保留已核实可用的安装并报告冲突；若我已明确要求
   重装，则先保存需保留的数据、停止相关服务并处理旧目录，再运行安装器。不要伪造收据或覆盖旧目录。
   安装器使用包内系统依赖、ARM64 镜像和上传工具，安装预编译 App、设置 2 Mbps Bridge 和开机自启，
   然后启动。跨版本更新或需要恢复已保存的配置时，停止新 App，再执行 sudo systemctl stop xgoduck-ai-hardware.service，
   迁移第 3 项的持久配置并核对内容、权限和归属，保留新生成的 native 文件。
   依次执行 sudo systemctl start xgoduck-ai-hardware.service 和 arduino-app-cli app start --no-prepare <新目录>。
   全量包不会重刷系统。完成配置迁移及第 7 项选区处理后再验收。
   全量包针对参考官方 Debian 13 镜像。若本机缺少包外组件，先定位缺的组件，再决定如何补齐。

5. .ard 路线先执行 arduino-app-cli version 和 arduino-app-cli app install --help，确认 CLI 和 daemon
   支持 Release（0.14+）。电脑 App Lab 较旧但板端支持时，直接用 SSH 执行
   arduino-app-cli app install --yes <已校验的.ard>。也可用 App Lab 0.11+ 的 Import or install App。
   从安装输出或 App 列表确认新目录。若提示依赖下载失败或已安装，先核对实际目录、版本和日志，
   复用已经安装的同版本 Release，补齐缺失的 Docker 镜像或 STM32 上传工具。正常使用包内固件和
   Python 环境，跳过源码 ZIP 的工具链和 PyPI 准备。固件缺失或校验失败时，按 docs/ai-chat.md 的
   「UNO Q 预编译版」重建。板端版本过低且希望继续用 .ard 时，先确认是否更新板端组件。

6. 源码 ZIP 路线先检查 arduino-app-cli app import --help，支持时用 arduino-app-cli app import <ZIP>，
   否则指导我在 App Lab 导入。用实际导入目录，避免直接解压覆盖旧 App。
   核对 sketch/sketch.yaml、provenance/toolchain.json 和 python/requirements.txt。以 arduino 用户执行
   arduino-cli core list。若安装的 arduino:zephyr 缺失或版本不符，按 sketch/sketch.yaml 安装并复查。
   0.3.6 对应 arduino-cli core install arduino:zephyr@1.0.0，
   按该包的 sketch.yaml 使用配套的 RouterBridge 和 RPClite。若列出本地目录，使用 Arduino CLI 1.3.0 或更高版本读取随包库。使用 Linux ARM64
   arm-zephyr-eabi 1.0.1。上传时也要用对应 core。工具链下载失败时，用电脑从 provenance/toolchain.json
   的官方 URL 下载，校验大小和 SHA-256，SCP 到板上，再在 App 目录执行
   python3 tools/prepare-ai-sketch.py --toolchain-archive <板上的官方压缩包路径>。
   国内下载 Python 依赖慢且没有自定义源时，可在 App 目录执行
   python3 tools/configure-ai-python.py --index-url https://pypi.tuna.tsinghua.edu.cn/simple/
   板端 Python 下载仍慢时，可用电脑上匹配板端容器镜像、Linux ARM64 架构和 Python 版本的容器，
   按 python/requirements.txt 下载或构建全部 wheel 及其依赖，校验后 SCP 到板上，在 App 的 Python
   环境中离线安装并核对锁定版本和 SDK 导入。不要使用 Mac 原生 wheel，也不要提前写入安装成功标记。
   下载 core、Docker 镜像或系统包失败时，根据日志只补缺失项。必要时临时使用我电脑已有的代理，
   下载完成后恢复原配置。

7. 服务区域选自动识别时，首装保留未选区，更新保留原选区或自建地址。我明确选区时，启动后从驾驶舱选择，
   或在 .ard / 源码 ZIP 启动前运行对应命令：python3 tools/configure-ai.py --region cn
   或 python3 tools/configure-ai.py --region global。
   对 .ard 和源码 ZIP，在实际 App 目录迁移数据后，保持联网，依次执行 bash tools/install-ai-support.sh、
   sudo bash tools/router-2m.sh、arduino-app-cli app start "$PWD"。每条成功后再继续。启动会上传 STM32 程序。
   参考全量包预装系统 Router 0.11.0、App CLI 0.14.1、Arduino CLI 1.5.1 和 ARM64 Python 基础镜像
   0.13.1。可靠协议包的安装脚本另外启动包内指定版本的 Router。核对包内要求，复用兼容的现有组件。确认默认 App 指向新目录。对于可靠协议包，核对 Router 的 --serial-protocol auto 和
   --serial-max-baudrate 2000000；App 启动后用 sudo stty -F /dev/ttyHS1 speed 确认实际为 2000000。
   对于旧版固定 2M 包，使用旧包自己的 Router 脚本。

8. 验收时保持舵机卸力。检查驾驶舱、9527 控制页和 /api/version，确认实际运行版本。
   首次启动及依赖准备完成后，执行 sudo systemctl reboot。重新连接 Wi-Fi SSH，等待网页/API 就绪，
   确认 boot ID 已改变、默认 App 自动运行且版本正确。跨版本更新后，对照旧 App 核实舵机中位、
   IMU 标定、设备身份和原有账号绑定均已保留。
   检查 /api/status 的 Bridge；按实际接线情况检查舵机和 IMU。未接线或未标定时记录待完成项。
   检查 /api/voice/status 和日志。首次未绑定、缺少 USB 音频或没有公网时，不要求 bound、
   cloud_connected、audio_ready 全为 true；记录待联网、待绑定或待接设备。进程崩溃或依赖错误需处理。
   已绑定、音频设备齐全且公网可用时，才要求语音云连接和音频就绪；不要把待绑定当作安装失败。
   本次正常重启不要求断开公网；断网冷启动可在配置完成后另测。已接 ToF 时轮询 /api/tof/status，确认 state=ranging、
   fresh=true、seq 递增且 valid_zones > 0。若异常，用容器内 python /app/tools/read-tof.py --seconds 4 对照。
   若验收重启后仍检测不到已接入的 ToF，记录为「ToF 待复测」。
   只提醒我「请手动将小鸭的电源开关关闭再打开一次。等小鸭启动后，再查看 /api/tof/status。」
   若语音日志提示 libopus.so.0 权限错误，检查新 App 的 native 库和目录对容器运行用户是否可读及可遍历。
   启动失败先查 App、Router 和硬件服务日志。若需回退，停止新 App，按 docs/deployment.md 从旧目录
   恢复硬件服务和默认项，再启动旧 App，保留备份。汇报所用安装包、校验结果、版本、实际目录和验收结果。
   给出重启后确认的板子 IP、驾驶舱地址 http://<板子IP>/ 和控制页地址 http://<板子IP>:9527/，用实际 IP 替换占位符。
   安装成功后提醒我按 README 完成第 5～8 步。依次设置舵机编号、组装、统一标定 15 个舵机、单独标定 30 号颈部舵机、
   标定 IMU，再检查默认姿势，最后绑定账号并选择智能体。已完成的步骤跳过，告诉我下一项。
```

[duck-releases]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases
[duck-full-ms]: https://modelscope.cn/models/mingyangbj/xgo-duck-ai/resolve/master/xgoduck-ai-unoq-full.zip
[duck-full-ms-sha]: https://modelscope.cn/models/mingyangbj/xgo-duck-ai/resolve/master/xgoduck-ai-unoq-full.zip.sha256
[duck-full]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq-full.zip
[duck-full-sha]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq-full.zip.sha256
[duck-ard]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq.ard
[duck-ard-sha]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq.ard.sha256
[duck-zip]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip
[duck-zip-sha]: https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip.sha256

## 5. 设置舵机并编号

[按官方图文完成舵机设置与编号](https://www.xgorobot.com/duck-developer-guide/05-servo-setup.html)

## 6. 组装 XGO Duck

[按官方图文组装 XGO Duck](https://www.xgorobot.com/duck-developer-guide/06-assembly.html)

[音视频安装教程（PDF）](docs/xgo-duck-av-installation.pdf) · [下载中英装配 PPTX（约 69 MB）](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/assembly/xgo-duck-assembly-zh-en-20261016.pptx)

<img src="docs/images/wiring/overview-zh.png" alt="XGO Duck 实物端口接线图，标出 UNO Q、载板、摄像头、ToF 模块、扬声器和补光灯" width="960">

## 7. IMU 标定和舵机标定

打开 `http://<板子IP>:9527/`，确认 **15/15** 个舵机在线。扶稳小鸭后点顶部 **标定**，等页面确认舵机已卸力。

1. **统一标定舵机。** 按四张图摆好小鸭，避开线束。点 **标定 15 个舵机**，等页面确认中位已保存。
2. **单独标定颈部。** 让脖子与躯干平行，点 **标定 30 号舵机**。看到中位已保存后，点 **全部卸力**。
3. **标定 IMU。** 保持舵机卸力，让 UNO Q 竖直、电池接口朝上、板子下边缘贴住水平桌面。点一次 **标定 IMU**，保持静止直到页面显示已保存。若读数有波动，页面会显示自动重试的进度。[IMU 标定细节](docs/imu-calibration.md)
4. **检查默认姿势。** 把小鸭放到平坦处扶稳，点标定页底部的 **默认姿势**。等舵机上力，再对照实拍检查头颈、腿和脚的位置。如果姿势不对，先扶稳并点 **全部卸力**，再复查摆位和舵机标定。

<img src="docs/images/default-pose-reference.jpg" alt="小鸭默认站姿实拍：左为轮脚，右为普通脚掌" width="900">

[标定按钮和状态说明](docs/calibration-guide.md)

## 8. 绑定账号，选择智能体

1. 电脑或手机与小鸭连接同一网络，打开 **`http://<板子IP>/`**，无需端口和页面后缀。

   <img src="docs/images/cockpit-live-cn.png" alt="中文驾驶舱的实时画面、ToF 测距、左右遥控摇杆和底部动作按钮，顶部为 AI 语音交互绑定入口" width="1000">

2. 打开准备使用的绑定平台。也可在驾驶舱顶部的 **AI 语音交互绑定** 里提前选择 **国内版** 或 **国际版**，点击即保存选区并重连语音。

   | 设备选区 | 唤醒词 | 绑定与管理平台 |
   | --- | --- | --- |
   | `cn` 国内版 | **小鸭小鸭** | [agent.xgorobot.com/devices](https://agent.xgorobot.com/devices) |
   | `global` 国际版 | **Hey Ducky** | [agent.luwudynamics.ai](https://agent.luwudynamics.ai/) |

3. 登录平台，在「设备」中点击「添加设备」。按提示开启蓝牙，选择 `XGO-Duck` 开头的设备，完成配网和账号绑定，再选择要使用的智能体。
4. 回到驾驶舱打开 **AI 语音交互绑定**，确认语音在线后，说页面上当前选区对应的唤醒词。

<img src="docs/images/ai-binding-cn.jpg" alt="AI 语音交互绑定窗口显示国内版唤醒词小鸭小鸭和国际版唤醒词 Hey Ducky" width="760">

网页蓝牙需使用支持 Web Bluetooth 的 Chrome / Edge。iPhone / iPad 可用对应的小程序或 App 完成添加：

| 国内微信小程序 | 国际版 App |
| --- | --- |
| <img src="docs/images/qr_miniprogram.png" alt="国内微信小程序二维码" width="150"> | <img src="docs/images/qr_app_intl.png" alt="国际版 App 下载二维码" width="150"> |
| 用微信扫码 | [打开 App 下载页](https://agent.luwudynamics.ai/download) |

国内、国际账号和设备绑定相互独立。若小鸭还未选区，联网后会自动检查两边的绑定，只绑定一边就使用该服务，两边都绑定则使用国内版。已保存的选区优先。切换服务器会保留对话语言、音量和运动设置。首次切到另一侧需要在那里单独绑定。设备已绑定其他账号时，应先在原账号中解绑。

如果绑定后语音没上线，可以复制这段提示词让 AI 协助排查。

```text
板子 IP：[填写]，我选择的服务区域：[国内 cn / 国际 global]。
请读取驾驶舱的绑定状态和 9527 的 /api/voice/status，检查服务区域和语音连接。
告诉我卡在配网、绑定还是语音连接，并引导我在相应平台完成下一步。
账号登录、蓝牙授权、绑定和换区由我操作。
```

### 开机提示音

后续通电会自动探测 USB 音视频设备，启动网页、聊天和运动服务，**无需每次打开 App Lab 或点击 Run**。完成配网、账号绑定并接好 USB 音频设备后，会依次听到两段启动鸭叫：

| 顺序 | 含义 | 示例音效 |
| --- | --- | --- |
| ① | 联网成功 | [试听第一段鸭叫](assets/startup-network.wav?raw=true) |
| ② | 语音服务已连接，聊天就绪 | [试听第二段鸭叫](assets/startup-voice.wav?raw=true) |

如果还未绑定，联网鸭叫之后会播报三遍「请在浏览器打开［当前 IP 地址］，完成 AI 绑定」。开机连上 Wi-Fi 后，UNO Q 的板载点阵将完整 IP（如 `192.168.31.8`）连同分隔点一起向右循环滚动，末位数字最先从左侧进入，30 秒后熄灭。

听到语音服务就绪的鸭叫后，说 **AI 语音交互绑定 → 开机自启与唤醒** 中显示的唤醒词。听到唤醒鸭叫后就可以说话。启动音示例可在当前手机或电脑上试听，实际启动音由小鸭扬声器播放。

## 日常使用

- **界面语言：**每次打开或刷新时跟随访问设备的浏览器系统语言，中文显示中文，其余显示英文；顶部切换按钮仅临时生效。
- **音频测试：**首页「实时音频」中，左侧「测试扬声器」播放约 3 秒合成鸭叫，播放时可点击停止；右侧「实时监听」收听小鸭麦克风。
- 手机横屏、竖屏默认都显示双摇杆。「更多」里有其他动作和鸭叫，「视图 → 显示全部」可打开设备设置。
- 电脑上按住 **W/S** 前进或后退、**A/D** 侧移、**Q/E** 转向。松开按键或按 **空格** 停止移动。
- 国内版说「小鸭小鸭」，国际版说「Hey Ducky」唤醒聊天。在平台中管理智能体，在驾驶舱「设置 → 扬声器」调整音量。
- **音视频：**驾驶舱可开启/关闭摄像头、控制补光灯、保存照片和实时监听麦克风。
- 开机先开启 USB Host，最多等待 5 秒识别 USB 摄像头或音频设备。识别到就保留 Host，否则切回 Device，方便用 USB 连接电脑。开机后才插入外设时，先让小鸭停止运动，再通过 Wi-Fi 打开驾驶舱，用顶部的 **USB Host** 开关手动开启。切换会中断当前 USB 连接。
- ToF 正常测距时显示 8×8 实时距离，面板的「前向避障」默认关闭。开启后，普通速度前进时在 15 cm 内减速，10 cm 及以内停止前进。未接 ToF、传感器离线或没有有效的前方读数时，仍可前进。[避障说明](docs/forward-avoidance.md)

## 常见问题

### 1. 小鸭走不稳或发抖

1. 先停止移动，扶稳小鸭并卸力。在舵机设置的「单舵机运动控制」中核对舵机 ID、安装位置和顺序，检查舵盘和固定螺丝是否松动。
2. 打开 9527 的 **标定** 页，按[第 7 步](#7-imu-标定和舵机标定)统一标定全部 15 个舵机，单独标定 30 号颈部舵机，再标定 IMU、检查默认姿势。
3. 打开 **舵机设置**，点 **扫描所有 ID**。逐个选中舵机，将 **KP 设为 5、KD 设为 10**，点 **写入 KP/KD（Set KpKd）**，再点 **读取** 核对保存值和运行值。断电重启后，舵机会加载保存值。

本项目基于 Pollen Robotics 的 [Microduck](https://github.com/pollen-robotics/microduck)。[硬件](https://github.com/LuwuDynamics/xgoduck_hardware) · [训练代码](https://github.com/LuwuDynamics/xgoduck_rl)
