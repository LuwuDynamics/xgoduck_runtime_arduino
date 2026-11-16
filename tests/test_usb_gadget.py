import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('usb_role_gadget_test', ROOT / 'tools/gamepad/usb_role.py')
usb_role = importlib.util.module_from_spec(spec)
spec.loader.exec_module(usb_role)


class USBGadgetTest(unittest.TestCase):
    def test_host_stops_gadget_before_role_write_and_device_restores_after_write(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mode'
            path.write_text('device\n')
            gadget = Mock()
            gadget.stop_for_host.side_effect = lambda: self.assertEqual(path.read_text(), 'device\n')
            gadget.start_for_device.side_effect = lambda: self.assertEqual(path.read_text(), 'device\n')
            role = usb_role.USBRole(path, gadget)
            gadget.stop_for_host.assert_not_called()  # Construction is read-only.
            role.set_host(True)
            self.assertEqual(path.read_text(), 'host\n')
            role.set_host(False)
            gadget.stop_for_host.assert_called_once()
            gadget.start_for_device.assert_called_once()

    def test_already_host_still_stops_a_restarted_adbd(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mode'
            path.write_text('host\n')
            gadget = Mock()
            usb_role.USBRole(path, gadget).set_host(True)
            gadget.stop_for_host.assert_called_once()
            gadget.start_for_device.assert_not_called()
            self.assertEqual(path.read_text(), 'host\n')

    def test_failed_gadget_stop_leaves_device_and_attempts_adb_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'mode'
            path.write_text('device\n')
            gadget = Mock()
            gadget.stop_for_host.side_effect = subprocess.TimeoutExpired('systemctl stop', 3)
            with self.assertRaisesRegex(ValueError, 'Unable to switch'):
                usb_role.USBRole(path, gadget).set_host(True)
            self.assertEqual(path.read_text(), 'device\n')
            gadget.start_for_device.assert_called_once()

    def test_unavailable_node_does_not_stop_adb(self):
        with tempfile.TemporaryDirectory() as directory:
            gadget = Mock()
            with self.assertRaisesRegex(ValueError, 'unavailable'):
                usb_role.USBRole(Path(directory) / 'missing', gadget).set_host(True)
            gadget.stop_for_host.assert_not_called()

    def test_service_commands_preserve_enabled_state_and_respect_disabled_adb(self):
        def exercise(unit_state, active):
            calls = []
            def run(command, **_):
                calls.append(command)
                output = f'LoadState=loaded\nActiveState={active}\nUnitFileState={unit_state}\n'
                return subprocess.CompletedProcess(command, 0, stdout=output)
            gadget = usb_role.ADBGadget(run, systemd=True)
            gadget.stop_for_host()
            gadget.start_for_device()
            return calls
        for state, active in [('enabled', 'active'), ('enabled', 'failed'), ('disabled', 'active')]:
            calls = exercise(state, active)
            self.assertIn(['systemctl', 'stop', 'adbd.service'], calls)
            self.assertIn(['systemctl', 'start', 'adbd.service'], calls)
            self.assertFalse(any('disable' in call or 'mask' in call for call in calls))
        calls = exercise('disabled', 'inactive')
        self.assertIn(['systemctl', 'stop', 'adbd.service'], calls)
        self.assertNotIn(['systemctl', 'start', 'adbd.service'], calls)

    def test_gadget_unbinds_before_functionfs_service_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            udc = Path(directory) / 'UDC'
            udc.write_text('4e00000.usb\n')
            def run(command, **_):
                if command[1:3] == ['stop', 'adbd.service']:
                    self.assertEqual(udc.read_text().strip(), '')
                return subprocess.CompletedProcess(command, 0,
                    stdout='LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n')
            gadget = usb_role.ADBGadget(run, systemd=True, udc=udc)
            gadget.stop_for_host()
            self.assertEqual(udc.read_text().strip(), '')
            gadget.start_for_device()
            self.assertEqual(udc.read_text().strip(), '4e00000.usb')

    def test_failed_stop_restores_live_daemon_gadget_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            udc, role = Path(directory) / 'UDC', Path(directory) / 'mode'
            udc.write_text('4e00000.usb\n')
            role.write_text('device\n')
            def run(command, **_):
                if command[1:3] == ['stop', 'adbd.service']:
                    raise subprocess.CalledProcessError(1, command)
                return subprocess.CompletedProcess(command, 0,
                    stdout='LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n')
            gadget = usb_role.ADBGadget(run, systemd=True, udc=udc)
            with self.assertRaisesRegex(ValueError, 'Unable to switch'):
                usb_role.USBRole(role, gadget).set_host(True)
            self.assertEqual(role.read_text().strip(), 'device')
            self.assertEqual(udc.read_text().strip(), '4e00000.usb')

    def test_host_stops_serial_monitor_before_adb_and_device_restores_it(self):
        calls = []
        def run(command, **_):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0,
                stdout='LoadState=loaded\nActiveState=active\nUnitFileState=enabled\n')
        gadget = usb_role.ADBGadget(run, systemd=True, udc=Path('/nonexistent/UDC'))
        gadget.stop_for_host()
        self.assertLess(calls.index(['systemctl', 'stop', 'arduino-router-serial.path']),
                        calls.index(['systemctl', 'stop', 'arduino-router-serial.service']))
        self.assertLess(calls.index(['systemctl', 'stop', 'arduino-router-serial.service']),
                        calls.index(['systemctl', 'stop', 'adbd.service']))
        gadget.start_for_device()
        self.assertLess(calls.index(['systemctl', 'start', 'adbd.service']),
                        calls.index(['systemctl', 'start', 'arduino-router-serial.service']))
        self.assertLess(calls.index(['systemctl', 'start', 'adbd.service']),
                        calls.index(['systemctl', 'start', 'arduino-router-serial.path']))


if __name__ == '__main__':
    unittest.main()
