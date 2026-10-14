#!/usr/bin/env python3
"""Board Bluetooth/gamepad worker. Local Unix socket, no TCP listener."""
import argparse
import grp
from http.server import BaseHTTPRequestHandler
import json
import os
from pathlib import Path
import re
import socketserver
import threading
import time
import dbus
import dbus.service
from dbus.mainloop.glib import DBusGMainLoop
from gi.repository import GLib
from input_reader import InputReader
from unitree_remote import UnitreeRemote
from usb_role import USBRole

BLUEZ = 'org.bluez'
PROPS = 'org.freedesktop.DBus.Properties'
DEVICE = 'org.bluez.Device1'
ADAPTER = 'org.bluez.Adapter1'
AGENT = 'org.bluez.Agent1'


def is_unitree_remote(p):
    return str(p.get('Name', p.get('Alias', ''))).lower().startswith('unitree-')


def candidate(p):
    name = str(p.get('Name', '')).lower()
    cls = int(p.get('Class', 0))
    return (is_unitree_remote(p) or str(p.get('Icon', '')) == 'input-gaming' or
            int(p.get('Appearance', 0)) in (0x3c3, 0x3c4) or
            (cls & 0x1f00 == 0x500 and (cls & 0x3c) in (4, 8)) or
            any(s in name for s in ('xbox', 'gamepad', 'controller', 'joy-con', '8bitdo')))


class Rejected(dbus.DBusException):
    _dbus_error_name = 'org.bluez.Error.Rejected'


class Agent(dbus.service.Object):
    def __init__(self, manager):
        super().__init__(manager.bus, '/duck/gamepad/agent')
        self.manager = manager

    def check(self, device):
        if str(device) != self.manager.pending:
            raise Rejected('Select this controller in the Duck page first.')

    @dbus.service.method(AGENT, in_signature='ou', out_signature='')
    def RequestConfirmation(self, device, passkey):
        self.check(device)

    @dbus.service.method(AGENT, in_signature='o', out_signature='')
    def RequestAuthorization(self, device):
        self.check(device)

    @dbus.service.method(AGENT, in_signature='os', out_signature='')
    def AuthorizeService(self, device, uuid):
        self.check(device)
        if str(uuid).lower() not in ('00001124-0000-1000-8000-00805f9b34fb', '00001812-0000-1000-8000-00805f9b34fb'):
            raise Rejected('Only gamepad HID services are allowed.')

    @dbus.service.method(AGENT, in_signature='o', out_signature='s')
    def RequestPinCode(self, device):
        raise Rejected('PIN-based controllers are not supported.')

    @dbus.service.method(AGENT, in_signature='o', out_signature='u')
    def RequestPasskey(self, device):
        raise Rejected('Passkey-based controllers are not supported.')

    @dbus.service.method(AGENT, in_signature='', out_signature='')
    def Cancel(self):
        pass

    @dbus.service.method(AGENT, in_signature='', out_signature='')
    def Release(self):
        pass


