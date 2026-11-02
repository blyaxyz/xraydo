from __future__ import annotations

import argparse
import shlex
import sys
from collections.abc import Callable, Sequence
from typing import Optional

from .i18n import Translator
from .terminal_ui import (
    command_card, command_result, main_header, menu_option, navigation as styled_navigation,
    panel, prompt as styled_prompt, section_header,
)

CommandRunner = Callable[[Sequence[str]], int]


class _Navigate(Exception):
    def __init__(self, destination: str) -> None:
        super().__init__(destination)
        self.destination = destination


def _read(prompt: str, default: Optional[str] = None) -> str:
    try:
        value = input(styled_prompt(prompt, stream=sys.stdout)).strip()
    except (EOFError, KeyboardInterrupt) as error:
        print()
        raise _Navigate("quit") from error
    lowered = value.casefold()
    if lowered in {"q", "quit", "exit", "выход"}:
        raise _Navigate("quit")
    if lowered in {"m", "main", "home", "главное", "главная"}:
        raise _Navigate("main")
    if lowered in {"b", "back", "назад"}:
        raise _Navigate("back")
    if lowered in {"h", "help", "?", "помощь"}:
        raise _Navigate("help")
    return value or (default or "")


def _required(translator: Translator, key: str, default: Optional[str] = None) -> str:
    while True:
        value = _read(translator.get(key), default)
        if value:
            return value
        print(translator.get("menu.error.required"))


def _required_prompt(translator: Translator, prompt: str) -> str:
    while True:
        value = _read(prompt)
        if value:
            return value
        print(translator.get("menu.error.required"))


def _confirm(translator: Translator) -> bool:
    return _read(translator.get("menu.confirm"), "no").casefold() in {
        "y", "yes", "да", "д",
    }


def _show_help(translator: Translator) -> None:
    print("\n" + panel(
        translator.get("menu.help.title"), translator.get("menu.help.body"),
        accent="cyan", stream=sys.stdout,
    ))


def _execute(runner: CommandRunner, translator: Translator, arguments: Sequence[str]) -> int:
    rendered = shlex.join(["xraydo", *arguments])
    print("\n" + command_card(
        translator.get("menu.command", command=rendered), stream=sys.stdout
    ) + "\n")
    try:
        result = runner(arguments)
    except SystemExit as error:
        result = error.code if isinstance(error.code, int) else 2
    print(command_result(result, stream=sys.stdout) + "  "
          + translator.get("menu.command.result", code=result))
    return result


def _submenu(
    translator: Translator,
    title: str,
    items: Sequence[tuple[str, str, Callable[[], None]]],
) -> str:
    while True:
        print("\n" + section_header(title, stream=sys.stdout) + "\n")
        for choice, label, _handler in items:
            print(menu_option(choice, label, stream=sys.stdout))
        print("\n  " + styled_navigation(translator.get("menu.navigation"), stream=sys.stdout))
        try:
            choice = _read(translator.get("menu.choice")) or "1"
        except _Navigate as navigation:
            if navigation.destination == "back":
                return "main"
            if navigation.destination == "help":
                _show_help(translator)
                continue
            return navigation.destination
        if choice == "0":
            return "main"
        handler = next((handler for number, _label, handler in items if number == choice), None)
        if handler is None:
            print(translator.get("menu.error.choice"))
            continue
        try:
            handler()
        except _Navigate as navigation:
            if navigation.destination == "back":
                continue
            if navigation.destination == "help":
                _show_help(translator)
                continue
            return navigation.destination


