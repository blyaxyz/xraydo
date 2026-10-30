from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_cli(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    environment["XRAYDO_DATA_DIR"] = str(tmp_path / "data")
    return subprocess.run(
        [sys.executable, "-m", "xraydo.cli", *arguments],
        text=True,
        capture_output=True,
        env=environment,
        check=False,
    )


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_create_list_and_secret_is_hashed(self) -> None:
        created = run_cli(self.path, "--json", "user", "create", "alice")
        self.assertEqual(created.returncode, 0, created.stderr)
        payload = json.loads(created.stdout)
        token = payload["subscription_token"]
        listed = run_cli(self.path, "--json", "user", "list")
        users = json.loads(listed.stdout)["users"]
        self.assertEqual(users[0]["name"], "alice")
        self.assertNotIn(token, (self.path / "data" / "state.json").read_text())
        self.assertEqual((self.path / "data" / "state.json").stat().st_mode & 0o777, 0o600)

    def test_duplicate_has_stable_error_code(self) -> None:
        self.assertEqual(run_cli(self.path, "user", "create", "alice").returncode, 0)
        duplicate = run_cli(self.path, "--json", "user", "create", "ALICE")
        self.assertEqual(duplicate.returncode, 2)
        self.assertEqual(json.loads(duplicate.stderr)["error"]["code"], "duplicate_name")

    def test_language_persists_and_can_be_overridden(self) -> None:
        self.assertEqual(run_cli(self.path, "settings", "language", "ru").returncode, 0)
        self.assertIn("готов", run_cli(self.path, "status").stdout)
        self.assertIn("ready", run_cli(self.path, "--lang", "en", "status").stdout)

    def test_non_tty_does_not_open_menu(self) -> None:
        result = run_cli(self.path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("terminal", result.stderr.lower())
