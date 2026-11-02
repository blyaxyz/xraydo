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
grep -q 'readonly XRAY_VERSION="v26.10.31"' "$ROOT_DIR/install.sh"
grep -q 'readonly TELEMT_VERSION="3.5.16"' "$ROOT_DIR/install.sh"
grep -q 'e4bd9119f50ae7c41658b1abd0679ae06f98bd864a2907ff33f2f2cf7081b214' "$ROOT_DIR/install.sh"
grep -q '1811c8f1da0c3b6dc707ebaf1c8390e987ba39859a8f0a7aadd8526157f2ec8d' "$ROOT_DIR/install.sh"
grep -q 'e6f46b2f3becf29bfd555cec8a059e429f7c082c1af402499b3a7940cf8ebd34' "$ROOT_DIR/install.sh"
grep -q '05d7c32eff5990285657408cf383614d845ecc06aac0d5adc02a86d72c907146' "$ROOT_DIR/install.sh"
grep -q '1e1bc87f05923b9673cbbaf34a3e50f7fdcf7d496cd24efcdb34c278ed18a659' "$ROOT_DIR/install.sh"
grep -q '33d7b2aa55f8852ddcc59e0fa8f5ad9bca3ce94d9fea6665b918e9ae91820ae3' "$ROOT_DIR/install.sh"

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
[[ "$output" == *'Первый пользователь: skipped'* ]]
[[ "$output" == *'"first_user":"skip"'* ]]

first_user_answers="$TEMP_DIR/first-user-answers"
printf '%s\n' \
    'LANGUAGE=en' \
    "PREFIX=$TEMP_DIR/prefix" \
    'PROFILE=advanced' \
    'ROLE=standalone' \
    'CORES=xray,hysteria,telemt' \
    'FIRST_USER_MODE=custom' \
    'FIRST_USER_NAME=alice' \
    'FIRST_USER_LIMITS=custom' \
    'FIRST_USER_TRAFFIC=250GB' \
    'FIRST_USER_DURATION=60d' \
    'FIRST_USER_IPS=3' \
    'PUBLIC_HOST=203.0.113.10' \
    'ACME_EMAIL=ops@example.com' \
    'REALITY_TARGET=ya.ru' \
    'TELEMT_PORT=8443' \
    'TELEMT_TLS_DOMAIN=vk.com' \
    'UTLS_PROFILE=randomizednoalpn' \
    'HYSTERIA_ECH=decoy.example.com' > "$first_user_answers"

first_user_output="$("$ROOT_DIR/install.sh" --answers "$first_user_answers" --dry-run --non-interactive)"
[[ "$first_user_output" == *'First user / Первый пользователь: alice (custom)'* ]]
[[ "$first_user_output" == *'duration=60d, traffic=250GB, devices=1, IP=3'* ]]
[[ "$first_user_output" == *'REALITY uTLS: randomizednoalpn'* ]]
[[ "$first_user_output" == *'REALITY target/SNI: ya.ru'* ]]
[[ "$first_user_output" == *'Telemt: TCP 8443; TLS domain: vk.com'* ]]
[[ "$first_user_output" == *'Hysteria ECH: decoy.example.com'* ]]
[[ "$first_user_output" != *'subscription_token'* ]]

quick_output="$("$ROOT_DIR/install.sh" --profile quick --lang ru --dry-run --non-interactive)"
[[ "$quick_output" == *'Profile / Профиль: quick'* ]]
[[ "$quick_output" == *'Role / Роль: standalone'* ]]
[[ "$quick_output" == *'Cores / Ядра: xray,hysteria,telemt'* ]]
[[ "$quick_output" == *'TLS: public-ip'* ]]
[[ "$quick_output" == *'Первый пользователь: default (recommended)'* ]]
[[ "$quick_output" == *'Limits / Лимиты: unlimited'* ]]
[[ "$quick_output" != *'duration=30d, traffic=100GB'* ]]
[[ "$quick_output" == *'REALITY target/SNI: ya.ru'* ]]
[[ "$quick_output" == *'Telemt: TCP 8443; TLS domain: vk.com'* ]]
[[ "$quick_output" == *'"profile":"quick"'* ]]

