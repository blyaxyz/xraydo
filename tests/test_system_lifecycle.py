from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from xraydo.system_lifecycle import SystemLifecycleService, find_installer


class SystemLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.installer = self.root / "install.sh"
        self.installer.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then echo 9.9.9; exit 0; fi\n"
            "printf '%s\\n' \"$*\"\n",
            encoding="utf-8",
        )
        os.chmod(self.installer, 0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_status_and_dry_run_use_verified_local_installer(self) -> None:
        service = SystemLifecycleService(self.installer)
        self.assertEqual(service.status()["installer_version"], "9.9.9")
        result = service.execute("update", prefix=self.root / "prefix", dry_run=True)
        self.assertEqual(result["returncode"], 0)
        self.assertIn("--update", result["stdout"])
        self.assertIn("--dry-run", result["stdout"])
        self.assertNotIn("--yes", result["stdout"])

    def test_mutating_actions_require_confirmation(self) -> None:
        service = SystemLifecycleService(self.installer)
        with self.assertRaisesRegex(ValueError, "confirmation_required"):
            service.execute("uninstall", prefix=self.root / "prefix")
        result = service.execute(
            "purge", prefix=self.root / "prefix", confirmed=True, language="ru"
        )
        self.assertIn("--purge", result["stdout"])
        self.assertIn("--yes", result["stdout"])
        self.assertIn("--lang ru", result["stdout"])

    def test_unsafe_or_non_executable_installer_is_rejected(self) -> None:
        os.chmod(self.installer, 0o722)
        with self.assertRaisesRegex(ValueError, "system_installer_unsafe"):
            find_installer(self.installer)
        os.chmod(self.installer, 0o600)
        with self.assertRaisesRegex(ValueError, "system_installer_not_executable"):
            find_installer(self.installer)


if __name__ == "__main__":
    unittest.main()
