from __future__ import annotations

import tempfile
import unittest
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from xraydo.access_keys import AccessKeyService
from xraydo.automation import AutomationService
from xraydo.certificates import NodeCertificateService
from xraydo.inbounds import InboundService
from xraydo.nodes import NodeService
from xraydo.rollout import RolloutService
from xraydo.store import Store
from xraydo.subscription_server import SubscriptionRateLimiter
from xraydo.sync_queue import SyncQueueService
from xraydo.users import UserService
from xraydo.xray_manager import XrayManager


class ManagementExtensionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_automation_enforces_expiry_and_queues_usage_collection(self) -> None:
        user, _token = UserService(self.store).create("alice")
        key = AccessKeyService(self.store).default_for_user(user["id"])
        AccessKeyService(self.store).set_limits(
            key["id"], expires_at=(datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        )
        node = NodeService(self.store).create("edge")
        data = self.store.load()
        data["node_cores"].append({"node_id": node["id"], "core": "xray", "enabled": True})
        self.store.save(data)
        result = AutomationService(self.store).run_once()
        current = AccessKeyService(self.store).find(key["id"])
        self.assertEqual(current["state"], "disabled")
        self.assertEqual(current["disabled_reason"], "expiry")
        self.assertTrue(result["policy_changes"])
        self.assertTrue(any(item["resource_type"] == "usage_collect"
                            for item in self.store.load()["node_operations"]))

    def test_rate_limiter_limits_tokens_and_bans_failed_address(self) -> None:
        limiter = SubscriptionRateLimiter(requests=2, window_seconds=60,
                                          failures=2, ban_seconds=60)
        self.assertTrue(limiter.allow("192.0.2.1", "token")[0])
        self.assertTrue(limiter.allow("192.0.2.1", "token")[0])
        allowed, retry = limiter.allow("192.0.2.1", "token")
        self.assertFalse(allowed); self.assertGreater(retry, 0)
        abuse = SubscriptionRateLimiter(requests=10, window_seconds=60,
                                        failures=2, ban_seconds=60)
        abuse.failure("192.0.2.2"); abuse.failure("192.0.2.2")
        self.assertFalse(abuse.allow("192.0.2.2")[0])

    def test_rollout_canary_and_batch_dependencies(self) -> None:
        nodes = [NodeService(self.store).create(f"edge-{index}") for index in range(5)]
        service = RolloutService(self.store)
        plan = service.plan(resource_type="core", action="install", canary=1,
            max_unavailable=2, targets=[{"node_id": node["id"], "resource_id": "xray",
                                         "payload": {"core": "xray"}} for node in nodes])
        result = service.start(plan, confirm=True)
        operations = self.store.load()["node_operations"]
        self.assertEqual(result["state"], "running")
        self.assertNotIn("depends_on", operations[0])
        self.assertEqual(operations[1]["depends_on"], [operations[0]["id"]])
        self.assertEqual(operations[2]["depends_on"], [operations[0]["id"]])
        self.assertEqual(set(operations[3]["depends_on"]),
                         {operations[1]["id"], operations[2]["id"]})

    def test_wss_wizard_probe_and_reality_rotation_ack(self) -> None:
        service = InboundService(self.store)
        with mock.patch("xraydo.inbounds.tls_context"):
            result = service.guided_wss_tls(
                node_id="local", port=8443, public_address="vpn.example",
                sni=["sni.example"], http_host="front.example",
                certificate={"certificateFile": "/tmp/cert", "keyFile": "/tmp/key"},
                register=False,
            )
        self.assertTrue(result["client"]["path"].startswith("/"))
        self.assertNotEqual(result["client"]["sni"], result["client"]["host"])

        node = NodeService(self.store).create("edge")
        reality = {"listen": "0.0.0.0", "port": 443, "protocol": "vless", "tag": "r",
            "settings": {"clients": []}, "streamSettings": {"network": "tcp",
            "security": "reality", "realitySettings": {"target": "example.com:443",
            "serverNames": ["example.com"], "privateKey": "secret", "shortIds": ["0011"]}}}
        inbound = service.register(node["id"], reality)
        self.assertEqual(service.probe_port(node["id"], listen="0.0.0.0", port=443)["state"],
                         "managed_conflict")
        plan = service.rotation_plan(inbound["id"], node_id=node["id"])
        pending = service.rotate_reality(plan, confirm=True)
        queue = SyncQueueService(self.store)
        operation = queue.claim(node["id"], supported_types={"reality_rotation"})[0]
        queue.finish(operation["id"], node_id=node["id"], lease_id=operation["lease_id"],
                     result={"health": "healthy", "atomic": True})
        current = service.find(inbound["id"], node["id"])
        self.assertEqual(current["config"]["streamSettings"]["realitySettings"]["shortIds"],
                         plan["proposed"]["short_ids"])
        self.assertEqual(pending["state"], "pending_node_execution")

    def test_xray_remote_lifecycle_is_verified_on_target(self) -> None:
        node = NodeService(self.store).create("edge")
        result = XrayManager(self.store).install(node["id"], version="v1.2.3")
        operation = self.store.load()["node_operations"][-1]
        self.assertEqual(result["state"], "pending_node_execution")
        self.assertEqual(operation["resource_type"], "core")
        self.assertEqual(operation["payload"]["core"], "xray")
        self.assertTrue(operation["payload"]["verify_sha256"])

    def test_remote_quick_port_change_waits_for_probe_and_sni_waits_for_ack(self) -> None:
        user, _token = UserService(self.store).create("alice")
        key = AccessKeyService(self.store).default_for_user(user["id"])
        node = NodeService(self.store).create("edge")
        config = {"listen": "0.0.0.0", "port": 443, "protocol": "vless", "tag": "r",
            "settings": {"clients": []}, "streamSettings": {"network": "tcp",
            "security": "reality", "realitySettings": {"target": "example.com:443",
            "serverNames": ["example.com"], "privateKey": "secret", "shortIds": ["0011"]}}}
        inbound = InboundService(self.store).register(node["id"], config)
        AccessKeyService(self.store).set_endpoint(key["id"], "xray_reality", {
            "uri": "vless://id@edge:443?security=reality&sni=example.com",
            "inbound_id": inbound["id"], "node_id": node["id"],
        })
        service = InboundService(self.store)
        changed = service.change_port(inbound["id"], 8443, node_id=node["id"], apply=True)
        operations = self.store.load()["node_operations"]
        probe = next(item for item in operations if item["id"] == changed["probe_operation_id"])
        edit = next(item for item in operations if item["id"] == changed["operation_id"])
        self.assertEqual(probe["resource_type"], "port_probe")
        self.assertEqual(edit["depends_on"], probe["id"])

        queue = SyncQueueService(self.store)
        probe_delivery = queue.claim(node["id"], supported_types={"port_probe"})[0]
        queue.finish(probe_delivery["id"], node_id=node["id"],
                     lease_id=probe_delivery["lease_id"],
                     result={"state": "available", "transport": "tcp"})
        port_delivery = queue.claim(node["id"], supported_types={"inbound"})[0]
        queue.finish(port_delivery["id"], node_id=node["id"],
                     lease_id=port_delivery["lease_id"],
                     result={"health": "healthy", "atomic": True})
        endpoint = AccessKeyService(self.store).find(key["id"])["endpoints"]["xray_reality"]
        self.assertIn("edge:8443", endpoint["uri"])

        changed_sni = service.change_sni(inbound["id"], "cdn.example",
                                         node_id=node["id"], apply=True)
        operation = queue.claim(node["id"], supported_types={"inbound"})[0]
        self.assertEqual(operation["id"], changed_sni["operation_id"])
        queue.finish(operation["id"], node_id=node["id"], lease_id=operation["lease_id"],
                     result={"health": "healthy", "atomic": True})
        current = service.find(inbound["id"], node["id"])
        self.assertEqual(current["config"]["streamSettings"]["realitySettings"]["serverNames"],
                         ["cdn.example"])
        endpoint = AccessKeyService(self.store).find(key["id"])["endpoints"]["xray_reality"]
        self.assertIn("sni=cdn.example", endpoint["uri"])

    @unittest.skipUnless(shutil.which("openssl"), "openssl is required")
    def test_node_certificate_key_is_delivered_only_to_assigned_node(self) -> None:
        first = NodeService(self.store).create("edge-a")
        second = NodeService(self.store).create("edge-b")
        manager = NodeCertificateService(self.store)
        certificate = manager.issue(first["id"], domains=["vpn.example"], ips=[])
        self.assertNotIn("key", certificate)
        config = {"listen": "0.0.0.0", "port": 443, "protocol": "vless", "tag": "tls",
            "settings": {"clients": []}, "streamSettings": {"network": "ws",
            "security": "tls", "tlsSettings": {"certificates": [{
                "certificateFile": "/managed/cert", "keyFile": "/managed/key"}]},
            "wsSettings": {"path": "/safe"}}}
        inbound = InboundService(self.store).register(first["id"], config)
        assigned = manager.assign(certificate["id"], inbound["id"])
        operations = self.store.load()["node_operations"]
        delivery = next(item for item in operations if item["id"] == assigned["operation_id"])
        self.assertEqual(delivery["node_id"], first["id"])
        self.assertIn("private_key", delivery["payload"])
        self.assertFalse(any(item["node_id"] == second["id"]
                             and item["resource_type"] == "tls_certificate"
                             for item in operations))


if __name__ == "__main__":
    unittest.main()