telemt_only_output="$("$ROOT_DIR/install.sh" --telemt-only --lang ru \
    --telemt-port 9443 --telemt-tls-domain vk.com --dry-run --non-interactive)"
[[ "$telemt_only_output" == *'Cores / Ядра: telemt'* ]]
[[ "$telemt_only_output" == *'Telemt: TCP 9443; TLS domain: vk.com'* ]]
[[ "$telemt_only_output" == *'TLS: Telemt Fake TLS (vk.com)'* ]]
[[ "$telemt_only_output" == *'Первый пользователь: hello (recommended)'* ]]
[[ "$telemt_only_output" == *'Limits / Лимиты: unlimited'* ]]
[[ "$telemt_only_output" != *'HTTPS subscription: TCP'* ]]
[[ "$telemt_only_output" != *'ACME email:'* ]]
[[ "$telemt_only_output" == *'"cores":"telemt"'* ]]

grep -q 'xraydo tls renew-daemon --interval 3600 --jitter 300' "$ROOT_DIR/install.sh"
grep -q 'enable --now xraydo-tls-renew.service' "$ROOT_DIR/install.sh"
grep -q 'WantedBy=multi-user.target' "$ROOT_DIR/install.sh"
grep -q 'systemctl enable "xraydo-\$core.service"' "$ROOT_DIR/install.sh"
grep -q 'systemctl is-enabled --quiet "xraydo-\$core.service"' "$ROOT_DIR/install.sh"
grep -q 'systemctl restart "xraydo-\$core.service"' "$ROOT_DIR/install.sh"
grep -q 'systemctl restart xraydo-subscription.service' "$ROOT_DIR/install.sh"
grep -q 'subscription HTTPS protocol health-check' "$ROOT_DIR/install.sh"
grep -q 'subscription token protocol health-check' "$ROOT_DIR/install.sh"
grep -q 'user show "\$FIRST_USER_NAME"' "$ROOT_DIR/install.sh"
grep -q 'Base user, subscription content and access keys' "$ROOT_DIR/install.sh"
grep -q 'quiet_root_retry "subscription HTTPS protocol health-check" 12 1' "$ROOT_DIR/install.sh"
grep -q 'settings subscription-url off' "$ROOT_DIR/install.sh"
grep -q 'disable --now xraydo-subscription.service' "$ROOT_DIR/install.sh"
grep -q 'next_free_port_excluding tcp 8444 "\$telemt_port" "\$xray_port"' "$ROOT_DIR/install.sh"
grep -q 'systemctl is-active --quiet "xraydo-\$core.service"' "$ROOT_DIR/install.sh"
grep -q 'rc-update add "xraydo-\$core" default' "$ROOT_DIR/install.sh"
grep -q 'compgen -G.*src/xraydo/data/\*\.json' "$ROOT_DIR/install.sh"
grep -q 'tls renew-daemon --once' "$ROOT_DIR/install.sh"
grep -q 'port_owner 443' "$ROOT_DIR/install.sh"
grep -q 'user create.*FIRST_USER_NAME' "$ROOT_DIR/install.sh"
grep -q 'core reality' "$ROOT_DIR/install.sh"
grep -q -- '--target "\$REALITY_TARGET" --sni "\$REALITY_TARGET"' "$ROOT_DIR/install.sh"
grep -q 'core hysteria' "$ROOT_DIR/install.sh"
grep -q 'core telemt' "$ROOT_DIR/install.sh"
grep -q -- '--tls-domain "\$TELEMT_TLS_DOMAIN"' "$ROOT_DIR/install.sh"
grep -q -- '--telemt-only' "$ROOT_DIR/install.sh"
grep -q 'Продвинутая установка — ручной выбор роли, ядер, TLS и пользователя' "$ROOT_DIR/install.sh"
grep -q 'if \[\[ "$PROFILE" == "quick" \]\]; then' "$ROOT_DIR/install.sh"
grep -q 'ASSUME_YES=true' "$ROOT_DIR/install.sh"
grep -q 'FIRST_USER_LIMITS="unlimited"' "$ROOT_DIR/install.sh"
grep -q 'access_key_id.*--unlimited' "$ROOT_DIR/install.sh"
grep -q 'progress 100 "Готово"' "$ROOT_DIR/install.sh"
grep -q 'QR-код подписки' "$ROOT_DIR/install.sh"
grep -q 'Ключ Hysteria 2' "$ROOT_DIR/install.sh"
grep -q 'Ключ Xray REALITY' "$ROOT_DIR/install.sh"
grep -q 'Telegram Proxy URL' "$ROOT_DIR/install.sh"
grep -q 'Your Telegram Proxy connection link' "$ROOT_DIR/install.sh"
grep -q 'Ссылка для подключения Telegram Proxy' "$ROOT_DIR/install.sh"
grep -q 'protocol.*state.*healthy' "$ROOT_DIR/install.sh"
grep -q 'xraydo-certbot-renew.timer' "$ROOT_DIR/install.sh"
grep -q 'renewal-hooks/deploy' "$ROOT_DIR/install.sh"
grep -q 'hysteria.*ech --public-name' "$ROOT_DIR/install.sh"
grep -q 'subscription qr.*--token-file' "$ROOT_DIR/install.sh"
grep -q 'NON_INTERACTIVE.*-t 0.*-t 1.*-w /dev/tty' "$ROOT_DIR/install.sh"
grep -q 'install -d -m 0755 /var/lib/xraydo' "$ROOT_DIR/install.sh"
grep -q 'stable_installer=.*lib/xraydo/install.sh' "$ROOT_DIR/install.sh"
grep -q 'install -m 0755.*stable_installer' "$ROOT_DIR/install.sh"