def _users_menu(runner: CommandRunner, translator: Translator) -> str:
    def create() -> None:
        name = _required(translator, "menu.prompt.user", "default")
        _execute(runner, translator, ["user", "create", name])

    def show() -> None:
        reference = _required(translator, "menu.prompt.user")
        _execute(runner, translator, ["user", "show", reference])

        def refresh() -> None:
            _execute(runner, translator, ["user", "show", reference])

        def card_rename() -> None:
            _execute(runner, translator, [
                "user", "rename", reference,
                _required(translator, "menu.prompt.new_name"),
            ])

        def card_enabled() -> None:
            _execute(runner, translator, [
                "user", "set-enabled", reference,
                _required(translator, "menu.prompt.on_off", "on"),
            ])

        def card_note() -> None:
            _execute(runner, translator, [
                "user", "set-note", reference,
                _required(translator, "menu.prompt.note"),
            ])

        def card_tags() -> None:
            _execute(runner, translator, [
                "user", "set-tags", reference,
                _required(translator, "menu.prompt.tags"),
            ])

        def card_key_create() -> None:
            _execute(runner, translator, [
                "key", "create", "--user", reference, "--device",
                _required(translator, "menu.prompt.device", "default"),
            ])

        def card_key_state(action: str, confirm: bool = False) -> Callable[[], None]:
            def run() -> None:
                key_id = _required(translator, "menu.prompt.key")
                if confirm and not _confirm(translator):
                    print(translator.get("menu.cancelled"))
                    return
                _execute(runner, translator, ["key", action, key_id])
            return run

        def card_delete_plan() -> None:
            _execute(runner, translator, [
                "user", "delete-plan", reference, "--grace",
                _required(translator, "menu.prompt.grace", "1d"),
            ])

        def card_delete() -> None:
            grace = _required(translator, "menu.prompt.grace", "1d")
            if not _confirm(translator):
                print(translator.get("menu.cancelled"))
                return
            _execute(runner, translator, [
                "user", "delete", reference, "--grace", grace, "--yes",
            ])

        def card_purge() -> None:
            if not _confirm(translator):
                print(translator.get("menu.cancelled"))
                return
            _execute(runner, translator, ["user", "purge", reference, "--yes"])

        def card_subscription(action: str, confirm: bool = False) -> Callable[[], None]:
            def run() -> None:
                if confirm and not _confirm(translator):
                    print(translator.get("menu.cancelled"))
                    return
                arguments = ["subscription", action, reference]
                if action == "show":
                    arguments.extend(["--format", "plain"])
                _execute(runner, translator, arguments)
            return run

        _submenu(
            translator,
            translator.get("menu.users.card", user=reference),
            (
                ("1", translator.get("menu.users.refresh"), refresh),
                ("2", translator.get("menu.users.rename"), card_rename),
                ("3", translator.get("menu.users.enabled"), card_enabled),
                ("4", translator.get("menu.users.note"), card_note),
                ("5", translator.get("menu.users.tags"), card_tags),
                ("6", translator.get("menu.keys.list_full"), lambda: _execute(
                    runner, translator, ["key", "list", "--user", reference, "--full"]
                )),
                ("7", translator.get("menu.keys.create"), card_key_create),
                ("8", translator.get("menu.subscription.show"), card_subscription("show")),
                ("9", translator.get("menu.subscription.rotate"), card_subscription("rotate", True)),
                ("10", translator.get("menu.subscription.revoke"), card_subscription("revoke", True)),
                ("11", translator.get("menu.keys.disable"), card_key_state("disable")),
                ("12", translator.get("menu.keys.enable"), card_key_state("enable")),
                ("13", translator.get("menu.keys.revoke"), card_key_state("revoke", True)),
                ("14", translator.get("menu.users.delete_plan"), card_delete_plan),
                ("15", translator.get("menu.users.delete"), card_delete),
                ("16", translator.get("menu.users.delete_status"), lambda: _execute(
                    runner, translator, ["user", "delete-status", reference]
                )),
                ("17", translator.get("menu.users.purge"), card_purge),
            ),
        )
        _execute(runner, translator, ["user", "list"])

    def search() -> None:
        _execute(runner, translator, ["user", "search", _required(translator, "menu.prompt.query")])

    def rename() -> None:
        reference = _required(translator, "menu.prompt.user")
        new_name = _required(translator, "menu.prompt.new_name")
        _execute(runner, translator, ["user", "rename", reference, new_name])

    def enabled() -> None:
        reference = _required(translator, "menu.prompt.user")
        value = _required(translator, "menu.prompt.on_off", "on")
        _execute(runner, translator, ["user", "set-enabled", reference, value])

    def note() -> None:
        reference = _required(translator, "menu.prompt.user")
        value = _required(translator, "menu.prompt.note")
        _execute(runner, translator, ["user", "set-note", reference, value])

    def tags() -> None:
        reference = _required(translator, "menu.prompt.user")
        value = _required(translator, "menu.prompt.tags")
        _execute(runner, translator, ["user", "set-tags", reference, value])

    def delete_plan() -> None:
        reference = _required(translator, "menu.prompt.user")
        grace = _required(translator, "menu.prompt.grace", "1d")
        _execute(runner, translator, ["user", "delete-plan", reference, "--grace", grace])

    def delete_request() -> None:
        reference = _required(translator, "menu.prompt.user")
        grace = _required(translator, "menu.prompt.grace", "1d")
        if not _confirm(translator):
            print(translator.get("menu.cancelled"))
            return
        _execute(runner, translator, ["user", "delete", reference, "--grace", grace, "--yes"])

    def deletion_status() -> None:
        reference = _required(translator, "menu.prompt.user")
        _execute(runner, translator, ["user", "delete-status", reference])

    def purge() -> None:
        reference = _required(translator, "menu.prompt.user")
        if not _confirm(translator):
            print(translator.get("menu.cancelled"))
            return
        _execute(runner, translator, ["user", "purge", reference, "--yes"])

    _execute(runner, translator, ["user", "list"])
    return _submenu(
        translator,
        translator.get("menu.users.title"),
        (
            (
                "1",
                translator.get("menu.users.list"),
                lambda: _execute(runner, translator, ["user", "list"]),
            ),
            ("2", translator.get("menu.users.create"), create),
            ("3", translator.get("menu.users.show"), show),
            ("4", translator.get("menu.users.search"), search),
            ("5", translator.get("menu.users.rename"), rename),
            ("6", translator.get("menu.users.enabled"), enabled),
            ("7", translator.get("menu.users.note"), note),
            ("8", translator.get("menu.users.tags"), tags),
            ("9", translator.get("menu.users.delete_plan"), delete_plan),
            ("10", translator.get("menu.users.delete"), delete_request),
            ("11", translator.get("menu.users.delete_status"), deletion_status),
            ("12", translator.get("menu.users.purge"), purge),
        ),
    )


