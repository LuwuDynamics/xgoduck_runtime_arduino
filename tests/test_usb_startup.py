import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('usb_prepare_test', ROOT / 'tools/ai-support/usb_prepare.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class USBStartupTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.sysfs = Path(directory.name)
        self.now = 0.0
        clock = patch.object(module, 'time', SimpleNamespace(
            monotonic=lambda: self.now, sleep=self.advance))
        clock.start()
        self.addCleanup(clock.stop)

    def advance(self, delay):
        self.now += delay

    def sysfs_value(self, relative_path, value, controller='4e00000.usb'):
        entry = self.sysfs / relative_path
        device = self.sysfs / 'devices' / controller / entry.parent.name
        device.mkdir(parents=True, exist_ok=True)
        entry.parent.parent.mkdir(parents=True, exist_ok=True)
        entry.parent.symlink_to(device, target_is_directory=True)
        entry.write_text(value + '\n')

    def exercise(self, host=False, adb='active', router='active', error=None):
        events = []
        usb = Mock()
        usb.path = self.sysfs / 'kernel/debug/usb/4e00000.usb/mode'
        usb.status.return_value = dict(available=True, host=host, mode='host' if host else 'device')
        usb.gadget.state.return_value = dict(ActiveState=adb)
        usb.gadget.stop_monitor.side_effect = lambda: events.append('stop-monitor')
        usb.gadget.start_monitor.side_effect = lambda: events.append('start-monitor')

        def change(enabled):
            mode = 'host' if enabled else 'device'
            events.append(mode)
            if error:
                raise error
            usb.status.return_value.update(host=enabled, mode=mode)

        usb.set_host.side_effect = change

        def run(command, **_):
            events.append(command[1])
            return subprocess.CompletedProcess(command, 0, stdout='ActiveState=' + router + '\n')

        return module.StartupUSB(usb, runner=run, sysfs=self.sysfs), usb, events

    def test_router_closed_during_transition_and_restored_before_return(self):
        startup, usb, events = self.exercise()
        self.sysfs_value('bus/usb/devices/1-1.1:1.0/bInterfaceClass', '0e')
        self.assertEqual(startup.prepare(), 'Host (USB audio or video detected)')
        self.assertTrue(usb.status()['host'])
        self.assertEqual(events, ['show', 'stop-monitor', 'stop', 'host', 'start'])

    def test_audio_only_keeps_host_after_delayed_enumeration(self):
        startup, usb, events = self.exercise()
        audio = 'bus/usb/devices/1-1.2:1.0/bInterfaceClass'

        def enumerate_audio(delay):
            self.advance(delay)
            if usb.status()['host'] and self.now >= 3 and not (self.sysfs / audio).exists():
                self.sysfs_value(audio, '01')

        with patch.object(module.time, 'sleep', enumerate_audio):
            self.assertEqual(startup.prepare(), 'Host (USB audio or video detected)')
        self.assertLess(self.now, module.MEDIA_WAIT_S)
        self.assertEqual(events, ['show', 'stop-monitor', 'stop', 'host', 'start'])

    def test_empty_hub_and_media_on_other_port_fall_back_to_device(self):
        startup, _, events = self.exercise()
        self.sysfs_value('bus/usb/devices/1-1:1.0/bInterfaceClass', '09')
        self.sysfs_value('bus/usb/devices/2-1:1.0/bInterfaceClass', '0e', controller='other.usb')
        self.assertEqual(startup.prepare(), 'Device (no USB audio or video detected)')
        self.assertAlmostEqual(self.now, module.MEDIA_WAIT_S)
        self.assertEqual(events, ['show', 'stop-monitor', 'stop', 'host', 'device', 'start'])

    def test_already_host_quiet_adb_never_restarts_router(self):
        startup, usb, events = self.exercise(host=True, adb='failed')
        self.sysfs_value('bus/usb/devices/1-1.1:1.0/bInterfaceClass', '0e')
        startup.prepare()
        usb.set_host.assert_not_called()
        self.assertEqual(events, ['stop-monitor'])

    def test_already_host_without_media_switches_directly_to_device(self):
        startup, _, events = self.exercise(host=True, adb='failed')
        self.assertEqual(startup.prepare(), 'Device (no USB audio or video detected)')
        self.assertEqual(events, ['show', 'stop-monitor', 'stop', 'device', 'start'])

    def test_already_host_active_adb_is_quiesced_with_router_closed(self):
        startup, _, events = self.exercise(host=True)
        self.sysfs_value('bus/usb/devices/1-1.2:1.0/bInterfaceClass', '01')
        startup.prepare()
        self.assertEqual(events, ['show', 'stop-monitor', 'stop', 'host', 'start'])

    def test_failed_transition_restores_previously_running_router(self):
        startup, _, events = self.exercise(error=ValueError('Device mode retained'))
        with self.assertRaisesRegex(ValueError, 'Device mode retained'):
            startup.prepare()
        self.assertEqual(events, ['show', 'stop-monitor', 'stop', 'host', 'start', 'start-monitor'])

    def test_inactive_router_is_not_started_and_missing_usb_has_no_side_effect(self):
        startup, usb, events = self.exercise(router='inactive')
        startup.prepare()
        self.assertEqual(events, ['show', 'stop-monitor', 'host', 'device'])
        events.clear()
        usb.status.return_value = dict(available=False, host=False)
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            startup.prepare()
        self.assertEqual(events, [])

    def test_failed_service_inspection_leaves_usb_and_router_untouched(self):
        startup, usb, events = self.exercise()
        startup.run = Mock(side_effect=subprocess.TimeoutExpired('systemctl show', 2))
        with self.assertRaisesRegex(ValueError, 'USB startup preparation failed'):
            startup.prepare()
        usb.set_host.assert_not_called()
        self.assertEqual(events, [])

    def test_app_verification_never_changes_usb_router_or_service_state(self):
        startup, usb, events = self.exercise(host=True, adb='failed')
        startup.verify()
        self.assertEqual(events, [])
        usb.status.return_value = dict(available=False, host=False, mode='unavailable')
        with self.assertRaisesRegex(ValueError, 'Stop the App'):
            startup.verify()
        self.assertEqual(events, [])
        usb.status.return_value = dict(available=True, host=True, mode='host')
        usb.gadget.state.return_value = dict(ActiveState='active')
        with self.assertRaisesRegex(ValueError, 'not ready'):
            startup.verify()
        self.assertEqual(events, [])

    def test_app_can_restart_in_device_mode_with_adb_active(self):
        startup, usb, events = self.exercise()
        startup.verify()
        usb.gadget.state.assert_not_called()
        usb.set_host.assert_not_called()
        self.assertEqual(events, [])


if __name__ == '__main__':
    unittest.main()
