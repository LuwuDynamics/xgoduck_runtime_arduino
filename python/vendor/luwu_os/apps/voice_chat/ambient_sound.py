"""待机声音分类：共享唤醒词 PCM，独立、限队列的 YAMNet 推理线程。

本模块不依赖聊天协议，不发送或保存录音。缺少模型/运行库时只关闭此功能。
"""
import csv
import threading
import time
import zipfile
from collections import deque
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
SAMPLE_RATE = 16000


def load_catalog(path):
    with open(path, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    if len(rows) != 521 or [int(r["index"]) for r in rows] != list(range(521)):
        raise ValueError("声音标签必须按模型顺序包含完整的 521 类")
    for row in rows:
        if not all(row[k].strip() for k in ("label_en", "label_cn", "emotion")):
            raise ValueError(f"声音标签不完整: {row['index']}")
        row["index"] = int(row["index"])
        row["threshold"] = float(row["threshold"]) if row["threshold"] else None
        row["confirmations"] = int(row["confirmations"]) if row["confirmations"] else None
        if row["threshold"] is not None and not 0 <= row["threshold"] <= 1:
            raise ValueError("分类阈值必须在 0 到 1 之间")
        if row["confirmations"] is not None and row["confirmations"] < 1:
            raise ValueError("连续确认次数至少为 1")
        row["enabled"] = row["enabled"].lower() not in ("0", "false", "no")
    return rows


class SoundPolicy:
    """先取全局最高分，再判断阈值；不会把被禁用/低分的声音硬猜成另一类。"""
    def __init__(self, catalog, threshold=0.4, confirmations=2, hold_seconds=2.5):
        self.catalog = catalog
        self.threshold = max(0.0, min(1.0, float(threshold)))
        self.confirmations = max(1, int(confirmations))
        self.hold_seconds = max(0.0, float(hold_seconds))
        self.reset()

    def reset(self):
        self.candidate = None
        self.hits = 0
        self.current = None
        self.changed_at = float("-inf")

    def update(self, scores, now):
        scores = np.asarray(scores).reshape(-1)
        if scores.size != len(self.catalog) or not np.isfinite(scores).all():
            raise ValueError("无效的 YAMNet 输出")
        index = int(scores.argmax())
        row = self.catalog[index]
        threshold = self.threshold if row["threshold"] is None else row["threshold"]
        if not row["enabled"] or scores[index] < threshold:
            self.candidate, self.hits = None, 0
            return None
        if self.candidate == index:
            self.hits += 1
        else:
            self.candidate, self.hits = index, 1
        required = row["confirmations"] or self.confirmations
        if self.hits < required:
            return None
        if self.current != index:
            if now - self.changed_at < self.hold_seconds:
                return None
            self.current, self.changed_at = index, now
        return dict(row, score=float(scores[index]))


class YamnetModel:
    def __init__(self, path, catalog, threads=1):
        # 校验官方模型内置标签，防止换模型后 521 个索引静默错位。
        with zipfile.ZipFile(path) as archive:
            labels = archive.read("yamnet_label_list.txt").decode().splitlines()
        if labels != [r["label_en"] for r in catalog]:
            raise ValueError("模型标签与声音映射表不一致")
        try:
            from ai_edge_litert.interpreter import Interpreter
        except ImportError:
            from tflite_runtime.interpreter import Interpreter
        self.interpreter = Interpreter(model_path=str(path), num_threads=max(1, int(threads)))
        self.interpreter.allocate_tensors()
        inputs = self.interpreter.get_input_details()
        outputs = self.interpreter.get_output_details()
        if (len(inputs) != 1 or inputs[0]["dtype"] != np.float32
                or int(np.prod(inputs[0]["shape"])) != 15600):
            raise ValueError("需要官方 float32、15600 样本的 YAMNet 分类模型")
        self.input = inputs[0]
        self.output = next(o for o in outputs if int(np.prod(o["shape"])) == 521)
        self.samples = 15600  # 975 ms PCM -> 96 个 log-mel 帧

    def predict(self, pcm):
        self.interpreter.set_tensor(self.input["index"], pcm.reshape(self.input["shape"]))
        self.interpreter.invoke()
        return self.interpreter.get_tensor(self.output["index"]).reshape(-1)


class AmbientSoundDetector:
    def __init__(self, config, on_result, eligible=lambda: True):
        self.config = config
        self.on_result = on_result
        self.eligible = eligible
        self.catalog = load_catalog(config.get("labels_path") or ROOT / "apps/voice_chat/sound_labels.csv")
        self._lock = threading.Lock()
        self._capture_lock = threading.Lock()
        self._queue = deque(maxlen=3)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread = None
        self._stream = None
        self._enabled = False
        self._epoch = 0
        self._sequence = 0
        self._muted_until = 0.0
        self.failed = False

    def start(self):
        self._thread = threading.Thread(target=self._run, name="ambient-yamnet", daemon=True)
        self._thread.start()

    def set_enabled(self, enabled):
        with self._lock:
            self._enabled = enabled and not self._stop.is_set()
            self._epoch += 1
            self._queue.clear()
            self._muted_until = time.monotonic() + float(self.config.get("resume_delay_seconds", 1.0))
        if not enabled:
            self.close_input()
        self._wake.set()

    def is_current(self, epoch):
        with self._lock:
            return self._enabled and not self._stop.is_set() and epoch == self._epoch

    def feed(self, pcm):
        """由麦克风回调调用：只复制入有界队列，绝不在录音线程推理。"""
        now = time.monotonic()
        with self._lock:
            if not self._enabled or self.failed or self._stop.is_set():
                return
            if not self.eligible():
                self._epoch += 1
                self._queue.clear()
                self._muted_until = now + 1.0
                return
            if now < self._muted_until:
                return
            self._sequence += 1
            self._queue.append((self._epoch, self._sequence, now,
                                np.asarray(pcm, dtype=np.float32).reshape(-1).copy()))
        self._wake.set()

    def open_input(self):
        """仅在唤醒词未启用/缺模型时自行采音；正常情况完全复用唤醒词流。"""
        with self._capture_lock:
            if self._stream is not None or self.failed or self._stop.is_set():
                return
            import sounddevice as sd
            stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                    blocksize=8000, callback=lambda data, *args: self.feed(data[:, 0]))
            try:
                stream.start()
            except Exception:
                stream.close()
                raise
            self._stream = stream

    def close_input(self):
        with self._capture_lock:
            stream, self._stream = self._stream, None
            if stream is not None:
                stream.close()

    def close(self):
        self._stop.set()
        self.set_enabled(False)
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def _run(self):
        try:
            model = YamnetModel(self.config.get("model_path") or ROOT / "model/yamnet.tflite",
                                self.catalog, self.config.get("num_threads", 1))
            policy = SoundPolicy(self.catalog, self.config.get("score_threshold", 0.4),
                                 self.config.get("confirmations", 2),
                                 self.config.get("hold_seconds", 2.5))
            hop = max(8000, int(SAMPLE_RATE * float(self.config.get("interval_seconds", 0.5))))
            ring = np.empty(0, dtype=np.float32)
            epoch, sequence, pending = -1, -1, 0
            print("[ambient] YAMNet 就绪: 521 类, 本地待机识别", flush=True)
            while not self._stop.is_set():
                self._wake.wait(0.2)
                self._wake.clear()
                while not self._stop.is_set():
                    with self._lock:
                        item = self._queue.popleft() if self._queue else None
                    if item is None:
                        break
                    item_epoch, item_sequence, captured_at, pcm = item
                    if not self.is_current(item_epoch) or time.monotonic() - captured_at > 1.5:
                        continue
                    if item_epoch != epoch or item_sequence != sequence + 1:
                        ring = np.empty(0, dtype=np.float32)
                        pending = 0
                        policy.reset()
                    epoch, sequence = item_epoch, item_sequence
                    ring = np.concatenate((ring, pcm))[-model.samples:]
                    pending += len(pcm)
                    if len(ring) < model.samples or pending < hop:
                        continue
                    pending = 0
                    scores = model.predict(ring)
                    # 推理可能跨过唤醒/切聊天，旧结果不得再影响屏幕。
                    if (not self.is_current(epoch) or not self.eligible()
                            or time.monotonic() - captured_at > 1.5):
                        continue
                    event = policy.update(scores, time.monotonic())
                    if event is not None:
                        self.on_result(dict(event, epoch=epoch,
                                            display_ms=int(self.config.get("display_seconds", 4.0) * 1000)))
        except Exception as e:
            self.failed = True
            print(f"[ambient] 声音分类已停用，聊天/唤醒不受影响: {e}", flush=True)
        finally:
            self.close_input()
