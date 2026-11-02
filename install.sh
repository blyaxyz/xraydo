#!/bin/sh

# Minimal POSIX bootstrap for distributions such as Alpine that do not include
# Bash in the base image. All installer logic below this block runs in Bash.
if [ -z "${BASH_VERSION:-}" ]; then
    if command -v bash >/dev/null 2>&1; then
        exec bash "$0" "$@"
    fi

    if [ "$(id -u)" -eq 0 ]; then
        BOOTSTRAP_ROOT=""
    elif command -v sudo >/dev/null 2>&1; then
        BOOTSTRAP_ROOT="sudo"
    else
        echo "Bash is required; run this installer as root so it can install Bash." >&2
        exit 2
    fi

    echo "Installing Bash / Установка Bash..."
    if command -v apk >/dev/null 2>&1; then
        $BOOTSTRAP_ROOT apk add --no-cache bash
    elif command -v apt-get >/dev/null 2>&1; then
        $BOOTSTRAP_ROOT apt-get update
        $BOOTSTRAP_ROOT apt-get install -y --no-install-recommends bash
    elif command -v dnf >/dev/null 2>&1; then
        $BOOTSTRAP_ROOT dnf install -y bash
    elif command -v yum >/dev/null 2>&1; then
        $BOOTSTRAP_ROOT yum install -y bash
    elif command -v zypper >/dev/null 2>&1; then
        $BOOTSTRAP_ROOT zypper --non-interactive install --no-recommends bash
    else
        echo "Cannot install Bash: supported package manager not found." >&2
        exit 2
    fi
    exec bash "$0" "$@"
fi

set -Eeuo pipefail

# This file is intentionally self-contained. It can be downloaded on its own;
# no checkout of the repository is required.
readonly INSTALLER_VERSION="0.2.0"
readonly XRAYDO_REF="d6563421551fbd4cefa62db1a28d441bacb023ab"
readonly XRAYDO_ARCHIVE_SHA256="4233e7262cf958474b383cd29eed89b5889addcc2f01ba1df47ce870a56fe4ff"
readonly XRAYDO_ARCHIVE_URL="https://github.com/blyaxyz/xraydo/archive/${XRAYDO_REF}.tar.gz"
readonly XRAY_VERSION="v26.10.31"
readonly HYSTERIA_VERSION="app/v2.13.0"
readonly TELEMT_VERSION="3.5.14"

LANGUAGE=""
PREFIX="/usr/local"
DRY_RUN=false
NON_INTERACTIVE=false
ASSUME_YES=false
PROFILE=""
ROLE=""
CORES=""
TLS_MODE="auto"
ANSWER_FILE=""
INSTALL_ACTION=""
FIRST_USER_MODE=""
FIRST_USER_NAME="default"
FIRST_USER_LIMITS="recommended"
FIRST_USER_TRAFFIC="100GB"
FIRST_USER_DURATION="30d"
FIRST_USER_IPS="2"
PUBLIC_HOST=""
ACME_EMAIL=""
REALITY_TARGET="ya.ru"
TELEMT_PORT="8443"
TELEMT_TLS_DOMAIN="vk.com"
TELEMT_ONLY=false
UTLS_PROFILE="firefox"
HYSTERIA_ECH="off"
PACKAGE_MANAGER=""
INIT_SYSTEM="none"
ARCH=""
OS_ID="linux"
OS_VERSION="unknown"
TEMP_DIR=""
SOURCE_DIR=""
INSTALL_WITH_ROOT=false

usage() {
    cat <<'EOF'
Usage: ./install.sh [options]

Downloads and installs everything required to run xraydo. The repository does
not need to be cloned beforehand.

Options:
  --lang en|ru          Installer and default xraydo language
  --prefix PATH         Installation prefix (default: /usr/local)
  --profile NAME        quick, master, node, or advanced
  --role ROLE           standalone, master, or node
  --cores LIST          Comma list: xray,hysteria,telemt (default: all)
  --answers FILE        Read non-secret KEY=VALUE answers
  --update              Reinstall the pinned release, preserving config/state
  --uninstall           Remove programs/services but preserve config and state
  --purge               Fully remove programs, config, state, keys, and users
  --first-user MODE     recommended, custom, or skip
  --user-name NAME      First user name (default: default)
  --limits PROFILE      recommended, unlimited, or custom
  --public-host HOST    Public IPv4/IPv6 or DNS name for client links
  --acme-email EMAIL    Let's Encrypt account email (recommended)
  --reality-target HOST REALITY target and SNI (default: ya.ru)
  --telemt-port PORT    Telemt public port (default: 8443)
  --telemt-tls-domain D Fake TLS domain for Telemt (default: vk.com)
  --telemt-only         Short Telemt setup: language, port, TLS domain, link
  --utls PROFILE        REALITY uTLS profile (default: firefox)
  --hysteria-ech NAME   Generate ECH keys with this public/decoy name
  --dry-run             Print the complete plan without changing the system
  --non-interactive     Disable prompts (requires --yes to install)
  --yes                 Accept the final installation plan
  --version             Print installer version
  -h, --help            Show this help
EOF
}

fail() {
    printf 'Error: %s\n' "$1" >&2
    exit 2
}

cleanup() {
    if [[ -n "$TEMP_DIR" && -d "$TEMP_DIR" ]]; then
        rm -rf -- "$TEMP_DIR"
    fi
}
trap cleanup EXIT

load_answers() {
    local path="$1"
    local key value
    [[ -r "$path" ]] || fail "answer file is not readable: $path"
    while IFS='=' read -r key value; do
        [[ -z "$key" || "$key" == \#* ]] && continue
        case "$key" in
            LANGUAGE) LANGUAGE="$value" ;;
            PREFIX) PREFIX="$value" ;;
            PROFILE) PROFILE="$value" ;;
            ROLE) ROLE="$value" ;;
            CORES) CORES="$value" ;;
            TLS_MODE) TLS_MODE="$value" ;;
            FIRST_USER_MODE) FIRST_USER_MODE="$value" ;;
            FIRST_USER_NAME) FIRST_USER_NAME="$value" ;;
            FIRST_USER_LIMITS) FIRST_USER_LIMITS="$value" ;;
            FIRST_USER_TRAFFIC) FIRST_USER_TRAFFIC="$value" ;;
            FIRST_USER_DURATION) FIRST_USER_DURATION="$value" ;;
            FIRST_USER_IPS) FIRST_USER_IPS="$value" ;;
            PUBLIC_HOST) PUBLIC_HOST="$value" ;;
            ACME_EMAIL) ACME_EMAIL="$value" ;;
            REALITY_TARGET) REALITY_TARGET="$value" ;;
            TELEMT_PORT) TELEMT_PORT="$value" ;;
            TELEMT_TLS_DOMAIN) TELEMT_TLS_DOMAIN="$value" ;;
            UTLS_PROFILE) UTLS_PROFILE="$value" ;;
            HYSTERIA_ECH) HYSTERIA_ECH="$value" ;;
            *) fail "unknown answer-file key: $key" ;;
        esac
    done < "$path"
}

