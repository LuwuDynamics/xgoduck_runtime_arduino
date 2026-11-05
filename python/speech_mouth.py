"""Small TTS jaw envelope, DAC-timed IPC and a separate mouth-only lease."""
import array
from collections import deque
import math
import threading
import time
from motion_feedback import MOUTH_ID, feedback_ready, feedback_problem
from rl_core import MOUTH_MIN_DEG

MAX_DEGREES = 30.
MIN_DEGREES = MOUTH_MIN_DEG
MAX_SPEED_DPS = 150.
ANALYSIS_HZ = 100
OUTPUT_HZ = 50
WINDOWS_MS = (25, 120, 300)
ONSET_GAIN = .7
MEDIUM_GAIN = .35
MOUTH_ADVANCE_S = .100


class MouthEnvelope:
    def __init__(self):
        self.angle = MIN_DEGREES
        self.elapsed = 0.
        # Five-ms blocks retain exact samples at window edges, including the
        # 25-ms window between 10-ms analysis ticks. No audio-callback work.
        self.energy = deque(maxlen=61)
        self.windows = [0., 0., 0.]
        self.base = self.onset = self.medium_change = self.voice = 0.
        self.target = MIN_DEGREES
        self.rate = None
        self.pending = array.array('h')
        self.received = self.processed = self.bins = self.last_analysis = 0
        self.analysis_updates = self.output_points = 0

    def _window_rms(self, seconds):
        remaining = int(self.rate * seconds)
        total = power = count = 0
        for block, block_total, block_power in reversed(self.energy):
            take = min(remaining, len(block))
            if take < len(block):
                tail = block[-take:]
                block_total = sum(tail)
                block_power = sum(v * v for v in tail)
            total += block_total
            power += block_power
            count += take
            remaining -= take
            if not remaining:
                break
        # Keep the existing DC rejection; startup uses the available history.
        variance = max(0., power / count - (total / count) ** 2) if count else 0.
        return math.sqrt(variance) / 32768.

    def analyse(self, pcm, rate):
        if not isinstance(rate, int) or isinstance(rate, bool) or rate < 200:
            raise ValueError('invalid mouth PCM sample rate')
        if self.rate != rate:
            self.rate = rate
            self.pending = array.array('h')
            self.energy.clear()
            self.received = self.processed = self.bins = self.last_analysis = 0
        samples = array.array('h', pcm)
        start = self.received
        self.received += len(samples)
        self.pending.extend(samples)
        consumed = 0
        result = []
        while True:
            size = (self.bins + 1) * rate // 200 - self.processed
            if len(self.pending) - consumed < size:
                break
            block = self.pending[consumed:consumed + size]
            consumed += size
            self.processed += size
            self.bins += 1
            self.energy.append((block, sum(block), sum(v * v for v in block)))
            if self.bins % 2:
                continue
            dt = (self.processed - self.last_analysis) / rate
            self.last_analysis = self.processed
            self.elapsed += dt
            short, medium, long = (self._window_rms(ms / 1000) for ms in WINDOWS_MS)
            self.windows = [short, medium, long]
            self.base = min(1., medium / max(long, .015))
            self.onset = max(0., min(1., (short - medium) / (.7 * max(medium, .015))))
            self.medium_change = max(-1., min(1., (medium - long) / max(long, .015)))
            self.voice = max(0., min(1., (short - .006) / .012))
            opening = self.voice * max(0., min(1., .08 + .27 * self.base
                + ONSET_GAIN * self.onset + MEDIUM_GAIN * self.medium_change))
            self.target = target = MIN_DEGREES + (MAX_DEGREES - MIN_DEGREES) * opening
            alpha = 1 - math.exp(-dt / (.025 if target > self.angle else .070))
            limit = MAX_SPEED_DPS * dt
            change = max(-limit, min(limit, (target - self.angle) * alpha))
            self.angle = max(MIN_DEGREES, min(MAX_DEGREES, self.angle + change))
            if target == MIN_DEGREES and self.angle - MIN_DEGREES < .5:
                self.angle = MIN_DEGREES
            self.analysis_updates += 1
            if not self.bins % 4:
                # End-of-window timestamps keep arbitrary PCM splits aligned
                # with the DAC clock. Two analysis ticks form one output point.
                result.append(((self.processed - start) / rate, round(self.angle, 3)))
                self.output_points += 1
        if consumed:
            del self.pending[:consumed]
        return result

    def status(self):
        return dict(algorithm='three_rms', windows_ms=list(WINDOWS_MS),
                    rms=[round(v * 32768, 1) for v in self.windows],
                    rms_normalized=[round(v, 6) for v in self.windows],
                    components=dict(base=round(self.base, 4), onset=round(self.onset, 4),
                                    medium_change=round(self.medium_change, 4), voice=round(self.voice, 4)),
                    analysis_hz=ANALYSIS_HZ, output_hz=OUTPUT_HZ, onset_gain=ONSET_GAIN,
                    medium_gain=MEDIUM_GAIN,
                    analysis_updates=self.analysis_updates, output_points=self.output_points,
                    min_degrees=MIN_DEGREES, max_degrees=MAX_DEGREES, speed_limit_dps=MAX_SPEED_DPS,
                    attack_ms=25, release_ms=70, target_degrees=round(self.target, 3),
                    angle_degrees=round(self.angle, 3))


