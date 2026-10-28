"""Read USB capture descriptors on the host (App containers mask /proc/asound)."""
from pathlib import Path
import re


def capture_devices(root=Path('/proc/asound')):
    result = []
    for card in sorted(root.glob('card[0-9]*')):
        try:
            usb_id = (card / 'usbid').read_text().strip().lower()
            card_id = (card / 'id').read_text().strip()
            channels, rates = set(), set()
            for stream in card.glob('stream*'):
                text = stream.read_text()
                if 'Capture:' not in text:
                    continue
                text = text.split('Capture:', 1)[1].split('Playback:', 1)[0]
                channels.update(map(int, re.findall(r'^\s*Channels:\s*(\d+)', text, re.M)))
                for line in re.findall(r'^\s*Rates:\s*([\d, ]+)', text, re.M):
                    rates.update(map(int, re.findall(r'\d+', line)))
            if channels:
                result.append(dict(card=card.name[4:], id=card_id, usbid=usb_id,
                                   channels=sorted(channels), rates=sorted(rates)))
        except (OSError, ValueError):
            continue
    return result
