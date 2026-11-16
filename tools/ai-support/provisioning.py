"""Host-only Wi-Fi and plaintext BluFi session, compatible with the Luwu clients.

Protocol and packet sizing follow Luwu-OS apps/voice_chat/blufi.py (see provenance).
Passwords are neither logged nor returned through the local status socket.
"""
import asyncio
import binascii
import hashlib
import os
from pathlib import Path
import re
import subprocess

CTRL, DATA = 0, 1
ACK, CONNECT, STATUS, SCAN = 0, 3, 5, 9
SSID, PASSWORD, WIFI_STATE, WIFI_LIST, ERROR, CUSTOM = 2, 3, 15, 17, 18, 19
ENCRYPTED, CHECKSUM, DIRECTION, REQUIRE_ACK, FRAGMENT = 1, 2, 4, 8, 16


def read_board_serial_number(path=Path('/sys/devices/soc0/serial_number')):
    """Read the low 24 bits of the UNO Q SoC serial as six uppercase hex digits."""
    try:
        serial = int(path.read_text(encoding='ascii'), 10)
    except (OSError, ValueError):
        return None
    if 0 < serial <= 0xFFFFFFFF:
        return f'{serial & 0xFFFFFF:06X}'
    return None


def identity_bytes(value):
    if not isinstance(value, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', value):
        raise ValueError('A persistent device identity is required.')
    raw = bytes.fromhex(value.replace(':', ''))
    if raw in (b'\0' * 6, b'\xff' * 6) or raw[0] & 1:
        raise ValueError('Invalid device identity.')
    return raw


def frame(kind, subtype, seq, payload=b'', flags=DIRECTION):
    if len(payload) > 255:
        raise ValueError('BluFi frame too large.')
    body = bytes([(subtype << 2) | kind, flags, seq & 255, len(payload)]) + payload
    return body + crc(body[2:]) if flags & CHECKSUM else body


def crc(payload):
    # ESP BluFi / the installed iOS Pod: CCITT, initial/final xor 0xffff,
    # encoded little-endian over sequence, data length and plaintext data.
    return (binascii.crc_hqx(payload, 0xffff) ^ 0xffff).to_bytes(2, 'little')


def wifi_frames(aps):
    blob = bytearray()
    for ssid, rssi in aps[:80]:
        raw = ssid.encode('utf-8')
        if not 1 <= len(raw) <= 32:
            continue
        blob.extend(bytes([len(raw) + 1, max(-127, min(-1, rssi)) & 255]) + raw)
    if not blob:
        return [(b'', DIRECTION)]  # Complete an empty scan instead of timing out on the phone.
    chunks = []
    while len(blob) > 16:
        chunks.append((len(blob).to_bytes(2, 'little') + blob[:14], DIRECTION | FRAGMENT))
        del blob[:14]
    chunks.append((bytes(blob), DIRECTION))
    return chunks


def split_nmcli(line):
    """nmcli -t escapes colons and backslashes in SSIDs/profile names."""
    fields, field, escaped = [], '', False
    for char in line:
        if escaped:
            field += char
            escaped = False
        elif char == '\\':
            escaped = True
        elif char == ':':
            fields.append(field)
            field = ''
        else:
            field += char
    fields.append(field + ('\\' if escaped else ''))
    return fields


def wifi_device_status(interface='wlan0'):
    """Read the active access point without triggering a scan."""
    status = {'type': 'wifi'}
    try:
        result = subprocess.run(
            ['nmcli', '-t', '-e', 'yes', '-f', 'IN-USE,SSID,SIGNAL', 'device', 'wifi',
             'list', 'ifname', interface, '--rescan', 'no'],
            capture_output=True, encoding='utf-8', errors='replace', check=True,
            timeout=1, env=dict(os.environ, LC_ALL='C'))
    except (OSError, subprocess.SubprocessError):
        return status
    for line in result.stdout.splitlines():
        fields = split_nmcli(line)
        if len(fields) != 3 or fields[0] != '*':
            continue
        _, ssid, strength = fields
        if ssid:
            status['ssid'] = ssid
        if strength.isascii() and strength.isdecimal() and 0 <= int(strength) <= 100:
            percent = int(strength)
            status['signal'] = 'strong' if percent >= 80 else ('medium' if percent >= 60 else 'weak')
        break
    return status


async def run_command(args, timeout=10):
    proc = None
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, env=dict(os.environ, LC_ALL='C', LANG='C'))
        output, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode, output.decode('utf-8', errors='replace')
    except (OSError, asyncio.TimeoutError):
        return -1, ''
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()


