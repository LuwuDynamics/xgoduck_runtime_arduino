"""Seeded pet vocalizations adapted from Pollen Robotics MicroDuck (Apache-2.0).

Translated from sounds/src/{personality,synth,voices}.rs at
d7560ef059c73fab4177eb247c1774013f6feb9d. See NOTICE.md and LICENSE.
Changes: NumPy RNG/DSP, short one-shot wheee, band-limited PCM conversion.
The recipes are local synthesis, with no network, models or audio devices.
"""
from dataclasses import dataclass, replace
import hashlib
import zlib

import numpy as np

SR = 48000
TAGS = ('alarm', 'greet', 'inquire', 'peck', 'chirp', 'coo', 'wheee')
VARIANTS = dict.fromkeys(TAGS, 10) | {'greet': 12, 'chirp': 12, 'wheee': 6}
MAX_SECONDS = 2.0


def seed_from_id(identity):
    return int.from_bytes(hashlib.sha256(identity.encode()).digest()[:4], 'big')


@dataclass(frozen=True)
class Personality:
    seed: int
    pitch: float
    spread: float
    glide: float
    brightness: float
    tilt: float
    nasal: float
    skew: float
    formant: int
    formant_gain: float
    vibrato_rate: float
    vibrato_depth: float
    jitter: float
    breath: float
    quackiness: float
    am_rate: float
    am_depth: float
    warble_rate: float
    warble_depth: float
    sharpness: float
    speed: float

    @classmethod
    def from_seed(cls, seed):
        r = np.random.Generator(np.random.PCG64(seed))
        register = r.choice([-1., 0., 0., 1.]) + r.uniform(-.4, .4)
        pitch = float(np.clip(r.uniform(160, 380) * 2 ** (register * .45), 110, 620))
        return cls(seed, pitch, r.uniform(.4, 1.2), r.uniform(-1, 1),
                   r.uniform(.05, .55), r.uniform(1.4, 2.8), r.uniform(.1, 1),
                   r.uniform(-1, 1), int(r.integers(1, 6)), r.uniform(0, 1.4),
                   r.uniform(3.5, 9.5), r.uniform(0, .7), r.uniform(.03, .35),
                   r.uniform(0, .3), r.uniform(.2, 1), r.uniform(18, 55),
                   r.uniform(.15, .7), r.uniform(7, 18), r.uniform(0, 1.4),
                   r.uniform(0, 1), r.uniform(.82, 1.22))

    def rng(self, tag, variant):
        seed = (self.seed * 1000003) ^ zlib.crc32(tag.encode()) ^ (variant * 2654435761)
        return np.random.Generator(np.random.PCG64(seed & 0xffffffff))

    def harmonics(self):
        weights = []
        for n in range(1, 8):
            base = n ** -self.tilt
            high = self.brightness * (n / 7) ** 1.5
            nasal = self.nasal * (.6 if n in (2, 3) else 0)
            skew = (self.skew * (.4 if n % 2 == 0 else -.2) if self.skew >= 0
                    else -self.skew * (-.3 if n % 2 == 0 else .4))
            formant = self.formant_gain * base * 1.5 if n == self.formant else 0
            weights.append(max(0., base + high + nasal + skew + formant))
        weights[0] = max(.7, weights[0])
        return weights


def _time(duration):
    return np.arange(max(1, round(duration * SR)), dtype=np.float64) / SR


def _curve(t, points):
    x, y = zip(*points)
    return np.interp(t, x, y)


def _attack(p, duration, snappy):
    return max(.001, duration * (.04 - .037 * p.sharpness * snappy))


def _decay(t, attack, decay):
    return np.minimum(t / max(.0001, attack), 1) * np.exp(-np.maximum(0, t - attack) / decay)


def _bell(t, attack, release):
    return np.minimum(np.minimum(t / attack, 1), np.clip((t[-1] - t) / release, 0, 1))


def _vibrato(t, rate, depth, phase):
    return 2 ** (depth * np.sin(2 * np.pi * rate * t + phase) / 12)


