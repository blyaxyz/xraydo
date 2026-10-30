from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from xraydo.store import Store
from xraydo.users import UserService


class AuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        self.users = UserService(self.store)
        self.user, self.token = self.users.create("alice")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_note_change_records_time_actor_and_field_names_only(self) -> None:
        updated = self.users.set_metadata(
            "alice",
            note="private customer note",
            tags=["paid", "priority"],
            actor="operator-1",
        )
        self.assertEqual(updated["metadata_updated_by"], "operator-1")
        self.assertIn("metadata_updated_at", updated)
        event = self.users.audit_log()[0]
        self.assertEqual(event["actor"], "operator-1")
        self.assertEqual(event["fields"], ["note", "tags"])
        serialized = json.dumps(event)
        self.assertNotIn("private customer note", serialized)
        self.assertNotIn(self.token, serialized)

    def test_admin_metadata_is_structured_and_audited_without_values(self) -> None:
        updated = self.users.set_admin_metadata(
            self.user["id"],
            contact="alice@example.com",
            source="partner",
            external_id="crm-42",
            attributes={"plan": "premium", "region": "eu"},
            actor="operator-2",
        )
        self.assertEqual(updated["admin"]["external_id"], "crm-42")
        self.assertEqual(updated["admin"]["attributes"]["plan"], "premium")
        event = self.users.audit_log()[0]
        self.assertEqual(event["action"], "user.admin_metadata.update")
        self.assertEqual(
            event["fields"],
            ["attributes", "contact", "external_id", "source"],
        )
        self.assertNotIn("alice@example.com", json.dumps(event))

    def test_admin_metadata_limits_and_empty_updates_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "admin_metadata_empty"):
            self.users.set_admin_metadata("alice")
        with self.assertRaisesRegex(ValueError, "invalid_admin_metadata"):
            self.users.set_admin_metadata("alice", attributes={"bad key": "value"})
        with self.assertRaisesRegex(ValueError, "invalid_audit_limit"):
            self.users.audit_log(0)
