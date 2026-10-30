from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xraydo.access_keys import AccessKeyService
from xraydo.nodes import NodeService
from xraydo.store import Store
from xraydo.sync_queue import SyncQueueService, public_operation
from xraydo.users import UserService


class SyncQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        self.nodes = NodeService(self.store)
        self.queue = SyncQueueService(self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_offline_node_keeps_change_until_acknowledged(self) -> None:
        node = self.nodes.create("edge", alias="edge-1")
        user, _token = UserService(self.store).create("alice")
        pending = self.queue.list(node["id"], "pending")
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["payload"]["user_id"], user["id"])
        self.assertNotIn("payload", public_operation(pending[0]))

        claimed = self.queue.claim(node["id"])
        self.assertEqual(claimed[0]["state"], "delivering")
        retried = self.queue.finish(claimed[0]["id"], error="temporary outage")
        self.assertEqual(retried["state"], "pending")
        claimed_again = self.queue.claim(node["id"])
        self.assertEqual(claimed_again[0]["attempts"], 2)
        applied = self.queue.finish(claimed_again[0]["id"])
        self.assertEqual(applied["state"], "applied")
        summary = self.queue.summary(node["id"])
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["last_error"], "temporary outage")

    def test_disabled_node_does_not_receive_new_changes(self) -> None:
        node = self.nodes.create("edge")
        self.nodes.set_enabled(node["id"], False)
        UserService(self.store).create("alice")
        self.assertEqual(self.queue.list(node["id"]), [])

    def test_rename_never_becomes_a_sync_identity_or_payload_field(self) -> None:
        node = self.nodes.create("edge")
        users = UserService(self.store)
        user, _token = users.create("alice")
        keys = AccessKeyService(self.store)
        key = keys.default_for_user(user["id"])
        users.rename("alice", "bob")
        keys.set_state(key["id"], "disabled", "manual")

        operations = self.queue.list(node["id"])
        self.assertEqual(len(operations), 2)
        for operation in operations:
            self.assertEqual(operation["owner_id"], user["id"])
            self.assertEqual(operation["resource_id"], key["id"])
            self.assertEqual(operation["payload"]["user_id"], user["id"])
            serialized = json.dumps(operation)
            self.assertNotIn("alice", serialized)
            self.assertNotIn("bob", serialized)

    def test_new_node_receives_existing_access_keys(self) -> None:
        UserService(self.store).create("alice")
        first = self.nodes.create("edge-1")
        self.assertEqual(len(self.queue.list(first["id"], "pending")), 1)
        node = self.nodes.create("edge-2")
        operations = self.queue.list(node["id"], "pending")
        self.assertEqual(len(operations), 1)
        self.assertEqual(operations[0]["resource_type"], "access_key")
        self.assertEqual(len(self.queue.list(first["id"], "pending")), 1)

    def test_heartbeat_and_remove_node_with_its_queue(self) -> None:
        node = self.nodes.create("edge")
        healthy = self.nodes.heartbeat(node["id"], "healthy", latency_ms=12)
        self.assertEqual(healthy["connection_state"], "healthy")
        self.assertEqual(healthy["latency_ms"], 12)
        self.assertIsNotNone(healthy["last_seen_at"])
        UserService(self.store).create("alice")
        removed = self.nodes.remove(node["id"])
        self.assertEqual(removed["id"], node["id"])
        self.assertEqual(self.queue.list(node["id"]), [])

    def test_missed_heartbeat_uses_threshold_and_maintenance_suppresses_alarm(self) -> None:
        node = self.nodes.create("edge")
        self.nodes.heartbeat(node["id"])
        first = self.nodes.missed_heartbeat(node["id"])
        self.assertEqual(first["connection_state"], "unhealthy")
        second = self.nodes.missed_heartbeat(node["id"])
        self.assertEqual(second["connection_state"], "unhealthy")
        third = self.nodes.missed_heartbeat(node["id"])
        self.assertEqual(third["connection_state"], "offline")
        self.assertIsNotNone(third["offline_since"])

        recovering = self.nodes.heartbeat(node["id"])
        self.assertEqual(recovering["connection_state"], "unhealthy")
        recovered = self.nodes.heartbeat(node["id"])
        self.assertEqual(recovered["connection_state"], "healthy")

        maintenance = self.nodes.set_maintenance(node["id"], True)
        self.assertEqual(maintenance["connection_state"], "maintenance")
        missed = self.nodes.missed_heartbeat(node["id"])
        self.assertEqual(missed["connection_state"], "maintenance")

    def test_health_thresholds_are_configurable(self) -> None:
        policy = self.nodes.set_health_policy(failure_threshold=1, recovery_threshold=3)
        self.assertEqual(policy, {"failure_threshold": 1, "recovery_threshold": 3})
        node = self.nodes.create("edge")
        offline = self.nodes.missed_heartbeat(node["id"])
        self.assertEqual(offline["connection_state"], "offline")
        for _attempt in range(2):
            recovering = self.nodes.heartbeat(node["id"])
            self.assertEqual(recovering["connection_state"], "unhealthy")
        recovered = self.nodes.heartbeat(node["id"])
        self.assertEqual(recovered["connection_state"], "healthy")
