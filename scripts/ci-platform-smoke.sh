#!/usr/bin/env bash
set -Eeuo pipefail

source /etc/os-release
case "$ID:$VERSION_ID" in
    ubuntu:22.04|ubuntu:24.04|ubuntu:26.04|ubuntu:26.10|debian:12|debian:13) ;;
    *) printf 'Unexpected compatibility image: %s:%s\n' "$ID" "$VERSION_ID" >&2; exit 1 ;;
esac

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl gzip python3 python3-yaml tar
python_bin=python3

"$python_bin" -c 'import sys; assert sys.version_info >= (3, 10), sys.version'
bash -n install.sh
./install.sh --dry-run --non-interactive
PYTHONPATH=src XRAYDO_DATA_DIR=/tmp/xraydo-smoke "$python_bin" -m xraydo.cli --json status
