# -*- coding: utf-8 -*-
"""音频编解码器 (ALSA/sounddevice + opus)

替代 xiaozhi_test 的 PyAudio 实现:
  - 采集/播放各一个专职线程, 无轮询触发线程
  - 播放完成判定基于样本计数, 不靠 sleep 猜时序
"""

import os
import queue
import threading
import time
from collections import deque

import numpy as np
# opuslib 3.x consults ldconfig, which cannot see an App-local library bundle.
# Resolve just libopus explicitly when the UNO Q support installer staged it.
import ctypes.util
import math
from pathlib import Path
_find_library = ctypes.util.find_library
_local_opus = Path(os.environ.get('DUCK_NATIVE_LIB_DIR', '/nonexistent')) / 'libopus.so.0'
if _local_opus.is_file():
    ctypes.util.find_library = lambda name: str(_local_opus) if name == 'opus' else _find_library(name)
try:
    import opuslib
finally:
    ctypes.util.find_library = _find_library
import sounddevice as sd


class AudioConfig:
    INPUT_SAMPLE_RATE = 16000
    OUTPUT_SAMPLE_RATE = 16000  # 服务器 hello 会纠正 (服务端 TTS 即 16k opus)
    CHANNELS = 1
    FRAME_DURATION_MS = 60
    # 播放端 jitter buffer (C 方案, 与服务端 max_lead 发送水位配合):
    PREBUFFER_FRAMES = 5    # 开局攒 5 帧 (300ms 语音内容, 按帧数非墙钟) 再放行
    PLC_MAX_FRAMES = 3      # 断供时最多垫 3 帧 (180ms) 丢帧隐藏, 超限即真停


