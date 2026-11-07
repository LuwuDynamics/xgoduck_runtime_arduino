import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'tools/ai-support'),
               str(ROOT / 'python/vendor/luwu_os/apps/voice_chat')]
from onboarding import Onboarding, CloudProbe, HardwareClient, ota_url
from voice_config import VoiceConfig
from provisioning import (Session, Wifi, frame, crc, wifi_frames, split_nmcli, run_command,
    CTRL, DATA, SSID, PASSWORD, CONNECT, STATUS, SCAN, WIFI_STATE, WIFI_LIST, CUSTOM,
    FRAGMENT, CHECKSUM, REQUIRE_ACK, read_board_serial_number)
from blufi import SetupManager, Peripheral, Advertisement, Notify, Write, UUID
from install_config import write_units

IDENTITY = '02:12:34:56:AB:CD'
BOARD_SERIAL = '001234'
MQTT = {'endpoint': 'mock:1883', 'client_id': 'fixture', 'password': 'test-only-secret'}


class FakeHardware:
    def __init__(self):
        self.connected, self.advertising = False, True
        self.device_id = IDENTITY
        self.calls = []

    async def snapshot(self):
        return dict(network=dict(connected=self.connected), advertising=self.advertising, device_id=self.device_id,
                    name='XGO-DuckABCD', error='')

    async def advertise(self, enabled):
        self.advertising = enabled
        self.calls.append(enabled)

    async def close(self):
        pass


