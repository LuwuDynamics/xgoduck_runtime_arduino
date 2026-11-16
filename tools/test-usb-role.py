"""USB role switching without touching hardware."""
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parent/'gamepad'))
from usb_role import USBRole

class USBRoleTest(unittest.TestCase):
    def test_read_only_startup_and_explicit_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            mode=Path(folder)/'mode';mode.write_text('device\n')
            usb=USBRole(mode)
            self.assertFalse(usb.status()['host'])
            self.assertEqual(mode.read_text(),'device\n')
            usb.set_host(True);self.assertEqual(mode.read_text(),'host\n')
            self.assertTrue(usb.status()['host'])
            # Starting/restarting the worker never switches a running role.
            USBRole(mode);self.assertEqual(mode.read_text(),'host\n')
            usb.set_host(False);self.assertEqual(mode.read_text(),'device\n')
            for invalid in (None,1,'host','device','true',{}):
                with self.assertRaises(ValueError):usb.set_host(invalid)
                self.assertEqual(mode.read_text(),'device\n')
    def test_missing_node(self):
        with tempfile.TemporaryDirectory() as folder:
            usb=USBRole(Path(folder)/'missing'/'mode')
            self.assertFalse(usb.status()['available'])
            with self.assertRaises(ValueError):usb.set_host(True)

if __name__=='__main__':unittest.main()
