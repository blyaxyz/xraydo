#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/xraydo-installer-test.XXXXXX")"
trap 'rm -rf -- "$TEMP_DIR"' EXIT

uname() {
    case "${1:-}" in
        -s) printf 'Linux\n' ;;
        -m) printf 'x86_64\n' ;;
        *) command uname "$@" ;;
    esac
}
apt-get() { return 0; }
qrencode() { return 0; }
export -f uname apt-get qrencode

[[ "$("$ROOT_DIR/install.sh" --version)" == "0.2.0" ]]

answers="$TEMP_DIR/answers"
printf '%s\n' \
    'LANGUAGE=ru' \
    "PREFIX=$TEMP_DIR/prefix" \
    'PROFILE=node' \
    'ROLE=node' \
    'CORES=xray' \
    'TLS_MODE=private-ca' > "$answers"

output="$("$ROOT_DIR/install.sh" --answers "$answers" --dry-run --non-interactive)"
[[ "$output" == *'"profile":"node"'* ]]
[[ "$output" == *'"role":"node"'* ]]
[[ "$output" == *'"cores":"xray"'* ]]
[[ "$output" == *'Xray REALITY: TCP'* ]]
[[ "$output" != *'Hysteria 2: UDP'* ]]

grep -q 'xraydo tls renew-daemon --interval 3600 --jitter 300' "$ROOT_DIR/install.sh"
grep -q 'enable --now xraydo-tls-renew.service' "$ROOT_DIR/install.sh"
grep -q 'compgen -G.*src/xraydo/data/\*\.json' "$ROOT_DIR/install.sh"
grep -q 'tls renew-daemon --once' "$ROOT_DIR/install.sh"

if "$ROOT_DIR/install.sh" --role invalid --dry-run --non-interactive >/dev/null 2>&1; then
    printf 'invalid role was accepted\n' >&2
    exit 1
fi

printf 'installer tests: ok\n'
