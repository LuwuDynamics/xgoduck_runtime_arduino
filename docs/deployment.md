# XGO Duck AI 部署简表

适用于运行官方 Debian 13 ARM64 的 Arduino UNO Q。

| 安装包 | 适合谁 | 下载和安装 |
| --- | --- | --- |
| 全量首装 ZIP（约 470 MB） | 新板或刚重刷系统 | [Release 附件](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq-full.zip)。校验后用 SCP 传到 UNO Q，解压并以 `arduino` 用户按 `INSTALL.txt` 运行 `bash install.sh`。 |
| 预编译 `.ard`（约 80 MB） | 更新，或在已准备好运行环境的板子上首装 | [Release 附件](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases/latest/download/xgoduck-ai-unoq.ard)。用 App Lab 0.11+ 或通过 SSH 用 `arduino-app-cli app install` 安装。板端 App CLI 和 daemon 需 0.14+。 |
| App Lab 源码 ZIP（约 35 MB） | 板端无法装 `.ard`，或需要编辑源码 | [现有 ZIP 链接](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/raw/ai-chat-audio-video-tof/packages/xgoduck_ai.zip)。在 App Lab 导入后，板上准备依赖并编译 STM32。 |

全量包包含参考系统缺少的依赖、ARM64 Docker 镜像、Python 环境、STM32 固件及上传工具。脚本会配置 2 Mbps Router、开机自启并启动 App。包传到板上后，安装可在局域网内完成。绑定账号和云端语音需要互联网。`.ard` 和源码 ZIP 导入后按 README 第 4 步配置并启动。

全量包不会重刷系统。它固定安装到 `install.sh` 中的 `app_root`；换解压目录不会改变安装目标。0.3.6 的目标是 `/var/lib/arduino-app-cli/releases/xgoduck-ai-0-3-6-unoq`。已有目标目录只有在 `.xgoduck-full-install.sha256` 与当前包内 `.ard` 的 SHA-256 完全一致时才能复用。单独装过 `.ard` 没有该收据，同版本重新打包也会改变校验值。遇到冲突时保留已验证可用的安装；明确需要重装时，先保存应保留的数据、停止 App 和相关服务，再处理旧目录。不要伪造收据或覆盖原目录。[完整中英文安装说明](full-install.txt)

## 更新与回退

1. 扶稳小鸭并卸力，停止旧 App，记录默认 App 和安装目录，备份旧 App 及 `data/`。用新的 `.ard` 或源码 ZIP 安装到另一个目录。
2. 新旧 App 都停止时，迁移同一只鸭子的持久数据，包括 `data/imu-calibration.json`、舵机中位记录、`data/voice_config.json`、`data/voice-state/` 和手柄映射。保留文件权限及归属。跳过 `.cache/`、`data/native/` 和运行时 socket。历史软件零位按硬件中位迁移说明处理。 [标定迁移](ai-chat.md)
3. 在新目录按 README 第 4 步配置和启动。当前开发版的 Router 与 STM32 使用可靠协议，从 115200 握手后协商至 2 Mbaud。回退到旧版固定 2M 时，停止新 App，在旧目录恢复硬件支持和开机默认 App，并执行旧目录的 `sudo bash tools/router-2m.sh`，再启动旧 App。 [安装与启动](../README_CN.md#4-终端配置与启动)

若使用全量包迁回旧配置，安装器会自动启动新 App。安装结束后先运行 `arduino-app-cli app stop <新目录>`，再执行 `sudo systemctl stop xgoduck-ai-hardware.service`。按上面的文件范围迁回数据，保留权限、归属和新生成的 `data/native/`。核对完成后依次执行 `sudo systemctl start xgoduck-ai-hardware.service` 和 `arduino-app-cli app start --no-prepare <新目录>`，再验收。

## 安装验收

首次启动及依赖准备完成后，保持舵机卸力，执行一次 `sudo systemctl reboot`。重新连接 Wi-Fi SSH，等待网页/API 就绪，确认 boot ID 已改变、默认 App 自动运行且 `/api/version` 正确；已有配置和标定应保留。按实际接线检查 MCU、舵机、IMU 和可选 ToF。

首次未绑定账号、缺少 USB 音频或没有公网时，不要求语音在线，将对应配置记录为待完成项。语音进程崩溃或依赖错误仍需处理。只有已绑定、音频设备齐全且公网可用时，才验收语音云连接和音频就绪。正常重启验收不要求断网；断网冷启动可在配置完成后另测。

## 发版文件

制作全量包的离线 APT 仓库时，从要支持的系统镜像分别导出 `/var/lib/dpkg/status`。在已更新 APT 索引的 Debian 13 ARM64 构建环境中运行 `bash tools/prepare-full-dbus.sh <全量包的debs目录> <status文件> [更多status文件...]`。脚本会下载 `python3-dbus`、Router 所需的 `socat`、摄像头工具 `v4l-utils` 及其配套库 `libv4l2rds0t64`，按各镜像清单补齐这些包的依赖，再生成 `debs/Packages`。打包前分别用每份清单和包内仓库离线验证安装依赖，随后重算包内 `SHA256SUMS` 与 ZIP 校验文件。

三个包使用同一版本的源码。源码 ZIP 继续放在 `packages/xgoduck_ai.zip`，同时更新其 `.sha256`。在 UNO Q 上用 `python3 tools/package-ai-app.py --release` 构建带版本号的 `.ard`，让每版有独立的安装目录。制作全量包时使用同一版本的 `.ard`，让包内脚本、安装目录和清单与新版本一致。 [预编译包制作与恢复](ai-chat.md#precompiled-uno-q-release)

构建完成后将 Release 的外层附件命名为 `xgoduck-ai-unoq.ard` 和 `xgoduck-ai-unoq-full.zip`。为这两个最终文件名分别生成 `.sha256` 后一起上传，把这一版设为最新 Release。只在发布时使用固定附件名，`.ard` 的内部目录仍带版本号。旧版可从 [GitHub Releases](https://github.com/LuwuDynamics/xgoduck_runtime_arduino/releases) 的对应标签获取。

仅修改文档而保留版本号时，也需同步源码 ZIP、`.ard` 内的文档及 `build-manifest.json`，并把新的 `.ard` 放入全量包。全量包外层 `INSTALL.txt` 与本仓库 `docs/full-install.txt` 保持一致，更新包内 `SHA256SUMS` 及三个成品的校验文件。保留原实机测试对应的旧校验值，并注明新归档是否重新做过实机安装；仅文档重打包不能表述为新归档已通过实机重装。
