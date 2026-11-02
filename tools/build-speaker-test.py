"""Rebuild the bundled speaker check with the existing local duck synthesizer."""
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from vendor.microduck_voice import pcm16, render, seed_from_id


def build():
    rate = 48000
    seed = seed_from_id('XGO-Duck speaker test')
    calls = [pcm16(render(kind, seed, variant), rate)
             for kind, variant in [('greet', 0), ('inquire', 1), ('greet', 2)]]
    silence = lambda seconds: b'\0\0' * round(rate * seconds)
    pcm = silence(.05) + silence(.3).join(calls) + silence(.15)
    output = ROOT / 'python/assets/speaker-test.wav'
    with wave.open(str(output), 'wb') as target:
        target.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        target.writeframes(pcm)
    print(f'{output}: {len(pcm) / (rate * 2):.2f} s, mono 48 kHz PCM16')


if __name__ == '__main__':
    build()
