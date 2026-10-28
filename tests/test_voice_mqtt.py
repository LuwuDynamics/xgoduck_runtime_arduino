import asyncio
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python/vendor/luwu_os/apps/voice_chat'))
from mqtt_udp_protocol import MqttUdpProtocol


class MQTTLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.loop = Mock()
        self.protocol = MqttUdpProtocol({'endpoint': 'local-test.invalid', 'tls': False}, self.loop)

    def test_closed_protocol_never_starts_a_new_client(self):
        self.protocol.force_stop_mqtt()
        with patch('mqtt_udp_protocol.mqtt.Client') as factory:
            self.assertFalse(self.protocol._start_mqtt_client())
        factory.assert_not_called()

    def test_stale_callbacks_cannot_change_current_connection_or_deliver_commands(self):
        old, current = Mock(), Mock()
        self.protocol._mqtt = current
        self.protocol._mqtt_connected = True
        self.protocol._on_mqtt_disconnect(old, None, 0)
        self.assertTrue(self.protocol._mqtt_connected)
        self.protocol._mqtt_connected = False
        self.protocol._on_mqtt_connect(old, None, None, 0)
        self.assertFalse(self.protocol._mqtt_connected)
        self.protocol.on_incoming_json = Mock()
        self.protocol._on_mqtt_message(old, None, Mock(payload=b'{"type":"mcp","payload":{}}'))
        self.loop.call_soon_threadsafe.assert_not_called()

    def test_client_created_during_shutdown_never_connects(self):
        entered, release = threading.Event(), threading.Event()
        client = Mock()
        def construct(*args, **kwargs):
            entered.set()
            release.wait(3)
            return client
        with patch('mqtt_udp_protocol.mqtt.Client', side_effect=construct):
            thread = threading.Thread(target=self.protocol._start_mqtt_client)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.protocol.force_stop_mqtt()
            release.set()
            thread.join(2)
        self.assertFalse(thread.is_alive())
        client.connect_async.assert_not_called()
        client.loop_start.assert_not_called()


class MQTTAsyncLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_cancels_pending_reconnect(self):
        protocol = MqttUdpProtocol({'endpoint': 'local-test.invalid'}, asyncio.get_running_loop())
        protocol._schedule_reconnect()
        task = protocol._reconnect_task
        await protocol.shutdown()
        await asyncio.sleep(0)
        self.assertTrue(task.done())
        self.assertTrue(protocol._closing)


if __name__ == '__main__':
    unittest.main()
