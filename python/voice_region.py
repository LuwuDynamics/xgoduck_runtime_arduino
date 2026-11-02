"""Same-origin, explicit server selection; all writes run in the voice process."""
import asyncio
import json
from urllib.parse import urlsplit
from fastapi import Request
from fastapi.responses import JSONResponse


def register_voice_region(ui, voice, config):
    def read():
        try:
            return config.region_status()
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=409)

    async def write(request: Request):
        origin = request.headers.get('origin')
        if request.headers.get('x-device-test') != '1' or (
                origin and urlsplit(origin).netloc != request.headers.get('host')):
            return JSONResponse({'error': 'Request origin mismatch.'}, status_code=403)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024:
                return JSONResponse({'error': 'Request body too large.'}, status_code=413)
        try:
            body = json.loads(raw)
            if (not isinstance(body, dict) or set(body) != {'cloud_region'}
                    or body['cloud_region'] not in ('cn', 'global')):
                raise ValueError('Select cn or global.')
        except (ValueError, TypeError, UnicodeDecodeError):
            return JSONResponse({'error': 'Select cn or global.'}, status_code=400)
        try:
            return await asyncio.to_thread(voice.call, '/region', body)
        except ValueError as exc:
            return JSONResponse({'error': str(exc)}, status_code=400)
        except OSError:
            return JSONResponse({'error': 'Voice service unavailable. Please retry.'}, status_code=503)

    ui.expose_api('GET', '/api/voice/region', read)
    ui.expose_api('POST', '/api/voice/region', write)
