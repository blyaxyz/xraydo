from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .audit import append_audit
from .store import Store


DEFAULT_RETENTION_DAYS = {
    "audit_log": 365,
    "usage_events": 90,
    "ip_observations": 7,
    "inbound_versions": 180,
    "node_operations": 30,
}
DEFAULT_MAX_RECORDS = {
    "audit_log": 10000,
    "usage_events": 100000,
    "ip_observations": 100000,
    "inbound_versions": 10000,
    "node_operations": 50000,
}
TIMESTAMP_FIELDS = {
    "audit_log": "timestamp",
    "usage_events": "timestamp",
    "ip_observations": "observed_at",
    "inbound_versions": "created_at",
    "node_operations": "updated_at",
}


def _datetime(value: Any) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class StateMaintenanceService:
    def __init__(self, store: Store) -> None:
        self.store = store

    @staticmethod
    def _compact_data(
        data: dict[str, Any], retention_days: dict[str, int], max_records: dict[str, int]
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        removed: dict[str, int] = {}
        for collection, default_days in DEFAULT_RETENTION_DAYS.items():
            days = retention_days.get(collection, default_days)
            maximum = max_records.get(collection, DEFAULT_MAX_RECORDS[collection])
            if not 1 <= days <= 3650 or not 100 <= maximum <= 10_000_000:
                raise ValueError("state_invalid_retention")
            original = list(data.get(collection, []))
            cutoff = now - timedelta(days=days)
            kept = []
            for item in original:
                # Pending/delivering operations are durable desired state and
                # never subject to age or count retention.
                if collection == "node_operations" and item.get("state") != "applied":
                    kept.append(item)
                    continue
                timestamp = _datetime(item.get(TIMESTAMP_FIELDS[collection]))
                if timestamp is None or timestamp >= cutoff:
                    kept.append(item)
            if collection == "inbound_versions":
                latest: dict[tuple[Any, Any], dict[str, Any]] = {}
                for item in original:
                    key = (item.get("node_id"), item.get("inbound_id"))
                    if key not in latest or str(item.get("created_at", "")) > str(latest[key].get("created_at", "")):
                        latest[key] = item
                known = {item.get("id") for item in kept}
                kept.extend(item for item in latest.values() if item.get("id") not in known)
            durable = ([item for item in kept if collection == "node_operations"
                        and item.get("state") != "applied"] if collection == "node_operations" else [])
            compactable = ([item for item in kept if item.get("state") == "applied"]
                           if collection == "node_operations" else kept)
            compactable.sort(key=lambda item: str(item.get(TIMESTAMP_FIELDS[collection], "")))
            compactable = compactable[-maximum:]
            data[collection] = durable + compactable
            removed[collection] = len(original) - len(data[collection])
        return removed

    def compact(
        self, *, dry_run: bool = False, retention_days: Optional[dict[str, int]] = None,
        max_records: Optional[dict[str, int]] = None,
    ) -> dict[str, Any]:
        retention_days = retention_days or {}
        max_records = max_records or {}
        if dry_run:
            data = copy.deepcopy(self.store.load())
            removed = self._compact_data(data, retention_days, max_records)
            return {"dry_run": True, "removed": removed}
        with self.store.transaction() as data:
            removed = self._compact_data(data, retention_days, max_records)
            append_audit(data, action="state.compact", object_type="state",
                         object_id="state.json", fields=[name for name, count in removed.items() if count])
            data.setdefault("settings", {})["last_compaction_at"] = datetime.now(timezone.utc).isoformat()
            return {"dry_run": False, "removed": removed,
                    "compacted_at": data["settings"]["last_compaction_at"]}
