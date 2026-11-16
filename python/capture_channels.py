"""Select a mono near-end signal before AEC; never sum uncalibrated microphones."""
import math
import threading
import numpy as np


def hardware_channels(devices, card):
    # Plug PCMs can advertise 128 channels. Use only the matching hardware PCM.
    marker = f'(hw:{int(card["card"])},0)'
    matches = [d for d in devices if marker in d['name'] and d['max_input_channels'] > 0]
    return min(2, int(matches[0]['max_input_channels'])) if matches else 1


class CaptureChannels:
    def __init__(self, channels=1, mode='auto', rate=16000):
        if type(channels) is not int or channels not in (1, 2):
            raise ValueError('capture channels must be 1 or 2')
        if mode not in ('auto', 'left', 'right', 'average'):
            raise ValueError('invalid microphone channel mode')
        self.channels, self.mode, self.rate = channels, mode, rate
        self.selected = 1 if mode == 'right' and channels == 2 else 0
        self.energy = np.zeros(channels)
        self.pending = 0.
        self.switches = self.blocks = 0
        self.lock = threading.Lock()

    def floats(self, samples):
        x = np.asarray(samples, dtype=np.float32).reshape(-1, self.channels)
        if not len(x):
            return np.empty(0, np.float32)
        # This runs in a worker, not the PortAudio callback. Remove DC from the
        # energy measurement, not from the outgoing waveform.
        power = np.mean((x - x.mean(axis=0)) ** 2, axis=0)
        dt = len(x) / self.rate
        with self.lock:
            self.blocks += 1
            alpha = 1 - math.exp(-dt / .25)
            self.energy += alpha * (power - self.energy)
            if self.mode == 'auto' and self.channels == 2:
                other = 1 - self.selected
                better = self.energy[other] > max(self.energy[self.selected] * 4, 1e-7)
                self.pending = self.pending + dt if better else 0.
                if self.pending >= .5:
                    self.selected = other
                    self.switches += 1
                    self.pending = 0.
            selected = self.selected
        # Average is an explicit opt-in after checking polarity. Auto retains
        # a stable channel (6 dB advantage for 0.5 s before switching).
        return x.mean(axis=1) if self.mode == 'average' else x[:, selected].copy()

    def pcm(self, pcm):
        x = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        return np.clip(self.floats(x) * 32768, -32768, 32767).astype('<i2').tobytes()

    def status(self):
        with self.lock:
            return dict(channels=self.channels, mode=self.mode, selected_channel=self.selected + 1,
                        rms=[round(float(v) ** .5, 6) for v in self.energy],
                        blocks=self.blocks, switches=self.switches)