class Wifi:
    def __init__(self, run=run_command, interface='wlan0'):
        self.run, self.interface = run, interface
        self.lock = asyncio.Lock()

    async def status(self):
        rc, out = await self.run(['nmcli', '-t', '-f', 'GENERAL.STATE,GENERAL.CONNECTION',
                                  'device', 'show', self.interface], timeout=3)
        values = {parts[0]: parts[1] for line in out.splitlines()
                  if len(parts := split_nmcli(line)) >= 2}
        state = values.get('GENERAL.STATE', '').split(' ', 1)[0]
        connected, uuid = rc == 0 and state == '100', ''
        if connected:
            active_rc, active = await self.run(['nmcli', '-t', '-f', 'UUID,DEVICE',
                                                'connection', 'show', '--active'], timeout=2)
            if active_rc == 0:
                uuid = next((parts[0] for line in active.splitlines()
                             if len(parts := split_nmcli(line)) == 2 and parts[1] == self.interface), '')
        return dict(available=rc == 0, connected=connected, connection_uuid=uuid)

    async def scan(self):
        async with self.lock:
            await self.run(['nmcli', 'radio', 'wifi', 'on'], timeout=2)
            await self.run(['nmcli', 'device', 'wifi', 'rescan', 'ifname', self.interface], timeout=8)
            rc, out = await self.run(['nmcli', '-t', '-f', 'SSID,SIGNAL', 'device', 'wifi',
                                      'list', 'ifname', self.interface, '--rescan', 'no'], timeout=3)
        if rc:
            raise RuntimeError('Wi-Fi scan unavailable.')
        strongest = {}
        for line in out.splitlines():
            parts = split_nmcli(line)
            if len(parts) != 2 or not parts[0] or not parts[1].isdigit():
                continue
            signal = max(-95, min(-1, int(parts[1]) - 100))
            strongest[parts[0]] = max(signal, strongest.get(parts[0], -127))
        return sorted(strongest.items(), key=lambda item: item[1], reverse=True)

    async def connect(self, ssid, password):
        if not 1 <= len(ssid.encode('utf-8')) <= 32 or '\0' in ssid:
            return False
        if password and not (8 <= len(password.encode('utf-8')) <= 63 or
                             re.fullmatch(r'[0-9a-fA-F]{64}', password)):
            return False
        # Never delete the user's working profile before a new network succeeds.
        async with self.lock:
            before = await self.status()
            name = 'xgoduck-' + hashlib.sha256(ssid.encode()).hexdigest()[:12]
            rc, profiles = await self.run(['nmcli', '-t', '-f', 'UUID,NAME', 'connection', 'show'], timeout=3)
            old_profiles = [parts[0] for line in profiles.splitlines()
                            if len(parts := split_nmcli(line)) == 2 and parts[1] == name] if rc == 0 else []
            # A unique candidate avoids overwriting a previously working Duck profile.
            import uuid
            candidate = str(uuid.uuid4())
            args = ['nmcli', 'connection', 'add', 'type', 'wifi', 'ifname', self.interface,
                    'con-name', name, 'connection.uuid', candidate, 'ssid', ssid,
                    'connection.autoconnect', 'yes', 'connection.autoconnect-priority', '20',
                    '802-11-wireless.cloned-mac-address', 'permanent']
            if password:
                args += ['wifi-sec.key-mgmt', 'wpa-psk', 'wifi-sec.psk', password]
            success = False
            try:
                async with asyncio.timeout(26):
                    await self.run(['nmcli', 'radio', 'wifi', 'on'], timeout=2)
                    rc, _ = await self.run(args, timeout=3)
                    if rc == 0:
                        rc, _ = await self.run(['nmcli', '--wait', '20', 'connection', 'up',
                                               'uuid', candidate, 'ifname', self.interface], timeout=21)
                        after = await self.status()
                        success = rc == 0 and after['connected'] and after['connection_uuid'] == candidate
            except asyncio.TimeoutError:
                pass
            finally:
                if not success:
                    await self.run(['nmcli', 'connection', 'delete', 'uuid', candidate], timeout=2)
                    # Restore only when the failed attempt actually took the old link down.
                    current = await self.status()
                    if before['connected'] and not current['connected'] and before['connection_uuid']:
                        await self.run(['nmcli', '--wait', '8', 'connection', 'up', 'uuid',
                                        before['connection_uuid']], timeout=9)
                else:
                    for old in old_profiles:
                        await self.run(['nmcli', 'connection', 'delete', 'uuid', old], timeout=2)
            return success


