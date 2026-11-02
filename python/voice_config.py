"""Persistent product settings; credentials live in the separate private state directory."""
import json
import os
import threading
import tempfile
from pathlib import Path

from app_paths import DATA

CONFIG_PATH = DATA / 'voice_config.json'
DEFAULTS = dict(enabled=True, language='cn', enable_wakeword=True, enable_aec=False,
                input_device=None, output_device=None, voice_motion_enabled=False,
                volume=60, enable_camera=True, ota_url='', camera_device=None, remote_motion_enabled=False,
                cloud_region=None, input_auto_gain=None, input_gain_db=0, output_gain_db=0,
                tts_mouth_enabled=True, input_channel_mode='auto', input_sample_rate=None,
                aec_output_headroom_db=0)


class VoiceConfig:
    def __init__(self, path=CONFIG_PATH):
        self.path = Path(path)
        self.lock = threading.RLock()

    def get(self):
        with self.lock:
            config = dict(DEFAULTS)
            try:
                config.update(self.validate(json.loads(self.path.read_text())))
            except (OSError, ValueError, TypeError):
                pass
            return config

    @staticmethod
    def validate(changes):
        if not isinstance(changes, dict) or any(k not in DEFAULTS for k in changes):
            raise ValueError('unknown voice configuration field')
        for key, value in changes.items():
            if key == 'aec_output_headroom_db' and (isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not 0 <= value <= 18):
                raise ValueError('aec_output_headroom_db must be 0–18 dB')
            if key == 'input_channel_mode' and value not in ('auto', 'left', 'right', 'average'):
                raise ValueError('input_channel_mode must be auto/left/right/average')
            if key == 'input_sample_rate' and value is not None and (
                    type(value) is not int or value not in (16000, 48000)):
                raise ValueError('input_sample_rate must be null, 16000 or 48000')
            if isinstance(DEFAULTS[key], bool) and not isinstance(value, bool):
                raise ValueError(key + ' must be boolean')
            if key == 'language' and value not in ('cn', 'en'):
                raise ValueError('language must be cn/en')
            if key == 'cloud_region' and value not in (None, 'cn', 'global'):
                raise ValueError('cloud_region must be cn/global (or null before setup)')
            if key == 'input_auto_gain' and value is not None and not isinstance(value, bool):
                raise ValueError('input_auto_gain must be boolean or null')
            if key in ('input_gain_db', 'output_gain_db') and (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not 0 <= value <= 12):
                raise ValueError(key + ' must be 0–12 dB')
            if key in ('input_device', 'output_device') and value is not None:
                if not isinstance(value, str) or not 1 <= len(value) <= 200:
                    raise ValueError('audio device must be a USB key, ALSA card name, or null')
            if key == 'volume' and (isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100):
                raise ValueError('volume must be 0–100')
            if key == 'ota_url' and (not isinstance(value, str) or (value and not value.startswith('https://'))):
                raise ValueError('ota_url must be HTTPS or empty')
            if key == 'camera_device' and value is not None and (
                    not isinstance(value, str) or not value.startswith('/dev/v4l/by-id/')):
                raise ValueError('camera_device must be a /dev/v4l/by-id/ path or null')
        return changes

    def region_status(self):
        """Read only public region metadata; never expose an advanced OTA URL."""
        with self.lock:
            config = dict(DEFAULTS)
            try:
                config.update(self.validate(json.loads(self.path.read_text())))
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError) as exc:
                raise ValueError('Saved voice configuration is invalid; repair it before switching servers.') from exc
            return dict(cloud_region=config['cloud_region'], custom_endpoint=bool(config['ota_url']),
                        configured=bool(config['ota_url'] or config['cloud_region']))

    def select_region(self, region):
        """Explicit user selection preserves all unrelated settings and identity."""
        if region not in ('cn', 'global'):
            raise ValueError('Select cn or global.')
        with self.lock:
            before = self.region_status()
            changed = before['cloud_region'] != region or before['custom_endpoint']
            if changed:
                self.update(dict(cloud_region=region, ota_url=''))
            return dict(self.region_status(), changed=changed, reconnecting=changed)

    def update(self, changes):
        self.validate(changes)
        with self.lock:
            config = self.get()
            config.update(changes)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False) as f:
                tmp = Path(f.name)
                f.write(json.dumps(config, indent=2) + '\n')
            try:
                os.chmod(tmp, 0o600)
                tmp.replace(self.path)
            finally:
                tmp.unlink(missing_ok=True)
            return config