class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.config = VoiceConfig(self.folder / 'voice.json')
        self.config.update({'cloud_region': 'cn'})
        self.hardware = FakeHardware()
        self.probe = AsyncMock(return_value=dict(bound=False, mqtt={}))
        self.now = 1.
        self.onboard = Onboarding(self.config, self.hardware, self.probe, self.folder, clock=lambda: self.now)
        self.env = patch.dict(os.environ, DUCK_DEVICE_ID=IDENTITY)
        self.env.start()
        self.addCleanup(self.env.stop)

    async def asyncTearDown(self):
        await self.onboard.cancel_probe()

    async def tick(self, seconds=4):
        self.now += seconds
        await self.onboard.step()
        await asyncio.sleep(0)
        await self.onboard.step()

    async def test_first_boot_wifi_then_bind_without_audio_keeps_advertising_until_bound(self):
        await self.tick()
        self.assertEqual(self.onboard.snapshot()['state'], 'awaiting_network')
        self.probe.assert_not_called()
        self.hardware.connected = True
        await self.tick()
        self.assertFalse(self.onboard.ready)
        self.assertFalse(self.onboard.bound)
        self.assertTrue(self.hardware.advertising)
        # Already online but unbound must never shut down the phone's setup session.
        await self.tick(20)
        self.assertTrue(self.hardware.advertising)
        self.probe.return_value = dict(bound=True, mqtt=MQTT)
        await self.tick()
        self.assertTrue(self.onboard.ready)
        self.assertFalse(self.hardware.advertising)
        self.assertNotIn('test-only-secret', json.dumps(self.onboard.snapshot()))
        self.assertEqual((self.folder / 'binding.json').stat().st_mode & 0o777, 0o600)

    async def test_cached_binding_offline_recovery_and_unknown_cloud_are_separate(self):
        self.hardware.connected = True
        self.probe.return_value = dict(bound=True, mqtt=MQTT)
        await self.tick()
        restored = Onboarding(self.config, self.hardware, self.probe, self.folder, clock=lambda: self.now)
        self.onboard = restored
        self.hardware.connected = False
        await self.tick()
        self.assertTrue(restored.bound)
        self.assertFalse(restored.ready)
        self.assertTrue(self.hardware.advertising)
        self.hardware.connected = True
        self.probe.side_effect = httpx.ConnectError('fixture unavailable')
        await self.tick()
        self.assertTrue(restored.ready)
        self.assertTrue(restored.snapshot()['cached'])
        self.assertEqual(restored.snapshot()['cloud_error'], 'ConnectError')

    async def test_server_unbind_overrides_cache_and_region_does_not_reuse_other_cloud(self):
        self.hardware.connected = True
        self.probe.return_value = dict(bound=True, mqtt=MQTT)
        await self.tick()
        self.probe.return_value = dict(bound=False, mqtt={})
        await self.tick(31)
        self.assertFalse(self.onboard.ready)
        self.assertEqual(self.onboard.mqtt, {})
        self.assertTrue(self.hardware.advertising)
        self.config.update({'cloud_region': 'global'})
        self.probe.side_effect = httpx.ConnectError('fixture')
        await self.tick()
        self.assertIsNone(self.onboard.bound)
        self.assertFalse(self.onboard.ready)

    async def test_restored_unbound_state_is_cached_until_the_cloud_confirms_it(self):
        self.hardware.connected = True
        await self.tick()
        self.assertFalse(self.onboard.snapshot()['cached'])
        self.onboard = Onboarding(self.config, self.hardware, self.probe, self.folder, clock=lambda: self.now)
        self.probe.side_effect = httpx.ConnectError('fixture unavailable')
        await self.tick()
        self.assertEqual(self.onboard.snapshot()['state'], 'awaiting_binding')
        self.assertTrue(self.onboard.snapshot()['cached'])
        self.probe.side_effect = None
        await self.tick()
        self.assertFalse(self.onboard.snapshot()['cached'])

    async def test_cloud_requires_local_choice_and_language_never_selects_or_changes_it(self):
        self.hardware.connected = True
        self.config.update({'cloud_region': None, 'language': 'en'})
        # An obsolete phone-written file must never select a cloud.
        (self.folder / 'cloud-region').write_text('global')
        await self.tick()
        self.assertEqual(self.onboard.snapshot()['state'], 'awaiting_cloud_config')
        self.assertFalse(self.onboard.ready)
        self.probe.assert_not_called()
        self.assertTrue(self.hardware.advertising)
        self.config.update({'cloud_region': 'cn'})
        self.probe.return_value = dict(bound=True, mqtt=MQTT)
        await self.tick()
        self.assertTrue(self.onboard.ready)
        self.assertIn('xl-api.xgorobot.com', ota_url(self.config.get()))
        generation = self.onboard.generation
        self.config.update({'language': 'cn'})
        await self.tick()
        self.assertEqual(self.onboard.generation, generation)
        self.assertTrue(self.onboard.ready)
        self.config.update({'cloud_region': 'global'})
        self.probe.side_effect = httpx.ConnectError('fixture')
        await self.tick()
        self.assertIsNone(self.onboard.bound)
        self.assertEqual(self.onboard.mqtt, {})
        self.assertFalse(self.onboard.ready)
        self.assertIn('xl-api.luwudynamics.ai', ota_url(self.config.get()))

    async def test_mqtt_refresh_uses_selected_cloud_and_honors_revocation(self):
        self.hardware.connected = True
        self.probe.return_value = dict(bound=True, mqtt=MQTT)
        await self.tick()
        url = ota_url(self.config.get())
        self.probe.return_value = dict(bound=True, mqtt=dict(MQTT, password='new-fixture'))
        refreshed = await self.onboard.refresh_credentials(url)
        self.assertEqual(refreshed['password'], 'new-fixture')
        self.probe.return_value = dict(bound=False, mqtt={})
        self.assertIsNone(await self.onboard.refresh_credentials(url))
        self.assertFalse(self.onboard.ready)
        self.assertEqual(self.onboard.mqtt, {})
        self.config.update({'cloud_region': 'global'})
        self.probe.reset_mock()
        self.assertIsNone(await self.onboard.refresh_credentials(url))
        self.probe.assert_not_called()

    async def test_unknown_or_invalid_cloud_never_marks_device_bound(self):
        self.hardware.connected = True
        self.probe.return_value = dict(bound=True, mqtt={})
        await self.tick()
        self.assertIsNone(self.onboard.bound)
        self.assertFalse(self.onboard.ready)
        self.assertTrue(self.hardware.advertising)
        self.probe.side_effect = asyncio.TimeoutError()
        await self.tick()
        self.assertIsNone(self.onboard.bound)
        self.assertFalse(self.onboard.ready)

    async def test_root_and_chat_identity_mismatch_disables_misleading_bluetooth_binding(self):
        self.hardware.connected = True
        self.hardware.device_id = '02:12:34:56:00:01'
        self.probe.return_value = dict(bound=True, mqtt=MQTT)
        await self.tick()
        self.assertFalse(self.onboard.ready)
        self.assertTrue(self.onboard.snapshot()['identity_mismatch'])
        self.assertEqual(self.onboard.snapshot()['state'], 'hardware_error')
        self.assertFalse(self.hardware.advertising)
        self.hardware.device_id = IDENTITY
        await self.tick()
        self.assertTrue(self.onboard.ready)
        self.assertFalse(self.hardware.advertising)

    async def test_network_loss_cancels_an_inflight_probe_and_runtime_stop_cleans_up(self):
        self.hardware.connected = True
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def slow(_):
            entered.set()
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()
        self.probe.side_effect = slow
        await self.onboard.step()
        await entered.wait()
        self.hardware.connected = False
        await self.onboard.step()
        self.assertTrue(cancelled.is_set())
        self.assertIsNone(self.onboard.probe_task)

    async def test_rotating_credentials_do_not_interrupt_an_active_chat_session(self):
        self.hardware.connected = True
        self.probe.return_value = dict(bound=True, mqtt=MQTT)
        await self.tick()
        generation = self.onboard.generation
        self.probe.return_value = dict(bound=True, mqtt=dict(MQTT, password='rotated-fixture'))
        await self.tick(31)
        self.assertTrue(self.onboard.ready)
        self.assertEqual(self.onboard.generation, generation)
        self.assertEqual(self.onboard.mqtt['password'], 'rotated-fixture')


class PacketTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.notifications, self.events = [], []
        self.wifi = type('Network', (), dict(scan=AsyncMock(return_value=[('办公室:A', -40)]),
            status=AsyncMock(return_value={'connected': True}), connect=AsyncMock(return_value=True)))()
        async def send(raw):
            self.notifications.append(raw)
        self.session = Session(IDENTITY, self.wifi, send, lambda event, data: self.events.append(event))
        self.session.notifying = True

    async def test_phone_custom_frames_cannot_select_cloud_or_trigger_motion(self):
        for seq, payload in enumerate((b'cloud:cn', b'cloud:global', b'Duck.RemoteMove')):
            await self.session.feed(frame(DATA, CUSTOM, seq, payload))
        self.assertEqual(self.events, [])
        self.wifi.connect.assert_not_called()
        self.assertEqual(self.notifications, [])

    async def asyncTearDown(self):
        await self.session.close()

    async def test_serial_extends_mac_in_status_and_successful_connect(self):
        self.session = Session(IDENTITY, self.wifi, self.session.send, serial_number=BOARD_SERIAL)
        self.session.notifying = True
        await self.session.status()
        self.session.phone_done.set()
        await self.session.connect('office', 'fixture-password')
        reports = [packet for packet in self.notifications if packet[0] >> 2 == CUSTOM]
        self.assertEqual(len(reports), 3)
        for packet in reports:
            self.assertEqual(packet[3], 13)
            self.assertEqual(packet[4:10].hex(':').upper(), IDENTITY)
            self.assertEqual(packet[10:], b'\x02001234')
            self.assertLessEqual(len(packet), 20)

    async def test_ios_checksum_fragmented_password_ack_and_stable_binding_id(self):
        self.assertEqual(crc(b'123456789'), bytes.fromhex('4ed6'))
        await self.session.feed(frame(DATA, SSID, 0, '办公室:A'.encode(), CHECKSUM))
        password = b'fixture-password'
        await self.session.feed(frame(DATA, PASSWORD, 1, len(password).to_bytes(2, 'little') + password[:8],
                                      CHECKSUM | FRAGMENT | REQUIRE_ACK))
        await self.session.feed(frame(DATA, PASSWORD, 2, password[8:], CHECKSUM))
        await self.session.feed(frame(CTRL, CONNECT, 3, flags=CHECKSUM))
        # Repeated connect commands must not open a second nmcli connection.
        await self.session.feed(frame(CTRL, CONNECT, 4))
        for _ in range(60):
            if any(raw[0] >> 2 == CUSTOM for raw in self.notifications):
                break
            await asyncio.sleep(.01)
        self.session.phone_done.set()
        await self.session.connect_task
        self.wifi.connect.assert_awaited_once_with('办公室:A', 'fixture-password')
        raw_id = bytes.fromhex(IDENTITY.replace(':', ''))
        self.assertEqual([raw[4:] for raw in self.notifications if raw[0] >> 2 == CUSTOM], [raw_id, raw_id])
        self.assertEqual([raw[2] for raw in self.notifications], list(range(len(self.notifications))))
        self.assertEqual(self.session.password, '')
        self.assertEqual(self.events[-1], 'provisioned')

    async def test_malformed_crc_encrypted_and_mismatched_fragments_never_reach_wifi(self):
        valid = frame(DATA, PASSWORD, 0, b'fixture-password', CHECKSUM)
        for raw in (b'\x01', valid[:-1], valid[:-1] + bytes([valid[-1] ^ 1]), frame(DATA, PASSWORD, 0, b'x', 1)):
            with self.assertRaises(ValueError):
                await self.session.feed(raw)
        await self.session.feed(frame(DATA, SSID, 1, b'\x04\x00test', FRAGMENT))
        with self.assertRaises(ValueError):
            await self.session.feed(frame(DATA, PASSWORD, 2, b'no'))
        self.wifi.connect.assert_not_called()
        self.assertEqual(self.session.password, '')

    async def test_wifi_scan_empty_response_and_reconnect_clear_old_credentials(self):
        self.wifi.scan.return_value = []
        await self.session.feed(frame(CTRL, SCAN, 0))
        await self.session.scan_task
        self.assertEqual(self.notifications[0][0] >> 2, WIFI_LIST)
        self.assertEqual(self.notifications[0][3], 0)
        self.session.password, self.session.ssid = 'fixture-password', 'old'
        self.session.reset()
        self.assertEqual((self.session.password, self.session.ssid, self.session.seq), ('', '', -1))

    async def test_list_fragments_fit_mtu_and_keep_unicode_ssids(self):
        aps = [('办公室:A', -38), ('B' * 32, -80), ('guest', -50)]
        chunks = wifi_frames(aps)
        blob = b''.join(payload[2:] if flags & FRAGMENT else payload for payload, flags in chunks)
        recovered = []
        while blob:
            length, rssi = blob[0], blob[1] - 256
            recovered.append((blob[2:1 + length].decode(), rssi))
            blob = blob[1 + length:]
        self.assertEqual(recovered, aps)
        for seq, (payload, flags) in enumerate(chunks):
            self.assertLessEqual(len(frame(DATA, WIFI_LIST, seq, payload, flags)), 20)

    async def test_status_does_not_interleave_a_fragmented_wifi_list(self):
        self.wifi.scan.return_value = [('long-network-name-' + str(i), -40) for i in range(5)]
        await self.session.feed(frame(CTRL, SCAN, 0))
        while not self.notifications:
            await asyncio.sleep(.005)
        status = asyncio.create_task(self.session.status())
        await self.session.scan_task
        await status
        types = [raw[0] >> 2 for raw in self.notifications]
        last_list = max(i for i, kind in enumerate(types) if kind == WIFI_LIST)
        self.assertTrue(all(kind == WIFI_LIST for kind in types[:last_list + 1]))
        self.assertEqual(types[last_list + 1:], [WIFI_STATE, CUSTOM])

    async def test_connect_timeout_notifies_failure_before_slow_rollback_finishes(self):
        restoring, release = asyncio.Event(), asyncio.Event()
        async def slow(*_):
            try:
                await asyncio.sleep(30)
            finally:
                restoring.set()
                await release.wait()
        self.wifi.connect.side_effect = slow
        self.session.connect_timeout = .03
        await self.session.feed(frame(CTRL, CONNECT, 0))
        for _ in range(100):
            if self.notifications:
                break
            await asyncio.sleep(.005)
        self.assertEqual(self.notifications[0][4:7], bytes([1, 1, 0]))
        self.assertTrue(self.session.busy)
        await asyncio.wait_for(restoring.wait(), 1)
        release.set()
        await self.session.connect_task
        self.assertEqual(self.events[-1], 'connect_failed')


