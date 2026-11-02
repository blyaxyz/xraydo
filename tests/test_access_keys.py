from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from xraydo.access_keys import (
    PROTOCOL_CREDENTIAL_SCHEMA,
    AccessKeyService,
    protocol_credentials,
)
from xraydo.store import Store
from xraydo.users import UserService


class AccessKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        self.users = UserService(self.store)
        self.keys = AccessKeyService(self.store)
        self.user, _ = self.users.create("alice")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_user_and_multiple_keys_are_separate_entities(self) -> None:
        self.assertNotIn("credentials", self.user)
        default = self.keys.default_for_user(self.user["id"])
        phone = self.keys.create(self.user["id"], "phone")
        self.assertNotEqual(default["id"], phone["id"])
        self.assertNotEqual(
            default["credentials"]["xray_uuid"],
            phone["credentials"]["xray_uuid"],
        )
        self.assertNotEqual(
            default["credentials"]["telemt_secret"],
            phone["credentials"]["telemt_secret"],
        )

    def test_relocate_corrects_existing_geolocated_key_name(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        state = self.store.load()
        stored = next(item for item in state["access_keys"] if item["id"] == key["id"])
        stored["device_name"] = "🇩🇪 Germany · default"
        stored["location"] = {"ip": "8.8.8.8", "country": "DE", "version": "1"}
        self.store.save(state)

        changed = self.keys.relocate(
            {"ip": "8.8.8.8", "country": "PL", "version": "2"},
            "ru",
        )
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["device_name"], "🇵🇱 Польша · default")
        self.assertEqual(changed[0]["location"]["country"], "PL")

    def test_protocol_credential_schema_is_explicit_and_fail_closed(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        self.assertEqual(
            set(PROTOCOL_CREDENTIAL_SCHEMA),
            {"xray_reality", "hysteria2", "telemt"},
        )
        expected = {
            "xray_reality": "xray_uuid",
            "hysteria2": "hysteria_password",
            "telemt": "telemt_secret",
        }
        for protocol, field in expected.items():
            self.assertEqual(
                PROTOCOL_CREDENTIAL_SCHEMA[protocol]["access_key_fields"],
                (field,),
            )
            self.assertEqual(protocol_credentials(key, protocol), {field: key["credentials"][field]})
            broken = copy.deepcopy(key)
            broken["credentials"].pop(field)
            with self.assertRaisesRegex(ValueError, "access_key_invalid_credentials"):
                protocol_credentials(broken, protocol)
        with self.assertRaisesRegex(ValueError, "access_key_unknown_protocol"):
            protocol_credentials(key, "unknown")

    def test_disable_enable_and_irreversible_revoke_keep_reason(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        disabled = self.keys.set_state(key["id"], "disabled", "ip_limit")
        self.assertEqual(disabled["disabled_reason"], "ip_limit")
        with self.assertRaisesRegex(ValueError, "access_key_inactive"):
            self.keys.default_for_user(self.user["id"], key["id"])
        enabled = self.keys.set_state(key["id"], "active")
        self.assertIsNone(enabled["disabled_reason"])
        self.keys.set_state(key["id"], "revoked", "manual")
        with self.assertRaisesRegex(ValueError, "access_key_revoked"):
            self.keys.set_state(key["id"], "active")

    def test_explicit_key_must_belong_to_user(self) -> None:
        bob, _ = self.users.create("bob")
        bob_key = self.keys.default_for_user(bob["id"])
        with self.assertRaisesRegex(ValueError, "access_key_wrong_user"):
            self.keys.default_for_user(self.user["id"], bob_key["id"])

    def test_endpoint_is_stored_on_key(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        updated = self.keys.set_endpoint(key["id"], "telemt", {"uri": "secret-link"})
        self.assertEqual(updated["endpoints"]["telemt"]["uri"], "secret-link")
        self.assertNotIn("endpoints", self.users.find("alice"))

    def test_total_directional_limits_and_first_connect_policy(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        updated = self.keys.set_limits(
            key["id"],
            total_bytes=10 * 1024**3,
            upload_bytes=2 * 1024**3,
            download_bytes=8 * 1024**3,
            max_ips=3,
            duration_seconds=30 * 86_400,
            starts="first_connect",
        )
        self.assertEqual(updated["quota"]["total_bytes"], 10 * 1024**3)
        self.assertEqual(updated["quota"]["upload_bytes"], 2 * 1024**3)
        self.assertEqual(updated["quota"]["download_bytes"], 8 * 1024**3)
        self.assertEqual(updated["quota"]["max_ips"], 3)
        self.assertEqual(updated["validity"]["starts"], "first_connect")
        self.assertIsNone(updated["validity"]["activated_at"])
        self.assertIsNone(updated["validity"]["expires_at"])

    def test_absolute_expiry_extend_and_traffic_reset(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        updated = self.keys.set_limits(
            key["id"],
            expires_at="2030-01-01T00:00:00+03:00",
        )
        self.assertEqual(updated["validity"]["expires_at"], "2029-12-31T21:00:00+00:00")
        extended = self.keys.extend(key["id"], 86_400)
        self.assertEqual(extended["validity"]["expires_at"], "2030-01-01T21:00:00+00:00")
        state = self.store.load()
        stored = next(item for item in state["access_keys"] if item["id"] == key["id"])
        stored["quota"]["used_total_bytes"] = 123
        stored["quota"]["used_upload_bytes"] = 45
        stored["quota"]["used_download_bytes"] = 78
        self.store.save(state)
        reset = self.keys.reset_traffic(key["id"])
        self.assertEqual(reset["quota"]["used_total_bytes"], 0)
        self.assertEqual(reset["quota"]["used_upload_bytes"], 0)
        self.assertEqual(reset["quota"]["used_download_bytes"], 0)

    def test_usage_events_are_idempotent_aggregated_and_activate_validity(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        self.keys.set_limits(
            key["id"],
            total_bytes=100,
            duration_seconds=3600,
            starts="first_connect",
        )
        first, duplicate = self.keys.record_usage(
            key["id"],
            source_id="node-a",
            event_id="batch-1",
            upload_bytes=25,
            download_bytes=25,
            connected=True,
        )
        self.assertFalse(duplicate)
        self.assertIsNotNone(first["validity"]["activated_at"])
        self.assertIsNotNone(first["validity"]["expires_at"])
        repeated, duplicate = self.keys.record_usage(
            key["id"],
            source_id="node-a",
            event_id="batch-1",
            upload_bytes=25,
            download_bytes=25,
        )
        self.assertTrue(duplicate)
        self.assertEqual(repeated["quota"]["used_total_bytes"], 50)
        exhausted, duplicate = self.keys.record_usage(
            key["id"],
            source_id="node-b",
            event_id="batch-2",
            upload_bytes=30,
            download_bytes=20,
        )
        self.assertFalse(duplicate)
        self.assertEqual(exhausted["state"], "disabled")
        self.assertEqual(exhausted["disabled_reason"], "traffic")
        usage = self.keys.usage(key["id"])
        self.assertEqual(set(usage["sources"]), {"node-a", "node-b"})
        self.assertEqual(self.keys.user_usage(self.user["id"])["total_bytes"], 100)

    def test_usage_event_identity_is_scoped_to_access_key(self) -> None:
        first_key = self.keys.default_for_user(self.user["id"])
        second_key = self.keys.create(self.user["id"], "tablet")
        for key in (first_key, second_key):
            _updated, duplicate = self.keys.record_usage(
                key["id"],
                source_id="node-a",
                event_id="batch-1",
                upload_bytes=1,
                download_bytes=2,
            )
            self.assertFalse(duplicate)
        self.assertEqual(self.keys.user_usage(self.user["id"])["total_bytes"], 6)

    def test_expired_key_cannot_be_reenabled(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        self.keys.set_limits(key["id"], expires_at="2020-01-01T00:00:00Z")
        evaluated = self.keys.set_state(key["id"], "active")
        self.assertEqual(evaluated["state"], "disabled")
        self.assertEqual(evaluated["disabled_reason"], "expiry")

    def test_public_ip_limit_aggregates_nodes_and_ignores_trusted_networks(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        self.keys.set_limits(
            key["id"],
            max_ips=2,
            ip_window_seconds=300,
            ip_action="disable",
            trusted_networks=["8.8.8.0/24"],
        )
        addresses = ["8.8.8.8", "1.1.1.1", "9.9.9.9", "2606:4700:4700::1111"]
        for index, address in enumerate(addresses):
            updated, duplicate = self.keys.record_usage(
                key["id"],
                source_id=f"node-{index}",
                event_id=f"ip-{index}",
                upload_bytes=0,
                download_bytes=0,
                connected=True,
                client_ip=address,
            )
            self.assertFalse(duplicate)
        self.assertEqual(updated["state"], "disabled")
        self.assertEqual(updated["disabled_reason"], "ip_limit")
        active = self.keys.active_ips(key["id"])
        self.assertEqual(active["count"], 3)
        self.assertNotIn("8.8.8.8", active["active_ips"])
        self.assertIsNotNone(updated["last_connected_at"])

    def test_ip_warn_private_addresses_and_clear(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        self.keys.set_limits(key["id"], max_ips=1, ip_action="warn")
        for index, address in enumerate(("10.0.0.1", "1.1.1.1", "9.9.9.9")):
            updated, _duplicate = self.keys.record_usage(
                key["id"],
                source_id="node-a",
                event_id=f"warn-{index}",
                upload_bytes=0,
                download_bytes=0,
                client_ip=address,
            )
        self.assertEqual(updated["state"], "active")
        self.assertTrue(updated["quota"]["ip_limit_warning"])
        self.assertEqual(self.keys.active_ips(key["id"])["count"], 2)
        self.assertEqual(self.keys.clear_ips(key["id"])["count"], 0)

    def test_temporary_ip_block_has_expiry(self) -> None:
        key = self.keys.default_for_user(self.user["id"])
        self.keys.set_limits(
            key["id"], max_ips=1, ip_window_seconds=30, ip_action="temporary_block"
        )
        for index, address in enumerate(("1.1.1.1", "9.9.9.9")):
            updated, _duplicate = self.keys.record_usage(
                key["id"],
                source_id="node-a",
                event_id=f"temporary-{index}",
                upload_bytes=0,
                download_bytes=0,
                client_ip=address,
            )
        self.assertEqual(updated["state"], "disabled")
        self.assertEqual(updated["disabled_reason"], "ip_limit")
        self.assertIsNotNone(updated["blocked_until"])