class Session:
    def __init__(self, identity, wifi, send, emit=lambda *_: None, *, serial_number=None):
        self.identity_payload = identity_bytes(identity)
        if isinstance(serial_number, str) and re.fullmatch(r'[0-9A-F]{6}', serial_number):
            # Version 2 adds six ASCII hex digits after the legacy MAC prefix.
            self.identity_payload += b'\x02' + serial_number.encode('ascii')
        self.wifi, self.send, self.emit = wifi, send, emit
        self.tasks = set()
        self.send_lock = asyncio.Lock()
        self.connect_timeout = 24  # The Mini Program/Web configuration timeout is 30 s.
        self.scan_task = self.connect_task = None
        self.reset()

    def reset(self):
        self.seq, self.ssid, self.password = -1, '', ''
        self.fragments, self.fragment_type = bytearray(), None
        self.notifying, self.owner = False, None
        self.phone_done = asyncio.Event()

    @property
    def busy(self):
        return self.connect_task is not None and not self.connect_task.done()

    async def notify(self, kind, subtype, payload=b'', flags=DIRECTION):
        async with self.send_lock:
            await self.notify_unlocked(kind, subtype, payload, flags)

    async def notify_unlocked(self, kind, subtype, payload=b'', flags=DIRECTION):
        if self.notifying:
            self.seq = (self.seq + 1) & 255
            await self.send(frame(kind, subtype, self.seq, payload, flags))

    def spawn(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        def finished(task):
            self.tasks.discard(task)
            if not task.cancelled() and (exc := task.exception()) is not None:
                self.emit('error', {'error': type(exc).__name__})
        task.add_done_callback(finished)
        return task

    async def feed(self, raw, owner=None):
        # Never log raw packets: SSIDs and passwords can both be fragmented.
        if not self.notifying or (self.owner is not None and owner != self.owner):
            return
        if len(raw) < 4:
            raise ValueError('Truncated BluFi frame.')
        type_byte, flags, seq, size = raw[:4]
        expected_size = 4 + size + (2 if flags & CHECKSUM else 0)
        if flags & ENCRYPTED or len(raw) != expected_size:
            raise ValueError('Unsupported or malformed BluFi frame.')
        if flags & CHECKSUM and crc(raw[2:-2]) != raw[-2:]:
            raise ValueError('Invalid BluFi checksum.')
        self.owner = owner
        if flags & REQUIRE_ACK:
            await self.notify(CTRL, ACK, bytes([seq]))
        kind, subtype, payload = type_byte & 3, type_byte >> 2, raw[4:4 + size]
        if flags & FRAGMENT:
            if len(payload) < 2 or (self.fragment_type is not None and self.fragment_type != (kind, subtype)):
                self.fragments.clear()
                self.fragment_type = None
                raise ValueError('Invalid BluFi fragment.')
            self.fragment_type = kind, subtype
            self.fragments.extend(payload[2:])
            if len(self.fragments) > 128:
                self.fragments.clear()
                self.fragment_type = None
                raise ValueError('BluFi value too large.')
            return
        if self.fragment_type is not None:
            if self.fragment_type != (kind, subtype) or len(self.fragments) + len(payload) > 128:
                self.fragments.clear()
                self.fragment_type = None
                raise ValueError('Invalid final BluFi fragment.')
            payload = bytes(self.fragments) + payload
            self.fragments.clear()
            self.fragment_type = None
        if kind == DATA and subtype in (SSID, PASSWORD):
            if self.busy:
                return
            value = payload.decode('utf-8')
            if subtype == SSID:
                self.ssid = value
            else:
                self.password = value
        elif kind == CTRL and subtype == SCAN:
            if not self.busy and (self.scan_task is None or self.scan_task.done()):
                self.scan_task = self.spawn(self.scan())
        elif kind == CTRL and subtype == STATUS:
            self.spawn(self.status())
        elif kind == CTRL and subtype == CONNECT and not self.busy:
            self.connect_task = self.spawn(self.connect(self.ssid, self.password))

    async def scan(self):
        self.emit('scanning', {})
        try:
            aps = await self.wifi.scan()
            # A complete fragmented message must be contiguous. A status/ACK
            # in the middle would corrupt the native and Mini Program parser.
            async with self.send_lock:
                for payload, flags in wifi_frames(aps):
                    await self.notify_unlocked(DATA, WIFI_LIST, payload, flags)
                    await asyncio.sleep(.03)
            self.emit('scan_complete', {'count': len(aps)})
        except (OSError, RuntimeError):
            await self.notify(DATA, WIFI_LIST)
            self.emit('scan_failed', {})

    async def status(self):
        state = await self.wifi.status()
        async with self.send_lock:
            await self.notify_unlocked(DATA, WIFI_STATE, bytes([1, 0 if state['connected'] else 1, 0]))
            if state['connected']:
                await self.notify_unlocked(DATA, CUSTOM, self.identity_payload)

    async def connect(self, ssid, password):
        self.emit('connecting', {})
        operation = asyncio.create_task(self.wifi.connect(ssid, password))
        try:
            done, _ = await asyncio.wait({operation}, timeout=self.connect_timeout)
            success = bool(operation.result()) if done else False
            # State and stable ID must reach the phone before unregistering GATT.
            for attempt in range(2):
                if attempt:
                    await asyncio.sleep(.5)
                async with self.send_lock:
                    await self.notify_unlocked(DATA, WIFI_STATE, bytes([1, 0 if success else 1, 0]))
                    if success:
                        await asyncio.sleep(.1)
                        await self.notify_unlocked(DATA, CUSTOM, self.identity_payload)
            if success:
                try:
                    await asyncio.wait_for(self.phone_done.wait(), 8)
                except asyncio.TimeoutError:
                    pass
            self.emit('provisioned' if success else 'connect_failed', {})
        except (OSError, RuntimeError):
            await self.notify(DATA, WIFI_STATE, bytes([1, 1, 0]))
            self.emit('connect_failed', {})
        finally:
            self.password = ''
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)

    async def close(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.reset()
