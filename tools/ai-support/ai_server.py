#!/usr/bin/env python3
"""USB/gamepad worker plus headless BluFi. No new TCP port or motion path."""
import argparse
import asyncio
import grp
import json
import os
from pathlib import Path
import signal
import threading
from gi.repository import GLib
from server import Bluetooth, Handler, Server
from usb_role import USBRole
from usb_prepare import StartupUSB
from blufi import SetupManager
from audio_caps import capture_devices


class Hardware:
    def __init__(self, identity, app_root=None):
        self.setup = SetupManager(identity, app_root=app_root)
        self.usb = USBRole()
        self.startup_usb = StartupUSB(self.usb)
        self.gamepad = None
        self.gamepad_error = ''
        # A failed Bluetooth controller must not take down USB/Wi-Fi recovery.
        try:
            self.gamepad = Bluetooth()
        except Exception as exc:
            self.gamepad_error = type(exc).__name__

    def snapshot(self):
        if self.gamepad is not None:
            data = self.gamepad.snapshot()
        else:
            data = dict(bluetooth=dict(available=False, devices=[], error=self.gamepad_error),
                        input=dict(connected=False, devices=[]), usb=self.usb.status())
        return dict(data, provisioning=self.setup.snapshot(), audio_inputs=capture_devices())

    def dispatch(self, action, body):
        if action == 'provision':
            if set(body) != {'enabled'}:
                raise ValueError('Expected only enabled.')
            self.setup.request(body['enabled'])
        elif action == 'prepare-usb':
            if body:
                raise ValueError('USB startup preparation takes no parameters.')
            self.startup_usb.verify()
        elif action == 'usb':
            self.usb.set_host(body.get('host'))
        elif self.gamepad is not None:
            self.gamepad.dispatch(action, body)
        else:
            raise ValueError('Bluetooth controller unavailable.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', required=True)
    parser.add_argument('--device-id', required=True)
    parser.add_argument('--app-root', required=True)
    args = parser.parse_args()
    path = Path(args.socket)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    hardware = Hardware(args.device_id, args.app_root)
    loop = asyncio.new_event_loop()
    def provision():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(hardware.setup.run())
        loop.close()
    thread = threading.Thread(target=provision, name='blufi', daemon=True)
    thread.start()
    glib = GLib.MainLoop()
    def stop(*_):
        hardware.setup.closing = True
        glib.quit()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, stop)
    try:
        with Server(str(path), Handler) as server:
            server.manager = hardware
            os.chown(path, 0, grp.getgrnam('arduino').gr_gid)
            os.chmod(path, 0o660)
            http = threading.Thread(target=server.serve_forever, daemon=True)
            http.start()
            try:
                glib.run()
            finally:
                server.shutdown()
                http.join(2)
    finally:
        hardware.setup.closing = True
        thread.join(12)
        path.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
