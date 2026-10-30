import json
import unittest
from pathlib import Path


class LocaleTests(unittest.TestCase):
    def test_locale_keys_match(self) -> None:
        locale_dir = Path(__file__).resolve().parents[1] / "src" / "xraydo" / "locales"
        english = json.loads((locale_dir / "en.json").read_text(encoding="utf-8"))
        russian = json.loads((locale_dir / "ru.json").read_text(encoding="utf-8"))
        self.assertEqual(set(english), set(russian))