class WifiTest(unittest.IsolatedAsyncioTestCase):
    async def test_scan_handles_escaped_ssid_colons_backslashes_and_duplicates(self):
        async def run(args, **kwargs):
            if 'SSID,SIGNAL' in args:
                return 0, 'Office\\:A:65\nOffice\\:A:85\nSlash\\\\Name:50\n办公室:70\n:99\n'
            return 0, ''
        aps = await Wifi(run).scan()
        self.assertEqual(aps, [('Office:A', -15), ('办公室', -30), ('Slash\\Name', -50)])

    async def exercise_connection(self, success):
        active, candidate, calls = 'old-id', None, []
        async def run(args, **kwargs):
            nonlocal active, candidate
            calls.append(args)
            if 'GENERAL.STATE,GENERAL.CONNECTION' in args:
                return 0, 'GENERAL.STATE:' + ('100 (connected)' if active else '30 (disconnected)') + '\n'
            if 'UUID,DEVICE' in args:
                return 0, active + ':wlan0\n' if active else ''
            if 'UUID,NAME' in args:
                return 0, 'old-id:ExistingUserNetwork\n'
            if 'add' in args:
                candidate = args[args.index('connection.uuid') + 1]
            if 'up' in args:
                requested = args[args.index('uuid') + 1]
                if requested == candidate:
                    active = candidate if success else None
                    return (0 if success else 1), ''
                active = requested
            return 0, ''
        result = await Wifi(run).connect('Office:A', 'fixture-password')
        self.assertEqual(result, success)
        self.assertEqual(active, candidate if success else 'old-id')
        deletes = [args[args.index('uuid') + 1] for args in calls if 'delete' in args]
        self.assertNotIn('old-id', deletes)
        if not success:
            self.assertEqual(deletes, [candidate])

    async def test_failed_new_network_preserves_and_restores_existing_profile(self):
        await self.exercise_connection(False)

    async def test_success_keeps_other_user_profiles_and_uses_permanent_mac(self):
        await self.exercise_connection(True)

    async def test_nmcli_timeout_and_cancellation_reap_children(self):
        processes = []
        real = asyncio.create_subprocess_exec
        async def tracked(*args, **kwargs):
            proc = await real(*args, **kwargs)
            processes.append(proc)
            return proc
        with patch('provisioning.asyncio.create_subprocess_exec', side_effect=tracked):
            rc, _ = await run_command([sys.executable, '-c', 'import time;time.sleep(30)'], timeout=.05)
            self.assertEqual(rc, -1)
            task = asyncio.create_task(run_command([sys.executable, '-c', 'import time;time.sleep(30)']))
            while len(processes) < 2:
                await asyncio.sleep(.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        for proc in processes:
            self.assertIsNotNone(proc.returncode)
            with self.assertRaises(ProcessLookupError):
                os.kill(proc.pid, 0)


class HostConfigurationTest(unittest.IsolatedAsyncioTestCase):
    async def test_boot_units_quote_paths_and_order_official_daemon_after_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            write_units('/home/arduino/Duck 100% App', IDENTITY, target)
            unit = (target / 'xgoduck-ai-hardware.service').read_text()
            self.assertIn('Duck 100%% App/data/gamepad.sock', unit)
            self.assertIn('--device-id ' + IDENTITY, unit)
            self.assertIn('WantedBy=multi-user.target', unit)
            self.assertIn('After=xgoduck-ai-hardware.service', (target / 'arduino-app-cli.service.d/xgoduck-ai.conf').read_text())
            self.assertIn('ExecStartPre=/usr/bin/python3 /usr/local/lib/xgoduck-ai-support/sketch_cache.py "/home/arduino/Duck 100%% App"',
                          (target / 'arduino-app-cli.service.d/xgoduck-ai.conf').read_text())
            usb = (target / 'xgoduck-ai-usb-startup.service').read_text()
            self.assertIn('Type=oneshot', usb)
            self.assertIn('RemainAfterExit=yes', usb)
            self.assertIn('Before=xgoduck-ai-hardware.service arduino-app-cli.service', usb)
            self.assertIn('xgoduck-ai-usb-startup.service', unit)

    async def test_first_boot_retry_is_only_for_our_default_and_never_after_manual_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            root, default = Path(directory), Path(directory) / 'default.app'
            default.write_text(str(root))
            run = AsyncMock(return_value=(0, ''))
            manager = SetupManager(IDENTITY, app_root=root, default_file=default, run=run)
            manager.event('provisioned', {})
            await manager.recover_default_app()
            await manager.recover_default_app()
            run.assert_awaited_once_with(['systemctl', 'try-restart', 'arduino-app-cli.service'], timeout=8)
            run.reset_mock()
            manager.restarted = False
            manager.request(False)  # Python already ran; a later manual Stop must stay stopped.
            await manager.recover_default_app()
            run.assert_not_called()
            manager.app_seen = False
            default.write_text(str(root / 'another-app'))
            await manager.recover_default_app()
            run.assert_not_called()


class BoardSerialTest(unittest.TestCase):
    def test_decimal_sysfs_serial_is_read_as_six_hex_digits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'serial_number'
            self.assertIsNone(read_board_serial_number(path))
            for raw, expected in (('4660\n', BOARD_SERIAL), ('123456\n', '01E240'),
                                  ('305419896\n', '345678'), ('16777216\n', '000000'),
                                  ('4294967295\n', 'FFFFFF'),
                                  ('', None), ('0', None), ('-1', None), ('4294967296', None)):
                with self.subTest(raw=raw):
                    path.write_text(raw)
                    self.assertEqual(read_board_serial_number(path), expected)


class CloudResponseTest(unittest.IsolatedAsyncioTestCase):
    async def test_ota_payload_and_binding_response_never_require_audio(self):
        import ota
        # The manager API returns MQTT alongside activation for an unbound
        # device. Activation must take precedence over those credentials.
        responses = [dict(activation={'code': 'fixture'}, mqtt=MQTT), dict(mqtt=MQTT),
                     dict(mqtt=MQTT), dict(firmware={})]
        bodies = []
        def respond(request):
            bodies.append(json.loads(request.content))
            self.assertEqual(request.headers['Device-Id'], IDENTITY.lower())
            payload = json.loads(request.content)
            self.assertEqual(payload['mac_address'], IDENTITY.lower())
            self.assertEqual(payload['board']['mac'], IDENTITY.lower())
            return httpx.Response(200, json=responses.pop(0))
        hardware = HardwareClient()
        self.addAsyncCleanup(hardware.close)
        await hardware.client.aclose()
        host_replies = iter([httpx.Response(200, json={'serial_number': BOARD_SERIAL}), httpx.Response(404),
                             httpx.Response(200, json={'serial_number': '00001234'}),
                             httpx.Response(200, json={'serial_number': None})])

        def host_response(request):
            self.assertEqual(request.url.path, '/serial-number')
            return next(host_replies)

        hardware.client = httpx.AsyncClient(transport=httpx.MockTransport(host_response), base_url='http://hardware')
        probe = CloudProbe(hardware)
        self.addAsyncCleanup(probe.close)
        await probe.client.aclose()
        probe.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        cfg = dict(language='cn', ota_url='https://fixture.invalid/ota/')
        with patch('ota.get_device_id', return_value=IDENTITY), patch('ota.get_client_id', return_value='fixture'), \
             patch('ota.OtaManager._headers', return_value={'Device-Id': IDENTITY}), \
             patch('ota._read_app_version', return_value='0.2.0'):
            self.assertFalse((await probe(cfg))['bound'])
            result = await probe(cfg)
            self.assertTrue(result['bound'])
            self.assertEqual(result['mqtt'], MQTT)  # Signed credentials remain opaque.
            self.assertEqual(ota.get_device_id(), IDENTITY)  # Local/BluFi identity is unchanged.
            self.assertTrue((await probe(cfg))['bound'])  # An older host helper may still return eight digits.
            with self.assertRaises(ValueError):
                await probe(cfg)
        self.assertEqual(bodies[0]['board']['type'], 'XGO-Duck')
        self.assertEqual(bodies[0]['application']['name'], 'xgoduck-ai')
        self.assertEqual(bodies[0]['serial_number'], BOARD_SERIAL)
        for body in bodies[1:]:
            self.assertNotIn('serial_number', body)

    async def test_ota_drops_serial_on_redirects_and_plain_http(self):
        hardware = AsyncMock()
        hardware.serial_number.return_value = BOARD_SERIAL
        requests = []

        def respond(request):
            requests.append((str(request.url), json.loads(request.content)))
            if request.url.host == 'fixture.invalid' and request.url.scheme == 'https':
                return httpx.Response(307, headers={'location': 'http://redirected.invalid/ota/'})
            return httpx.Response(200, json={'mqtt': MQTT})

        probe = CloudProbe(hardware)
        self.addAsyncCleanup(probe.close)
        await probe.client.aclose()
        probe.client = httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True)
        with patch('ota.get_device_id', return_value=IDENTITY), patch('ota.get_client_id', return_value='fixture'):
            await probe({'ota_url': 'https://fixture.invalid/ota/'})
            await probe({'ota_url': 'http://fixture.invalid/ota/'})
        hardware.serial_number.assert_awaited_once()
        self.assertEqual([url for url, _ in requests], [
            'https://fixture.invalid/ota/', 'https://fixture.invalid/ota/', 'http://redirected.invalid/ota/',
            'http://fixture.invalid/ota/',
        ])
        self.assertEqual(requests[0][1]['serial_number'], BOARD_SERIAL)
        for _, body in requests[1:]:
            self.assertNotIn('serial_number', body)


if __name__ == '__main__':
    unittest.main()
