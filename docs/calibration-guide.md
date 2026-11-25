# 统一标定 / Unified calibration

在 `http://<板子IP>:9527/calibrate.html` 打开标定页。页面支持中文和英文。

1. 扶稳小鸭，等页面确认舵机卸力，且 15 个舵机均在线。按四张参考图手动摆位，拨开可能被夹住的线束。点 **标定 15 个舵机**，等待写入确认。
2. 让脖子与躯干平行，点 **标定 30 号舵机**。看到中位已保存后，点 **全部卸力**。
3. 按 [IMU 摆放图](imu-calibration.md) 扶稳小鸭。保持卸力，点一次 **标定 IMU**，保持静止直到页面显示 **IMU 标定已保存**。如出现自动重试提示，继续扶稳即可。
4. 把小鸭放到平坦处扶稳，点页底的 **默认姿势**。舵机上力后对照实拍。姿势不对或检查结束时，扶稳并点 **全部卸力** 或 **退出**。

舵机中位写入后保存在舵机里。如果批量写入失败，部分设置可能已写入；保持摆位，检查提示后重新统一标定。退出或切到后台会结束标定和默认姿势测试；测试断线后最多保持两秒。

逐个验证舵机的安装位置，可用 **舵机设置 → 单舵机运动控制**。摆位照片来自 XGO Duck 主分支；来源和文件校验见 [calibration-guide.json](../provenance/calibration-guide.json)。

## English

Open `http://<board-ip>:9527/calibrate.html` to use unified calibration.

1. Support Duck. Wait for the page to confirm that the servos are unloaded and make sure all 15 servos are online. Match the four reference photos and move loose cables clear of the joints. Click **Calibrate all 15 servos** and wait for confirmation.
2. Position the neck parallel to the torso and click **Calibrate ID 30**. When the center is saved, click **Unload all**.
3. Position Duck according to the [IMU posture guide](imu-calibration.md). Keep the servos unloaded, click **Calibrate IMU** once, and hold still through any automatic retries until the page confirms the save.
4. Support Duck on a level surface and click **Default pose** at the bottom of the page. Wait for the servos to engage and compare the pose with the photo. Support Duck and click **Unload all** or **Exit** if the pose looks wrong or when you finish.

Servo centers stay saved in the servos. If a write fails, some settings may already have changed. Keep the calibration pose, check the error message, and retry unified calibration. Leaving or hiding the page ends the test and releases torque. A disconnected default-pose test expires after two seconds.

Use the **Single-servo motion control** under **Servo setup** to verify individual joint locations. The reference photos come from the XGO Duck master branch; see [calibration-guide.json](../provenance/calibration-guide.json).

## 本地检查 / Local verification

这些检查只使用本机文件和回环模拟器，不连接或操作实体鸭子。The following checks use local files and a loopback simulator only.

```sh
python3 -m unittest discover -s tests -p 'test_calibration_page.py' -v
node --test tests/calibration-page.test.cjs
```

浏览器检查需要可用的 `playwright` Node 包，默认使用本机 Chrome；使用已下载的 Playwright Chromium 时设置 `PLAYWRIGHT_CHANNEL=chromium`。This is a development dependency, not a board/runtime dependency.

```sh
node tests/calibration-mock.cjs
```

终端会打印本地预览地址。该预览仅模拟反馈和标定结果，不能代替实机验收。The simulator prints its local preview URL; its results are not physical calibration acceptance.
