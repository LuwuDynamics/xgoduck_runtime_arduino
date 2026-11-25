#!/bin/bash
# Configure the UNO Q host end of the Duck's reliable Bridge.
set -euo pipefail
if (( EUID != 0 )); then
  echo 'Run tools/router-2m.sh with sudo on the UNO Q.' >&2
  exit 1
fi
cd -- "$(dirname -- "$0")"
package=router-runtime/arduino-router_0.0.0-45cccf1-1_arm64.deb
package_sha=30e5b17d81bbded62a2c8e59eca8bd45edcecd35a613f2b217a515cb481cf3b0
binary_sha=4fec6f237b3bccd8eea8e55732c725cac9567823405bfe29f377f959625424a0
binary=/usr/local/lib/xgoduck-router-rc/arduino-router-45cccf1
unit_dir=/etc/systemd/system/arduino-router.service.d
target=$unit_dir/90-xgoduck-baud.conf
service=arduino-router.service

exec 9>/run/xgoduck-router-install.lock
flock 9
if ! printf '%s  %s\n' "$package_sha" "$package" | sha256sum --check --status; then
  echo "Missing or damaged bundled Router package: $package" >&2
  exit 1
fi
systemctl cat "$service" >/dev/null
install -d -m 755 "$unit_dir" "$(dirname "$binary")"
work=$(mktemp -d "$unit_dir/.xgoduck.XXXXXX")
trap 'rm -rf -- "$work"' EXIT

if [[ ! -x "$binary" ]] || ! printf '%s  %s\n' "$binary_sha" "$binary" | sha256sum --check --status 2>/dev/null; then
  dpkg-deb -x "$package" "$work/package"
  extracted=$work/package/usr/bin/arduino-router
  printf '%s  %s\n' "$binary_sha" "$extracted" | sha256sum --check --status
  "$extracted" version >/dev/null
  install -m 755 "$extracted" "$binary.new"
  mv -fT "$binary.new" "$binary"
fi

# Other directives in the board-generated unit handle the boot/reset GPIOs.
cat > "$work/new.conf" <<UNIT
[Service]
ExecStart=
ExecStart=$binary --unix-port /var/run/arduino-router.sock --serial-port /dev/ttyHS1 --serial-baudrate 115200 --serial-max-baudrate 2000000 --serial-protocol auto --after-ready '/usr/bin/gpioset -c /dev/gpiochip1 -t0 70=1'
UNIT
effective_config_matches() {
  local command
  command=$(systemctl show "$service" -p ExecStart --value) || return 1
  [[ $command == *"path=$binary ;"* && $command == *'--serial-baudrate 115200 '* &&
     $command == *'--serial-max-baudrate 2000000 '* && $command == *'--serial-protocol auto '* ]]
}

was_active=false
if systemctl is-active --quiet "$service"; then was_active=true; fi
if cmp -s "$work/new.conf" "$target" && "$was_active" && effective_config_matches; then
  pid=$(systemctl show "$service" -p MainPID --value)
  if [[ $pid =~ ^[1-9][0-9]*$ && /proc/$pid/exe -ef $binary ]]; then
    echo 'Arduino Router already configured for reliable negotiation up to 2 Mbaud.'
    exit 0
  fi
fi

if [[ -f "$target" ]]; then
  cp -p -- "$target" "$work/previous.conf"
  if ! cmp -s "$target" "$work/new.conf"; then
    install -d -m 700 /var/backups/xgoduck-router
    cp -p -- "$target" "/var/backups/xgoduck-router/90-xgoduck-baud-$(date +%Y%m%d-%H%M%S)-$$.conf"
  fi
fi
chmod 644 "$work/new.conf"
mv -fT "$work/new.conf" "$target"
restarted=false
if systemctl daemon-reload && effective_config_matches; then
  restarted=true
  if systemctl restart "$service" && systemctl is-active --quiet "$service"; then
    echo 'Arduino Router ready. The sketch negotiates from 115200 up to 2 Mbaud.'
    exit 0
  fi
fi

echo 'Could not activate the Duck Router settings. Restoring the previous configuration.' >&2
if [[ -f "$work/previous.conf" ]]; then
  mv -fT "$work/previous.conf" "$target"
else
  rm -f -- "$target"
fi
systemctl daemon-reload
if "$restarted"; then
  if "$was_active"; then systemctl restart "$service"; else systemctl stop "$service"; fi
fi
exit 1
