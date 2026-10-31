from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
import uuid
from contextlib import contextmanager
import fcntl
from pathlib import Path
from typing import Any, Callable, Iterator, Optional, TypeVar

from .user_ids import is_short_user_id, migrated_user_id

CURRENT_SCHEMA_VERSION = 10
T = TypeVar("T")


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
        self.backup_path = self.data_dir / "state.json.previous"
        self.lock_path = self.data_dir / ".state.lock"

    def _data_dir_mode(self) -> int:
        # Core services use separate unprivileged accounts and keep their
        # working directories below the system data root.  Telemt securely
        # opens every parent directory while rejecting symlinks, so it needs
        # read/search permission here.  state.json remains 0600 and TLS/key
        # directories retain their stricter modes.
        return 0o755 if self.data_dir == Path("/var/lib/xraydo") else 0o700

    @staticmethod
    def initial() -> dict[str, Any]:
        return {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "state_revision": 0,
            "settings": {
                "country_auto_flags": True,
                "role": configured_role(),
                "node_health": {"failure_threshold": 3, "recovery_threshold": 2},
                "next_user_numeric_id": 1,
            },
            "users": [],
            "nodes": [],
            "audit_log": [],
            "access_keys": [],
            "usage_events": [],
            "ip_observations": [],
            "subscription_credentials": [],
            "node_operations": [],
            "reality_checks": [],
            "inbounds": [],
            "inbound_versions": [],
            "node_cores": [],
            "core_versions": [],
            "hysteria_diagnostics": [],
            "telemt_artifacts": [],
            "telemt_diagnostics": [],
            "telemt_usage_cursors": [],
            "control_bootstrap_tokens": [],
            "node_credentials": [],
            "control_nonces": [],
            "user_deletions": [],
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
        if version == 8:
            keys_by_id = {
                item.get("id"): item for item in data.setdefault("access_keys", [])
            }
            for operation in data.setdefault("node_operations", []):
                if operation.get("resource_type") != "access_key":
                    continue
                payload = operation.get("payload") or {}
                key = keys_by_id.get(operation.get("resource_id")) or {}
                owner_id = payload.get("user_id") or key.get("user_id")
                if not isinstance(owner_id, str) or not owner_id:
                    raise RuntimeError("invalid access key sync owner")
                operation["owner_id"] = owner_id
            data["schema_version"] = 9
            version = 9
            changed = True
        if version == 9:
            users = data.setdefault("users", [])
            used_ids: set[str] = set()
            used_numbers: set[int] = set()
            id_map: dict[str, str] = {}
            next_number = 1
            for user in users:
                old_id = str(user.get("id", ""))
                short_id = old_id if is_short_user_id(old_id) and old_id not in used_ids else None
                if short_id is None:
                    short_id = migrated_user_id(old_id, used_ids)
                used_ids.add(short_id)
                id_map[old_id] = short_id
                user["id"] = short_id

                numeric_id = user.get("numeric_id")
                if not isinstance(numeric_id, int) or isinstance(numeric_id, bool) \
                        or numeric_id < 1 or numeric_id in used_numbers:
                    while next_number in used_numbers:
                        next_number += 1
                    numeric_id = next_number
                user["numeric_id"] = numeric_id
                used_numbers.add(numeric_id)
                next_number = max(next_number, numeric_id + 1)

            def replace_user_references(value: Any) -> None:
                if isinstance(value, dict):
                    for key, item in list(value.items()):
                        if key == "user_id" and isinstance(item, str) and item in id_map:
                            value[key] = id_map[item]
                        elif key == "owner_id" and isinstance(item, str) and item in id_map:
                            value[key] = id_map[item]
                        elif key == "object_id" and value.get("object_type") == "user" \
                                and isinstance(item, str) and item in id_map:
                            value[key] = id_map[item]
                        else:
                            replace_user_references(item)
                elif isinstance(value, list):
                    for item in value:
                        replace_user_references(item)

            for credential in data.setdefault("subscription_credentials", []):
                old_user_id = credential.get("user_id")
                if isinstance(old_user_id, str) and old_user_id in id_map \
                        and id_map[old_user_id] != old_user_id:
                    credential.setdefault(
                        "token_user_id_digest",
                        hashlib.sha256(old_user_id.encode()).hexdigest(),
                    )
            replace_user_references(data)
            settings = data.setdefault("settings", {})
            configured_next = settings.get("next_user_numeric_id")
            settings["next_user_numeric_id"] = max(
                next_number,
                configured_next if isinstance(configured_next, int)
                and not isinstance(configured_next, bool) else 1,
            )
            data["schema_version"] = 10
            version = 10
            changed = True
        if "audit_log" not in data:
            data["audit_log"] = []
            changed = True
        if "reality_checks" not in data:
            data["reality_checks"] = []
            changed = True
        for collection in ("inbounds", "inbound_versions", "node_cores", "core_versions",
                           "hysteria_diagnostics", "telemt_artifacts", "telemt_diagnostics",
                           "telemt_usage_cursors", "control_bootstrap_tokens",
                           "node_credentials", "control_nonces", "user_deletions"):
            if collection not in data:
                data[collection] = []
                changed = True
        if version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError("unsupported state schema")
        if not isinstance(data.get("state_revision", 0), int):
            raise RuntimeError("invalid state revision")
        data.setdefault("state_revision", 0)
        return data, changed

    def _prepare_directory(self) -> None:
        directory_mode = self._data_dir_mode()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=directory_mode)
        os.chmod(self.data_dir, directory_mode)

    @contextmanager
    def _lock(self, exclusive: bool = True) -> Iterator[None]:
        self._prepare_directory()
        descriptor = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.chmod(self.lock_path, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read_path(self, path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise RuntimeError("invalid state document")
        return data

    def _load_unlocked(self, *, recover: bool = True) -> tuple[dict[str, Any], bool]:
        if not self.path.exists():
            return self.initial(), False
        try:
            data = self._read_path(self.path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            if not recover or not self.backup_path.is_file():
                raise ValueError("state_corrupt") from error
            try:
                backup, changed = self.migrate(self._read_path(self.backup_path))
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as backup_error:
                raise ValueError("state_corrupt") from backup_error
            self._write_unlocked(backup, keep_backup=False)
            return backup, changed
        try:
            return self.migrate(data)
        except RuntimeError as error:
            if str(error) == "unsupported state schema":
                raise
            if not recover or not self.backup_path.is_file():
                raise ValueError("state_corrupt") from error
            try:
                backup, changed = self.migrate(self._read_path(self.backup_path))
            except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as backup_error:
                raise ValueError("state_corrupt") from backup_error
            self._write_unlocked(backup, keep_backup=False)
            return backup, changed

    def load(self) -> dict[str, Any]:
        with self._lock(exclusive=True):
            data, changed = self._load_unlocked()
            if changed:
                revision = int(data.get("state_revision", 0))
                data["state_revision"] = revision + 1
                self._write_unlocked(data)
            return data

    def save(self, data: dict[str, Any]) -> None:
        with self._lock(exclusive=True):
            current, _changed = self._load_unlocked()
            expected = int(data.get("state_revision", 0))
            actual = int(current.get("state_revision", 0))
            if expected != actual:
                raise ValueError("state_conflict")
            data["state_revision"] = actual + 1
            self._write_unlocked(data)

    def _write_unlocked(self, data: dict[str, Any], *, keep_backup: bool = True) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=self.data_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            if keep_backup and self.path.is_file():
                backup_descriptor, backup_temporary = tempfile.mkstemp(
                    prefix=".previous-", dir=self.data_dir
                )
                os.close(backup_descriptor)
                try:
                    shutil.copyfile(self.path, backup_temporary)
                    os.chmod(backup_temporary, 0o600)
                    with open(backup_temporary, "rb") as handle:
                        os.fsync(handle.fileno())
                    os.replace(backup_temporary, self.backup_path)
                finally:
                    Path(backup_temporary).unlink(missing_ok=True)
            os.replace(temporary, self.path)
            directory_descriptor = os.open(self.data_dir, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @contextmanager
    def transaction(self) -> Iterator[dict[str, Any]]:
        """Hold the inter-process lock for one atomic read-modify-write cycle."""
        with self._lock(exclusive=True):
            data, _changed = self._load_unlocked()
            revision = int(data.get("state_revision", 0))
            yield data
            data["state_revision"] = revision + 1
            self._write_unlocked(data)

    def update(self, callback: Callable[[dict[str, Any]], T]) -> T:
        with self.transaction() as data:
            return callback(data)
