from __future__ import annotations

import io
import unittest
from collections.abc import Sequence
from unittest import mock

from xraydo.i18n import Translator
from xraydo.cli import parser
from xraydo.menu import _subcommands, run_interactive


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class InteractiveMenuTests(unittest.TestCase):
    def run_menu(self, answers: list[object]) -> tuple[int, list[list[str]], str]:
        commands: list[list[str]] = []
        output = TTYBuffer()

        def runner(arguments: Sequence[str]) -> int:
            commands.append(list(arguments))
            return 0

        with (
            mock.patch("xraydo.menu.sys.stdin", TTYBuffer()),
            mock.patch("xraydo.menu.sys.stdout", output),
            mock.patch("builtins.input", side_effect=answers),
        ):
            result = run_interactive(Translator("en"), runner)
        return result, commands, output.getvalue()

    def test_main_status_and_exit_use_cli_runner(self) -> None:
        result, commands, output = self.run_menu(["8", "0"])
        self.assertEqual(result, 0)
        self.assertEqual(commands, [["status"]])
        self.assertIn("Access keys and subscriptions", output)
        self.assertIn("Running: xraydo status", output)

    def test_nested_user_creation_and_back_navigation(self) -> None:
        result, commands, _output = self.run_menu(["1", "2", "alice", "0", "0"])
        self.assertEqual(result, 0)
        self.assertEqual(commands, [["user", "list"], ["user", "create", "alice"]])

    def test_user_card_lists_immediately_and_reuses_selected_user(self) -> None:
        result, commands, output = self.run_menu([
            "1", "3", "alice", "6", "0", "0", "0",
        ])
        self.assertEqual(result, 0)
        self.assertEqual(commands, [
            ["user", "list"],
            ["user", "show", "alice"],
            ["key", "list", "--user", "alice", "--full"],
            ["user", "list"],
        ])
        self.assertIn("Show all access keys and secrets", output)

    def test_advanced_command_uses_shlex_without_shell(self) -> None:
        result, commands, _output = self.run_menu(["9", 'xraydo user search "team a"', "0"])
        self.assertEqual(result, 0)
        self.assertEqual(commands, [["user", "search", "team a"]])

    def test_parser_exit_does_not_close_menu(self) -> None:
        output = TTYBuffer()

        def runner(_arguments: Sequence[str]) -> int:
            raise SystemExit(2)

        with (
            mock.patch("xraydo.menu.sys.stdin", TTYBuffer()),
            mock.patch("xraydo.menu.sys.stdout", output),
            mock.patch("builtins.input", side_effect=["9", "bad-command", "0"]),
        ):
            self.assertEqual(run_interactive(Translator("en"), runner), 0)
        self.assertIn("code 2", output.getvalue())

    def test_destructive_action_requires_confirmation(self) -> None:
        result, commands, output = self.run_menu(["2", "6", "key-id", "", "0", "0"])
        self.assertEqual(result, 0)
        self.assertEqual(commands, [])
        self.assertIn("Operation cancelled", output)

    def test_eof_and_ctrl_c_exit_cleanly(self) -> None:
        for interruption in (EOFError(), KeyboardInterrupt()):
            result, commands, _output = self.run_menu([interruption])
            self.assertEqual(result, 0)
            self.assertEqual(commands, [])

    def test_non_tty_is_rejected(self) -> None:
        error = io.StringIO()
        with (
            mock.patch("xraydo.menu.sys.stdin.isatty", return_value=False),
            mock.patch("xraydo.menu.sys.stderr", error),
        ):
            self.assertEqual(run_interactive(Translator("en"), lambda _args: 0), 2)
        self.assertIn("terminal", error.getvalue())

    def test_system_update_plan_is_available_from_base_menu(self) -> None:
        result, commands, output = self.run_menu(["10", "2", "/opt/xraydo", "0", "0"])
        self.assertEqual(result, 0)
        self.assertEqual(commands, [[
            "system", "update", "--prefix", "/opt/xraydo", "--dry-run"
        ]])
        self.assertIn("Update and removal", output)

    def test_full_cli_catalog_executes_any_parser_leaf(self) -> None:
        command_parser = parser()
        status_index = next(
            index for index, (name, _child) in enumerate(_subcommands(command_parser), 1)
            if name == "status"
        )
        commands: list[list[str]] = []
        output = TTYBuffer()
        with (
            mock.patch("xraydo.menu.sys.stdin", TTYBuffer()),
            mock.patch("xraydo.menu.sys.stdout", output),
            mock.patch("builtins.input", side_effect=["11", str(status_index), "", "0", "0"]),
        ):
            result = run_interactive(
                Translator("en"), lambda arguments: commands.append(list(arguments)) or 0,
                command_parser,
            )
        self.assertEqual(result, 0)
        self.assertEqual(commands, [["status"]])
        self.assertIn("All xraydo CLI commands", output.getvalue())

    def test_visual_theme_can_be_colored_or_no_color(self) -> None:
        with mock.patch.dict("os.environ", {"TERM": "xterm-256color"}, clear=True):
            _result, _commands, colored = self.run_menu(["0"])
        self.assertIn("\x1b[", colored)
        self.assertIn("╭", colored)
        with mock.patch.dict("os.environ", {"TERM": "xterm", "NO_COLOR": "1"}, clear=False):
            _result, _commands, plain = self.run_menu(["0"])
        self.assertNotIn("\x1b[", plain)
        self.assertIn("╭", plain)

    def test_help_is_available_in_russian_menu(self) -> None:
        output = TTYBuffer()
        with (
            mock.patch("xraydo.menu.sys.stdin", TTYBuffer()),
            mock.patch("xraydo.menu.sys.stdout", output),
            mock.patch("builtins.input", side_effect=["h", "0"]),
        ):
            self.assertEqual(run_interactive(Translator("ru"), lambda _args: 0), 0)
        rendered = output.getvalue()
        self.assertIn("Помощь по меню", rendered)
        self.assertIn("h) Помощь", rendered)

    def test_quick_port_and_sni_changes_are_available_in_inbound_menu(self) -> None:
        result, commands, output = self.run_menu([
            "4", "7", "inbound-a", "", "8443", "yes",
            "8", "inbound-b", "edge-1", "cdn.example", "yes", "0", "0",
        ])
        self.assertEqual(result, 0)
        self.assertEqual(commands, [
            ["inbound", "list"],
            ["inbound", "set-port", "inbound-a", "8443", "--apply", "--yes"],
            ["inbound", "list"],
            ["inbound", "set-sni", "inbound-b", "cdn.example", "--apply", "--yes",
             "--node", "edge-1"],
        ])
        self.assertIn("Quickly change protocol/proxy port", output)
        self.assertIn("Quickly change TLS/REALITY SNI", output)
