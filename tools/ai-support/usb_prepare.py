"""Prepare USB on the host before App CLI creates the App container."""
import subprocess
import threading


class StartupUSB:
    def __init__(self, usb, runner=subprocess.run):
        self.usb, self.run = usb, runner
        self.lock = threading.Lock()

    def verify(self):
        status = self.usb.status()
        adb = self.usb.gadget.state()
        if not status['host'] or (adb and adb.get('ActiveState') in ('active', 'activating', 'reloading')):
            raise ValueError('USB Host is not prepared. Stop the App and rerun tools/install-ai-support.sh over Wi-Fi/SSH.')

    def prepare(self):
        with self.lock:
            status = self.usb.status()
            if not status['available']:
                raise ValueError('USB role control is unavailable.')
            try:
                adb = self.usb.gadget.state()
                active = adb and adb.get('ActiveState') in ('active', 'activating', 'reloading')
                if status['host'] and not active:
                    self.usb.gadget.stop_monitor()
                    return
                result = self.run(['systemctl', 'show', 'arduino-router.service', '-p', 'ActiveState'],
                                  check=True, capture_output=True, text=True, timeout=2)
                running = result.stdout.strip() in ('ActiveState=active', 'ActiveState=activating',
                                                    'ActiveState=reloading')
                self.usb.gadget.stop_monitor()
                try:
                    if running:
                        self.run(['systemctl', 'stop', 'arduino-router.service'], check=True,
                                 capture_output=True, text=True, timeout=5)
                    self.usb.set_host(True)
                finally:
                    if running:
                        self.run(['systemctl', 'start', 'arduino-router.service'], check=True,
                                 capture_output=True, text=True, timeout=5)
                    if not self.usb.status()['host']:
                        self.usb.gadget.start_monitor()
            except (OSError, subprocess.SubprocessError) as exc:
                detail = (getattr(exc, 'stderr', '') or type(exc).__name__).strip()[:400]
                raise ValueError('USB startup preparation failed: ' + detail) from exc


if __name__ == '__main__':
    from usb_role import USBRole
    StartupUSB(USBRole()).prepare()
