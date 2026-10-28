"""Real SDK/MCP and loopback HTTP, using a synthetic image and token fixture."""
import io
import json
import os
from pathlib import Path
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'python'), str(ROOT / 'python/vendor/luwu_os/apps/voice_chat')]
from PIL import Image
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
