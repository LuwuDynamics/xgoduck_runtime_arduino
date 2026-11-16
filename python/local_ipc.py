"""Loopback-only JSON IPC shared by the two app processes."""
import hmac
import json
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def request(port, token, path, body=None, timeout=1.0):
    req = urllib.request.Request(
        f'http://127.0.0.1:{port}{path}',
        data=None if body is None else json.dumps(body, allow_nan=False).encode(),
        headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    # Never route local robot IPC through environment proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        try:
            detail = json.load(exc).get('error', str(exc))
        except (ValueError, OSError):
            detail = str(exc)
        raise ValueError(detail) from exc


class JsonServer:
    def __init__(self, port, token, dispatch):
        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(2)

            def log_message(self, *_):
                pass

            def do_GET(self):
                self.handle_json(False)

            def do_POST(self):
                self.handle_json(True)

            def handle_json(self, read):
                if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                    self.reply(401, {'error': 'IPC authentication failed'})
                    return
                try:
                    size = int(self.headers.get('Content-Length', '0'))
                    if size < 0 or size > 16384:
                        raise ValueError('request too large')
                    body = json.loads(self.rfile.read(size)) if read and size else {}
                    if not isinstance(body, dict):
                        raise ValueError('expected a JSON object')
                    result = dispatch(self.command, self.path, body)
                    self.reply(200, result)
                except (ValueError, KeyError, TypeError) as exc:
                    self.reply(400, {'error': str(exc)})
                except Exception as exc:
                    self.reply(503, {'error': str(exc)})

            def reply(self, code, data):
                raw = json.dumps(data, allow_nan=False).encode()
                try:
                    self.send_response(code)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw)))
                    self.end_headers()
                    self.wfile.write(raw)
                except (ConnectionError, TimeoutError):
                    # A timed-out caller may already have closed its socket.
                    # Do not turn a failed reply into a second 503 reply.
                    self.close_connection = True

        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, name='local-ipc', daemon=True)

    def start(self):
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