def _voice(p, t, freq, r, am=1., breath=1.):
    vib = _vibrato(t, p.vibrato_rate, p.vibrato_depth, r.uniform(0, 2 * np.pi))
    window = round(64 * SR / 22050)
    wobble = np.convolve(r.standard_normal(len(t)), np.ones(window) / window, 'same')
    phase = 2 * np.pi * np.cumsum(freq * vib * 2 ** (p.jitter * wobble / 12)) / SR
    body = sum(w * np.sin(n * phase) for n, w in enumerate(p.harmonics(), 1))
    depth = p.am_depth * am * p.quackiness
    body *= 1 - depth * (.5 + .5 * np.sin(2 * np.pi * p.am_rate * t))
    if p.breath * breath:
        pink = r.standard_normal(len(t))
        pole, leak = .985 ** (22050 / SR), 0.
        for i, value in enumerate(pink):
            leak = pole * leak + value
            pink[i] = leak
        pink /= np.max(np.abs(pink)) + 1e-9
        body += p.breath * breath * pink
    return body


def _greet_syllable(p, r, duration_scale=1., pitch_scale=1.):
    duration = (.32 + .25 * r.random()) * duration_scale / p.speed
    t = _time(duration)
    f = p.pitch * (.9 + .15 * r.random()) * pitch_scale
    bend = .10 + .15 * p.spread
    freq = _curve(t, [(0, f * (1 - p.glide * bend * .5)),
                      (.18 * duration, f * (1 + p.glide * bend)),
                      (duration, f * (1 - p.glide * bend * .3) * (.92 + .08 * r.random()))])
    return _voice(p, t, freq, r) * _decay(t, _attack(p, duration, .5), duration * .7)


def _chirp_syllable(p, r, f, duration, contour):
    t = _time(duration)
    freq = f * _curve(t, contour) * _vibrato(t, p.warble_rate, p.warble_depth, r.uniform(0, 2 * np.pi))
    soft = replace(p, quackiness=p.quackiness * .2, brightness=p.brightness * .5,
                   formant_gain=p.formant_gain * .5)
    return _voice(soft, t, freq, r, breath=.4) * _decay(t, _attack(p, duration, .7), duration * .55)


