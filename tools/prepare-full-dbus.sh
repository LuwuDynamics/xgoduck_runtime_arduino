#!/usr/bin/env bash
# Run in a Debian 13 ARM64 build environment with up-to-date APT indexes.
set -euo pipefail
if [[ $# -lt 2 || $(dpkg --print-architecture) != arm64 ]]; then
    echo "Usage (Debian ARM64): bash $0 BUNDLE_DEBS_DIR BOARD_DPKG_STATUS [BOARD_DPKG_STATUS ...]" >&2
    exit 1
fi
repo=$(realpath "$1")
shift
statuses=()
for status in "$@"; do
    test -s "$status"
    statuses+=("$(realpath "$status")")
done
test -s "$repo/Packages"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/archives/partial"
cd "$work/archives"

# Early UNO Q images lack socat for the Router and the Video4Linux utilities.
# Include the matching RDS library even if the build's reference board has it.
apt-get download python3-dbus socat v4l-utils libv4l2rds0t64
required=(./*.deb)
for status in "${statuses[@]}"; do
    apt-get -y --download-only --no-install-recommends --no-remove \
        -o "Dir::State::status=$status" -o "Dir::Cache::archives=$work/archives" \
        install "${required[@]}"
done
install -m 0644 ./*.deb "$repo/"

# Index every payload, including the existing Arduino and Debian packages.
cd "$repo"
for deb in ./*.deb; do
    control=$(dpkg-deb -f "$deb")
    printf '%s\n' "$control"
    printf 'Filename: %s\nSize: %s\n' "$deb" "$(stat -c %s "$deb")"
    checksum=$(sha256sum -- "$deb")
    printf 'SHA256: %s\n\n' "${checksum%% *}"
done > "$work/Packages"
install -m 0644 "$work/Packages" Packages
