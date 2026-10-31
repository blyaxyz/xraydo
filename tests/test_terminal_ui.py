from __future__ import annotations

import io
import os
import unittest
from unittest import mock

from xraydo.terminal_ui import (
    ANSI_RE, command_result, main_header, menu_option, panel, visible_width,
)


class TTYBuffer(io.StringIO):
    def isatty(self) -> bool:
        return True


class TerminalUITests(unittest.TestCase):
    def test_tty_uses_color_and_unicode_panels(self) -> None:
        stream = TTYBuffer()
        with mock.patch.dict(os.environ, {"TERM": "xterm-256color"}, clear=True):
            rendered = main_header("xraydo", "service management", stream=stream)
            option = menu_option("1", "Users", stream=stream)
            result = command_result(0, stream=stream)
        self.assertIn("\x1b[", rendered)
        self.assertIn("╭", rendered)
        self.assertIn("Users", option)
        self.assertIn("✓", result)

    def test_no_color_and_ascii_fallback_are_clean(self) -> None:
        stream = TTYBuffer()
        with mock.patch.dict(
            os.environ, {"TERM": "xterm", "NO_COLOR": "1", "XRAYDO_ASCII": "1"},
            clear=False,
        ):
            rendered = panel("Result", "plain output", stream=stream)
        self.assertNotRegex(rendered, ANSI_RE)
        self.assertIn("+", rendered)
        self.assertNotIn("╭", rendered)

    def test_narrow_terminal_wraps_without_exceeding_width(self) -> None:
        stream = TTYBuffer()
        terminal_size = os.terminal_size((36, 24))
        with (
            mock.patch("xraydo.terminal_ui.shutil.get_terminal_size", return_value=terminal_size),
            mock.patch.dict(os.environ, {"NO_COLOR": "1"}, clear=False),
        ):
            rendered = panel("A readable response", "A very long line that must wrap cleanly", stream=stream)
            option = menu_option("10", "A very long destructive action description", stream=stream)
        for line in (rendered + "\n" + option).splitlines():
            self.assertLessEqual(visible_width(line), 36)


if __name__ == "__main__":
    unittest.main()
