#!/usr/bin/env bash
set -Eeuo pipefail

root_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
installer="$root_dir/install.sh"

constant() {
    local name="$1"
    sed -n "s/^readonly $name=\"\([^\"]*\)\"$/\1/p" "$installer"
}

xray_version="$(constant XRAY_VERSION)"
hysteria_version="$(constant HYSTERIA_VERSION)"
telemt_version="$(constant TELEMT_VERSION)"
xraydo_ref="$(constant XRAYDO_REF)"

[[ -n "$xray_version" && -n "$hysteria_version" && -n "$telemt_version" && -n "$xraydo_ref" ]]

urls=(
    "https://github.com/blyaxyz/xraydo/archive/${xraydo_ref}.tar.gz"
    "https://github.com/XTLS/Xray-core/releases/download/$xray_version/Xray-linux-64.zip"
    "https://github.com/XTLS/Xray-core/releases/download/$xray_version/Xray-linux-arm64-v8a.zip"
    "https://github.com/HyNetworks/hysteria/releases/download/${hysteria_version//\//%2F}/hysteria-linux-amd64"
    "https://github.com/HyNetworks/hysteria/releases/download/${hysteria_version//\//%2F}/hysteria-linux-arm64"
    "https://github.com/telemt/telemt/releases/download/$telemt_version/telemt-x86_64-linux-gnu.tar.gz"
    "https://github.com/telemt/telemt/releases/download/$telemt_version/telemt-x86_64-linux-musl.tar.gz"
    "https://github.com/telemt/telemt/releases/download/$telemt_version/telemt-aarch64-linux-gnu.tar.gz"
    "https://github.com/telemt/telemt/releases/download/$telemt_version/telemt-aarch64-linux-musl.tar.gz"
)

for url in "${urls[@]}"; do
    printf 'checking %s\n' "$url"
    curl --fail --silent --show-error --location --head \
        --connect-timeout 15 --retry 3 --retry-all-errors --output /dev/null "$url"
done

printf 'release assets: ok\n'
