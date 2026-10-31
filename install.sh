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
readonly XRAYDO_REF="ec7eedb7381645d34b6827821b781da3d406e540"
readonly XRAYDO_ARCHIVE_SHA256="d34331f1b47e9da4779ab467f6e1b10553cdd18445f475aa14711cc9d6beec31"
readonly XRAYDO_ARCHIVE_URL="https://github.com/blyaxyz/xraydo/archive/${XRAYDO_REF}.tar.gz"
readonly XRAY_VERSION="v26.10.29"
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
[[ -z "$PROFILE" || "$PROFILE" =~ ^(quick|master|node|advanced)$ ]] || fail "unsupported profile: $PROFILE"
[[ -z "$ROLE" || "$ROLE" =~ ^(standalone|master|node)$ ]] || fail "unsupported role: $ROLE"
[[ -z "$CORES" || "$CORES" =~ ^(none|((xray|hysteria|telemt)(,(xray|hysteria|telemt))*))$ ]] || fail "--cores accepts xray,hysteria,telemt or none"
[[ "$TLS_MODE" =~ ^(auto|domain|public-ip|private-ca)$ ]] || fail "unsupported TLS mode: $TLS_MODE"
[[ "$(uname -s)" == "Linux" ]] || fail "only Linux is supported"

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

xray_port="$(next_free_port tcp 443)"
hysteria_port="$(next_free_port udp 1935)"
telemt_port="$(next_free_port tcp 8443)"

printf '\n%s:\n' "$PLAN_TITLE"
printf '  %s: %s %s / %s / %s\n' "$PLATFORM_LABEL" "$OS_ID" "$OS_VERSION" "$ARCH" "$PACKAGE_MANAGER"
printf '  Profile / Профиль: %s\n' "$PROFILE"
printf '  Role / Роль: %s\n' "$ROLE"
printf '  %s: %s\n' "$PACKAGES_LABEL" "$packages_display"
printf '  %s: %s @ %s\n' "$SOURCE_LABEL" "$XRAYDO_ARCHIVE_URL" "$XRAYDO_REF"
printf '  SHA-256: %s\n' "$XRAYDO_ARCHIVE_SHA256"
printf '  %s: %s\n' "$PREFIX_LABEL" "$PREFIX"
printf '  %s: %s\n' "$CORES_LABEL" "$CORES"
printf '  TLS: %s\n' "$TLS_MODE"
printf '  Init: %s\n' "$INIT_SYSTEM"
printf '  Security / Защита: %s (unchanged)\n' "$security_modules"
printf '  RAM available / Доступно RAM: %s MB\n' "$memory_mb"
printf '  Disk available / Доступно диска: %s MB\n' "$disk_available_mb"
[[ ",$CORES," == *,xray,* ]] && printf '  Xray REALITY: TCP %s\n' "$xray_port"
[[ ",$CORES," == *,hysteria,* ]] && printf '  Hysteria 2: UDP %s\n' "$hysteria_port"
[[ ",$CORES," == *,telemt,* ]] && printf '  Telemt: TCP %s\n' "$telemt_port"
printf '  Firewall / Файрвол: unchanged / без изменений\n'

if [[ "$DRY_RUN" == true ]]; then
    printf '{"action":"install","arch":"%s","cores":"%s","dry_run":true,"language":"%s","os":"%s","package_manager":"%s","profile":"%s","ref":"%s","role":"%s"}\n' \
        "$ARCH" "$CORES" "$LANGUAGE" "$OS_ID" "$PACKAGE_MANAGER" "$PROFILE" "$XRAYDO_REF" "$ROLE"
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

root_exec() {
    if ((EUID == 0)); then
        "$@"
    else
        command -v sudo >/dev/null 2>&1 || fail "root privileges are required; run as root or install sudo"
        sudo "$@"
    fi
}

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
    curl --fail --location --silent --show-error \
        --proto '=https' --tlsv1.2 \
        --connect-timeout 15 --retry 3 \
        --output "$destination" "$url"
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

