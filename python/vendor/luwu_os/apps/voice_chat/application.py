# -*- coding: utf-8 -*-
"""语音聊天应用核心 (状态机 + 消息处理)

架构决策 (对比 xiaozhi_test 的改造):
  - 单 asyncio 事件循环为主, 仅音频采集/播放与唤醒词用专职线程
  - 协议层用 RIG-Omni 的 MQTT+UDP (AES-CTR) 实现
  - 唤醒词复用 apps/ai/wakeword_manager.py (小陆同学 ONNX 模型)
  - IoT 命令在 executor 线程执行, 不阻塞消息循环
  - TTS 播完判定基于音频队列状态, 不靠 sleep 猜时序
"""

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from audio_codec import AudioCodec, AudioConfig
from mqtt_udp_protocol import MqttUdpProtocol, ListeningMode, AbortReason
from ota import OtaManager, STATE_DIR, get_device_id, get_client_id, migrate_state_file
from mcp_server import McpServer
from vision import VisionClient
from iot.thing import ThingManager
from iot.robot_dog import RobotThing

# 自动打断(端侧AEC)开关持久化 (同 OTA 凭据的状态目录惯例); 仅旧 xiaozhi 命名时迁移
AEC_STATE_FILE = migrate_state_file(STATE_DIR / "voice_chat_aec.json", ["xiaozhi_aec.json"])

# ---- 通用 MCP 工具 (对齐 RIG-Omni mcp_server.cc) 的系统资源 ----
LUWU_ROOT = os.environ.get("LUWU_ROOT", "/opt/luwu-os")
VOLUME_INI = Path(LUWU_ROOT) / "apps/settings/volume.ini"
LANGUAGE_INI = Path(LUWU_ROOT) / "configs/language.ini"
VERSION_INI = Path(LUWU_ROOT) / "version.txt"

# 会话提示音 (非打断模式即 AEC 关闭时播放; 唤醒反馈音不分模式)
DING_WAV = str(Path(LUWU_ROOT) / "assets/music/ding.wav")  # 唤醒反馈 / 回答完成
SEND_WAV = str(Path(LUWU_ROOT) / "assets/music/send.wav")  # 进入思考 (LLM 已收到)

# 复用 apps/ai 的唤醒词检测
AI_APP_DIR = str(Path(LUWU_ROOT) / "apps/ai")
if AI_APP_DIR not in sys.path:
    sys.path.insert(0, AI_APP_DIR)
try:
    from wakeword_manager import WakeWordManager
except Exception as e:
    WakeWordManager = None
    print(f"[app] 唤醒词模块导入失败: {e}")


class DeviceState:
    IDLE = "idle"
    CONNECTING = "connecting"
    LISTENING = "listening"
    SPEAKING = "speaking"


# 同进程唯一活跃实例: 新实例 start 前自动强拆旧实例的 MQTT/唤醒词/音频。
# 场景: 配网流程重新拉起聊天 (如 ai_chat_pro 后台线程重跑创建新实例) 时,
# 旧实例若不关闭, 其 MQTT 客户端成孤儿 — 服务端凭据按 MAC 签发, 新旧实例
# 共用同一 client_id 互踢 → MQTT 无限断连循环、服务器下行丢失、无法聊天
_active_app = None


