from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

from xraydo.roles import RoleService
from xraydo.store import Store


class RoleTests(unittest.TestCase):
    def test_role_is_explicit_and_persistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roles = RoleService(Store(Path(directory)))
            self.assertEqual(roles.get(), "standalone")
            self.assertEqual(roles.set("master"), {"role": "master"})
            self.assertEqual(roles.get(), "master")
            with self.assertRaisesRegex(ValueError, "invalid_role"):
                roles.set("invalid")

    def test_fresh_state_inherits_installer_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.json"
            config.write_text(json.dumps({"role": "node"}), encoding="utf-8")
            with patch.dict("os.environ", {"XRAYDO_CONFIG": str(config)}):
                roles = RoleService(Store(root / "state"))
                self.assertEqual(roles.get(), "node")
