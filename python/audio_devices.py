"""Stable USB audio selection and a shared hardware/software volume setting."""
import array
import json
import os
import re
import subprocess
import threading
import uuid
from pathlib import Path
from app_paths import DATA, PRIVATE, private_directory


def capture_capabilities(card):
    import httpx
    socket = os.environ.get('DUCK_GAMEPAD_SOCKET', str(DATA / 'gamepad.sock'))
    try:
        with httpx.Client(transport=httpx.HTTPTransport(uds=socket), base_url='http://gamepad',
                          timeout=1, trust_env=False) as client:
            response = client.get('/status')
            response.raise_for_status()
            entries = response.json().get('audio_inputs', [])
        return next((c for c in entries if c['card'] == card['card'] and c['id'] == card['id']
                     and card['key'].startswith('usb:' + c['usbid'] + ':')), {})
    except (OSError, httpx.HTTPError, KeyError, ValueError, TypeError):
        return {}


def usb_audio_cards(root=Path('/proc/asound'), sysroot=Path('/sys/class/sound')):
    cards = []
    try:
        pcm = (root / 'pcm').read_text()
    except OSError:
        pcm = ''
    names = {path.name for base in (root, sysroot) for path in base.glob('card[0-9]*')}
    for name in sorted(names):
        try:
            number = int(name[4:])
            path, syscard = root / name, sysroot / name
            device = (syscard / 'device').resolve()
            usb = next((parent for parent in (device, *device.parents)
                        if (parent / 'idVendor').exists() and (parent / 'idProduct').exists()), None)
            if (path / 'usbid').exists():
                usb_id = (path / 'usbid').read_text().strip()
            elif usb is not None:
                # App CLI containers mask /proc/asound, but expose sound sysfs
                # and /dev/snd. Keep discovery independent of that proc mount.
                usb_id = ':'.join((usb / field).read_text().strip().lower()
                                  for field in ('idVendor', 'idProduct'))
            else:
                continue  # Exclude the board's internal, non-USB sound card.
            card_id = (path / 'id' if (path / 'id').exists() else syscard / 'id').read_text().strip()
            serial = ((usb / 'serial').read_text().strip() if (usb / 'serial').exists() else usb.name) if usb else ''
            matching = [line for line in pcm.splitlines() if re.match(rf'^0*{number}-', line)]
            pcms = [node.name for node in sysroot.glob(f'pcmC{number}D*')]
            cards.append(dict(card=str(number), id=card_id, key=f'usb:{usb_id}:{serial or card_id}',
                              input=any('capture' in line for line in matching) or any(p.endswith('c') for p in pcms),
                              output=any('playback' in line for line in matching) or any(p.endswith('p') for p in pcms)))
        except (OSError, ValueError):
            continue  # A USB device may disappear halfway through enumeration.
    return cards


def select_audio(cards, direction, preferred=None):
    matches = [c for c in cards if c[direction]]
    if preferred is not None:
        matches = [c for c in matches if preferred in (c['key'], c['id'], c['card'])]
    if not matches:
        raise ValueError(f'USB audio {direction} unavailable; check Host mode and device selection.')
    return matches[0]


def prepare_portaudio(input_card, output_card, capture_rate=None, capture_channels=None):
    # ALSA plug PCMs resample a 48 kHz-only USB microphone to the 16 kHz
    # chat/wake-word format. They do not alter the board's system defaults.
    path = private_directory() / 'alsa.conf'
    body = '</usr/share/alsa/alsa.conf>\n'
    for name, card in [('duck_input', input_card), ('duck_output', output_card)]:
        number = int(card['card'])
        slave = f'pcm "hw:{number},0"'
        if name == 'duck_input':
            if capture_rate is not None:
                if type(capture_rate) is not int or capture_rate not in (16000, 48000):
                    raise ValueError('capture rate must be 16000 or 48000')
                slave += f' rate {capture_rate}'
            if capture_channels is not None:
                if type(capture_channels) is not int or capture_channels not in (1, 2):
                    raise ValueError('capture channels must be 1 or 2')
                slave += f' channels {capture_channels}'
        body += f'pcm.{name} {{ type plug slave {{ {slave} }} hint {{ show on description "{name}" }} }}\n'
    path.write_text(body)
    path.chmod(0o600)
    return path


