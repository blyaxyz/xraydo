from __future__ import annotations

import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from xraydo.nodes import NodeService
from xraydo.store import Store
from xraydo.subscriptions import SubscriptionService
from xraydo.sync_queue import SyncQueueService
from xraydo.telemt_manager import TelemtManager, TelemtManagerError, api_request, release
from xraydo.users import UserService


def completed(stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


class TelemtManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = Store(self.root / "state")
        self.manager = TelemtManager(self.store, self.root / "cache")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_release_uses_official_asset_and_published_digest(self) -> None:
        metadata = {"tag_name": "3.5.15", "target_commitish": "main", "assets": [{
            "name": "telemt-x86_64-linux-gnu.tar.gz", "digest": "sha256:" + "ab" * 32,
            "browser_download_url": "https://github.example/telemt.tar.gz"}]}
        with (mock.patch("xraydo.telemt_manager._request_json", return_value=metadata),
              mock.patch("xraydo.telemt_manager.platform_capabilities", return_value={
                  "compatible": True, "target": "x86_64-unknown-linux-gnu", "libc": "gnu"})):
            selected = release("3.5.15")
            self.assertEqual(selected["sha256"], "ab" * 32)
            metadata["assets"][0]["digest"] = None
            with self.assertRaisesRegex(TelemtManagerError, "telemt_checksum_missing"):
                release()

    def test_source_build_is_locked_cached_and_records_provenance(self) -> None:
        commit = "a" * 40
        preflight = {"target": "x86_64-unknown-linux-gnu", "profile": "economy",
                     "rustc": "rustc 1.88.0", "commands": {"git": "git", "cargo": "cargo", "rustc": "rustc"}}

        def run(arguments: list[str], **kwargs: object) -> SimpleNamespace:
            if "clone" in arguments:
                source = Path(arguments[-1]); source.mkdir(parents=True)
                (source / "Cargo.lock").write_text("locked", encoding="utf-8")
            if "rev-parse" in arguments:
                return completed(commit + "\n")
            if arguments[0] == "cargo":
                source = Path(kwargs["cwd"])
                binary = source / "target" / preflight["target"] / "release" / "telemt"
                binary.parent.mkdir(parents=True); binary.write_bytes(b"compiled")
                environment = kwargs["env"]
                self.assertEqual(environment["CARGO_BUILD_JOBS"], "1")
                self.assertIn("--locked", arguments)
            return completed()

        with (mock.patch.object(self.manager, "preflight", return_value=preflight),
              mock.patch("xraydo.telemt_manager._run", side_effect=run)):
            artifact = self.manager.build("3.5.15", profile="economy")
        self.assertEqual(artifact["commit"], commit)
        self.assertTrue(artifact["provenance"]["cargo_locked"])
        self.assertEqual(artifact["cargo_lock_sha256"], hashlib.sha256(b"locked").hexdigest())
        self.assertTrue(Path(artifact["artifact"]).is_file())

    def test_verified_release_archive_installs_and_records_core(self) -> None:
        binary_content = b"telemt-binary"
        archive_buffer = io.BytesIO()
        with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
            info = tarfile.TarInfo("telemt-3.5.15/telemt"); info.size = len(binary_content)
            archive.addfile(info, io.BytesIO(binary_content))
        archive_content = archive_buffer.getvalue()
        metadata = {"version": "3.5.15", "commit": "a" * 40,
                    "asset": "telemt-x86_64-linux-gnu.tar.gz",
                    "url": "https://example.test/telemt.tar.gz",
                    "sha256": hashlib.sha256(archive_content).hexdigest(),
                    "target": "x86_64-unknown-linux-gnu", "source": "official",
                    "kind": "release", "profile": "release"}

        def download(_url: str, destination: Path) -> None:
            destination.write_bytes(archive_content)

        destination = self.root / "bin" / "telemt"
        with (mock.patch("xraydo.telemt_manager.release", return_value=metadata),
              mock.patch("xraydo.telemt_manager._download", side_effect=download),
              mock.patch("xraydo.telemt_manager._run", return_value=completed("telemt 3.5.15")),
              mock.patch("xraydo.telemt_manager.platform_capabilities", return_value={"features": {}})):
            result = self.manager.install(binary_path=destination)
        self.assertEqual(result["state"], "installed")
        self.assertEqual(destination.read_bytes(), binary_content)
        self.assertEqual(self.store.load()["node_cores"][0]["sha256"],
                         hashlib.sha256(binary_content).hexdigest())

    def test_remote_artifact_distribution_never_builds_on_node(self) -> None:
        node = NodeService(self.store).create("edge")
        artifact_file = self.root / "telemt"; artifact_file.write_bytes(b"artifact")
        data = self.store.load(); data["telemt_artifacts"].append({
            "id": "artifact-1", "artifact": str(artifact_file), "sha256": hashlib.sha256(b"artifact").hexdigest(),
            "version": "3.5.15", "commit": "b" * 40, "target": "x86_64-unknown-linux-gnu",
            "profile": "release", "kind": "source", "source": "official"})
        self.store.save(data)
        result = self.manager.install(node["id"], artifact_id="artifact-1")
        operation = next(item for item in self.store.load()["node_operations"]
                         if item["id"] == result["operation_id"])
        self.assertFalse(operation["payload"]["build_on_node"])
        self.assertTrue(operation["payload"]["verify_sha256_after_transfer"])

    def test_all_node_install_is_rolling_and_remote_nodes_never_compile(self) -> None:
        first = NodeService(self.store).create("first")
        second = NodeService(self.store).create("second")
        original = self.manager.install

        def install(target: str, **kwargs: object) -> dict[str, object]:
            if target == "local":
                return {"node_id": "local", "state": "installed"}
            return original(target, **kwargs)

        with mock.patch.object(self.manager, "install", side_effect=install):
            result = self.manager.install_many("all", version="3.5.15")
        first_result, second_result = result["results"][1:]
        operations = {item["id"]: item for item in self.store.load()["node_operations"]}
        self.assertIsNone(operations[first_result["operation_id"]]["depends_on"])
        self.assertEqual(operations[second_result["operation_id"]]["depends_on"],
                         first_result["operation_id"])
        self.assertFalse(operations[second_result["operation_id"]]["payload"]["build_on_node"])
        queue = SyncQueueService(self.store)
        self.assertEqual(queue.claim(second["id"]), [])
        queue.claim(first["id"]); queue.finish(first_result["operation_id"])
        self.assertEqual(queue.claim(second["id"])[0]["id"], second_result["operation_id"])

    def test_rollback_restores_only_verified_cached_binary(self) -> None:
        old = self.root / "old-telemt"; old.write_bytes(b"old")
        current_binary = self.root / "bin" / "telemt"; current_binary.parent.mkdir(parents=True)
        current_binary.write_bytes(b"current")
        data = self.store.load()
        current = {"node_id": "local", "core": "telemt", "enabled": True,
                   "version": "3.5.15", "artifact": str(current_binary),
                   "sha256": hashlib.sha256(b"current").hexdigest()}
        data["node_cores"].append(dict(current))
        data["core_versions"].extend([
            {**current, "version": "3.5.14", "artifact": str(old),
             "sha256": hashlib.sha256(b"old").hexdigest()}, current])
        self.store.save(data)
        with mock.patch("xraydo.telemt_manager.control_service"):
            result = self.manager.rollback(binary_path=current_binary)
        self.assertEqual(result["core"]["version"], "3.5.14")
        self.assertEqual(current_binary.read_bytes(), b"old")

    def test_new_key_syncs_and_links_publish_only_after_ack(self) -> None:
        node = NodeService(self.store).create("edge")
        data = self.store.load(); data["node_cores"].append({
            "node_id": node["id"], "core": "telemt", "enabled": True})
        self.store.save(data)
        user, _token = UserService(self.store).create("alice")
        state = self.store.load(); key = next(item for item in state["access_keys"] if item["user_id"] == user["id"])
        operation = next(item for item in state["node_operations"] if item["resource_type"] == "telemt_user")
        self.assertNotIn("telemt@" + node["id"], key["endpoints"])
        queue = SyncQueueService(self.store); queue.claim(node["id"])
        queue.finish(operation["id"], result={"endpoint": {
            "tg_uri": "tg://proxy?server=edge.example&port=443&secret=dd00",
            "https_uri": "https://t.me/proxy?server=edge.example&port=443&secret=dd00"}})
        subscription = SubscriptionService(self.store).build(user["id"])
        self.assertEqual(len(subscription["entries"]), 0)
        self.assertEqual(len(subscription["mtproto"]), 2)

    def test_usage_cursors_prevent_double_counting(self) -> None:
        user, _token = UserService(self.store).create("alice")
        key = next(item for item in self.store.load()["access_keys"] if item["user_id"] == user["id"])
        username = "k_" + key["id"].replace("-", "")
        response = {"ok": True, "data": [{"username": username, "total_octets": 100,
                                             "current_connections": 2}]}
        with mock.patch("xraydo.telemt_manager.api_request", return_value=response):
            first = self.manager.collect_usage("local", "127.0.0.1:9091", "Bearer secret")
            second = self.manager.collect_usage("local", "127.0.0.1:9091", "Bearer secret")
        self.assertEqual(first["applied"][0]["delta_bytes"], 100)
        self.assertEqual(second["applied"][0]["delta_bytes"], 0)
        self.assertEqual(self.store.load()["access_keys"][0]["quota"]["used_total_bytes"], 100)

    def test_api_is_loopback_only_and_backup_is_private(self) -> None:
        with self.assertRaisesRegex(TelemtManagerError, "telemt_api_not_loopback"):
            api_request("0.0.0.0:9091", "Bearer secret", "GET", "/v1/health")
        config = self.root / "telemt.toml"; config.write_text("[access.users]\n", encoding="utf-8")
        output = self.root / "backup" / "telemt.json"
        result = self.manager.backup("local", config, output)
        self.assertEqual(result["mode"], "0600")
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(output.read_text())["format"], "xraydo-telemt-backup-v1")


if __name__ == "__main__":
    unittest.main()
