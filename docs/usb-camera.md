# USB 摄像头与音频页面

Duck 的主控制页（9527 端口）包含 USB camera 面板，点击开启后显示连续 MJPEG 视频流，可以保存 JPEG 照片。请求采集 640×480 / 30 fps，页面显示的是摄像头实测采集帧率，实际速度受设备曝光及网络影响。页面在后台会断开预览，20 秒无人观看后后台释放摄像头。

顶部的 **Audio** 标签进入 `/audio.html`，采用英文设置界面，提供 0–100% USB 扬声器音量设置（Apply volume 立即生效）、扬声器测试旋律、5/10/30 秒 USB 麦克风录音、录音回放和下载。豆包 XGO-Duck 音色测试放在折叠区域。主控制页的摄像头按钮和提示也统一为英文。音乐上传保留在 9528 诊断页。音频采集和扬声器播放发生在板子上；页面的 audio 播放器则在当前手机/电脑上试听。

## 服务结构

- `assets/media-camera.js`：主控制页的视频流与拍照。
- `assets/audio.html`、`assets/media-audio.js`、`assets/media.css`：音频标签页与样式。
- `python/media_proxy.py`：9527 上的 `/api/media/*` 异步代理，转发至板子上的独立音视频服务。Docker 内自动发现默认网关，不依赖 Wi-Fi IP；可用 `DUCK_MEDIA_URL` 覆盖地址。
- `tools/device-test/`：独立音视频后台，作为用户级 `duck-device-test.service` 在 9528 端口运行。保留原测试页作为诊断入口；主固件的音视频功能依赖此服务。

采集和语音合成不会运行在策略控制线程内。摄像头按 `/dev/v4l/by-id/*-video-index0` 选择，USB 声卡通过 `/proc/asound/card*/usbid` 自动识别，不固定设备编号。

## 部署

在板子上将 `tools/device-test/` 内容复制到 `~/duck-device-test/`，把其中的 systemd 单元复制到 `~/.config/systemd/user/`，运行：

```sh
systemctl --user daemon-reload
systemctl --user enable --now duck-device-test
```

主固件同步 `assets/`、`python/main.py`、`python/media_proxy.py` 和 Python 依赖后，重启 Linux 应用即可，无需重新烧录 STM32。部署时保留 `data/zero_pos.json` 校准文件及现有其他代码。

豆包密钥只在板子用户目录 `~/.config/duck-device-test/doubao.json` 中保存，权限为 0600。字段为 `api_key`、`speaker`、`resource_id`、`model`；不要将真实配置提交到仓库。当前使用音色 `S_LxgNAbAM1`、资源 `seed-icl-2.0`、模型 `seed-tts-2.0-standard`。修改配置后重启音视频服务。

USB 摄像头/音频需要手动打开 **USB Host** 开关（Audio、Gamepad 或手机版 Settings → General）。关闭开关恢复 Device 模式。开机默认 Device，启动 Duck 应用不会改变 USB 模式，也不保存 Host 为开机设置。通过 Wi-Fi 切换；切换会断开当前 USB 设备或 USB 电脑连接。

安装或迁移：

```sh
sudo systemctl disable --now duck-usb-host.service
sudo install -m 0755 tools/device-test/usb-role.sh /usr/local/sbin/duck-usb-role
sudo install -m 0644 tools/device-test/duck-usb-device.service /etc/systemd/system/
sudo install -m 0644 tools/gamepad/duck-gamepad.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now duck-usb-device.service
sudo systemctl restart duck-gamepad.service
```

现有本地 Unix socket 工作进程只允许写入固定的 USB mode 节点，网页只接受同源带专用请求头的布尔开关值。没有新增 TCP 服务，不改变舵机、策略或 Bridge 频率。用 `sudo cat /sys/kernel/debug/usb/4e00000.usb/mode` 检查实际模式。

## 验证

```sh
# 在包含 FastAPI 和 httpx 的应用环境中
PYTHONPATH=python python3 tools/test-media-proxy.py
python3 -m unittest discover -s tools/device-test -p 'test_*.py'
```

代理测试覆盖允许的路径、同源操作、请求大小限制、二进制上传及视频流转发。实机验证还应确认摄像头开启时 `/api/status` 的反馈和策略频率持续更新，以及音频页面的录音、音乐和 TTS 可用。普通 GET 请求不会启用摄像头、录音或舵机。
