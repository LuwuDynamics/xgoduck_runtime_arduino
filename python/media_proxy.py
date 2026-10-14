"""Expose the host's isolated USB media worker through the firmware WebUI."""
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import socket
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

READ_PATHS = {'status', 'camera/frame', 'camera/stream', 'audio/stream', 'recording', 'tts/audio'}
WRITE_PATHS = {'camera/start', 'camera/stop', 'audio/play', 'audio/stop',
               'audio/record', 'audio/volume', 'upload', 'tts/speak'}
MAX_BODY = 12 * 1024 * 1024


def media_url():
    override = os.environ.get('DUCK_MEDIA_URL')
    if override:
        return override.rstrip('/')
    # Docker bridge gateway is the board itself; no dependence on its Wi-Fi IP.
    if Path('/.dockerenv').exists():
        for line in Path('/proc/net/route').read_text().splitlines()[1:]:
            fields = line.split()
            if fields[1] == '00000000' and int(fields[3], 16) & 2:
                gateway = socket.inet_ntoa(int(fields[2], 16).to_bytes(4, 'little'))
                return f'http://{gateway}:9528'
        raise RuntimeError('No Docker host gateway; set DUCK_MEDIA_URL.')
    return 'http://127.0.0.1:9528'


def register_media(ui, client=None):
    upstream = media_url()
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(15, connect=3),
        limits=httpx.Limits(max_connections=12, max_keepalive_connections=6), trust_env=False)
    streams = asyncio.Semaphore(4)
    original_lifespan = ui.app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(app):
        async with original_lifespan(app) as state:
            try:
                yield state
            finally:
                await client.aclose()
    ui.app.router.lifespan_context = lifespan

    def error(message, status=503):
        return JSONResponse({'error': message}, status_code=status)

    async def forward(method, path, body=b'', mime='application/json'):
        try:
            res = await client.request(method, f'{upstream}/api/{path}', content=body,
                                       headers={'Content-Type': mime, 'X-Device-Test': '1'})
            return Response(res.content, status_code=res.status_code,
                headers={'Content-Type': res.headers.get('Content-Type', 'application/json'),
                         'Cache-Control': 'no-store'})
        except httpx.HTTPError:
            return error('Media service unavailable. Check the duck-device-test service.')

    async def read(path: str, request: Request):
        if path not in READ_PATHS:
            return error('Not found', 404)
        if path == 'audio/stream':
            origin = request.headers.get('origin')
            if request.headers.get('x-device-test') != '1' or (
                    origin and urlsplit(origin).netloc != request.headers.get('host')):
                return error('Request origin mismatch. Use the Duck page.', 403)
        if path not in {'camera/stream', 'audio/stream'}:
            return await forward('GET', path)
        if streams.locked():
            return error('Too many media streams. Close another Duck page.', 429)
        await streams.acquire()
        try:
            res = await client.send(client.build_request('GET', f'{upstream}/api/{path}',
                headers={'X-Device-Test': '1'}), stream=True)
            if res.status_code != 200:
                data = await res.aread()
                await res.aclose()
                streams.release()
                return Response(data, status_code=res.status_code, media_type='application/json')
        except httpx.HTTPError:
            streams.release()
            return error('Media stream unavailable.')
        except asyncio.CancelledError:
            streams.release()
            raise

        async def frames():
            try:
                async for chunk in res.aiter_raw():
                    yield chunk
            except httpx.HTTPError:
                pass
            finally:
                await res.aclose()
                streams.release()
        return StreamingResponse(frames(), headers={
            'Content-Type': res.headers['Content-Type'], 'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff', 'X-Accel-Buffering': 'no'})

    async def write(path: str, request: Request):
        if path not in WRITE_PATHS:
            return error('Not found', 404)
        origin = request.headers.get('origin')
        if request.headers.get('x-device-test') != '1' or (
                origin and urlsplit(origin).netloc != request.headers.get('host')):
            return error('Request origin mismatch. Use the Duck page.', 403)
        limit = MAX_BODY if path == 'upload' else 8192
        data = bytearray()
        async for chunk in request.stream():
            data.extend(chunk)
            if len(data) > limit:
                return error('Request body too large.', 413)
        return await forward('POST', path, bytes(data), request.headers.get('content-type', 'application/json'))

    ui.expose_api('GET', '/api/media/{path:path}', read)
    ui.expose_api('POST', '/api/media/{path:path}', write)
