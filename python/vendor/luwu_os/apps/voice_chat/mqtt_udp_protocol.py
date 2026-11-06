# -*- coding: utf-8 -*-
"""MQTT + UDP 协议层 (小智协议 v3)

忠实移植自 RIG-Omni main/protocols/mqtt_protocol.cc:
  - hello v3 (transport=udp, features.mcp, audio_params)
  - 服务器 hello 下发 udp server/port/key/nonce → AES-128-CTR 加密 UDP 音频通道
  - 16 字节 nonce 头: |type 1|flags 1|payload_len 2|ssrc 4|timestamp 4|sequence 4|
  - 收发双向序列号校验
  - 通道打开后主动发 UDP 空包让网关学习设备地址 (事件触发场景必需)
  - goodbye 防乒乓; MQTT 指数退避重连 3s→30s
"""

import asyncio
import json
import ssl
import threading
import time

import paho.mqtt.client as mqtt
from Crypto.Cipher import AES
from Crypto.Util import Counter


class ListeningMode:
    ALWAYS_ON = "realtime"
    AUTO_STOP = "auto"
    MANUAL = "manual"


class AbortReason:
    NONE = "none"
    WAKE_WORD_DETECTED = "wake_word_detected"
    USER_INTERRUPTION = "user_interruption"


MQTT_RECONNECT_INITIAL_MS = 3000
MQTT_RECONNECT_MAX_MS = 30000
# CONNACK 连续被拒次数阈值: 达到后判定凭据可能失效, 回调上层回 OTA 刷新凭据
MQTT_AUTH_REJECT_THRESHOLD = 3
CHANNEL_IDLE_TIMEOUT_S = 60  # 通道打开后超过该时长无任何数据 → 判定超时
# hello 握手: MQTT 已连接时往返通常 <1s, 单次等 5s 即判超时并自动重试
# (Omni 为单次 10s 不重试; 总时长上限持平, 反馈更快且多一次自动重试)
HELLO_WAIT_TIMEOUT_S = 5.0
HELLO_MAX_ATTEMPTS = 2
MQTT_CONNECT_WAIT_S = 5      # 等待 CONNACK 上限: paho connect() 阻塞式握手可达 60s,
                             # 改用 connect_async+loop_start 非阻塞建连, 等待上限收敛到 5s


def _decode_hex(hex_string: str) -> bytes:
    return bytes.fromhex(hex_string)


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, owner):
        self.owner = owner

    def datagram_received(self, data, addr):
        self.owner._on_udp_packet(bytes(data))

    def error_received(self, exc):
        print(f"[protocol] UDP 错误: {exc}")


