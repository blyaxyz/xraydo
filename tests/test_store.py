from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xraydo.store import Store


class StoreTests(unittest.TestCase):
    def test_legacy_state_is_migrated_without_losing_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.mkdir(exist_ok=True)
            legacy = {"settings": {"language": "ru"}, "users": [], "custom": "keep"}
            (root / "state.json").write_text(json.dumps(legacy), encoding="utf-8")
            state = Store(root).load()
            self.assertEqual(state["schema_version"], 8)
            self.assertEqual(state["nodes"], [])
            self.assertTrue(state["settings"]["country_auto_flags"])
            self.assertEqual(state["custom"], "keep")
            persisted = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(persisted["schema_version"], 8)

    def test_future_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "state.json").write_text('{"schema_version": 999}', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unsupported state schema"):
                Store(root).load()

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
            self.assertEqual(state["schema_version"], 8)
            self.assertEqual(state["users"][0]["id"], "preserved")
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
            self.assertEqual(state["schema_version"], 8)
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
            self.assertEqual(state["schema_version"], 8)
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
            self.assertEqual(state["schema_version"], 8)
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
            self.assertEqual(state["schema_version"], 8)
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
            self.assertEqual(state["schema_version"], 8)
            self.assertNotIn("subscription_token_hash", state["users"][0])
            credential = state["subscription_credentials"][0]
            self.assertEqual(credential["algorithm"], "sha256-legacy")
            self.assertEqual(credential["digest"], "legacy-digest")
