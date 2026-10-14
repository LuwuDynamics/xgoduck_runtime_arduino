"""Fixed USB role control for the board's existing privileged worker."""
from pathlib import Path
import threading
import time

ROLE = Path('/sys/kernel/debug/usb/4e00000.usb/mode')


class USBRole:
    def __init__(self, path=ROLE):
        self.path = path
        self.lock = threading.Lock()

    def status(self):
        try:
            mode = self.path.read_text().strip()
            return dict(available=mode in ('host', 'device', 'otg'),
                        host=mode == 'host', mode=mode, error='')
        except OSError:
            return dict(available=False, host=False, mode='unavailable',
                        error='USB role control is unavailable.')

    def set_host(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('host must be true or false.')
        mode = 'host' if enabled else 'device'
        with self.lock:
            try:
                if self.status()['mode'] != mode:
                    self.path.write_text(mode + '\n')
                until = time.monotonic() + 2
                while time.monotonic() < until:
                    if self.status()['mode'] == mode:
                        return
                    time.sleep(.05)
            except OSError as exc:
                raise ValueError('Unable to switch USB mode: ' + str(exc)) from exc
            raise ValueError('USB mode change did not complete. Check the USB connection.')
