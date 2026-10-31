from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
from typing import Optional, TextIO


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
RESET = "\x1b[0m"
COLORS = {
    "bold": "\x1b[1m",
    "dim": "\x1b[2m",
    "blue": "\x1b[38;5;39m",
    "cyan": "\x1b[38;5;45m",
    "green": "\x1b[38;5;42m",
    "yellow": "\x1b[38;5;214m",
    "red": "\x1b[38;5;196m",
    "gray": "\x1b[38;5;245m",
}
DANGER_WORDS = {
    "delete", "purge", "remove", "revoke", "uninstall", "stop",
    "удал", "отоз", "останов", "безвозврат",
}


def color_enabled(stream: Optional[TextIO] = None) -> bool:
    stream = stream or sys.stdout
    return (
        bool(getattr(stream, "isatty", lambda: False)())
        and "NO_COLOR" not in os.environ
        and os.environ.get("CLICOLOR") != "0"
        and os.environ.get("TERM", "") != "dumb"
    )


def unicode_enabled(stream: Optional[TextIO] = None) -> bool:
    stream = stream or sys.stdout
    if os.environ.get("XRAYDO_ASCII", "").casefold() in {"1", "true", "yes", "on"}:
        return False
    encoding = getattr(stream, "encoding", None) or "utf-8"
    return encoding.casefold().replace("-", "") not in {"ascii", "usascii", "ansi_x3.4-1968"}


def paint(value: str, *styles: str, stream: Optional[TextIO] = None) -> str:
    if not color_enabled(stream):
        return value
    prefix = "".join(COLORS[item] for item in styles if item in COLORS)
    return f"{prefix}{value}{RESET}" if prefix else value


def visible_width(value: str) -> int:
    return len(ANSI_RE.sub("", value))


def terminal_width() -> int:
    return max(32, min(100, shutil.get_terminal_size((80, 24)).columns))


def _characters(stream: Optional[TextIO] = None) -> dict[str, str]:
    if unicode_enabled(stream):
        return {
            "tl": "╭", "tr": "╮", "bl": "╰", "br": "╯", "h": "─", "v": "│",
            "arrow": "›", "ok": "✓", "fail": "✗", "bullet": "•", "prompt": "❯",
        }
    return {
        "tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|",
        "arrow": ">", "ok": "OK", "fail": "X", "bullet": "*", "prompt": ">",
    }


def panel(
    title: str, body: Optional[str] = None, *, accent: str = "blue",
    stream: Optional[TextIO] = None,
) -> str:
    stream = stream or sys.stdout
    chars = _characters(stream)
    width = terminal_width()
    inner = width - 4
    title_value = title[:max(8, width - 6)]
    title_text = f" {title_value} "
    top_fill = max(0, width - 3 - len(title_text))
    lines = [
        paint(chars["tl"] + chars["h"], accent, stream=stream)
        + paint(title_text, "bold", accent, stream=stream)
        + paint(chars["h"] * top_fill + chars["tr"], accent, stream=stream)
    ]
    if body:
        for paragraph in body.splitlines() or [""]:
            wrapped = textwrap.wrap(paragraph, width=inner, replace_whitespace=False) or [""]
            for line in wrapped:
                padding = " " * max(0, inner - len(line))
                lines.append(
                    paint(chars["v"], accent, stream=stream)
                    + f" {line}{padding} "
                    + paint(chars["v"], accent, stream=stream)
                )
    lines.append(paint(chars["bl"] + chars["h"] * (width - 2) + chars["br"], accent,
                       stream=stream))
    return "\n".join(lines)


def main_header(title: str, subtitle: str, *, stream: Optional[TextIO] = None) -> str:
    return panel(title, subtitle, accent="cyan", stream=stream)


def section_header(title: str, *, root: str = "xraydo", stream: Optional[TextIO] = None) -> str:
    stream = stream or sys.stdout
    chars = _characters(stream)
    breadcrumb = (
        paint(root, "dim", stream=stream)
        + " " + paint(chars["arrow"], "gray", stream=stream) + " "
        + paint(title, "bold", "cyan", stream=stream)
    )
    rule = paint(chars["h"] * max(8, min(terminal_width(), 100)), "gray", stream=stream)
    return f"{breadcrumb}\n{rule}"


def menu_option(choice: str, label: str, *, stream: Optional[TextIO] = None) -> str:
    stream = stream or sys.stdout
    dangerous = any(word in label.casefold() for word in DANGER_WORDS)
    accent = "red" if dangerous else "blue"
    number = paint(f"[{choice:>2}]", "bold", accent, stream=stream)
    marker = paint(_characters(stream)["bullet"], accent, stream=stream)
    wrapped = textwrap.wrap(label, width=max(12, terminal_width() - 10)) or [""]
    lines = [
        f"  {number} {marker} "
        + paint(wrapped[0], "yellow" if dangerous else "bold", stream=stream)
    ]
    lines.extend("         " + paint(line, "yellow" if dangerous else "bold", stream=stream)
                 for line in wrapped[1:])
    return "\n".join(lines)


def navigation(value: str, *, stream: Optional[TextIO] = None) -> str:
    return paint(value, "dim", "gray", stream=stream)


def prompt(value: str, *, stream: Optional[TextIO] = None) -> str:
    stream = stream or sys.stdout
    return paint(_characters(stream)["prompt"] + " ", "cyan", "bold", stream=stream) + value


def command_card(command: str, *, stream: Optional[TextIO] = None) -> str:
    stream = stream or sys.stdout
    chars = _characters(stream)
    return (
        paint(chars["arrow"] + " command", "cyan", "bold", stream=stream)
        + "  " + paint(command, "bold", stream=stream)
    )


def command_result(code: int, *, stream: Optional[TextIO] = None) -> str:
    stream = stream or sys.stdout
    chars = _characters(stream)
    success = code == 0
    icon = chars["ok"] if success else chars["fail"]
    color = "green" if success else "red"
    label = "success" if success else "failed"
    return paint(f"{icon} {label}", "bold", color, stream=stream) + paint(
        f"  exit={code}", "dim", stream=stream
    )


def response(message: str, *, stream: Optional[TextIO] = None) -> str:
    stream = stream or sys.stdout
    if not getattr(stream, "isatty", lambda: False)() or not message:
        return message
    return panel("Result", message, accent="green", stream=stream)


def error(message: str, *, stream: Optional[TextIO] = None) -> str:
    stream = stream or sys.stderr
    if not getattr(stream, "isatty", lambda: False)():
        return message
    return panel("Error", message, accent="red", stream=stream)
