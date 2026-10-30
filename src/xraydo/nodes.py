from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from .countries import detect_countries, format_remark, get_country
from .store import Store
from .sync_queue import enqueue_access_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _valid_name(value: str) -> bool:
    return bool(value) and len(value) <= 128 and not any(ord(character) < 32 for character in value)


def _valid_alias(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value))


class NodeService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def list(self) -> list[dict[str, Any]]:
        return list(self.store.load()["nodes"])

    def health_policy(self) -> dict[str, int]:
        policy = self.store.load()["settings"].get("node_health", {})
        return {
            "failure_threshold": int(policy.get("failure_threshold", 3)),
            "recovery_threshold": int(policy.get("recovery_threshold", 2)),
        }

    def set_health_policy(
        self, failure_threshold: Optional[int] = None, recovery_threshold: Optional[int] = None
    ) -> dict[str, int]:
        if failure_threshold is None and recovery_threshold is None:
            return self.health_policy()
        if any(
            value is not None and not 1 <= value <= 100
            for value in (failure_threshold, recovery_threshold)
        ):
            raise ValueError("node_invalid_health_threshold")
        data = self.store.load()
        policy = data["settings"].setdefault(
            "node_health", {"failure_threshold": 3, "recovery_threshold": 2}
        )
        if failure_threshold is not None:
            policy["failure_threshold"] = failure_threshold
        if recovery_threshold is not None:
            policy["recovery_threshold"] = recovery_threshold
        self.store.save(data)
        return dict(policy)

    def find(self, reference: str) -> dict[str, Any]:
        folded = reference.casefold()
        nodes = self.list()
        exact = [node for node in nodes if node["id"] == reference]
        if exact:
            return exact[0]
        aliases = [node for node in nodes if (node.get("alias") or "").casefold() == folded]
        if aliases:
            return aliases[0]
        names = [node for node in nodes if node["name"].casefold() == folded]
        if len(names) > 1:
            raise ValueError("node_ambiguous")
        if names:
            return names[0]
        raise ValueError("node_not_found")

    @staticmethod
    def _country(name: str, choice: Optional[str], auto_enabled: bool) -> tuple[str, Optional[dict[str, Any]], list[str]]:
        if choice == "none" or (choice is None and not auto_enabled):
            return "none", None, []
        if choice and choice != "auto":
            country = get_country(choice)
            if country is None:
                raise ValueError("country_invalid_iso")
            return "manual", country, []
        matches = detect_countries(name)
        if len(matches) == 1:
            return "auto", matches[0], []
        return "auto", None, [country["iso"] for country in matches]

    @staticmethod
    def _apply_country(node: dict[str, Any], country: Optional[dict[str, Any]]) -> None:
        node["country"] = country["iso"] if country else None
        base_remark = node.get("remark_base") or node["name"]
        node["remark"] = format_remark(base_remark, country) if country else base_remark

    def create(
        self,
        name: str,
        *,
        alias: Optional[str] = None,
        country: Optional[str] = None,
        remark: Optional[str] = None,
    ) -> dict[str, Any]:
        name = name.strip()
        alias = alias.strip() if alias else None
        if not _valid_name(name) or (remark is not None and not _valid_name(remark.strip())):
            raise ValueError("invalid_node_name")
        if alias and not _valid_alias(alias):
            raise ValueError("invalid_node_alias")
        data = self.store.load()
        if alias and any((node.get("alias") or "").casefold() == alias.casefold() for node in data["nodes"]):
            raise ValueError("duplicate_node_alias")
        mode, selected_country, candidates = self._country(
            name,
            country,
            bool(data["settings"].get("country_auto_flags", True)),
        )
        timestamp = _now()
        node = {
            "id": str(uuid.uuid4()),
            "name": name,
            "alias": alias,
            "country_mode": mode,
            "country_candidates": candidates,
            "remark_base": remark.strip() if remark else name,
            "enabled": True,
            "connection_state": "unknown",
            "last_seen_at": None,
            "last_success_at": None,
            "state_changed_at": timestamp,
            "offline_since": None,
            "latency_ms": None,
            "consecutive_failures": 0,
            "consecutive_successes": 0,
            "last_error": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        self._apply_country(node, selected_country)
        data["nodes"].append(node)
        for key in data.get("access_keys", []):
            enqueue_access_key(data, key, node_ids={node["id"]})
        self.store.save(data)
        return node

    def set_enabled(self, reference: str, enabled: bool) -> dict[str, Any]:
        data = self.store.load()
        target_id = self.find(reference)["id"]
        node = next(item for item in data["nodes"] if item["id"] == target_id)
        node["enabled"] = enabled
        node["updated_at"] = _now()
        self.store.save(data)
        return node

    def heartbeat(
        self,
        reference: str,
        state: str = "healthy",
        error: Optional[str] = None,
        latency_ms: Optional[int] = None,
    ) -> dict[str, Any]:
        if state not in {"healthy", "degraded", "unhealthy"}:
            raise ValueError("node_invalid_state")
        if error is not None and (len(error) > 500 or any(ord(char) < 32 for char in error)):
            raise ValueError("node_invalid_error")
        if latency_ms is not None and not 0 <= latency_ms <= 3_600_000:
            raise ValueError("node_invalid_latency")
        data = self.store.load()
        target_id = self.find(reference)["id"]
        node = next(item for item in data["nodes"] if item["id"] == target_id)
        timestamp = _now()
        previous = node["connection_state"]
        successes = int(node.get("consecutive_successes", 0)) + 1
        failures = int(node.get("consecutive_failures", 0))
        policy = data["settings"].get("node_health", {})
        failure_threshold = int(policy.get("failure_threshold", 3))
        recovery_threshold = int(policy.get("recovery_threshold", 2))
        if previous != "maintenance":
            node["connection_state"] = (
                "unhealthy"
                if failures >= failure_threshold and successes < recovery_threshold
                else state
            )
        node["last_seen_at"] = timestamp
        node["last_success_at"] = timestamp
        node["latency_ms"] = latency_ms
        node["consecutive_successes"] = successes
        if node["connection_state"] == state:
            node["offline_since"] = None
            node["consecutive_failures"] = 0
        node["last_error"] = error
        if node["connection_state"] != previous:
            node["state_changed_at"] = timestamp
        node["updated_at"] = timestamp
        self.store.save(data)
        return node

    def missed_heartbeat(self, reference: str, error: str = "heartbeat timeout") -> dict[str, Any]:
        if not error or len(error) > 500 or any(ord(char) < 32 for char in error):
            raise ValueError("node_invalid_error")
        data = self.store.load()
        target_id = self.find(reference)["id"]
        node = next(item for item in data["nodes"] if item["id"] == target_id)
        timestamp = _now()
        failures = int(node.get("consecutive_failures", 0)) + 1
        failure_threshold = int(
            data["settings"].get("node_health", {}).get("failure_threshold", 3)
        )
        node["consecutive_failures"] = failures
        node["consecutive_successes"] = 0
        node["last_error"] = error
        previous = node["connection_state"]
        if previous != "maintenance":
            node["connection_state"] = (
                "offline" if failures >= failure_threshold else "unhealthy"
            )
        if node["connection_state"] == "offline" and not node.get("offline_since"):
            node["offline_since"] = timestamp
        if node["connection_state"] != previous:
            node["state_changed_at"] = timestamp
        node["updated_at"] = timestamp
        self.store.save(data)
        return node

    def set_maintenance(self, reference: str, enabled: bool) -> dict[str, Any]:
        data = self.store.load()
        target_id = self.find(reference)["id"]
        node = next(item for item in data["nodes"] if item["id"] == target_id)
        state = "maintenance" if enabled else "unknown"
        if node["connection_state"] != state:
            node["connection_state"] = state
            node["state_changed_at"] = _now()
        node["updated_at"] = _now()
        self.store.save(data)
        return node

    def remove(self, reference: str) -> dict[str, Any]:
        data = self.store.load()
        target_id = self.find(reference)["id"]
        node = next(item for item in data["nodes"] if item["id"] == target_id)
        data["nodes"] = [item for item in data["nodes"] if item["id"] != target_id]
        data["node_operations"] = [
            item for item in data.get("node_operations", []) if item["node_id"] != target_id
        ]
        self.store.save(data)
        return node

    def rename(self, reference: str, name: str, country: Optional[str] = None) -> dict[str, Any]:
        name = name.strip()
        if not _valid_name(name):
            raise ValueError("invalid_node_name")
        data = self.store.load()
        target_id = self.find(reference)["id"]
        node = next(item for item in data["nodes"] if item["id"] == target_id)
        node["name"] = name
        node["remark_base"] = name
        if country is None and node.get("country_mode") == "manual":
            mode, selected, candidates = "manual", get_country(node["country"]), []
        else:
            choice = country if country is not None else node.get("country_mode", "auto")
            mode, selected, candidates = self._country(name, choice, True)
        node["country_mode"] = mode
        node["country_candidates"] = candidates
        self._apply_country(node, selected)
        node["updated_at"] = _now()
        self.store.save(data)
        return node

    def set_country(self, reference: str, country: str) -> dict[str, Any]:
        data = self.store.load()
        target_id = self.find(reference)["id"]
        node = next(item for item in data["nodes"] if item["id"] == target_id)
        mode, selected, candidates = self._country(node["name"], country, True)
        node["country_mode"] = mode
        node["country_candidates"] = candidates
        self._apply_country(node, selected)
        node["updated_at"] = _now()
        self.store.save(data)
        return node

    def set_alias(self, reference: str, alias: str) -> dict[str, Any]:
        alias = alias.strip()
        if alias and not _valid_alias(alias):
            raise ValueError("invalid_node_alias")
        data = self.store.load()
        target_id = self.find(reference)["id"]
        if alias and any(
            node["id"] != target_id and (node.get("alias") or "").casefold() == alias.casefold()
            for node in data["nodes"]
        ):
            raise ValueError("duplicate_node_alias")
        node = next(item for item in data["nodes"] if item["id"] == target_id)
        node["alias"] = alias or None
        node["updated_at"] = _now()
        self.store.save(data)
        return node

    def set_remark(self, reference: str, remark: str) -> dict[str, Any]:
        remark = remark.strip()
        if not _valid_name(remark):
            raise ValueError("invalid_node_name")
        data = self.store.load()
        target_id = self.find(reference)["id"]
        node = next(item for item in data["nodes"] if item["id"] == target_id)
        node["remark_base"] = remark
        self._apply_country(node, get_country(node["country"]) if node.get("country") else None)
        node["updated_at"] = _now()
        self.store.save(data)
        return node