health_line="$(grep -n 'Xray version health-check' "$ROOT_DIR/install.sh" | head -n 1 | cut -d: -f1)"
create_line="$(grep -n 'user create.*FIRST_USER_NAME' "$ROOT_DIR/install.sh" | head -n 1 | cut -d: -f1)"
((health_line < create_line))

uninstall_prefix="$TEMP_DIR/uninstall-prefix"
mkdir -p "$uninstall_prefix/bin" "$uninstall_prefix/lib/xraydo" \
    "$uninstall_prefix/share/doc/xraydo" "$uninstall_prefix/etc/xraydo" \
    "$uninstall_prefix/var/lib/xraydo"
touch "$uninstall_prefix/bin/xraydo" "$uninstall_prefix/bin/xray" \
    "$uninstall_prefix/bin/hysteria" "$uninstall_prefix/bin/telemt" \
    "$uninstall_prefix/etc/xraydo/config.json" "$uninstall_prefix/var/lib/xraydo/state.json"
uninstall_output="$("$ROOT_DIR/install.sh" --prefix "$uninstall_prefix" --uninstall --non-interactive --yes)"
[[ "$uninstall_output" == *'configuration and state were preserved'* ]]
[[ ! -e "$uninstall_prefix/bin/xraydo" ]]
[[ ! -e "$uninstall_prefix/lib/xraydo" ]]
[[ -e "$uninstall_prefix/etc/xraydo/config.json" ]]
[[ -e "$uninstall_prefix/var/lib/xraydo/state.json" ]]

purge_output="$("$ROOT_DIR/install.sh" --prefix "$uninstall_prefix" --purge --non-interactive --yes)"
[[ "$purge_output" == *'credentials and cryptographic keys were removed'* ]]
[[ ! -e "$uninstall_prefix/etc/xraydo" ]]
[[ ! -e "$uninstall_prefix/var/lib/xraydo" ]]

dry_prefix="$TEMP_DIR/dry-purge-prefix"
mkdir -p "$dry_prefix/etc/xraydo"
touch "$dry_prefix/etc/xraydo/config.json"
dry_purge_output="$("$ROOT_DIR/install.sh" --prefix "$dry_prefix" --purge --dry-run --non-interactive)"
[[ "$dry_purge_output" == *'"action":"purge"'* ]]
[[ -e "$dry_prefix/etc/xraydo/config.json" ]]

if "$ROOT_DIR/install.sh" --role invalid --dry-run --non-interactive >/dev/null 2>&1; then
    printf 'invalid role was accepted\n' >&2
    exit 1
fi

printf 'installer tests: ok\n'
