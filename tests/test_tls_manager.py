from __future__ import annotations

import shutil
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xraydo.store import Store
from xraydo.tls_manager import TLSManager


@unittest.skipUnless(shutil.which("openssl"), "openssl is required")
class TLSManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.manager = TLSManager(Store(root / "state"), root / "pki")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_private_ca_multi_san_atomic_renew_and_export(self) -> None:
        waiting = self.manager.run_renewer(once=True)
        self.assertEqual(waiting["state"], "waiting")
        ca = self.manager.init_ca()
        self.assertTrue(ca["ca_ready"])
        self.assertEqual(len(ca["ca_fingerprint_sha256"]), 64)
        ca_version = (self.manager.ca_root / "current").resolve()
        self.assertEqual((ca_version / "ca.key").stat().st_mode & 0o777, 0o600)

        issued = self.manager.issue(
            domains=["Example.COM"],
            ips=["127.0.0.1", "::1"],
            days=30,
            renew_percent=75,
        )
        self.assertTrue(issued["leaf_ready"])
        first_leaf = (self.manager.leaf_root / "current").resolve()
        decoded = ssl._ssl._test_decode_cert(issued["cert"])  # type: ignore[attr-defined]
        sans = set(decoded["subjectAltName"])
        self.assertIn(("DNS", "example.com"), sans)
        self.assertIn(("IP Address", "127.0.0.1"), sans)
        self.assertIn(("IP Address", "0:0:0:0:0:0:0:1"), sans)
        self.assertEqual(Path(issued["key"]).stat().st_mode & 0o777, 0o600)
        self.assertEqual(issued["metadata"]["renew_percent"], 75)
        self.assertEqual(
            issued["metadata"]["local_https_probe_hosts"],
            ["example.com", "127.0.0.1", "::1"],
        )
        self.assertTrue(issued["metadata"]["local_https_probe_at"])
        self.assertEqual(
            self.manager.store.load()["settings"]["subscription_tls"]["mode"],
            "private-ca",
        )

        skipped = self.manager.renew()
        self.assertFalse(skipped["renewed"])
        self.assertEqual(self.manager.run_renewer(once=True)["state"], "current")
        renewed = self.manager.renew(force=True)
        self.assertTrue(renewed["renewed"])
        self.assertNotEqual((self.manager.leaf_root / "current").resolve(), first_leaf)
        self.assertEqual((self.manager.ca_root / "current").resolve(), ca_version)

        imported = self.manager.import_leaf(
            cert=Path(renewed["cert"]),
            key=Path(renewed["key"]),
            domains=["example.com"],
            ips=["127.0.0.1", "::1"],
            ca_cert=ca_version / "ca.crt",
        )
        self.assertEqual(imported["metadata"]["source"], "import")
        self.assertTrue(imported["metadata"]["issuer"])
        imported_version = (self.manager.leaf_root / "current").resolve()
        with self.assertRaisesRegex(ValueError, "tls_imported_not_renewable"):
            self.manager.renew(force=True)
        with self.assertRaisesRegex(ValueError, "subscription_tls_name_mismatch"):
            self.manager.import_leaf(
                cert=Path(imported["cert"]),
                key=Path(imported["key"]),
                domains=["wrong.example"],
                ips=[],
                ca_cert=ca_version / "ca.crt",
            )
        self.assertEqual((self.manager.leaf_root / "current").resolve(), imported_version)

        destination = Path(self.temporary.name) / "export" / "xraydo-ca.crt"
        self.assertEqual(self.manager.export_ca(destination), destination.resolve())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o644)
        self.assertIn("BEGIN CERTIFICATE", destination.read_text(encoding="utf-8"))
        self.assertNotIn("PRIVATE KEY", destination.read_text(encoding="utf-8"))
        self.assertIn(str(destination.resolve()), self.manager.install_command(destination))

    def test_san_and_lifetime_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "tls_san_required"):
            self.manager.issue(domains=[], ips=[])
        with self.assertRaisesRegex(ValueError, "tls_invalid_san"):
            self.manager.issue(domains=["bad domain"], ips=[])
        with self.assertRaisesRegex(ValueError, "tls_invalid_days"):
            self.manager.issue(domains=["example.com"], ips=[], days=0)
        with self.assertRaisesRegex(ValueError, "tls_invalid_renew_percent"):
            self.manager.issue(domains=["example.com"], ips=[], renew_percent=10)
        with self.assertRaisesRegex(ValueError, "tls_invalid_renew_interval"):
            self.manager.run_renewer(interval_seconds=10, once=True)

    def test_ca_rotation_prepare_activate_and_finalize_overlap(self) -> None:
        self.manager.init_ca()
        self.manager.issue(domains=["example.com"], ips=[], days=30)
        old_ca = (self.manager.ca_root / "current").resolve()
        old_leaf = (self.manager.leaf_root / "current").resolve()

        prepared = self.manager.prepare_ca_rotation(overlap_days=30)
        self.assertEqual(prepared["ca_rotation"]["state"], "prepared")
        self.assertEqual((self.manager.ca_root / "current").resolve(), old_ca)
        new_ca = self.manager.ca_root / prepared["ca_rotation"]["new_version"]
        self.assertNotEqual(new_ca, old_ca)
        bundle = self.manager.ca_root / "trust-bundle.crt"
        self.assertEqual(bundle.read_text(encoding="utf-8").count("BEGIN CERTIFICATE"), 2)
        with self.assertRaisesRegex(ValueError, "tls_rotation_in_progress"):
            self.manager.prepare_ca_rotation()

        exported = Path(self.temporary.name) / "rotation-bundle.crt"
        self.manager.export_ca(exported)
        self.assertEqual(exported.read_text(encoding="utf-8").count("BEGIN CERTIFICATE"), 2)

        active = self.manager.activate_ca_rotation()
        self.assertEqual(active["ca_rotation"]["state"], "active")
        self.assertEqual((self.manager.ca_root / "current").resolve(), new_ca)
        self.assertNotEqual((self.manager.leaf_root / "current").resolve(), old_leaf)
        self.assertTrue(old_ca.is_dir())
        with self.assertRaisesRegex(ValueError, "tls_rotation_overlap_active"):
            self.manager.finalize_ca_rotation()

        finalized = self.manager.finalize_ca_rotation(force=True)
        self.assertEqual(finalized["ca_rotation"]["state"], "finalized")
        self.assertEqual(bundle.read_text(encoding="utf-8").count("BEGIN CERTIFICATE"), 1)
        self.assertTrue(old_ca.is_dir())

    def test_ip_reconciliation_reissues_leaf_and_updates_subscription_url(self) -> None:
        self.manager.init_ca()
        self.manager.issue(
            domains=["subscriptions.example"], ips=["192.0.2.10"], days=30
        )
        data = self.manager.store.load()
        data["settings"]["subscription_base_url"] = (
            "https://192.0.2.10:8444/subscriptions"
        )
        self.manager.store.save(data)
        old_leaf = (self.manager.leaf_root / "current").resolve()

        synced = self.manager.reconcile_ips(["2001:db8::20", "192.0.2.20"])
        self.assertTrue(synced["ip_changed"])
        self.assertTrue(synced["url_updated"])
        self.assertEqual(synced["previous_ips"], ["192.0.2.10"])
        self.assertEqual(synced["metadata"]["ips"], ["192.0.2.20", "2001:db8::20"])
        self.assertNotEqual((self.manager.leaf_root / "current").resolve(), old_leaf)
        self.assertEqual(
            self.manager.store.load()["settings"]["subscription_base_url"],
            "https://192.0.2.20:8444/subscriptions",
        )
        decoded = ssl._ssl._test_decode_cert(synced["cert"])  # type: ignore[attr-defined]
        sans = set(decoded["subjectAltName"])
        self.assertNotIn(("IP Address", "192.0.2.10"), sans)
        self.assertIn(("IP Address", "192.0.2.20"), sans)

        current_leaf = (self.manager.leaf_root / "current").resolve()
        unchanged = self.manager.reconcile_ips()
        self.assertFalse(unchanged["ip_changed"])
        self.assertEqual((self.manager.leaf_root / "current").resolve(), current_leaf)

    def test_ip_reconciliation_requires_an_authoritative_source(self) -> None:
        self.manager.init_ca()
        self.manager.issue(domains=["subscriptions.example"], ips=[], days=30)
        with self.assertRaisesRegex(ValueError, "tls_ip_source_required"):
            self.manager.reconcile_ips()

    def test_failed_local_https_probe_preserves_active_leaf(self) -> None:
        self.manager.init_ca()
        self.manager.issue(domains=["working.example"], ips=[], days=30)
        active = (self.manager.leaf_root / "current").resolve()
        with mock.patch(
            "xraydo.tls_manager._local_https_probe",
            side_effect=ValueError("tls_local_probe_failed"),
        ):
            with self.assertRaisesRegex(ValueError, "tls_local_probe_failed"):
                self.manager.issue(domains=["replacement.example"], ips=[], days=30)
        self.assertEqual((self.manager.leaf_root / "current").resolve(), active)

    def test_encrypted_backup_contains_keys_and_detects_tampering(self) -> None:
        self.manager.init_ca()
        issued = self.manager.issue(domains=["backup.example"], ips=["192.0.2.30"])
        passphrase = Path(self.temporary.name) / "backup-passphrase"
        passphrase.write_text("correct horse battery staple\n", encoding="utf-8")
        passphrase.chmod(0o600)
        destination = Path(self.temporary.name) / "backups" / "tls.backup"

        result = self.manager.backup(destination, passphrase)
        self.assertEqual(Path(result["path"]), destination.resolve())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        encrypted = destination.read_bytes()
        self.assertNotIn(b"BEGIN PRIVATE KEY", encrypted)
        self.assertNotIn(b"BEGIN CERTIFICATE", encrypted)
        manifest = self.manager.verify_backup(destination, passphrase)
        self.assertEqual(manifest["format"], "xraydo-tls-backup-v1")
        self.assertEqual(manifest["ca_fingerprint_sha256"], issued["ca_fingerprint_sha256"])
        paths = {item["path"] for item in manifest["files"]}
        self.assertTrue(any(path.endswith("ca.key") for path in paths))
        self.assertTrue(any(path.endswith("server.key") for path in paths))
        self.assertTrue(manifest["contains_private_keys"])

        tampered = bytearray(encrypted)
        tampered[-1] ^= 1
        destination.write_bytes(tampered)
        with self.assertRaisesRegex(ValueError, "tls_backup_auth_failed"):
            self.manager.verify_backup(destination, passphrase)

        passphrase.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "tls_backup_passphrase_permissions"):
            self.manager.backup(destination, passphrase)