def _access_menu(runner: CommandRunner, translator: Translator) -> str:
    def key_list() -> None:
        user = _required(translator, "menu.prompt.user")
        _execute(runner, translator, ["key", "list", "--user", user])

    def key_create() -> None:
        user = _required(translator, "menu.prompt.user")
        device = _required(translator, "menu.prompt.device", "default")
        _execute(runner, translator, ["key", "create", "--user", user, "--device", device])

    def key_show() -> None:
        _execute(runner, translator, ["key", "show", _required(translator, "menu.prompt.key")])

    def key_state(action: str) -> Callable[[], None]:
        def run() -> None:
            key_id = _required(translator, "menu.prompt.key")
            if action == "revoke" and not _confirm(translator):
                print(translator.get("menu.cancelled"))
                return
            _execute(runner, translator, ["key", action, key_id])
        return run

    def subscription(action: str, *, confirm: bool = False) -> Callable[[], None]:
        def run() -> None:
            user = _required(translator, "menu.prompt.user")
            if confirm and not _confirm(translator):
                print(translator.get("menu.cancelled"))
                return
            arguments = ["subscription", action, user]
            if action == "show":
                arguments.extend(["--format", "plain"])
            _execute(runner, translator, arguments)
        return run

    return _submenu(
        translator,
        translator.get("menu.access.title"),
        (
            ("1", translator.get("menu.keys.list"), key_list),
            ("2", translator.get("menu.keys.create"), key_create),
            ("3", translator.get("menu.keys.show"), key_show),
            ("4", translator.get("menu.keys.disable"), key_state("disable")),
            ("5", translator.get("menu.keys.enable"), key_state("enable")),
            ("6", translator.get("menu.keys.revoke"), key_state("revoke")),
            ("7", translator.get("menu.subscription.status"), subscription("status")),
            ("8", translator.get("menu.subscription.show"), subscription("show")),
            ("9", translator.get("menu.subscription.update"), subscription("update")),
            (
                "10",
                translator.get("menu.subscription.rotate"),
                subscription("rotate", confirm=True),
            ),
            (
                "11",
                translator.get("menu.subscription.revoke"),
                subscription("revoke", confirm=True),
            ),
        ),
    )