class LocalAEC:
    """端侧回声消除 (WebRTC AEC3 优先, speexdsp 兜底)

    参考信号 = 播放 PCM 写入连续时间轴环形缓冲, 采集时按时间顺序读取
    (无播放时补静音), 保证参考与近端时间轴始终平行; 剩余延迟
    (ALSA 输出缓冲 + 声学路径) 交给 AEC3 内部延迟估计器自行测量。
    处理器要求 10ms 子帧 (AEC3 硬性要求), 60ms 帧拆分处理, 写法对齐
    xiaozhi-server core/providers/aec/server_aec.py 的 _apply_aec。
    """

    SUB_FRAME_MS = 10
    REF_BUFFER_MS = 1500  # 参考环形缓冲容量 (覆盖最长播放积压)

    def __init__(self, sample_rate=16000, frame_ms=60, ref_delay_ms=120):
        self.sample_rate = sample_rate
        self.frame_ms = frame_ms
        self.ref_delay_ms = ref_delay_ms
        self.frame_size = int(sample_rate * frame_ms / 1000)
        self.sub_frame_size = int(sample_rate * self.SUB_FRAME_MS / 1000)
        self._silence = bytes(self.sub_frame_size * 2)

        # 参考缓冲: 设备时间轴的滑动窗口 (deque 存 10ms 子帧)。
        # 对齐策略: 采集重启时以当时队列深度为锚, 之后每次采集帧
        # 只从队头取 1 个子帧 —— 参考时间轴严格以设备时钟推进,
        # 与采集线程是否卡顿/丢帧无关 (AEC3 的 delay hint 吸收固定偏差)
        max_frames = int(self.REF_BUFFER_MS / self.SUB_FRAME_MS)
        self._ref_max = max_frames
        self._ref_queue = deque(maxlen=max_frames)
        self._ref_lock = threading.Lock()
        self._ref_leftover = b""   # 推入 PCM 不足一子帧的残余
        self._ref_calibrated = False  # 是否已建立深度锚点
        # 销毁保护: process/参考操作与 close 并发时直接放行,
        # 防止 close 销毁 ctypes 句柄的瞬间另一线程还在用 → 段错误
        self._destroyed = False

        self._backend = None
        self._aec = None
        self._sub_bytes = self.sub_frame_size * 2
        # 首选: py-xiaozhi 官方预编译 libwebrtc_apm.so (libs/webrtc_apm,
        # 免源码编译); 回退: pip aec-audio-processing; 再回退: speexdsp
        try:
            import ctypes
            from libs import webrtc_apm as wam
            apm = wam.WebRTCAudioProcessing()
            cfg = wam.create_default_config()
            cfg.echo.enabled = True
            cfg.echo.mobile_mode = False
            cfg.high_pass.enabled = True
            cfg.noise_suppress.enabled = True
            cfg.noise_suppress.noise_level = wam.NoiseSuppressionLevel.MODERATE
            if apm.apply_config(cfg) != 0:
                raise RuntimeError("apply_config 返回非 0")
            self._wam = wam
            self._apm = apm
            self._stream_cfg = apm.create_stream_config(sample_rate, 1)
            nf = self.sub_frame_size
            self._apm_fin = (ctypes.c_short * nf)()
            self._apm_fout = (ctypes.c_short * nf)()
            self._webrtc_frame = self.sub_frame_size * 2  # 10ms 帧字节数
            self._backend = "webrtc_apm_official"
            print(f"[audio] 端侧AEC: 官方预编译 libwebrtc_apm (参考缓冲 "
                  f"{self.REF_BUFFER_MS}ms, 延迟提示 {ref_delay_ms}ms)")
        except Exception as e:
            print(f"[audio] 官方 libwebrtc_apm 不可用: {e}")
            try:
                from aec_audio_processing import AudioProcessor
                ap = AudioProcessor(enable_aec=True, enable_ns=True, ns_level=3,
                                    enable_agc=False, enable_vad=False)
                ap.set_stream_format(sample_rate, 1)
                ap.set_reverse_stream_format(sample_rate, 1)
                self._aec = ap
                self._webrtc_frame = ap.get_frame_size() * 2  # 10ms 帧字节数
                self._backend = "webrtc_aec3"
                print(f"[audio] 端侧AEC: WebRTC AEC3 pip包 (参考缓冲 "
                      f"{self.REF_BUFFER_MS}ms, 延迟提示 {ref_delay_ms}ms)")
            except Exception as e2:
                print(f"[audio] aec-audio-processing 不可用: {e2}")
                try:
                    from speexdsp import EchoCanceller
                    self._aec = EchoCanceller.create(
                        self.sub_frame_size,
                        int(sample_rate * 300 / 1000),  # 300ms 滤波器长度
                        sample_rate)
                    self._backend = "speexdsp"
                    print(f"[audio] 端侧AEC: speexdsp (参考缓冲 {self.REF_BUFFER_MS}ms)")
                except Exception as e3:
                    print(f"[audio] speexdsp 也不可用: {e3}, 端侧AEC关闭")

    @property
    def available(self) -> bool:
        return self._backend is not None

    @property
    def backend(self):
        return self._backend

    # ---------- 参考缓冲 (采集线程排空 far 时 push / 采集帧时 pop) ----------
    def push_reference(self, pcm: bytes):
        """采集线程排空 far 队列时调用: 按 10ms 子帧切分入队 (设备时间轴)"""
        with self._ref_lock:
            buf = self._ref_leftover + pcm
            fsz = self.sub_frame_size * 2
            while len(buf) >= fsz:
                self._ref_queue.append(buf[:fsz])
                buf = buf[fsz:]
            self._ref_leftover = buf

    def pop_reference(self, n: int = 1) -> bytes:
        """为 n 个 10ms 近端子帧取对齐的参考。
        首次调用 (或 clear 后) 修剪到锚定深度再消费 —— 采集/打断重启
        都是时间轴断点; 之后每子帧固定从队头取 1 帧 —— 参考按设备
        时钟推进, 处理线程卡顿/丢块不会造成时间轴错位"""
        fsz = self.sub_frame_size * 2
        with self._ref_lock:
            if not self._ref_calibrated:
                self._trim_locked()
                self._ref_calibrated = True
            # 队列超过容量 80% 时丟最旧防溢出 (正常深度≈延迟+少量余量)
            over = len(self._ref_queue) - int(self._ref_max * 0.8)
            if over > 0:
                for _ in range(over):
                    self._ref_queue.popleft()
            out = b""
            for _ in range(n):
                if self._ref_queue:
                    out += self._ref_queue.popleft()
                else:
                    out += self._silence
            return out

    def clear(self):
        """打断场景 (清空播放队列): 旧参考全部作废"""
        with self._ref_lock:
            self._ref_queue.clear()
            self._ref_leftover = b""
            self._ref_calibrated = False

    def calibrate(self):
        """采集重启/打断场景: 保留连续的 far 历史 (队列已按设备时钟
        推进), 只修剪到 延迟+余量 深度 —— 避免积压的旧参考超出
        AEC3 滤波器范围造成错位"""
        with self._ref_lock:
            self._trim_locked()
            self._ref_calibrated = True

    def _trim_locked(self):
        """修剪到 延迟提示+150ms 余量 深度 (调用方需持有 _ref_lock)"""
        max_keep = int((self.ref_delay_ms + 150) / self.SUB_FRAME_MS)
        while len(self._ref_queue) > max_keep:
            self._ref_queue.popleft()

    # ---------- 处理 ----------
    def process(self, near: bytes, far: bytes) -> bytes:
        """近端帧 + 对齐后的同长参考帧 → 消回声后的帧 (按 10ms 子帧处理)"""
        if self._backend is None or self._destroyed:
            return near
        if self._backend == "webrtc_apm_official":
            import ctypes
            # 显式延迟提示: 回调消费→实际出声的声卡缓冲 + 声学路径,
            # 帮助 AEC3 延迟估计器快速收敛
            self._apm.set_stream_delay_ms(self.ref_delay_ms)
            n = min(len(near), len(far))
            # 拷成连续可写数组 (frombuffer 对 bytes 是只读视图,
            # 切片会产生临时对象, .ctypes.data 会指向已释放内存)
            ns = np.frombuffer(near[:n], dtype=np.int16).copy()
            fs = np.frombuffer(far[:n], dtype=np.int16).copy()
            out = bytearray()
            full = (len(ns) // self.sub_frame_size) * self.sub_frame_size
            for i in range(0, full, self.sub_frame_size):
                sl = slice(i, i + self.sub_frame_size)
                ctypes.memmove(self._apm_fin, fs[sl].ctypes.data, self._sub_bytes)
                if self._apm.process_reverse_stream(
                        self._apm_fin, self._stream_cfg,
                        self._stream_cfg, self._apm_fout) != 0:
                    out.extend(ns[sl].tobytes())
                    continue
                ctypes.memmove(self._apm_fin, ns[sl].ctypes.data, self._sub_bytes)
                if self._apm.process_stream(
                        self._apm_fin, self._stream_cfg,
                        self._stream_cfg, self._apm_fout) != 0:
                    out.extend(ns[sl].tobytes())
                    continue
                out.extend(bytes(self._apm_fout))
            out.extend(ns[full:].tobytes())    # 不足一子帧的残余直通
            out.extend(near[len(ns) * 2:])     # near 超出公共长度的尾部
            return bytes(out)
        if self._backend == "webrtc_aec3":
            # pip 包同样显式设延迟提示
            try:
                self._aec.set_stream_delay(self.ref_delay_ms)
            except Exception:
                pass
            n = min(len(near), len(far))
            out = bytearray()
            for i in range(0, n, self._webrtc_frame):
                sf = near[i:i + self._webrtc_frame]
                sr = far[i:i + self._webrtc_frame]
                if len(sf) == self._webrtc_frame and len(sr) == self._webrtc_frame:
                    self._aec.process_reverse_stream(sr)
                    out.extend(self._aec.process_stream(sf))
                else:
                    out.extend(sf)
            return bytes(out)
        # speexdsp
        n = min(len(near), len(far))
        out = bytearray()
        for i in range(0, n, self._sub_bytes):
            sf = near[i:i + self._sub_bytes]
            sr = far[i:i + self._sub_bytes]
            if len(sf) == self._sub_bytes and len(sr) == self._sub_bytes:
                out.extend(self._aec.process(sf, sr))
            else:
                out.extend(sf)
        return bytes(out)

    def close(self):
        # 先标记销毁: 并发进入 process/参考操作的线程会立即直通,
        # 之后再销毁 ctypes 句柄, 避免 use-after-free 段错误
        self._destroyed = True
        if self._backend == "webrtc_apm_official":
            try:
                apm = getattr(self, "_apm", None)
                if apm and getattr(self, "_stream_cfg", None):
                    apm.destroy_stream_config(self._stream_cfg)
                if apm and getattr(apm, "_handle", None):
                    # 显式销毁 APM 并置空句柄: __del__ 在解释器退出时才跑,
                    # 期间 ctypes 库可能被卸载; 置空后 __del__ 幂等跳过
                    try:
                        self._wam._lib.WebRTC_APM_Destroy(apm._handle)
                    except Exception:
                        pass
                    apm._handle = None
            except Exception:
                pass
            self._apm = None
            self._stream_cfg = None
        if self._backend == "webrtc_aec3":
            try:
                if hasattr(self._aec, "close"):
                    self._aec.close()
            except Exception:
                pass
        self._aec = None
        self._backend = None


class AudioCodec:
    def __init__(self, input_rate=None, output_rate=None, frame_ms=None,
                 aec_ref_delay_ms=120, input_device=None, output_device=None, output_latency=None,
                 prebuffer_ms=None, input_latency=None, input_channels=1):
        if type(input_channels) is not int or input_channels not in (1, 2):
            raise ValueError('input_channels must be 1 or 2')
        self.input_channels = input_channels
        self.input_downmix = None
        if input_latency is not None and (isinstance(input_latency, bool)
                or not isinstance(input_latency, (int, float))
                or not math.isfinite(input_latency) or not .06 <= input_latency <= .6):
            raise ValueError('input_latency must be 0.06–0.6 seconds or None')
        if output_latency is not None and (isinstance(output_latency, bool)
                or not isinstance(output_latency, (int, float))
                or not math.isfinite(output_latency) or not .06 <= output_latency <= .6):
            raise ValueError('output_latency must be 0.06–0.6 seconds or None')
        self.output_latency = output_latency
        self.input_latency = input_latency
        self.input_device, self.output_device = input_device, output_device
        self.input_transform = lambda pcm: pcm
        self.output_transform = lambda pcm: pcm
        self.output_observer = self.output_reset_observer = None
        self.input_rate = input_rate or AudioConfig.INPUT_SAMPLE_RATE
        self.output_rate = output_rate or AudioConfig.OUTPUT_SAMPLE_RATE
        self.frame_ms = frame_ms or AudioConfig.FRAME_DURATION_MS
        if prebuffer_ms is not None and (isinstance(prebuffer_ms, bool)
                or not isinstance(prebuffer_ms, (int, float)) or not math.isfinite(prebuffer_ms)
                or not 60 <= prebuffer_ms <= 1500):
            raise ValueError('prebuffer_ms must be 60–1500 milliseconds or None')
        self.prebuffer_frames = (AudioConfig.PREBUFFER_FRAMES if prebuffer_ms is None else
                                 math.ceil(prebuffer_ms / self.frame_ms))
        self.prebuffer_ms = prebuffer_ms

        self.input_frame_size = int(self.input_rate * self.frame_ms / 1000)
        self.output_frame_size = int(self.output_rate * self.frame_ms / 1000)

        # 端侧 AEC (库缺失时 available=False, 采集链自动退化为直通)
        self.aec = LocalAEC(self.input_rate, self.frame_ms, aec_ref_delay_ms)
        self.aec_enabled = False  # 运行时开关 (由小程序 self.set_aec 控制)

        self.opus_encoder = opuslib.Encoder(
            self.input_rate, AudioConfig.CHANNELS, opuslib.APPLICATION_AUDIO)
        self.opus_decoder = opuslib.Decoder(
            self.output_rate, AudioConfig.CHANNELS)

        # 播放队列 (约 15 秒上限)
        max_q = int(15 * 1000 / self.frame_ms)
        self.play_queue = queue.Queue(maxsize=max_q)

        self.input_stream = None
        self.output_stream = None
        self._running = False
        self._capture_enabled = False  # 是否真正编码上送 (由状态机控制)
        self._capture_thread = None
        self._play_thread = None
        # 输出回调的解码 PCM 缓冲 (播放线程填充, 回调消费)
        self._play_pcm_buf = b""
        self._play_pcm_lock = threading.Lock()
        # Absolute byte ranges for local cues, aligned with the shared PCM
        # queue. Keep them across tts:start, which preserves queued send.wav.
        self._play_pcm_offset = 0
        self._mouthless_ranges = deque()
        # Sequence watermarks pair each near block with the far blocks that
        # existed on its callback. Resampling/AEC-reference/dump work belongs
        # only to the capture worker, never either PortAudio callback.
        self._far_pending = deque(maxlen=100)
        self._far_seq = 0
        self._reference_generation = 0
        self._far_held = None  # worker-owned item newer than the current near block
        self._far_queue_drops = 0
        self._lock = threading.Lock()
        self._play_state_lock = threading.RLock()
        # 采集回调推来的近端块队列 (阻塞式 read 在 GIL 卡顿时会积压
        # 多帧但每次只报一次 overflow, 参考时间轴无法同步 → 改回调模式)
        self._near_q = deque(maxlen=30)
        self._near_q_lock = threading.Lock()
        self._capture_generation = 0
        self._capture_sync = None
        self._capture_last_seq = None
        self._capture_queue_drops = 0
        self._capture_queue_peak = 0
        self._input_callbacks = 0
        self._aec_frames = 0
        self._aec_reference_resets = 0
        self._last_input_at = None
        self._max_input_gap_ms = self._max_input_work_ms = 0.
        self._max_capture_work_ms = self._max_capture_age_ms = 0.

        # 调试录制: XIAOZHI_AEC_DUMP=1 开启 (默认落盘 xgo-media/aec_test),
        # 也可直接指定目录。近端原始/AEC后/参考 三路 16k mono s16le 原始 PCM,
        # 供对比评估 AEC 效果
        self._dump_dir = os.environ.get("XIAOZHI_AEC_DUMP", "")
        if self._dump_dir in ("1", "true", "yes"):
            self._dump_dir = "/opt/luwu-os/xgo-media/aec_test"
        self._dump_files = {}
        if self._dump_dir:
            try:
                os.makedirs(self._dump_dir, exist_ok=True)
                for name in ("near", "aec_out", "ref"):
                    self._dump_files[name] = open(
                        os.path.join(self._dump_dir, f"{name}.pcm"), "ab")
                print(f"[audio] AEC 调试录制已开启: {self._dump_dir} "
                      f"(near/aec_out/ref .pcm, 16k mono s16le)")
            except Exception as e:
                print(f"[audio] AEC 调试录制开启失败: {e}")
                self._dump_files = {}

        # 播放完成判定: 回调模式下看解码缓冲 (见 is_playback_idle)

        # 播放欠载检测 (诊断 TTS 断播): 仅在 opus 已供数 (真正播报期) 才统计,
        # 避免把思考/说话期的正常静音误判。供数恢复时若静音超阈值则打印。
        # _out_had_data/_underrun_start 仅输出回调线程读写, 无锁。
        self._out_had_data = False
        self._underrun_start = 0.0
        self._opus_fed = False   # write_opus 置位, clear_play_queue 复位
        self._last_opus_time = 0.0  # 供数间隙统计: 上一 opus 包到达时刻
        # jitter buffer 状态 (仅播放链线程读写, 无锁):
        # _prebuf: None=非攒集 / list=预缓冲攒集中 (攒的是未解码 opus, 内存小)
        # _plc_count: 本次断供已垫的 PLC 帧数, 真数据到达时清零
        # _plc_stopped: tts stop 已到达, 禁止恢复垫帧预算 (迟到尾包不得复活 PLC)
        self._prebuf = None
        self._plc_count = 0
        self._plc_stopped = False
        self._status_log_ts = 0.0  # 回调 status 异常节流打印 (underflow/overflow 留痕)
        # Real-time callbacks only record bounded events; the decoder thread logs.
        self._stream_events = deque(maxlen=32)
        self._output_underflows = self._input_overflows = self._output_callbacks = 0
        self._pcm_starvations = 0
        self._last_output_at = None
        self._max_output_gap_ms = 0.
        self._audible_until = 0.
        self._opus_generation = self._pcm_generation = 0
        self._decode_active = False

    # ---------- 流管理 ----------
    def start(self):
        """启动音频工作线程。由应用决定何时打开输入流。"""
        if self._running:
            return
        self._running = True

        # 输出流用回调模式: 回调消费数据的时刻即设备时间轴,
        # 在此处喂 AEC far 参考 (含静音块), 保证参考与采集时间轴严格平行
        self.output_stream = sd.RawOutputStream(
            device=self.output_device,
            samplerate=self.output_rate, channels=AudioConfig.CHANNELS,
            dtype="int16", blocksize=self.output_frame_size,
            latency=self.output_latency,
            callback=self._output_callback)
        self.output_stream.start()

        self._capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._capture_thread.start()
        self._play_thread = threading.Thread(target=self._play_loop, daemon=True)
        self._play_thread.start()

    def open_input(self):
        """Open capture with bounded callback work and worker-side near/far pairing."""
        with self._lock:
            if self.input_stream is not None:
                return
            stream = None
            try:
                stream = sd.RawInputStream(
                    device=self.input_device,
                    samplerate=self.input_rate, channels=self.input_channels,
                    dtype="int16", blocksize=self.input_frame_size,
                    latency=self.input_latency,
                    callback=self._input_callback)
                self._last_input_at = None
                self._capture_generation += 1
                self.input_stream = stream
                stream.start()
            except Exception as e:
                print(f"[audio] 打开采集流失败: {e}")
                self.input_stream = None
                if stream is not None:
                    try:
                        stream.close()
                    except Exception:
                        pass

    def close_input(self):
        """停止采集并释放设备。PortAudio 等当前回调结束后才返回。"""
        with self._lock:
            stream, self.input_stream = self.input_stream, None
            self._capture_generation += 1
        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._near_q_lock:
            self._near_q.clear()

    def close(self):
        # 顺序关键: 先停工作线程 (它们持有 AEC ctypes 句柄的并发使用),
        # join 完成后再停流/销毁 AEC, 否则 close 与 process 竞态会段错误
        self._running = False
        for t in (self._capture_thread, self._play_thread):
            if t and t.is_alive():
                t.join(timeout=2.0)
        self.close_input()
        for s in (self.output_stream,):
            try:
                if s:
                    s.stop()
                    s.close()
            except Exception:
                pass
        self.aec.close()
        for f in self._dump_files.values():
            try:
                f.close()
            except Exception:
                pass
        self._dump_files = {}

    # ---------- 采集 ----------
    def set_capture_enabled(self, enabled: bool, *, keep_pending=False):
        with self._near_q_lock:
            was = self._capture_enabled
            self._capture_enabled = enabled
            if enabled != was and not keep_pending:
                self._capture_generation += 1
            if not enabled:
                self._near_q.clear()  # 停捕即丢弃已攒近端 (过时数据)
        # The worker recalibrates after draining this generation's far watermark.
        # Calibrating here would race queued reference work from the old turn.

    on_audio_frame = None  # 回调: (opus_bytes) 由 Application 设置
    on_idle_audio = None   # Optional wake detector feed on the same input stream.

    def _input_callback(self, indata, frames, t, status):
        """Copy one block and its far watermark; no AEC, resampling or file I/O."""
        started = time.monotonic()
        self._log_stream_status("采集", status)
        if not self._running:
            return
        if self._last_input_at is not None:
            self._max_input_gap_ms = max(self._max_input_gap_ms, (started - self._last_input_at) * 1000)
        self._last_input_at = started
        self._input_callbacks += 1
        with self._near_q_lock:
            generation, far_seq = self._capture_generation, self._far_seq
            pcm = bytes(indata) if self._capture_enabled or self.on_idle_audio is not None else None
            if len(self._near_q) == self._near_q.maxlen:
                self._capture_queue_drops += 1
            self._near_q.append((generation, pcm, far_seq, started,
                                 self._input_overflows, self._input_callbacks))
            self._capture_queue_peak = max(self._capture_queue_peak, len(self._near_q))
        self._max_input_work_ms = max(self._max_input_work_ms, (time.monotonic() - started) * 1000)

    def _capture_loop(self):
        while self._running:
            if self.input_stream is None:
                self._drain_far()
                time.sleep(0.02)
                continue
            with self._near_q_lock:
                block = self._near_q.popleft() if self._near_q else None
                capturing = self._capture_enabled
                idle_sink = self.on_idle_audio if not capturing else None
            if block is None:
                time.sleep(0.005)
                continue
            generation, pcm, far_seq, captured_at, overflows, sequence = block
            previous = self._capture_last_seq
            self._capture_last_seq = sequence
            started = time.monotonic()
            try:
                self._drain_far(through=far_seq)
                if (pcm is None or (not capturing and idle_sink is None) or self.on_audio_frame is None
                        or generation != self._capture_generation):
                    continue
                self._max_capture_age_ms = max(self._max_capture_age_ms, (started - captured_at) * 1000)
                if len(pcm) != self.input_frame_size * 2 * self.input_channels:
                    continue
                if self.input_channels > 1:
                    if self.input_downmix is None:
                        raise ValueError('multichannel capture requires an explicit mono selector')
                    pcm = self.input_downmix(pcm)
                    if len(pcm) != self.input_frame_size * 2:
                        raise ValueError('mono selector returned an invalid frame')
                if capturing:
                    self._dump("near", pcm)
                    sync = (generation, overflows)
                    skipped = previous is not None and sequence != previous + 1
                    if self.aec.available and (sync != self._capture_sync or skipped):
                        # Re-anchor after the far history for this block has arrived.
                        self.aec.calibrate()
                        self._capture_sync = sync
                    if self.aec.available and self.aec_enabled:
                        n_sub = self.input_frame_size // self.aec.sub_frame_size
                        far = self.aec.pop_reference(max(1, n_sub))
                        pcm = self.aec.process(pcm, far)
                        self._aec_frames += 1
                        self._dump("aec_out", pcm)
                # Product capture gain runs once in this worker, after AEC,
                # without changing its near/far levels or the real-time callback.
                pcm = self.input_transform(pcm)
                if idle_sink is not None:
                    idle_sink(pcm, captured_at, overflows, sequence)
                # Use the same encoder for the short wake history and live frames.
                opus = self.opus_encoder.encode(pcm, self.input_frame_size)
                if (self._running and self.input_stream is not None
                        and generation == self._capture_generation
                        and (self._capture_enabled or self.on_idle_audio is not None)):
                    self.on_audio_frame(opus)
            except Exception as e:
                self._capture_sync = None
                print(f"[audio] 采集异常: {e}")
                time.sleep(0.1)
            finally:
                self._max_capture_work_ms = max(self._max_capture_work_ms,
                                                (time.monotonic() - started) * 1000)

    # ---------- 播放 ----------
    def write_opus(self, opus_data: bytes):
        with self._play_state_lock:
            self._write_opus_locked(opus_data)

    def _write_opus_locked(self, opus_data):
        # 供数间隙统计: 服务端超过 2.5 帧没来包则记录 (配合协议层序号跳变
        # 可区分服务端断供 vs 网络丢包; 间隙后队列深度反映缓冲是否垫平)
        now = time.time()
        if self._opus_fed and self._last_opus_time:
            gap = (now - self._last_opus_time) * 1000
            if gap > 150:
                # PCM 水位一并打出: 间隙结束时刻的真实缓冲深度,
                # lead_start = gap + 水位 - PLC垫入, 可直接还原服务端超前量 (验证 max_lead)
                with self._play_pcm_lock:
                    pcm_ms = len(self._play_pcm_buf) * 1000 // (2 * self.output_rate)
                print(f"[audio] opus 供数间隙 {gap:.0f}ms "
                      f"(间隙前队列={self.play_queue.qsize()}, "
                      f"PCM水位={pcm_ms}ms)")
        self._last_opus_time = now
        if not self._opus_fed:
            self._opus_fed = True  # TTS 音频开始供数 (欠载检测以此为播报期起点)
            self._prebuf = []      # 新一轮供数: 开启预缓冲攒集
            self._plc_count = 0
            self._plc_stopped = False
            # 新供数期同步复位欠载检测: 防上一回合尾部静音的 _underrun_start
            # 跨回合残留, 在本回合首个数据到达时打出 bogus 超长欠载
            # (实测遭过 4319ms 假阳性: 回合尾垫帧排空后重置前一直挂着)
            self._underrun_start = 0.0
            self._out_had_data = False
        if self._prebuf is not None:
            # 预缓冲: 攒够 PREBUFFER_FRAMES 帧语音内容后一次性放行,
            # 开局即有水位, 吸收开头网络抖动 (帧数判定, 非墙钟等待)
            self._prebuf.append(opus_data)
            if len(self._prebuf) < self.prebuffer_frames:
                return
            for pkt in self._prebuf:
                self._put_play(pkt)
            print(f"[audio] 预缓冲放行 {len(self._prebuf)}帧 (开局攒集完成)")
            self._prebuf = None
        else:
            self._put_play(opus_data)
        if not self._plc_stopped:
            self._plc_count = 0  # 真数据到达, 恢复 PLC 垫帧预算 (回合结束除外)

    def _put_play(self, opus_data: bytes):
        try:
            self.play_queue.put_nowait((self._opus_generation, opus_data))
        except queue.Full:
            try:
                self.play_queue.get_nowait()  # 丢最旧
                self.play_queue.put_nowait((self._opus_generation, opus_data))
            except queue.Empty:
                pass

    def stop_plc(self):
        with self._play_state_lock:
            self._stop_plc_locked()

    def _stop_plc_locked(self):
        """tts stop 到达时调用: 禁止回合结束后的 PLC 拖尾。
        句间断供仍可垫 (预算未用完), 回合结束不垫,
        保证 is_playback_idle 及时判闲、状态机不被垫帧卡住。
        同时放行预缓冲残余: 超短回答 (<PREBUFFER_FRAMES 帧) 攒不满,
        若不放行, is_playback_idle 会立即判闲 → 状态机清队列时连音频一起丢掉"""
        self._plc_stopped = True
        self._plc_count = AudioConfig.PLC_MAX_FRAMES
        if self._prebuf is not None:
            for pkt in self._prebuf:
                self._put_play(pkt)
            print(f"[audio] 预缓冲残余放行 {len(self._prebuf)}帧 (tts stop)")
            self._prebuf = None

    def play_wav(self, path: str, volume: float = 1.0):
        """播放本地 WAV 提示音: 解码 → 单声道 → 重采样到输出采样率 → 混入播放缓冲。

        与 opus 播放共用同一输出流/回调缓冲, 不会与 TTS 抢占声卡设备;
        线程安全 (持 _play_pcm_lock), 可在任意线程调用。volume 为附加增益系数。
        """
        generation = self._pcm_generation
        try:
            import wave
            with wave.open(path, "rb") as wf:
                sr = wf.getframerate()
                ch = wf.getnchannels()
                sw = wf.getsampwidth()
                data = wf.readframes(wf.getnframes())
            if sw != 2:
                print(f"[audio] 提示音格式不支持 (sampwidth={sw}): {path}")
                return
            s = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            if ch > 1:
                s = s.reshape(-1, ch).mean(axis=1)  # 立体声 → 单声道
            if sr != self.output_rate:
                # 线性插值重采样 (与 _push_aec_reference 同法)
                n_out = int(self.output_rate * s.size / sr)
                x = np.linspace(0.0, 1.0, s.size)
                s = np.interp(np.linspace(0.0, 1.0, n_out), x, s)
            pcm = np.clip(s * volume, -32768, 32767).astype(np.int16).tobytes()
            with self._play_pcm_lock:
                if generation != self._pcm_generation:
                    return
                self._append_local_pcm_locked(pcm)
            print(f"[audio] 提示音已入队: {os.path.basename(path)} "
                  f"({len(pcm)//2/self.output_rate:.2f}s)")
        except Exception as e:
            print(f"[audio] 提示音播放失败 ({path}): {e}")

    def _append_local_pcm_locked(self, pcm):
        """Queue an audible cue whose samples must not animate the TTS mouth."""
        if not pcm:
            return
        start = self._play_pcm_offset + len(self._play_pcm_buf)
        end = start + len(pcm)
        if self._mouthless_ranges and self._mouthless_ranges[-1][1] == start:
            start = self._mouthless_ranges.pop()[0]
        self._mouthless_ranges.append((start, end))
        self._play_pcm_buf += pcm

    def queue_local_pcm(self, pcm):
        """Queue one bounded mono PCM16 call on an idle, running output stream.

        Product MCP adapter owns capture/TTS scheduling. Returns a generation
        ticket so an interrupt/reset cannot be reported as successful playback.
        """
        if not isinstance(pcm, bytes) or len(pcm) % 2 or not 0 < len(pcm) <= self.output_rate * 2 * 2:
            raise ValueError('Local sound must be 0–2 seconds of mono PCM16')
        with self._play_state_lock:
            if not self._running or not self.output_stream or not self.output_stream.active:
                raise ValueError('USB speaker output stream unavailable')
            if not self.is_playback_idle():
                raise ValueError('Chat speaker busy')
            with self._play_pcm_lock:
                self._append_local_pcm_locked(pcm)
                return self._pcm_generation

    def local_pcm_current(self, generation):
        return self._running and generation == self._pcm_generation

    def clear_play_queue(self, clear_pcm=True, *, reset_aec=True):
        with self._play_state_lock:
            self._clear_play_queue_locked(clear_pcm, reset_aec=reset_aec)

    def _clear_play_queue_locked(self, clear_pcm, *, reset_aec=True):
        """清空播放队列; clear_pcm=False 时保留已解码入缓冲的音频
        (用于 TTS 新句开始: 只丢未解码的 opus 残留, 不清掉提示音)"""
        self._opus_generation += 1
        cleared = 0
        while True:
            try:
                self.play_queue.get_nowait()
                cleared += 1
            except queue.Empty:
                break
        if clear_pcm:
            with self._play_pcm_lock:
                self._pcm_generation += 1
                self._play_pcm_buf = b""
                self._play_pcm_offset = 0
                self._mouthless_ranges.clear()
            if self.output_reset_observer is not None:
                self.output_reset_observer()
            # A software queue clear cannot remove 500 ms already queued in ALSA.
            if self._audible_until > time.monotonic() and self.output_stream and self._running:
                self.output_stream.abort()
                self.output_stream.start()
                self._last_output_at = None
            self._audible_until = 0.
        if reset_aec:
            self._reference_generation += 1
            self._far_pending.clear()
            self.aec.clear()
            self._aec_reference_resets += 1
        self._opus_fed = False  # 本轮 TTS 结束/被打断, 复位欠载检测
        self._last_opus_time = 0.0
        self._prebuf = None    # jitter buffer 状态一并复位 (打断立即静音)
        self._plc_count = 0
        self._plc_stopped = False
        if cleared:
            print(f"[audio] 清空播放队列 ({cleared} 帧)")

    def is_playback_idle(self) -> bool:
        """Wait for queued/decoding PCM and the last submitted DAC samples."""
        if not self.play_queue.empty() or self._prebuf is not None or self._decode_active:
            return False
        with self._play_pcm_lock:
            return len(self._play_pcm_buf) == 0 and time.monotonic() >= self._audible_until

    def wait_playback_done(self, timeout=10.0):
        start = time.time()
        while not self.is_playback_idle() and time.time() - start < timeout:
            time.sleep(0.05)
        return self.is_playback_idle()

    def _log_stream_status(self, tag: str, status):
        """PortAudio 回调 status 异常节流打印: underflow/overflow 在回调层面
        静默发生 (听感变弱/断字但 Python 层水位正常), 不留痕则无法归因;
        同类异常 1s 内只打一条防刷屏"""
        if not status:
            return
        if tag == "输出" and status.output_underflow:
            self._output_underflows += 1
        elif tag == "采集" and status.input_overflow:
            self._input_overflows += 1
        now = time.time()
        if now - self._status_log_ts < 1.0:
            return
        self._status_log_ts = now
        self._stream_events.append(('status', tag, status))

    def _drain_stream_events(self):
        while self._stream_events:
            kind, value, detail = self._stream_events.popleft()
            if kind == 'status':
                context = f"; capture={self.capture_status()}" if value == '采集' else ''
                print(f"[audio] {value}流异常: {detail}{context}")
            else:
                print(f"[audio] TTS 播放欠载 {value:.0f}ms")

    def capture_status(self):
        try:
            latency_ms = round(self.input_stream.latency * 1000, 1) if self.input_stream else None
        except Exception:
            latency_ms = None
        with self._near_q_lock:
            queued = len(self._near_q)
        backend = getattr(self.aec, 'backend', None)
        return dict(input_latency_ms=latency_ms, input_callbacks=self._input_callbacks,
                    aec_enabled=bool(self.aec_enabled and self.aec.available),
                    aec_backend=backend if isinstance(backend, str) else None,
                    aec_frames=self._aec_frames, aec_reference_resets=self._aec_reference_resets,
                    capture_queue_ms=queued * self.frame_ms,
                    capture_queue_peak_ms=self._capture_queue_peak * self.frame_ms,
                    capture_queue_drops=self._capture_queue_drops, far_queue_drops=self._far_queue_drops,
                    max_input_callback_gap_ms=round(self._max_input_gap_ms, 1),
                    max_input_callback_ms=round(self._max_input_work_ms, 3),
                    max_capture_work_ms=round(self._max_capture_work_ms, 1),
                    max_capture_age_ms=round(self._max_capture_age_ms, 1))

    def playback_status(self):
        with self._play_pcm_lock:
            pcm_ms = len(self._play_pcm_buf) * 1000 / (2 * self.output_rate)
        try:
            latency_ms = self.output_stream.latency * 1000 if self.output_stream else None
        except Exception:
            latency_ms = None
        return dict(prebuffer_ms=self.prebuffer_frames * self.frame_ms,
                    output_latency_ms=latency_ms, pcm_buffer_ms=round(pcm_ms, 1),
                    queued_audio_ms=self.play_queue.qsize() * self.frame_ms,
                    output_underflows=self._output_underflows, input_overflows=self._input_overflows,
                    pcm_starvations=self._pcm_starvations, output_callbacks=self._output_callbacks,
                    hardware_tail_ms=round(max(0., self._audible_until - time.monotonic()) * 1000, 1),
                    max_callback_gap_ms=round(self._max_output_gap_ms, 1), **self.capture_status())

    def _output_callback(self, outdata, frames, t, status):
        """输出回调 (PortAudio 实时线程): 只做取数/补静音/拷贝入队,
        微秒级返回。重采样/AEC 参考/dump 由采集线程 _drain_far 完成
        (对齐 py-xiaozhi AecEngine.feed_far 的线程模型)"""
        self._log_stream_status("输出", status)
        now = time.monotonic()
        if self._last_output_at is not None:
            self._max_output_gap_ms = max(self._max_output_gap_ms, (now - self._last_output_at) * 1000)
        self._last_output_at = now
        self._output_callbacks += 1
        if not self._running:
            outdata[:] = b"\x00" * (frames * 2)
            return
        need = frames * 2
        delay = max(0., getattr(t, 'outputBufferDacTime', 0.) - getattr(t, 'currentTime', 0.))
        mouthless = []
        with self._play_pcm_lock:
            reference_generation = self._reference_generation
            buf = self._play_pcm_buf
            submitted = min(len(buf), need)
            if len(buf) < need:
                outdata[:] = buf + b"\x00" * (need - len(buf))
                self._play_pcm_buf = b""
                has_data = len(buf) > 0
            else:
                outdata[:] = buf[:need]
                self._play_pcm_buf = buf[need:]
                has_data = True
            if submitted:
                self._audible_until = now + delay + submitted / (2 * self.output_rate)
                start, end = self._play_pcm_offset, self._play_pcm_offset + submitted
                while self._mouthless_ranges and self._mouthless_ranges[0][0] < end:
                    a, b = self._mouthless_ranges[0]
                    mouthless.append((max(a, start) - start, min(b, end) - start))
                    if b > end:
                        break
                    self._mouthless_ranges.popleft()
                self._play_pcm_offset = end
        # 欠载检测: 仅统计 TTS 播报期 (_opus_fed) 的缓冲耗尽,
        # 思考期 (opus 未到) / 说话期的正常静音不计。供数恢复时打印静音时长。
        if self._opus_fed:
            if has_data:
                if self._underrun_start:
                    gap_ms = (time.time() - self._underrun_start) * 1000
                    if gap_ms > 50:
                        self._pcm_starvations += 1
                        self._stream_events.append(('starvation', gap_ms, None))
                    self._underrun_start = 0.0
            elif self._out_had_data and not self._underrun_start \
                    and self._prebuf is None:
                # 预缓冲攒集期的静音是设计行为 (攒帧未放行), 不计欠载
                self._underrun_start = time.time()
        self._out_had_data = has_data
        # Product volume is applied before both the speaker and AEC reference.
        outdata[:] = self.output_transform(bytes(outdata))
        # 设备实际消费的数据入队 (含静音也入, 保持 far 时间流连续)
        seq = self._far_seq + 1
        if len(self._far_pending) == self._far_pending.maxlen:
            self._far_queue_drops += 1
        self._far_pending.append((seq, reference_generation, bytes(outdata)))
        self._far_seq = seq
        if self.output_observer is not None and submitted:
            # Product hook only appends to a bounded queue. Analysis and robot
            # IPC run in its own worker, timed to the DAC rather than receipt.
            observed = bytes(outdata)
            if mouthless:
                observed = bytearray(observed)
                for a, b in mouthless:
                    observed[a:b] = bytes(b - a)
                observed = bytes(observed)
            self.output_observer(observed, self.output_rate, now + delay)

    def _drain_far(self, through=None):
        """Worker-only: process reference through one captured near watermark."""
        if through is None:
            through = self._far_seq
        while True:
            if self._far_held is None:
                try:
                    self._far_held = self._far_pending.popleft()
                except IndexError:
                    return
            seq, generation, pcm = self._far_held
            if seq > through:
                return  # Preserve a concurrently appended future block for the next near frame.
            self._far_held = None
            if generation != self._reference_generation:
                continue
            self._dump("ref", pcm)
            if self.aec.available:
                self._push_aec_reference(pcm)

    def _play_loop(self):
        """解码线程: opus → PCM 填充回调缓冲 (不再阻塞 write 声卡)。
        取包超时 20ms: 队列空时及时进入 PLC 垫帧判定"""
        while self._running:
            self._drain_stream_events()
            try:
                generation, opus_data = self.play_queue.get(timeout=0.02)
            except queue.Empty:
                self._maybe_plc_pad()
                continue
            self._decode_active = True
            try:
                pcm = self.opus_decoder.decode(opus_data, self.output_frame_size)
                with self._play_pcm_lock:
                    if generation == self._opus_generation:
                        self._play_pcm_buf += pcm
            except opuslib.OpusError as e:
                print(f"[audio] 解码失败, 丢帧: {e}")
            finally:
                self._decode_active = False

    def _maybe_plc_pad(self):
        """断供兜底 (PLC): 播报期队列空且水位不足时, 用 opus 丢帧隐藏
        (len=0 包 = libopus 官方 PLC 用法) 生成预测帧维持时间轴,
        网络在预算帧数内恢复则无感衔接; 超预算即真停 (回调补静音),
        保证 is_playback_idle 能判闲, tts stop 不被拖住"""
        if not self._opus_fed or self._prebuf is not None:
            return
        if self._plc_stopped or self._plc_count >= AudioConfig.PLC_MAX_FRAMES:
            return
        with self._play_pcm_lock:
            level = len(self._play_pcm_buf)
        if level >= self.output_frame_size * 2 * 2:  # 水位 >= 2 帧 (120ms) 不垫
            return
        try:
            generation = self._opus_generation
            pcm = self.opus_decoder.decode(b"", self.output_frame_size)
            with self._play_pcm_lock:
                if generation != self._opus_generation or self._plc_stopped:
                    return
                self._play_pcm_buf += pcm
            if self._plc_count == 0:
                print(f"[audio] PLC 垫帧 (队列空, 水位="
                      f"{level * 1000 // (2 * self.output_rate)}ms)")
            self._plc_count += 1
        except opuslib.OpusError:
            pass

    def _push_aec_reference(self, pcm: bytes):
        """采集线程内调用: 设备采样率 PCM → 16k 参考 → AEC 时间轴"""
        try:
            if self.aec._destroyed:
                return
            if self.output_rate != self.input_rate:
                s = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
                if s.size < 2:
                    return
                n_out = int(self.input_rate * s.size / self.output_rate)
                x = np.linspace(0.0, 1.0, s.size)
                pcm = np.interp(np.linspace(0.0, 1.0, n_out), x, s)
                pcm = np.clip(pcm, -32768, 32767).astype(np.int16).tobytes()
            self.aec.push_reference(pcm)
        except Exception:
            pass

    # ---------- 调试录制 ----------
    def _dump(self, name: str, pcm: bytes):
        f = self._dump_files.get(name)
        if f is None:
            return
        try:
            f.write(pcm)
            f.flush()
        except Exception:
            pass

    # ---------- 服务器参数协商 ----------
    def update_server_params(self, sample_rate=None, frame_duration=None):
        """服务器 hello 下发输出参数; 帧时长变化时重建解码器"""
        if sample_rate and sample_rate != self.output_rate:
            self.output_rate = sample_rate
            self.output_frame_size = int(self.output_rate * self.frame_ms / 1000)
            self.opus_decoder = opuslib.Decoder(self.output_rate, AudioConfig.CHANNELS)
            self._restart_output_stream()
            print(f"[audio] 输出采样率更新为 {sample_rate}")
        if frame_duration and frame_duration != self.frame_ms:
            self.frame_ms = frame_duration
            self.prebuffer_frames = (AudioConfig.PREBUFFER_FRAMES if self.prebuffer_ms is None else
                                     math.ceil(self.prebuffer_ms / self.frame_ms))
            self.output_frame_size = int(self.output_rate * self.frame_ms / 1000)
            self.input_frame_size = int(self.input_rate * self.frame_ms / 1000)
            print(f"[audio] 帧时长更新为 {frame_duration}ms")

    def _restart_output_stream(self):
        with self._lock:
            try:
                if self.output_stream:
                    self.output_stream.stop()
                    self.output_stream.close()
                self.output_stream = sd.RawOutputStream(
                    device=self.output_device,
                    samplerate=self.output_rate, channels=AudioConfig.CHANNELS,
                    dtype="int16", blocksize=self.output_frame_size,
                    latency=self.output_latency,
                    callback=self._output_callback)
                self.output_stream.start()
            except Exception as e:
                print(f"[audio] 重建输出流失败: {e}")
