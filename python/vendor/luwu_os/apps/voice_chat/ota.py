# -*- coding: utf-8 -*-
"""OTA 与连接凭据管理

对齐 RIG-Omni main/ota.cc 的 check_version 流程, 但激活方式不同:
  xl-api 服务器不走验证码激活, 而是由小程序/App 在蓝牙配网成功后
  直接调 POST /device/bind (activationCode=STA MAC) 完成绑定。
  本模块职责:
  1. POST 系统信息到 OTA 地址 (headers: Device-Id/Client-Id)
  2. 获取并缓存服务器下发的 mqtt 凭据
  3. 若服务器仍提示未激活(未绑定), 回调通知 UI 展示配网二维码
设备身份: Device-Id = wlan0 MAC (与小程序 /device/bind 绑定身份一致)
"""

import json
import os
import time
import uuid
import threading
from pathlib import Path

import requests

STATE_DIR = Path(os.environ.get("DUCK_VOICE_STATE_DIR", "/opt/luwu-os/.state"))
# 状态文件命名前缀 (与 apps/voice_chat 目录对齐); 读取时自动迁移旧的 xiaozhi 前缀文件。
# 注: MQTT 凭据按语言分流 (中英文连不同服务器); client_id/aec 是设备身份或硬件行为, 共用。
STATE_PREFIX = "voice_chat"

ACTIVATE_POLL_INTERVAL_S = 5
ACTIVATE_MAX_WAIT_S = 300

# 海外服务器链路偶发 TCP 建连挂死 (实测 ~50%), 单次请求快失败+重试
# 比单次长超时更稳 (对齐 Omni CheckNewVersion 的重试思路, 但收敛更快)
HTTP_CONNECT_TIMEOUT_S = 5
HTTP_READ_TIMEOUT_S = 10
HTTP_MAX_RETRIES = 5          # 首次失败后最多重试次数
HTTP_RETRY_GAP_S = 0.5

# 全局 Session: keep-alive 复用 TCP+TLS 连接, 连上后不再重复握手, 降低撞上抖动的概率
_HTTP_SESSION = requests.Session()


def get_device_id() -> str:
    """wlan0 MAC 作为 Device-Id"""
    if os.environ.get('DUCK_DEVICE_ID'):
        return os.environ['DUCK_DEVICE_ID']
    try:
        return Path("/sys/class/net/wlan0/address").read_text().strip()
    except Exception:
        return "00:00:00:00:00:00"


def _lang() -> str:
    """当前界面语言 (cn/en), 用于区分中英文服务器的凭据缓存"""
    try:
        from libs.i18n import get_lang
        return get_lang()
    except Exception:
        return "cn"


def _board_info() -> dict:
    if os.environ.get("DUCK_BOARD_TYPE"):
        return {"type": os.environ["DUCK_BOARD_TYPE"], "name": os.environ["DUCK_BOARD_TYPE"], "mac": get_device_id()}
    """board 字段 (对齐 RIG-Omni GetBoardJson: type/name/mac)。

    机型由 libs.robot_type 按底层固件版本前缀判定 (M/L/MW/R/RA →
    XGO-mini/Lite/miniW/Rider/Ranger)。读不到机型时返回空 dict,
    上报省略 board 字段, 服务端按 chip_model_name 兜底, 避免失真。
    """
    try:
        from libs.robot_type import (ROBOT_TYPE_NAMES, ROBOT_TYPE_UNKNOWN,
                                     detect_robot_type)
        robot_type = detect_robot_type()
        if robot_type == ROBOT_TYPE_UNKNOWN:
            return {}
        name = ROBOT_TYPE_NAMES.get(robot_type)
        if not name:
            return {}
        return {"type": name, "name": name, "mac": get_device_id()}
    except Exception as e:
        print(f"[ota] 读取机型失败, board 字段省略: {e}")
        return {}


def migrate_state_file(new: Path, old_names: list) -> Path:
    """new 不存在时, 依次把 old_names (相对 STATE_DIR) 迁移为 new, 保留内容并删旧件"""
    if new.exists():
        return new
    new.parent.mkdir(parents=True, exist_ok=True)
    for name in old_names:
        old = STATE_DIR / name
        if old.exists():
            try:
                new.write_text(old.read_text())
                old.unlink()
                print(f"[voice_chat] 已迁移状态文件 {old.name} → {new.name}")
                return new
            except Exception as e:
                print(f"[voice_chat] 迁移状态文件失败: {old.name}: {e}")
    return new


