"""Build browser examples with the real connection-sound recipes and gain."""
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from connection_sounds import KINDS, SOFTWARE_GAIN
from duck_sound import amplify_sound, sound_pcm

SOURCES = {'network': 'startup-network.wav', 'voice_service': 'startup-voice.wav'}
IDENTITY = 'XGO-Duck'
RATE = 16000


def build():
    for event, filename in SOURCES.items():
        pcm = amplify_sound(sound_pcm(KINDS[event], IDENTITY, 0, RATE, 0), SOFTWARE_GAIN, 0)
        output = ROOT / 'assets' / filename
        with wave.open(str(output), 'wb') as target:
            target.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            target.writeframes(pcm)
        print(f'{event}: {output.name}, {len(pcm) / (2 * RATE):.2f} s')


if __name__ == '__main__':
    build()
