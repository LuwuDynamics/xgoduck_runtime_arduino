#!/bin/sh
# UNO Q: select USB Host after the controller's debugfs role switch is ready.
set -eu
role=/sys/kernel/debug/usb/4e00000.usb/mode
for attempt in $(seq 1 30); do
    if [ -w "$role" ]; then
        if [ "$(cat "$role")" != host ]; then
            echo host > "$role"
        fi
        if [ "$(cat "$role")" = host ]; then
            echo 'USB controller is in Host mode.'
            exit 0
        fi
    fi
    sleep 1
done
echo 'Unable to select USB Host mode.' >&2
exit 1