def _mqtt_cache_file() -> Path:
    """语言相关的 MQTT 凭据缓存路径; 依次迁移旧 xiaozhi_mqtt_<lang>.json / xiaozhi_mqtt.json"""
    lang = _lang()
    return migrate_state_file(
        STATE_DIR / f"{STATE_PREFIX}_mqtt_{lang}.json",
        [f"xiaozhi_mqtt_{lang}.json", "xiaozhi_mqtt.json"])


def get_client_id() -> str:
    """持久化的 UUID (首次生成)"""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    f = migrate_state_file(STATE_DIR / f"{STATE_PREFIX}_client_id", ["xiaozhi_client_id"])
    if f.exists():
        cid = f.read_text().strip()
        if cid:
            return cid
    cid = str(uuid.uuid4())
    f.write_text(cid)
    return cid


class OtaManager:
    def __init__(self, ota_url: str, on_activation_code=None, stop_event=None):
        """on_activation_code: (code, message) 回调, 用于屏幕/语音提示"""
        # 保留末尾斜杠: 服务器对无斜杠路径返回 Resource not found
        if ota_url and not ota_url.endswith("/"):
            ota_url += "/"
        self.ota_url = ota_url or ""
        self.device_id = get_device_id()
        self.client_id = get_client_id()
        self.on_activation_code = on_activation_code
        self.bound = None  # 绑定状态: True/False/None(未知, 未访问过服务器)
        self.stop_event = stop_event or threading.Event()

    def _headers(self):
        return {
            "Activation-Version": "1",
            "Device-Id": self.device_id,
            "Client-Id": self.client_id,
            "Content-Type": "application/json",
        }

    @staticmethod
    def load_cached_mqtt() -> dict:
        """读取上次 OTA 下发的 MQTT 凭据 (按当前语言分流)"""
        try:
            f = _mqtt_cache_file()
            if f.exists():
                data = json.loads(f.read_text())
                if data.get("endpoint"):
                    return data
        except Exception as e:
            print(f"[ota] 读取凭据缓存失败: {e}")
        return {}

    @staticmethod
    def save_mqtt_config(mqtt_cfg: dict):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        f = _mqtt_cache_file()
        f.write_text(json.dumps(mqtt_cfg, indent=2))
        print(f"[ota] MQTT 凭据已缓存 (endpoint={mqtt_cfg.get('endpoint')}, lang={_lang()})")

    def check_version(self) -> dict:
        """请求 OTA 地址, 返回解析后的响应 (失败返回 {})

        请求体按小智系统信息格式 (对齐 RIG-Omni Board::GetSystemInfoJson),
        字段缺失会导致服务端 500; board.type 携带机型显示名 (XGO-mini/Lite/...),
        服务端存库后用作 MQTT groupId 与智控台机型识别。
        """
        if not self.ota_url:
            print("[ota] 未配置 OTA 地址, 跳过")
            return {}
        try:
            body = {
                "version": 2,
                "mac_address": self.device_id,
                "uuid": self.client_id,
                "chip_model_name": "Arduino UNO Q",
                "firmware_version": _read_app_version(),
                "application": {
                    "name": "xgoduck-ai",
                    "version": _read_app_version(),
                },
            }
            board = _board_info()
            if board:
                body["board"] = board
            last_err = None
            for attempt in range(HTTP_MAX_RETRIES + 1):
                if self.stop_event.is_set():
                    return {}
                try:
                    resp = _HTTP_SESSION.post(
                        self.ota_url, headers=self._headers(), json=body,
                        timeout=(HTTP_CONNECT_TIMEOUT_S, HTTP_READ_TIMEOUT_S))
                    if resp.status_code != 200:
                        print(f"[ota] check_version 失败: HTTP {resp.status_code}")
                        return {}
                    data = resp.json()
                    if self.stop_event.is_set():
                        return {}
                    if data.get("code") == 500 or data.get("msg"):
                        print(f"[ota] 服务端错误: {data.get('msg')}")
                        return {}
                    if attempt:
                        print(f"[ota] check_version 成功 (第 {attempt + 1} 次尝试)")
                    else:
                        print("[ota] check_version 成功")
                    return data
                except Exception as e:
                    last_err = e
                    if attempt < HTTP_MAX_RETRIES:
                        print(f"[ota] check_version 第 {attempt + 1} 次失败: {e}, "
                              f"{HTTP_RETRY_GAP_S}s 后重试")
                        if self.stop_event.wait(HTTP_RETRY_GAP_S):
                            return {}
            print(f"[ota] check_version {HTTP_MAX_RETRIES + 1} 次请求均失败: {last_err}")
            return {}
        except Exception as e:
            print(f"[ota] check_version 异常: {e}")
            return {}

    def run_activation(self, activation: dict) -> bool:
        """兼容分支: 若服务器要求验证码激活 (xl-api 正常流程不走这里)"""
        code = activation.get("code", "")
        message = activation.get("message", "请登录控制面板添加设备, 输入验证码")
        challenge = activation.get("challenge", "")
        timeout_ms = activation.get("timeout_ms", ACTIVATE_MAX_WAIT_S * 1000)
        max_wait = min(ACTIVATE_MAX_WAIT_S, int(timeout_ms / 1000) + 60)

        print("=" * 50)
        print(f"[ota] {message}")
        print(f"[ota] 验证码: {code}")
        print("=" * 50)
        if self.on_activation_code:
            try:
                self.on_activation_code(code, message)
            except Exception:
                pass

        url = f"{self.ota_url}/activate"
        payload = {"Payload": {"serial_number": self.device_id, "challenge": challenge}}
        deadline = time.time() + max_wait
        last_error = None
        while time.time() < deadline and not self.stop_event.is_set():
            try:
                resp = requests.post(url, headers=self._headers(),
                                     json=payload, timeout=10)
                if resp.status_code == 200:
                    print("[ota] 设备激活成功")
                    return True
                if resp.status_code == 202:
                    pass  # 等待用户输入验证码
                else:
                    try:
                        err = resp.json().get("error", str(resp.status_code))
                    except Exception:
                        err = str(resp.status_code)
                    if err != last_error:
                        print(f"[ota] 激活等待中: {err}")
                        last_error = err
            except Exception:
                pass
            if self.stop_event.wait(ACTIVATE_POLL_INTERVAL_S):
                return False

        print("[ota] 激活超时, 请重新触发")
        return False

    def provision(self, manual_mqtt: dict = None) -> dict:
        """获取 MQTT 连接凭据并探测绑定状态。

        优先访问 OTA (同时获得绑定状态 self.bound), 网络不可达时降级用缓存/手动配置。
        返回可直接传给 MqttUdpProtocol 的 settings dict (可能为空)。
        """
        if self.stop_event.is_set():
            return {}
        if self.ota_url:
            resp = self.check_version()
            if self.stop_event.is_set():
                return {}
            if resp:
                # 服务器下发 activation 段 = 尚未绑定账号
                activation = resp.get("activation")
                self.bound = not bool(
                    isinstance(activation, dict) and activation.get("code"))
                mqtt_cfg = resp.get("mqtt")
                if isinstance(mqtt_cfg, dict) and mqtt_cfg.get("endpoint"):
                    self.save_mqtt_config(mqtt_cfg)
                    if not self.bound:
                        print("[ota] 设备尚未绑定账号 — 请用小程序/App 扫码绑定")
                        if self.on_activation_code:
                            try:
                                self.on_activation_code(
                                    activation.get("code", ""),
                                    activation.get("message", ""))
                            except Exception:
                                pass
                    return mqtt_cfg
                # 无凭据且要求激活: 兼容旧流程 (轮询 /activate)
                if not self.bound and self.run_activation(activation):
                    resp = self.check_version()
                    mqtt_cfg = resp.get("mqtt")
                    if isinstance(mqtt_cfg, dict) and mqtt_cfg.get("endpoint"):
                        self.save_mqtt_config(mqtt_cfg)
                        self.bound = True
                        return mqtt_cfg

        cached = self.load_cached_mqtt()
        if cached:
            return cached

        if manual_mqtt and manual_mqtt.get("endpoint"):
            print("[ota] 使用手动配置的 MQTT 凭据")
            return manual_mqtt
        return {}


def _read_app_version() -> str:
    """version.txt 为 JSON 格式 ({"v": "2.1.2", ...}), 取 v 字段"""
    try:
        text = Path(os.environ.get("DUCK_APP_VERSION_FILE", "/opt/luwu-os/version.txt")).read_text().strip()
        try:
            return json.loads(text).get("v", "1.0.0")
        except Exception:
            return text or "1.0.0"
    except Exception:
        return "1.0.0"