def prepare_capture(card, auto_gain=None, run=subprocess.run):
    """Optionally set USB capture AGC; leave the device's default alone otherwise."""
    if auto_gain is None:
        return None
    if not isinstance(auto_gain, bool):
        raise ValueError('microphone automatic gain must be boolean or null')
    try:
        run(['amixer', '-c', str(int(card['card'])), 'sset', 'Auto Gain Control',
             'on' if auto_gain else 'off'], check=True, capture_output=True, text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        print('[audio] USB microphone automatic gain control unavailable.', flush=True)
        return None  # USB microphones without this optional control remain usable.
    print('[audio] USB microphone automatic gain:', 'on' if auto_gain else 'off', flush=True)
    return auto_gain


def device_identity():
    path = private_directory() / 'device-id'
    if path.exists():
        identity = path.read_text().strip()
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', identity) or identity == '00:00:00:00:00:00':
            raise ValueError('Invalid saved voice device identity.')
        raw = bytes.fromhex(identity.replace(':', ''))
        if raw == b'\xff' * 6 or raw[0] & 1:
            raise ValueError('Invalid saved voice device identity.')
        return identity.upper()
    # Install script normally provides the factory WLAN MAC. Never use a
    # Docker NIC or the Wi-Fi scan-randomized MAC as a cloud device identity.
    value = bytearray(uuid.uuid4().bytes[:6])
    value[0] = (value[0] | 2) & 0xfe
    identity = ':'.join(f'{b:02X}' for b in value)
    path.write_text(identity + '\n')
    path.chmod(0o600)
    return identity


def scale_pcm(pcm, volume):
    samples = array.array('h')
    samples.frombytes(pcm)
    gain = volume / 100
    if gain > 1:
        return array.array('h', (max(-32768, min(32767, int(x * gain))) for x in samples)).tobytes()
    return array.array('h', (int(x * gain) for x in samples)).tobytes()


class CaptureGain:
    """Session-local boost shared by wake-word and chat capture workers."""
    def __init__(self, gain_db=0):
        from voice_config import VoiceConfig
        VoiceConfig.validate({'input_gain_db': gain_db})
        self.gain = 10 ** (gain_db / 20)

    def pcm(self, pcm):
        if self.gain == 1:
            return pcm
        import numpy as np
        samples = np.frombuffer(pcm, dtype='<i2').astype(np.float32)
        # Widen before scaling and saturate before narrowing: no int16 wrap.
        return np.clip(samples * self.gain, -32768, 32767).astype('<i2').tobytes()

    def floats(self, samples):
        if self.gain == 1:
            return samples
        import numpy as np
        return np.clip(samples * self.gain, -1., 32767 / 32768).astype(np.float32)


class Volume:
    def __init__(self, config, cards=usb_audio_cards, run=subprocess.run):
        self.config, self.cards, self.run = config, cards, run
        self.lock = threading.RLock()
        self.apply_lock = threading.Lock()
        self.hardware = False
        self.card = None
        settings = config.get()
        self.value = settings['volume']
        self.boost = 10 ** (settings['output_gain_db'] / 20)

    def apply(self, value=None):
        if value is None:
            value = self.config.get()['volume']
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ValueError('Volume must be an integer from 0 to 100.')
        with self.apply_lock:
            settings = self.config.get()
            boost = 10 ** (settings['output_gain_db'] / 20)
            try:
                output = select_audio(self.cards(), 'output', settings['output_device'])
            except ValueError:
                output = None  # Save the choice for a speaker plugged in later.
            card, hardware = output['card'] if output else None, False
            for control in ('Speaker', 'PCM', 'Master', 'Headphone') if output else ():
                try:
                    self.run(['amixer', '-c', card, 'sset', control, f'{value}%', 'unmute'],
                             check=True, capture_output=True, text=True, timeout=3)
                    hardware = True
                    break
                except (OSError, subprocess.SubprocessError):
                    continue
            self.config.update({'volume': value})
            # PortAudio reads only this short snapshot lock; never make its
            # callback wait for amixer, filesystem I/O or device discovery.
            with self.lock:
                self.hardware, self.card, self.value = hardware, card, value
                self.boost = boost
            return value

    @property
    def software_gain(self):
        with self.lock:
            if self.value == 0:
                return 0  # A hardware minimum may still be audible; enforce mute.
            return (100 if self.hardware else self.value) * self.boost

    def pcm(self, pcm):
        volume = self.software_gain
        return pcm if volume == 100 else scale_pcm(pcm, volume)
