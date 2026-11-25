"""Real SDK/MCP and loopback HTTP, using a synthetic image and token fixture."""
import asyncio
import io
import json
import os
from pathlib import Path
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'python/vendor/luwu_os/apps/voice_chat')]
from PIL import Image
from voice_application import application_type
from voice_media import vision_type
from iot.thing import ThingManager
from mcp_server import McpServer
from ota import get_device_id

MAC = '14:B5:CD:F1:0B:C1'


class VisionIdentityTest(unittest.TestCase):
    def setUp(self):
        self.requests = []
        outer = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                outer.requests.append((dict(self.headers), body))
                # The deployed vision endpoint compares the verified token's
                # device subject and Device-Id with exact string equality.
                ok = (self.headers.get('Authorization') == 'Bearer test-vision-token'
                      and self.headers.get('Device-Id') == MAC.lower())
                reply = dict(success=ok, response='Synthetic image recognized.' if ok else '',
                             message='' if ok else '设备ID与token不匹配')
                raw = json.dumps(reply).encode()
                self.send_response(200 if ok else 403)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        self.url = 'http://127.0.0.1:%s/vision' % server.server_port
        image = Image.new('RGB', (2, 2), (10, 30, 50))
        buffer = io.BytesIO()
        image.save(buffer, format='JPEG')
        self.jpeg = buffer.getvalue()

    def call_photo(self, device_id, token='test-vision-token'):
        vision = vision_type()(device_id, 'original-client-id', Mock())
        vision.set_explain(self.url, token)
        provider = SimpleNamespace(mcp_take_photo=lambda question: vision.explain(question, self.jpeg))
        mcp = McpServer(ThingManager(), self_provider=provider)
        with patch.dict(os.environ, {'NO_PROXY': '127.0.0.1,localhost'}):
            reply = mcp.handle_message({'id': 12, 'method': 'tools/call', 'params':
                {'name': 'self.camera.take_photo', 'arguments': {'question': 'What is ahead?'}}})
        return reply

    def test_photo_uses_ota_case_without_changing_saved_identity_client_or_token(self):
        for device_id in (MAC, MAC.lower()):
            with self.subTest(device_id=device_id), patch.dict(os.environ, {'DUCK_DEVICE_ID': MAC}):
                reply = self.call_photo(device_id)
                self.assertNotIn('error', reply)
                self.assertFalse(reply['result']['isError'])
                self.assertEqual(reply['result']['content'][0]['text'], 'Synthetic image recognized.')
                headers, body = self.requests[-1]
                self.assertEqual(headers['Device-Id'], MAC.lower())
                self.assertEqual(headers['Client-Id'], 'original-client-id')
                self.assertEqual(headers['Authorization'], 'Bearer test-vision-token')
                self.assertIn(self.jpeg, body)
                self.assertEqual(get_device_id(), MAC)

    def test_invalid_vision_token_still_returns_an_actual_mcp_error(self):
        reply = self.call_photo(MAC, 'wrong-test-token')
        self.assertEqual(reply['error']['code'], -32603)
        self.assertIn('设备ID与token不匹配', reply['error']['message'])

    def test_pending_photo_cannot_use_a_frame_from_a_reopened_camera(self):
        started = threading.Event()
        bench = Mock(frame_ready=threading.Condition(), frames=0, jpeg=None)
        bench.camera.poll.return_value = None
        bench.camera_start.side_effect = started.set
        vision = vision_type()(MAC, 'client', bench)
        with ThreadPoolExecutor() as pool:
            pending = pool.submit(vision.capture)
            self.assertTrue(started.wait(2))
            with bench.frame_ready:
                bench.camera = Mock()
                bench.camera.poll.return_value = None
                bench.frames, bench.jpeg = 1, self.jpeg
                bench.frame_ready.notify_all()
            with self.assertRaisesRegex(ValueError, 'stopped'):
                pending.result(2)
            started.clear()
            next_photo = pool.submit(vision.capture)
            self.assertTrue(started.wait(2))
            with bench.frame_ready:
                bench.frames += 1
                bench.frame_ready.notify_all()
            jpeg, image = next_photo.result(2)
            self.assertEqual(jpeg, self.jpeg)
            self.assertEqual(image.size, (2, 2))


class CameraControlMcpTest(unittest.TestCase):
    def setUp(self):
        self.bench = Mock()
        self.bench.set_camera_light.side_effect = lambda enabled: {'enabled': enabled}
        self.bench.camera_stop.return_value = {'running': False}
        adapter = application_type(object, SimpleNamespace(bench=self.bench))
        self.app = object.__new__(adapter)
        self.app.mcp_camera_available = Mock(return_value=True)
        self.mcp = McpServer(ThingManager(), self_provider=self.app)

    def call(self, name, args):
        async def dispatch():
            return await self.mcp.handle_message_async(
                {'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': args}},
                asyncio.get_running_loop().run_in_executor)
        return asyncio.run(dispatch())

    def test_public_camera_controls_dispatch_to_shared_media_bench(self):
        tools = self.mcp.handle_message({'id': 1, 'method': 'tools/list'})['result']['tools']
        public = {tool['name']: tool['inputSchema'] for tool in tools}
        self.assertEqual(public['self.camera.set_light']['properties']['enabled']['type'], 'boolean')
        self.assertEqual(public['self.camera.set_light']['required'], ['enabled'])
        self.assertEqual(public['self.camera.close']['required'], [])
        for enabled in (True, False):
            reply = self.call('self.camera.set_light', {'enabled': enabled})
            self.assertEqual(json.loads(reply['result']['content'][0]['text']), {'enabled': enabled})
            self.bench.set_camera_light.assert_called_with(enabled)
        reply = self.call('self.camera.close', {})
        self.assertEqual(json.loads(reply['result']['content'][0]['text']), {'running': False})
        self.bench.camera_stop.assert_called_once_with()
        self.bench.capture.assert_not_called()

    def test_unavailable_camera_and_unconfirmed_light_are_mcp_errors(self):
        self.app.mcp_camera_available.return_value = False
        tools = self.mcp.handle_message({'id': 1, 'method': 'tools/list'})['result']['tools']
        self.assertNotIn('self.camera.set_light', {t['name'] for t in tools})
        self.assertIn('不可用', self.call('self.camera.set_light', {'enabled': True})['error']['message'])
        self.bench.set_camera_light.assert_not_called()
        self.app.mcp_camera_available.return_value = True
        self.bench.set_camera_light.side_effect = ValueError('摄像头未出图，无法打开补光灯。')
        self.assertIn('未出图', self.call('self.camera.set_light', {'enabled': True})['error']['message'])
