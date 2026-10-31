from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xraydo.hysteria_manager import (
    HysteriaManager,
    HysteriaManagerError,
    platform_capabilities,
    release,
    traffic_api,
)
from xraydo.nodes import NodeService
from xraydo.store import Store
from xraydo.subscriptions import SubscriptionService
from xraydo.sync_queue import SyncQueueService
from xraydo.users import UserService


class HysteriaManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "state")
        self.manager = HysteriaManager(self.store, self.root / "cache")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_platform_and_official_release_digest_are_verified(self) -> None:
        with (mock.patch("xraydo.hysteria_manager.platform.system", return_value="Linux"),
              mock.patch("xraydo.hysteria_manager.platform.machine", return_value="aarch64")):
            self.assertEqual(platform_capabilities()["architecture"], "arm64")
        metadata = {"tag_name": "app/v2.13.0", "draft": False, "prerelease": False,
                    "published_at": "2026-01-01T00:00:00Z", "assets": [{
                        "name": "hysteria-linux-amd64", "digest": "sha256:" + "ab" * 32,
                        "browser_download_url": "https://github.example/hysteria"}]}
        with (mock.patch("xraydo.hysteria_manager._request_json", return_value=metadata),
              mock.patch("xraydo.hysteria_manager.platform_capabilities", return_value={
                  "compatible": True, "architecture": "amd64", "features": {}})):
            selected = release("v2.13.0")
            self.assertEqual(selected["sha256"], "ab" * 32)
            metadata["assets"][0]["digest"] = None
            with self.assertRaisesRegex(HysteriaManagerError, "hysteria_checksum_missing"):
                release()

    def test_local_install_checks_binary_and_records_verified_core(self) -> None:
        binary = self.root / "bin" / "hysteria"
        content = b"verified-binary"
        metadata = {"version": "app/v2.13.0", "sha256": hashlib.sha256(content).hexdigest(),
                    "source": "https://github.com/HyNetworks/hysteria/releases/tag/app/v2.13.0",
                    "asset": "hysteria-linux-amd64", "url": "https://download.example/hysteria"}

        def download(_url: str, destination: Path) -> None:
            destination.write_bytes(content)

        with (mock.patch("xraydo.hysteria_manager.release", return_value=metadata),
              mock.patch("xraydo.hysteria_manager._download", side_effect=download),
              mock.patch("xraydo.hysteria_manager.subprocess.run", return_value=SimpleNamespace(
                  returncode=0, stdout="Version v2.13.0", stderr="")),
              mock.patch("xraydo.hysteria_manager.platform_capabilities", return_value={
                  "os": "linux", "machine": "x86_64", "architecture": "amd64",
                  "compatible": True, "features": {"quic": True}})):
            result = self.manager.install(binary_path=binary)
        self.assertEqual(result["state"], "installed")
        self.assertEqual(binary.read_bytes(), content)
        core = self.store.load()["node_cores"][0]
        self.assertEqual(core["sha256"], metadata["sha256"])
        self.assertEqual(core["version"], "app/v2.13.0")

    def test_remote_install_is_resolved_and_verified_on_target_node(self) -> None:
        node = NodeService(self.store).create("edge")
        with mock.patch("xraydo.hysteria_manager.release") as remote_release:
            result = self.manager.install(node["id"], version="v2.13.0")
        remote_release.assert_not_called()
        operation = SyncQueueService(self.store).list(node["id"])[-1]
        self.assertEqual(result["state"], "pending_node_execution")
        self.assertTrue(operation["payload"]["resolve_release_on_node"])
        self.assertTrue(operation["payload"]["verify_sha256"])
        self.assertEqual(operation["payload"]["version"], "app/v2.13.0")

    def test_all_node_install_is_rolling_and_claim_waits_for_previous_ack(self) -> None:
        first = NodeService(self.store).create("first")
        second = NodeService(self.store).create("second")
        original_install = self.manager.install

        def install(target: str, **kwargs: object) -> dict[str, object]:
            if target == "local":
                return {"node_id": "local", "state": "installed"}
            return original_install(target, **kwargs)

        with mock.patch.object(self.manager, "install", side_effect=install):
            result = self.manager.install_many("all")
        first_result, second_result = result["results"][1:]
        data = self.store.load()
        second_operation = next(item for item in data["node_operations"]
                                if item["id"] == second_result["operation_id"])
        self.assertEqual(second_operation["depends_on"], first_result["operation_id"])
        queue = SyncQueueService(self.store)
        self.assertEqual(queue.claim(second["id"]), [])
        queue.claim(first["id"])
        queue.finish(first_result["operation_id"])
        self.assertEqual(queue.claim(second["id"])[0]["id"], second_result["operation_id"])

    def test_rollback_uses_only_checksum_verified_cached_artifact(self) -> None:
        old = self.root / "cache" / "app-v2.12.0" / "hysteria-linux-amd64"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"old-verified")
        binary = self.root / "bin" / "hysteria"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"current")
        old_digest = hashlib.sha256(old.read_bytes()).hexdigest()
        data = self.store.load()
        data["node_cores"].append({"node_id": "local", "core": "hysteria2", "enabled": True,
                                   "version": "app/v2.13.0"})
        for version, digest, artifact in (("app/v2.12.0", old_digest, old),
                                          ("app/v2.13.0", "cd" * 32, binary)):
            data["core_versions"].append({"node_id": "local", "core": "hysteria2",
                "version": version, "sha256": digest, "source": "official",
                "artifact": str(artifact)})
        self.store.save(data)
        with (mock.patch("xraydo.hysteria_manager.control_service"),
              mock.patch("xraydo.hysteria_manager.platform_capabilities", return_value={
                  "architecture": "amd64", "features": {}})):
            result = self.manager.rollback(binary_path=binary)
        self.assertEqual(result["core"]["version"], "app/v2.12.0")
        self.assertEqual(binary.read_bytes(), b"old-verified")

    def test_core_ack_records_per_node_capabilities(self) -> None:
        node = NodeService(self.store).create("edge")
        operation = self.manager.install(node["id"])
        queue = SyncQueueService(self.store)
        queue.claim(node["id"])
        queue.finish(operation["operation_id"], result={"core": {
            "core": "hysteria2", "version": "app/v2.13.0", "enabled": True,
            "os": "linux", "machine": "aarch64", "architecture": "arm64",
            "compatible": True, "capabilities": {"quic": True, "udp": True}}})
        capabilities = self.manager.capabilities(node["id"])
        self.assertEqual(capabilities["architecture"], "arm64")
        self.assertTrue(capabilities["installed"])

    def test_access_key_endpoint_is_published_only_after_successful_ack(self) -> None:
        node = NodeService(self.store).create("edge")
        data = self.store.load()
        data["node_cores"].append({"node_id": node["id"], "core": "hysteria2", "enabled": True})
        self.store.save(data)
        user, _token = UserService(self.store).create("alice")
        key = next(item for item in self.store.load()["access_keys"] if item["user_id"] == user["id"])
        operations = [item for item in SyncQueueService(self.store).list(node["id"])
                      if item["resource_type"] == "hysteria_user"]
        self.assertEqual(len(operations), 1)
        self.assertNotIn("hysteria2@" + node["id"], key["endpoints"])
        queue = SyncQueueService(self.store)
        queue.claim(node["id"])
        queue.finish(operations[0]["id"], result={"endpoint": {
            "uri": "hysteria2://secret@edge.example:443/?sni=edge.example",
            "pin_sha256": "ab" * 32}})
        stored = next(item for item in self.store.load()["access_keys"] if item["id"] == key["id"])
        self.assertIn("hysteria2@" + node["id"], stored["endpoints"])
        built = SubscriptionService(self.store).build(user["id"])
        self.assertEqual(built["entries"][0]["protocol"], "hysteria2")

    def test_revoke_queues_every_enabled_hysteria_node_and_audits(self) -> None:
        first = NodeService(self.store).create("first")
        second = NodeService(self.store).create("second")
        user, _token = UserService(self.store).create("alice")
        data = self.store.load()
        data["node_cores"] = [{"node_id": item["id"], "core": "hysteria2", "enabled": True}
                              for item in (first, second)]
        self.store.save(data)
        key = next(item for item in data["access_keys"] if item["user_id"] == user["id"])
        result = self.manager.revoke_key(key["id"])
        self.assertEqual(len(result["results"]), 2)
        state = self.store.load()
        self.assertEqual(state["audit_log"][-1]["action"], "hysteria.key_revoke")
        operations = [item for item in state["node_operations"] if item["action"] == "revoke"]
        self.assertTrue(all(item["payload"]["kick_after_config_commit"] for item in operations))

    def test_loopback_stats_usage_and_limitations(self) -> None:
        with self.assertRaisesRegex(HysteriaManagerError, "hysteria_stats_not_loopback"):
            traffic_api("0.0.0.0:9999", "secret", "online")
        user, _token = UserService(self.store).create("alice")
        key = next(item for item in self.store.load()["access_keys"] if item["user_id"] == user["id"])
        username = "k_" + key["id"].replace("-", "")
        with mock.patch("xraydo.hysteria_manager.traffic_api", side_effect=[
                {username: {"tx": 11, "rx": 22}}, {username: 2}]):
            result = self.manager.collect_usage("local", "127.0.0.1:9999", "secret")
        self.assertEqual(result["applied"][0]["online_instances"], 2)
        self.assertEqual(result["applied"][0]["upload_bytes"], 22)
        self.assertEqual(result["applied"][0]["download_bytes"], 11)
        self.assertIn("client IPs", result["limitations"][0])
        self.assertEqual(self.store.load()["access_keys"][0]["quota"]["used_total_bytes"], 33)

    def test_backup_is_private_and_contains_integrity_hash(self) -> None:
        config = self.root / "config.yaml"
        output = self.root / "backups" / "hysteria.json"
        config.write_text('{"listen": ":443"}\n', encoding="utf-8")
        result = self.manager.backup("local", config, output)
        document = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["mode"], "0600")
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(document["config_sha256"], hashlib.sha256(
            document["config"].encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