class SpeechMouthPublisher:
    """The audio callback only appends bounded PCM; this worker does IPC/math."""
    def __init__(self, send, clock=time.monotonic):
        self.send, self.clock = send, clock
        self.frames = deque(maxlen=24)
        self.controls = deque(maxlen=16)
        self.context = None
        self.utterance = self.sequence = 0
        self.envelope = MouthEnvelope()
        self.analysis_context = None
        self.analysis_until = None
        self.published = self.errors = 0
        self.dropped = 0
        self.last_result = {}
        self.last_error = ''
        self.last_lead_ms = []
        self.closing = threading.Event()
        self.thread = None

    def begin(self, session):
        self.stop()
        self.utterance += 1
        self.context = (session, self.utterance)

    def stop(self):
        old, self.context = self.context, None
        self.frames.clear()
        if old is not None:
            self.controls.append(old)

    def observe(self, pcm, rate, audible_at):
        context = self.context
        if context is not None:
            self.frames.append((context, pcm, rate, audible_at))

    def _publish(self, context, points):
        self.sequence += 1
        if points:
            now = self.clock()
            self.last_lead_ms = [round((p[0] - now) * 1000, 1) for p in (points[0], points[-1])]
        body = dict(session=context[0], utterance=context[1], sequence=self.sequence, points=points)
        try:
            result = self.send(body)
            self.last_result = result if isinstance(result, dict) else {}
            self.published += 1
        except (OSError, ValueError) as exc:
            self.errors += 1  # Mouth IPC failure must never interrupt audio.
            self.last_error = str(exc)[:160]

    def process_once(self):
        if self.controls:
            self._publish(self.controls.popleft(), [])
            return
        # Coalesce recent audio callbacks; never replay a stale backlog.
        while len(self.frames) > 2:
            self.frames.popleft()
            self.dropped += 1
        points = []
        context = self.context
        for _ in range(min(2, len(self.frames))):
            frame_context, pcm, rate, audible_at = self.frames.popleft()
            if frame_context != context or context != self.context:
                continue
            until = audible_at + len(pcm) / (2 * rate)
            if until <= self.clock():
                self.dropped += 1
                continue
            if (context != self.analysis_context or self.analysis_until is None
                    or audible_at - self.analysis_until > .12):
                self.envelope = MouthEnvelope()
                self.analysis_context = context
            self.analysis_until = until
            points.extend([audible_at + offset - MOUTH_ADVANCE_S, angle]
                          for offset, angle in self.envelope.analyse(pcm, rate))
        # Only the scheduled portion still ahead of actual playback is useful.
        for offset in range(0, len(points), 8):
            if context != self.context:
                break
            now = self.clock()
            fresh = [p for p in points[offset:offset + 8] if p[0] >= now]
            if fresh:
                self._publish(context, fresh)

    def start(self):
        def run():
            while not self.closing.is_set() or self.controls:
                try:
                    self.process_once()
                except Exception:
                    self.errors += 1
                if not self.controls and not self.frames:
                    self.closing.wait(.01)
        self.thread = threading.Thread(target=run, name='tts-mouth', daemon=True)
        self.thread.start()

    def status(self):
        return dict(speaking=self.context is not None, queued_frames=len(self.frames),
                    published_packets=self.published, ipc_errors=self.errors,
                    dropped_frames=self.dropped, last_error=self.last_error,
                    last_lead_ms=list(self.last_lead_ms), advance_ms=MOUTH_ADVANCE_S * 1000,
                    motion=self.last_result,
                    envelope=self.envelope.status())

    def close(self):
        self.stop()
        self.closing.set()
        if self.thread:
            self.thread.join(timeout=.5)


