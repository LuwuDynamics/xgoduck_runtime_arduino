# -*- coding: utf-8 -*-
"""视觉分析客户端 (对齐 RIG-Omni esp_video.cc 的 Explain 链路)

服务端在 MCP initialize 的 capabilities.vision 中下发 {url, token}:
  LLM 调 self.camera.take_photo 工具 → 本客户端拍照 → multipart POST
  表单字段: question + file(camera.jpg), 请求头: Device-Id / Client-Id / Bearer token
  服务端 vision_handler 用 VLLM 分析后返回 {"success": true, "response": "..."}

摄像头: Picamera2 (树莓派 CSI), 不可用时 capture 抛异常由 MCP 工具层兜错。
"""

import base64
import io
import time

import requests

CAMERA_SIZE = (1280, 720)
CAMERA_WARMUP_S = 0.6       # 出图后等自动曝光收敛
CAMERA_JPEG_QUALITY = 85
UPLOAD_TIMEOUT = (5, 90)    # (连接, 读取): VLM 推理可能较慢

# get_base64 压缩参数: 网关对上行 MQTT 单条消息硬限制 max_mqtt_payload_size(16000B),
# 超限直接断连且无分片, base64 体积必须控制在预算内 (已扣除信封/topic 开销与余量)
CAMERA_THUMB_SIZE = (320, 180)   # 拍照回传尺寸 (与相机 16:9 同比例, 等比缩放无裁剪)
CAMERA_THUMB_QUALITY = 60
CAMERA_THUMB_QUALITY_FALLBACK = (45, 30)  # 超预算时逐档降质 (体积强依赖画面内容)
CAMERA_B64_BUDGET = 14000   # base64 字节预算


def thumb_base64(img, size=CAMERA_THUMB_SIZE, quality=CAMERA_THUMB_QUALITY,
                 budget=CAMERA_B64_BUDGET) -> str:
    """等比缩放到 size 并压成 JPEG base64, 超预算时逐档降质、最后缩半兜底。

    JPEG 体积强依赖画面内容 (实测同参数下照片 ~9KB, 文字网格可翻倍),
    固定质量无法保证不超网关限制, 必须按预算自适应。
    """
    from PIL import Image
    small = img.resize(size, Image.LANCZOS)
    for q in (quality,) + CAMERA_THUMB_QUALITY_FALLBACK:
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=q)
        b64 = base64.b64encode(buf.getvalue())
        if len(b64) <= budget:
            return b64.decode("ascii")
    # 全档降质仍超 (极端复杂画面): 缩半尺寸后用最低档重压
    buf = io.BytesIO()
    small.resize((size[0] // 2, size[1] // 2), Image.LANCZOS).save(
        buf, format="JPEG", quality=CAMERA_THUMB_QUALITY_FALLBACK[-1])
    return base64.b64encode(buf.getvalue()).decode("ascii")


class VisionClient:
    def __init__(self, device_id: str, client_id: str):
        self.device_id = device_id
        self.client_id = client_id
        self.explain_url = ""
        self.explain_token = ""
        try:
            from picamera2 import Picamera2  # noqa: F401
            self.camera_available = True
        except Exception as e:
            self.camera_available = False
            print(f"[vision] picamera2 不可用, 拍照功能关闭: {e}")

    def set_explain(self, url: str, token: str):
        """服务端 MCP initialize capabilities.vision 下发"""
        self.explain_url = url or ""
        self.explain_token = token or ""
        if self.explain_url:
            print(f"[vision] 视觉分析地址已配置: {self.explain_url}")

    @property
    def ready(self) -> bool:
        return self.camera_available and bool(self.explain_url)

    # ---------- 拍照 ----------
    def capture(self):
        """拍一张照片, 返回 (jpeg_bytes, PIL.Image)。摄像头不可用时抛异常。"""
        from picamera2 import Picamera2
        from PIL import Image

        # 物理摄像头只有一个: 先释放 atom_vision 的常驻相机,
        # 否则新 Picamera2 初始化失败 ("Camera __init__ sequence did not complete")。
        # 拍完后 atom_vision 下次识别时自动重开相机 (惰性), 模型不受影响。
        try:
            from libs.atom_vision import get_vision
            get_vision().close_cam()
        except Exception:
            pass

        picam = Picamera2()
        try:
            picam.configure(picam.create_still_configuration(
                main={"format": "RGB888", "size": CAMERA_SIZE}))
            picam.start()
            time.sleep(CAMERA_WARMUP_S)
            raw = picam.capture_array()
        finally:
            try:
                picam.stop()
            except Exception:
                pass
            try:
                picam.close()
            except Exception:
                pass

        h, w = raw.shape[:2]
        # Picamera2 "RGB888" 在树莓派上实际输出 BGR 排列 (同 apps/ai 实测结论)
        img = Image.frombytes("RGB", (w, h), raw.tobytes(), "raw", "BGR")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=CAMERA_JPEG_QUALITY)
        return buf.getvalue(), img

    def capture_base64(self, size=CAMERA_THUMB_SIZE,
                       quality=CAMERA_THUMB_QUALITY,
                       budget=CAMERA_B64_BUDGET) -> str:
        """拍照并压缩为 size 大小的 JPEG base64 (体积自适应, 不超 MQTT 上行预算)"""
        _, img = self.capture()
        return thumb_base64(img, size=size, quality=quality, budget=budget)

    # ---------- 视觉分析 ----------
    def explain(self, question: str, jpeg: bytes = None) -> str:
        """拍照并上传服务端 VLM 分析, 返回分析文本; 失败抛 RuntimeError。
        可传入已拍好的 jpeg (配合上屏预览, 避免重复拍照)。"""
        if not self.explain_url:
            raise RuntimeError("视觉分析地址未配置 (等待服务器 MCP initialize 下发)")
        if jpeg is None:
            jpeg, _ = self.capture()
        headers = {
            "Device-Id": self.device_id,
            "Client-Id": self.client_id,
        }
        if self.explain_token:
            headers["Authorization"] = f"Bearer {self.explain_token}"
        # 元组列表保证字段顺序: 服务端按 question → file 顺序读取
        files = [
            ("question", (None, question or "这张照片里有什么?")),
            ("file", ("camera.jpg", jpeg, "image/jpeg")),
        ]
        print(f"[vision] 上传分析: {len(jpeg)//1024}KB, 问题: {question}")
        resp = requests.post(self.explain_url, headers=headers,
                             files=files, timeout=UPLOAD_TIMEOUT)
        data = resp.json()
        if data.get("success"):
            return data.get("response", "")
        raise RuntimeError(data.get("message") or f"视觉分析失败 (HTTP {resp.status_code})")
