"""BlueZ GATT peripheral for provisioning only; never accepts motion commands."""
import asyncio
import contextlib
import threading
from pathlib import Path
from dbus_next import BusType, DBusError
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, method, dbus_property, PropertyAccess
from provisioning import Session, Wifi, identity_bytes, read_board_serial_number, run_command

BLUEZ = 'org.bluez'
ADAPTER = '/org/bluez/hci0'
ROOT = '/org/luwu/xgoduck/setup'
SERVICE = ROOT + '/service0'
AD = ROOT + '/advertisement0'
UUID = '0000{}-0000-1000-8000-00805f9b34fb'
READ = PropertyAccess.READ


async def close_bus(bus):
    if bus is None:
        return
    bus.disconnect()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(bus.wait_for_disconnect(), 2)
    # dbus-next 0.2.3 (Debian's version) shuts down but does not close these
    # descriptors. Explicitly release both after the event-loop reader is gone.
    for name in ('_stream', '_sock'):
        resource = getattr(bus, name, None)
        if resource is not None:
            resource.close()


class Advertisement(ServiceInterface):
    def __init__(self, name):
        super().__init__('org.bluez.LEAdvertisement1')
        self.local_name = name

    @dbus_property(access=READ)
    def Type(self) -> 's':
        return 'peripheral'

    @dbus_property(access=READ)
    def ServiceUUIDs(self) -> 'as':
        return [UUID.format('ffff')]

    @dbus_property(access=READ)
    def LocalName(self) -> 's':
        return self.local_name

    @dbus_property(access=READ)
    def MinInterval(self) -> 'u':
        return 20

    @dbus_property(access=READ)
    def MaxInterval(self) -> 'u':
        return 40

    @method()
    def Release(self):
        pass


