#!/usr/bin/env bash
set -Eeuo pipefail

if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends bash ca-certificates curl gzip python3 python3-pip python3-yaml tar
    python_bin=python3
elif command -v apk >/dev/null 2>&1; then
    apk add --no-cache bash ca-certificates curl gzip py3-pip py3-yaml python3 tar
    python_bin=python3
elif command -v dnf >/dev/null 2>&1; then
    if dnf install -y python3.11 python3.11-pip bash ca-certificates curl gzip tar; then
        python_bin=python3.11
    else
        dnf install -y python3 python3-pip bash ca-certificates curl gzip tar
        python_bin=python3
    fi
    "$python_bin" -m pip install --disable-pip-version-check 'PyYAML>=6.0'
elif command -v zypper >/dev/null 2>&1; then
    zypper --non-interactive install bash ca-certificates curl gzip python311 python311-pip tar
    python_bin=python3.11
    "$python_bin" -m pip install --disable-pip-version-check 'PyYAML>=6.0'
else
    printf 'Unsupported package manager in compatibility image\n' >&2
    exit 1
fi

"$python_bin" -c 'import sys; assert sys.version_info >= (3, 9), sys.version'
bash -n install.sh
bash tests/test_install.sh
PYTHONPATH=src "$python_bin" -m unittest discover -s tests -v
./install.sh --dry-run --non-interactive
PYTHONPATH=src XRAYDO_DATA_DIR=/tmp/xraydo-smoke "$python_bin" -m xraydo.cli --json status
