from __future__ import annotations

import json
import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from xraydo.access_keys import AccessKeyService
from xraydo.store import Store
from xraydo.subscriptions import SubscriptionService
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

    def test_token_uses_scrypt_and_verifies_without_plaintext_storage(self) -> None:
        self.assertEqual(self.users.verify_subscription_token(self.token)["id"], self.user["id"])
        state = self.store.load()
        serialized = json.dumps(state)
        self.assertNotIn(self.token, serialized)
        self.assertNotIn("subscription_token_hash", state["users"][0])
        credential = state["subscription_credentials"][0]
        self.assertEqual(credential["algorithm"], "scrypt-v1")
        self.assertNotEqual(credential["digest"], self.token)

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