def _nodes_menu(runner: CommandRunner, translator: Translator) -> str:
    def add() -> None:
        name = _required(translator, "menu.prompt.node_name")
        alias = _read(translator.get("menu.prompt.alias"))
        arguments = ["node", "add", name]
        if alias:
            arguments.extend(["--alias", alias])
        _execute(runner, translator, arguments)

    def node_command(action: str) -> Callable[[], None]:
        def run() -> None:
            node = _required(translator, "menu.prompt.node")
            _execute(runner, translator, ["node", action, node])
        return run

    def maintenance() -> None:
        node = _required(translator, "menu.prompt.node")
        value = _required(translator, "menu.prompt.on_off", "on")
        _execute(runner, translator, ["node", "maintenance", node, value])

    def enabled() -> None:
        node = _required(translator, "menu.prompt.node")
        value = _required(translator, "menu.prompt.on_off", "on")
        _execute(runner, translator, ["node", "set-enabled", node, value])

    def sync(action: str) -> Callable[[], None]:
        def run() -> None:
            node = _required(translator, "menu.prompt.node")
            _execute(runner, translator, ["sync", action, "--node", node])
        return run

    return _submenu(
        translator,
        translator.get("menu.nodes.title"),
        (
            (
                "1",
                translator.get("menu.nodes.list"),
                lambda: _execute(runner, translator, ["node", "list"]),
            ),
            ("2", translator.get("menu.nodes.add"), add),
            ("3", translator.get("menu.nodes.show"), node_command("show")),
            ("4", translator.get("menu.nodes.status"), node_command("status")),
            ("5", translator.get("menu.nodes.maintenance"), maintenance),
            ("6", translator.get("menu.nodes.enabled"), enabled),
            ("7", translator.get("menu.sync.list"), sync("list")),
            ("8", translator.get("menu.sync.summary"), sync("summary")),
        ),
    )


def _inbounds_menu(runner: CommandRunner, translator: Translator) -> str:
    def inbound_reference(action: str) -> Callable[[], None]:
        def run() -> None:
            inbound = _required(translator, "menu.prompt.inbound")
            _execute(runner, translator, ["inbound", action, inbound])
        return run

    def scan() -> None:
        target = _required(translator, "menu.prompt.target")
        _execute(runner, translator, ["reality", "scan", "--target", target])

    def import_inbound() -> None:
        path = _required(translator, "menu.prompt.file")
        _execute(runner, translator, ["inbound", "import", "--file", path])

    return _submenu(
        translator,
        translator.get("menu.inbounds.title"),
        (
            (
                "1",
                translator.get("menu.inbounds.list"),
                lambda: _execute(runner, translator, ["inbound", "list"]),
            ),
            ("2", translator.get("menu.inbounds.show"), inbound_reference("show")),
            ("3", translator.get("menu.inbounds.history"), inbound_reference("history")),
            ("4", translator.get("menu.inbounds.import"), import_inbound),
            ("5", translator.get("menu.reality.scan"), scan),
            (
                "6",
                translator.get("menu.reality.utls"),
                lambda: _execute(runner, translator, ["reality", "utls"]),
            ),
        ),
    )


