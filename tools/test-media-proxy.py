"""Run in the App container: PYTHONPATH=/app/python python /app/tools/test-media-proxy.py."""
import unittest
import httpx
from fastapi import FastAPI
from media_proxy import register_media


class UI:
    def __init__(self):
        self.app = FastAPI()

    def expose_api(self, method, path, fn):
        self.app.add_api_route(path, fn, methods=[method])


class ProxyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        async def upstream(req):
            self.requests.append(req)
            if req.url.path.endswith('/audio/stream'):
                return httpx.Response(200, headers={'Content-Type': 'application/octet-stream'},
                                      stream=httpx.ByteStream(b'\x00\x80\xff\x7f'))
            if req.url.path.endswith('/camera/stream'):
                return httpx.Response(200, headers={'Content-Type': 'multipart/x-mixed-replace; boundary=duckframe'},
                                      stream=httpx.ByteStream(b'--duckframe\r\nframe-data'))
            if req.url.path.endswith('/camera/frame'):
                return httpx.Response(503, json={'error': 'Not ready'})
            return httpx.Response(200, json={'audio': {'state': 'idle'}})
        self.upstream = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        self.ui = UI()
        register_media(self.ui, self.upstream)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.ui.app), base_url='http://duck:9527')

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.upstream.aclose()

    async def test_status_and_errors(self):
        response = await self.client.get('/api/media/status')
        self.assertEqual(response.json()['audio']['state'], 'idle')
        self.assertEqual(self.requests[-1].url.path, '/api/status')
        response = await self.client.get('/api/media/camera/frame')
        self.assertEqual(response.status_code, 503)

    async def test_stream_forwarded_without_json_encoding(self):
        response = await self.client.get('/api/media/camera/stream')
        self.assertEqual(response.content, b'--duckframe\r\nframe-data')
        self.assertIn('multipart/x-mixed-replace', response.headers['content-type'])

    async def test_only_allowlisted_paths(self):
        response = await self.client.get('/api/media/secrets')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(self.requests)

    async def test_live_audio_is_raw_and_requires_same_origin_header(self):
        for headers in ({}, {'X-Device-Test': '1', 'Origin': 'http://other'}):
            response = await self.client.get('/api/media/audio/stream', headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertFalse(self.requests)
        response = await self.client.get('/api/media/audio/stream',
            headers={'X-Device-Test': '1', 'Origin': 'http://duck:9527'})
        self.assertEqual(response.content, b'\x00\x80\xff\x7f')
        self.assertEqual(response.headers['content-type'], 'application/octet-stream')
        self.assertEqual(response.headers['x-accel-buffering'], 'no')
        self.assertEqual(self.requests[-1].headers['x-device-test'], '1')

    async def test_origin_and_custom_header(self):
        for headers in ({}, {'X-Device-Test': '1', 'Origin': 'http://other'}):
            response = await self.client.post('/api/media/audio/play', json={}, headers=headers)
            self.assertEqual(response.status_code, 403)
        self.assertFalse(self.requests)
        response = await self.client.post('/api/media/audio/play', json={'source': 'music'},
            headers={'X-Device-Test': '1', 'Origin': 'http://duck:9527'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.requests[-1].headers['x-device-test'], '1')

    async def test_volume_setting_forwarded(self):
        response = await self.client.post('/api/media/audio/volume', json={'volume': 75},
                                         headers={'X-Device-Test': '1'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.requests[-1].url.path, '/api/audio/volume')
        self.assertEqual(self.requests[-1].content, b'{"volume":75}')

    async def test_request_limit(self):
        response = await self.client.post('/api/media/tts/speak', content=b'x' * 8193,
                                         headers={'X-Device-Test': '1'})
        self.assertEqual(response.status_code, 413)
        self.assertFalse(self.requests)

    async def test_upload_bytes_preserved(self):
        data = b'RIFF\x00\x01test'
        response = await self.client.post('/api/media/upload', content=data,
                                         headers={'X-Device-Test': '1', 'Content-Type': 'audio/wav'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.requests[-1].content, data)


if __name__ == '__main__':
    unittest.main()
