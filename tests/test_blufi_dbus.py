"""Real private D-Bus/GATT round trips; only the radio and nmcli are simulated."""
import asyncio
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import AsyncMock, patch
from dbus_next import Variant
from dbus_next.aio import MessageBus
from dbus_next.service import ServiceInterface, dbus_property, method, PropertyAccess

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/ai-support'))
from blufi import Peripheral, ADAPTER, SERVICE, AD, close_bus
from provisioning import frame, CTRL, DATA, SSID, PASSWORD, CONNECT, CUSTOM, CHECKSUM


class Adapter(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.Adapter1')
        self.powered, self.alias = False, ''

    @dbus_property()
    def Powered(self) -> 'b':
        return self.powered

    @Powered.setter
    def Powered(self, value: 'b'):
        self.powered = value

    @dbus_property()
    def Alias(self) -> 's':
        return self.alias

    @Alias.setter
    def Alias(self, value: 's'):
        self.alias = value


class GattManager(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.GattManager1')
        self.registered = False

    @method()
    def RegisterApplication(self, path: 'o', options: 'a{sv}'):
        self.registered = True

    @method()
    def UnregisterApplication(self, path: 'o'):
        self.registered = False


class AdManager(ServiceInterface):
    def __init__(self):
        super().__init__('org.bluez.LEAdvertisingManager1')
        self.active = 0

    @dbus_property(access=PropertyAccess.READ)
    def ActiveInstances(self) -> 'y':
        return self.active

    @method()
    def RegisterAdvertisement(self, path: 'o', options: 'a{sv}'):
        self.active = 1

    @method()
    def UnregisterAdvertisement(self, path: 'o'):
        self.active = 0


@unittest.skipUnless(shutil.which('dbus-daemon'), 'Private D-Bus daemon required (Linux validation image includes it)')
class GattRoundTripTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_gatt_export_checksum_notify_stable_id_and_cleanup(self):
        daemon = await asyncio.create_subprocess_exec('dbus-daemon', '--session', '--nofork', '--print-address=1',
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        bluez = phone = peripheral = None
        try:
            address = (await asyncio.wait_for(daemon.stdout.readline(), 3)).decode().strip()
            bluez = await MessageBus(bus_address=address).connect()
            await bluez.request_name('org.bluez')
            adapter, gatt, advertisements = Adapter(), GattManager(), AdManager()
            for obj in (adapter, gatt, advertisements):
                bluez.export(ADAPTER, obj)
            wifi = type('Wifi', (), dict(connect=AsyncMock(return_value=True)))()
            events, values = [], []
            with patch.dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=address):
                peripheral = Peripheral('02:12:34:56:AB:CD', wifi, lambda event, data: events.append(event))
                self.assertTrue(await peripheral.start())
                self.assertTrue(gatt.registered)
                self.assertTrue(await peripheral.healthy())
                self.assertTrue(adapter.powered)
                self.assertEqual(adapter.alias, 'XGO-DuckABCD')
                phone = await MessageBus(bus_address=address).connect()
                owner = peripheral.bus.unique_name
                # BlueZ reads these properties when registering an advert.
                # Exercise the real interface name; a fake manager alone can
                # accept an object that BlueZ would reject.
                intro = await phone.introspect(owner, AD)
                properties = phone.get_proxy_object(owner, AD, intro).get_interface('org.freedesktop.DBus.Properties')
                advertisement = await properties.call_get_all('org.bluez.LEAdvertisement1')
                self.assertEqual(advertisement['LocalName'].value, 'XGO-DuckABCD')
                self.assertEqual(advertisement['Type'].value, 'peripheral')
                self.assertEqual(advertisement['ServiceUUIDs'].value, ['0000ffff-0000-1000-8000-00805f9b34fb'])
                intro = await phone.introspect(owner, SERVICE + '/notify')
                notify = phone.get_proxy_object(owner, SERVICE + '/notify', intro)
                notify.get_interface('org.freedesktop.DBus.Properties').on_properties_changed(
                    lambda iface, changed, invalidated: values.append(changed['Value'].value) if 'Value' in changed else None)
                characteristic = notify.get_interface('org.bluez.GattCharacteristic1')
                await characteristic.call_start_notify()
                intro = await phone.introspect(owner, SERVICE + '/write')
                write = phone.get_proxy_object(owner, SERVICE + '/write', intro).get_interface('org.bluez.GattCharacteristic1')
                options = {'device': Variant('o', '/org/bluez/hci0/dev_PHONE')}
                await write.call_write_value(frame(DATA, SSID, 0, b'Office:A', CHECKSUM), options)
                await write.call_write_value(frame(DATA, PASSWORD, 1, b'fixture-password', CHECKSUM), options)
                await write.call_write_value(frame(CTRL, CONNECT, 2, flags=CHECKSUM), options)
                for _ in range(100):
                    if any(raw[0] >> 2 == CUSTOM for raw in values):
                        break
                    await asyncio.sleep(.01)
                self.assertIn(bytes.fromhex('02123456abcd'), [raw[4:] for raw in values if raw[0] >> 2 == CUSTOM])
                await characteristic.call_stop_notify()
                await peripheral.session.connect_task
                self.assertIn('provisioned', events)
                wifi.connect.assert_awaited_once_with('Office:A', 'fixture-password')
                advertisements.active = 0
                self.assertFalse(await peripheral.healthy())
                await peripheral.stop()
                self.assertFalse(gatt.registered)
                self.assertEqual(advertisements.active, 0)
        finally:
            if peripheral:
                await peripheral.stop()
            for bus in (phone, bluez):
                if bus:
                    await close_bus(bus)
            daemon.terminate()
            await daemon.wait()
