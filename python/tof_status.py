"""On-demand, read-only ToF snapshots, isolated from the motion Bridge client."""
import copy
import importlib.util
import os
from pathlib import Path
import threading
import time


def _decoder():
    path = Path(__file__).resolve().parents[1] / 'tools/read-tof.py'
    spec = importlib.util.spec_from_file_location('duck_tof_reader', path)
    reader = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reader)
    return reader.decode


def _client():
    # Never use app_utils.Bridge or the CLI's legacy cleanup here: that client
    # belongs to the motion loop. This connection owns no provided methods.
    from arduino.router_bridge import Bridge, DEFAULT_ADDRESS
    return Bridge(os.environ.get('APP_SOCKET', DEFAULT_ADDRESS))


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
