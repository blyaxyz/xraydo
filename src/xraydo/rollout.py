from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .store import Store
from .sync_queue import enqueue_node_operation


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RolloutService:
    """Shared canary/batched rollout for core and inbound operations."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def plan(self, *, resource_type: str, action: str, targets: list[dict[str, Any]],
             canary: int = 1, max_unavailable: int = 1,
             rollback_action: str = "rollback") -> dict[str, Any]:
        if resource_type not in {"core", "inbound"} or not targets:
            raise ValueError("rollout_invalid_resource")
        if not 0 <= canary <= len(targets) or not 1 <= max_unavailable <= len(targets):
            raise ValueError("rollout_invalid_strategy")
        normalized = []
        seen = set()
        for target in targets:
            node_id, resource_id = target.get("node_id"), target.get("resource_id")
            if not isinstance(node_id, str) or not isinstance(resource_id, str):
                raise ValueError("rollout_invalid_target")
            if (node_id, resource_id) in seen:
                raise ValueError("rollout_duplicate_target")
            seen.add((node_id, resource_id)); normalized.append(copy.deepcopy(target))
        return {"id": str(uuid.uuid4()), "resource_type": resource_type, "action": action,
                "rollback_action": rollback_action, "canary": canary,
                "max_unavailable": max_unavailable, "targets": normalized,
                "state": "planned", "created_at": _now()}

    def start(self, plan: dict[str, Any], *, confirm: bool = False,
              local_executor: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None) -> dict[str, Any]:
        if not confirm:
            return {"state": "confirmation_required", "plan": copy.deepcopy(plan)}
        data = self.store.load(); results = []
        canary = int(plan["canary"]); width = int(plan["max_unavailable"])
        previous_batch: list[str] = []
        current_batch: list[str] = []
        for index, target in enumerate(plan["targets"]):
            node_id = target["node_id"]
            if node_id == "local" and local_executor:
                try:
                    value = local_executor(copy.deepcopy(target))
                except Exception as error:
                    results.append({"node_id": node_id, "state": "error", "error": str(error)})
                    break
                results.append({"node_id": node_id, "state": "applied", "result": value})
                continue
            operation = enqueue_node_operation(
                data, node_id, plan["resource_type"], plan["action"],
                {**copy.deepcopy(target.get("payload", {})), "rollout_id": plan["id"],
                 "health_required": True, "rollback_on_failure": True,
                 "rollback_action": plan["rollback_action"]}, target["resource_id"],
            )
            # Canary operations serialize. Later targets are split into batches;
            # each batch waits for the previous batch's final ACK.
            if previous_batch:
                operation["depends_on"] = list(previous_batch)
            results.append({"node_id": node_id, "resource_id": target["resource_id"],
                            "state": "pending", "operation_id": operation["id"]})
            current_batch.append(operation["id"])
            position = index + 1
            batch_complete = position <= canary or ((position - canary) % width == 0)
            if batch_complete:
                previous_batch, current_batch = current_batch, []
        record = copy.deepcopy(plan); record.update({"state": "running", "started_at": _now(),
                                                     "results": results})
        data.setdefault("rollouts", []).append(record); self.store.save(data)
        return copy.deepcopy(record)

    def reconcile(self, rollout_id: str) -> dict[str, Any]:
        data = self.store.load()
        rollout = next((item for item in data.get("rollouts", []) if item["id"] == rollout_id), None)
        if rollout is None:
            raise ValueError("rollout_not_found")
        operations = {item["id"]: item for item in data.get("node_operations", [])
                      if (item.get("payload") or {}).get("rollout_id") == rollout_id}
        failed = [item for item in operations.values() if item.get("last_delivery_error")]
        if failed and rollout["state"] == "running":
            rollout["state"] = "rolling_back"; rollout["stopped_at"] = _now()
            for operation in operations.values():
                if operation.get("state") != "applied":
                    continue
                rollback = enqueue_node_operation(data, operation["node_id"],
                    operation["resource_type"], rollout["rollback_action"], {
                        "rollout_id": rollout_id, "original_operation_id": operation["id"],
                        "atomic": True,
                    }, operation["resource_id"])
                rollout["results"].append({"node_id": operation["node_id"],
                    "resource_id": operation["resource_id"], "state": "rollback_pending",
                    "operation_id": rollback["id"]})
        elif operations and all(item.get("state") == "applied" for item in operations.values()):
            rollout["state"] = ("rolled_back" if rollout["state"] == "rolling_back" else "complete")
            rollout["finished_at"] = _now()
        self.store.save(data)
        return copy.deepcopy(rollout)
