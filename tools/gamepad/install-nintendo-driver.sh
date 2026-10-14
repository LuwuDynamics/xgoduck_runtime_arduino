#!/bin/sh
# Optional Switch Pro support for the UNO Q Linux 7.0 kernel.
set -eu
[ "$(id -u)" = 0 ] || { echo 'Run with sudo.' >&2; exit 1; }
kernel=$(uname -r)
case "$kernel" in 7.0.*) ;; *) echo 'This installer is pinned to Linux 7.0. Review driver compatibility for this kernel.' >&2; exit 1;; esac
[ -f "/lib/modules/$kernel/build/Makefile" ] || { echo 'Install matching kernel headers first.' >&2; exit 1; }
build_dir=$(mktemp -d /tmp/duck-nintendo.XXXXXX)
trap 'rm -rf "$build_dir"' EXIT
cd "$build_dir"
base=https://raw.githubusercontent.com/torvalds/linux/v7.0/drivers/hid
curl -fL --connect-timeout 15 --max-time 60 "$base/hid-nintendo.c" -o hid-nintendo.c
curl -fL --connect-timeout 15 --max-time 60 "$base/hid-ids.h" -o hid-ids.h
cat > SHA256SUMS <<'SUMS'
6b71cb4925c9eea5f98d0f02c41bba85f6bb6bda242a6ee27d57ce2b79db2975  hid-nintendo.c
a88d22bed0835d0b12d2ade286e9f562fcdb2dcebe2db380ab6b3c3ea8ad7838  hid-ids.h
SUMS
sha256sum -c SHA256SUMS
printf 'obj-m += hid-nintendo.o\n' > Makefile
make -j1 -C "/lib/modules/$kernel/build" M="$build_dir" modules
install -D -m 0644 hid-nintendo.ko "/lib/modules/$kernel/updates/duck/hid-nintendo.ko"
depmod -a
modprobe hid-nintendo
