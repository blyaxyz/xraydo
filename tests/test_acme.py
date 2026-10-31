from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xraydo.acme import CertbotManager, _certbot_version
from xraydo.store import Store


class FakeTLSManager:
    def __init__(self) -> None:
        self.arguments = None

    def import_leaf(self, **arguments):
        self.arguments = arguments
        return {"cert": "/managed/server.crt", "key": "/managed/server.key"}


class CertbotManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "state")
        self.tls = FakeTLSManager()
        self.manager = CertbotManager(
            self.store,
            certbot=Path("/snap/bin/certbot"),
            config_dir=self.root / "letsencrypt",
            work_dir=self.root / "work",
            logs_dir=self.root / "logs",
            tls_manager=self.tls,  # type: ignore[arg-type]
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_lineage(self) -> None:
        lineage = self.root / "letsencrypt" / "live" / "xraydo-subscription"
        lineage.mkdir(parents=True)
        for name in ("cert.pem", "chain.pem", "privkey.pem"):
            (lineage / name).write_text(name, encoding="utf-8")

    def test_version_parser(self) -> None:
        self.assertEqual(_certbot_version("certbot 5.4.0"), (5, 4, 0))
        self.assertEqual(_certbot_version("certbot 6.1"), (6, 1, 0))

    def test_ip_issue_requires_new_certbot_and_short_lived_profile(self) -> None:
        self.create_lineage()
        completed = subprocess.CompletedProcess([], 0, stdout="certbot 5.4.0\n", stderr="")
        with mock.patch("xraydo.acme.subprocess.run", return_value=completed) as run:
            status = self.manager.issue("1.1.1.1", email="ops@example.com")
        command = run.call_args_list[1].args[0]
        self.assertIn("--preferred-profile", command)
        self.assertIn("shortlived", command)
        self.assertIn("--ip-address", command)
        self.assertTrue(status["short_lived"])
        self.assertEqual(self.tls.arguments["ips"], ["1.1.1.1"])

    def test_domain_issue_uses_standalone_http_challenge(self) -> None:
        self.create_lineage()
        completed = subprocess.CompletedProcess([], 0, stdout="certbot 4.0.0\n", stderr="")
        with mock.patch("xraydo.acme.subprocess.run", return_value=completed) as run:
            status = self.manager.issue("vpn.example.com", email=None, staging=True)
        command = run.call_args_list[1].args[0]
        self.assertIn("--standalone", command)
        self.assertIn("--preferred-challenges", command)
        self.assertIn("--register-unsafely-without-email", command)
        self.assertIn("--staging", command)
        self.assertIn("--domain", command)
        self.assertFalse(status["short_lived"])
        self.assertEqual(
            self.store.load()["settings"]["subscription_tls"]["mode"], "acme"
        )

    def test_webroot_avoids_stopping_existing_http_server(self) -> None:
        self.create_lineage()
        webroot = self.root / "www"
        webroot.mkdir()
        completed = subprocess.CompletedProcess([], 0, stdout="certbot 5.4.0\n", stderr="")
        with mock.patch("xraydo.acme.subprocess.run", return_value=completed) as run:
            self.manager.issue("vpn.example.com", webroot=webroot)
        command = run.call_args_list[1].args[0]
        self.assertIn("--webroot", command)
        self.assertIn(str(webroot.resolve()), command)
        self.assertNotIn("--standalone", command)

    def test_private_or_reserved_ip_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "acme_ip_not_public"):
            self.manager.issue("127.0.0.1")

    def test_old_certbot_is_rejected_for_ip(self) -> None:
        completed = subprocess.CompletedProcess([], 0, stdout="certbot 4.0.0\n", stderr="")
        with mock.patch("xraydo.acme.subprocess.run", return_value=completed):
            with self.assertRaisesRegex(ValueError, "acme_certbot_ip_version"):
                self.manager.issue("1.1.1.1")


if __name__ == "__main__":
    unittest.main()
