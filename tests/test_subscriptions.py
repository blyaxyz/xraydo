from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from xraydo.access_keys import AccessKeyService
from xraydo.nodes import NodeService
from xraydo.store import Store
from xraydo.subscriptions import SubscriptionService
from xraydo.sync_queue import SyncQueueService
from xraydo.users import UserService


class SubscriptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        self.users = UserService(self.store)
        self.user, self.token = self.users.create("alice")
        self.keys = AccessKeyService(self.store)
        self.subscriptions = SubscriptionService(self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_random_token_uses_salted_digest_and_verifies_without_plaintext_storage(self) -> None:
        self.assertEqual(self.users.verify_subscription_token(self.token)["id"], self.user["id"])
        state = self.store.load()
        serialized = json.dumps(state)
        self.assertNotIn(self.token, serialized)
        self.assertNotIn("subscription_token_hash", state["users"][0])
        credential = state["subscription_credentials"][0]
        self.assertEqual(credential["algorithm"], "sha256-v1")
        self.assertNotEqual(credential["digest"], self.token)

    def test_user_create_does_not_run_password_kdf(self) -> None:
        with patch("xraydo.users.hashlib.scrypt", side_effect=AssertionError("slow KDF")):
            user, token = self.users.create("fast-user")
        self.assertEqual(self.users.verify_subscription_token(token)["id"], user["id"])

    def test_legacy_scrypt_token_remains_valid(self) -> None:
        state = self.store.load()
        credential = state["subscription_credentials"][0]
        salt = bytes.fromhex(credential["salt"])
        credential["algorithm"] = "scrypt-v1"
        credential["digest"] = hashlib.scrypt(
            self.token.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32
        ).hex()
        self.store.save(state)
        self.assertEqual(
            self.users.verify_subscription_token(self.token)["id"], self.user["id"]
        )

    def test_rotation_invalidates_old_token_and_audit_contains_no_secret(self) -> None:
        _user, replacement = self.users.rotate_subscription("alice", actor="operator")
        self.assertIsNone(self.users.verify_subscription_token(self.token))
        self.assertEqual(self.users.verify_subscription_token(replacement)["id"], self.user["id"])
        event = self.users.audit_log()[0]
        self.assertEqual(event["action"], "subscription.rotate")
        serialized = json.dumps(event)
        self.assertNotIn(self.token, serialized)
        self.assertNotIn(replacement, serialized)

    def test_revoke_and_disabled_user_reject_token(self) -> None:
        revoked = self.users.revoke_subscription("alice")
        self.assertEqual(revoked["subscription_state"], "revoked")
        self.assertIsNone(self.users.verify_subscription_token(self.token))
        _user, replacement = self.users.rotate_subscription("alice")
        self.users.set_enabled("alice", False)
        self.assertIsNone(self.users.verify_subscription_token(replacement))

    def test_explicit_create_requires_revoked_or_missing_subscription(self) -> None:
        with self.assertRaisesRegex(ValueError, "subscription_exists"):
            self.users.create_subscription("alice")
        self.users.revoke_subscription("alice")
        _user, replacement = self.users.create_subscription("alice", actor="operator")
        self.assertEqual(self.users.verify_subscription_token(replacement)["id"], self.user["id"])
        self.assertEqual(self.users.audit_log()[0]["action"], "subscription.create")

    def test_subscription_combines_active_keys_and_separates_mtproto(self) -> None:
        first = self.keys.default_for_user(self.user["id"])
        second = self.keys.create(self.user["id"], "tablet")
        self.keys.set_endpoint(
            first["id"],
            "hysteria2",
            {"node_id": "node-b", "uri": "hysteria2://first@example.test:443"},
        )
        self.keys.set_endpoint(
            second["id"],
            "xray_reality",
            {"node_id": "node-a", "uri": "vless://second@example.test:443"},
        )
        self.keys.set_endpoint(
            second["id"],
            "telemt",
            {
                "node_id": "node-a",
                "tg_uri": "tg://proxy?server=example.test&port=443&secret=abcd",
                "https_uri": "https://t.me/proxy?server=example.test&port=443&secret=abcd",
            },
        )
        built = self.subscriptions.build(self.user["id"])
        self.assertEqual([item["node_id"] for item in built["entries"]], ["node-a", "node-b"])
        self.assertEqual(len(built["mtproto"]), 2)
        decoded = base64.b64decode(built["base64"]).decode()
        self.assertEqual(decoded, built["plain"])

        self.keys.set_state(second["id"], "disabled", "manual")
        filtered = self.subscriptions.build(self.user["id"])
        self.assertEqual(len(filtered["entries"]), 1)
        self.assertEqual(filtered["mtproto"], [])

    def test_hysteria_pin_is_enforced_in_uri_and_structured_output(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        pin = "ab" * 32
        self.keys.set_endpoint(
            key["id"],
            "hysteria2",
            {
                "uri": "hysteria2://secret@example.test:443/?sni=example.test#phone",
                "pin_sha256": pin.upper(),
            },
        )
        built = self.subscriptions.build(self.user["id"])
        entry = built["entries"][0]
        self.assertIn("pinSHA256=" + pin, entry["uri"])
        self.assertEqual(
            entry["tls"],
            {"verification": "leaf-sha256", "pin_sha256": pin},
        )
        self.assertIn("pinSHA256=" + pin, built["plain"])

        self.keys.set_endpoint(
            key["id"],
            "hysteria2",
            {
                "uri": "hysteria2://secret@example.test:443/?pinSHA256=" + "cd" * 32,
                "pin_sha256": pin,
            },
        )
        with self.assertRaisesRegex(ValueError, "subscription_tls_pin_mismatch"):
            self.subscriptions.build(self.user["id"])

    def test_hysteria_uri_pin_is_validated_without_endpoint_metadata(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        self.keys.set_endpoint(
            key["id"],
            "hysteria2",
            {"uri": "hy2://secret@example.test/?pinSHA256=not-a-sha256"},
        )
        with self.assertRaisesRegex(ValueError, "subscription_tls_pin_invalid"):
            self.subscriptions.build(self.user["id"])

    def test_rename_updates_subscription_remark_without_changing_credentials(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        credentials_before = json.loads(json.dumps(key["credentials"]))
        stored_uri = (
            "hysteria2://secret@example.test:443/?sni=example.test&pinSHA256="
            + "ab" * 32
            + "#alice"
        )
        self.keys.set_endpoint(
            key["id"],
            "hysteria2",
            {"uri": stored_uri, "pin_sha256": "ab" * 32},
        )

        renamed = self.users.rename("alice", "Bob Phone")
        built = self.subscriptions.build(renamed["id"])
        entry = built["entries"][0]
        self.assertEqual(entry["remark"], "Bob Phone")
        self.assertEqual(urllib.parse.unquote(urllib.parse.urlsplit(entry["uri"]).fragment), "Bob Phone")
        self.assertIn("pinSHA256=" + "ab" * 32, entry["uri"])

        state = self.store.load()
        stored_key = next(item for item in state["access_keys"] if item["id"] == key["id"])
        self.assertEqual(stored_key["credentials"], credentials_before)
        self.assertEqual(stored_key["endpoints"]["hysteria2"]["uri"], stored_uri)

    def test_geolocated_key_name_is_used_as_client_remark(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        state = self.store.load()
        stored = next(item for item in state["access_keys"] if item["id"] == key["id"])
        stored["device_name"] = "🇩🇪 Germany · phone"
        stored["location"] = {"ip": "8.8.8.8", "country": "DE"}
        self.store.save(state)
        self.keys.set_endpoint(
            key["id"],
            "hysteria2",
            {"uri": "hysteria2://secret@example.test:443"},
        )

        entry = self.subscriptions.build(self.user["id"])["entries"][0]
        self.assertEqual(entry["remark"], "🇩🇪 Germany · phone")
        self.assertEqual(
            urllib.parse.unquote(urllib.parse.urlsplit(entry["uri"]).fragment),
            "🇩🇪 Germany · phone",
        )

    def test_admin_metadata_never_reaches_subscription_or_node_payload(self) -> None:
        private_values = [
            "private-note-7f2a",
            "private-tag-8c3b",
            "contact-9d4c@example.test",
            "private-source-a5e1",
            "external-b6f2",
            "attribute-c7a3",
        ]
        self.users.set_metadata(
            self.user["id"],
            note=private_values[0],
            tags=[private_values[1]],
            actor="privacy-test",
        )
        self.users.set_admin_metadata(
            self.user["id"],
            contact=private_values[2],
            source=private_values[3],
            external_id=private_values[4],
            attributes={"internal": private_values[5]},
            actor="privacy-test",
        )
        key = self.keys.default_for_user(self.user["id"])
        self.keys.set_endpoint(
            key["id"],
            "hysteria2",
            {"uri": "hysteria2://credential@example.test:443"},
        )
        node = NodeService(self.store).create("edge")

        subscription = json.dumps(self.subscriptions.build(self.user["id"]))
        operations = SyncQueueService(self.store).list(node["id"])
        synchronized = json.dumps([item["payload"] for item in operations])
        for value in private_values:
            self.assertNotIn(value, subscription)
            self.assertNotIn(value, synchronized)

    def test_export_is_atomic_private_and_revoke_blocks_render(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        self.keys.set_endpoint(
            key["id"], "hysteria2", {"uri": "hysteria2://secret@example.test:443"}
        )
        destination = Path(self.temporary.name) / "exports" / "alice.txt"
        result = self.subscriptions.export(self.user["id"], "plain", destination)
        self.assertEqual(result, destination.resolve())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
        self.assertIn("hysteria2://secret", destination.read_text())
        self.users.revoke_subscription("alice")
        with self.assertRaisesRegex(ValueError, "subscription_inactive"):
            self.subscriptions.render(self.user["id"], "plain")

    def test_https_url_validation_and_qr_contains_only_url(self) -> None:
        with self.assertRaisesRegex(ValueError, "subscription_invalid_url"):
            self.subscriptions.set_base_url("http://example.test")
        with self.assertRaisesRegex(ValueError, "subscription_invalid_url"):
            self.subscriptions.set_base_url("https://user:pass@example.test/sub?q=1")
        self.assertEqual(
            self.subscriptions.set_base_url("https://example.test/subscriptions/"),
            "https://example.test/subscriptions",
        )
        expected_url = self.subscriptions.url(self.token)
        completed = SimpleNamespace(returncode=0, stdout="####\n####\n", stderr="")
        with patch("xraydo.subscriptions.shutil.which", return_value="/usr/bin/qrencode"), patch(
            "xraydo.subscriptions.subprocess.run", return_value=completed
        ) as invoked:
            rendered = self.subscriptions.qr(self.token, ascii_mode=True, width=200)
        self.assertIn(expected_url, rendered)
        self.assertEqual(invoked.call_args.kwargs["input"], expected_url)
        self.assertEqual(invoked.call_args.args[0], ["/usr/bin/qrencode", "-t", "ASCII", "-o", "-"])
