#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import sys
from pathlib import Path

VERSION = "0.1.0"
SUPPORTED_ARCHES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
MESSAGES = {
    "en": {
        "plan": "Installation plan",
        "language": "Language",
        "platform": "Platform",
        "prefix": "Prefix",
        "confirm": "Install? [Enter = yes, q = cancel]: ",
        "done": "Installed. Run: xraydo --help",
        "cancel": "Installation cancelled.",
    },
    "ru": {
        "plan": "План установки",
        "language": "Язык",
        "platform": "Платформа",
        "prefix": "Путь",
        "confirm": "Начать установку? [Enter = да, q = отмена]: ",
        "done": "Установлено. Выполните: xraydo --help",
        "cancel": "Установка отменена.",
    },
}


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install xraydo")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--lang", choices=("en", "ru"))
    parser.add_argument("--prefix", type=Path, default=Path("/usr/local"))
    parser.add_argument("--version", action="version", version=VERSION)
    return parser.parse_args()


def choose_language(explicit: str | None, interactive: bool) -> str:
    if explicit:
        return explicit
    environment = os.environ.get("XRAYDO_LANG")
    if environment in MESSAGES:
        return environment
    if not interactive:
        return "en"
    print("Select language / Выберите язык:\n\n  1) English (default)\n  2) Русский")
    return "ru" if input("\nChoice / Выбор [1]: ").strip() == "2" else "en"


def os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values[key] = value.strip().strip('"')
    return values


def install(prefix: Path, source_root: Path) -> None:
    library = prefix / "lib" / "xraydo"
    binary = prefix / "bin" / "xraydo"
    library.parent.mkdir(parents=True, exist_ok=True)
    if library.exists():
        backup = library.with_name("xraydo.previous")
        if backup.exists():
            shutil.rmtree(backup)
        library.replace(backup)
    shutil.copytree(source_root / "src" / "xraydo", library / "xraydo")
    binary.parent.mkdir(parents=True, exist_ok=True)
    temporary = binary.with_suffix(".tmp")
    temporary.write_text(
        "#!/bin/sh\nXRAYDO_CONFIG="
        + shlex.quote(str(prefix / "etc" / "xraydo" / "config.json"))
        + " PYTHONPATH="
        + shlex.quote(str(library))
        + ' exec python3 -m xraydo.cli "$@"\n',
        encoding="utf-8",
    )
    temporary.chmod(0o755)
    temporary.replace(binary)


def main() -> int:
    args = arguments()
    interactive = sys.stdin.isatty() and not args.non_interactive
    language = choose_language(args.lang, interactive)
    message = MESSAGES[language]
    machine = platform.machine().lower()
    if sys.platform != "linux" or machine not in SUPPORTED_ARCHES:
        print(f"Unsupported platform: {sys.platform}/{machine}", file=sys.stderr)
        return 2
    release = os_release()
    detected = f"{release.get('ID', 'linux')} {release.get('VERSION_ID', '?')} / {SUPPORTED_ARCHES[machine]}"
    print(f"\n{message['plan']}:\n  {message['language']}: {language}\n  {message['platform']}: {detected}\n  {message['prefix']}: {args.prefix}")
    if args.dry_run:
        print(json.dumps({"action": "install", "dry_run": True, "language": language, "platform": detected, "prefix": str(args.prefix)}, sort_keys=True))
        return 0
    if not args.yes:
        if not interactive:
            print("Use --yes with --non-interactive.", file=sys.stderr)
            return 2
        if input(f"\n{message['confirm']}").strip().lower() in {"q", "n", "no", "нет"}:
            print(message["cancel"])
            return 0
    install(args.prefix, Path(__file__).resolve().parents[1])
    config_dir = args.prefix / "etc" / "xraydo"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = config_dir / "config.json"
    if not config.exists():
        config.write_text(json.dumps({"language": language}, indent=2) + "\n", encoding="utf-8")
        config.chmod(0o600)
    print(message["done"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
