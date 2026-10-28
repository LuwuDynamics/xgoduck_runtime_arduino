"""Private, nonblocking datagrams for frequent jaw targets, without HTTP waits."""
import hmac
import json
import os
from pathlib import Path
import socket
import stat
import threading


class MouthSender:
    def __init__(self, path, token):
        self.path, self.token, self.socket = str(path), token, None

    def send(self, body):
        raw = json.dumps(dict(body, _token=self.token), allow_nan=False).encode()
        if len(raw) > 4096:
            raise ValueError('mouth datagram too large')
        if self.socket is None:
            self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
            self.socket.setblocking(False)
        self.socket.sendto(raw, self.path)
        return {'submitted': True}  # Receipt/execution is visible in motion state.

    def close(self):
        if self.socket is not None:
            self.socket.close()
            self.socket = None


class MouthReceiver:
    def __init__(self, path, token, dispatch):
        self.path, self.token, self.dispatch = Path(path), token, dispatch
        self.socket = None
        self.thread = None
        self.stop = threading.Event()
        self.accepted = self.rejected = 0

    def start(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists():
            if not stat.S_ISSOCK(self.path.lstat().st_mode):
                raise ValueError('mouth socket path is occupied by a regular file')
            self.path.unlink()
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.socket.bind(str(self.path))
        self.path.chmod(0o600)
        self.socket.settimeout(.1)
        def run():
            while not self.stop.is_set():
                try:
                    raw = self.socket.recv(4097)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    if len(raw) > 4096:
                        raise ValueError('mouth datagram too large')
                    body = json.loads(raw)
                    if not isinstance(body, dict):
                        raise ValueError('invalid mouth datagram')
                    token = body.pop('_token', '')
                    if not isinstance(token, str) or not hmac.compare_digest(token, self.token):
                        raise ValueError('mouth IPC authentication failed')
                    self.dispatch(**body)
                    self.accepted += 1
                except (ValueError, TypeError, KeyError, OSError):
                    self.rejected += 1
        self.thread = threading.Thread(target=run, name='tts-mouth-receiver', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=.3)
        if self.socket:
            self.socket.close()
        if self.path.exists() and stat.S_ISSOCK(self.path.lstat().st_mode):
            self.path.unlink()