printf '%s\n' "$INSTALL_TEXT"
install_exec install -d -m 0755 "$LIBRARY_DIR/xraydo/locales" "$LIBRARY_DIR/xraydo/data" "$PREFIX/bin" "$CONFIG_DIR" "$DOCUMENTATION_DIR"
install_exec install -m 0644 "$SOURCE_DIR"/src/xraydo/*.py "$LIBRARY_DIR/xraydo/"
install_exec install -m 0644 "$SOURCE_DIR"/src/xraydo/locales/*.json "$LIBRARY_DIR/xraydo/locales/"
if compgen -G "$SOURCE_DIR/src/xraydo/data/*.json" >/dev/null; then
    install_exec install -m 0644 "$SOURCE_DIR"/src/xraydo/data/*.json "$LIBRARY_DIR/xraydo/data/"
fi
install_exec install -m 0644 "$SOURCE_DIR/LICENSE" "$SOURCE_DIR/THIRD_PARTY_NOTICES.md" "$DOCUMENTATION_DIR/"

wrapper_path="$TEMP_DIR/xraydo-wrapper"
printf '#!/usr/bin/env bash\nXRAYDO_CONFIG=%q PYTHONPATH=%q exec python3 -m xraydo.cli "$@"\n' \
    "$CONFIG_PATH" "$LIBRARY_DIR" > "$wrapper_path"
chmod 0755 "$wrapper_path"
install_exec install -m 0755 "$wrapper_path" "$BINARY_PATH"

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
        xray_checksum="8a5f3e6a1057e4bd0f8a7c57c9d91204b4be0b74222a6e210d070603101163c2"
    else
        xray_asset="Xray-linux-arm64-v8a.zip"
        xray_checksum="afe01f19045b05237420328028ee1ee2e1edd2100eb41a8cbbf50e683b0a6e58"
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
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
ReadWritePaths=$data_dir /var/log/xraydo/$core
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

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

provision_tls_renewer() {
    root_exec install -d -m 0700 /var/lib/xraydo
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

XRAYDO_DATA_DIR="$TEMP_DIR/xraydo-smoke" "$BINARY_PATH" --json status >/dev/null
XRAYDO_DATA_DIR="$TEMP_DIR/xraydo-smoke" "$BINARY_PATH" tls renew-daemon --once >/dev/null

if [[ "$PREFIX" == "/usr/local" && "$INIT_SYSTEM" != "none" ]]; then
    [[ ",$CORES," == *,xray,* ]] && provision_core_service xray "$PREFIX/bin/xray" "run -config $CONFIG_DIR/cores/xray/config.json" config.json
    [[ ",$CORES," == *,hysteria,* ]] && provision_core_service hysteria "$PREFIX/bin/hysteria" "server -c $CONFIG_DIR/cores/hysteria/config.yaml" config.yaml
    [[ ",$CORES," == *,telemt,* ]] && provision_core_service telemt "$PREFIX/bin/telemt" "$CONFIG_DIR/cores/telemt/telemt.toml" telemt.toml
    provision_tls_renewer
    if [[ "$INIT_SYSTEM" == "systemd" ]]; then
        root_exec systemctl daemon-reload
        root_exec systemctl enable --now xraydo-tls-renew.service
    else
        root_exec rc-update add xraydo-tls-renew default
        root_exec rc-service xraydo-tls-renew start
    fi
fi

printf '\n%s\n' "$DONE_TEXT"
printf '  xraydo: %s\n' "$BINARY_PATH"
printf '  config: %s\n' "$CONFIG_PATH"
printf '  command: xraydo\n\n'

if [[ "$PREFIX" == "/usr/local" && "$INIT_SYSTEM" != "none" && "$CORES" != "none" ]]; then
    printf '  services: installed but disabled until valid configs exist\n'
    printf '  service configs: %s/cores\n' "$CONFIG_DIR"
fi

[[ -x "$PREFIX/bin/xray" ]] && "$PREFIX/bin/xray" version
[[ -x "$PREFIX/bin/hysteria" ]] && "$PREFIX/bin/hysteria" version
[[ -x "$PREFIX/bin/telemt" ]] && "$PREFIX/bin/telemt" --version

if [[ -x "$BINARY_PATH" ]]; then
    XRAYDO_DATA_DIR="${TMPDIR:-/tmp}/xraydo-install-check" "$BINARY_PATH" --json status
fi

if [[ "$NON_INTERACTIVE" != true && -t 0 && -t 1 ]]; then
    exec "$BINARY_PATH"
fi
