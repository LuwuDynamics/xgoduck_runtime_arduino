"""Small TTS jaw envelope, DAC-timed IPC and a separate mouth-only lease."""
import array
from collections import deque
import math
import threading
import time
from motion_feedback import MOUTH_ID, feedback_ready, feedback_problem

MAX_DEGREES = 12.


class MouthEnvelope:
    def __init__(self):
        self.angle = 0.
        self.elapsed = 0.
        self.energy = deque()
        self.windows = [0., 0., 0.]
        self.rhythm = self.target = 0.

    def _window_rms(self, seconds):
        start = self.elapsed - seconds
        power = duration = 0.
        for end, dt, value in self.energy:
            overlap = max(0., end - max(start, end - dt))
            power += value * overlap
            duration += overlap
        return math.sqrt(power / duration) if duration else 0.

    def analyse(self, pcm, rate):
        samples = array.array('h', pcm)
        window = max(1, int(rate * .02))
        result = []
        for offset in range(0, len(samples), window):
            block = samples[offset:offset + window]
            mean = sum(block) / len(block)
            rms = math.sqrt(sum((x - mean) ** 2 for x in block) / len(block))
            dt = len(block) / rate
            self.elapsed += dt
            self.energy.append((self.elapsed, dt, rms * rms))
            while self.energy and self.energy[0][0] <= self.elapsed - .32:
                self.energy.popleft()
            medium, long = self._window_rms(.08), self._window_rms(.32)
            self.windows = [rms, medium, long]
            mixed = math.sqrt(.6 * rms * rms + .3 * medium * medium + .1 * long * long)
            level = max(0., min(1., (20 * math.log10(max(1., mixed) / 32768) + 48) / 36))
            # Relative energy changes retain syllable contrast even when loud
            # TTS saturates the absolute level. Two gentle speech-rate rhythms
            # prevent steady vowels from becoming a fixed open-mouth pose.
            contrast = max(-1., min(1., (.75 * (rms - medium) + .25 * (medium - long)) / max(300., long)))
            rhythm = .65 * math.sin(2 * math.pi * 3.2 * self.elapsed)
            rhythm += .35 * math.sin(2 * math.pi * 5.1 * self.elapsed + .8)
            self.rhythm = .62 + .38 * rhythm
            crossings = sum((a >= mean) != (b >= mean) for a, b in zip(block, block[1:]))
            brightness = min(1., crossings / max(1, len(block)) / .35)
            target = (MAX_DEGREES * level ** .65 * (.9 + .1 * brightness) * self.rhythm
                      + 2.8 * contrast * level + 2. * level) if rms >= 160 else 0.
            self.target = target = max(0., min(MAX_DEGREES, target))
            alpha = 1 - math.exp(-dt / (.035 if target > self.angle else .06))
            change = max(-90 * dt, min(90 * dt, (target - self.angle) * alpha))
            self.angle = max(0., min(MAX_DEGREES, self.angle + change))
            if target == 0 and self.angle < .5:
                self.angle = 0.
            result.append((offset / rate, round(self.angle, 3)))
        return result

    def status(self):
        return dict(windows_ms=[20, 80, 320], rms=[round(v, 1) for v in self.windows],
                    rhythm=round(self.rhythm, 3), target_degrees=round(self.target, 3),
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
        # Board HTTP round trips can exceed one 60 ms audio frame. Coalesce
        # adjacent frames and keep recent playback, never replay a stale backlog.
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
            points.extend([audible_at + offset, angle] for offset, angle in self.envelope.analyse(pcm, rate))
        # Only the scheduled portion still ahead of actual playback is useful.
        points = [p for p in points if p[0] >= self.clock()][-8:]
        if points and context == self.context:
            self._publish(context, points)

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
                    last_lead_ms=list(self.last_lead_ms), motion=self.last_result,
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
            c.mouth = self.baseline
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
                   for v in point) or not 0 <= degrees <= MAX_DEGREES or not now - .2 <= at <= now + 1.2:
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
