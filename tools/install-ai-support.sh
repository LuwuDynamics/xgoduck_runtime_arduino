#!/bin/bash
# One-time UNO Q host setup and official default App.
# Prepares USB Host; does not start the App or flash MCU. Use Wi-Fi/SSH.
set -euo pipefail
if [ "$(id -u)" != 0 ]; then
    exec sudo bash "$0" "$@"
fi
app_root="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != aarch64 ]; then
    echo 'Run on the UNO Q host after importing the App.' >&2
    exit 1
fi
if ! id arduino >/dev/null 2>&1; then
    echo 'The arduino Linux account is required.' >&2
    exit 1
fi
if [ "$(id -u arduino)" != 1000 ]; then
    echo 'Arduino App CLI requires the arduino account to have UID 1000.' >&2
    exit 1
fi
python3 -c 'import platform; from pathlib import Path; assert "VERSION_ID=\"13\"" in Path("/etc/os-release").read_text(), "UNO Q Debian 13 is required"'
python3 - "$app_root" <<'PY'
import json, pathlib, subprocess, sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / 'tools/ai-support'))
from install_preflight import require_stopped
result = subprocess.run(['runuser', '-u', 'arduino', '--', 'arduino-app-cli', 'app', 'list', '--format', 'json'],
                        check=True, capture_output=True, text=True)
apps = json.loads(result.stdout).get('apps', [])
require_stopped(apps)
PY
python3 - "$app_root" <<'PY'
import pathlib, sys
root = pathlib.Path(sys.argv[1])
sys.path.insert(0, str(root / 'python'))
from voice_config import VoiceConfig
config = VoiceConfig(root / 'data/voice_config.json').get()
if not config['ota_url'] and config['cloud_region'] not in ('cn', 'global'):
    raise SystemExit('Choose your cloud first: python3 tools/configure-ai.py --region cn (or global)')
PY
runuser -u arduino -- python3 "$app_root/tools/configure-ai-python.py"
# Resolve and verify the exact MCU build dependencies before changing services.
# This is compile-only: App Lab Run remains the explicit upload/start step.
runuser -u arduino -- python3 "$app_root/tools/prepare-ai-sketch.py"
apt-get update
apt-get install -y --no-upgrade python3-dbus python3-dbus-next python3-gi python3-evdev bluez network-manager ethtool alsa-utils v4l-utils libopus0 libportaudio2
install -d -m 0755 -o arduino -g arduino "$app_root/data"
install -d -m 0700 -o arduino -g arduino "$app_root/data/voice-state"
python3 "$app_root/tools/prepare-ai-native.py"
# Root code is read-only to the App user. BLE exposes only Wi-Fi provisioning;
# the Unix socket adds a fixed advertisement toggle to the existing operations.
support_dir=/usr/local/lib/xgoduck-ai-support
install -d -m 0755 "$support_dir"
install -m 0644 "$app_root/python/vendor/luwu_os/LICENSE" "$support_dir/LICENSE-luwu-os"
for file in server.py input_reader.py unitree_remote.py usb_role.py; do
    install -m 0644 "$app_root/tools/gamepad/$file" "$support_dir/$file"
done
for file in ai_server.py audio_caps.py blufi.py provisioning.py usb_prepare.py sketch_cache.py; do
    install -m 0644 "$app_root/tools/ai-support/$file" "$support_dir/$file"
done
python3 - "$app_root" <<'PY'
import json, os, pathlib, pwd, re, shutil, subprocess, sys
root = pathlib.Path(sys.argv[1])
state = root / 'data/voice-state'
account = pwd.getpwnam('arduino')
identity = state / 'device-id'
if not identity.exists():
    mac = None
    for path in pathlib.Path('/sys/firmware/devicetree/base').rglob('local-mac-address'):
        if 'wifi' in str(path).lower() or 'wlan' in str(path).lower():
            raw = path.read_bytes()
            if len(raw) == 6 and any(raw):
                mac = ':'.join(f'{b:02x}' for b in raw)
                break
    if not mac and shutil.which('ethtool'):
        result = subprocess.run(['ethtool', '-P', 'wlan0'], text=True, capture_output=True)
        match = re.search(r'(?:[0-9a-f]{2}:){5}[0-9a-f]{2}', result.stdout.lower())
        if match and match[0] != '00:00:00:00:00:00':
            mac = match[0]
    if mac:
        identity.write_text(mac + '\n')
        identity.chmod(0o600)
        os.chown(identity, account.pw_uid, account.pw_gid)
        print('Cloud device identity:', mac)
    else:
        print('Factory MAC unavailable; App will create and retain a private device identity.')
old = pathlib.Path(account.pw_dir) / '.config/duck-device-test/doubao.json'
target = state / 'doubao.json'
if old.is_file() and not target.exists():
    shutil.copyfile(old, target)
    target.chmod(0o600)
    os.chown(target, account.pw_uid, account.pw_gid)
    print('Copied existing Doubao settings without changing the original.')
sys.path.insert(0, str(root / 'python'))
os.environ['DUCK_DATA_DIR'] = str(root / 'data')
from audio_devices import device_identity
cloud_id = device_identity()
os.chown(identity, account.pw_uid, account.pw_gid)
sys.path.insert(0, str(root / 'tools/ai-support'))
from install_config import write_units
write_units(root, cloud_id)
PY
systemctl daemon-reload
# Replace only the legacy Duck helpers. Old USB oneshots can otherwise run
# late in the boot and undo the App's Host selection. Keep their unit files
# and the old App available for an explicit rollback.
for unit in duck-gamepad.service duck-usb-device.service duck-usb-host.service; do
    if systemctl cat "$unit" >/dev/null 2>&1; then
        systemctl disable --now "$unit"
    fi
done
systemctl enable xgoduck-ai-usb-host.service xgoduck-ai-hardware.service
systemctl restart xgoduck-ai-usb-host.service
systemctl restart xgoduck-ai-hardware.service
# Compatible with App CLI 0.10 as well as current versions. In newer releases
# `app build` creates a frozen release, so it is not a portable cache-prep step.
runuser -u arduino -- arduino-app-cli properties set default "$app_root"
systemctl enable arduino-app-cli.service
# The old media service must not reclaim port 9528 after a reboot.
arduino_uid="$(id -u arduino)"
if [ -S "/run/user/$arduino_uid/bus" ]; then
    runuser -u arduino -- env XDG_RUNTIME_DIR="/run/user/$arduino_uid" systemctl --user disable --now duck-device-test.service 2>/dev/null || true
fi
echo 'Support and default App installed. On next boot XGO Duck AI starts automatically.'
echo 'USB Host is ready; USB/ADB computer access is disconnected. BluFi works without USB audio.'
echo 'MCU compile and ELF checks passed. First Run still needs internet for container/Python dependencies.'
echo 'After the first successful Run, verify an offline cold boot.'
