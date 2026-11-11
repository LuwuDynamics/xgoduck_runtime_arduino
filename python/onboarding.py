"""Headless Wi-Fi/binding lifecycle. Runs even without USB audio or chat enabled."""
import asyncio
import contextlib
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
import time
import httpx
from app_paths import DATA, PRIVATE


def _retry_after_seconds(value):
    if value.isascii() and value.isdecimal():
        seconds = float(value)
        if math.isfinite(seconds):
            return seconds
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is not None:
            return max(0., date.timestamp() - time.time())
    except (ValueError, TypeError, OSError, OverflowError):
        pass
    return 30.


def ota_url(config):
    if config.get('ota_url'):
        return config['ota_url']
    region = config.get('cloud_region')
    if region not in ('cn', 'global'):
        return ''
    return ('https://xl-api.xgorobot.com/xiaolu/ota/' if region == 'cn'
            else 'https://xl-api.luwudynamics.ai/xiaolu/ota/')


class HardwareClient:
    def __init__(self, socket=None):
        path = socket or os.environ.get('DUCK_GAMEPAD_SOCKET', str(DATA / 'gamepad.sock'))
        self.client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=path),
            base_url='http://hardware', timeout=httpx.Timeout(2, connect=.5), trust_env=False)

    async def snapshot(self):
        response = await self.client.get('/status')
        response.raise_for_status()
        value = response.json().get('provisioning')
        if not isinstance(value, dict) or not isinstance(value.get('network'), dict):
            raise ValueError('Install the updated host hardware support.')
        return value

    async def serial_number(self):
        try:
            response = await self.client.get('/serial-number')
            response.raise_for_status()
            value = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        serial = value.get('serial_number') if isinstance(value, dict) else None
        if isinstance(serial, str) and re.fullmatch(r'[0-9A-F]{6}', serial):
            return serial
        return None

    async def advertise(self, enabled):
        response = await self.client.post('/provision', json={'enabled': enabled})
        response.raise_for_status()

    async def close(self):
        await self.client.aclose()