class Application:
    def __init__(self, config: dict):
        self.config = config
        self.state = DeviceState.IDLE
        self.keep_listening = True   # 会话内保持连续对话
        self.aborted = False
        self.current_text = ""

        self.loop = None
        self.audio = None
        self.protocol = None
        self.wakeword = None
        self.ambient = None
        self.mcp = None
        self.vision = None
        self._listen_timeout_task = None  # 倾听超时自动回待命任务
        self.aec_enabled = self._load_aec_state()  # 自动打断开关 (持久化)
        self.bound = None          # 服务器绑定状态 (OTA 探测), 供 UI 展示二维码
        self.status_callback = None  # 可选: (state, text) 供 UI 订阅
        self.emotion_callback = None  # 可选: (emotion_str) 供 UI 切换表情 (同 Omni SetEmotion)
        self.photo_callback = None  # 可选: (pil_image) 拍照预览展示到屏幕
        self.ambient_callback = None  # 可选: (event | None) 待机声音标签/表情
        self._ota_stop = threading.Event()
        self._closing = False
        self._tasks = set()
        self._tts_generation = 0

    def _spawn(self, coroutine):
        if self._closing:
            coroutine.close()
            return None
        task = asyncio.ensure_future(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ================= 启动 =================
    async def start(self):
        self._closing = False
        self.loop = asyncio.get_running_loop()
        print("[app] 启动语音聊天应用...")

        # 跨实例守护: 上一个实例未 shutdown 就被替换时先强拆其关键资源
        global _active_app
        if _active_app is not None and _active_app is not self:
            old, _active_app = _active_app, self
            print("[app] 检测到上一个聊天实例未关闭, 强拆其 MQTT/唤醒词/音频")
            # 旧实例的事件循环可能已停, 不能 await 其协程, 只做无循环依赖的同步强拆
            old_wakeword, old.wakeword = old.wakeword, None
            old_protocol, old.protocol = old.protocol, None
            old_audio, old.audio = old.audio, None
            old._stop_ambient()
            if old_wakeword is not None:
                try:
                    old_wakeword.stop()
                except Exception:
                    pass
            if old_protocol is not None:
                try:
                    old_protocol.force_stop_mqtt()
                except Exception:
                    pass
            if old_audio is not None:
                try:
                    old_audio.close()
                except Exception:
                    pass
        else:
            _active_app = self

        self._stop_ambient()

        # 同实例重复 start: 先停旧 protocol 的 MQTT (幂等, 防孤儿连接)
        if self.protocol is not None:
            try:
                await self.protocol.shutdown()
            except Exception:
                pass
            self.protocol = None

        # 1. 音频 (重复启动时先清理旧实例)
        if self.audio is not None:
            try:
                self.audio.close()
            except Exception:
                pass
            self.audio = None
        audio_cfg = self.config.get("audio", {})
        self.audio = self.config.get('audio_factory', AudioCodec)(
            output_rate=audio_cfg.get("output_sample_rate", AudioConfig.OUTPUT_SAMPLE_RATE),
            frame_ms=audio_cfg.get("frame_duration", AudioConfig.FRAME_DURATION_MS),
            aec_ref_delay_ms=audio_cfg.get("aec_ref_delay_ms", 120),
            input_device=audio_cfg.get("input_device"),
            output_device=audio_cfg.get("output_device"),
            input_latency=audio_cfg.get("input_latency"),
            input_channels=audio_cfg.get("input_channels", 1),
            output_latency=audio_cfg.get("output_latency"),
            prebuffer_ms=audio_cfg.get("prebuffer_ms"))
        self.audio.output_transform = self.config.get("output_transform", lambda pcm: pcm)
        self.audio.input_transform = self.config.get("input_transform", lambda pcm: pcm)
        self.audio.input_downmix = self.config.get('input_downmix')
        self.audio.output_observer = self.config.get('output_observer')
        self.audio.output_reset_observer = self.config.get('output_reset_observer')
        self.audio.on_audio_frame = self._on_capture_frame
        # 首次运行(无持久化状态)时以 config.enable_aec 为准 (缺省关闭), 之后小程序开关优先
        if not AEC_STATE_FILE.exists():
            self.aec_enabled = bool(self.config.get("enable_aec", False))
        # 端侧 AEC 生效条件: 开关打开 且 AEC 库可用 (否则退回播放期关麦的旧行为)
        if self.aec_enabled and self.audio.aec.available:
            self.audio.aec_enabled = True
            print(f"[app] 自动打断已开启 (后端: {self.audio.aec.backend})")
        elif self.aec_enabled:
            print("[app] 自动打断开关已开但 AEC 库不可用, "
                  "需安装 aec-audio-processing 或 speexdsp")
        self.audio.start()

        # 2. IoT
        thing_manager = ThingManager.get_instance()
        if self.config.get("enable_robot", True):
            factory = self.config.get("robot_factory")
            robot_port = self.config.get("robot_serial", "/dev/ttyAMA0")
            thing_manager.add_thing(factory() if factory else RobotThing(port=robot_port))
            print("[app] IoT 机器人设备已注册")
        self.mcp = McpServer(thing_manager, self_provider=self)

        # 3.5 视觉分析 (对齐 Omni: 服务端 MCP initialize 下发 url/token 后可用)
        if self.config.get("enable_camera", True):
            factory = self.config.get("vision_factory", VisionClient)
            self.vision = factory(get_device_id(), get_client_id())
            if self.vision.camera_available:
                print("[app] 相机可用, 视觉分析待服务器下发地址")
            else:
                self.vision = None

        # 3. OTA 激活 / 凭据获取 (阻塞操作放线程)
        self._ota_stop.clear()
        if 'provisioned_mqtt' in self.config:
            mqtt_settings = self.config['provisioned_mqtt']
            self.bound = self.config.get('provisioned_bound')
        else:
            ota = OtaManager(self.config.get("ota_url", ""),
                             on_activation_code=self._on_activation_code, stop_event=self._ota_stop)
            mqtt_settings = await self.loop.run_in_executor(
                None, ota.provision, self.config.get("mqtt"))
            self.bound = ota.bound
        if not mqtt_settings.get("endpoint"):
            print("[app] 无可用 MQTT 凭据, 请配置 ota_url 或 mqtt 段后重启")
            self._notify_status(DeviceState.IDLE, "未配置服务器")
            # 释放音频资源, 允许后续重新 start()
            try:
                self.audio.close()
            except Exception:
                pass
            self.audio = None
            return False

        # 4. 协议
        self.protocol = MqttUdpProtocol(mqtt_settings, self.loop)
        self.protocol.on_incoming_json = self._on_incoming_json
        self.protocol.on_incoming_audio = self._on_incoming_audio
        self.protocol.on_audio_channel_opened = self._on_audio_channel_opened
        self.protocol.on_audio_channel_closed = self._on_audio_channel_closed
        self.protocol.on_network_error = self._on_network_error
        self.protocol.on_credentials_expired = self._refresh_credentials

        await self.protocol.start()  # MQTT 常驻连接 (指数退避自动重连)

        # 5. 待机声音分类与唤醒词共用一条麦克风流
        self._init_ambient()
        self._init_wakeword()
        self._resume_ambient()

        self._notify_status(DeviceState.IDLE, "待命")
        print("[app] 启动完成")

        if self.config.get("auto_start_chat", False):
            self._spawn(self.start_chat())
        return True

    def _init_wakeword(self):
        if self.wakeword is not None:  # 重复 start: 停旧检测线程防双实例抢麦克风
            try:
                self.wakeword.stop()
            except Exception:
                pass
            self.wakeword = None
        if WakeWordManager is None or not self.config.get("enable_wakeword", True):
            print("[app] 唤醒词功能未启用")
            return
        ww_cfg = self.config.get("wakeword", {})
        model_path = ww_cfg.get("model_path") or None
        if model_path is None and self._lang() == "en":
            # 英文系统用 Hi Luka 模型 (同 apps/ai 的语言分流)
            try:
                from wakeword_manager import EN_MODEL_PATH
                model_path = EN_MODEL_PATH
            except Exception as e:
                print(f"[app] 英文唤醒词模型获取失败: {e}")
        self.wakeword = WakeWordManager(
            on_wakeup=self._on_wakeword_detected,
            model_path=model_path,
            threshold=ww_cfg.get("threshold"),
            on_audio=self._on_idle_audio,
            device=ww_cfg.get("device"),
            input_transform=ww_cfg.get("input_transform"),
            input_scale=ww_cfg.get("input_scale"),
            stride_duration=ww_cfg.get("stride_duration"),
            cooldown_sec=ww_cfg.get("cooldown_sec"),
            input_channels=ww_cfg.get('input_channels', 1),
            input_latency=ww_cfg.get('input_latency', .32),
        )
        if self.wakeword.is_available():
            self.wakeword.start()
            print("[app] 唤醒词检测已启动")
        else:
            print("[app] 唤醒词模型不可用")
            self.wakeword = None

    # ================= 待机声音分类 =================
    def _init_ambient(self):
        cfg = self.config.get("ambient_sound", {})
        if not cfg.get("enabled", True):
            return
        try:
            from ambient_sound import AmbientSoundDetector
            detector = AmbientSoundDetector(cfg, lambda event: self._on_ambient_result(detector, event),
                                            eligible=self._ambient_eligible)
            self.ambient = detector
            detector.start()
        except Exception as e:
            print(f"[ambient] 初始化失败，保留正常聊天: {e}")

    def _ambient_eligible(self):
        return (self.state == DeviceState.IDLE and self.audio is not None
                and self.audio.is_playback_idle())

    def _on_idle_audio(self, pcm):
        detector = self.ambient
        if detector is not None:
            detector.feed(pcm)

    def _on_ambient_result(self, detector, event):
        # 工作线程只投递事件；状态判断、UI 转发在应用事件循环中进行。
        if self.loop is not None and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self._deliver_ambient, detector, event)

    def _deliver_ambient(self, detector, event):
        if (detector is self.ambient and detector is not None
                and detector.is_current(event["epoch"]) and self._ambient_eligible()
                and self.ambient_callback):
            # UI 还需校验当前实例，防止重启后的旧信号覆盖新画面。
            self.ambient_callback(dict(event, source=detector))

    def _resume_ambient(self):
        if self.ambient is None:
            return
        self.ambient.set_enabled(True)
        if self.wakeword is None:
            try:
                self.ambient.open_input()
            except Exception as e:
                print(f"[ambient] 待机采音不可用: {e}")

    def _pause_ambient(self):
        if self.ambient is not None:
            self.ambient.set_enabled(False)
        if self.ambient_callback:
            self.ambient_callback(None)

    def _stop_ambient(self):
        detector, self.ambient = self.ambient, None
        if detector is not None:
            detector.close()
        if self.ambient_callback:
            self.ambient_callback(None)

    # ================= 唤醒词 =================
    @staticmethod
    def _lang() -> str:
        try:
            from libs.i18n import get_lang
            return get_lang()
        except Exception:
            return "cn"

    def _wake_word(self) -> str:
        """唤醒词文案按语言分流: cn=小陆同学, en=Hi Luka (对应不同 ONNX 模型)"""
        return "Hi Luka" if self._lang() == "en" else "小陆同学"

    def _on_wakeword_detected(self, *args):
        """唤醒词回调 (来自检测线程)"""
        print("[app] 检测到唤醒词")
        if self.loop is None or self._closing:
            return
        self.loop.call_soon_threadsafe(lambda: self._spawn(self._handle_wakeword()))

    def _play_wake_sound(self):
        """Local wake acknowledgement; products can supply a fixed cue."""
        self.audio.play_wav(DING_WAV)

    async def _handle_wakeword(self):
        if self.state == DeviceState.SPEAKING:
            await self.abort_speaking(AbortReason.WAKE_WORD_DETECTED)
            if self.audio is not None:
                self._play_wake_sound()
        elif self.state == DeviceState.IDLE:
            await self.start_chat(wake_word=self._wake_word())
        else:
            return

    def _on_activation_code(self, code, message):
        """OTA 验证码提示钩子 (可接屏幕/TTS)"""
        print(f"[app] 激活验证码: {code} — {message}")

    async def _refresh_credentials(self):
        """MQTT 连续被拒 (凭据疑似失效) → 回 OTA 重新获取凭据

        返回新 settings dict (含 endpoint) 或 None (OTA 不可达, 继续用旧凭据重试)
        """
        ota_url = self.config.get("ota_url", "")
        if not ota_url:
            return None
        ota = OtaManager(ota_url, stop_event=self._ota_stop)
        new_settings = await self.loop.run_in_executor(
            None, ota.provision, self.config.get("mqtt"))
        if new_settings.get("endpoint"):
            self.bound = ota.bound
            print(f"[app] OTA 凭据已刷新 (endpoint={new_settings.get('endpoint')})")
            return new_settings
        print("[app] OTA 凭据刷新失败, 继续用旧凭据重试")
        return None

    # ================= 会话流程 =================
    async def start_chat(self, wake_word=None):
        """IDLE → CONNECTING → LISTENING"""
        if self._closing or self.state != DeviceState.IDLE or self.protocol is None:
            return
        self._set_state(DeviceState.CONNECTING)
        if self.wakeword:
            self.wakeword.stop()   # 会话期间暂停唤醒词, 释放麦克风

        if wake_word and self.audio is not None:
            # 无屏设备先用本地声音确认唤醒，再等待云端握手。麦克风保持
            # 关闭，待提示音排空后才进 LISTENING，避免清队列截断提示音。
            self._play_wake_sound()
            await asyncio.to_thread(self.audio.wait_playback_done, 2.0)
            if self._closing or self.state != DeviceState.CONNECTING:
                return

        if not self.protocol.is_audio_channel_opened():
            ok = await self.protocol.open_audio_channel()
            if not ok:
                self._notify_status(DeviceState.IDLE, "连接失败")
                self._back_to_idle()
                return

        if wake_word and self.config.get("send_wake_word_data", False):
            # 同 Omni CONFIG_SEND_WAKE_WORD_DATA: 关闭时不上报唤醒词,
            # 直接进倾听 (服务端不回开场问候)
            await self.protocol.send_wake_word_detected(wake_word)
        self.keep_listening = True
        await self.protocol.send_start_listening(ListeningMode.AUTO_STOP)
        self._set_state(DeviceState.LISTENING)

    async def toggle_chat(self):
        """手动触发 (按键/外部命令, 同 Omni ToggleChatState):
        IDLE → 直接进倾听 (不上报唤醒词); SPEAKING → 打断并转倾听; LISTENING → 结束会话"""
        if self.state == DeviceState.IDLE:
            await self.start_chat()
        elif self.state == DeviceState.SPEAKING:
            await self.abort_speaking(AbortReason.USER_INTERRUPTION, relisten=True)
        elif self.state == DeviceState.LISTENING:
            await self.stop_chat()

    async def stop_chat(self):
        """结束会话回 IDLE"""
        if self.protocol and self.protocol.is_audio_channel_opened():
            await self.protocol.send_stop_listening()
            await self.protocol.close_audio_channel()
        self._back_to_idle()

    def _back_to_idle(self):
        if self._closing:
            return
        self._tts_generation += 1
        self._set_state(DeviceState.IDLE)
        self.audio.set_capture_enabled(False)
        self.audio.close_input()          # 释放麦克风
        self.audio.clear_play_queue()
        if self.wakeword and not self.wakeword._running:
            self.wakeword.start()         # 恢复唤醒词
        self._resume_ambient()
        self._notify_status(DeviceState.IDLE, "待命")

    async def abort_speaking(self, reason, relisten=False):
        if self.aborted:
            return
        self.aborted = True
        self._tts_generation += 1
        self.audio.clear_play_queue()
        if self.protocol:
            await self.protocol.send_abort_speaking(reason)
        # 打断后立即转倾听: 唤醒词打断默认转; 手动打断由 relisten 指定 (同 Omni 打断后继续对话)
        if (relisten or reason == AbortReason.WAKE_WORD_DETECTED) and \
                self.protocol.is_audio_channel_opened():
            await asyncio.sleep(0.1)
            await self.protocol.send_start_listening(ListeningMode.AUTO_STOP)
            self._set_state(DeviceState.LISTENING)
        else:
            self._back_to_idle()

    def _set_state(self, state):
        if self._closing:
            return
        if self.state == state:
            return
        previous_state = self.state
        self.state = state
        if state != DeviceState.IDLE:
            self._pause_ambient()
        print(f"[app] 状态: {state}")
        self._cancel_listen_timeout()
        if state == DeviceState.LISTENING:
            # A full-duplex microphone still holds delayed samples of the last
            # rendered audio after the DAC drains. Keep their echo reference
            # across a normal reply boundary. An actual interruption or a
            # playback timeout still uses the normal reset/flush path.
            preserve_reference = (previous_state == DeviceState.SPEAKING
                                  and self.audio.aec_enabled
                                  and self.audio.is_playback_idle())
            self.audio.clear_play_queue(reset_aec=not preserve_reference)
            self.audio.open_input()
            self.audio.set_capture_enabled(True)
            self._emit_emotion("listen")          # 同 Omni: LISTENING → listen
            # 倾听超过 60s 无结果 → 自动回待机 (服务端 VAD 失效/无人说话的兜底)
            self._listen_timeout_task = self._spawn(self._listen_timeout())
        elif state == DeviceState.SPEAKING:
            if self.audio.aec_enabled:
                # 端侧 AEC: 播放期保持采集上送, 支持语音实时打断
                self.audio.open_input()
                self.audio.set_capture_enabled(True)
            else:
                self.audio.set_capture_enabled(False)  # 防自听回声(无AEC)
        elif state == DeviceState.CONNECTING:
            self._emit_emotion("thinking")
        elif state == DeviceState.IDLE:
            self._emit_emotion("neutral")         # 同 Omni: IDLE → neutral

    def _cancel_listen_timeout(self):
        if self._listen_timeout_task and not self._listen_timeout_task.done():
            self._listen_timeout_task.cancel()
        self._listen_timeout_task = None

    async def _listen_timeout(self):
        """倾听超过 60 秒仍未进入下一状态 → 自动结束会话回待机"""
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            return
        if self.state == DeviceState.LISTENING:
            print("[app] 倾听超过 60s, 自动回待机")
            await self.stop_chat()

    def _emit_emotion(self, emotion):
        """表情事件 → UI (由服务器 llm/tts/alert 消息或状态切换触发)"""
        if emotion and self.emotion_callback:
            try:
                self.emotion_callback(emotion)
            except Exception:
                pass

    def _notify_status(self, state, text):
        if self._closing:
            return
        self.current_text = text
        if self.status_callback:
            try:
                self.status_callback(state, text)
            except Exception:
                pass

    # ================= 音频上送 =================
    def _on_capture_frame(self, opus_bytes):
        """采集线程回调: 编码后的 opus 帧 → UDP 上送

        timestamp 恒为 0: 端侧已完成 AEC, 服务端不再重复消回声
        (服务端仅用 timestamp>0 门控自己的 AEC, VAD 打断不受影响)
        """
        if self._closing:
            return
        if self.state != DeviceState.LISTENING and \
                not (self.state == DeviceState.SPEAKING and self.audio.aec_enabled):
            return
        if self.protocol and self.protocol.is_audio_channel_opened():
            asyncio.run_coroutine_threadsafe(
                self.protocol.send_audio(opus_bytes, 0), self.loop)

    # ================= 协议回调 =================
    async def _on_audio_channel_opened(self):
        if self._closing:
            return
        print("[app] 音频通道已打开")
        # 服务器输出参数同步到解码器
        self.audio.update_server_params(
            sample_rate=self.protocol.server_sample_rate,
            frame_duration=self.protocol.server_frame_duration)
        # 上报 IoT 描述符与状态
        thing_manager = ThingManager.get_instance()
        await self.protocol.send_iot_descriptors(thing_manager.get_descriptors_json())
        await self.protocol.send_iot_states(thing_manager.get_states_json())

    async def _on_audio_channel_closed(self):
        print("[app] 音频通道已关闭")
        self.keep_listening = False
        if self.state != DeviceState.IDLE:
            self._back_to_idle()

    def _on_network_error(self, message):
        print(f"[app] 网络错误: {message}")
        self.keep_listening = False
        if self.state != DeviceState.IDLE:
            self._back_to_idle()
        self._notify_status(DeviceState.IDLE, f"网络错误: {message}")

    def _on_incoming_audio(self, payload, timestamp):
        if not self._closing and self.state == DeviceState.SPEAKING:
            self.audio.write_opus(payload)

    def _on_incoming_json(self, data):
        if self._closing:
            return
        msg_type = data.get("type", "")
        if msg_type == "tts":
            self._handle_tts(data)
        elif msg_type == "stt":
            text = data.get("text", "")
            if text:
                print(f"[app] >> 用户: {text}")
                self._notify_status(self.state, text)
                # 播报中收到 STT = 服务端 VAD 已确认打断, 立即清掉残余 TTS 音频
                # (打断发生到服务端停发 TTS 有 RTT 窗口, 队列里会残留几百毫秒)
                if self.state == DeviceState.SPEAKING:
                    self.audio.clear_play_queue()
                # 同 Omni: 收到 STT 识别结果, 聆听中切思考表情
                if self.state == DeviceState.LISTENING:
                    self._emit_emotion("thinking")
                    if not self.audio.aec_enabled:
                        # 非打断模式: 进入思考 (LLM 已收到) 提示音
                        self.audio.play_wav(SEND_WAV)
        elif msg_type == "llm":
            # 同 Omni: llm 消息携带 emotion 字段 → 切换表情
            emotion = data.get("emotion")
            if isinstance(emotion, str) and emotion:
                self._emit_emotion(emotion)
        elif msg_type == "alert":
            # 同 Omni: alert 携带 status/message/emotion
            emotion = data.get("emotion")
            if isinstance(emotion, str) and emotion:
                self._emit_emotion(emotion)
            message = data.get("message", "")
            if message:
                self._notify_status(self.state, message)
        elif msg_type == "iot":
            self._spawn(self._handle_iot(data))
        elif msg_type == "mcp":
            self._spawn(self._handle_mcp(data))
        else:
            print(f"[app] 未知消息类型: {msg_type}")

    async def _handle_mcp(self, data):
        """处理服务器 MCP jsonrpc 请求 (initialize/tools/list/tools/call)

        相机类工具 (拍照+HTTP) 阻塞数秒, 由 handle_message_async 放 executor
        """
        payload = data.get("payload")
        method = payload.get("method", "?") if isinstance(payload, dict) else "?"
        msg_id = payload.get("id") if isinstance(payload, dict) else None
        print(f"[app] MCP 请求: {method}")
        reply = await self.mcp.handle_message_async(
            payload, self.loop.run_in_executor)
        if reply is not None and self.protocol:
            ok = await self.protocol.send_mcp_message(reply)
            # 诊断: 应答是否真的发出去 (排查工具结果不回导致服务端卡死)
            try:
                brief = json.dumps(reply, ensure_ascii=False)
                print(f"[mcp] 应答 {method} id={msg_id}: "
                      f"{'已发送' if ok else '发送失败'} {len(brief)} 字节 "
                      f"{brief[:120]}")
            except Exception:
                pass

    # ================= TTS =================
    def _is_server_tts_interrupt(self, data):
        """Recognize the deployed Xiaozhi cloud's existing abort packet.

        Normal stop carries the hello/channel session ID. Its abort handler
        instead carries a 32-hex reply ID. Keep unrecognized/legacy stop
        messages on the normal drain path: truncating every stop cuts replies.
        The MQTT gateway forwards these fields without rewriting them.
        """
        channel = getattr(self.protocol, "session_id", None)
        reply = data.get("session_id")
        return (data.get("state") == "stop" and isinstance(channel, str)
                and bool(channel) and isinstance(reply, str) and reply != channel
                and len(reply) == 32 and all(c in "0123456789abcdefABCDEF" for c in reply))

    def _stop_tts_from_server(self):
        started = time.monotonic()
        self.aborted = True
        self._tts_generation += 1
        self.audio.clear_play_queue()  # Also abort the PCM already queued in ALSA.
        if self.keep_listening and self.protocol.is_audio_channel_opened():
            # The server is already collecting the interruption. Do not send
            # another abort/listen:start that resets its VAD/preroll mid-phrase.
            self._set_state(DeviceState.LISTENING)
        else:
            self._back_to_idle()
        print(f"[app] TTS 打断: 已停止缓存和声卡播放，"
              f"本地处理={(time.monotonic()-started)*1000:.1f}ms")

    def _handle_tts(self, data):
        state = data.get("state", "")
        if state == "start":
            self._tts_generation += 1
            self.aborted = False
            # 只清未解码的 opus 残留, 保留刚入队的提示音 (send.wav 等)
            self.audio.clear_play_queue(clear_pcm=False,
                                        reset_aec=not self.audio.aec_enabled)
            if self.state in (DeviceState.IDLE, DeviceState.LISTENING):
                self._set_state(DeviceState.SPEAKING)
            # 部分服务器在 tts start 中携带 emotion (xiaozhi 协议)
            emotion = data.get("emotion")
            if isinstance(emotion, str) and emotion:
                self._emit_emotion(emotion)
        elif state == "stop":
            if self.state == DeviceState.SPEAKING:
                interrupted = self._is_server_tts_interrupt(data)
                print(f"[app] TTS stop: {'interrupt' if interrupted else 'complete'} "
                      f"generation={self._tts_generation}")
                if interrupted:
                    self._stop_tts_from_server()
                    return
                # 回合结束禁止 PLC 拖尾, 保证 is_playback_idle 及时判闲
                # (句间断供仍可垫, 见 audio_codec._maybe_plc_pad)
                self.audio.stop_plc()
                self._spawn(self._wait_tts_playback_done(self._tts_generation))
        elif state == "sentence_start":
            text = data.get("text", "")
            if text:
                print(f"[app] << 助手: {text}")
                self._notify_status(DeviceState.SPEAKING, text)

    async def _wait_tts_playback_done(self, generation=None):
        """等播放队列排空 (executor 内轮询, 不阻塞事件循环)"""
        if generation is None:
            generation = self._tts_generation
        await self.loop.run_in_executor(
            None, self.audio.wait_playback_done, 30.0)
        if (self.state != DeviceState.SPEAKING or self.aborted
                or generation != self._tts_generation):
            return
        if self.keep_listening and self.protocol.is_audio_channel_opened():
            await self.protocol.send_start_listening(ListeningMode.AUTO_STOP)
            self._set_state(DeviceState.LISTENING)
        else:
            self._back_to_idle()
        if not self.audio.aec_enabled:
            # 非打断模式: 回答完成提示音 (状态切换后再播, LISTENING/IDLE 会清队列)
            self.audio.play_wav(DING_WAV)

    # ================= 自动打断开关 (MCP self.set_aec, 对齐 RIG-Omni) =================
    @staticmethod
    def _load_aec_state() -> bool:
        """读持久化开关, 缺省关闭 (首次无状态文件时以 config.enable_aec 为准)"""
        try:
            if AEC_STATE_FILE.exists():
                return bool(json.loads(AEC_STATE_FILE.read_text()).get("enabled", False))
        except Exception as e:
            print(f"[app] AEC 状态读取失败: {e}")
        return False

    def _save_aec_state(self, enabled: bool):
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            AEC_STATE_FILE.write_text(json.dumps({"enabled": enabled}))
        except Exception as e:
            print(f"[app] AEC 状态保存失败: {e}")

    def mcp_get_device_status(self) -> dict:
        """self.get_device_status → 对齐 RIG-Omni 返回结构 (audio_speaker/screen/network/firmware_version)。

        附加 interrupt_mode / aec_backend 字段保留原 ai_chat_pro 语义 (小程序同步自动打断开关);
        后端按 Omni 结构消费时多余字段无碍, 而 LLM 也能读到真实设备状态。
        """
        mode = "device" if self.audio and self.audio.aec_enabled else "off"
        status = {
            "audio_speaker": {"volume": self._get_volume()},
            "screen": {},  # 树莓派无背光亮度/light-dark 主题 → 空对象 (同 Omni 保守行为)
            "network": self._wifi_info(),
            "firmware_version": self._luwuos_version(),
        }
        status["interrupt_mode"] = mode
        status["aec_backend"] = (self.audio.aec.backend
                                 if self.audio and self.audio.aec.available else "none")
        return status

    def mcp_set_aec(self, enable) -> dict:
        """self.set_aec → 开/关自动打断 (立即生效 + 持久化, 同 Omni 语义)"""
        want = bool(int(enable))
        if want and (self.audio is None or not self.audio.aec.available):
            raise RuntimeError("AEC库不可用, 需安装 aec-audio-processing 或 speexdsp")
        self.aec_enabled = want
        if self.audio is not None:
            self.audio.aec_enabled = want
            if self.state == DeviceState.SPEAKING:
                # 播报中切换: 开→继续采集; 关→停止采集防自听回声
                self.audio.set_capture_enabled(want)
                if want:
                    self.audio.open_input()
        self._save_aec_state(want)
        print(f"[app] 自动打断已{'开启' if want else '关闭'}")
        return {"interrupt_mode": "device" if want else "off"}

    # ================= 通用 MCP 工具 (对齐 RIG-Omni mcp_server.cc) =================
    @staticmethod
    def _read_text(path, default=""):
        try:
            return Path(path).read_text().strip()
        except Exception:
            return default

    def _get_volume(self) -> int:
        """读取系统音量 (0-100), 与 apps/settings 共用 volume.ini"""
        try:
            return int(self._read_text(VOLUME_INI, "50"))
        except Exception:
            return 50

    def _wifi_info(self) -> dict:
        """网络信息: type/ssid/signal (signal 按 RSSI 分级, 对齐 Omni)"""
        try:
            ssid = subprocess.run(
                ["iwgetid", "-r"], capture_output=True, text=True,
                timeout=3).stdout.strip()
        except Exception:
            ssid = ""
        signal = ""
        try:
            out = subprocess.run(
                ["iw", "dev", "wlan0", "link"], capture_output=True,
                text=True, timeout=3).stdout
            for line in out.splitlines():
                if "signal:" in line:
                    signal = line.split(":", 1)[1].strip()
                    break
        except Exception:
            pass
        dbm = None
        try:
            dbm = int(signal.split()[0])
        except Exception:
            dbm = None
        if dbm is None:
            sig = "" if not ssid else "medium"
        else:
            sig = "strong" if dbm >= -60 else ("medium" if dbm >= -70 else "weak")
        return {"type": "wifi", "ssid": ssid, "signal": sig}

    def _screen_resolution(self) -> tuple:
        """读取 framebuffer 分辨率, 拿不到时回退 320x240"""
        for fb in ("fb0", "fb1"):
            try:
                vs = self._read_text(f"/sys/class/graphics/{fb}/virtual_size", "")
                w, h = vs.split(",")
                return int(w), int(h)
            except Exception:
                continue
        return 320, 240

    def _luwuos_version(self) -> str:
        try:
            return str(json.loads(self._read_text(VERSION_INI, "{}")).get("v", ""))
        except Exception:
            return ""

    def _disk_total_bytes(self) -> int:
        """根分区总字节, 近似映射 Omni 的 flash_size"""
        try:
            st = os.statvfs("/")
            return st.f_blocks * st.f_frsize
        except Exception:
            return 0

    def _device_sn(self) -> str:
        mac = self._read_text("/sys/class/net/wlan0/address", "").replace(":", "").upper()
        return mac or "UNKNOWN"

    def mcp_set_volume(self, volume) -> bool:
        """self.audio_speaker.set_volume → 设置系统音量 (0-100), 返回 bool (对齐 Omni)"""
        vol = max(0, min(100, int(volume)))
        try:
            VOLUME_INI.parent.mkdir(parents=True, exist_ok=True)
            VOLUME_INI.write_text(str(vol))
        except Exception as e:
            raise RuntimeError(f"写音量失败: {e}")
        # 同 apps/settings/write_volume: 优先 pactl, 否则 amixer (0-100 → Playback 原始值)
        if subprocess.run(["which", "pactl"], capture_output=True).returncode == 0:
            subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{vol}%"],
                           capture_output=True)
        else:
            raw = 196 + vol * 59 // 100
            for cmd in (["amixer", "set", "Playback", str(raw)],
                        ["amixer", "set", "Speaker", "100%"],
                        ["amixer", "set", "Headphone", "100%"],
                        ["amixer", "set", "Speaker DC", "100%"],
                        ["amixer", "set", "Speaker AC", "100%"]):
                subprocess.run(cmd, capture_output=True)
            subprocess.run(["alsactl", "store"], capture_output=True)
        print(f"[app] 音量设置为 {vol}")
        return True

    def mcp_set_language(self, language) -> str:
        """self.set_language → 写全局语言配置 + 重启 launcher, 返回语言码字符串 (对齐 Omni)"""
        code = "cn" if language == "zh-CN" else ("en" if language == "en-US" else None)
        if code is None:
            raise RuntimeError(
                f"Unsupported language: {language}. Supported: zh-CN, en-US")
        try:
            LANGUAGE_INI.parent.mkdir(parents=True, exist_ok=True)
            LANGUAGE_INI.write_text(code)
        except Exception as e:
            raise RuntimeError(f"写语言配置失败: {e}")
        # launcher 界面文本启动时生成, 无热重载 → 重启让 LCD 桌面同步切换 (同 portal)
        subprocess.Popen(["systemctl", "restart", "luwu-launcher"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        display = "zh-CN" if code == "cn" else "en-US"
        print(f"[app] 语言设置为 {display}")
        return f"Language set to: {display}"

    def mcp_get_system_info(self) -> dict:
        """self.get_system_info → 对齐 RIG-Omni GetSystemInfoJson 字段命名。

        树莓派无 ESP32 的 flash/psram/chip/partition/ota 概念, 对应字段给近似值或空;
        key 名与 Omni 保持一致, 以便小程序/服务端按统一结构消费。
        """
        lang = "zh-CN" if self._read_text(LANGUAGE_INI, "cn") == "cn" else "en-US"
        mac = self._read_text("/sys/class/net/wlan0/address", "00:00:00:00:00:00")
        w, h = self._screen_resolution()
        cores = os.cpu_count() or 0
        cpu_model = ""
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("Model"):
                        cpu_model = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass
        mem_avail = 0
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable"):
                        mem_avail = int(line.split()[1]) * 1024
                        break
        except Exception:
            pass
        return {
            "version": 2,
            "language": lang,
            # 无 ESP32 flash/psram → flash_size 用根分区总字节近似, psram=0
            "flash_size": self._disk_total_bytes(),
            "psram_size": 0,
            "minimum_free_heap_size": mem_avail,
            "mac_address": mac,
            "uuid": get_client_id(),
            "chip_model_name": cpu_model or "board",
            "chip_info": {"model": 0, "cores": cores, "revision": 0, "features": 0},
            "application": {
                "name": "luwu-os",
                "version": self._luwuos_version(),
                "compile_time": "",
                "idf_version": "",
                "elf_sha256": "",
            },
            "partition_table": [],
            "ota": {"label": ""},
            "display": {"monochrome": False, "width": w, "height": h},
            "board": {"model": cpu_model, "sn": self._device_sn()},
        }

    def mcp_reboot(self) -> bool:
        """self.reboot → 延迟 1s 后重启系统, 返回 bool (对齐 Omni)"""
        def _do():
            import time
            time.sleep(1)
            subprocess.Popen(["systemctl", "reboot"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        threading.Thread(target=_do, daemon=True).start()
        return True

    def mcp_screen_get_info(self) -> dict:
        """self.screen.get_info → 屏幕信息 (宽/高/单色), 对齐 Omni"""
        w, h = self._screen_resolution()
        return {"width": w, "height": h, "monochrome": False}

    # ================= 视觉分析 (MCP self.camera.*, 对齐 RIG-Omni) =================
    def mcp_on_vision_config(self, url: str, token: str):
        """服务端 MCP initialize 下发 vision 能力"""
        if self.vision is not None:
            self.vision.set_explain(url, token)

    def mcp_camera_available(self) -> bool:
        return self.vision is not None and self.vision.camera_available

    def mcp_take_photo(self, question) -> str:
        """self.camera.take_photo: 拍照 → 上屏预览 (VLM 分析期间用户可看) → 服务端 VLM 分析"""
        if self.vision is None or not self.vision.camera_available:
            raise RuntimeError("相机不可用")
        jpeg, img = self.vision.capture()
        if self.photo_callback:
            try:
                self.photo_callback(img, 15000)  # 展示 15s 覆盖分析+播报, 之后服务器表情接管
            except TypeError:
                self.photo_callback(img)  # 兼容只收图的老回调
            except Exception as e:
                print(f"[app] 照片展示回调异常: {e}")
        return self.vision.explain(question, jpeg)

    def mcp_preview_photo(self) -> str:
        """self.camera.preview: 拍照并展示到屏幕 (不做 AI 分析)"""
        if self.vision is None or not self.vision.camera_available:
            raise RuntimeError("相机不可用")
        _, img = self.vision.capture()
        if self.photo_callback:
            try:
                self.photo_callback(img)
            except Exception as e:
                print(f"[app] 照片展示回调异常: {e}")
            return "照片已拍摄并显示在屏幕上"
        return "照片已拍摄 (无屏幕展示)"

    def mcp_camera_get_base64(self) -> str:
        """self.camera.get_base64: 拍照 → 等比压缩 320x180 JPEG → base64 直接返回 (不经 VLM)。

        体积自适应降质, 保证不超网关上行 MQTT 单条消息限制 (见 vision.thumb_base64)。
        """
        if self.vision is None or not self.vision.camera_available:
            raise RuntimeError("相机不可用")
        return self.vision.capture_base64()

    # ================= IoT =================
    async def _handle_iot(self, data):
        thing_manager = ThingManager.get_instance()
        commands = data.get("commands", [])
        for command in commands:
            try:
                result = await self.loop.run_in_executor(
                    None, thing_manager.invoke, command)
                print(f"[app] IoT 命令结果: {result}")
                # 回报最新状态
                await self.protocol.send_iot_states(
                    thing_manager.get_states_json())
            except Exception as e:
                print(f"[app] IoT 命令执行失败: {e}")

    # ================= 关闭 =================
    async def shutdown(self):
        global _active_app
        self._closing = True
        self._ota_stop.set()
        self._cancel_listen_timeout()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        print("[app] 正在关闭...")
        if _active_app is self:
            _active_app = None
        self._stop_ambient()
        try:
            if self.wakeword:
                self.wakeword.cleanup()
        finally:
            try:
                if self.protocol:
                    await self.protocol.shutdown()
            finally:
                if self.audio:
                    self.audio.close()
        print("[app] 已关闭")