def _cores_menu(runner: CommandRunner, translator: Translator) -> str:
    def service_action() -> None:
        core = _required(translator, "menu.prompt.core", "telemt").casefold()
        if core not in {"telemt", "hysteria"}:
            print(translator.get("menu.error.core"))
            return
        action = _required(translator, "menu.prompt.service_action", "status").casefold()
        if action not in {"start", "stop", "restart", "status", "version", "logs"}:
            print(translator.get("menu.error.action"))
            return
        if action in {"stop", "restart"} and not _confirm(translator):
            print(translator.get("menu.cancelled"))
            return
        _execute(runner, translator, [core, action])

    def diagnostics(core: str) -> Callable[[], None]:
        return lambda: _execute(runner, translator, [core, "diagnostics"])

    return _submenu(
        translator,
        translator.get("menu.cores.title"),
        (
            (
                "1",
                translator.get("menu.item.status"),
                lambda: _execute(runner, translator, ["status"]),
            ),
            ("2", translator.get("menu.cores.service"), service_action),
            ("3", translator.get("menu.cores.telemt_diagnostics"), diagnostics("telemt")),
            ("4", translator.get("menu.cores.hysteria_diagnostics"), diagnostics("hysteria")),
            (
                "5",
                translator.get("menu.cores.telemt_links"),
                lambda: _execute(
                    runner,
                    translator,
                    ["telemt", "link", _required(translator, "menu.prompt.user")],
                ),
            ),
        ),
    )


def _tls_menu(runner: CommandRunner, translator: Translator) -> str:
    def issue() -> None:
        kind = _required(translator, "menu.prompt.domain_ip", "ip").casefold()
        value = _required(translator, "menu.prompt.host")
        option = "--domain" if kind == "domain" else "--ip"
        _execute(runner, translator, ["tls", "issue-self", option, value])

    def export_ca() -> None:
        output = _required(translator, "menu.prompt.output", "./xraydo-ca.crt")
        _execute(runner, translator, ["tls", "export-ca", "--output", output])

    def sync_ip() -> None:
        address = _read(translator.get("menu.prompt.ip_optional"))
        arguments = ["tls", "sync-ip"]
        if address:
            arguments.extend(["--ip", address])
        _execute(runner, translator, arguments)

    return _submenu(
        translator,
        translator.get("menu.tls.title"),
        (
            (
                "1",
                translator.get("menu.tls.status"),
                lambda: _execute(runner, translator, ["tls", "status"]),
            ),
            (
                "2",
                translator.get("menu.tls.init"),
                lambda: _execute(runner, translator, ["tls", "init-ca"]),
            ),
            ("3", translator.get("menu.tls.issue"), issue),
            (
                "4",
                translator.get("menu.tls.renew"),
                lambda: _execute(runner, translator, ["tls", "renew"]),
            ),
            ("5", translator.get("menu.tls.export"), export_ca),
            ("6", translator.get("menu.tls.sync_ip"), sync_ip),
        ),
    )


