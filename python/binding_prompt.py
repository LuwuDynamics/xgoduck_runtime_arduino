"""Assemble prerecorded speech for an IPv4 binding prompt without a cloud call."""
from ipaddress import IPv4Address
from pathlib import Path
import wave
from zipfile import ZipFile

ASSETS = Path(__file__).with_name('assets')
BINDING_SAMPLE_RATE = 16000


def binding_pcm(language, address):
    language = 'cn' if language == 'cn' else 'en'
    if not address:
        # Also supports an older host service that predates the IPv4 field.
        with wave.open(str(ASSETS / f'bind-device-{language}.wav'), 'rb') as wav:
            return wav.readframes(wav.getnframes())

    # Speak each digit so even a long address sounds unambiguous.
    ip = str(IPv4Address(address))
    with ZipFile(ASSETS / 'bind-ip-parts.zip') as clips:
        def read(part):
            return clips.read(f'{language}/{part}.pcm')

        digit_gap = bytes(2 * int(BINDING_SAMPLE_RATE * .045))
        phrase_gap = bytes(2 * int(BINDING_SAMPLE_RATE * .18))
        spoken_address = digit_gap.join(read('dot' if char == '.' else char) for char in ip)
        return phrase_gap.join((read('prefix'), spoken_address, read('suffix')))
