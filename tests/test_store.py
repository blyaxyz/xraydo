from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xraydo.store import Store


class StoreTests(unittest.TestCase):
    def test_system_data_root_is_searchable_by_isolated_core_accounts(self) -> None:
        self.assertEqual(Store(Path("/var/lib/xraydo"))._data_dir_mode(), 0o755)
        self.assertEqual(Store(Path("/tmp/xraydo-test-state"))._data_dir_mode(), 0o700)

    def test_legacy_state_is_migrated_without_losing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.mkdir(exist_ok=True)
            legacy = {"settings": {"language": "ru"}, "users": [], "custom": "keep"}
            (root / "state.json").write_text(json.dumps(legacy), encoding="utf-8")
            state = Store(root).load()
            self.assertEqual(state["schema_version"], 10)
            self.assertEqual(state["nodes"], [])
            self.assertTrue(state["settings"]["country_auto_flags"])
            self.assertEqual(state["custom"], "keep")
            persisted = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], 10)

    def test_future_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.json").write_text('{"schema_version": 999}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unsupported state schema"):
                Store(root).load()
            self.assertEqual(
                json.loads((root / "state.json").read_text())["schema_version"], 999
            )

    def test_schema_one_adds_nodes_and_country_setting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {
                "schema_version": 1,
                "settings": {"language": "en"},
                "users": [{"id": "preserved"}],
            }
            (root / "state.json").write_text(json.dumps(original), encoding="utf-8")
            state = Store(root).load()
            self.assertEqual(state["schema_version"], 10)
            self.assertEqual(len(state["users"][0]["id"]), 7)
            self.assertEqual(state["users"][0]["numeric_id"], 1)
            self.assertEqual(state["users"][0]["subscription_state"], "revoked")
            self.assertEqual(state["nodes"], [])
            self.assertTrue(state["settings"]["country_auto_flags"])
            self.assertEqual(len(state["access_keys"]), 1)

    def test_schema_two_moves_credentials_and_endpoints_to_default_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {
                "schema_version": 2,
                "settings": {},
                "nodes": [],
                "audit_log": [],
                "users": [
                    {
                        "id": "user-1",
                        "name": "alice",
                        "enabled": True,
                        "credentials": {"xray_uuid": "xray-preserved"},
                        "endpoints": {
                            "hysteria2": {"password": "hy-preserved"},
                            "telemt": {"user_secret": "ab" * 16},
                        },
                    }
                ],
            }
            (root / "state.json").write_text(json.dumps(original), encoding="utf-8")
            state = Store(root).load()
            self.assertEqual(state["schema_version"], 10)
            self.assertNotIn("credentials", state["users"][0])
            key = state["access_keys"][0]
            self.assertEqual(key["credentials"]["xray_uuid"], "xray-preserved")
            self.assertEqual(key["credentials"]["hysteria_password"], "hy-preserved")
            self.assertEqual(key["credentials"]["telemt_secret"], "ab" * 16)

    def test_schema_four_adds_node_delivery_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {
                "schema_version": 4,
                "settings": {},
                "users": [],
                "access_keys": [],
                "usage_events": [],
                "nodes": [{"id": "node-1", "name": "edge"}],
            }
            (root / "state.json").write_text(json.dumps(original), encoding="utf-8")
            state = Store(root).load()
            self.assertEqual(state["schema_version"], 10)
            self.assertEqual(state["node_operations"], [])
            self.assertTrue(state["nodes"][0]["enabled"])
            self.assertEqual(state["nodes"][0]["connection_state"], "offline")

    def test_schema_five_adds_role_and_normalizes_online_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {
                "schema_version": 5,
                "settings": {},
                "users": [],
                "access_keys": [],
                "usage_events": [],
                "node_operations": [],
                "nodes": [{"id": "node-1", "connection_state": "online"}],
            }
            (root / "state.json").write_text(json.dumps(original), encoding="utf-8")
            state = Store(root).load()
            self.assertEqual(state["schema_version"], 10)
            self.assertEqual(state["settings"]["role"], "standalone")
            self.assertEqual(state["nodes"][0]["connection_state"], "healthy")

    def test_schema_six_adds_ip_observations_and_policy_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {
                "schema_version": 6,
                "settings": {},
                "users": [],
                "usage_events": [],
                "node_operations": [],
                "nodes": [],
                "access_keys": [{"id": "key-1", "quota": {"max_ips": 2}}],
            }
            (root / "state.json").write_text(json.dumps(original), encoding="utf-8")
            state = Store(root).load()
            self.assertEqual(state["schema_version"], 10)
            self.assertEqual(state["ip_observations"], [])
            quota = state["access_keys"][0]["quota"]
            self.assertEqual(quota["ip_window_seconds"], 300)
            self.assertEqual(quota["ip_action"], "disable")

    def test_schema_seven_moves_legacy_subscription_digest_out_of_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {
                "schema_version": 7,
                "settings": {},
                "users": [
                    {
                        "id": "user-1",
                        "name": "alice",
                        "subscription_token_hash": "legacy-digest",
                    }
                ],
                "access_keys": [],
                "usage_events": [],
                "ip_observations": [],
                "nodes": [],
                "node_operations": [],
            }
            (root / "state.json").write_text(json.dumps(original), encoding="utf-8")
            state = Store(root).load()
            self.assertEqual(state["schema_version"], 10)
            self.assertNotIn("subscription_token_hash", state["users"][0])
            credential = state["subscription_credentials"][0]
            self.assertEqual(credential["algorithm"], "sha256-legacy")
            self.assertEqual(credential["digest"], "legacy-digest")

    def test_schema_eight_adds_immutable_sync_owner_id(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = {
                "schema_version": 8,
                "settings": {},
                "users": [{"id": "user-1", "name": "alice"}],
                "access_keys": [{"id": "key-1", "user_id": "user-1"}],
                "nodes": [],
                "node_operations": [
                    {
                        "resource_type": "access_key",
                        "resource_id": "key-1",
                        "payload": {"id": "key-1", "user_id": "user-1"},
                    }
                ],
            }
            (root / "state.json").write_text(json.dumps(original), encoding="utf-8")
            state = Store(root).load()
            self.assertEqual(state["schema_version"], 10)
            migrated_id = state["users"][0]["id"]
            self.assertEqual(state["node_operations"][0]["owner_id"], migrated_id)
            self.assertEqual(state["node_operations"][0]["payload"]["user_id"], migrated_id)

    def test_schema_nine_replaces_uuid_user_ids_and_preserves_old_tokens(self) -> None:
        import hashlib

        from xraydo.users import UserService

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_id = "023dd7dc-e71c-46d6-be2e-4b972de4c08d"
            token = old_id + ".existing-secret"
            original = {
                "schema_version": 9,
                "settings": {},
                "users": [{
                    "id": old_id, "name": "alice", "enabled": True,
                    "subscription_state": "active",
                }],
                "access_keys": [{"id": "key-1", "user_id": old_id}],
                "subscription_credentials": [{
                    "user_id": old_id, "algorithm": "sha256-legacy", "salt": None,
                    "digest": hashlib.sha256(token.encode()).hexdigest(),
                    "revoked_at": None,
                }],
                "node_operations": [{
                    "owner_id": old_id, "payload": {"user_id": old_id},
                }],
                "audit_log": [{"object_type": "user", "object_id": old_id}],
                "user_deletions": [{"user_id": old_id}],
            }
            (root / "state.json").write_text(json.dumps(original), encoding="utf-8")
            store = Store(root)
            state = store.load()
            user = state["users"][0]
            self.assertEqual(state["schema_version"], 10)
            self.assertRegex(user["id"], r"^[23456789A-HJ-NP-Z]{7}$")
            self.assertEqual(user["numeric_id"], 1)
            self.assertNotEqual(user["id"], old_id)
            self.assertEqual(state["access_keys"][0]["user_id"], user["id"])
            self.assertEqual(state["subscription_credentials"][0]["user_id"], user["id"])
            self.assertEqual(state["node_operations"][0]["owner_id"], user["id"])
            self.assertEqual(state["audit_log"][0]["object_id"], user["id"])
            self.assertEqual(state["user_deletions"][0]["user_id"], user["id"])
            self.assertNotIn(old_id, json.dumps(state))
            self.assertEqual(UserService(store).verify_subscription_token(token)["id"], user["id"])
