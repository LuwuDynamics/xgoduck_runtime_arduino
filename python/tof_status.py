"""On-demand, read-only ToF snapshots, isolated from the motion Bridge client."""
import copy
import importlib.util
import os
from pathlib import Path
import socket
import threading
import time
from urllib.parse import urlparse


def _decoder():
    path = Path(__file__).resolve().parents[1] / 'tools/read-tof.py'
    spec = importlib.util.spec_from_file_location('duck_tof_reader', path)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader.decode


def _client():
    try:
        from arduino.router_bridge import Bridge, DEFAULT_ADDRESS
    except ModuleNotFoundError as exc:
        if exc.name not in ('arduino', 'arduino.router_bridge'):
            raise
        return _LegacyToFClient(os.environ.get('APP_SOCKET', 'unix:///var/run/arduino-router.sock'))
    return Bridge(os.environ.get('APP_SOCKET', DEFAULT_ADDRESS))


class _LegacyToFClient:
    """One outstanding read on a socket separate from the legacy motion singleton."""

    def __init__(self, address):
        import msgpack  # Also used by the legacy App SDK.
        self._msgpack = msgpack
        self._unpacker = msgpack.Unpacker()
        self._address = urlparse(address)
        self._socket = None
        self._request_id = 0

    def connect(self, timeout):
        if self._address.scheme == 'unix':
            self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._socket.settimeout(timeout)
            self._socket.connect(self._address.path)
        elif self._address.scheme == 'tcp':
            self._socket = socket.create_connection((self._address.hostname, self._address.port), timeout)
        else:
            raise ValueError('Unsupported Arduino Router socket address.')
        return True

    def call(self, method, *, timeout):
        self._request_id += 1
        deadline = time.monotonic() + timeout
        self._socket.settimeout(timeout)
        self._socket.sendall(self._msgpack.packb([0, self._request_id, method, []]))
        while (remaining := deadline - time.monotonic()) > 0:
            self._socket.settimeout(remaining)
            chunk = self._socket.recv(4096)
            if not chunk:
                raise ConnectionError('Arduino Router closed the ToF connection.')
            self._unpacker.feed(chunk)
            for response in self._unpacker:
                if isinstance(response, list) and response[:2] == [1, self._request_id] and len(response) == 4:
                    _, _, error, result = response
                    if error is not None:
                        raise RuntimeError(f'Router {method} failed: {error}')
                    return result
        raise TimeoutError(f'Router {method} timed out.')

    def disconnect(self):
        if self._socket is not None:
            self._socket.close()
            self._socket = None


def _empty(state='starting', message=''):
    return dict(state=state, error_message=message, fresh=False, seq=0,
                resolution=[8, 8], frequency_hz=5, age_ms=None, valid_zones=0,
                center_mm=None, min_mm=None, max_mm=None,
                grid_mm=[[None] * 8 for _ in range(8)])


class ToFMonitor:
    """HTTP reads a shared cache; only one worker ever calls the sensor RPC.

    A viewer renews a short lease. After the page closes, sampling and its
    private connection stop. An absent sensor or missing RPC is not an App error.
    """
    def __init__(self, client_factory=_client, *, interval=.2, lease=15, retry=5):
        self._factory = client_factory
        self._interval, self._lease, self._retry = interval, lease, retry
        self._lock = threading.Lock()
        self._wake, self._stop = threading.Event(), threading.Event()
        self._thread = None
        self._until = 0
        self._data, self._received = _empty(), 0

    def status(self):
        now = time.monotonic()
        with self._lock:
            if not self._stop.is_set():
                self._until = now + self._lease
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name='tof-view5', daemon=True)
                    self._thread.start()
                self._wake.set()
            data, received = copy.deepcopy(self._data), self._received
        if data.get('age_ms') is not None and received:
            data['age_ms'] += max(0, int((now - received) * 1000))
        if data.get('fresh') and data['age_ms'] > 1000:
            data.update(fresh=False, valid_zones=0, center_mm=None,
                        min_mm=None, max_mm=None, grid_mm=[[None] * 8 for _ in range(8)])
            data['error_message'] = 'Distance frame is stale; do not use it as a current measurement.'
        return data

    def _publish(self, data):
        with self._lock:
            self._data, self._received = data, time.monotonic()

    def _run(self):
        client, decode = None, None

        def disconnect():
            nonlocal client
            if client is not None:
                try:
                    client.disconnect()
                except Exception:
                    pass
                client = None

        try:
            while not self._stop.is_set():
                with self._lock:
                    active = time.monotonic() < self._until
                    if not active:
                        self._wake.clear()
                if not active:
                    disconnect()
                    self._wake.wait(.5)
                    continue
                started = time.monotonic()
                try:
                    if decode is None:
                        decode = _decoder()
                    if client is None:
                        client = self._factory()
                        if not client.connect(timeout=1):
                            raise ConnectionError('Could not connect to the Arduino Router.')
                    data = decode(client.call('duck_tof', timeout=.5))
                    self._publish(data)
                except Exception as exc:
                    self._publish(_empty('unavailable', str(exc)[:500] or type(exc).__name__))
                    disconnect()
                    self._stop.wait(self._retry)
                    continue
                self._stop.wait(max(.01, self._interval - (time.monotonic() - started)))
        finally:
            disconnect()

    def close(self):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
