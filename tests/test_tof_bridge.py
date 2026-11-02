"""Read-only ToF CLI lifecycle with both generations of the Arduino SDK."""
import importlib.util
import io
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tof_sdk_reader', ROOT / 'tools/read-tof.py')
reader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)


class TofBridgeTest(unittest.TestCase):
    def modern(self):
        client = Mock()
        client.connect.return_value = True
        router = SimpleNamespace(Bridge=Mock(return_value=client), DEFAULT_ADDRESS='default-test-socket')
        return client, router

    def test_current_sdk_uses_public_client_and_closes_only_its_connection(self):
        client, router = self.modern()
        with patch.object(reader, 'import_module', return_value=router), patch.dict(os.environ, APP_SOCKET='app-test-socket'):
            with reader.open_bridge() as actual:
                self.assertIs(actual, client)
                actual.call('duck_tof', timeout=3)
        router.Bridge.assert_called_once_with('app-test-socket')
        client.connect.assert_called_once_with(timeout=5)
        client.call.assert_called_once_with('duck_tof', timeout=3)
        client.disconnect.assert_called_once_with()
        client.notify.assert_not_called()
        client.provide.assert_not_called()

    def test_failed_connection_and_failed_read_both_close_client(self):
        for failure in ['connect', 'read']:
            with self.subTest(failure=failure):
                client, router = self.modern()
                client.connect.return_value = failure != 'connect'
                with patch.object(reader, 'import_module', return_value=router):
                    with self.assertRaises((ConnectionError, TimeoutError)):
                        with reader.open_bridge():
                            raise TimeoutError('Read timed out')
                client.disconnect.assert_called_once_with()

    def test_legacy_sdk_retains_its_original_cleanup(self):
        bridge, connection = Mock(), Mock()
        def load(name):
            if name == 'arduino.router_bridge':
                raise ModuleNotFoundError(name=name)
            if name == 'arduino.app_utils':return SimpleNamespace(Bridge=bridge)
            if name == 'arduino.app_utils.bridge':return SimpleNamespace(ClientServer=Mock(return_value=connection))
            raise AssertionError(name)
        with patch.object(reader, 'import_module', side_effect=load):
            with reader.open_bridge() as actual:
                self.assertIs(actual, bridge)
        connection.stop.assert_called_once_with()

    def test_missing_dependency_in_current_sdk_is_not_misreported_as_legacy(self):
        with patch.object(reader, 'import_module', side_effect=ModuleNotFoundError(name='msgpack')):
            with self.assertRaises(ModuleNotFoundError):
                with reader.open_bridge():pass

    def test_absent_sensor_exit_still_closes_current_sdk_connection(self):
        client, router = self.modern()
        client.call.return_value = reader.PACKET.pack(b'DTF1', 1,8,8,1,0x29,0,0,0,
            0,0,0xffffffff,0,0,0,*([0]*64),*([0]*64),*([0]*64))
        with patch.object(reader, 'import_module', return_value=router), patch('sys.argv', ['read-tof.py', '--json']), patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(reader.main(), 1)
        client.call.assert_called_once_with('duck_tof', timeout=3)
        client.disconnect.assert_called_once_with()


if __name__ == '__main__':unittest.main()
