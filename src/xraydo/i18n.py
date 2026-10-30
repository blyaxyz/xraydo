from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

SUPPORTED_LANGUAGES = ("en", "ru")


class Translator:
    def __init__(self, language: str = "en") -> None:
        self.language = language if language in SUPPORTED_LANGUAGES else "en"
        locale_dir = Path(__file__).with_name("locales")
        self._fallback = self._load(locale_dir / "en.json")
        self._messages = self._load(locale_dir / f"{self.language}.json")

    @staticmethod
    def _load(path: Path) -> dict[str, str]:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    def get(self, key: str, **values: Any) -> str:
        template = self._messages.get(key, self._fallback.get(key, key))
        return template.format(**values)


def requested_language(cli_language: Optional[str], configured: str = "en") -> str:
    candidate = cli_language or os.environ.get("XRAYDO_LANG") or configured
    return candidate if candidate in SUPPORTED_LANGUAGES else "en"


def installed_language() -> str:
    path = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json"))
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("language", "en")
    except (OSError, ValueError, AttributeError):
        return "en"
    return value if value in SUPPORTED_LANGUAGES else "en"
