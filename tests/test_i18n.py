import ast
import json
import unittest
from pathlib import Path

from xraydo.i18n import Translator


class LocaleTests(unittest.TestCase):
    def test_translation_can_format_key_placeholder(self) -> None:
        translator = Translator("en")
        rendered = translator.get("tls.leaf_ready", cert="server.crt", key="server.key")
        self.assertIn("server.crt", rendered)
        self.assertIn("server.key", rendered)

    @staticmethod
    def _locale_dir() -> Path:
        return Path(__file__).resolve().parents[1] / "src" / "xraydo" / "locales"

    def test_locale_keys_match(self) -> None:
        locale_dir = self._locale_dir()
        english = json.loads((locale_dir / "en.json").read_text(encoding="utf-8"))
        russian = json.loads((locale_dir / "ru.json").read_text(encoding="utf-8"))
        self.assertEqual(set(english), set(russian))

    def test_locale_has_no_unknown_or_unused_keys(self) -> None:
        source_dir = self._locale_dir().parent
        used: set[str] = {"user.state.enabled", "user.state.disabled"}
        translated_errors = {
            "ValueError",
            "RealityError",
            "HysteriaError",
            "HysteriaManagerError",
            "TelemtError",
            "TelemtManagerError",
            "CoreServiceError",
            "InboundError",
        }
        for path in source_dir.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value.startswith("menu.")
                ):
                    used.add(node.value)
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "translator"
                    and node.func.attr == "get"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    used.add(node.args[0].value)
                if (
                    isinstance(node, ast.Raise)
                    and isinstance(node.exc, ast.Call)
                    and isinstance(node.exc.func, ast.Name)
                    and node.exc.func.id in translated_errors
                    and node.exc.args
                    and isinstance(node.exc.args[0], ast.Constant)
                    and isinstance(node.exc.args[0].value, str)
                ):
                    used.add("error." + node.exc.args[0].value)

        english = json.loads((self._locale_dir() / "en.json").read_text(encoding="utf-8"))
        self.assertEqual(used, set(english))