class SpeechMouthMotion:
    """Uses the existing enabled mouth target without arming or taking the body."""
    def __init__(self, controller, link_ready, clock=time.monotonic):
        self.controller, self.link_ready, self.clock = controller, link_ready, clock
        self.pending = deque(maxlen=64)
        self.session = None
        self.utterance = self.sequence = self.blocked = 0
        self.baseline = self.applied = None
        self.deadline = 0.
        self.reason = 'idle'

    def _release(self, restore=True):
        c = self.controller
        if (restore and self.baseline is not None and not c.pick_active
                and c.mouth == self.applied):
            c.mouth = MIN_DEGREES if self.baseline == 0. else self.baseline
        self.baseline = self.applied = None
        self.pending.clear()
        self.deadline = 0.

    def override(self, reason='manual mouth control', *, restore=True):
        self._release(restore)
        self.blocked = self.utterance
        self.reason = reason

    def reset(self):
        self._release()
        self.session = None
        self.utterance = self.sequence = self.blocked = 0
        self.reason = 'voice session ended'

    def submit(self, session, utterance, sequence, points):
        now = self.clock()
        if any(isinstance(v, bool) or not isinstance(v, int) or not 0 < v < 2**53
               for v in (utterance, sequence)):
            raise ValueError('invalid speech mouth sequence')
        if not isinstance(points, list) or len(points) > 8:
            raise ValueError('invalid speech mouth points')
        checked = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError('invalid speech mouth point')
            at, degrees = point
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                   for v in point) or not MIN_DEGREES <= degrees <= MAX_DEGREES or not now - .2 <= at <= now + 1.2:
                raise ValueError('speech mouth point out of bounds')
            if checked and at < checked[-1][0]:
                raise ValueError('speech mouth points out of order')
            checked.append((at, degrees))
        if session != self.session:
            self.reset()
            self.session = session
        if sequence <= self.sequence or utterance < self.utterance:
            raise ValueError('speech mouth request retired')
        self.sequence = sequence
        if utterance > self.utterance:
            self._release()
            self.utterance = utterance
        if not points:
            self.override('playback stopped')
        elif utterance > self.blocked:
            for point in checked:
                if point[0] >= now and (not self.pending or point[0] >= self.pending[-1][0]):
                    self.pending.append(point)
            if self.pending:
                self.deadline = self.pending[-1][0] + .18
                self.reason = 'scheduled'
        return dict(ok=not points or utterance > self.blocked, **self.status())

    def tick(self, status):
        if not self.deadline:
            return
        c, now = self.controller, self.clock()
        if (c.mode not in ('policy', 'hold') or status.get('enabled') is not True
                or status.get('fault_hold')
                or not feedback_ready(status, (MOUTH_ID,)) or not self.link_ready(status)
                or c.input_source == 'gamepad' or c.pick_active or bool(c.roulade_until)
                or any((status.get(name) or {}).get('active') for name in ('sitstand', 'left_kick', 'right_kick'))
                or (status.get('recovery') or {}).get('phase') != 'walk'):
            self.override(feedback_problem(status, (MOUTH_ID,)) or 'body unavailable or special action owns mouth')
            return
        if now >= self.deadline:
            self._release()
            self.reason = 'audio lease expired'
            return
        target = None
        while self.pending and self.pending[0][0] <= now:
            _, target = self.pending.popleft()
        if target is not None:
            if self.baseline is None or c.mouth != self.applied:
                self.baseline = c.mouth
            c.mouth = self.applied = target
            self.reason = 'speaking'

    def status(self):
        return dict(active=self.applied is not None, degrees=self.applied or 0.,
                    queued_points=len(self.pending), reason=self.reason)