def _settings_menu(runner: CommandRunner, translator: Translator) -> str:
    def setting(command: str, prompt: str, default: Optional[str] = None) -> Callable[[], None]:
        def run() -> None:
            value = _required(translator, prompt, default)
            _execute(runner, translator, ["settings", command, value])
        return run

    def role() -> None:
        value = _required(translator, "menu.prompt.role", "standalone")
        _execute(runner, translator, ["role", "set", value])

    return _submenu(
        translator,
        translator.get("menu.settings.title"),
        (
            (
                "1",
                translator.get("menu.settings.language"),
                setting("language", "menu.prompt.language", "en"),
            ),
            (
                "2",
                translator.get("menu.settings.flags"),
                setting("country-flags", "menu.prompt.on_off", "on"),
            ),
            (
                "3",
                translator.get("menu.settings.subscription_url"),
                setting("subscription-url", "menu.prompt.url", "off"),
            ),
            (
                "4",
                translator.get("menu.settings.role_show"),
                lambda: _execute(runner, translator, ["role", "show"]),
            ),
            ("5", translator.get("menu.settings.role_set"), role),
            (
                "6",
                translator.get("menu.settings.audit"),
                lambda: _execute(
                    runner, translator, ["audit", "list", "--limit", "100"]
                ),
            ),
            (
                "7",
                translator.get("menu.settings.compact_preview"),
                lambda: _execute(runner, translator, ["state", "compact", "--dry-run"]),
            ),
        ),
    )


def _advanced(runner: CommandRunner, translator: Translator) -> None:
    raw = _required(translator, "menu.prompt.command")
    try:
        arguments = shlex.split(raw)
    except ValueError:
        print(translator.get("menu.error.command"))
        return
    if arguments and arguments[0] == "xraydo":
        arguments = arguments[1:]
    if not arguments:
        print(translator.get("menu.error.command"))
        return
    _execute(runner, translator, arguments)


def _system_menu(runner: CommandRunner, translator: Translator) -> str:
    def lifecycle(action: str, dry_run: bool) -> Callable[[], None]:
        def run() -> None:
            prefix = _required(translator, "menu.prompt.prefix", "/usr/local")
            if not dry_run and not _confirm(translator):
                print(translator.get("menu.cancelled"))
                return
            arguments = ["system", action, "--prefix", prefix]
            arguments.append("--dry-run" if dry_run else "--yes")
            _execute(runner, translator, arguments)
        return run

    return _submenu(
        translator,
        translator.get("menu.system.title"),
        (
            ("1", translator.get("menu.system.status"),
             lambda: _execute(runner, translator, ["system", "status"])),
            ("2", translator.get("menu.system.update_plan"), lifecycle("update", True)),
            ("3", translator.get("menu.system.update"), lifecycle("update", False)),
            ("4", translator.get("menu.system.uninstall_plan"), lifecycle("uninstall", True)),
            ("5", translator.get("menu.system.uninstall"), lifecycle("uninstall", False)),
            ("6", translator.get("menu.system.purge_plan"), lifecycle("purge", True)),
            ("7", translator.get("menu.system.purge"), lifecycle("purge", False)),
        ),
    )


def _subcommands(command_parser: argparse.ArgumentParser) -> list[tuple[str, argparse.ArgumentParser]]:
    for action in command_parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            unique: list[tuple[str, argparse.ArgumentParser]] = []
            seen: set[int] = set()
            for name, child in action.choices.items():
                if id(child) in seen:
                    continue
                seen.add(id(child))
                unique.append((name, child))
            return unique
    return []


def _catalog_leaf(
    runner: CommandRunner, translator: Translator, command_parser: argparse.ArgumentParser,
    prefix: list[str],
) -> None:
    arguments = list(prefix)
    for action in command_parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        if not action.option_strings:
            optional = action.nargs in {"?", "*"}
            prompt = translator.get("menu.prompt.argument", name=action.dest)
            value = _read(prompt) if optional else _required_prompt(translator, prompt)
            if value:
                arguments.extend(shlex.split(value) if action.nargs in {"*", "+"} else [value])
        elif action.required:
            option = next((item for item in action.option_strings if item.startswith("--")),
                          action.option_strings[0])
            value = _required_prompt(
                translator, translator.get("menu.prompt.option", name=option)
            )
            arguments.extend([option, value])
    extra = _read(translator.get("menu.prompt.extra_options"))
    if extra:
        try:
            arguments.extend(shlex.split(extra))
        except ValueError:
            print(translator.get("menu.error.command"))
            return
    _execute(runner, translator, arguments)


