"""Choose the USB role before App CLI creates the App container."""
from pathlib import Path
import subprocess
import threading
import time

ACTIVE = ('active', 'activating', 'reloading')
MEDIA_CLASSES = ('01', '0e')  # USB Audio and Video interfaces.
MEDIA_WAIT_S = 5.0
POLL_S = 0.1


class StartupUSB:
    def __init__(self, usb, runner=subprocess.run, sysfs=Path('/sys')):
        self.usb, self.run = usb, runner
        self.sysfs = sysfs
        self.lock = threading.Lock()

    def _adb_quiet(self):
        adb = self.usb.gadget.state()
        return not adb or adb.get('ActiveState') not in ACTIVE

    def verify(self):
        status = self.usb.status()
        if status['mode'] == 'device' or (status['host'] and self._adb_quiet()):
            return
        raise ValueError('USB role is not ready. Stop the App and rerun tools/install-ai-support.sh over Wi-Fi/SSH.')

    def _media_present(self):
        deadline = time.monotonic() + MEDIA_WAIT_S
        controller = self.usb.path.parent.name
        while True:
            for path in self.sysfs.glob('bus/usb/devices/*/bInterfaceClass'):
                try:
                    if controller in path.resolve(strict=True).parts and path.read_text().strip() in MEDIA_CLASSES:
                        return True
                except OSError:
                    # An interface can disappear while USB is enumerating.
                    continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(POLL_S, remaining))

    def prepare(self):
        with self.lock:
            status = self.usb.status()
            if not status['available']:
                raise ValueError('USB role control is unavailable.')
            try:
                host_ready = status['host'] and self._adb_quiet()
                if host_ready and self._media_present():
                    self.usb.gadget.stop_monitor()
                    return 'Host (USB audio or video detected)'

                result = self.run(['systemctl', 'show', 'arduino-router.service', '-p', 'ActiveState'],
                                  check=True, capture_output=True, text=True, timeout=2)
                running = result.stdout.strip().partition('=')[2] in ACTIVE
                prepared = False
                try:
                    self.usb.gadget.stop_monitor()
                    if running:
                        self.run(['systemctl', 'stop', 'arduino-router.service'], check=True,
                                 capture_output=True, text=True, timeout=5)
                    if not host_ready:
                        self.usb.set_host(True)
                        if self._media_present():
                            prepared = True
                            return 'Host (USB audio or video detected)'

                    self.usb.set_host(False)
                    prepared = True
                    return 'Device (no USB audio or video detected)'
                finally:
                    if running:
                        self.run(['systemctl', 'start', 'arduino-router.service'], check=True,
                                 capture_output=True, text=True, timeout=5)
                    if not prepared and self.usb.status()['mode'] in ('device', 'otg'):
                        self.usb.gadget.start_monitor()
            except (OSError, subprocess.SubprocessError) as exc:
                detail = (getattr(exc, 'stderr', '') or type(exc).__name__).strip()[:400]
                raise ValueError('USB startup preparation failed: ' + detail) from exc


if __name__ == '__main__':
    from usb_role import USBRole
    print('USB startup selected ' + StartupUSB(USBRole()).prepare(), flush=True)
