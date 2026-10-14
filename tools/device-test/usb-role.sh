#!/bin/sh
# Explicit roles only. Nothing in app startup enables Host.
set -eu
case "${1:-}" in host|device) mode=$1 ;; *) echo 'Usage: duck-usb-role host|device' >&2; exit 2 ;; esac
role=/sys/kernel/debug/usb/4e00000.usb/mode
for attempt in $(seq 1 30); do
    if [ -w "$role" ]; then
        if [ "$(cat "$role")" != "$mode" ]; then
            echo "$mode" > "$role"
        fi
        if [ "$(cat "$role")" = "$mode" ]; then
            exit 0
        fi
    fi
    sleep 1
done
echo 'Unable to select USB role.' >&2
exit 1