class CloudProbe:
    def __init__(self, hardware=None, *, clock=time.monotonic):
        self.hardware = hardware
        self.clock = clock
        self.activation_retry_at = {}
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(8, connect=4), trust_env=False,
                                       follow_redirects=True)

    async def _activation_status(self, region, device):
        if self.clock() < self.activation_retry_at.get(region, 0.):
            return None
        try:
            async with asyncio.timeout(6):
                response = await self.client.post(ota_url({'cloud_region': region}) + 'activate',
                    headers={'Device-Id': device}, follow_redirects=False)
        except (OSError, httpx.HTTPError, TimeoutError):
            return None
        if response.status_code == 429:
            delay = _retry_after_seconds(response.headers.get('Retry-After', '').strip())
            self.activation_retry_at[region] = self.clock() + delay
        if response.status_code == 200 and response.content.strip() == b'success':
            return True
        if response.status_code == 202 and not response.content:
            return False
        return None

    async def _find_region(self, device):
        if not re.fullmatch(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', device):
            raise ValueError('Missing valid device MAC for region discovery.')
        if self.clock() < self.activation_retry_at.get('cn', 0.):
            raise ValueError('Waiting to retry the China service.')
        domestic = asyncio.create_task(self._activation_status('cn', device))
        international = asyncio.create_task(self._activation_status('global', device))
        try:
            result = await domestic
            if result is True:
                return 'cn'
            if result is False:
                other = await international
                if other is not None:
                    return 'global' if other else None
            raise ValueError('Could not determine the voice service region.')
        finally:
            international.cancel()
            await asyncio.gather(international, return_exceptions=True)

    async def __call__(self, cfg):
        # OTA has no dependency on a microphone, PortAudio, a display or the MCU.
        from ota import OtaManager, get_device_id, get_client_id, _read_app_version
        # Manager status/MCP routes use lowercase MACs in MQTT client IDs.
        # Ask OTA to sign matching credentials; never rewrite a signed client ID.
        # The saved factory identity and BluFi binding MAC remain unchanged.
        device = get_device_id().lower()
        url = ota_url(cfg)
        auto_region = None
        if not url:
            auto_region = await self._find_region(device)
            if auto_region is None:
                return dict(bound=False, mqtt={})
            url = ota_url({'cloud_region': auto_region})
        url = url.rstrip('/') + '/'
        client = get_client_id()
        body = dict(version=2, mac_address=device, uuid=client, chip_model_name='Arduino UNO Q',
            application=dict(name='xgoduck-ai', version=_read_app_version()),
            board=dict(type='XGO-Duck', name='XGO-Duck', mac=device))
        serial = await self.hardware.serial_number() if self.hardware and url.startswith('https://') else None
        if serial:
            body['serial_number'] = serial
        async with asyncio.timeout(12):
            headers = dict(OtaManager(url)._headers())
            headers['Device-Id'] = device
            # Keep the serial on the configured HTTPS endpoint. Redirected
            # or plain HTTP requests use the legacy body.
            response = await self.client.post(url, headers=headers, json=body, follow_redirects=not serial)
            if serial and response.has_redirect_location:
                del body['serial_number']
                response = await self.client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
        if not isinstance(data, dict) or data.get('code', 0) != 0 or data.get('msg'):
            raise ValueError('Invalid OTA response.')
        activation = data.get('activation')
        if isinstance(activation, dict) and activation.get('code'):
            if auto_region:
                raise ValueError('Binding changed during region discovery.')
            return dict(bound=False, mqtt={})
        mqtt = data.get('mqtt')
        if not isinstance(mqtt, dict) or not isinstance(mqtt.get('endpoint'), str) or not mqtt['endpoint']:
            raise ValueError('OTA did not provide MQTT settings.')
        result = dict(bound=True, mqtt=mqtt)
        if auto_region:
            result['auto_region'] = auto_region
        return result

    async def close(self):
        await self.client.aclose()


class Onboarding:
    def __init__(self, config, hardware=None, probe=None, state_dir=PRIVATE, clock=time.monotonic):
        self.config = config
        self.hardware = hardware or HardwareClient()
        self.probe = probe or CloudProbe(self.hardware, clock=clock)
        self.directory, self.clock = Path(state_dir), clock
        self.stop = asyncio.Event()
        self.context = None
        self.next_probe = 0.
        self.discovery_started = None
        self.probe_task = None
        self.step_lock = asyncio.Lock()
        self.bound, self.mqtt, self.ready = None, {}, False
        self.last_advertise = None
        self.last_advertise_at = 0.
        self.generation = 0
        self.info = dict(state='starting', bound=None, wifi_connected=None, advertising=False,
                         name='', hardware_error='', cloud_error='', cached=False, identity_mismatch=False,
                         cloud_region=None, cloud_configured=False)

    def snapshot(self):
        return dict(self.info, bound=self.bound, ready=self.ready, generation=self.generation)

    def ready_for(self, url):
        return self.ready and self.context == self._binding_key(url)

    @staticmethod
    def _binding_key(url):
        return hashlib.sha256((url + '|' + os.environ.get('DUCK_DEVICE_ID', '')).encode()).hexdigest()

    def load(self, key):
        try:
            data = json.loads((self.directory / 'binding.json').read_text())
            if data['context'] == key and isinstance(data['bound'], bool):
                mqtt = data.get('mqtt', {})
                if data['bound'] and (not isinstance(mqtt, dict) or not isinstance(mqtt.get('endpoint'), str)
                                      or not mqtt['endpoint']):
                    return
                self.bound = data['bound']
                self.mqtt = mqtt if self.bound else {}
                self.info['cached'] = True
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def save(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.NamedTemporaryFile(mode='w', dir=self.directory, delete=False) as f:
            path = Path(f.name)
            json.dump(dict(context=self.context, bound=self.bound, mqtt=self.mqtt), f)
        try:
            path.chmod(0o600)
            path.replace(self.directory / 'binding.json')
        finally:
            path.unlink(missing_ok=True)

    async def step(self):
        async with self.step_lock:
            await self._step()

    async def refresh_credentials(self, expected_url):
        # MQTT refresh uses the same cloud-scoped cache and revocation checks
        # as onboarding, never the upstream SDK's language-based MQTT cache.
        async with self.step_lock:
            if not expected_url or ota_url(self.config.get()) != expected_url:
                return None
            await self.cancel_probe()
            self.next_probe = 0
            await self._step()
            if self.probe_task is not None:
                try:
                    await self.probe_task
                except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError, asyncio.TimeoutError):
                    pass  # _step records the error without discarding a valid same-cloud cache.
                await self._step()
            if self.ready_for(expected_url) and ota_url(self.config.get()) == expected_url:
                return dict(self.mqtt)
            return None

    async def _step(self):
        try:
            setup = await self.hardware.snapshot()
            expected, actual = os.environ.get('DUCK_DEVICE_ID', ''), setup.get('device_id', '')
            mismatch = bool(expected and actual and expected.upper() != actual.upper())
            self.info.update(wifi_connected=setup['network'].get('connected'),
                advertising=bool(setup.get('advertising')), name=setup.get('name', ''),
                hardware_error='Device identity mismatch; reinstall host support.' if mismatch else setup.get('error', ''),
                identity_mismatch=mismatch)
        except (OSError, ValueError, httpx.HTTPError):
            self.info.update(wifi_connected=None, advertising=False,
                             hardware_error='Hardware support unavailable; run install-ai-support.sh.')
        cfg, now = self.config.get(), self.clock()
        url = ota_url(cfg)
        self.info.update(cloud_region=cfg.get('cloud_region'), cloud_configured=bool(url))
        key = self._binding_key(url)
        if key != self.context:
            await self.cancel_probe()
            self.context, self.bound, self.mqtt = key, None, {}
            self.info.update(cached=False, cloud_error='')
            self.next_probe = 0
            self.discovery_started = None
            self.generation += 1
            if url:
                self.load(key)
        connected = self.info['wifi_connected']
        can_probe = (connected is not False if url else
                     connected is True and not self.info['identity_mismatch'])
        if not url and can_probe and self.discovery_started is None:
            self.discovery_started = now
        if can_probe and self.probe_task is not None and self.probe_task.done():
            try:
                result = self.probe_task.result()
                bound, mqtt = result['bound'], result['mqtt']
                if not isinstance(bound, bool) or not isinstance(mqtt, dict):
                    raise ValueError('Invalid binding probe.')
                if bound and not mqtt.get('endpoint'):
                    raise ValueError('Missing MQTT endpoint.')
                if not url:
                    self.bound = None if bound else False
                    if bound:
                        selection = self.config.select_region(result['auto_region'], only_if_unconfigured=True)
                        if selection['changed']:
                            url = ota_url(selection)
                            self.context = self._binding_key(url)
                            self.info.update(cloud_region=selection['cloud_region'], cloud_configured=True)
                self.info.update(cached=False, cloud_error='')
                if url:
                    previous = self.bound, self.mqtt
                    self.bound, self.mqtt = bound, mqtt if bound else {}
                    if (previous[0] != self.bound or previous[1].get('endpoint') != self.mqtt.get('endpoint')
                            or previous[1].get('client_id') != self.mqtt.get('client_id')):
                        self.generation += 1
                    if previous != (self.bound, self.mqtt):
                        self.save()
            except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError, asyncio.TimeoutError) as exc:
                # An error in discovery cannot confirm the absence of a binding.
                # Keep existing same-cloud credentials when a configured service is unreachable.
                if not url:
                    self.bound = None
                self.info.update(cloud_error=type(exc).__name__, cached=self.bound is not None)
            self.probe_task = None
            if url:
                self.next_probe = now + (30 if self.bound is True else 3)
            else:
                self.next_probe = now + (5 if now - self.discovery_started < 60 else 30)
        if not can_probe:
            await self.cancel_probe()
            self.next_probe = 0
            if not url:
                self.discovery_started = None
                self.bound = None
                self.info.update(cached=False, cloud_error='')
        elif self.probe_task is None and now >= self.next_probe:
            try:
                if url or not self.config.region_status()['configured']:
                    self.probe_task = asyncio.create_task(self.probe(cfg))
            except ValueError as exc:
                self.info.update(cloud_error=type(exc).__name__)
                self.next_probe = now + 30
        self.ready = (bool(url) and not self.info['identity_mismatch'] and connected is not False
                      and self.bound is True and bool(self.mqtt.get('endpoint')))
        desired = (not self.ready or bool(self.info['cloud_error'])) and not self.info['identity_mismatch']
        if desired != self.last_advertise or now - self.last_advertise_at >= 5:
            try:
                await self.hardware.advertise(desired)
                self.last_advertise, self.last_advertise_at = desired, now
            except (OSError, ValueError, httpx.HTTPError):
                self.last_advertise = None
        self.info['state'] = ('hardware_error' if self.info['identity_mismatch'] else
                              'awaiting_network' if not can_probe else
                              'awaiting_binding' if self.bound is False else
                              'ready' if self.ready else 'checking_binding')

    async def cancel_probe(self):
        if self.probe_task is not None:
            self.probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.probe_task
            self.probe_task = None

    async def run(self):
        try:
            while not self.stop.is_set():
                await self.step()
                try:
                    await asyncio.wait_for(self.stop.wait(), 1)
                except asyncio.TimeoutError:
                    pass
        finally:
            await self.cancel_probe()
            await self.hardware.close()
            await self.probe.close()
