from __future__ import annotations

import hashlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .access_keys import AccessKeyService
from .store import Store
from .sync_queue import enqueue_access_key, enqueue_node_operation
from .rollout import RolloutService


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutomationService:
    """Periodic reconciliation and policy enforcement for unattended operation."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def configure(self, *, enabled: bool = True, interval_seconds: int = 60) -> dict[str, Any]:
        if not 10 <= interval_seconds <= 86_400:
            raise ValueError("automation_invalid_interval")
        data = self.store.load()
        settings = data.setdefault("settings", {}).setdefault("automation", {})
        settings.update({"enabled": bool(enabled), "interval_seconds": interval_seconds,
                         "updated_at": _now()})
        self.store.save(data)
        return dict(settings)

    def run_once(self) -> dict[str, Any]:
        data = self.store.load()
        settings = data.setdefault("settings", {}).setdefault(
            "automation", {"enabled": True, "interval_seconds": 60, "last_run_at": None}
        )
        if not settings.get("enabled", True):
            return {"state": "disabled", "queued": 0, "policy_changes": []}
        now = datetime.now(timezone.utc)
        changes = []
        key_service = AccessKeyService(self.store)
        for key in data.get("access_keys", []):
            before = (key.get("state"), key.get("disabled_reason"), key.get("blocked_until"))
            key_service._evaluate(key, now)
            key_service._evaluate_ip_limit(data, key, now)
            after = (key.get("state"), key.get("disabled_reason"), key.get("blocked_until"))
            if after != before:
                key["updated_at"] = _now()
                enqueue_access_key(data, key)
                changes.append({"key_id": key["id"], "from": before[0], "to": after[0],
                                "reason": after[1]})

        queued = 0
        pending_usage = {(op.get("node_id"), op.get("resource_id"))
                         for op in data.get("node_operations", [])
                         if op.get("resource_type") == "usage_collect"
                         and op.get("state") in {"pending", "delivering"}}
        for core in data.get("node_cores", []):
            if not core.get("enabled", True) or core.get("core") not in {"xray", "hysteria2", "telemt"}:
                continue
            node_id, core_name = core["node_id"], core["core"]
            if node_id == "local" or (node_id, core_name) in pending_usage:
                continue
            enqueue_node_operation(data, node_id, "usage_collect", "collect", {
                "core": core_name, "include_traffic": True, "include_client_ips": True,
                "cursor": core.get("usage_cursor"), "privacy": "global-addresses-only",
            }, resource_id=core_name)
            queued += 1

        desired = self._desired_digest(data)
        previous = settings.get("desired_digest")
        if previous != desired:
            pending = {(op.get("node_id"), op.get("resource_id"), op.get("revision"))
                       for op in data.get("node_operations", [])
                       if op.get("resource_type") == "access_key"
                       and op.get("state") in {"pending", "delivering"}}
            for key in data.get("access_keys", []):
                for node in data.get("nodes", []):
                    signature = (node["id"], key["id"], key.get("updated_at"))
                    if node.get("enabled", True) and signature not in pending:
                        queued += len(enqueue_access_key(data, key, node_ids={node["id"]}))
            settings["desired_digest"] = desired

        rollout_updates = []
        for rollout in list(data.get("rollouts", [])):
            if rollout.get("state") not in {"running", "rolling_back"}:
                continue
            # Persist the work above before RolloutService loads the state.
            self.store.save(data)
            rollout_updates.append(RolloutService(self.store).reconcile(rollout["id"]))
            data = self.store.load()
        timestamp = _now()
        settings["last_run_at"] = timestamp
        run = {"id": str(uuid.uuid4()), "started_at": timestamp, "finished_at": _now(),
               "state": "complete", "queued": queued, "policy_changes": changes,
               "rollouts": [{"id": item["id"], "state": item["state"]}
                            for item in rollout_updates]}
        data.setdefault("automation_runs", []).append(run)
        del data["automation_runs"][:-200]
        self.store.save(data)
        return run

    @staticmethod
    def _desired_digest(data: dict[str, Any]) -> str:
        document = {
            "users": [{"id": item.get("id"), "enabled": item.get("enabled"),
                       "updated_at": item.get("updated_at")} for item in data.get("users", [])],
            "keys": [{"id": item.get("id"), "state": item.get("state"),
                      "updated_at": item.get("updated_at")} for item in data.get("access_keys", [])],
            "nodes": [{"id": item.get("id"), "enabled": item.get("enabled"),
                       "updated_at": item.get("updated_at")} for item in data.get("nodes", [])],
        }
        return hashlib.sha256(json.dumps(document, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()

    def run(self, *, once: bool = False, interval_seconds: Optional[int] = None) -> dict[str, Any]:
        while True:
            result = self.run_once()
            if once:
                return result
            configured = self.store.load().get("settings", {}).get("automation", {})
            delay = interval_seconds or int(configured.get("interval_seconds", 60))
            if not 10 <= delay <= 86_400:
                raise ValueError("automation_invalid_interval")
            time.sleep(delay)