class MqttUdpProtocol:
    """小智 MQTT+UDP 协议客户端"""

    def __init__(self, settings: dict, loop: asyncio.AbstractEventLoop):
        """
        settings: endpoint / client_id / username / password /
                  publish_topic / subscribe_topic / keepalive / tls_insecure
        """
        self.settings = settings
        self.loop = loop

        # 回调 (由 Application 设置)
        self.on_incoming_json = None      # (dict)
        self.on_incoming_audio = None     # (bytes payload, timestamp)
        self.on_audio_channel_opened = None   # async
        self.on_audio_channel_closed = None   # async
        self.on_network_error = None      # (str)
        self.on_connected = None          # ()
        self.on_disconnected = None       # ()
        self.on_credentials_expired = None  # async () -> dict|None 凭据失效时回 OTA 刷新

        self.session_id = ""
        self.server_sample_rate = 24000
        self.server_frame_duration = 60

        self._mqtt = None
        self._mqtt_guard = threading.Lock()
        self._mqtt_start_guard = threading.Lock()
        self._mqtt_connected = False
        self._reject_count = 0  # CONNACK 连续被拒次数 (连上后清零)
        self._reconnect_interval_ms = MQTT_RECONNECT_INITIAL_MS
        self._reconnect_task = None
        self._closing = False

        self._server_hello_event = asyncio.Event()
        self._udp_server = ""
        self._udp_port = 0
        self._aes_key = b""
        self._aes_nonce = b""
        self._local_sequence = 0
        self._remote_sequence = 0
        self._transport = None
        self._udp_channel_open = False

        self._last_incoming_time = 0.0
        self._last_packet_ts = None   # 上一有效音频包的报文 timestamp (诊断用)
        self._last_packet_at = 0.0    # 上一有效音频包的实际到达时刻

    # ================= MQTT =================
    async def start(self) -> bool:
        """非阻塞启动: 发起建连并最多等 MQTT_CONNECT_WAIT_S, 超时不抛异常

        MQTT 为常驻连接, 未连上时由指数退避自动重连兼顾;
        open_audio_channel 打开通道前也会再次等待/重试。
        """
        try:
            ok = await self.loop.run_in_executor(None, self._start_mqtt_client)
        except Exception as e:
            print(f"[protocol] MQTT 启动异常: {e}")
            self._schedule_reconnect()
            return False
        if ok and not self._mqtt_connected:
            deadline = time.time() + MQTT_CONNECT_WAIT_S
            while time.time() < deadline and not self._mqtt_connected:
                await asyncio.sleep(0.1)
            if not self._mqtt_connected:
                print(f"[protocol] {MQTT_CONNECT_WAIT_S}s 内未完成握手, 后台继续重连")
                return False
        return ok

    def _start_mqtt_client(self) -> bool:
        with self._mqtt_start_guard:
            if self._closing:
                return False
            return self._create_mqtt_client()

    @staticmethod
    def _stop_mqtt_client(client):
        if client is None:
            return
        def teardown():
            try:
                # Mark disconnecting before joining a potentially slow TLS
                # thread, so it cannot reconnect after the App releases audio.
                client.disconnect()
                client.loop_stop()
            except Exception:
                pass
        threading.Thread(target=teardown, daemon=True).start()

    def _create_mqtt_client(self) -> bool:
        s = self.settings
        endpoint = s.get("endpoint", "")
        if not endpoint:
            print("[protocol] 未配置 MQTT endpoint")
            return False

        broker, port = endpoint, 8883
        if ":" in endpoint:
            broker, port_s = endpoint.rsplit(":", 1)
            try:
                port = int(port_s)
            except ValueError:
                pass

        client_id = s.get("client_id", "") or None
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        if s.get("username"):
            client.username_pw_set(s["username"], s.get("password", ""))
        if port == 8883 or s.get("tls", True):
            client.tls_set(cert_reqs=ssl.CERT_NONE if s.get("tls_insecure") else ssl.CERT_REQUIRED)
            if s.get("tls_insecure"):
                client.tls_insecure_set(True)
        client.keepalive = int(s.get("keepalive", 30))

        client.on_connect = self._on_mqtt_connect
        client.on_disconnect = self._on_mqtt_disconnect
        client.on_message = self._on_mqtt_message

        try:
            with self._mqtt_guard:
                if self._closing:
                    self._stop_mqtt_client(client)
                    return False
                old, self._mqtt = self._mqtt, client
                self._mqtt_connected = False
                self._stop_mqtt_client(old)
                # Both calls are nonblocking. Shutdown takes this same short
                # guard, but never waits for client construction or TLS I/O.
                client.connect_async(broker, port)
                client.loop_start()
            print(f"[protocol] MQTT 连接中: {broker}:{port}")
            return True
        except Exception as e:
            self._stop_mqtt_client(client)
            print(f"[protocol] MQTT 连接失败: {e}")
            # 在主事件循环上调度重连 (_start_mqtt_client 可能在 executor 线程执行,
            # 直接 ensure_future 会报 no current event loop)
            self.loop.call_soon_threadsafe(self._schedule_reconnect)
            return False

    def _on_mqtt_connect(self, client, userdata, flags, reason_code, properties=None):
        if self._closing or client is not self._mqtt:
            self._stop_mqtt_client(client)
            return
        if reason_code == 0 or getattr(reason_code, "is_success", False):
            self._mqtt_connected = True
            self._reject_count = 0
            self._reconnect_interval_ms = MQTT_RECONNECT_INITIAL_MS
            pub = self.settings.get("publish_topic", "device-server")
            sub = self.settings.get("subscribe_topic") or ""
            if sub and sub != "null":
                client.subscribe(sub)
            print(f"[protocol] MQTT 已连接 (publish={pub}, subscribe={sub or '-'})")
            if self.on_connected:
                self.loop.call_soon_threadsafe(self.on_connected)
        else:
            self._reject_count += 1
            print(f"[protocol] MQTT 连接被拒 (连续 {self._reject_count} 次): {reason_code}")
            if self._reject_count >= MQTT_AUTH_REJECT_THRESHOLD and \
                    self.on_credentials_expired:
                # 连续被拒大概率是凭据失效 (服务端轮换/吊销): 回 OTA 刷新后重连
                self._reject_count = 0
                self.loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self._refresh_credentials()))
            else:
                self.loop.call_soon_threadsafe(self._schedule_reconnect)

    async def _refresh_credentials(self):
        """凭据失效兜底: 回调上层取新凭据, 成功则用新凭据重建连接"""
        try:
            new_settings = await self.on_credentials_expired()
        except Exception as e:
            print(f"[protocol] 凭据刷新回调异常: {e}")
            new_settings = None
        if self._closing:
            return
        if new_settings and new_settings.get("endpoint"):
            print("[protocol] 凭据已刷新, 用新凭据重连 MQTT")
            self.settings = new_settings
            self._reconnect_interval_ms = MQTT_RECONNECT_INITIAL_MS
            await self.loop.run_in_executor(None, self._start_mqtt_client)
        else:
            print("[protocol] 凭据刷新失败 (OTA 不可达?), 继续用旧凭据退避重连")
            self._schedule_reconnect()

    def _on_mqtt_disconnect(self, client, userdata, *args):
        if self._closing or client is not self._mqtt:
            return
        was_connected = self._mqtt_connected
        self._mqtt_connected = False
        if self._closing:
            return
        if was_connected:
            # rc: paho v5 断开回调 (rc, props); 服务端主动断开时 rc 携带原因
            rc = args[0] if args else None
            props = args[1] if len(args) > 1 else None
            print(f"[protocol] MQTT 断开 (rc={rc}, props={props}), "
                  f"{self._reconnect_interval_ms}ms 后重连")
            if self._udp_channel_open:
                self.loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self.close_audio_channel(send_goodbye=False)))
            if self.on_disconnected:
                self.loop.call_soon_threadsafe(self.on_disconnected)
            # paho 网络线程 → 回主事件循环调度重连
            self.loop.call_soon_threadsafe(self._schedule_reconnect)

    def _schedule_reconnect(self):
        if self._closing:
            return
        if self._reconnect_task and not self._reconnect_task.done():
            return
        interval = self._reconnect_interval_ms
        # 指数退避: 3s → 6s → 12s → 24s → 30s
        self._reconnect_interval_ms = min(self._reconnect_interval_ms * 2, MQTT_RECONNECT_MAX_MS)

        async def _delayed():
            await asyncio.sleep(interval / 1000)
            if not self._closing and not self._mqtt_connected:
                print("[protocol] 尝试重连 MQTT")
                await self.loop.run_in_executor(None, self._start_mqtt_client)

        try:
            self._reconnect_task = asyncio.ensure_future(_delayed())
        except RuntimeError:
            # 兜底: 无当前事件循环的线程 (paho/executor) 回调进来
            self._reconnect_task = asyncio.run_coroutine_threadsafe(
                _delayed(), self.loop)

    def _on_mqtt_message(self, client, userdata, msg):
        if self._closing or client is not self._mqtt:
            return
        try:
            data = json.loads(msg.payload.decode())
        except Exception as e:
            print(f"[protocol] JSON 解析失败: {e}")
            return
        self._last_incoming_time = time.time()
        msg_type = data.get("type")
        if msg_type == "hello":
            self._parse_server_hello(data)
        elif msg_type == "goodbye":
            sid = data.get("session_id")
            print(f"[protocol] 收到 goodbye (session={sid})")
            if not sid or sid == self.session_id:
                # 服务器发起的 goodbye, 不回发以免乒乓
                self.loop.call_soon_threadsafe(
                    lambda: asyncio.ensure_future(self.close_audio_channel(send_goodbye=False)))
        elif self.on_incoming_json:
            self.loop.call_soon_threadsafe(self.on_incoming_json, data)

    # ================= 音频通道 =================
    def is_audio_channel_opened(self) -> bool:
        if not self._udp_channel_open:
            return False
        if self._last_incoming_time and \
                time.time() - self._last_incoming_time > CHANNEL_IDLE_TIMEOUT_S:
            return False
        return True

    def _hello_message(self) -> str:
        return json.dumps({
            "type": "hello",
            "version": 3,
            "transport": "udp",
            "features": {"mcp": True},
            "audio_params": {
                "format": "opus",
                "sample_rate": 16000,
                "channels": 1,
                "frame_duration": 60,
            },
        })

    async def open_audio_channel(self) -> bool:
        if not self._mqtt_connected:
            print("[protocol] MQTT 未连接, 先尝试连接")
            if not await self.start():
                return False
            # 等待连接建立
            for _ in range(50):
                if self._mqtt_connected:
                    break
                await asyncio.sleep(0.1)
            if not self._mqtt_connected:
                return False

        self.session_id = ""
        hello_ok = False
        for attempt in range(1, HELLO_MAX_ATTEMPTS + 1):
            self._server_hello_event.clear()

            if not await self.send_text(self._hello_message()):
                return False

            try:
                await asyncio.wait_for(self._server_hello_event.wait(),
                                       timeout=HELLO_WAIT_TIMEOUT_S)
                hello_ok = True
                break
            except asyncio.TimeoutError:
                if attempt < HELLO_MAX_ATTEMPTS:
                    print(f"[protocol] 等待服务器 hello 超时, 重试 ({attempt}/{HELLO_MAX_ATTEMPTS})")
                else:
                    print("[protocol] 等待服务器 hello 超时 (已重试)")
        if not hello_ok:
            if self.on_network_error:
                self.on_network_error("服务器响应超时")
            return False

        # 建立 UDP 通道
        try:
            self._transport, _ = await self.loop.create_datagram_endpoint(
                lambda: _UdpProtocol(self),
                remote_addr=(self._udp_server, self._udp_port))
        except Exception as e:
            print(f"[protocol] UDP 通道创建失败: {e}")
            return False

        self._udp_channel_open = True

        # 新会话开始: 重置供数诊断基准 (否则会把跨会话间隔误判为供数异常)
        self._last_packet_ts = None
        self._last_packet_at = 0.0

        # 主动发送 UDP 空包让网关学习设备地址 (同 RIG-Omni 注释:
        # idle 状态不收音时网关无设备地址, 下行 TTS 会被静默丢弃)
        nonce = bytearray(self._aes_nonce)
        nonce[2:4] = (0).to_bytes(2, "big")       # payload_len = 0
        nonce[8:12] = (0).to_bytes(4, "big")      # timestamp = 0
        self._local_sequence += 1
        nonce[12:16] = (self._local_sequence & 0xFFFFFFFF).to_bytes(4, "big")
        try:
            self._transport.sendto(bytes(nonce))
        except Exception as e:
            print(f"[protocol] UDP 空包发送失败: {e}")

        if self.on_audio_channel_opened:
            await self.on_audio_channel_opened()
        return True

    def _parse_server_hello(self, data: dict):
        transport = data.get("transport")
        if transport != "udp":
            print(f"[protocol] 不支持的传输方式: {transport}")
            return

        self.session_id = data.get("session_id", "") or ""
        print(f"[protocol] Session ID: {self.session_id}")

        audio_params = data.get("audio_params") or {}
        if isinstance(audio_params.get("sample_rate"), int):
            self.server_sample_rate = audio_params["sample_rate"]
        if isinstance(audio_params.get("frame_duration"), int):
            self.server_frame_duration = audio_params["frame_duration"]

        udp = data.get("udp")
        if not isinstance(udp, dict):
            print("[protocol] 服务器 hello 缺少 udp 段")
            return
        self._udp_server = udp.get("server", "")
        self._udp_port = int(udp.get("port", 0))
        self._aes_key = _decode_hex(udp.get("key", ""))
        self._aes_nonce = _decode_hex(udp.get("nonce", ""))
        self._local_sequence = 0
        self._remote_sequence = 0
        print(f"[protocol] UDP 通道参数就绪: {self._udp_server}:{self._udp_port}")
        self._server_hello_event.set()

    async def close_audio_channel(self, send_goodbye=True):
        was_open = self._udp_channel_open
        self._udp_channel_open = False
        if self._transport:
            try:
                self._transport.close()
            except Exception:
                pass
            self._transport = None

        if was_open and send_goodbye:
            await self.send_text(json.dumps({
                "session_id": self.session_id, "type": "goodbye"}))

        if was_open and self.on_audio_channel_closed:
            await self.on_audio_channel_closed()

    # ================= 数据收发 =================
    async def send_text(self, message: str, *, wait_published=False) -> bool:
        topic = self.settings.get("publish_topic", "device-server")
        client = self._mqtt
        if not client or not self._mqtt_connected:
            return False

        def publish():
            info = client.publish(topic, message)
            if info.rc != mqtt.MQTT_ERR_SUCCESS:
                return False
            if wait_published:
                info.wait_for_publish(timeout=1.)
                return info.is_published()
            return True

        try:
            return await self.loop.run_in_executor(None, publish)
        except Exception as e:
            print(f"[protocol] 发送文本失败: {e}")
            return False

    async def send_audio(self, payload: bytes, timestamp: int = None) -> bool:
        if not self._udp_channel_open or self._transport is None:
            return False
        if timestamp is None:
            timestamp = int(time.time() * 1000) & 0xFFFFFFFF

        nonce = bytearray(self._aes_nonce)
        nonce[2:4] = (len(payload) & 0xFFFF).to_bytes(2, "big")
        nonce[8:12] = (timestamp & 0xFFFFFFFF).to_bytes(4, "big")
        self._local_sequence += 1
        nonce[12:16] = (self._local_sequence & 0xFFFFFFFF).to_bytes(4, "big")

        # 与 mbedtls_aes_crypt_ctr 等价: 完整 16 字节 nonce 作为 128-bit 计数器初始值
        ctr = Counter.new(128, initial_value=int.from_bytes(bytes(nonce), "big"))
        cipher = AES.new(self._aes_key, AES.MODE_CTR, counter=ctr)
        encrypted = cipher.encrypt(payload)
        try:
            self._transport.sendto(bytes(nonce) + encrypted)
            return True
        except Exception as e:
            print(f"[protocol] 发送音频失败: {e}")
            if self.on_network_error:
                self.on_network_error(f"发送音频失败: {e}")
            return False

    def _on_udp_packet(self, data: bytes):
        """UDP Encrypted OPUS Packet:
        |type 1|flags 1|payload_len 2|ssrc 4|timestamp 4|sequence 4|payload|
        """
        if len(data) < 16:
            print(f"[protocol] 非法音频包大小: {len(data)}")
            return
        if data[0] != 0x01:
            print(f"[protocol] 非法音频包类型: {data[0]:x}")
            return
        timestamp = int.from_bytes(data[8:12], "big")
        sequence = int.from_bytes(data[12:16], "big")
        if sequence < self._remote_sequence:
            return  # 旧包丢弃
        if sequence != self._remote_sequence + 1:
            print(f"[protocol] 序列号跳变: 收到 {sequence}, 期望 {self._remote_sequence + 1}")

        # 供数诊断: 实际到达间隔 vs 报文时间轴间隔 (区分服务端断供 vs 网络延迟)
        # 报文 timestamp 间隔正常(≈帧长)但到达间隔大 → 网络延迟; 报文间隔本身大 → 服务端断供
        # 注意: 只能用 at_ms 做打印条件, 断供时 tb_ms 也大会被 at_ms>tb_ms+150 误筛掉
        import time as _t
        now = _t.time()
        if self._last_packet_ts is not None:
            tb_ms = (timestamp - self._last_packet_ts) & 0xFFFFFFFF  # 服务端声称的音频时间轴间隔
            at_ms = int((now - self._last_packet_at) * 1000)         # 实际到达间隔
            if at_ms > 250:
                print(f"[protocol] 供数异常: 到达间隔 {at_ms}ms / 报文间隔 {tb_ms}ms"
                      f" → {'网络延迟' if tb_ms < 150 else '服务端断供'}")
        self._last_packet_ts = timestamp
        self._last_packet_at = now

        nonce = data[:16]
        try:
            ctr = Counter.new(128, initial_value=int.from_bytes(nonce, "big"))
            cipher = AES.new(self._aes_key, AES.MODE_CTR, counter=ctr)
            payload = cipher.decrypt(data[16:])
        except Exception as e:
            print(f"[protocol] 解密失败: {e}")
            return

        self._remote_sequence = sequence
        self._last_incoming_time = time.time()
        if self.on_incoming_audio:
            self.on_incoming_audio(payload, timestamp)

    # ================= 会话消息 =================
    async def send_abort_speaking(self, reason=AbortReason.NONE):
        message = {"session_id": self.session_id, "type": "abort"}
        if reason == AbortReason.WAKE_WORD_DETECTED:
            message["reason"] = "wake_word_detected"
        await self.send_text(json.dumps(message))

    async def send_wake_word_detected(self, wake_word: str):
        await self.send_text(json.dumps({
            "session_id": self.session_id, "type": "listen",
            "state": "detect", "text": wake_word}))

    async def send_start_listening(self, mode: str, *, wait_published=False):
        return await self.send_text(json.dumps({
            "session_id": self.session_id, "type": "listen",
            "state": "start", "mode": mode}), wait_published=wait_published)

    async def send_stop_listening(self):
        await self.send_text(json.dumps({
            "session_id": self.session_id, "type": "listen", "state": "stop"}))

    async def send_iot_descriptors(self, descriptors):
        if isinstance(descriptors, str):
            descriptors = json.loads(descriptors)
        await self.send_text(json.dumps({
            "session_id": self.session_id, "type": "iot",
            "descriptors": descriptors}))

    async def send_iot_states(self, states):
        if isinstance(states, str):
            states = json.loads(states)
        await self.send_text(json.dumps({
            "session_id": self.session_id, "type": "iot", "states": states}))

    async def send_mcp_message(self, payload):
        """payload: jsonrpc 应答 dict (或 str)。信封中 payload 必须是 JSON 对象,
        不能是字符串 (对齐 RIG-Omni protocol.cc SendMcpMessage: "payload": + payload)。
        服务端按 cJSON_IsObject(payload) 解析, 字符串会被静默丢弃导致 MCP 链路断裂。
        """
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception as e:
                print(f"[protocol] MCP 应答不是合法 JSON, 丢弃: {e}")
                return False
        return await self.send_text(json.dumps({
            "session_id": self.session_id, "type": "mcp", "payload": payload}))

    # ================= 生命周期 =================
    def force_stop_mqtt(self):
        """同步停 MQTT 客户端 (无事件循环依赖, 供跨实例替换/强拆场景)

        _closing 先拦住断开回调里的重连调度, 再后台线程拆连接:
        paho 的 loop_stop() 会 join 网络线程; 若卡在阻塞式 TCP/TLS 握手
        (海外链路实测可卡 50s+), join 会把调用方一起卡死 (同 _start_mqtt_client 注释)
        """
        with self._mqtt_guard:
            self._closing = True
            old, self._mqtt = self._mqtt, None
            self._mqtt_connected = False
        self._stop_mqtt_client(old)

    async def shutdown(self):
        self._closing = True
        task, self._reconnect_task = self._reconnect_task, None
        if task:
            task.cancel()
            if isinstance(task, asyncio.Future):
                await asyncio.gather(task, return_exceptions=True)
        await self.close_audio_channel(send_goodbye=False)
        self.force_stop_mqtt()
