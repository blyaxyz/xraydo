from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from xraydo.access_keys import AccessKeyService
from xraydo.inbounds import InboundError, InboundService, apply_assignments
from xraydo.nodes import NodeService
from xraydo.store import Store
from xraydo.users import UserService


def inbound(port: int = 443, *, security: str = "none") -> dict:
    config = {
        "tag": "main-inbound", "listen": "0.0.0.0", "port": port,
        "protocol": "vless",
        "settings": {"clients": [{"id": "11111111-1111-4111-8111-111111111111",
                                    "email": "access-key:unknown", "password": "secret"}]},
        "streamSettings": {"network": "tcp", "security": security},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
    }
    if security == "reality":
        config["streamSettings"]["realitySettings"] = {
            "target": "example.com:443", "serverNames": ["example.com"],
            "privateKey": "node-secret", "shortIds": ["00"], "xver": 0,
        }
    return config


class InboundTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "state")
        self.service = InboundService(self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_identity_filters_safe_show_and_associated_users(self) -> None:
        user, _token = UserService(self.store).create("alice")
        key = AccessKeyService(self.store).default_for_user(user["id"])
        config = inbound()
        config["settings"]["clients"][0]["email"] = "access-key:" + key["id"]
        created = self.service.register("local", config, remark="Primary")
        self.assertEqual(created["node_id"], "local")
        self.assertEqual(created["associated_users"][0]["user"], "alice")
        self.assertEqual(created["config"]["settings"]["clients"][0]["id"], "***")
        self.assertEqual(created["config"]["settings"]["clients"][0]["password"], "***")
        self.assertEqual(self.service.list(protocol="vless", port=443, remark="prim")[0]["id"], created["id"])
        self.assertEqual(self.service.show(created["id"], "local", secrets=True)["config"]["settings"]["clients"][0]["password"], "secret")

    def test_plan_preserves_masked_secrets_and_omitted_clients(self) -> None:
        created = self.service.register("local", inbound())
        partial = {"port": 8443, "settings": {"clients": [{"id": "***", "password": "***"}]}}
        result = self.service.edit(created["id"], partial, expected_revision=1)
        plan = result["plan"]
        self.assertEqual(plan["config"]["settings"]["clients"][0]["id"], "***")
        internal = self.service.plan(created["id"], partial)
        self.assertEqual(internal["config"]["settings"]["clients"][0]["id"], "11111111-1111-4111-8111-111111111111")
        self.assertEqual(internal["config"]["settings"]["clients"][0]["email"], "access-key:unknown")
        self.assertIn("port", internal["impact"]["breaking_client_fields"])
        self.assertTrue(internal["impact"]["restart_required"])

    def test_revision_and_managed_port_conflicts_fail_before_apply(self) -> None:
        first = self.service.register("local", inbound(443))
        self.service.register("local", inbound(8443), remark="second")
        with self.assertRaisesRegex(InboundError, "inbound_port_conflict"):
            self.service.plan(first["id"], {"port": 8443})
        with self.assertRaisesRegex(InboundError, "inbound_revision_conflict"):
            self.service.plan(first["id"], {"port": 9443}, expected_revision=2)

    def test_remote_apply_is_queued_without_pretending_master_validated_it(self) -> None:
        node = NodeService(self.store).create("edge", alias="edge-1")
        created = self.service.register(node["id"], inbound())
        result = self.service.edit(created["id"], {"port": 8443}, node_id=node["id"],
                                   expected_revision=1, apply=True)
        self.assertEqual(result["state"], "pending_node_execution")
        state = self.store.load()
        operation = state["node_operations"][-1]
        self.assertEqual(operation["node_id"], node["id"])
        self.assertTrue(operation["payload"]["apply_time_validation"])
        self.assertEqual(self.service.find(created["id"], node["id"])["revision"], 1)
        pending = self.service.set_state(created["id"], "disabled", node_id=node["id"], expected_revision=1)
        self.assertEqual(pending["state"], "pending_node_execution")
        self.assertEqual(self.service.find(created["id"], node["id"])["state"], "enabled")

    def test_local_apply_validates_snapshots_restarts_and_updates_subscription_after_health(self) -> None:
        user, _token = UserService(self.store).create("alice")
        keys = AccessKeyService(self.store)
        key = keys.default_for_user(user["id"])
        config = inbound()
        config["settings"]["clients"][0]["email"] = "access-key:" + key["id"]
        created = self.service.register("local", config)
        data = self.store.load()
        stored_key = next(item for item in data["access_keys"] if item["id"] == key["id"])
        stored_key["endpoints"]["xray_reality"] = {
            "uri": "vless://example", "node_id": "local", "inbound_id": created["id"],
            "inbound_revision": 1,
        }
        self.store.save(data)
        path = self.root / "config.json"
        path.write_text(json.dumps({"inbounds": [config], "outbounds": [{"protocol": "freedom"}]}), encoding="utf-8")
        restart = mock.Mock(return_value={})
        health = mock.Mock(return_value={"state": "active"})
        with mock.patch("xraydo.inbounds._validate_binary") as validate, \
                mock.patch.object(self.service, "_check_local_resources"):
            result = self.service.edit(created["id"], {"port": 8443}, expected_revision=1,
                                       apply=True, binary="xray", config_path=path,
                                       restart=restart, health=health, listener_health=lambda _config: True)
        self.assertEqual(result["state"], "applied")
        validate.assert_called_once()
        restart.assert_called_once_with("xray", "restart")
        self.assertEqual(self.service.find(created["id"], "local")["revision"], 2)
        saved_key = next(item for item in self.store.load()["access_keys"] if item["id"] == key["id"])
        self.assertEqual(saved_key["endpoints"]["xray_reality"]["inbound_revision"], 2)
        self.assertGreaterEqual(len(self.service.history(created["id"])), 3)

    def test_failed_health_restores_file_and_does_not_publish_revision(self) -> None:
        config = inbound()
        created = self.service.register("local", config)
        path = self.root / "config.json"
        original = json.dumps({"inbounds": [config]})
        path.write_text(original, encoding="utf-8")
        with mock.patch("xraydo.inbounds._validate_binary"), \
                mock.patch.object(self.service, "_check_local_resources"), \
                self.assertRaisesRegex(InboundError, "inbound_health_failed"):
            self.service.edit(created["id"], {"port": 8443}, apply=True, binary="xray", config_path=path,
                              restart=mock.Mock(return_value={}), health=mock.Mock(return_value={"state": "failed"}),
                              listener_health=lambda _config: True)
        self.assertEqual(json.loads(path.read_text()), json.loads(original))
        self.assertEqual(self.service.find(created["id"], "local")["revision"], 1)

    def test_reality_reuses_scanner_and_state_grace_history_restore(self) -> None:
        created = self.service.register("local", inbound(security="reality"))
        checked = {"suitable": True, "target": "example.com:443"}
        with mock.patch("xraydo.inbounds.scan_target", return_value=checked) as scanner:
            result = self.service.edit(created["id"], {"remark": "ignored"})
        self.assertEqual(result["reality_check"], checked)
        scanner.assert_called_once()
        deleted = self.service.set_state(created["id"], "deleted", expected_revision=1, grace_seconds=600)
        self.assertEqual(deleted["state"], "deleted")
        restored = self.service.restore(created["id"])
        self.assertEqual(restored["state"], "disabled")
        self.assertTrue(self.service.history(created["id"]))

    def test_clone_bulk_plan_and_assignment_parser(self) -> None:
        source = self.service.register("local", inbound())
        clone = self.service.clone(source["id"], "local", port=8443)
        self.assertNotEqual(source["id"], clone["id"])
        edited = apply_assignments(inbound(), ["port=9443", "streamSettings.network=\"ws\""])
        self.assertEqual(edited["port"], 9443)
        self.assertEqual(edited["streamSettings"]["network"], "ws")
        report = self.service.bulk_plan(
            [("local", source["id"]), ("local", clone["id"])],
            lambda value: apply_assignments(value, ["sniffing.enabled=false"]),
        )
        self.assertTrue(report["complete"])
        self.assertEqual(len(report["results"]), 2)

    def test_safe_client_addition_uses_handler_api_hot_reload(self) -> None:
        config = inbound()
        created = self.service.register("local", config, api_endpoint="127.0.0.1:10085")
        new_client = {"id": "22222222-2222-4222-8222-222222222222", "email": "access-key:new"}
        proposed = {"settings": {"clients": config["settings"]["clients"] + [new_client]}}
        plan = self.service.plan(created["id"], proposed)
        self.assertTrue(plan["impact"]["hot_reload_supported"])
        path = self.root / "config.json"
        path.write_text(json.dumps({"inbounds": [config]}), encoding="utf-8")
        reload_api = mock.Mock()
        with mock.patch("xraydo.inbounds._validate_binary"), \
                mock.patch.object(self.service, "_check_local_resources"):
            result = self.service.edit(
                created["id"], proposed, apply=True, binary="xray", config_path=path,
                restart=mock.Mock(), health=mock.Mock(return_value={"state": "active"}),
                listener_health=lambda _config: True, hot_reload=reload_api,
            )
        self.assertEqual(result["method"], "hot-reload")
        reload_api.assert_called_once_with("xray", "127.0.0.1:10085", "main-inbound", [new_client])

    def test_utls_change_is_subscription_only_and_rewrites_link_after_commit(self) -> None:
        user, _token = UserService(self.store).create("alice")
        key = AccessKeyService(self.store).default_for_user(user["id"])
        config = inbound(security="reality")
        created = self.service.register("local", config)
        data = self.store.load()
        stored_key = next(item for item in data["access_keys"] if item["id"] == key["id"])
        stored_key["endpoints"]["xray_reality"] = {
            "uri": "vless://id@example.test:443?security=reality&fp=firefox",
            "node_id": "local", "inbound_id": created["id"], "fingerprint": "firefox",
        }
        self.store.save(data)
        with mock.patch("xraydo.inbounds.scan_target", return_value={"suitable": True}):
            result = self.service.edit(created["id"], {}, fingerprint="chrome", apply=True)
        self.assertEqual(result["method"], "subscription-only")
        endpoint = next(item for item in self.store.load()["access_keys"] if item["id"] == key["id"])["endpoints"]["xray_reality"]
        self.assertEqual(endpoint["fingerprint"], "chrome")
        self.assertIn("fp=chrome", endpoint["uri"])


if __name__ == "__main__":
    unittest.main()
