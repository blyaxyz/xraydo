from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from . import __version__
from .i18n import SUPPORTED_LANGUAGES, Translator, installed_language, requested_language
from .store import Store
from .users import UserService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="xraydo", description="Terminal-first Xray management")
    result.add_argument("--lang", choices=SUPPORTED_LANGUAGES, help="temporary output language")
    result.add_argument("--json", action="store_true", help="stable machine-readable output")
    result.add_argument("--version", action="version", version=f"xraydo {__version__}")
    commands = result.add_subparsers(dest="command")

    settings = commands.add_parser("settings")
    settings_commands = settings.add_subparsers(dest="settings_command", required=True)
    language = settings_commands.add_parser("language")
    language.add_argument("value", choices=SUPPORTED_LANGUAGES, nargs="?")

    user = commands.add_parser("user", aliases=["users"])
    user_commands = user.add_subparsers(dest="user_command", required=True)
    create = user_commands.add_parser("create", aliases=["new"])
    create.add_argument("name", nargs="?", default="default")
    user_commands.add_parser("list")
    show = user_commands.add_parser("show")
    show.add_argument("reference")
    state = user_commands.add_parser("set-enabled")
    state.add_argument("reference")
    state.add_argument("value", choices=("on", "off"))

    legacy = commands.add_parser("newUser", help="alias for 'user create'")
    legacy.add_argument("name", nargs="?", default="default")
    commands.add_parser("status")
    return result


def emit(payload: object, message: str, machine: bool) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True) if machine else message)


def interactive(service: UserService, translator: Translator) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(translator.get("error.non_tty"), file=sys.stderr)
        return 2
    while True:
        print(f"\n{translator.get('menu.header')}\n")
        print(f"  1) {translator.get('menu.item.users')}")
        print(f"  2) {translator.get('menu.item.status')}")
        print(f"  9) {translator.get('menu.item.settings')}")
        print(f"  0) {translator.get('menu.item.exit')}")
        choice = input(f"\n{translator.get('menu.choice')}").strip() or "1"
        if choice in {"0", "q", "quit"}:
            return 0
        if choice == "2":
            print(translator.get("status.ready", count=len(service.list())))
        elif choice == "1":
            print(f"\n{translator.get('menu.users.title')}\n")
            print(f"  1) {translator.get('menu.users.create')}")
            print(f"  2) {translator.get('menu.users.list')}")
            print(f"  0) {translator.get('menu.item.exit')}")
            action = input(f"\n{translator.get('menu.choice')}").strip() or "1"
            if action == "1":
                name = input(translator.get("menu.users.name")).strip() or "default"
                user, token = service.create(name)
                print(translator.get("user.created", name=user["name"], id=user["id"]))
                print(translator.get("user.secret_once", token=token))
            elif action == "2":
                print_users(service.list(), translator)


def print_users(users: list[dict[str, object]], translator: Translator) -> None:
    if not users:
        print(translator.get("users.empty"))
    for user in users:
        state = "enabled" if user["enabled"] else "disabled"
        print(f"{user['id']}  {user['name']}  {translator.get(f'user.state.{state}')}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    store = Store()
    data = store.load()
    language = requested_language(args.lang, data["settings"].get("language", installed_language()))
    translator = Translator(language)
    service = UserService(store)
    try:
        if args.command is None:
            return interactive(service, translator)
        if args.command == "settings" and args.settings_command == "language":
            if args.value:
                data["settings"]["language"] = args.value
                store.save(data)
                emit({"language": args.value}, translator.get("language.saved", language=args.value), args.json)
            else:
                current = data["settings"].get("language", installed_language())
                emit({"language": current}, translator.get("language.current", language=current), args.json)
        elif args.command in {"user", "users"}:
            if args.user_command in {"create", "new"}:
                user, token = service.create(args.name)
                emit(
                    {"user": user, "subscription_token": token},
                    translator.get("user.created", name=user["name"], id=user["id"])
                    + "\n"
                    + translator.get("user.secret_once", token=token),
                    args.json,
                )
            elif args.user_command == "list":
                users = service.list()
                if args.json:
                    emit({"users": users}, "", True)
                else:
                    print_users(users, translator)
            elif args.user_command == "show":
                user = service.find(args.reference)
                emit({"user": user}, json.dumps(user, ensure_ascii=False, indent=2), args.json)
            elif args.user_command == "set-enabled":
                user = service.set_enabled(args.reference, args.value == "on")
                state = "enabled" if user["enabled"] else "disabled"
                emit(
                    {"user": user},
                    translator.get("user.updated", name=user["name"], state=translator.get(f"user.state.{state}")),
                    args.json,
                )
        elif args.command == "newUser":
            user, token = service.create(args.name)
            emit(
                {"user": user, "subscription_token": token},
                translator.get("user.created", name=user["name"], id=user["id"])
                + "\n"
                + translator.get("user.secret_once", token=token),
                args.json,
            )
        elif args.command == "status":
            count = len(service.list())
            emit({"status": "ready", "users": count}, translator.get("status.ready", count=count), args.json)
        return 0
    except ValueError as error:
        code = str(error)
        if args.json:
            print(json.dumps({"error": {"code": code, "message": translator.get(f"error.{code}")}}, ensure_ascii=False), file=sys.stderr)
        else:
            print(translator.get(f"error.{code}"), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
