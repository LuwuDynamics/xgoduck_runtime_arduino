#!/bin/bash
# Install the port-80 mobile entrance on the UNO Q host, including autostart.
# Does not restart the Duck App or change USB, Bridge or MCU firmware.
set -euo pipefail
if [ "$(id -u)" != 0 ]; then
    exec sudo bash "$0" "$@"
fi
if [ "$(uname -s)" != Linux ] || ! command -v systemctl >/dev/null; then
    echo 'Run on the UNO Q Linux host.' >&2
    exit 1
fi
duck_web_root="$(cd -- "$(dirname -- "$0")/.." && pwd -P)"
duck_web_site=/etc/nginx/sites-available/xgoduck-mobile
duck_web_link=/etc/nginx/sites-enabled/xgoduck-mobile
if [ -n "$(ss -H -ltn 'sport = :80')" ] && [ ! -L "$duck_web_link" ]; then
    echo 'Port 80 is already in use; no existing web service was changed.' >&2
    exit 1
fi
if ! command -v nginx >/dev/null; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-upgrade --no-install-recommends nginx
fi
duck_web_backup="/var/backups/xgoduck-mobile-web/$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$duck_web_backup"
for duck_web_path in "$duck_web_site" "$duck_web_link" /etc/nginx/sites-enabled/default; do
    if [ -e "$duck_web_path" ] || [ -L "$duck_web_path" ]; then
        cp -a --parents "$duck_web_path" "$duck_web_backup/"
    fi
done
install -d -m 0755 /etc/nginx/sites-available /etc/nginx/sites-enabled
install -m 0644 "$duck_web_root/tools/web/nginx-mobile.conf" "$duck_web_site"
ln -sfn "$duck_web_site" "$duck_web_link"
# Keep the stock site file, and only disable its default listener symlink.
if [ -L /etc/nginx/sites-enabled/default ] &&
   [ "$(readlink -f /etc/nginx/sites-enabled/default)" = /etc/nginx/sites-available/default ]; then
    rm /etc/nginx/sites-enabled/default
fi
nginx -t
systemctl enable nginx.service
if systemctl is-active --quiet nginx.service; then
    systemctl reload nginx.service
else
    systemctl start nginx.service
fi
echo 'Mobile cockpit: http://<board-ip>/ (port 80, no page suffix).'
echo "Previous site configuration saved at $duck_web_backup"