while (($#)); do
    case "$1" in
        --lang)
            (($# >= 2)) || fail "--lang requires en or ru"
            LANGUAGE="$2"
            shift 2
            ;;
        --prefix)
            (($# >= 2)) || fail "--prefix requires an absolute path"
            PREFIX="$2"
            shift 2
            ;;
        --profile)
            (($# >= 2)) || fail "--profile requires quick, master, node, or advanced"
            PROFILE="$2"
            shift 2
            ;;
        --role)
            (($# >= 2)) || fail "--role requires standalone, master, or node"
            ROLE="$2"
            shift 2
            ;;
        --cores)
            (($# >= 2)) || fail "--cores requires a comma-separated list"
            CORES="$2"
            shift 2
            ;;
        --answers)
            (($# >= 2)) || fail "--answers requires a file"
            ANSWER_FILE="$2"
            load_answers "$ANSWER_FILE"
            shift 2
            ;;
        --update)
            [[ -z "$INSTALL_ACTION" || "$INSTALL_ACTION" == "install" ]] || fail "choose only one installer action"
            INSTALL_ACTION="install"; shift ;;
        --uninstall)
            [[ -z "$INSTALL_ACTION" || "$INSTALL_ACTION" == "uninstall" ]] || fail "choose only one removal action"
            INSTALL_ACTION="uninstall"; shift ;;
        --purge)
            [[ -z "$INSTALL_ACTION" || "$INSTALL_ACTION" == "purge" ]] || fail "choose only one removal action"
            INSTALL_ACTION="purge"; shift ;;
        --first-user)
            (($# >= 2)) || fail "--first-user requires recommended, custom, or skip"
            FIRST_USER_MODE="$2"; shift 2 ;;
        --user-name)
            (($# >= 2)) || fail "--user-name requires a value"
            FIRST_USER_NAME="$2"; shift 2 ;;
        --limits)
            (($# >= 2)) || fail "--limits requires recommended, unlimited, or custom"
            FIRST_USER_LIMITS="$2"; shift 2 ;;
        --public-host)
            (($# >= 2)) || fail "--public-host requires a value"
            PUBLIC_HOST="$2"; shift 2 ;;
        --acme-email)
            (($# >= 2)) || fail "--acme-email requires a value"
            ACME_EMAIL="$2"; shift 2 ;;
        --reality-target)
            (($# >= 2)) || fail "--reality-target requires a host"
            REALITY_TARGET="$2"; shift 2 ;;
        --telemt-port)
            (($# >= 2)) || fail "--telemt-port requires a value"
            TELEMT_PORT="$2"; shift 2 ;;
        --telemt-tls-domain)
            (($# >= 2)) || fail "--telemt-tls-domain requires a domain"
            TELEMT_TLS_DOMAIN="$2"; shift 2 ;;
        --telemt-only) TELEMT_ONLY=true; shift ;;
        --utls)
            (($# >= 2)) || fail "--utls requires a profile"
            UTLS_PROFILE="$2"; shift 2 ;;
        --hysteria-ech)
            (($# >= 2)) || fail "--hysteria-ech requires a public name"
            HYSTERIA_ECH="$2"; shift 2 ;;
        --dry-run) DRY_RUN=true; shift ;;
        --non-interactive) NON_INTERACTIVE=true; shift ;;
        --yes) ASSUME_YES=true; shift ;;
        --version) printf '%s\n' "$INSTALLER_VERSION"; exit 0 ;;
        -h|--help) usage; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
done

[[ -z "$LANGUAGE" || "$LANGUAGE" == "en" || "$LANGUAGE" == "ru" ]] || \
    fail "unsupported language: $LANGUAGE"
[[ "$PREFIX" == /* ]] || fail "--prefix must be an absolute path"
[[ "$PREFIX" != "/" ]] || fail "--prefix cannot be the filesystem root"
PREFIX="${PREFIX%/}"
[[ -z "$PROFILE" || "$PROFILE" =~ ^(quick|master|node|advanced)$ ]] || fail "unsupported profile: $PROFILE"
[[ -z "$ROLE" || "$ROLE" =~ ^(standalone|master|node)$ ]] || fail "unsupported role: $ROLE"
[[ -z "$CORES" || "$CORES" =~ ^(none|((xray|hysteria|telemt)(,(xray|hysteria|telemt))*))$ ]] || fail "--cores accepts xray,hysteria,telemt or none"
[[ "$TLS_MODE" =~ ^(auto|domain|public-ip|private-ca)$ ]] || fail "unsupported TLS mode: $TLS_MODE"
[[ -z "$FIRST_USER_MODE" || "$FIRST_USER_MODE" =~ ^(recommended|custom|skip)$ ]] || fail "unsupported first-user mode: $FIRST_USER_MODE"
[[ "$FIRST_USER_LIMITS" =~ ^(recommended|unlimited|custom)$ ]] || fail "unsupported limits profile: $FIRST_USER_LIMITS"
[[ "$FIRST_USER_NAME" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || fail "first user name must contain 1-64 safe characters"
if [[ ! "$FIRST_USER_IPS" =~ ^[0-9]+$ ]] || ((FIRST_USER_IPS > 65535)); then
    fail "FIRST_USER_IPS must be 0..65535"
fi
[[ "$UTLS_PROFILE" =~ ^(firefox|chrome|safari|ios|android|edge|360|qq|random|randomized|randomizednoalpn)$ ]] || fail "unsupported uTLS profile: $UTLS_PROFILE"
[[ "$HYSTERIA_ECH" == "off" || "$HYSTERIA_ECH" =~ ^[A-Za-z0-9.-]{1,253}$ ]] || fail "invalid Hysteria ECH public name"
[[ -z "$PUBLIC_HOST" || "$PUBLIC_HOST" =~ ^[A-Za-z0-9.:-]{1,253}$ ]] || fail "invalid public host"
[[ -z "$ACME_EMAIL" || "$ACME_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$ ]] || fail "invalid ACME email"
[[ "$REALITY_TARGET" =~ ^[A-Za-z0-9.-]{1,253}$ ]] || fail "invalid REALITY target"
[[ "$TELEMT_TLS_DOMAIN" =~ ^[A-Za-z0-9.-]{1,253}$ ]] || fail "invalid Telemt TLS domain"
[[ "$TELEMT_PORT" =~ ^[0-9]+$ ]] && ((TELEMT_PORT >= 1 && TELEMT_PORT <= 65535)) || fail "invalid Telemt port"
[[ -z "$INSTALL_ACTION" || "$INSTALL_ACTION" =~ ^(install|uninstall|purge)$ ]] || fail "invalid installer action"
[[ "$(uname -s)" == "Linux" ]] || fail "only Linux is supported"

if [[ -z "$LANGUAGE" ]]; then
    if [[ "${XRAYDO_LANG:-}" == "en" || "${XRAYDO_LANG:-}" == "ru" ]]; then
        LANGUAGE="$XRAYDO_LANG"
    elif [[ "$NON_INTERACTIVE" == true || ! -t 0 ]]; then
        LANGUAGE="en"
    else
        printf '%s\n' \
            'Select language / Выберите язык:' '' \
            '  1) English (default)' \
            '  2) Русский' ''
        read -r -p 'Choice / Выбор [1]: ' language_choice
        [[ "$language_choice" == "2" ]] && LANGUAGE="ru" || LANGUAGE="en"
    fi
fi

if [[ "$TELEMT_ONLY" == true ]]; then
    INSTALL_ACTION="${INSTALL_ACTION:-install}"
    PROFILE="quick"
    ROLE="standalone"
    CORES="telemt"
    TLS_MODE="private-ca"
    FIRST_USER_MODE="recommended"
    FIRST_USER_NAME="hello"
    FIRST_USER_LIMITS="unlimited"
    ASSUME_YES=true
fi

root_exec() {
    if ((EUID == 0)); then
        "$@"
    else
        command -v sudo >/dev/null 2>&1 || fail "root privileges are required; run as root or install sudo"
        sudo "$@"
    fi
}

remove_exec() {
    case "$PREFIX" in
        /usr|/usr/*|/opt|/opt/*) root_exec "$@" ;;
        *) "$@" ;;
    esac
}

safe_remove_tree() {
    local target="$1"
    case "$target" in
        ""|/|/usr|/usr/local|/opt|/var|/var/lib|/var/log) fail "refusing unsafe removal target: $target" ;;
        */xraydo|*/xraydo/*) remove_exec rm -rf -- "$target" ;;
        *) fail "refusing unexpected removal target: $target" ;;
    esac
}

remove_service_units() {
    local service
    local services=(xraydo-xray xraydo-hysteria xraydo-telemt xraydo-tls-renew xraydo-certbot-renew xraydo-subscription)
    [[ "$PREFIX" == "/usr/local" ]] || return 0
    if command -v systemctl >/dev/null 2>&1; then
        for service in "${services[@]}"; do
            root_exec systemctl disable --now "$service.service" >/dev/null 2>&1 || true
        done
    fi
    if command -v rc-service >/dev/null 2>&1; then
        for service in "${services[@]}"; do
            root_exec rc-service "$service" stop >/dev/null 2>&1 || true
            root_exec rc-update del "$service" default >/dev/null 2>&1 || true
        done
    fi
    for service in "${services[@]}"; do
        root_exec rm -f -- "/etc/systemd/system/$service.service" \
            "/etc/systemd/system/multi-user.target.wants/$service.service" \
            "/etc/init.d/$service"
    done
    if command -v systemctl >/dev/null 2>&1; then
        root_exec systemctl daemon-reload >/dev/null 2>&1 || true
        root_exec systemctl reset-failed >/dev/null 2>&1 || true
    fi
}

remove_system_users() {
    local account
    [[ "$PREFIX" == "/usr/local" ]] || return 0
    for account in xraydo-xray xraydo-hysteria xraydo-telemt; do
        id -u "$account" >/dev/null 2>&1 || continue
        if command -v userdel >/dev/null 2>&1; then
            root_exec userdel "$account"
        elif command -v deluser >/dev/null 2>&1; then
            root_exec deluser "$account"
        else
            printf 'Warning: cannot remove system account %s (userdel/deluser unavailable).\n' "$account" >&2
        fi
    done
}

remove_xraydo() {
    local action="$1"
    local data_dir="$PREFIX/var/lib/xraydo"
    [[ "$PREFIX" == "/usr/local" ]] && data_dir="/var/lib/xraydo"
    local confirmation expected
    local purge=false
    [[ "$action" == "purge" ]] && purge=true

    printf '\nRemoval plan / План удаления:\n'
    printf '  action / действие: %s\n  prefix: %s\n' "$action" "$PREFIX"
    printf '  programs: %s/bin/{xraydo,xray,hysteria,telemt}, %s/lib/xraydo\n' "$PREFIX" "$PREFIX"
    printf '  services: xraydo-*\n'
    printf '  system packages / системные пакеты: preserved (they may be shared)\n'
    if [[ "$purge" == true ]]; then
        printf '  PERMANENTLY DELETE / УДАЛИТЬ БЕЗВОЗВРАТНО:\n'
        printf '    config: %s/etc/xraydo\n    state, users, TLS/private keys: %s\n' "$PREFIX" "$data_dir"
        [[ "$PREFIX" == "/usr/local" ]] && printf '    logs: /var/log/xraydo; system users: xraydo-*\n'
        expected="PURGE xraydo"
    else
        printf '  preserve / сохранить: %s/etc/xraydo and %s\n' "$PREFIX" "$data_dir"
        expected="UNINSTALL xraydo"
    fi

    if [[ "$DRY_RUN" == true ]]; then
        printf '{"action":"%s","dry_run":true,"prefix":"%s","state":"%s"}\n' "$action" "$PREFIX" "$data_dir"
        return 0
    fi
    if [[ "$ASSUME_YES" != true ]]; then
        [[ "$NON_INTERACTIVE" != true && -t 0 ]] || fail "use --yes with --$action in non-interactive mode"
        read -r -p "Type '$expected' to continue / Введите '$expected': " confirmation
        [[ "$confirmation" == "$expected" ]] || fail "removal confirmation did not match"
    fi

    remove_service_units
    remove_exec rm -f -- "$PREFIX/bin/xraydo" "$PREFIX/bin/xray" "$PREFIX/bin/hysteria" "$PREFIX/bin/telemt"
    safe_remove_tree "$PREFIX/lib/xraydo"
    safe_remove_tree "$PREFIX/share/doc/xraydo"
    if [[ "$purge" == true ]]; then
        safe_remove_tree "$PREFIX/etc/xraydo"
        safe_remove_tree "$data_dir"
        if [[ "$PREFIX" == "/usr/local" ]]; then
            safe_remove_tree "/var/log/xraydo"
        fi
        remove_system_users
        printf 'xraydo fully purged; configuration, state, credentials and cryptographic keys were removed.\n'
        printf 'Physical secure-erasure guarantees depend on the storage/filesystem.\n'
    else
        printf 'xraydo uninstalled; configuration and state were preserved.\n'
        printf 'Run this script with --purge to remove preserved credentials and keys.\n'
    fi
}

if [[ -z "$INSTALL_ACTION" ]]; then
    if [[ "$NON_INTERACTIVE" == true || ! -t 0 ]]; then
        INSTALL_ACTION="install"
    else
        if [[ "$LANGUAGE" == "ru" ]]; then
            printf '\nДействие:\n  1) Установить или обновить (рекомендуется)\n  2) Удалить программу, сохранив конфигурацию и данные\n  3) Полное удаление (purge): данные, пользователи, credentials и ключи\n  q) Отмена\n'
            read -r -p 'Выбор [1]: ' action_choice
        else
            printf '\nAction:\n  1) Install or update (recommended)\n  2) Uninstall programs and preserve configuration/state\n  3) Full purge: data, users, credentials, and keys\n  q) Cancel\n'
            read -r -p 'Choice [1]: ' action_choice
        fi
        case "${action_choice:-1}" in
            1) INSTALL_ACTION="install" ;;
            2) INSTALL_ACTION="uninstall" ;;
            3) INSTALL_ACTION="purge" ;;
            q|Q) printf 'Cancelled / Отменено.\n'; exit 0 ;;
            *) fail "invalid action choice" ;;
        esac
    fi
fi

if [[ "$INSTALL_ACTION" == "uninstall" || "$INSTALL_ACTION" == "purge" ]]; then
    remove_xraydo "$INSTALL_ACTION"
    exit 0
fi

case "$(uname -m)" in
    x86_64|amd64) ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) fail "unsupported architecture: $(uname -m)" ;;
esac

if [[ -r /etc/os-release ]]; then
    while IFS='=' read -r key value; do
        value="${value%\"}"
        value="${value#\"}"
        case "$key" in
            ID) OS_ID="$value" ;;
            VERSION_ID) OS_VERSION="$value" ;;
        esac
    done < /etc/os-release
fi

for candidate in apt-get dnf yum zypper apk; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PACKAGE_MANAGER="$candidate"
        break
    fi
done
[[ -n "$PACKAGE_MANAGER" ]] || fail "supported package manager not found (apt, dnf, yum, zypper, apk)"

if [[ -z "$PROFILE" && "$NON_INTERACTIVE" != true && -t 0 ]]; then
    while true; do
        if [[ "$LANGUAGE" == "ru" ]]; then
            printf '\n%s\n' \
                'Выберите сценарий:' \
                '  1) Быстрая установка (рекомендуется) — один сервер, безопасные defaults' \
                '  2) Главная нода — централизованное управление пользователями и нодами' \
                '  3) Управляемая нода — подключение к существующей главной ноде' \
                '  4) Расширенная настройка — ручной выбор роли, ядер и TLS' \
                '  q) Отмена'
            read -r -p 'Выбор [1]: ' profile_choice
        else
            printf '\n%s\n' \
                'Select installation profile:' \
                '  1) Quick install (recommended) — one server with safe defaults' \
                '  2) Master node — centralized user and node management' \
                '  3) Managed node — attach to an existing master' \
                '  4) Advanced setup — choose role, cores, and TLS manually' \
                '  q) Cancel'
            read -r -p 'Choice [1]: ' profile_choice
        fi
        case "${profile_choice:-1}" in
            1) PROFILE="quick"; break ;;
            2) PROFILE="master"; break ;;
            3) PROFILE="node"; break ;;
            4) PROFILE="advanced"; break ;;
            q|Q) printf '%s\n' "Cancelled / Отменено."; exit 0 ;;
        esac
    done
fi

[[ -n "$PROFILE" ]] || PROFILE="quick"

if [[ -z "$ROLE" ]]; then
    case "$PROFILE" in
        quick) ROLE="standalone" ;;
        master) ROLE="master" ;;
        node) ROLE="node" ;;
        advanced)
            if [[ "$NON_INTERACTIVE" == true || ! -t 0 ]]; then
                ROLE="standalone"
            else
                if [[ "$LANGUAGE" == "ru" ]]; then
                    printf '\nРоль сервера:\n  1) standalone (рекомендуется)\n  2) master\n  3) node\n'
                    read -r -p 'Выбор [1]: ' role_choice
                else
                    printf '\nServer role:\n  1) standalone (recommended)\n  2) master\n  3) node\n'
                    read -r -p 'Choice [1]: ' role_choice
                fi
                case "${role_choice:-1}" in
                    1) ROLE="standalone" ;;
                    2) ROLE="master" ;;
                    3) ROLE="node" ;;
                    *) fail "invalid role choice" ;;
                esac
            fi
            ;;
    esac
fi

if [[ -z "$CORES" ]]; then
    if [[ "$PROFILE" == "advanced" && "$NON_INTERACTIVE" != true && -t 0 ]]; then
        if [[ "$LANGUAGE" == "ru" ]]; then
            printf '\nЯдра:\n  1) Все три (рекомендуется)\n  2) Только Xray-core\n  3) Xray-core + Hysteria 2\n  4) Не устанавливать ядра\n'
            read -r -p 'Выбор [1]: ' cores_choice
        else
            printf '\nCores:\n  1) All three (recommended)\n  2) Xray-core only\n  3) Xray-core + Hysteria 2\n  4) Do not install cores\n'
            read -r -p 'Choice [1]: ' cores_choice
        fi
        case "${cores_choice:-1}" in
            1) CORES="xray,hysteria,telemt" ;;
            2) CORES="xray" ;;
            3) CORES="xray,hysteria" ;;
            4) CORES="none" ;;
            *) fail "invalid cores choice" ;;
        esac
    else
        CORES="xray,hysteria,telemt"
    fi
fi

if [[ "$PROFILE" == "advanced" && "$NON_INTERACTIVE" != true && -t 0 ]]; then
    if [[ "$LANGUAGE" == "ru" ]]; then
        printf '\nTLS подписок:\n  1) Автоматически (рекомендуется)\n  2) Публичный домен\n  3) Публичный IP-сертификат\n  4) Автономный private CA\n'
        read -r -p 'Выбор [1]: ' tls_choice
    else
        printf '\nSubscription TLS:\n  1) Automatic (recommended)\n  2) Public domain\n  3) Public IP certificate\n  4) Offline private CA\n'
        read -r -p 'Choice [1]: ' tls_choice
    fi
    case "${tls_choice:-1}" in
        1) TLS_MODE="auto" ;;
        2) TLS_MODE="domain" ;;
        3) TLS_MODE="public-ip" ;;
        4) TLS_MODE="private-ca" ;;
        *) fail "invalid TLS choice" ;;
    esac
fi

if [[ "$ROLE" != "node" && "$TLS_MODE" != "private-ca" && "$NON_INTERACTIVE" != true && -t 0 ]]; then
    if [[ -z "$PUBLIC_HOST" ]]; then
        if [[ "$LANGUAGE" == "ru" ]]; then
            read -r -p 'Домен для Let’s Encrypt (Enter = сертификат на публичный IP): ' PUBLIC_HOST
        else
            read -r -p "Let's Encrypt domain (Enter = certificate for the public IP): " PUBLIC_HOST
        fi
    fi
    if [[ -n "$PUBLIC_HOST" && ! "$PUBLIC_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && "$PUBLIC_HOST" != *:* ]]; then
        TLS_MODE="domain"
    else
        TLS_MODE="public-ip"
    fi
    if [[ -z "$ACME_EMAIL" ]]; then
        read -r -p "Let's Encrypt email / Email Let's Encrypt (optional): " ACME_EMAIL
    fi
fi

if [[ "$TLS_MODE" == "auto" ]]; then
    if [[ -n "$PUBLIC_HOST" && ! "$PUBLIC_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ && "$PUBLIC_HOST" != *:* ]]; then
        TLS_MODE="domain"
    else
        TLS_MODE="public-ip"
    fi
fi

if [[ ",$CORES," == *,telemt,* && "$NON_INTERACTIVE" != true && -t 0 ]]; then
    read -r -p "Telemt port / Порт Telemt [$TELEMT_PORT]: " custom_telemt_port
    read -r -p "Telemt TLS domain / TLS-домен Telemt [$TELEMT_TLS_DOMAIN]: " custom_telemt_domain
    TELEMT_PORT="${custom_telemt_port:-$TELEMT_PORT}"
    TELEMT_TLS_DOMAIN="${custom_telemt_domain:-$TELEMT_TLS_DOMAIN}"
    [[ "$TELEMT_PORT" =~ ^[0-9]+$ ]] && ((TELEMT_PORT >= 1 && TELEMT_PORT <= 65535)) || fail "invalid Telemt port"
    [[ "$TELEMT_TLS_DOMAIN" =~ ^[A-Za-z0-9.-]{1,253}$ ]] || fail "invalid Telemt TLS domain"
fi

if [[ -z "$FIRST_USER_MODE" ]]; then
    if [[ "$ROLE" == "node" ]]; then
        FIRST_USER_MODE="skip"
    elif [[ "$NON_INTERACTIVE" == true || ! -t 0 ]]; then
        FIRST_USER_MODE="recommended"
    else
        if [[ "$LANGUAGE" == "ru" ]]; then
            printf '\n%s\n' \
                'Создать первого пользователя?' \
                '  1) Да, рекомендуемый профиль (default, 30 дней, 100 GB, 2 IP)' \
                '  2) Да, настроить имя, лимиты и стартовые протоколы' \
                '  3) Нет, создать позже через xraydo'
            read -r -p 'Выбор [1]: ' first_user_choice
        else
            printf '\n%s\n' \
                'Create the first user?' \
                '  1) Yes, recommended profile (default, 30 days, 100 GB, 2 IPs)' \
                '  2) Yes, configure name, limits, and initial protocols' \
                '  3) No, create later with xraydo'
            read -r -p 'Choice [1]: ' first_user_choice
        fi
        case "${first_user_choice:-1}" in
            1) FIRST_USER_MODE="recommended" ;;
            2) FIRST_USER_MODE="custom" ;;
            3) FIRST_USER_MODE="skip" ;;
            *) fail "invalid first-user choice" ;;
        esac
    fi
fi

if [[ "$FIRST_USER_MODE" == "custom" && "$NON_INTERACTIVE" != true && -t 0 ]]; then
    read -r -p "First user name / Имя первого пользователя [default]: " custom_user_name
    FIRST_USER_NAME="${custom_user_name:-default}"
    [[ "$FIRST_USER_NAME" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || fail "invalid first user name"
    printf '%s\n' \
        'Limits / Лимиты:' \
        '  1) Recommended: 30d, 100GB, 1 device, 2 IPs' \
        '  2) Unlimited' \
        '  3) Custom traffic, duration and IP count'
    read -r -p 'Choice / Выбор [1]: ' limits_choice
    case "${limits_choice:-1}" in
        1) FIRST_USER_LIMITS="recommended" ;;
        2) FIRST_USER_LIMITS="unlimited" ;;
        3)
            FIRST_USER_LIMITS="custom"
            read -r -p 'Traffic / Трафик [100GB]: ' custom_traffic
            read -r -p 'Duration / Срок [30d]: ' custom_duration
            read -r -p 'IP limit / Лимит IP [2]: ' custom_ips
            FIRST_USER_TRAFFIC="${custom_traffic:-100GB}"
            FIRST_USER_DURATION="${custom_duration:-30d}"
            FIRST_USER_IPS="${custom_ips:-2}"
            ;;
        *) fail "invalid limits choice" ;;
    esac
    if [[ ",$CORES," == *,xray,* ]]; then
        printf '%s\n' \
            'REALITY uTLS fixed profiles: firefox (recommended), chrome, safari, ios, android, edge, 360, qq' \
            'Randomizers: random selects a browser profile; randomized creates a randomized ClientHello with ALPN;' \
            'randomizednoalpn removes ALPN and may be incompatible with the selected target/client.'
        read -r -p 'uTLS [firefox]: ' custom_utls
        UTLS_PROFILE="${custom_utls:-firefox}"
    fi
    if [[ ",$CORES," == *,hysteria,* ]]; then
        read -r -p 'ECH public/decoy name (blank = disabled; enable only for supporting clients; useful mainly without obfs): ' custom_ech
        HYSTERIA_ECH="${custom_ech:-off}"
    fi
fi

[[ "$FIRST_USER_NAME" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || fail "invalid first user name"
[[ "$FIRST_USER_TRAFFIC" =~ ^[1-9][0-9]*(B|KB|MB|GB|TB)$ ]] || fail "invalid first-user traffic limit"
[[ "$FIRST_USER_DURATION" =~ ^[1-9][0-9]*(s|m|h|d|w)$ ]] || fail "invalid first-user duration"
if [[ ! "$FIRST_USER_IPS" =~ ^[0-9]+$ ]] || ((FIRST_USER_IPS > 65535)); then
    fail "invalid first-user IP limit"
fi
[[ "$UTLS_PROFILE" =~ ^(firefox|chrome|safari|ios|android|edge|360|qq|random|randomized|randomizednoalpn)$ ]] || fail "invalid uTLS profile"

if [[ "$LANGUAGE" == "ru" ]]; then
    PLAN_TITLE="План быстрой установки"
    PLATFORM_LABEL="Система"
    PACKAGES_LABEL="Системные пакеты"
    SOURCE_LABEL="Исходный код"
    PREFIX_LABEL="Путь установки"
    CORES_LABEL="Ядра"
    CONFIRM_TEXT="Скачать и установить? [Enter = да, q = отмена]: "
    CANCEL_TEXT="Установка отменена."
    DOWNLOAD_TEXT="Загрузка проверенного xraydo..."
    INSTALL_TEXT="Установка xraydo..."
    DONE_TEXT="xraydo установлен и готов к работе."
else
    PLAN_TITLE="Quick installation plan"
    PLATFORM_LABEL="System"
    PACKAGES_LABEL="System packages"
    SOURCE_LABEL="Source"
    PREFIX_LABEL="Installation prefix"
    CORES_LABEL="Cores"
    CONFIRM_TEXT="Download and install? [Enter = yes, q = cancel]: "
    CANCEL_TEXT="Installation cancelled."
    DOWNLOAD_TEXT="Downloading verified xraydo..."
    INSTALL_TEXT="Installing xraydo..."
    DONE_TEXT="xraydo is installed and ready."
fi

declare -a REQUIRED_PACKAGES=()
command -v python3 >/dev/null 2>&1 || REQUIRED_PACKAGES+=(python3)
if ! python3 -c 'import yaml' >/dev/null 2>&1; then
    case "$PACKAGE_MANAGER" in
        apt-get) REQUIRED_PACKAGES+=(python3-yaml) ;;
        dnf|yum) REQUIRED_PACKAGES+=(python3-pyyaml) ;;
        zypper) REQUIRED_PACKAGES+=(python3-PyYAML) ;;
        apk) REQUIRED_PACKAGES+=(py3-yaml) ;;
    esac
fi
command -v curl >/dev/null 2>&1 || REQUIRED_PACKAGES+=(curl)
command -v tar >/dev/null 2>&1 || REQUIRED_PACKAGES+=(tar)
if ! command -v qrencode >/dev/null 2>&1; then
    if [[ "$PACKAGE_MANAGER" == "apk" ]]; then
        REQUIRED_PACKAGES+=(libqrencode-tools)
    else
        REQUIRED_PACKAGES+=(qrencode)
    fi
fi
if ! command -v openssl >/dev/null 2>&1; then
    REQUIRED_PACKAGES+=(openssl)
fi
if [[ ",$CORES," == *,xray,* ]] && ! command -v unzip >/dev/null 2>&1; then
    REQUIRED_PACKAGES+=(unzip)
fi
if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
    REQUIRED_PACKAGES+=(coreutils)
fi
if [[ ! -d /etc/ssl/certs ]]; then
    REQUIRED_PACKAGES+=(ca-certificates)
fi
certbot_ip_capable() {
    local binary="${1:-certbot}" version major minor
    [[ "$binary" == */* ]] || binary="$(command -v "$binary" 2>/dev/null || true)"
    [[ -n "$binary" && -x "$binary" ]] || return 1
    version="$($binary --version 2>&1 | awk '{print $NF}' | head -n 1)"
    major="${version%%.*}"
    minor="${version#*.}"; minor="${minor%%.*}"
    [[ "$major" =~ ^[0-9]+$ && "$minor" =~ ^[0-9]+$ ]] || return 1
    ((major > 5 || (major == 5 && minor >= 4)))
}

CERTBOT_PATH="$(command -v certbot 2>/dev/null || true)"
if [[ "$TLS_MODE" == "domain" && -z "$CERTBOT_PATH" ]]; then
    REQUIRED_PACKAGES+=(certbot)
elif [[ "$TLS_MODE" == "public-ip" ]] && ! certbot_ip_capable "${CERTBOT_PATH:-certbot}"; then
    [[ "$PACKAGE_MANAGER" != "apk" ]] || fail "public IP certificates require Certbot 5.4+; snap is unavailable on Alpine"
    command -v snap >/dev/null 2>&1 || REQUIRED_PACKAGES+=(snapd)
fi

packages_display="none"
if ((${#REQUIRED_PACKAGES[@]})); then
    packages_display="${REQUIRED_PACKAGES[*]}"
fi

if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    INIT_SYSTEM="systemd"
elif command -v rc-service >/dev/null 2>&1; then
    INIT_SYSTEM="openrc"
fi

security_modules="none"
if command -v getenforce >/dev/null 2>&1; then
    security_modules="SELinux:$(getenforce 2>/dev/null || printf unknown)"
fi
if [[ -d /sys/module/apparmor ]]; then
    [[ "$security_modules" == "none" ]] && security_modules="AppArmor" || security_modules="$security_modules,AppArmor"
fi

memory_mb=0
if [[ -r /proc/meminfo ]]; then
    while read -r memory_key memory_kb _; do
        if [[ "$memory_key" == "MemAvailable:" ]]; then
            memory_mb=$((memory_kb / 1024))
            break
        fi
    done < /proc/meminfo
fi

disk_probe="$PREFIX"
while [[ ! -e "$disk_probe" && "$disk_probe" != "/" ]]; do
    disk_probe="${disk_probe%/*}"
    [[ -n "$disk_probe" ]] || disk_probe="/"
done
disk_available_kb="$(df -Pk "$disk_probe" 2>/dev/null | awk 'NR==2 {print $4}')"
disk_available_mb=$(( ${disk_available_kb:-0} / 1024 ))
((disk_available_mb >= 200)) || fail "at least 200 MB of free disk space is required"
if ((memory_mb > 0 && memory_mb < 128)); then
    fail "at least 128 MB of available RAM is required"
fi

port_in_use() {
    local protocol="$1"
    local port="$2"
    local hex_port
    printf -v hex_port '%04X' "$port"
    if command -v ss >/dev/null 2>&1; then
        if [[ "$protocol" == "tcp" ]]; then
            ss -H -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${port}$"
        else
            ss -H -lun 2>/dev/null | awk '{print $5}' | grep -Eq "[:.]${port}$"
        fi
    elif [[ "$protocol" == "tcp" ]]; then
        awk -v port="$hex_port" '$2 ~ (":" port "$") && $4 == "0A" {found=1} END {exit !found}' \
            /proc/net/tcp /proc/net/tcp6 2>/dev/null
    else
        awk -v port="$hex_port" '$2 ~ (":" port "$") && $4 == "07" {found=1} END {exit !found}' \
            /proc/net/udp /proc/net/udp6 2>/dev/null
    fi
}

next_free_port() {
    local protocol="$1"
    local candidate="$2"
    while port_in_use "$protocol" "$candidate"; do
        candidate=$((candidate + 1))
        ((candidate <= 65535)) || fail "no free $protocol port found"
    done
    printf '%s' "$candidate"
}

next_free_port_excluding() {
    local protocol="$1"
    local candidate="$2"
    shift 2
    local reserved reserved_port
    while true; do
        reserved=false
        for reserved_port in "$@"; do
            if [[ "$candidate" == "$reserved_port" ]]; then
                reserved=true
                break
            fi
        done
        if [[ "$reserved" == false ]] && ! port_in_use "$protocol" "$candidate"; then
            printf '%s' "$candidate"
            return 0
        fi
        candidate=$((candidate + 1))
        ((candidate <= 65535)) || fail "no free $protocol port found"
    done
}

managed_service_active() {
    local service="$1"
    if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
        systemctl is-active --quiet "$service"
    elif command -v rc-service >/dev/null 2>&1; then
        rc-service "${service%.service}" status >/dev/null 2>&1
    else
        return 1
    fi
}

port_owner() {
    local port="$1"
    local owner=""
    if command -v ss >/dev/null 2>&1; then
        owner="$(ss -H -ltnp "sport = :$port" 2>/dev/null | head -n 1 || true)"
    elif command -v lsof >/dev/null 2>&1; then
        owner="$(lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | awk 'NR==2 {print $1 " pid=" $2}' || true)"
    fi
    owner="${owner//$'\n'/ }"
    printf '%s' "${owner:-unknown process}"
}

tcp_443_conflict=""
if port_in_use tcp 443; then
    tcp_443_conflict="$(port_owner 443)"
fi
xray_port="$(next_free_port tcp 443)"
hysteria_port="$(next_free_port udp 1935)"
telemt_port="$TELEMT_PORT"
if [[ ",$CORES," == *,telemt,* ]] && port_in_use tcp "$telemt_port" && \
    ! managed_service_active xraydo-telemt.service; then
    fail "Telemt TCP port $telemt_port is occupied by $(port_owner "$telemt_port"); choose another port explicitly"
fi
if port_in_use tcp 8444 && managed_service_active xraydo-subscription.service; then
    subscription_port=8444
else
    subscription_port="$(next_free_port_excluding tcp 8444 "$telemt_port" "$xray_port")"
fi
hysteria_stats_port="$(next_free_port tcp 9999)"
telemt_api_port="$(next_free_port tcp 9091)"

printf '\n%s:\n' "$PLAN_TITLE"
printf '  %s: %s %s / %s / %s\n' "$PLATFORM_LABEL" "$OS_ID" "$OS_VERSION" "$ARCH" "$PACKAGE_MANAGER"
printf '  Profile / Профиль: %s\n' "$PROFILE"
printf '  Role / Роль: %s\n' "$ROLE"
printf '  %s: %s\n' "$PACKAGES_LABEL" "$packages_display"
printf '  %s: %s @ %s\n' "$SOURCE_LABEL" "$XRAYDO_ARCHIVE_URL" "$XRAYDO_REF"
printf '  SHA-256: %s\n' "$XRAYDO_ARCHIVE_SHA256"
printf '  %s: %s\n' "$PREFIX_LABEL" "$PREFIX"
printf '  %s: %s\n' "$CORES_LABEL" "$CORES"
if [[ "$TELEMT_ONLY" == true ]]; then
    printf '  TLS: Telemt Fake TLS (%s)\n' "$TELEMT_TLS_DOMAIN"
else
    printf '  TLS: %s\n' "$TLS_MODE"
    [[ "$TLS_MODE" != "private-ca" ]] && printf '    ACME email: %s\n' "${ACME_EMAIL:-not provided}"
fi
printf '  Init: %s\n' "$INIT_SYSTEM"
if [[ "$INIT_SYSTEM" != "none" && "$CORES" != "none" ]]; then
    printf '  Core autostart / Автозапуск ядер: enabled (%s)\n' "$INIT_SYSTEM"
fi
printf '  Security / Защита: %s (unchanged)\n' "$security_modules"
printf '  RAM available / Доступно RAM: %s MB\n' "$memory_mb"
printf '  Disk available / Доступно диска: %s MB\n' "$disk_available_mb"
[[ ",$CORES," == *,xray,* ]] && printf '  Xray REALITY: TCP %s\n' "$xray_port"
[[ ",$CORES," == *,xray,* ]] && printf '    REALITY target/SNI: %s\n' "$REALITY_TARGET"
if [[ -n "$tcp_443_conflict" ]]; then
    printf '    TCP 443 occupied / занят: %s; selected / выбран: TCP %s\n' "$tcp_443_conflict" "$xray_port"
fi
[[ ",$CORES," == *,hysteria,* ]] && printf '  Hysteria 2: UDP %s\n' "$hysteria_port"
[[ ",$CORES," == *,telemt,* ]] && printf '  Telemt: TCP %s; TLS domain: %s\n' "$telemt_port" "$TELEMT_TLS_DOMAIN"
if [[ "$FIRST_USER_MODE" != "skip" ]]; then
    printf '  First user / Первый пользователь: %s (%s)\n' "$FIRST_USER_NAME" "$FIRST_USER_MODE"
    if [[ "$FIRST_USER_LIMITS" == "unlimited" ]]; then
        printf '    Limits / Лимиты: unlimited; devices: 1\n'
    else
        printf '    Limits / Лимиты: duration=%s, traffic=%s, devices=1, IP=%s\n' \
            "$FIRST_USER_DURATION" "$FIRST_USER_TRAFFIC" "$FIRST_USER_IPS"
    fi
    [[ ",$CORES," == *,xray,* ]] && printf '    REALITY uTLS: %s\n' "$UTLS_PROFILE"
    [[ ",$CORES," == *,hysteria,* ]] && printf '    Hysteria ECH: %s\n' "$HYSTERIA_ECH"
    [[ "$TELEMT_ONLY" == true ]] || \
        printf '    HTTPS subscription: TCP %s (credentials generated after health-check)\n' "$subscription_port"
else
    printf '  First user / Первый пользователь: skipped (no credentials/subscription)\n'
fi
printf '  Firewall / Файрвол: unchanged / без изменений\n'

if [[ "$DRY_RUN" == true ]]; then
    printf '{"action":"install","arch":"%s","cores":"%s","dry_run":true,"first_user":"%s","language":"%s","limits":"%s","os":"%s","package_manager":"%s","profile":"%s","ref":"%s","role":"%s"}\n' \
        "$ARCH" "$CORES" "$FIRST_USER_MODE" "$LANGUAGE" "$FIRST_USER_LIMITS" "$OS_ID" "$PACKAGE_MANAGER" "$PROFILE" "$XRAYDO_REF" "$ROLE"
    exit 0
fi

if [[ "$ASSUME_YES" != true ]]; then
    [[ "$NON_INTERACTIVE" != true && -t 0 ]] || fail "use --yes with --non-interactive"
    read -r -p "$CONFIRM_TEXT" confirmation
    if [[ "$confirmation" == "q" || "$confirmation" == "n" || "$confirmation" == "no" || "$confirmation" == "нет" ]]; then
        printf '%s\n' "$CANCEL_TEXT"
        exit 0
    fi
fi

packages_apt() {
    root_exec apt-get update
    root_exec env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${REQUIRED_PACKAGES[@]}"
}

packages_dnf() { root_exec dnf install -y "${REQUIRED_PACKAGES[@]}"; }
packages_yum() { root_exec yum install -y "${REQUIRED_PACKAGES[@]}"; }
packages_zypper() { root_exec zypper --non-interactive install --no-recommends "${REQUIRED_PACKAGES[@]}"; }
packages_apk() { root_exec apk add --no-cache "${REQUIRED_PACKAGES[@]}"; }

install_system_packages() {
    ((${#REQUIRED_PACKAGES[@]})) || return 0
    case "$PACKAGE_MANAGER" in
        apt-get) packages_apt ;;
        dnf) packages_dnf ;;
        yum) packages_yum ;;
        zypper) packages_zypper ;;
        apk) packages_apk ;;
    esac
}

install_system_packages

ensure_certbot() {
    if [[ "$TLS_MODE" == "private-ca" ]]; then
        return 0
    fi
    if [[ "$TLS_MODE" == "domain" ]]; then
        CERTBOT_PATH="$(command -v certbot 2>/dev/null || true)"
        [[ -n "$CERTBOT_PATH" ]] || fail "Certbot installation failed"
        return 0
    fi
    if certbot_ip_capable "${CERTBOT_PATH:-certbot}"; then
        CERTBOT_PATH="$(command -v "${CERTBOT_PATH:-certbot}")"
        return 0
    fi
    [[ "$INIT_SYSTEM" == "systemd" ]] || fail "Certbot 5.4+ IP certificates require systemd/snap on this platform"
    command -v snap >/dev/null 2>&1 || fail "snapd is unavailable after package installation"
    root_exec systemctl enable --now snapd.socket >/dev/null
    root_exec snap wait system seed.loaded
    if ! root_exec snap list certbot >/dev/null 2>&1; then
        root_exec snap install certbot --classic
    else
        root_exec snap refresh certbot
    fi
    CERTBOT_PATH="/snap/bin/certbot"
    certbot_ip_capable "$CERTBOT_PATH" || fail "Certbot 5.4 or newer is required for public IP certificates"
}

ensure_certbot

for required_command in python3 curl tar install qrencode; do
    command -v "$required_command" >/dev/null 2>&1 || fail "required command is unavailable after package installation: $required_command"
done
if [[ ",$CORES," == *,hysteria,* ]]; then
    command -v openssl >/dev/null 2>&1 || fail "openssl is unavailable after package installation"
fi
if [[ ",$CORES," == *,xray,* ]]; then
    command -v unzip >/dev/null 2>&1 || fail "unzip is unavailable after package installation"
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/xraydo-install.XXXXXX")"
archive_path="$TEMP_DIR/xraydo.tar.gz"

printf '%s\n' "$DOWNLOAD_TEXT"
curl --fail --location --silent --show-error \
    --proto '=https' --tlsv1.2 \
    --connect-timeout 15 --retry 3 \
    --output "$archive_path" "$XRAYDO_ARCHIVE_URL"

if command -v sha256sum >/dev/null 2>&1; then
    actual_checksum="$(sha256sum "$archive_path")"
else
    actual_checksum="$(shasum -a 256 "$archive_path")"
fi
actual_checksum="${actual_checksum%% *}"
[[ "$actual_checksum" == "$XRAYDO_ARCHIVE_SHA256" ]] || \
    fail "archive checksum mismatch; expected $XRAYDO_ARCHIVE_SHA256, got $actual_checksum"

tar -xzf "$archive_path" -C "$TEMP_DIR"
SOURCE_DIR="$TEMP_DIR/xraydo-$XRAYDO_REF"
[[ -f "$SOURCE_DIR/src/xraydo/cli.py" ]] || fail "downloaded archive does not contain xraydo sources"

verify_checksum() {
    local file="$1"
    local expected="$2"
    local actual
    if command -v sha256sum >/dev/null 2>&1; then
        actual="$(sha256sum "$file")"
    else
        actual="$(shasum -a 256 "$file")"
    fi
    actual="${actual%% *}"
    [[ "$actual" == "$expected" ]] || fail "checksum mismatch for $(basename "$file"): expected $expected, got $actual"
}

download_verified() {
    local url="$1"
    local destination="$2"
    local checksum="$3"
    if ! curl --fail --location --silent --show-error \
        --proto '=https' --tlsv1.2 \
        --connect-timeout 15 --retry 3 \
        --output "$destination" "$url"; then
        fail "cannot download pinned artifact: $url"
    fi
    verify_checksum "$destination" "$checksum"
}

case "$PREFIX" in
    /usr|/usr/*|/opt|/opt/*) ((EUID != 0)) && INSTALL_WITH_ROOT=true ;;
esac

install_exec() {
    if [[ "$INSTALL_WITH_ROOT" == true ]]; then
        root_exec "$@"
    else
        "$@"
    fi
}

readonly LIBRARY_DIR="$PREFIX/lib/xraydo/$INSTALLER_VERSION"
readonly BINARY_PATH="$PREFIX/bin/xraydo"
readonly CONFIG_DIR="$PREFIX/etc/xraydo"
readonly CONFIG_PATH="$CONFIG_DIR/config.json"
readonly CORE_DIR="$PREFIX/lib/xraydo/cores"
readonly DOCUMENTATION_DIR="$PREFIX/share/doc/xraydo"
if [[ "$PREFIX" == "/usr/local" ]]; then
    readonly DATA_DIR="/var/lib/xraydo"
else
    readonly DATA_DIR="$PREFIX/var/lib/xraydo"
fi

printf '%s\n' "$INSTALL_TEXT"
install_exec install -d -m 0755 "$LIBRARY_DIR/xraydo/locales" "$LIBRARY_DIR/xraydo/data" "$PREFIX/bin" "$CONFIG_DIR" "$DOCUMENTATION_DIR"
install_exec install -m 0644 "$SOURCE_DIR"/src/xraydo/*.py "$LIBRARY_DIR/xraydo/"
install_exec install -m 0644 "$SOURCE_DIR"/src/xraydo/locales/*.json "$LIBRARY_DIR/xraydo/locales/"
if compgen -G "$SOURCE_DIR/src/xraydo/data/*.json" >/dev/null; then
    install_exec install -m 0644 "$SOURCE_DIR"/src/xraydo/data/*.json "$LIBRARY_DIR/xraydo/data/"
fi
install_exec install -m 0644 "$SOURCE_DIR/LICENSE" "$SOURCE_DIR/THIRD_PARTY_NOTICES.md" "$DOCUMENTATION_DIR/"
stable_installer="$PREFIX/lib/xraydo/install.sh"
if [[ ! -e "$stable_installer" || "$(readlink -f "$0")" != "$(readlink -f "$stable_installer")" ]]; then
    install_exec install -m 0755 "$0" "$stable_installer"
fi

wrapper_path="$TEMP_DIR/xraydo-wrapper"
printf '#!/usr/bin/env bash\nXRAYDO_CONFIG=%q XRAYDO_DATA_DIR=%q PYTHONPATH=%q exec python3 -m xraydo.cli "$@"\n' \
    "$CONFIG_PATH" "$DATA_DIR" "$LIBRARY_DIR" > "$wrapper_path"
chmod 0755 "$wrapper_path"
install_exec install -m 0755 "$wrapper_path" "$BINARY_PATH"
# The root state file remains mode 0600 and sensitive subdirectories remain
# mode 0700.  Core processes need to open and validate this parent directory
# before reaching their own mode-0750 working directories (Telemt explicitly
# walks the complete path without following symlinks).
if [[ "$DATA_DIR" == "/var/lib/xraydo" ]]; then
    install_exec install -d -m 0755 "$DATA_DIR"
else
    install_exec install -d -m 0700 "$DATA_DIR"
fi

if [[ ! -e "$CONFIG_PATH" ]]; then
    config_path="$TEMP_DIR/config.json"
    cores_json="[]"
    if [[ "$CORES" != "none" ]]; then
        cores_json="[\"${CORES//,/\",\"}\"]"
    fi
    printf '{\n  "schema_version": 1,\n  "language": "%s",\n  "profile": "%s",\n  "role": "%s",\n  "cores": %s,\n  "tls_mode": "%s",\n  "ports": {"xray_tcp": %s, "hysteria_udp": %s, "telemt_tcp": %s}\n}\n' \
        "$LANGUAGE" "$PROFILE" "$ROLE" "$cores_json" "$TLS_MODE" \
        "$xray_port" "$hysteria_port" "$telemt_port" > "$config_path"
    chmod 0600 "$config_path"
    install_exec install -m 0600 "$config_path" "$CONFIG_PATH"
fi

if [[ ",$CORES," == *,xray,* ]]; then
    if [[ "$ARCH" == "amd64" ]]; then
        xray_asset="Xray-linux-64.zip"
        xray_checksum="e4bd9119f50ae7c41658b1abd0679ae06f98bd864a2907ff33f2f2cf7081b214"
    else
        xray_asset="Xray-linux-arm64-v8a.zip"
        xray_checksum="1811c8f1da0c3b6dc707ebaf1c8390e987ba39859a8f0a7aadd8526157f2ec8d"
    fi
    xray_archive="$TEMP_DIR/$xray_asset"
    download_verified "https://github.com/XTLS/Xray-core/releases/download/$XRAY_VERSION/$xray_asset" "$xray_archive" "$xray_checksum"
    xray_extract="$TEMP_DIR/xray"
    mkdir -p "$xray_extract"
    unzip -q "$xray_archive" -d "$xray_extract"
    install_exec install -d -m 0755 "$CORE_DIR/xray/$XRAY_VERSION"
    install_exec install -m 0755 "$xray_extract/xray" "$CORE_DIR/xray/$XRAY_VERSION/xray"
    install_exec install -m 0644 "$xray_extract/geoip.dat" "$xray_extract/geosite.dat" "$CORE_DIR/xray/$XRAY_VERSION/"
    install_exec install -m 0755 "$xray_extract/xray" "$PREFIX/bin/xray"
fi

if [[ ",$CORES," == *,hysteria,* ]]; then
    if [[ "$ARCH" == "amd64" ]]; then
        hysteria_asset="hysteria-linux-amd64"
        hysteria_checksum="907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691"
    else
        hysteria_asset="hysteria-linux-arm64"
        hysteria_checksum="a68a61a84452ca250ce0368202521965ca9cc9d801a404f1dc9008ac6cf677a7"
    fi
    hysteria_binary="$TEMP_DIR/$hysteria_asset"
    download_verified "https://github.com/HyNetworks/hysteria/releases/download/app%2Fv2.13.0/$hysteria_asset" "$hysteria_binary" "$hysteria_checksum"
    install_exec install -d -m 0755 "$CORE_DIR/hysteria/$HYSTERIA_VERSION"
    install_exec install -m 0755 "$hysteria_binary" "$CORE_DIR/hysteria/$HYSTERIA_VERSION/hysteria"
    install_exec install -m 0755 "$hysteria_binary" "$PREFIX/bin/hysteria"
fi

if [[ ",$CORES," == *,telemt,* ]]; then
    if ldd --version 2>&1 | grep -qi musl || ls /lib/ld-musl-*.so.1 >/dev/null 2>&1; then
        telemt_libc="musl"
    else
        telemt_libc="gnu"
    fi
    if [[ "$ARCH" == "amd64" ]]; then
        telemt_arch="x86_64"
        [[ "$telemt_libc" == "gnu" ]] && telemt_checksum="b10f86c6851f5c422a49d98deea55e8ef4aea7879605e59c1d4aa43c8f4c70ce" || telemt_checksum="71c1454f79cf755edea0985500629daba553002f9863be754e8e38a418822773"
    else
        telemt_arch="aarch64"
        [[ "$telemt_libc" == "gnu" ]] && telemt_checksum="d2e3ca6a3f407aecb190c4a73db74ec581d56b5dd6ab469cb0e1d0f2c3d31c57" || telemt_checksum="21795430c4165cf964405459714d08eea319edaa5c9027beac4de373c356d84d"
    fi
    telemt_asset="telemt-$telemt_arch-linux-$telemt_libc.tar.gz"
    telemt_archive="$TEMP_DIR/$telemt_asset"
    download_verified "https://github.com/telemt/telemt/releases/download/$TELEMT_VERSION/$telemt_asset" "$telemt_archive" "$telemt_checksum"
    telemt_extract="$TEMP_DIR/telemt"
    mkdir -p "$telemt_extract"
    tar -xzf "$telemt_archive" -C "$telemt_extract"
    telemt_binary="$(find "$telemt_extract" -type f -name telemt -print -quit)"
    [[ -n "$telemt_binary" ]] || fail "Telemt archive does not contain the telemt binary"
    install_exec install -d -m 0755 "$CORE_DIR/telemt/$TELEMT_VERSION"
    install_exec install -m 0755 "$telemt_binary" "$CORE_DIR/telemt/$TELEMT_VERSION/telemt"
    install_exec install -m 0755 "$telemt_binary" "$PREFIX/bin/telemt"
fi

ensure_system_user() {
    local name="$1"
    local home="$2"
    id -u "$name" >/dev/null 2>&1 && return 0
    if command -v useradd >/dev/null 2>&1; then
        root_exec useradd --system --no-create-home --home-dir "$home" --shell "$(command -v nologin || printf /bin/false)" "$name"
    elif command -v adduser >/dev/null 2>&1; then
        root_exec adduser -S -D -H -h "$home" -s /sbin/nologin "$name"
    else
        fail "cannot create system user $name: useradd/adduser is unavailable"
    fi
}

write_systemd_unit() {
    local core="$1"
    local user="$2"
    local command_line="$3"
    local config_file="$4"
    local data_dir="$5"
    local unit="$TEMP_DIR/xraydo-$core.service"
    local capabilities="CAP_NET_BIND_SERVICE"
    if [[ "$core" == "telemt" ]]; then
        capabilities="CAP_NET_BIND_SERVICE CAP_NET_ADMIN"
    fi
    cat > "$unit" <<EOF
[Unit]
Description=xraydo managed $core service
After=network-online.target
Wants=network-online.target
ConditionPathExists=$config_file

[Service]
Type=simple
User=$user
Group=$user
ExecStart=$command_line
WorkingDirectory=$data_dir
Restart=on-failure
RestartSec=5s
LimitNOFILE=65536
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=$data_dir /var/log/xraydo/$core
AmbientCapabilities=$capabilities
CapabilityBoundingSet=$capabilities

[Install]
WantedBy=multi-user.target
EOF
    root_exec install -m 0644 "$unit" "/etc/systemd/system/xraydo-$core.service"
}

write_openrc_unit() {
    local core="$1"
    local user="$2"
    local command_path="$3"
    local command_args="$4"
    local config_file="$5"
    local service="$TEMP_DIR/xraydo-$core"
    cat > "$service" <<EOF
#!/sbin/openrc-run
name="xraydo $core"
command="$command_path"
command_args="$command_args"
command_user="$user:$user"
directory="$data_dir"
command_background=true
pidfile="/run/xraydo-$core.pid"
required_files="$config_file"

depend() {
    need net
    after firewall
}
EOF
    root_exec install -m 0755 "$service" "/etc/init.d/xraydo-$core"
}

provision_core_service() {
    local core="$1"
    local binary="$2"
    local args="$3"
    local config_name="$4"
    local user="xraydo-$core"
    local config_dir="$CONFIG_DIR/cores/$core"
    local config_file="$config_dir/$config_name"
    local data_dir="/var/lib/xraydo/$core"

    ensure_system_user "$user" "$data_dir"
    root_exec install -d -m 0750 -o root -g "$user" "$config_dir"
    root_exec install -d -m 0750 -o "$user" -g "$user" "$data_dir"
    root_exec install -d -m 0750 -o "$user" -g "$user" "/var/log/xraydo/$core"

    case "$INIT_SYSTEM" in
        systemd) write_systemd_unit "$core" "$user" "$binary $args" "$config_file" "$data_dir" ;;
        openrc) write_openrc_unit "$core" "$user" "$binary" "$args" "$config_file" ;;
    esac
}

enable_core_autostart() {
    local core="$1"
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        root_exec systemctl enable "xraydo-$core.service" >/dev/null
        root_exec systemctl is-enabled --quiet "xraydo-$core.service"
    elif [[ "$INIT_SYSTEM" == "openrc" ]]; then
        root_exec rc-update add "xraydo-$core" default >/dev/null
    fi
}

provision_tls_renewer() {
    root_exec install -d -m 0755 /var/lib/xraydo
    root_exec install -d -m 0750 /var/log/xraydo/tls-renew
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        local unit="$TEMP_DIR/xraydo-tls-renew.service"
        cat > "$unit" <<EOF
[Unit]
Description=xraydo subscription TLS renewal agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$PREFIX/bin/xraydo tls renew-daemon --interval 3600 --jitter 300
Restart=always
RestartSec=15s
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=/var/lib/xraydo /var/log/xraydo/tls-renew
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
EOF
        root_exec install -m 0644 "$unit" /etc/systemd/system/xraydo-tls-renew.service
    else
        local service="$TEMP_DIR/xraydo-tls-renew"
        cat > "$service" <<EOF
#!/sbin/openrc-run
name="xraydo TLS renewal agent"
command="$PREFIX/bin/xraydo"
command_args="tls renew-daemon --interval 3600 --jitter 300"
directory="/var/lib/xraydo"
command_background=true
pidfile="/run/xraydo-tls-renew.pid"
output_log="/var/log/xraydo/tls-renew/output.log"
error_log="/var/log/xraydo/tls-renew/error.log"

depend() {
    need localmount
    after net
}
EOF
        root_exec install -m 0755 "$service" /etc/init.d/xraydo-tls-renew
    fi
}

provision_certbot_renewer() {
    local hook_dir="/etc/letsencrypt/renewal-hooks/deploy"
    local hook="$TEMP_DIR/xraydo-certbot-deploy"
    local cert="$DATA_DIR/tls/leaf/current/server.crt"
    local key="$DATA_DIR/tls/leaf/current/server.key"
    cat > "$hook" <<EOF
#!/bin/sh
set -eu
$BINARY_PATH --json tls acme-deploy --identifier '$PUBLIC_HOST' --certbot '$CERTBOT_PATH'
if [ -d '$CONFIG_DIR/cores/hysteria/pki' ]; then
    install -m 0644 '$cert' '$CONFIG_DIR/cores/hysteria/pki/server.crt'
    install -m 0640 '$key' '$CONFIG_DIR/cores/hysteria/pki/server.key'
fi
if command -v systemctl >/dev/null 2>&1; then
    systemctl try-restart xraydo-subscription.service >/dev/null 2>&1 || true
    systemctl try-restart xraydo-hysteria.service >/dev/null 2>&1 || true
elif command -v rc-service >/dev/null 2>&1; then
    rc-service xraydo-subscription restart >/dev/null 2>&1 || true
    rc-service xraydo-hysteria restart >/dev/null 2>&1 || true
fi
EOF
    root_exec install -d -m 0755 "$hook_dir"
    root_exec install -m 0755 "$hook" "$hook_dir/xraydo"
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        local service="$TEMP_DIR/xraydo-certbot-renew.service"
        local timer="$TEMP_DIR/xraydo-certbot-renew.timer"
        cat > "$service" <<EOF
[Unit]
Description=Renew xraydo Let's Encrypt certificate with Certbot
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$CERTBOT_PATH renew --cert-name xraydo-subscription --quiet
EOF
        cat > "$timer" <<'EOF'
[Unit]
Description=Twice-daily xraydo Certbot renewal check

[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=3600
Persistent=true

[Install]
WantedBy=timers.target
EOF
        root_exec install -m 0644 "$service" /etc/systemd/system/xraydo-certbot-renew.service
        root_exec install -m 0644 "$timer" /etc/systemd/system/xraydo-certbot-renew.timer
    elif [[ "$INIT_SYSTEM" == "openrc" ]]; then
        local cron="$TEMP_DIR/xraydo-certbot-renew"
        cat > "$cron" <<EOF
#!/bin/sh
$CERTBOT_PATH renew --cert-name xraydo-subscription --quiet
EOF
        root_exec install -d -m 0755 /etc/periodic/12hourly
        root_exec install -m 0755 "$cron" /etc/periodic/12hourly/xraydo-certbot-renew
    fi
}

provision_subscription_service() {
    local cert="$DATA_DIR/tls/leaf/current/server.crt"
    local key="$DATA_DIR/tls/leaf/current/server.key"
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        local unit="$TEMP_DIR/xraydo-subscription.service"
        cat > "$unit" <<EOF
[Unit]
Description=xraydo HTTPS subscription service
After=network-online.target
Wants=network-online.target
ConditionPathExists=$cert

[Service]
Type=simple
ExecStart=$BINARY_PATH subscription serve --https-only --listen 0.0.0.0 --port $subscription_port --public-host $PUBLIC_HOST --cert $cert --key $key
Restart=on-failure
RestartSec=5s
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadOnlyPaths=$DATA_DIR/tls
ReadWritePaths=$DATA_DIR

[Install]
WantedBy=multi-user.target
EOF
        root_exec install -m 0644 "$unit" /etc/systemd/system/xraydo-subscription.service
    elif [[ "$INIT_SYSTEM" == "openrc" ]]; then
        local service="$TEMP_DIR/xraydo-subscription"
        cat > "$service" <<EOF
#!/sbin/openrc-run
name="xraydo HTTPS subscription service"
command="$BINARY_PATH"
command_args="subscription serve --https-only --listen 0.0.0.0 --port $subscription_port --public-host $PUBLIC_HOST --cert $cert --key $key"
directory="$DATA_DIR"
command_background=true
pidfile="/run/xraydo-subscription.pid"

depend() {
    need net
}
EOF
        root_exec install -m 0755 "$service" /etc/init.d/xraydo-subscription
    fi
}

start_core_service() {
    local core="$1"
    [[ "$PREFIX" == "/usr/local" && "$INIT_SYSTEM" != "none" ]] || return 0
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        root_exec systemctl restart "xraydo-$core.service" >/dev/null
        root_exec systemctl is-active --quiet "xraydo-$core.service" || return 1
    else
        if root_exec rc-service "xraydo-$core" status >/dev/null 2>&1; then
            root_exec rc-service "xraydo-$core" restart >/dev/null
        else
            root_exec rc-service "xraydo-$core" start >/dev/null
        fi
        root_exec rc-service "xraydo-$core" status >/dev/null || return 1
    fi
    local attempt report="$TEMP_DIR/$core-protocol-health.json"
    for attempt in {1..30}; do
        if root_exec env XRAYDO_CONFIG="$CONFIG_DIR/config.json" \
            "$BINARY_PATH" --json status >"$report" 2>/dev/null && \
            python3 -c 'import json,sys; data=json.load(open(sys.argv[1], encoding="utf-8")); raise SystemExit(data["cores"][sys.argv[2]]["protocol"]["state"] != "healthy")' \
                "$report" "$core"; then
            return 0
        fi
        sleep 1
    done
    return 1
}

detect_public_host() {
    [[ -n "$PUBLIC_HOST" ]] && return 0
    PUBLIC_HOST="$(curl --fail --silent --show-error --connect-timeout 5 --max-time 10 https://api.ipify.org 2>/dev/null || true)"
    if [[ -z "$PUBLIC_HOST" && "$NON_INTERACTIVE" != true && -t 0 ]]; then
        read -r -p 'Public DNS name or IP / Публичный DNS или IP: ' PUBLIC_HOST
    fi
    [[ -n "$PUBLIC_HOST" ]] || fail "cannot detect public address; use --public-host"
    [[ "$PUBLIC_HOST" =~ ^[A-Za-z0-9.:-]{1,253}$ ]] || fail "invalid public host"
}

quiet_root() {
    local label="$1"
    shift
    local diagnostics="$TEMP_DIR/diagnostics.log"
    if ! root_exec "$@" >>"$diagnostics" 2>&1; then
        printf 'Warning: %s failed; details: %s\n' "$label" "$diagnostics" >&2
        return 1
    fi
}

quiet_root_retry() {
    local label="$1"
    local attempts="$2"
    local delay="$3"
    shift 3
    local diagnostics="$TEMP_DIR/diagnostics.log"
    local attempt
    for ((attempt = 1; attempt <= attempts; attempt++)); do
        if root_exec "$@" >>"$diagnostics" 2>&1; then
            return 0
        fi
        if ((attempt < attempts)); then
            sleep "$delay"
        fi
    done
    printf 'Warning: %s failed after %s attempts; details: %s\n' \
        "$label" "$attempts" "$diagnostics" >&2
    return 1
}

managed_leaf_matches_public_host() {
    local cert="$DATA_DIR/tls/leaf/current/server.crt"
    local key="$DATA_DIR/tls/leaf/current/server.key"
    [[ -s "$cert" && -s "$key" ]] || return 1
    if [[ "$PUBLIC_HOST" == *:* || "$PUBLIC_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        root_exec openssl x509 -in "$cert" -noout -checkip "$PUBLIC_HOST" >/dev/null 2>&1
    else
        root_exec openssl x509 -in "$cert" -noout -checkhost "$PUBLIC_HOST" >/dev/null 2>&1
    fi
}

# Installation health must pass before any first-user secret is generated.
XRAYDO_DATA_DIR="$TEMP_DIR/xraydo-smoke" "$BINARY_PATH" --json status >/dev/null
XRAYDO_DATA_DIR="$TEMP_DIR/xraydo-smoke" "$BINARY_PATH" tls renew-daemon --once >/dev/null
[[ ",$CORES," == *,xray,* ]] && quiet_root "Xray version health-check" "$PREFIX/bin/xray" version
[[ ",$CORES," == *,hysteria,* ]] && quiet_root "Hysteria version health-check" "$PREFIX/bin/hysteria" version
[[ ",$CORES," == *,telemt,* ]] && quiet_root "Telemt version health-check" "$PREFIX/bin/telemt" --version

# ACME hooks and service units must be rendered with the final public identifier.
[[ "$FIRST_USER_MODE" == "skip" ]] || detect_public_host

if [[ "$PREFIX" == "/usr/local" && "$INIT_SYSTEM" != "none" ]]; then
    [[ ",$CORES," == *,xray,* ]] && provision_core_service xray "$PREFIX/bin/xray" "run -config $CONFIG_DIR/cores/xray/config.json" config.json
    [[ ",$CORES," == *,hysteria,* ]] && provision_core_service hysteria "$PREFIX/bin/hysteria" "server -c $CONFIG_DIR/cores/hysteria/config.yaml" config.yaml
    [[ ",$CORES," == *,telemt,* ]] && provision_core_service telemt "$PREFIX/bin/telemt" "$CONFIG_DIR/cores/telemt/telemt.toml" telemt.toml
    if [[ "$TELEMT_ONLY" != true ]]; then
        if [[ "$TLS_MODE" == "private-ca" ]]; then
            provision_tls_renewer
        else
            provision_certbot_renewer
        fi
    fi
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        root_exec systemctl daemon-reload
        [[ ",$CORES," == *,xray,* ]] && enable_core_autostart xray
        [[ ",$CORES," == *,hysteria,* ]] && enable_core_autostart hysteria
        [[ ",$CORES," == *,telemt,* ]] && enable_core_autostart telemt
        if [[ "$TELEMT_ONLY" != true ]]; then
            if [[ "$TLS_MODE" == "private-ca" ]]; then
                root_exec systemctl enable --now xraydo-tls-renew.service >/dev/null
            else
                root_exec systemctl enable --now xraydo-certbot-renew.timer >/dev/null
            fi
        fi
    else
        [[ ",$CORES," == *,xray,* ]] && enable_core_autostart xray
        [[ ",$CORES," == *,hysteria,* ]] && enable_core_autostart hysteria
        [[ ",$CORES," == *,telemt,* ]] && enable_core_autostart telemt
        if [[ "$TELEMT_ONLY" != true && "$TLS_MODE" == "private-ca" ]]; then
            root_exec rc-update add xraydo-tls-renew default >/dev/null
            root_exec rc-service xraydo-tls-renew start >/dev/null
        fi
    fi
fi

first_user_created=false
subscription_service_started=false
subscription_token=""
xray_state="not selected"
hysteria_state="not selected"
telemt_state="not selected"

if [[ "$FIRST_USER_MODE" != "skip" ]]; then
    detect_public_host
    url_host="$PUBLIC_HOST"
    [[ "$url_host" == *:* && "$url_host" != \[*\] ]] && url_host="[$url_host]"
    subscription_base="https://$url_host:$subscription_port"

    if [[ "$TELEMT_ONLY" == true ]]; then
        : # Telemt Fake TLS does not use an X.509 certificate.
    elif [[ "$TLS_MODE" == "private-ca" ]]; then
        quiet_root "private CA initialization" "$BINARY_PATH" --json tls init-ca || fail "TLS CA initialization failed"
        if [[ "$PUBLIC_HOST" == *:* || "$PUBLIC_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            quiet_root "server IP certificate" "$BINARY_PATH" --json tls issue-self --ip "$PUBLIC_HOST" || fail "TLS certificate issuance failed"
        else
            quiet_root "server DNS certificate" "$BINARY_PATH" --json tls issue-self --domain "$PUBLIC_HOST" || fail "TLS certificate issuance failed"
        fi
    else
        acme_args=(--identifier "$PUBLIC_HOST" --certbot "$CERTBOT_PATH")
        [[ -z "$ACME_EMAIL" ]] || acme_args+=(--email "$ACME_EMAIL")
        if port_in_use tcp 80; then
            [[ -d /var/www/html ]] || fail "TCP port 80 is occupied by $(port_owner 80), and /var/www/html is unavailable for ACME webroot"
            acme_args+=(--webroot /var/www/html)
        fi
        quiet_root "Let's Encrypt certificate" "$BINARY_PATH" --json tls acme-issue "${acme_args[@]}" || fail "Let's Encrypt certificate issuance failed"
    fi
    if [[ "$TELEMT_ONLY" != true ]]; then
        quiet_root "subscription URL setup" "$BINARY_PATH" --json settings subscription-url "$subscription_base" || fail "subscription URL setup failed"
    fi

    users_json="$TEMP_DIR/users.json"
    root_exec "$BINARY_PATH" --json user list >"$users_json"
    chmod 0600 "$users_json"
    if grep -Fq "\"name\": \"$FIRST_USER_NAME\"" "$users_json"; then
        printf '  first user: existing user %s preserved; token was not rotated\n' "$FIRST_USER_NAME"
    else
        user_json="$TEMP_DIR/first-user.json"
        (umask 077; root_exec "$BINARY_PATH" --json user create "$FIRST_USER_NAME" >"$user_json")
        subscription_token="$(sed -n 's/.*"subscription_token": "\([^"]*\)".*/\1/p' "$user_json")"
        [[ -n "$subscription_token" ]] || fail "first user was created but its one-time token could not be captured"
        first_user_created=true
    fi

    keys_json="$TEMP_DIR/access-keys.json"
    root_exec "$BINARY_PATH" --json key list --user "$FIRST_USER_NAME" >"$keys_json"
    chmod 0600 "$keys_json"
    access_key_id="$(sed -n 's/.*"id": "\([^"]*\)".*/\1/p' "$keys_json" | head -n 1)"
    [[ -n "$access_key_id" ]] || fail "default access key not found"
    if [[ "$FIRST_USER_LIMITS" != "unlimited" ]]; then
        quiet_root "first-user limits" "$BINARY_PATH" --json key set-limit "$access_key_id" \
            --traffic "$FIRST_USER_TRAFFIC" --duration "$FIRST_USER_DURATION" \
            --starts immediate --ips "$FIRST_USER_IPS" || fail "cannot apply first-user limits"
    fi

    if [[ ",$CORES," == *,xray,* ]]; then
        if quiet_root "Xray REALITY validation" "$BINARY_PATH" --json core reality \
            --user "$FIRST_USER_NAME" --server "$PUBLIC_HOST" --listen-port "$xray_port" \
            --target "$REALITY_TARGET" --sni "$REALITY_TARGET" \
            --fingerprint "$UTLS_PROFILE" --config "$CONFIG_DIR/cores/xray/config.json"; then
            if start_core_service xray; then xray_state="configured and healthy"; else xray_state="configured; service health failed"; fi
        else
            xray_state="failed; endpoint not published"
        fi
    fi

    if [[ ",$CORES," == *,hysteria,* ]]; then
        hysteria_cert="$CONFIG_DIR/cores/hysteria/pki/server.crt"
        hysteria_key="$CONFIG_DIR/cores/hysteria/pki/server.key"
        root_exec install -d -m 0750 "$CONFIG_DIR/cores/hysteria/pki"
        if id -u xraydo-hysteria >/dev/null 2>&1; then
            root_exec chown root:xraydo-hysteria "$CONFIG_DIR/cores/hysteria/pki"
            root_exec install -m 0644 -o root -g xraydo-hysteria "$DATA_DIR/tls/leaf/current/server.crt" "$hysteria_cert"
            root_exec install -m 0640 -o root -g xraydo-hysteria "$DATA_DIR/tls/leaf/current/server.key" "$hysteria_key"
        else
            root_exec install -m 0644 "$DATA_DIR/tls/leaf/current/server.crt" "$hysteria_cert"
            root_exec install -m 0600 "$DATA_DIR/tls/leaf/current/server.key" "$hysteria_key"
        fi
        ech_args=()
        if [[ "$HYSTERIA_ECH" != "off" ]]; then
            ech_path="$CONFIG_DIR/cores/hysteria/ech.pem"
            if quiet_root "Hysteria ECH capability" "$PREFIX/bin/hysteria" ech --help && \
                quiet_root "Hysteria ECH generation" "$PREFIX/bin/hysteria" ech --public-name "$HYSTERIA_ECH" --output "$ech_path"; then
                if id -u xraydo-hysteria >/dev/null 2>&1; then
                    root_exec chown root:xraydo-hysteria "$ech_path"
                    root_exec chmod 0640 "$ech_path"
                else
                    root_exec chmod 0600 "$ech_path"
                fi
                # shellcheck disable=SC2016
                ech_config="$(root_exec awk '/BEGIN ECH CONFIGS/{copy=1;next}/END ECH CONFIGS/{copy=0}copy{printf "%s",$0}' "$ech_path")"
                [[ -n "$ech_config" ]] && ech_args=(--ech-key "$ech_path" --ech-config "$ech_config")
            else
                printf 'Warning: built-in Hysteria ECH is unavailable; ECH was not enabled.\n' >&2
            fi
        fi
        if quiet_root "Hysteria configuration validation" "$BINARY_PATH" --json core hysteria \
            --user "$FIRST_USER_NAME" --server "$PUBLIC_HOST" --port "$hysteria_port" \
            --stats-port "$hysteria_stats_port" --cert "$hysteria_cert" --key "$hysteria_key" \
            --config "$CONFIG_DIR/cores/hysteria/config.yaml" "${ech_args[@]}"; then
            if start_core_service hysteria; then hysteria_state="configured and healthy"; else hysteria_state="configured; service health failed"; fi
        else
            hysteria_state="failed; endpoint not published"
        fi
    fi

    if [[ ",$CORES," == *,telemt,* ]]; then
        if quiet_root "Telemt configuration validation" "$BINARY_PATH" --json core telemt \
            --user "$FIRST_USER_NAME" --server "$PUBLIC_HOST" --port "$telemt_port" \
            --api-port "$telemt_api_port" --tls-domain "$TELEMT_TLS_DOMAIN" \
            --config "$CONFIG_DIR/cores/telemt/telemt.toml"; then
            if start_core_service telemt; then telemt_state="configured and healthy"; else telemt_state="configured; service health failed"; fi
        else
            telemt_state="failed; endpoint not published"
        fi
    fi

    if [[ "$TELEMT_ONLY" == true ]]; then
        quiet_root "disable standard subscription URL for Telemt-only" \
            "$BINARY_PATH" --json settings subscription-url off || \
            fail "cannot disable the standard subscription URL for Telemt-only"
        if [[ "$PREFIX" == "/usr/local" && "$INIT_SYSTEM" == "systemd" ]] && \
            root_exec systemctl cat xraydo-subscription.service >/dev/null 2>&1; then
            root_exec systemctl disable --now xraydo-subscription.service >/dev/null
        elif [[ "$PREFIX" == "/usr/local" && "$INIT_SYSTEM" == "openrc" ]] && \
            [[ -e /etc/init.d/xraydo-subscription ]]; then
            root_exec rc-service xraydo-subscription stop >/dev/null 2>&1 || true
            root_exec rc-update del xraydo-subscription default >/dev/null 2>&1 || true
        fi
    fi

    if [[ "$TELEMT_ONLY" != true && "$PREFIX" == "/usr/local" && "$INIT_SYSTEM" != "none" ]]; then
        provision_subscription_service
        if [[ "$INIT_SYSTEM" == "systemd" ]]; then
            root_exec systemctl daemon-reload
            root_exec systemctl enable --now xraydo-subscription.service >/dev/null
            root_exec systemctl restart xraydo-subscription.service >/dev/null
            root_exec systemctl is-active --quiet xraydo-subscription.service || fail "subscription service health-check failed"
        else
            root_exec rc-update add xraydo-subscription default >/dev/null
            root_exec rc-service xraydo-subscription restart >/dev/null
            root_exec rc-service xraydo-subscription status >/dev/null || fail "subscription service health-check failed"
        fi
        subscription_curl=(curl --fail --silent --show-error --connect-timeout 2 --max-time 5 \
            --noproxy '*' --output /dev/null)
        if [[ "$TLS_MODE" == "private-ca" ]]; then
            subscription_curl+=(--cacert "$DATA_DIR/tls/ca/current/ca.crt")
        fi
        subscription_probe=("${subscription_curl[@]}" \
            --resolve "$PUBLIC_HOST:$subscription_port:127.0.0.1" \
            "https://$url_host:$subscription_port/healthz")
        quiet_root_retry "subscription HTTPS protocol health-check" 12 1 \
            "${subscription_probe[@]}" || \
            fail "subscription HTTPS protocol health-check failed"
        subscription_service_started=true
    fi

    if [[ "$first_user_created" == true && "$TELEMT_ONLY" != true ]]; then
        token_file="$TEMP_DIR/subscription-token"
        (umask 077; printf '%s' "$subscription_token" >"$token_file")
        subscription_url="$subscription_base/sub/$subscription_token"
        if [[ "$subscription_service_started" == true ]]; then
            subscription_token_probe=("${subscription_curl[@]}" \
                --resolve "$PUBLIC_HOST:$subscription_port:127.0.0.1" "$subscription_url")
            quiet_root_retry "subscription token protocol health-check" 12 1 \
                "${subscription_token_probe[@]}" || fail "generated subscription URL returned an error"
        fi
        telemt_json="$TEMP_DIR/telemt-links.json"
        root_exec "$BINARY_PATH" --json telemt link "$FIRST_USER_NAME" >"$telemt_json" 2>/dev/null || printf '{"links":[]}' >"$telemt_json"
        chmod 0600 "$telemt_json"
        mtproto_link="$(sed -n 's/.*"uri": "\(tg:\/\/proxy?[^"]*\)".*/\1/p' "$telemt_json" | head -n 1)"
        if [[ "$NON_INTERACTIVE" != true && -t 0 && -t 1 && -w /dev/tty ]]; then
            {
                printf '\nHTTPS subscription (shown once):\n%s\n' "$subscription_url"
                if [[ "$TLS_MODE" == "private-ca" ]]; then
                    printf 'Private CA: %s/tls/ca/current/ca.crt\n' "$DATA_DIR"
                else
                    printf "TLS: Let's Encrypt (%s)\n" "$PUBLIC_HOST"
                fi
                [[ -z "$mtproto_link" ]] || printf '\nMTProto link:\n%s\n' "$mtproto_link"
                printf '\nSubscription QR:\n'
            } >/dev/tty
            root_exec "$BINARY_PATH" subscription qr "$FIRST_USER_NAME" --token-file "$token_file" >/dev/tty
            if [[ -n "$mtproto_link" ]]; then
                printf '%s' "$mtproto_link" | qrencode -t ANSIUTF8 -o - >/dev/tty
            fi
        else
            credentials_file="$DATA_DIR/bootstrap-$FIRST_USER_NAME.txt"
            credentials_tmp="$TEMP_DIR/bootstrap.txt"
            (umask 077; {
                printf 'HTTPS subscription: %s\n' "$subscription_url"
                if [[ "$TLS_MODE" == "private-ca" ]]; then
                    printf 'Private CA: %s/tls/ca/current/ca.crt\n' "$DATA_DIR"
                else
                    printf "TLS: Let's Encrypt (%s)\n" "$PUBLIC_HOST"
                fi
                [[ -z "$mtproto_link" ]] || printf 'MTProto: %s\n' "$mtproto_link"
            } >"$credentials_tmp")
            root_exec install -m 0600 "$credentials_tmp" "$credentials_file"
            printf '  one-time credentials: %s (move securely, then delete)\n' "$credentials_file"
        fi
    fi
fi

if [[ "$TELEMT_ONLY" == true ]]; then
    [[ "$telemt_state" == "configured and healthy" ]] || \
        fail "Telemt installation did not pass its service health-check"
    telemt_json="$TEMP_DIR/telemt-links.json"
    root_exec "$BINARY_PATH" --json telemt link "$FIRST_USER_NAME" >"$telemt_json" || \
        fail "Telemt is running, but its connection links could not be rendered"
    chmod 0600 "$telemt_json"
    mtproto_link="$(python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); print(next((item["uri"] for item in data["links"] if item.get("format") == "tg_uri"), ""))' "$telemt_json")"
    mtproto_https_link="$(python3 -c 'import json,sys; data=json.load(open(sys.argv[1])); print(next((item["uri"] for item in data["links"] if item.get("format") == "https_uri"), ""))' "$telemt_json")"
    [[ -n "$mtproto_link" && -n "$mtproto_https_link" ]] || \
        fail "Telemt connection links are missing after configuration"
    credentials_file="$DATA_DIR/telemt-$FIRST_USER_NAME.txt"
    credentials_tmp="$TEMP_DIR/telemt-credentials.txt"
    (umask 077; {
        printf 'Telegram: %s\n' "$mtproto_link"
        printf 'Telegram HTTPS: %s\n' "$mtproto_https_link"
    } >"$credentials_tmp")
    root_exec install -m 0600 "$credentials_tmp" "$credentials_file"
    if [[ "$LANGUAGE" == "ru" ]]; then
        printf '\n====================================================================\n'
        printf '                    УСТАНОВКА ЗАВЕРШЕНА\n'
        printf '====================================================================\n\n'
        printf 'Ссылка для подключения Telegram Proxy:\n%s\n\n' "$mtproto_link"
        printf 'HTTPS-ссылка:\n%s\n\n' "$mtproto_https_link"
        printf 'Копия сохранена с правами 0600: %s\n' "$credentials_file"
    else
        printf '\n====================================================================\n'
        printf '                      INSTALLATION SUCCESS\n'
        printf '====================================================================\n\n'
        printf 'Your Telegram Proxy connection link:\n%s\n\n' "$mtproto_link"
        printf 'HTTPS connection link:\n%s\n\n' "$mtproto_https_link"
        printf 'A mode-0600 copy is stored at: %s\n' "$credentials_file"
    fi
fi

printf '\n%s\n' "$DONE_TEXT"
printf '  xraydo: %s\n' "$BINARY_PATH"
printf '  config: %s\n' "$CONFIG_PATH"
printf '  state: %s\n' "$DATA_DIR"
printf '  Xray: %s\n  Hysteria 2: %s\n  Telemt: %s\n' "$xray_state" "$hysteria_state" "$telemt_state"
if [[ "$PREFIX" == "/usr/local" && "$INIT_SYSTEM" != "none" && "$CORES" != "none" ]]; then
    printf '  core autostart: enabled via %s; services resume after reboot\n' "$INIT_SYSTEM"
fi
if [[ "$FIRST_USER_MODE" == "skip" ]]; then
    printf '  first user: skipped; no credentials or subscription were created\n'
else
    if [[ "$FIRST_USER_LIMITS" == "unlimited" ]]; then
        printf '  first user: %s; limits=unlimited, devices=1\n' "$FIRST_USER_NAME"
    else
        printf '  first user: %s; limits=%s, duration=%s, traffic=%s, devices=1, IP=%s\n' \
            "$FIRST_USER_NAME" "$FIRST_USER_LIMITS" "$FIRST_USER_DURATION" "$FIRST_USER_TRAFFIC" "$FIRST_USER_IPS"
    fi
fi
printf '  command: sudo xraydo\n'
