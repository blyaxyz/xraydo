from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .store import Store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue_access_key(
    data: dict[str, Any],
    key: dict[str, Any],
    action: str = "upsert",
    node_ids: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Add one immutable desired-state operation for every enabled node."""
    timestamp = _now()
    operations = data.setdefault("node_operations", [])
    created = []
    for node in data.get("nodes", []):
        if not node.get("enabled", True) or (node_ids is not None and node["id"] not in node_ids):
            continue
        operation = {
            "id": str(uuid.uuid4()),
            "node_id": node["id"],
            "resource_type": "access_key",
            "resource_id": key["id"],
            "action": action,
            "revision": key["updated_at"],
            "payload": copy.deepcopy(key),
            "state": "pending",
            "attempts": 0,
            "last_error": None,
            "last_delivery_error": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        operations.append(operation)
        created.append(operation)
    return created


def public_operation(operation: dict[str, Any]) -> dict[str, Any]:
    """Queue inspection never returns credential-bearing payloads."""
    return {key: value for key, value in operation.items() if key != "payload"}


class SyncQueueService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def list(
        self, node_id: Optional[str] = None, state: Optional[str] = None
    ) -> list[dict[str, Any]]:
        operations = self.store.load().get("node_operations", [])
        return [
            operation
            for operation in operations
            if (node_id is None or operation["node_id"] == node_id)
            and (state is None or operation["state"] == state)
        ]

    def find(self, operation_id: str) -> dict[str, Any]:
        operation = next(
            (item for item in self.store.load().get("node_operations", []) if item["id"] == operation_id),
            None,
        )
        if operation is None:
            raise ValueError("sync_operation_not_found")
        return operation

    def claim(self, node_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("sync_invalid_limit")
        data = self.store.load()
        node = next((item for item in data["nodes"] if item["id"] == node_id), None)
        if node is None:
            raise ValueError("node_not_found")
        claimed = []
        timestamp = _now()
        for operation in data.get("node_operations", []):
            if operation["node_id"] != node_id or operation["state"] != "pending":
                continue
            operation["state"] = "delivering"
            operation["attempts"] += 1
            operation["updated_at"] = timestamp
            claimed.append(copy.deepcopy(operation))
            if len(claimed) == limit:
                break
        self.store.save(data)
        return claimed

    def finish(self, operation_id: str, *, error: Optional[str] = None) -> dict[str, Any]:
        if error is not None:
            error = error.strip()
            if not error or len(error) > 500 or any(ord(char) < 32 for char in error):
                raise ValueError("sync_invalid_error")
        data = self.store.load()
        operation = next(
            (item for item in data.get("node_operations", []) if item["id"] == operation_id),
            None,
        )
        if operation is None:
            raise ValueError("sync_operation_not_found")
        if operation["state"] != "delivering":
            raise ValueError("sync_operation_not_claimed")
        operation["state"] = "pending" if error else "applied"
        operation["last_error"] = error
        if error:
            operation["last_delivery_error"] = error
        operation["updated_at"] = _now()
        self.store.save(data)
        return operation

    def summary(self, node_id: str) -> dict[str, Any]:
        states = {"pending": 0, "delivering": 0, "applied": 0}
        operations = self.list(node_id)
        for operation in operations:
            states[operation["state"]] = states.get(operation["state"], 0) + 1
        pending = [item for item in operations if item["state"] in {"pending", "delivering"}]
        errors = [item for item in operations if item.get("last_delivery_error")]
        oldest = min((item["created_at"] for item in pending), default=None)
        oldest_age = None
        if oldest:
            created = datetime.fromisoformat(oldest.replace("Z", "+00:00"))
            oldest_age = max(0, int((datetime.now(timezone.utc) - created).total_seconds()))
        return {
            "node_id": node_id,
            "states": states,
            "complete": not pending,
            "oldest_pending_at": oldest,
            "oldest_pending_age_seconds": oldest_age,
            "last_error": max(errors, key=lambda item: item["updated_at"])["last_delivery_error"] if errors else None,
        }
