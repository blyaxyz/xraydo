from __future__ import annotations

import io
import unittest
from collections.abc import Sequence
from unittest import mock

from xraydo.i18n import Translator
from xraydo.menu import run_interactive


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
        self.assertEqual(commands, [["user", "create", "alice"]])

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