def render(tag, seed, variant=0):
    """Return bounded float32 mono at 48 kHz; the same inputs reproduce the voice."""
    if not isinstance(tag, str) or tag not in TAGS:
        raise ValueError('kind must be ' + '/'.join(TAGS))
    if isinstance(variant, bool) or not isinstance(variant, int) or not 0 <= variant < VARIANTS[tag]:
        raise ValueError('invalid sound variant')
    p = Personality.from_seed(seed)
    r = p.rng(tag, variant)
    peak_db = -3.
    if tag == 'greet':
        sig = _greet_syllable(p, r)
        if r.random() < .4:
            gap = np.zeros(int((.05 + .06 * r.random()) / p.speed * SR))
            sig = np.concatenate((sig, gap, _greet_syllable(p, r, .8, .95 + .06 * r.random())))
    elif tag == 'chirp':
        f = p.pitch * (.95 + .75 * r.random())
        shape = variant % 4
        duration = ((.22 + .14 * r.random()) if shape == 2 else
                    (.08 + .05 * r.random()) if shape == 3 else (.10 + .10 * r.random())) / p.speed
        if shape == 0:
            contour = [(0, .88), (.5 * duration, 1.12 + .10 * r.random()), (duration, 1.05)]
        elif shape == 1:
            contour = [(0, 1.12 + .10 * r.random()), (.3 * duration, 1), (duration, .78)]
        elif shape == 2:
            p = replace(p, warble_depth=max(.7, p.warble_depth) * 1.6)
            contour = [(0, 1), (.5 * duration, 1.06), (duration, .94)]
        else:
            contour = [(0, .95), (.4 * duration, 1.10), (duration, .88)]
        sig = _chirp_syllable(p, r, f, duration, contour)
        if shape == 3:
            gap = np.zeros(int((.03 + .03 * r.random()) / p.speed * SR))
            d = duration * .9
            sig = np.concatenate((sig, gap, _chirp_syllable(p, r, f * (.92 + .10 * r.random()), d,
                                  [(0, .95), (.4 * d, 1.10), (d, .88)])))
        peak_db = -6.
    elif tag == 'wheee':
        # A bounded joy call; the upstream held-trigger loop is deliberately shorter here.
        start, loop, end = ((.35 + .1 * r.random()) / p.speed,
                           (.45 + .1 * r.random()) / p.speed, (.35 + .1 * r.random()) / p.speed)
        duration = start + loop + end
        t = _time(duration)
        f, top = p.pitch * (.95 + .1 * r.random()), 1.6 + .5 * p.spread + .25 * r.random()
        freq = _curve(t, [(0, f * .85), (.15 * start, f), (start, f * top),
                          (start + loop, f * top), (start + loop + .25 * end, f * top * 1.04),
                          (duration, f * .60)])
        swell = _curve(t, [(0, .15), (start, 1), (duration, 1)])
        freq *= 2 ** (.5 * swell * np.sin(2 * np.pi * (4.5 + 3 * r.random()) * t) / 12)
        env = _curve(t, [(0, 0), (min(.06, .5 * start), 1), (start + loop + .4 * end, 1), (duration, 0)])
        joy = replace(p, vibrato_depth=p.vibrato_depth * .5, quackiness=p.quackiness * .5)
        sig = _voice(joy, t, freq, r, am=.5, breath=.5) * env
        peak_db = -4.
    else:
        duration = dict(alarm=(.20, .12), inquire=(.42, .25), peck=(.16, .12), coo=(.85, .55))[tag]
        duration = (duration[0] + duration[1] * r.random()) / p.speed
        t = _time(duration)
        if tag == 'alarm':
            f = p.pitch * (1.25 + .35 * p.spread) * (.94 + .12 * r.random())
            freq = _curve(t, [(0, f), (.05 * duration, f * (1.15 + .25 * p.spread + .10 * r.random())),
                              (duration, f * (.75 + .20 * (1 - p.spread)))])
            env = _decay(t, _attack(p, duration, 1), duration * (.40 + .20 * r.random()))
            sig = (_voice(p, t, freq, r, am=.5) + (.04 + .10 * p.brightness) * r.standard_normal(len(t))) * env
        elif tag == 'inquire':
            f = p.pitch * (.88 + .10 * r.random())
            rise = 1.15 + .50 * p.spread + .20 * max(0, p.glide) + .10 * r.random()
            freq = _curve(t, [(0, f * .92), (.30 * duration, f * .95), (duration, f * rise)])
            sig = _voice(p, t, freq, r, am=.6) * _bell(t, duration * (.06 + .10 * (1 - p.sharpness)), duration * .32)
        elif tag == 'peck':
            f = p.pitch * (.45 + .20 * r.random())
            freq = _curve(t, [(0, f * 1.5), (.04 * duration, f), (duration, f * .80)])
            sig = _voice(p, t, freq, r, am=.3, breath=.5) * _decay(t, _attack(p, duration, 1), duration * .35)
            length = int((.003 + .006 * p.sharpness) * SR)
            sig[:length] += (.4 + .4 * p.sharpness) * r.uniform(-1, 1, length) * (1 - np.arange(length) / length) ** 2
        else:
            f = p.pitch * (.42 + .15 * (1 - p.sharpness)) * (.94 + .12 * r.random())
            freq = _curve(t, [(0, f * .94), (.5 * duration, f * (1 + .05 * r.random() + .04 * p.glide)),
                              (duration, f * .90)])
            soft = replace(p, breath=max(.12, p.breath) + .10, quackiness=p.quackiness * .25,
                           am_rate=p.am_rate * .30, vibrato_rate=p.vibrato_rate * .45,
                           vibrato_depth=p.vibrato_depth * .7)
            sig = _voice(soft, t, freq, r) * _bell(t, duration * (.18 + .10 * (1 - p.sharpness)), duration * .30)
            peak_db = -5.
    if len(sig) > MAX_SECONDS * SR or not np.isfinite(sig).all():
        raise ValueError('invalid synthesized audio')
    # Fade both edges to suppress hard cuts (including peck's transient).
    edge = min(round(.003 * SR), len(sig) // 2)
    sig[:edge] *= np.linspace(0, 1, edge)
    sig[-edge:] *= np.linspace(1, 0, edge)
    sig *= 10 ** (peak_db / 20) / (np.max(np.abs(sig)) + 1e-9)
    return sig.astype(np.float32)


def pcm16(samples, rate, headroom_db=0):
    """Low-pass before resampling, then reserve headroom for the product output gain."""
    if rate not in (8000, 16000, 24000, 48000):
        raise ValueError('unsupported sound output rate')
    if not 0 <= headroom_db <= 18:
        raise ValueError('invalid sound headroom')
    if rate != SR:
        n = np.arange(-96, 97)
        cutoff = .45 * rate / SR
        kernel = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(len(n))
        samples = np.convolve(samples, kernel / kernel.sum(), 'same')[::SR // rate]
    # Filtering can overshoot; normalization prevents downstream gain from clipping.
    limit = 10 ** (-3 / 20)
    samples = samples * min(1., limit / (np.max(np.abs(samples)) + 1e-9))
    return np.clip(samples * 10 ** (-headroom_db / 20) * 32767, -32768, 32767).astype('<i2').tobytes()
