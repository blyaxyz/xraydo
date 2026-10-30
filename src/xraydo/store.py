from __future__ import annotations

import json
import os
import secrets
import tempfile
import uuid
from pathlib import Path
from typing import Any, Optional

CURRENT_SCHEMA_VERSION = 8


def configured_role() -> str:
    path = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json"))
    try:
        with path.open(encoding="utf-8") as handle:
            role = json.load(handle).get("role")
    except (OSError, ValueError, AttributeError):
        return "standalone"
    return role if role in {"standalone", "master", "node"} else "standalone"


def default_data_dir() -> Path:
    override = os.environ.get("XRAYDO_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.geteuid() == 0:
        return Path("/var/lib/xraydo")
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "xraydo"


class Store:
    """Small atomic JSON store; schema is deliberately explicit and versioned."""

    def __init__(self, data_dir: Optional[Path] = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.path = self.data_dir / "state.json"

    @staticmethod
    def initial() -> dict[str, Any]:
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "settings": {
                "country_auto_flags": True,
                "role": configured_role(),
                "node_health": {"failure_threshold": 3, "recovery_threshold": 2},
            },
            "users": [],
            "nodes": [],
            "audit_log": [],
            "access_keys": [],
            "usage_events": [],
            "ip_observations": [],
            "subscription_credentials": [],
            "node_operations": [],
        }

    @staticmethod
    def migrate(data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Apply ordered, idempotent state migrations without dropping user data."""
        version = data.get("schema_version", 0)
        if not isinstance(version, int) or version < 0:
            raise RuntimeError("invalid state schema")
        if version > CURRENT_SCHEMA_VERSION:
            raise RuntimeError("unsupported state schema")
        changed = False
        if version == 0:
            data.setdefault("settings", {})
            data.setdefault("users", [])
            data["schema_version"] = 1
            version = 1
            changed = True
        if version == 1:
            data.setdefault("nodes", [])
            data.setdefault("settings", {}).setdefault("country_auto_flags", True)
            data.setdefault("audit_log", [])
            data["schema_version"] = 2
            version = 2
            changed = True
        if version == 2:
            keys = data.setdefault("access_keys", [])
            for user in data.get("users", []):
                old_credentials = user.pop("credentials", {})
                old_endpoints = user.pop("endpoints", {})
                hysteria = old_endpoints.get("hysteria2", {})
                telemt = old_endpoints.get("telemt", {})
                keys.append(
                    {
                        "id": str(uuid.uuid4()),
                        "user_id": user["id"],
                        "device_name": "default",
                        "state": "active" if user.get("enabled", True) else "disabled",
                        "disabled_reason": None if user.get("enabled", True) else "manual",
                        "credentials": {
                            "xray_uuid": old_credentials.get("xray_uuid", str(uuid.uuid4())),
                            "hysteria_password": hysteria.get(
                                "password", secrets.token_urlsafe(24)
                            ),
                            "telemt_secret": telemt.get("user_secret", secrets.token_hex(16)),
                        },
                        "endpoints": old_endpoints,
                        "quota": {
                            "total_bytes": None,
                            "upload_bytes": None,
                            "download_bytes": None,
                            "used_total_bytes": 0,
                            "used_upload_bytes": 0,
                            "used_download_bytes": 0,
                            "max_ips": None,
                        },
                        "validity": {
                            "starts": "immediate",
                            "duration_seconds": None,
                            "activated_at": user.get("created_at"),
                            "expires_at": None,
                        },
                        "created_at": user.get("created_at"),
                        "updated_at": user.get("updated_at"),
                    }
                )
            data["schema_version"] = 3
            version = 3
            changed = True
        if version == 3:
            data.setdefault("usage_events", [])
            for key in data.setdefault("access_keys", []):
                key.setdefault(
                    "quota",
                    {
                        "total_bytes": None,
                        "upload_bytes": None,
                        "download_bytes": None,
                        "used_total_bytes": 0,
                        "used_upload_bytes": 0,
                        "used_download_bytes": 0,
                        "max_ips": None,
                    },
                )
                key.setdefault(
                    "validity",
                    {
                        "starts": "immediate",
                        "duration_seconds": None,
                        "activated_at": key.get("created_at"),
                        "expires_at": None,
                    },
                )
            data["schema_version"] = 4
            version = 4
            changed = True
        if version == 4:
            data.setdefault("node_operations", [])
            for node in data.setdefault("nodes", []):
                node.setdefault("enabled", True)
                node.setdefault("connection_state", "offline")
                node.setdefault("last_seen_at", None)
                node.setdefault("last_error", None)
            data["schema_version"] = 5
            version = 5
            changed = True
        if version == 5:
            data.setdefault("settings", {}).setdefault("role", configured_role())
            data["settings"].setdefault(
                "node_health", {"failure_threshold": 3, "recovery_threshold": 2}
            )
            for node in data.setdefault("nodes", []):
                if node.get("connection_state") == "online":
                    node["connection_state"] = "healthy"
                node.setdefault("connection_state", "unknown")
                node.setdefault("state_changed_at", node.get("updated_at"))
                node.setdefault("last_success_at", node.get("last_seen_at"))
                node.setdefault("offline_since", None)
                node.setdefault("latency_ms", None)
                node.setdefault("consecutive_failures", 0)
                node.setdefault("consecutive_successes", 0)
            for operation in data.setdefault("node_operations", []):
                operation.setdefault("last_delivery_error", operation.get("last_error"))
            data["schema_version"] = 6
            version = 6
            changed = True
        if version == 6:
            data.setdefault("ip_observations", [])
            for key in data.setdefault("access_keys", []):
                quota = key.setdefault("quota", {})
                quota.setdefault("ip_window_seconds", 300)
                quota.setdefault("ip_action", "disable")
                quota.setdefault("trusted_networks", [])
                quota.setdefault("ip_limit_warning", False)
                key.setdefault("last_connected_at", None)
            data["schema_version"] = 7
            version = 7
            changed = True
        if version == 7:
            credentials = data.setdefault("subscription_credentials", [])
            known_users = {item.get("user_id") for item in credentials}
            for user in data.setdefault("users", []):
                legacy_digest = user.pop("subscription_token_hash", None)
                has_credential = bool(legacy_digest or user["id"] in known_users)
                if legacy_digest and user["id"] not in known_users:
                    credentials.append(
                        {
                            "user_id": user["id"],
                            "algorithm": "sha256-legacy",
                            "salt": None,
                            "digest": legacy_digest,
                            "created_at": user.get("created_at"),
                            "updated_at": user.get("updated_at"),
                            "revoked_at": None,
                        }
                    )
                user.setdefault("subscription_state", "active" if has_credential else "revoked")
            data["schema_version"] = 8
            version = 8
            changed = True
        if "audit_log" not in data:
            data["audit_log"] = []
            changed = True
        if version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError("unsupported state schema")
        return data, changed

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.initial()
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise RuntimeError("invalid state document")
        data, changed = self.migrate(data)
        if changed:
            self.save(data)
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=self.data_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
