"""Fixed USB role control for the board's existing privileged worker."""
from pathlib import Path
import subprocess
import threading
import time

ROLE = Path('/sys/kernel/debug/usb/4e00000.usb/mode')
ADB_UDC = Path('/sys/kernel/config/usb_gadget/g1/UDC')


class ADBGadget:
    """Quiesce the fixed ADB gadget service while the USB port is a host."""
    def __init__(self, runner=subprocess.run, systemd=None, udc=ADB_UDC):
        self.run = runner
        self.systemd = Path('/run/systemd/system').is_dir() if systemd is None else systemd
        self.resume = False
        self.resume_serial = False
        self.resume_path = False
        self.udc = udc
        self.previous_udc = ''

    def state(self, service='adbd.service'):
        if not self.systemd:
            return None
        result = self.run(['systemctl', 'show', service, '-p', 'LoadState',
                           '-p', 'ActiveState', '-p', 'UnitFileState'],
                          check=True, capture_output=True, text=True, timeout=1)
        state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        return None if state.get('LoadState') == 'not-found' else state

    def stop_monitor(self):
        # The path watcher immediately restarts the proxy while ttyGS0 exists;
        # the proxy in turn Requires=router. Stop the watcher first.
        for unit, attribute in [('arduino-router-serial.path', 'resume_path'),
                                ('arduino-router-serial.service', 'resume_serial')]:
            state = self.state(unit)
            if state is not None:
                resume = (state.get('ActiveState') in ('active', 'activating', 'reloading')
                          or state.get('UnitFileState') in ('enabled', 'enabled-runtime'))
                setattr(self, attribute, getattr(self, attribute) or resume)
                self.run(['systemctl', 'stop', unit], check=True,
                         capture_output=True, text=True, timeout=3)

    def start_monitor(self):
        for unit, attribute in [('arduino-router-serial.path', 'resume_path'),
                                ('arduino-router-serial.service', 'resume_serial')]:
            state = self.state(unit)
            if state is not None and state.get('UnitFileState') not in ('masked', 'masked-runtime'):
                if getattr(self, attribute) or state.get('UnitFileState') in ('enabled', 'enabled-runtime'):
                    self.run(['systemctl', 'start', unit], check=True,
                             capture_output=True, text=True, timeout=3)

    def stop_for_host(self):
        self.stop_monitor()
        state = self.state()
        if state is None:
            return
        self.resume |= (state.get('ActiveState') in ('active', 'activating', 'reloading')
                        or state.get('UnitFileState') in ('enabled', 'enabled-runtime'))
        if self.udc.exists():
            controller = self.udc.read_text().strip()
            if controller:
                self.previous_udc = controller
                # The packaged reset script unmounts FunctionFS without first
                # unbinding it, which can warn in the kernel and lose UART RX.
                self.udc.write_text('\n')
                if self.udc.read_text().strip():
                    raise ValueError('ADB gadget did not unbind from the USB controller.')
        # An explicit stop also cancels Restart=always, including a restart loop.
        self.run(['systemctl', 'stop', 'adbd.service'], check=True,
                 capture_output=True, text=True, timeout=3)

    def start_for_device(self):
        state = self.state()
        if state is not None and state.get('UnitFileState') not in ('masked', 'masked-runtime') and (
                self.resume or state.get('UnitFileState') in ('enabled', 'enabled-runtime')):
            self.run(['systemctl', 'start', 'adbd.service'], check=True,
                     capture_output=True, text=True, timeout=3)
            # If stop failed before the daemon exited, start is a no-op. Restore
            # the original binding rather than leave that live daemon detached.
            if self.previous_udc and self.udc.exists() and not self.udc.read_text().strip():
                self.udc.write_text(self.previous_udc + '\n')
        self.start_monitor()


class USBRole:
    def __init__(self, path=ROLE, gadget=None):
        self.path = path
        self.gadget = gadget if gadget is not None else ADBGadget()
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
            previous = self.status()['mode']
            if previous not in ('host', 'device', 'otg'):
                raise ValueError('USB role control is unavailable.')
            try:
                if enabled:
                    # Stop FunctionFS before removing the USB device controller.
                    # Otherwise adbd repeatedly binds a gadget in Host mode.
                    self.gadget.stop_for_host()
                if previous != mode:
                    self.path.write_text(mode + '\n')
                until = time.monotonic() + 2
                while time.monotonic() < until:
                    if self.status()['mode'] == mode:
                        if not enabled:
                            self.gadget.start_for_device()
                        return
                    time.sleep(.05)
                raise ValueError('USB mode change did not complete. Check the USB connection.')
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                if enabled and previous != 'host':
                    try:
                        self.path.write_text(previous + '\n')
                        self.gadget.start_for_device()
                    except (OSError, ValueError, subprocess.SubprocessError):
                        raise ValueError('USB Host failed and ADB could not be restored. Use Wi-Fi/SSH to recover.') from exc
                raise ValueError('Unable to switch USB mode: ' + str(exc)) from exc