class Service(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.GattService1')

    @dbus_property(access=READ)
    def UUID(self) -> 's':
        return UUID.format('ffff')

    @dbus_property(access=READ)
    def Primary(self) -> 'b':
        return True


class Characteristic(ServiceInterface):
    def __init__(self, uuid, flags):
        super().__init__('org.bluez.GattCharacteristic1')
        self.uuid, self.flags = uuid, flags

    @dbus_property(access=READ)
    def UUID(self) -> 's':
        return self.uuid

    @dbus_property(access=READ)
    def Service(self) -> 'o':
        return SERVICE

    @dbus_property(access=READ)
    def Flags(self) -> 'as':
        return self.flags


class Notify(Characteristic):
    def __init__(self):
        super().__init__(UUID.format('ff02'), ['read', 'notify'])
        self.value = b''
        self.session = None

    @dbus_property(access=READ)
    def Value(self) -> 'ay':
        return self.value

    @method()
    def ReadValue(self, options: 'a{sv}') -> 'ay':
        return self.value

    @method()
    def StartNotify(self):
        # BlueZ invokes this when the first subscriber arrives. Fresh sequence
        # zero is required by the native Android/iOS BluFi libraries.
        if self.session.busy:
            raise DBusError('org.bluez.Error.InProgress', 'Wi-Fi configuration is still finishing.')
        for task in list(self.session.tasks):
            task.cancel()
        self.session.reset()
        self.session.notifying = True

    @method()
    def StopNotify(self):
        self.session.notifying = False
        self.session.phone_done.set()
        self.session.owner = None

    async def send(self, value):
        self.value = value
        self.emit_properties_changed({'Value': value})


class Write(Characteristic):
    def __init__(self, session):
        super().__init__(UUID.format('ff01'), ['write', 'write-without-response'])
        self.session = session

    @method()
    async def WriteValue(self, value: 'ay', options: 'a{sv}'):
        device = options.get('device')
        try:
            await self.session.feed(bytes(value), device.value if device else None)
        except (ValueError, UnicodeError):
            raise DBusError('org.bluez.Error.InvalidValueLength', 'Invalid BluFi packet.')


class Peripheral:
    def __init__(self, identity, wifi, emit):
        self.name = 'XGO-Duck' + identity_bytes(identity).hex()[-4:].upper()
        self.identity, self.wifi, self.emit = identity, wifi, emit
        self.serial_number = read_board_serial_number()
        self.bus = self.adapter = self.session = None
        self.owner = None
        self.paths = []
        self.gatt_registered = self.ad_registered = False

    @property
    def busy(self):
        return bool(self.session and (self.session.busy or self.session.notifying))

    async def start(self):
        try:
            async with asyncio.timeout(8):
                self.bus = MessageBus(bus_type=BusType.SYSTEM)
                await self.bus.connect()
                dbus_intro = await self.bus.introspect('org.freedesktop.DBus', '/org/freedesktop/DBus')
                dbus = self.bus.get_proxy_object('org.freedesktop.DBus', '/org/freedesktop/DBus', dbus_intro)
                self.owner = await dbus.get_interface('org.freedesktop.DBus').call_get_name_owner(BLUEZ)
                intro = await self.bus.introspect(BLUEZ, ADAPTER)
                self.adapter = self.bus.get_proxy_object(BLUEZ, ADAPTER, intro)
                adapter = self.adapter.get_interface('org.bluez.Adapter1')
                await adapter.set_powered(True)
                await adapter.set_alias(self.name)  # GAP 0x2A00, used by Web Bluetooth.
                notify = Notify()
                self.session = Session(self.identity, self.wifi, notify.send, self.emit,
                                       serial_number=self.serial_number)
                notify.session = self.session
                for path, obj in [(SERVICE, Service()), (SERVICE + '/write', Write(self.session)),
                                  (SERVICE + '/notify', notify), (AD, Advertisement(self.name))]:
                    self.bus.export(path, obj)
                    self.paths.append(path)
                await self.adapter.get_interface('org.bluez.GattManager1').call_register_application(ROOT, {})
                self.gatt_registered = True
                await self.adapter.get_interface('org.bluez.LEAdvertisingManager1').call_register_advertisement(AD, {})
                self.ad_registered = True
            return True
        except (Exception, asyncio.CancelledError):
            await self.stop()
            raise

    async def healthy(self):
        try:
            async with asyncio.timeout(3):
                if self.bus is None or not self.bus.connected:
                    return False
                # Detect daemon replacement even when another service is advertising.
                intro = await self.bus.introspect('org.freedesktop.DBus', '/org/freedesktop/DBus')
                dbus = self.bus.get_proxy_object('org.freedesktop.DBus', '/org/freedesktop/DBus', intro)
                owner = await dbus.get_interface('org.freedesktop.DBus').call_get_name_owner(BLUEZ)
                powered = await self.adapter.get_interface('org.bluez.Adapter1').get_powered()
                manager = self.adapter.get_interface('org.bluez.LEAdvertisingManager1')
                return owner == self.owner and powered and await manager.get_active_instances() > 0
        except Exception:
            return False

    async def stop(self):
        if self.session is not None:
            await self.session.close()
        if self.adapter is not None:
            for registered, interface, method_name, path in [
                (self.ad_registered, 'org.bluez.LEAdvertisingManager1', 'call_unregister_advertisement', AD),
                (self.gatt_registered, 'org.bluez.GattManager1', 'call_unregister_application', ROOT)]:
                if registered:
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(getattr(self.adapter.get_interface(interface), method_name)(path), 2)
        if self.bus is not None:
            for path in self.paths:
                self.bus.unexport(path)
            await close_bus(self.bus)
        self.bus = self.adapter = self.session = None
        self.owner = None
        self.gatt_registered = self.ad_registered = False
        self.paths.clear()


class SetupManager:
    """Runs in an asyncio thread of the existing host hardware service."""
    def __init__(self, identity, wifi=None, peripheral=Peripheral, app_root=None,
                 default_file='/var/lib/arduino-app-cli/default.app', run=run_command):
        self.wifi = wifi or Wifi()
        self.peripheral = peripheral(identity, self.wifi, self.event)
        self.lock = threading.Lock()
        self.desired, self.closing = True, False
        self.app_root = Path(app_root).resolve() if app_root else None
        self.default_file, self.run_command = Path(default_file), run
        self.app_seen, self.restarted, self.setup_completed = False, False, False
        self.state = dict(advertising=False, requested=True, name=self.peripheral.name,
                          device_id=identity.upper(), event='', event_id=0, error='',
                          network=dict(available=False, connected=False))

    def event(self, event, data):
        with self.lock:
            self.state.update(event=event, event_id=self.state['event_id'] + 1)
            if event == 'provisioned':
                self.setup_completed = True

    def snapshot(self):
        with self.lock:
            return dict(self.state, network=dict(self.state['network']))

    def request(self, enabled):
        if not isinstance(enabled, bool):
            raise ValueError('enabled must be boolean')
        with self.lock:
            self.desired = enabled
            self.app_seen = True
            self.state['requested'] = enabled

    async def recover_default_app(self):
        # The official daemon attempts default startup once. On a fresh/offline
        # boot, missing dependencies can fail before Python ever reaches us.
        # Retry only after successful phone provisioning, only our default App,
        # once, and never after a manual Stop of an App we already saw running.
        with self.lock:
            recover = self.setup_completed and not self.app_seen and not self.restarted
        if not recover or self.app_root is None:
            return
        try:
            default = Path(self.default_file.read_text().strip()).resolve()
        except (OSError, ValueError):
            return
        if default != self.app_root:
            return
        self.restarted = True
        await self.run_command(['systemctl', 'try-restart', 'arduino-app-cli.service'], timeout=8)

    async def run(self):
        retry_at = 0.
        try:
            while not self.closing:
                await self.recover_default_app()
                network = await self.wifi.status()
                with self.lock:
                    self.state['network'] = network
                    desired, advertising = self.desired, self.state['advertising']
                now = asyncio.get_running_loop().time()
                if advertising and not await self.peripheral.healthy():
                    await self.peripheral.stop()
                    advertising = False
                    with self.lock:
                        self.state.update(advertising=False, error='Bluetooth controller reset; retrying.')
                if desired and not advertising and now >= retry_at:
                    try:
                        await self.peripheral.start()
                        with self.lock:
                            self.state.update(advertising=True, error='')
                    except Exception as exc:
                        retry_at = now + 5
                        with self.lock:
                            self.state.update(advertising=False, error=type(exc).__name__)
                elif advertising and not desired and not self.peripheral.busy:
                    await self.peripheral.stop()
                    with self.lock:
                        self.state['advertising'] = False
                await asyncio.sleep(1)
        finally:
            await self.peripheral.stop()
