from __future__ import annotations

import copy
import json
import secrets
import uuid
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .store import Store


ACCESS_KEY_SYNC_FIELDS = (
    "id",
    "user_id",
    "device_name",
    "state",
    "disabled_reason",
    "credentials",
    "endpoints",
    "quota",
    "validity",
    "created_at",
    "updated_at",
    "last_connected_at",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue_access_key(
    data: dict[str, Any],
    key: dict[str, Any],
    action: str = "upsert",
    node_ids: Optional[set[str]] = None,
) -> list[dict[str, Any]]:
    """Add one immutable desired-state operation for every enabled node."""
    owner_id = key.get("user_id")
    resource_id = key.get("id")
    if not isinstance(owner_id, str) or not owner_id or not isinstance(resource_id, str):
        raise ValueError("sync_invalid_identity")
    timestamp = _now()
    operations = data.setdefault("node_operations", [])
    payload = {
        field: copy.deepcopy(key[field])
        for field in ACCESS_KEY_SYNC_FIELDS
        if field in key
    }
    created = []
    for node in data.get("nodes", []):
        if not node.get("enabled", True) or (node_ids is not None and node["id"] not in node_ids):
            continue
        operation = {
            "id": str(uuid.uuid4()),
            "node_id": node["id"],
            "resource_type": "access_key",
            "resource_id": resource_id,
            "owner_id": owner_id,
            "action": action,
            "revision": key["updated_at"],
            "payload": copy.deepcopy(payload),
            "state": "pending",
            "attempts": 0,
            "last_error": None,
            "last_delivery_error": None,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        operations.append(operation)
        created.append(operation)
    # Hysteria credentials are separate per AccessKey and only go to nodes where
    # that core is enabled. Endpoint publication waits for the node's ack.
    existing_nodes = {operation["node_id"] for operation in created if operation["resource_type"] == "hysteria_user"}
    for core in data.get("node_cores", []):
        if core.get("core") != "hysteria2" or not core.get("enabled", True):
            continue
        node_id = core["node_id"]
        if node_ids is not None and node_id not in node_ids or node_id in existing_nodes:
            continue
        credential = key.get("credentials", {}).get("hysteria_password")
        operation = {
            "id": str(uuid.uuid4()), "node_id": node_id, "resource_type": "hysteria_user",
            "resource_id": resource_id, "owner_id": owner_id,
            "action": "revoke" if action == "revoke" or key.get("state") == "revoked" else "upsert",
            "revision": key["updated_at"],
            "payload": {"access_key_id": resource_id,
                        "username": "k_" + resource_id.replace("-", ""),
                        "password": credential if key.get("state") == "active" else None,
                        "state": key.get("state")},
            "state": "pending", "attempts": 0, "last_error": None,
            "last_delivery_error": None, "created_at": timestamp, "updated_at": timestamp,
        }
        operations.append(operation); created.append(operation)
    # Telemt uses the same immutable AccessKey identity, but its user API and
    # links are kept separate from ordinary proxy subscription entries.
    for core in data.get("node_cores", []):
        if core.get("core") != "telemt" or not core.get("enabled", True):
            continue
        node_id = core["node_id"]
        if node_ids is not None and node_id not in node_ids:
            continue
        operation = {
            "id": str(uuid.uuid4()), "node_id": node_id, "resource_type": "telemt_user",
            "resource_id": resource_id, "owner_id": owner_id,
            "action": "delete" if action == "revoke" or key.get("state") == "revoked" else "upsert",
            "revision": key["updated_at"],
            "payload": {"access_key_id": resource_id,
                        "username": "k_" + resource_id.replace("-", ""),
                        "secret": key.get("credentials", {}).get("telemt_secret")
                                  if key.get("state") == "active" else None,
                        "enabled": key.get("state") == "active", "api_preferred": True,
                        "atomic_config_fallback": True, "controlled_restart_if_required": True},
            "state": "pending", "attempts": 0, "last_error": None,
            "last_delivery_error": None, "created_at": timestamp, "updated_at": timestamp,
        }
        operations.append(operation); created.append(operation)
    return created


def enqueue_node_operation(
    data: dict[str, Any], node_id: str, resource_type: str, action: str,
    payload: dict[str, Any], resource_id: Optional[str] = None,
) -> dict[str, Any]:
    """Queue an explicit node-local job such as a REALITY scan or apply."""
    node = next((item for item in data.get("nodes", []) if item["id"] == node_id), None)
    if node_id != "local" and (node is None or not node.get("enabled", True)):
        raise ValueError("node_not_found")
    timestamp = _now()
    operation = {
        "id": str(uuid.uuid4()), "node_id": node_id, "resource_type": resource_type,
        "resource_id": resource_id or str(uuid.uuid4()), "owner_id": None,
        "action": action, "revision": timestamp, "payload": copy.deepcopy(payload),
        "state": "pending", "attempts": 0, "last_error": None,
        "last_delivery_error": None, "created_at": timestamp, "updated_at": timestamp,
    }
    data.setdefault("node_operations", []).append(operation)
    return operation


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

    def claim(
        self,
        node_id: str,
        limit: int = 100,
        *,
        supported_types: Optional[set[str]] = None,
        lease_seconds: int = 60,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("sync_invalid_limit")
        if not 5 <= lease_seconds <= 3600:
            raise ValueError("sync_invalid_lease")
        data = self.store.load()
        node = next((item for item in data["nodes"] if item["id"] == node_id), None)
        if node is None:
            raise ValueError("node_not_found")
        claimed = []
        now = datetime.now(timezone.utc)
        timestamp = now.isoformat()
        # A crashed agent must not leave operations permanently stuck in
        # delivering. An expired lease is safe to redeliver because operation
        # ids are immutable and agents persist their applied ids.
        for operation in data.get("node_operations", []):
            if operation.get("state") != "delivering":
                continue
            expires = operation.get("lease_expires_at")
            try:
                expired = expires is None or datetime.fromisoformat(expires) <= now
            except (TypeError, ValueError):
                expired = True
            if expired:
                operation["state"] = "pending"
                operation["lease_id"] = None
                operation["lease_expires_at"] = None
        for operation in data.get("node_operations", []):
            if operation["node_id"] != node_id or operation["state"] != "pending":
                continue
            if supported_types is not None and operation.get("resource_type") not in supported_types:
                continue
            retry_at = operation.get("next_attempt_at")
            if retry_at:
                try:
                    if datetime.fromisoformat(retry_at) > now:
                        continue
                except (TypeError, ValueError):
                    pass
            dependency_value = operation.get("depends_on")
            dependency_ids = ([dependency_value] if isinstance(dependency_value, str)
                              else dependency_value if isinstance(dependency_value, list) else [])
            applied_ids = {item["id"] for item in data.get("node_operations", [])
                           if item["state"] == "applied"}
            if dependency_ids and not set(dependency_ids).issubset(applied_ids):
                continue
            operation["state"] = "delivering"
            operation["attempts"] += 1
            operation["lease_id"] = secrets.token_urlsafe(24)
            operation["lease_expires_at"] = (now + timedelta(seconds=lease_seconds)).isoformat()
            operation["next_attempt_at"] = None
            operation["updated_at"] = timestamp
            claimed.append(copy.deepcopy(operation))
            if len(claimed) == limit:
                break
        self.store.save(data)
        return claimed

    def finish(self, operation_id: str, *, error: Optional[str] = None,
               result: Optional[dict[str, Any]] = None, node_id: Optional[str] = None,
               lease_id: Optional[str] = None, retry_after_seconds: int = 0) -> dict[str, Any]:
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
        if node_id is not None and operation["node_id"] != node_id:
            raise ValueError("sync_wrong_node")
        if operation["state"] == "applied" and error is None:
            # An ACK may be retried after the response was lost. It is accepted
            # only for the same node and result, never as a new state change.
            if lease_id is not None and lease_id != operation.get("last_lease_id"):
                raise ValueError("sync_invalid_lease")
            if result is not None and operation.get("result") != result:
                raise ValueError("sync_conflicting_ack")
            return operation
        if operation["state"] != "delivering":
            raise ValueError("sync_operation_not_claimed")
        if lease_id is not None and lease_id != operation.get("lease_id"):
            raise ValueError("sync_invalid_lease")
        if not 0 <= retry_after_seconds <= 86400:
            raise ValueError("sync_invalid_retry")
        operation["state"] = "pending" if error else "applied"
        operation["last_error"] = error
        if error:
            operation["last_delivery_error"] = error
            operation["next_attempt_at"] = (
                datetime.now(timezone.utc) + timedelta(seconds=retry_after_seconds)
            ).isoformat() if retry_after_seconds else None
        operation["last_lease_id"] = operation.get("lease_id")
        operation["lease_id"] = None
        operation["lease_expires_at"] = None
        operation["updated_at"] = _now()
        if result is not None:
            if not isinstance(result, dict) or len(json.dumps(result)) > 65536:
                raise ValueError("sync_invalid_result")
            operation["result"] = copy.deepcopy(result)
        if not error and operation.get("resource_type") in {"hysteria_user", "hysteria_inbound"} and result:
            endpoint = result.get("endpoint")
            if isinstance(endpoint, dict) and isinstance(endpoint.get("uri"), str):
                key = next((item for item in data.get("access_keys", [])
                            if item["id"] == operation["resource_id"]), None)
                if key is not None and key.get("state") == "active":
                    endpoint = copy.deepcopy(endpoint)
                    endpoint["node_id"] = operation["node_id"]
                    endpoint_key = ("hysteria2" if operation["node_id"] == "local"
                                    else "hysteria2@" + operation["node_id"])
                    key.setdefault("endpoints", {})[endpoint_key] = endpoint
                    key["updated_at"] = _now()
        if not error and operation.get("resource_type") in {"telemt_user", "telemt_inbound"} and result:
            endpoint = result.get("endpoint")
            if isinstance(endpoint, dict) and any(isinstance(endpoint.get(field), str)
                                                  for field in ("tg_uri", "https_uri")):
                key = next((item for item in data.get("access_keys", [])
                            if item["id"] == operation["resource_id"]), None)
                if key is not None and key.get("state") == "active":
                    endpoint = copy.deepcopy(endpoint); endpoint["node_id"] = operation["node_id"]
                    endpoint_key = ("telemt" if operation["node_id"] == "local"
                                    else "telemt@" + operation["node_id"])
                    key.setdefault("endpoints", {})[endpoint_key] = endpoint
                    key["updated_at"] = _now()
        if not error and operation.get("resource_type") == "core" and result:
            core = result.get("core")
            if isinstance(core, dict) and core.get("core") in {"hysteria2", "telemt"}:
                reported = copy.deepcopy(core)
                reported["node_id"] = operation["node_id"]
                records = data.setdefault("node_cores", [])
                current = next((item for item in records
                                if item.get("node_id") == operation["node_id"]
                                and item.get("core") == reported["core"]), None)
                if current is None:
                    records.append(reported)
                else:
                    current.update(reported)
        if not error and operation.get("resource_type") in {"inbound", "reality_rotation"}:
            if not result or result.get("health") != "healthy":
                raise ValueError("sync_health_ack_required")
            payload = operation.get("payload") or {}
            inbound = next((item for item in data.get("inbounds", [])
                            if item.get("id") == operation.get("resource_id")
                            and item.get("node_id") == operation.get("node_id")), None)
            if inbound is not None and inbound.get("revision") == payload.get(
                    "base_revision", payload.get("expected_revision")):
                if isinstance(payload.get("config"), dict):
                    inbound["config"] = copy.deepcopy(payload["config"])
                    inbound["client_settings"] = copy.deepcopy(
                        payload.get("client_settings", inbound.get("client_settings", {})))
                desired_state = payload.get("desired_state")
                if desired_state in {"enabled", "disabled", "deleted"}:
                    inbound["state"] = desired_state
                inbound["revision"] += 1
                inbound["updated_at"] = _now()
                inbound["last_apply"] = {"state": "working", "at": _now(),
                                         "health": "healthy", "operation_id": operation["id"]}
                data.setdefault("inbound_versions", []).append({
                    "id": str(uuid.uuid4()), "inbound_id": inbound["id"],
                    "node_id": inbound["node_id"], "revision": inbound["revision"],
                    "action": operation["resource_type"] + ".ack", "status": "working",
                    "config": copy.deepcopy(inbound["config"]), "remark": inbound["remark"],
                    "client_settings": copy.deepcopy(inbound.get("client_settings", {})),
                    "state": inbound["state"], "created_at": _now(),
                })
                short_ids = inbound.get("config", {}).get("streamSettings", {}).get(
                    "realitySettings", {}).get("shortIds", [])
                spider_x = inbound.get("client_settings", {}).get("spider_x")
                client_sni = inbound.get("client_settings", {}).get("sni")
                for key in data.get("access_keys", []):
                    for endpoint in key.get("endpoints", {}).values():
                        if endpoint.get("inbound_id") != inbound["id"] or endpoint.get(
                                "node_id", "local") != inbound["node_id"]:
                            continue
                        endpoint["inbound_revision"] = inbound["revision"]
                        endpoint["enabled"] = inbound["state"] == "enabled"
                        uri = endpoint.get("uri")
                        public_port = inbound.get("config", {}).get("port")
                        if isinstance(public_port, int):
                            endpoint["port"] = public_port
                            if isinstance(uri, str):
                                parsed_port = urllib.parse.urlsplit(uri)
                                if parsed_port.hostname:
                                    credentials = (parsed_port.netloc.rsplit("@", 1)[0] + "@"
                                                   if "@" in parsed_port.netloc else "")
                                    host = (f"[{parsed_port.hostname}]" if ":" in parsed_port.hostname
                                            else parsed_port.hostname)
                                    uri = urllib.parse.urlunsplit(parsed_port._replace(
                                        netloc=f"{credentials}{host}:{public_port}"))
                                    endpoint["uri"] = uri
                        if isinstance(uri, str) and (short_ids or spider_x or client_sni):
                            parsed = urllib.parse.urlsplit(uri)
                            query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                            replacements = {"sid": short_ids[0] if short_ids else None,
                                            "spx": spider_x, "sni": client_sni}
                            query = [(name, replacements.get(name) or value)
                                     for name, value in query]
                            if client_sni and not any(name == "sni" for name, _value in query):
                                query.append(("sni", client_sni))
                            endpoint["uri"] = urllib.parse.urlunsplit(
                                parsed._replace(query=urllib.parse.urlencode(query)))
                        key["updated_at"] = _now()
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
