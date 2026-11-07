# 舵机图示标定 / Illustrated servo centering

在 `http://<板子IP>:9527/calibrate.html` 打开标定页。页面根据浏览器语言显示中文或英文，也可手动切换。

1. 将小鸭安放在桌面上，或放入标定架。打开页面会卸力，不需要让它自行站立。
2. 按头颈（30–34）、左腿（10–14）、右腿（20–24）逐区操作。左右以小鸭自身为准，正面照片中左腿位于画面右侧。
3. 看实物照片里的白色 ID 牌找到关节。对照该 ID 的摆位图，用手将关节调整到零位，再点击 **Set center / 设置中位**（旧版按钮名为 Set center & hold）。页面确认“已设中位”后，继续下一个关节；这个提示确认的是中位设置，不代表已确认舵机上力。
4. 橙黄色部件或圆圈标示要调整的关节，绿色虚线表示对齐方向，红色半透明部件和箭头举例说明如何转回零位。零位的连杆为水平或竖直；侧面的大腿与小腿呈直角，不是把整条腿拉直。ID 34 的嘴自然合拢即可，不要挤压外壳。
5. 照片和摆位图可点击放大，再点“关闭大图”或按 Esc 返回。大图仍在当前页面内，不会停止标定心跳。
6. 全部完成后点击 **Unload all / 全部卸力**，再点 **Exit / 退出**。中位保存在舵机内，卸力不清除中位。本轮进度只统计这次已设中位的关节；卸力或重新打开页面后计数归零。如需重做，卸力后摆正该关节，再设中位。

切到别的应用、标签页或离开页面时，沿用原来的退出卸力逻辑；回来后需点击 **Unload all** 重新进入。无反馈的 ID 不可点击，不影响其他在线关节。网络状态读取失败时暂停设置，恢复反馈后可继续；失败的请求会显示在对应 ID 下。

图示来自[官方装配指南](https://www.xgorobot.com/duck-developer-guide/06-assembly.html)和 [XGO Duck URDF](https://github.com/LuwuDynamics/xgoduck_rl/tree/326d77a1122870bdefa2c36403937502c958e69c/src/mjlab_microduck/robot/xgoduck)。照片保留原来的白色编号牌；摆位图按 URDF 的零位渲染。URDF 没有嘴部活动关节，34 号图片只标出自然闭嘴的参考位置。请对照实际装配，不要根据示意图强行扭动。完整来源、模型提交及文件校验值在 [provenance/calibration-guide.json](../provenance/calibration-guide.json)。

## English

Open `http://<board-ip>:9527/calibrate.html`. The page follows the browser language (Chinese or English), with a manual toggle.

Rest Duck securely on a table or in a calibration stand. Entering the page unloads all servos. Work through **Head & neck (30–34)**, **Left leg (10–14)** and **Right leg (20–24)**; left/right are from Duck’s perspective. Find the white ID tag in the real photo, position that joint to match its picture, then click **Set center** (formerly Set center & hold). Once **Center set** appears, move on to the next joint. This confirms calibration, not the servo’s torque state.

Orange parts/circles identify the joint. Green dashed lines show alignment; red ghosts/arrows illustrate returning an offset part to zero. The zero pose has horizontal and vertical links and cannot stand normally. In the side view, the upper and lower leg form a right angle. Close the mouth gently for ID 34. Images enlarge within the same page, so the calibration heartbeat continues.

Finish with **Unload all**, then **Exit**. Centers remain saved in the servos; the counter shows centers set during this session. Unloading or reopening resets the count. To redo a center, unload first, reposition that joint and set it again. Leaving or hiding the page retains the existing automatic exit/unload behavior. Missing servo feedback disables only that ID; failed status reads disable centering until fresh feedback returns.

The photos retain the original ID tags from the official guide. Pose drawings use the URDF’s zero configuration; ID 34 is a closed-mouth reference because the model has no articulated jaw. Check actual assembly before applying the reference pose.

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
