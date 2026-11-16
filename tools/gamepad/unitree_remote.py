"""Unitree FFE0 BLE input via the system BlueZ gatttool (no HID pairing)."""
import math
import os
import pty
import re
import select
import struct
import subprocess
import threading
import time
from input_reader import normalize

KEYS = ('rb', 'lb', 'start', 'back', 'rt', 'lt', 'f1', 'f3',
        'south', 'east', 'west', 'north', 'up', 'right', 'down', 'left')


def decode_packet(raw):
    if len(raw) != 20:
        raise ValueError('Expected a 20-byte Unitree input packet.')
    lx, rx, ry, ly, keys, battery, signal = struct.unpack('<ffffHBB', raw)
    if not all(math.isfinite(x) and abs(x) <= 1.05 for x in (lx, ly, rx, ry)):
        raise ValueError('Invalid Unitree stick values.')
    # Unitree Y axes are positive forward/up; evdev Y axes are positive down.
    axes = {k: normalize(v, -1, 1) for k, v in dict(lx=lx, ly=-ly, rx=rx, ry=-ry).items()}
    return dict(axes=axes, buttons={key: bool(keys & (1 << bit)) for bit, key in enumerate(KEYS)},
                battery=min(battery, 100))


class Session:
    """Bounded interactive GATT session; no shell interpolation or dependencies."""
    def __init__(self, address, adapter, stop):
        master, slave = pty.openpty()
        try:
            self.proc = subprocess.Popen(['/usr/bin/gatttool', '-i', adapter, '-b', address,
                '-t', 'public', '-I'], stdin=slave, stdout=slave, stderr=slave, close_fds=True)
        except Exception:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self.fd, self.stop, self.buffer = master, stop, ''

    def send(self, command):
        os.write(self.fd, (command+'\n').encode('ascii'))

    def expect(self, pattern, timeout=5):
        end = time.monotonic()+timeout
        while not self.stop.is_set():
            match = re.search(pattern, self.buffer, re.I)
            if match:
                before = self.buffer[:match.end()]
                self.buffer = self.buffer[match.end():]
                return match, before
            if self.proc.poll() is not None or time.monotonic() > end:
                raise ValueError('Unitree BLE timed out. Keep the remote on and disconnect it from other apps.')
            if select.select([self.fd], [], [], .1)[0]:
                try:
                    chunk = os.read(self.fd, 8192)
                except OSError as e:
                    raise ValueError('Unitree BLE connection closed.') from e
                if not chunk:
                    raise ValueError('Unitree BLE connection closed.')
                self.buffer = (self.buffer+chunk.decode('ascii', errors='replace'))[-65536:]
                if re.search(r'Error:|Disconnected|connect error', self.buffer, re.I):
                    raise ValueError('Unitree BLE connection failed or disconnected.')
        raise ValueError('Unitree BLE disconnected.')

    def close(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=2)
        os.close(self.fd)


class UnitreeRemote:
    def __init__(self):
        self.lock = threading.RLock()
        self.thread = None
        self.stop = threading.Event()
        self.state = dict(connected=False, devices=[], error='')

    def snapshot(self):
        with self.lock:
            data = dict(self.state)
            if data.get('connected') and time.monotonic()-data.get('stamp', 0) >= .5:
                data['connected'] = False
            return data

    def disconnect(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=4)
        with self.lock:
            self.state = dict(connected=False, devices=[], error='')

    def connect(self, address, name, adapter, done):
        self.disconnect()
        self.stop = threading.Event()
        with self.lock:
            self.state = dict(connected=False, devices=[], error='', address=address, name=name)
        self.thread = threading.Thread(target=self.run, args=(address, name, adapter, done),
                                       daemon=True, name='unitree-ble')
        self.thread.start()

    def run(self, address, name, adapter, done):
        session, announced = None, False
        try:
            session = Session(address, adapter, self.stop)
            session.send('connect')
            session.expect('Connection successful', timeout=12)
            if self.stop.wait(.2):
                return
            session.send('mtu 64')
            session.expect('MTU was exchanged successfully')
            session.send('characteristics')
            _, chars = session.expect(r'uuid\s*[:=]\s*0000ffe2-0000-1000-8000-00805f9b34fb')
            found = re.findall(r'handle\s*[:=]\s*(0x[0-9a-f]+), char properties\s*[:=]\s*0x[0-9a-f]+, char value handle\s*[:=]\s*(0x[0-9a-f]+), uuid\s*[:=]\s*(\S+)', chars, re.I)
            handles = {uuid.lower(): (int(decl,16), int(value,16)) for decl, value, uuid in found}
            notify = handles['0000ffe1-0000-1000-8000-00805f9b34fb'][1]
            write_decl, write = handles['0000ffe2-0000-1000-8000-00805f9b34fb']
            session.send(f'char-desc 0x{notify+1:04x} 0x{write_decl-1:04x}')
            match, _ = session.expect(r'handle\s*[:=]\s*(0x[0-9a-f]+), uuid\s*[:=]\s*00002902-0000-1000-8000-00805f9b34fb')
            session.send(f'char-write-req {match.group(1)} 0100')
            session.expect('Characteristic value was written successfully')
            # Eight ASCII bytes "59532b32", represented as hex for gatttool.
            session.send(f'char-write-cmd 0x{write:04x} 3539353332623332')
            identity = 'unitree:'+str(time.monotonic_ns())
            previous, counts = {}, {}
            while not self.stop.is_set():
                match, _ = session.expect(r'Notification handle\s*[:=]\s*0x([0-9a-f]+) value: ([0-9a-f ]+)\r?\n', timeout=1)
                if int(match.group(1),16) != notify:
                    continue
                packet = decode_packet(bytes.fromhex(match.group(2)))
                for key, down in packet['buttons'].items():
                    if down and not previous.get(key):
                        counts[key] = counts.get(key,0)+1
                previous = packet['buttons']
                with self.lock:
                    self.state = dict(packet, connected=True, id=identity, stamp=time.monotonic(),
                        path='unitree:'+address, address=address, name=name, kind='unitree_ble',
                        button_presses=dict(counts), error='', devices=[])
                if not announced:
                    announced = True
                    done(None)
        except Exception as e:
            with self.lock:
                self.state.update(connected=False, error='' if self.stop.is_set() else str(e))
            if not announced:
                done(str(e))
        finally:
            if session:
                session.close()
            with self.lock:
                self.state['connected'] = False