def _command_catalog(
    runner: CommandRunner, translator: Translator, command_parser: argparse.ArgumentParser,
    prefix: Optional[list[str]] = None,
) -> None:
    prefix = prefix or []
    while True:
        children = _subcommands(command_parser)
        if not children:
            _catalog_leaf(runner, translator, command_parser, prefix)
            return
        title = "xraydo " + " ".join(prefix) if prefix else translator.get("menu.catalog.title")
        print("\n" + section_header(title, root="xraydo CLI", stream=sys.stdout) + "\n")
        for index, (name, child) in enumerate(children, 1):
            description = child.description or child.format_usage().strip()
            print(menu_option(str(index), f"{name} — {description}", stream=sys.stdout))
        print("\n  " + styled_navigation(translator.get("menu.navigation"), stream=sys.stdout))
        try:
            choice = _read(translator.get("menu.choice"))
        except _Navigate as navigation:
            if navigation.destination in {"back", "main"}:
                return
            if navigation.destination == "help":
                _show_help(translator)
                continue
            raise
        if choice == "0":
            return
        try:
            selected = children[int(choice) - 1]
        except (ValueError, IndexError):
            print(translator.get("menu.error.choice"))
            continue
        name, child = selected
        if _subcommands(child):
            _command_catalog(runner, translator, child, [*prefix, name])
        else:
            _catalog_leaf(runner, translator, child, [*prefix, name])


def run_interactive(
    translator: Translator, runner: CommandRunner,
    command_parser: Optional[argparse.ArgumentParser] = None,
) -> int:
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(translator.get("error.non_tty"), file=sys.stderr)
        return 2
    menus: dict[str, Callable[[], str]] = {
        "1": lambda: _users_menu(runner, translator),
        "2": lambda: _access_menu(runner, translator),
        "3": lambda: _nodes_menu(runner, translator),
        "4": lambda: _inbounds_menu(runner, translator),
        "5": lambda: _cores_menu(runner, translator),
        "6": lambda: _tls_menu(runner, translator),
        "7": lambda: _settings_menu(runner, translator),
        "10": lambda: _system_menu(runner, translator),
    }
    while True:
        print("\n" + main_header(
            translator.get("menu.header"), translator.get("menu.subtitle"), stream=sys.stdout
        ) + "\n")
        entries = (
            ("1", "menu.item.users"), ("2", "menu.item.access"),
            ("3", "menu.item.nodes"), ("4", "menu.item.inbounds"),
            ("5", "menu.item.cores"), ("6", "menu.item.tls"),
            ("7", "menu.item.settings"), ("8", "menu.item.status"),
            ("9", "menu.item.advanced"), ("10", "menu.item.system"),
            ("11", "menu.item.catalog"), ("0", "menu.item.exit"),
        )
        for number, key in entries:
            print(menu_option(number, translator.get(key), stream=sys.stdout))
        print("\n  " + styled_navigation(translator.get("menu.navigation"), stream=sys.stdout))
        try:
            choice = _read(f"\n{translator.get('menu.choice')}") or "1"
        except _Navigate as navigation:
            if navigation.destination == "quit":
                return 0
            if navigation.destination == "help":
                _show_help(translator)
            continue
        if choice == "0":
            return 0
        if choice == "8":
            _execute(runner, translator, ["status"])
            continue
        if choice == "9":
            try:
                _advanced(runner, translator)
            except _Navigate as navigation:
                if navigation.destination == "quit":
                    return 0
            continue
        if choice == "11":
            if command_parser is None:
                from .cli import parser as build_parser
                command_parser = build_parser()
            try:
                _command_catalog(runner, translator, command_parser)
            except _Navigate as navigation:
                if navigation.destination == "quit":
                    return 0
            continue
        menu = menus.get(choice)
        if menu is None:
            print(translator.get("menu.error.choice"))
            continue
        if menu() == "quit":
            return 0
