#!/usr/bin/env bash
# Run on Debian 13 ARM64 with current APT indexes and a staged offline repository.
set -euo pipefail
if [[ $# -lt 2 || $(dpkg --print-architecture) != arm64 ]]; then
    echo "Usage (Debian ARM64): bash $0 BUNDLE_DEBS_DIR BOARD_DPKG_STATUS [BOARD_DPKG_STATUS ...]" >&2
    exit 1
fi

recipe=$(realpath "$(dirname "$0")/full-bundle-packages.txt")
repo=$(realpath "$1")
shift
statuses=()
for status in "$@"; do
    test -s "$status"
    statuses+=("$(realpath "$status")")
done
test -d "$repo"
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/archives/partial"
cd "$work/archives"

# Preserve versions already selected for this release, and include the mandatory
# payloads even when a reference board already has them installed.
required=()
while read -r name; do
    matches=("$repo/${name}_"*.deb)
    if [[ -f ${matches[0]} ]]; then
        if [[ ${#matches[@]} -ne 1 || $(dpkg-deb -f "${matches[0]}" Package) != "$name" ]]; then
            echo "Expected one $name package in $repo" >&2
            exit 1
        fi
        cp -- "${matches[0]}" .
    else
        apt-get download "$name"
    fi
    matches=("./${name}_"*.deb)
    required+=("${matches[0]}")
done < "$recipe"

for status in "${statuses[@]}"; do
    apt-get -y --download-only --no-install-recommends --no-remove \
        -o "Dir::State::status=$status" -o "Dir::Cache::archives=$work/archives" \
        install "${required[@]}"
done
shopt -s nullglob
downloads=(./*.deb)
if (( ${#downloads[@]} )); then
    install -m 0644 "${downloads[@]}" "$repo/"
fi

cd "$repo"
for deb in ./*.deb; do
    # Some Arduino control files already end with a blank stanza separator.
    control=$(dpkg-deb -f "$deb")
    printf '%s\n' "$control"
    printf 'Filename: %s\nSize: %s\n' "$deb" "$(stat -c %s "$deb")"
    checksum=$(sha256sum -- "$deb")
    printf 'SHA256: %s\n\n' "${checksum%% *}"
done > "$work/Packages"
install -m 0644 "$work/Packages" Packages
