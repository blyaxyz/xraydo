from __future__ import annotations

import json
import multiprocessing
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from xraydo.access_keys import AccessKeyService
from xraydo.nodes import NodeService
from xraydo.state_maintenance import StateMaintenanceService
from xraydo.store import Store
from xraydo.subscriptions import SubscriptionService
from xraydo.sync_queue import SyncQueueService
from xraydo.users import UserService


def _increment_state(directory: str, count: int) -> None:
    store = Store(Path(directory))
    for _index in range(count):
        with store.transaction() as data:
            data.setdefault("settings", {})["concurrent_counter"] = (
                int(data["settings"].get("concurrent_counter", 0)) + 1
            )


class StoreIntegrityTests(unittest.TestCase):
    def test_transactions_serialize_multiple_processes_without_lost_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            processes = [multiprocessing.get_context("fork").Process(
                target=_increment_state, args=(directory, 20)
            ) for _index in range(4)]
            for process in processes:
                process.start()
            for process in processes:
                process.join(20)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(Store(Path(directory)).load()["settings"]["concurrent_counter"], 80)

    def test_legacy_load_save_uses_revision_cas_instead_of_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            first = store.load()
            second = store.load()
            first["settings"]["first"] = True
            store.save(first)
            second["settings"]["second"] = True
            with self.assertRaisesRegex(ValueError, "state_conflict"):
                store.save(second)
            state = store.load()
            self.assertTrue(state["settings"]["first"])
            self.assertNotIn("second", state["settings"])

    def test_failed_atomic_replace_keeps_previous_state_readable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            initial = store.load()
            store.save(initial)
            changed = store.load()
            changed["settings"]["must_not_commit"] = True
            real_replace = os.replace

            def fail_state_replace(source: str, destination: str) -> None:
                if Path(destination) == store.path:
                    raise OSError("simulated crash")
                real_replace(source, destination)

            with mock.patch("xraydo.store.os.replace", side_effect=fail_state_replace):
                with self.assertRaisesRegex(OSError, "simulated crash"):
                    store.save(changed)
            self.assertNotIn("must_not_commit", store.load()["settings"])

    def test_exception_inside_transaction_rolls_back_every_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            baseline = store.load()["state_revision"]
            with self.assertRaisesRegex(RuntimeError, "simulated operation failure"):
                with store.transaction() as data:
                    data["settings"]["half_written"] = True
                    data["users"].append({"id": "partial"})
                    raise RuntimeError("simulated operation failure")
            state = store.load()
            self.assertEqual(state["state_revision"], baseline)
            self.assertNotIn("half_written", state["settings"])
            self.assertEqual(state["users"], [])

    def test_corrupt_primary_recovers_last_valid_previous_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            first = store.load()
            first["settings"]["generation"] = 1
            store.save(first)
            second = store.load()
            second["settings"]["generation"] = 2
            store.save(second)
            store.path.write_text('{"schema_version": 9, broken', encoding="utf-8")
            recovered = store.load()
            self.assertEqual(recovered["settings"]["generation"], 1)
            self.assertEqual(json.loads(store.path.read_text())["settings"]["generation"], 1)


class UserLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        self.nodes = NodeService(self.store)
        self.first_node = self.nodes.create("edge-1")
        self.second_node = self.nodes.create("edge-2")
        self.users = UserService(self.store)
        self.user, self.token = self.users.create("alice")
        self.keys = AccessKeyService(self.store)
        self.key = self.keys.default_for_user(self.user["id"])

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _ack_all(self) -> None:
        queue = SyncQueueService(self.store)
        for node in (self.first_node, self.second_node):
            while True:
                operations = queue.claim(node["id"], limit=100)
                if not operations:
                    break
                for operation in operations:
                    queue.finish(operation["id"], node_id=node["id"],
                                 lease_id=operation["lease_id"], result={})

    def test_disable_is_atomic_cascades_keys_and_blocks_subscription(self) -> None:
        self.keys.set_endpoint(self.key["id"], "xray_reality", {"uri": "vless://secret"})
        disabled = self.users.set_enabled("alice", False)
        self.assertFalse(disabled["enabled"])
        state = self.store.load()
        stored = next(item for item in state["access_keys"] if item["id"] == self.key["id"])
        self.assertEqual(stored["state"], "disabled")
        self.assertEqual(stored["disabled_reason"], "user_disabled")
        with self.assertRaisesRegex(ValueError, "subscription_inactive"):
            SubscriptionService(self.store).build(self.user["id"])
        pending_nodes = {item["node_id"] for item in state["node_operations"]
                         if item.get("owner_id") == self.user["id"] and item["state"] == "pending"}
        self.assertEqual(pending_nodes, {self.first_node["id"], self.second_node["id"]})
        enabled = self.users.set_enabled("alice", True)
        self.assertTrue(enabled["enabled"])
        self.assertEqual(self.keys.find(self.key["id"])["state"], "active")

    def test_delete_waits_for_every_node_and_grace_before_secret_purge(self) -> None:
        credentials = dict(self.key["credentials"])
        plan = self.users.delete_plan("alice", 0)
        self.assertEqual(set(plan["active_nodes"]), {self.first_node["id"], self.second_node["id"]})
        deletion = self.users.request_delete("alice", 0, actor="operator")
        self.assertEqual(deletion["state"], "blocking")
        with self.assertRaisesRegex(ValueError, "user_deletion_in_progress"):
            self.users.set_enabled("alice", True)
        with self.assertRaisesRegex(ValueError, "user_deletion_not_ready"):
            self.users.purge("alice")
        self._ack_all()
        status = self.users.deletion_status("alice")
        self.assertEqual(status["state"], "ready_to_purge")
        self.assertEqual(set(status["confirmed_node_ids"]),
                         {self.first_node["id"], self.second_node["id"]})
        purged = self.users.purge("alice", actor="operator")
        self.assertEqual(purged["state"], "purged")
        serialized = json.dumps(self.store.load())
        for secret in credentials.values():
            self.assertNotIn(secret, serialized)
        self.assertNotIn(self.token, serialized)
        with self.assertRaisesRegex(ValueError, "user_not_found"):
            self.users.find("alice")
        event = self.users.audit_log()[0]
        self.assertEqual(event["action"], "user.delete.purge")
        self.assertNotIn("operator", event["fields"])

    def test_delete_status_reports_partial_node_confirmation(self) -> None:
        self.users.request_delete("alice", 0)
        queue = SyncQueueService(self.store)
        for operation in queue.claim(self.first_node["id"], limit=100):
            queue.finish(operation["id"], node_id=self.first_node["id"],
                         lease_id=operation["lease_id"], result={})
        status = self.users.deletion_status("alice")
        self.assertEqual(status["state"], "blocking")
        self.assertEqual(status["nodes"][self.first_node["id"]], "confirmed")
        self.assertEqual(status["nodes"][self.second_node["id"]], "pending")

    def test_audit_events_are_value_free_for_user_key_and_limits(self) -> None:
        self.users.rename("alice", "private-name")
        self.users.set_alias("private-name", "private-alias")
        self.keys.set_limits(self.key["id"], total_bytes=123456789)
        serialized = json.dumps(self.users.audit_log(100))
        self.assertNotIn("private-name", serialized)
        self.assertNotIn("private-alias", serialized)
        self.assertNotIn("123456789", serialized)
        actions = {item["action"] for item in self.users.audit_log(100)}
        self.assertTrue({"user.create", "user.rename", "user.alias.update",
                         "access_key.limits.update"}.issubset(actions))


class RetentionTests(unittest.TestCase):
    def test_compaction_drops_old_history_but_never_pending_operations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            old = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
            new = datetime.now(timezone.utc).isoformat()
            with store.transaction() as data:
                data["audit_log"] = [
                    {"id": "old", "timestamp": old}, {"id": "new", "timestamp": new}
                ]
                data["usage_events"] = [{"id": "old", "timestamp": old}]
                data["ip_observations"] = [{"id": "old", "observed_at": old}]
                data["inbound_versions"] = [
                    {"id": "old", "node_id": "n", "inbound_id": "i", "created_at": old},
                    {"id": "new", "node_id": "n", "inbound_id": "i", "created_at": new},
                ]
                data["node_operations"] = [
                    {"id": "applied", "state": "applied", "updated_at": old},
                    {"id": "pending", "state": "pending", "updated_at": old},
                ]
            before_revision = store.load()["state_revision"]
            preview = StateMaintenanceService(store).compact(dry_run=True)
            self.assertGreater(sum(preview["removed"].values()), 0)
            self.assertEqual(store.load()["state_revision"], before_revision)
            result = StateMaintenanceService(store).compact()
            self.assertFalse(result["dry_run"])
            state = store.load()
            audit_ids = {item["id"] for item in state["audit_log"] if "id" in item}
            self.assertIn("new", audit_ids)
            self.assertNotIn("old", audit_ids)
            self.assertEqual([item["id"] for item in state["node_operations"]], ["pending"])
            self.assertEqual([item["id"] for item in state["inbound_versions"]], ["new"])


if __name__ == "__main__":
    unittest.main()
