import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('usb_prepare_test', ROOT / 'tools/ai-support/usb_prepare.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class USBStartupTest(unittest.TestCase):
    def exercise(self, host=False, adb='active', router='active', error=None):
        events = []
        usb = Mock()
        usb.status.return_value = dict(available=True, host=host)
        usb.gadget.state.return_value = dict(ActiveState=adb)
        usb.gadget.stop_monitor.side_effect = lambda: events.append('stop-monitor')
        def change(_):
            events.append('host')
            if error:
                raise error
        usb.set_host.side_effect = change
        def run(command, **_):
            events.append(command[1])
            return subprocess.CompletedProcess(command, 0, stdout='ActiveState=' + router + '\n')
        return module.StartupUSB(usb, runner=run), usb, events

    def test_router_closed_during_transition_and_restored_before_return(self):
        startup, _, events = self.exercise()
        startup.prepare()
        self.assertEqual(events, ['show', 'stop-monitor', 'stop', 'host', 'start'])

    def test_already_host_quiet_adb_never_restarts_router(self):
        startup, usb, events = self.exercise(host=True, adb='failed')
        startup.prepare()
        usb.set_host.assert_not_called()
        self.assertEqual(events, ['stop-monitor'])

    def test_already_host_active_adb_is_quiesced_with_router_closed(self):
        startup, _, events = self.exercise(host=True)
        startup.prepare()
        self.assertEqual(events, ['show', 'stop-monitor', 'stop', 'host', 'start'])

    def test_failed_transition_restores_previously_running_router(self):
        startup, _, events = self.exercise(error=ValueError('Device mode retained'))
        with self.assertRaisesRegex(ValueError, 'Device mode retained'):
            startup.prepare()
        self.assertEqual(events, ['show', 'stop-monitor', 'stop', 'host', 'start'])

    def test_inactive_router_is_not_started_and_missing_usb_has_no_side_effect(self):
        startup, usb, events = self.exercise(router='inactive')
        startup.prepare()
        self.assertEqual(events, ['show', 'stop-monitor', 'host'])
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
        usb.status.return_value = dict(available=True, host=False)
        with self.assertRaisesRegex(ValueError, 'Stop the App'):
            startup.verify()
        self.assertEqual(events, [])
        usb.status.return_value = dict(available=True, host=True)
        usb.gadget.state.return_value = dict(ActiveState='active')
        with self.assertRaisesRegex(ValueError, 'not prepared'):
            startup.verify()
        self.assertEqual(events, [])


if __name__ == '__main__':
    unittest.main()
