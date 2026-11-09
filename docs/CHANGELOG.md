# XGO Duck AI 更新记录 / Changelog

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