class Bluetooth:
    def __init__(self):
        self.lock = threading.RLock()
        self.state = {'available': False, 'devices': [], 'error': '', 'busy': False}
        self.pending = None
        self.paths = {}
        self.scan_until = 0
        self.reader = InputReader()
        self.unitree = UnitreeRemote()
        self.remote_selected = False
        self.usb = USBRole()
        DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.agent = Agent(self)
        dbus.Interface(self.bus.get_object(BLUEZ, '/org/bluez'), 'org.bluez.AgentManager1').RegisterAgent(
            '/duck/gamepad/agent', 'NoInputNoOutput')
        self.refresh()
        GLib.timeout_add(750, self.refresh)

    def snapshot(self):
        with self.lock:
            hid, remote = self.reader.snapshot(), self.unitree.snapshot()
            devices = [dict(d, selected=d['selected'] and not self.remote_selected) for d in hid['devices']]
            if remote.get('connected'):
                devices.append(dict(path=remote['path'], name=remote['name'], selected=self.remote_selected))
            data = dict(remote if self.remote_selected else hid)
            data['devices'] = devices
            return dict(bluetooth=dict(self.state), input=data, usb=self.usb.status())

    def refresh(self):
        try:
            objects = dbus.Interface(self.bus.get_object(BLUEZ, '/'),
                'org.freedesktop.DBus.ObjectManager').GetManagedObjects(timeout=3)
            adapters = [(str(p), v[ADAPTER]) for p, v in objects.items() if ADAPTER in v]
            self.adapter = adapters[0][0] if adapters else None
            devices, paths = [], {}
            remote = self.unitree.snapshot()
            for path, interfaces in objects.items():
                p = interfaces.get(DEVICE)
                if not p or not candidate(p):
                    continue
                addr = str(p['Address'])
                paths[addr] = str(path)
                devices.append(dict(address=addr, name=str(p.get('Name', p.get('Alias', addr))),
                    paired=bool(p.get('Paired')), connected=(bool(remote.get('connected')) if is_unitree_remote(p) and addr == remote.get('address') else bool(p.get('Connected')) and not is_unitree_remote(p)),
                    kind='unitree_ble' if is_unitree_remote(p) else 'gamepad',
                    connect_supported=True,
                    trusted=bool(p.get('Trusted')), rssi=int(p['RSSI']) if 'RSSI' in p else None))
            self.paths = paths
            a = adapters[0][1] if adapters else {}
            with self.lock:
                self.state.update(available=bool(adapters), powered=bool(a.get('Powered')),
                    discovering=bool(a.get('Discovering')), devices=devices,
                    scan_remaining=max(0, round(self.scan_until-time.monotonic())))
                if self.remote_selected and remote.get('error'):
                    self.state['error'] = remote['error']
            if self.scan_until and time.monotonic() >= self.scan_until:
                self.stop_scan()
        except Exception as e:
            with self.lock:
                self.state.update(available=False, error=str(e))
        return True

    def stop_scan(self):
        if self.scan_until and self.adapter:
            self.scan_until = 0
            try:
                dbus.Interface(self.bus.get_object(BLUEZ, self.adapter), ADAPTER).StopDiscovery(timeout=3)
            except dbus.DBusException:
                pass

    def finish(self, error=None):
        with self.lock:
            self.state.update(busy=False, operation='', error=str(error) if error else '')
        self.pending = None
        self.refresh()

    def dispatch(self, action, body):
        # All D-Bus calls run on GLib's owner thread, including async pairing replies.
        result, done = {}, threading.Event()
        def call():
            if self.state['busy'] and action != 'select':
                result['error'] = 'A Bluetooth operation is already in progress.'
                done.set()
                return False
            try:
                self.action(action, body)
                result['ok'] = True
            except Exception as e:
                result['error'] = str(e)
                self.finish(e)
            finally:
                done.set()
            return False
        GLib.idle_add(call)
        if not done.wait(8):
            raise ValueError('Bluetooth is busy. Check status before retrying.')
        if 'error' in result:
            raise ValueError(result['error'])

    def action(self, action, body):
        if action == 'usb':
            self.usb.set_host(body.get('host'))
            return
        if action == 'select':
            path = body.get('path')
            remote = self.unitree.snapshot()
            if remote.get('connected') and path == remote.get('path'):
                self.remote_selected = True
            else:
                self.reader.select(path)
                self.remote_selected = False
            self.state['error'] = ''
            return
        if self.state['busy']:
            raise ValueError('A Bluetooth operation is already in progress.')
        if not self.adapter:
            raise ValueError('No Bluetooth adapter found.')
        obj = self.bus.get_object(BLUEZ, self.adapter)
        if action == 'scan':
            dbus.Interface(obj, PROPS).Set(ADAPTER, 'Powered', dbus.Boolean(True), timeout=3)
            self.state['error'] = ''
            if not self.scan_until:
                iface = dbus.Interface(obj, ADAPTER)
                iface.SetDiscoveryFilter({'Transport': dbus.String('auto')}, timeout=3)
                iface.StartDiscovery(timeout=3)
            self.scan_until = time.monotonic() + 30
            self.refresh()
            return
        if action == 'stop-scan':
            self.stop_scan()
            self.refresh()
            return
        if action not in ('connect', 'disconnect', 'forget'):
            raise ValueError('Unknown operation.')
        address = body.get('address', '')
        if not isinstance(address, str) or not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', address):
            raise ValueError('Invalid Bluetooth address.')
        path = self.paths.get(address.upper())
        if not path:
            raise ValueError('Controller not found. Scan again.')
        dev = self.bus.get_object(BLUEZ, path)
        iface, props = dbus.Interface(dev, DEVICE), dbus.Interface(dev, PROPS)
        properties = props.GetAll(DEVICE, timeout=3)
        if is_unitree_remote(properties) and action in ('connect', 'disconnect', 'forget'):
            if action == 'connect':
                self.stop_scan()
                self.state.update(busy=True, operation=action, error='')
                def ready(error):
                    def finish_remote():
                        if not error:
                            self.remote_selected = True
                        self.finish(error)
                        return False
                    GLib.idle_add(finish_remote)
                self.unitree.connect(address.upper(), str(properties.get('Name', address)),
                                     self.adapter.rsplit('/',1)[-1], ready)
                return
            self.unitree.disconnect()
            if action == 'disconnect':
                self.finish()
                return
        self.state.update(busy=True, operation=action, error='')
        if action == 'disconnect':
            iface.Disconnect(reply_handler=lambda: self.finish(), error_handler=self.finish, timeout=15)
        elif action == 'forget':
            dbus.Interface(obj, ADAPTER).RemoveDevice(path, reply_handler=lambda: self.finish(),
                                                       error_handler=self.finish, timeout=15)
        else:
            self.stop_scan()
            self.pending = path
            def connected():
                # An explicit HID connection leaves the old BLE source.
                # Robot control still requires a separate Enable action.
                self.remote_selected = False
                self.finish()
            def connect():
                try:
                    props.Set(DEVICE, 'Trusted', dbus.Boolean(True), timeout=3)
                    iface.Connect(reply_handler=connected, error_handler=self.finish, timeout=20)
                except Exception as e:
                    self.finish(e)
            if bool(props.Get(DEVICE, 'Paired', timeout=3)):
                connect()
            else:
                iface.Pair(reply_handler=connect, error_handler=self.finish, timeout=50)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def respond(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        self.respond(200, self.server.manager.snapshot()) if self.path == '/status' else self.respond(404, {'error': 'Not found'})

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 <= length <= 4096:
                raise ValueError('Request too large.')
            body = json.loads(self.rfile.read(length) or b'{}')
            if not isinstance(body, dict):
                raise ValueError('Expected an object.')
            self.server.manager.dispatch(self.path.lstrip('/'), body)
            self.respond(200, self.server.manager.snapshot())
        except (ValueError, TypeError) as e:
            self.respond(400, {'error': str(e)})


class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--socket', required=True)
    args = p.parse_args()
    path = Path(args.socket)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    manager = Bluetooth()
    with Server(str(path), Handler) as server:
        server.manager = manager
        os.chown(path, 0, grp.getgrnam('arduino').gr_gid)
        os.chmod(path, 0o660)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        GLib.MainLoop().run()


if __name__ == '__main__':
    main()
