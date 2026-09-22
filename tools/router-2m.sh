#!/bin/bash
set -euo pipefail
# Run with sudo on Uno Q. Preserve the board-generated GPIO boot/reset hooks.
target=/etc/systemd/system/arduino-router.service.d/90-xgoduck-baud.conf
if [ -n "${SUDO_USER:-}" ]; then
  user_home=$(getent passwd "$SUDO_USER" | cut -d: -f6)
else
  user_home="$HOME"
fi
backup_dir="${user_home:-/tmp}/xgoduck-backup"
mkdir -p "$backup_dir" /etc/systemd/system/arduino-router.service.d
if [ -f "$target" ]; then
  cp -p "$target" "$backup_dir/router-override-$(date +%Y%m%d-%H%M%S).conf"
fi
systemctl cat arduino-router > "$backup_dir/router-before-$(date +%Y%m%d-%H%M%S).txt"
cat > "$target" <<'EOF'
[Service]
ExecStart=
ExecStart=/usr/bin/arduino-router --unix-port /var/run/arduino-router.sock --serial-port /dev/ttyHS1 --serial-baudrate 2000000 --after-ready '/usr/bin/gpioset -c /dev/gpiochip1 -t0 70=1'
EOF
systemctl daemon-reload
systemctl restart arduino-router
systemctl show arduino-router -p ExecStart
