from __future__ import annotations

import secrets
import re
import uuid
import ipaddress
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .audit import append_audit
from .countries import format_key_name, get_country
from .store import Store
from .sync_queue import enqueue_access_key


PROTOCOL_CREDENTIAL_SCHEMA = {
    "xray_reality": {
        "access_key_fields": ("xray_uuid",),
        "client_endpoint_fields": ("public_key", "short_id", "spider_x", "fingerprint"),
        "node_only_fields": ("reality_private_key",),
    },
    "hysteria2": {
        "access_key_fields": ("hysteria_password",),
        "client_endpoint_fields": ("username", "obfs_password", "pin_sha256", "uri"),
        "node_only_fields": ("stats_secret", "tls_private_key"),
    },
    "telemt": {
        "access_key_fields": ("telemt_secret",),
        "client_endpoint_fields": ("username", "connection_secret", "tg_uri", "https_uri"),
        "node_only_fields": ("api_auth_header",),
    },
}


def protocol_credentials(key: dict[str, Any], protocol: str) -> dict[str, str]:
    schema = PROTOCOL_CREDENTIAL_SCHEMA.get(protocol)
    if schema is None:
        raise ValueError("access_key_unknown_protocol")
    credentials = key.get("credentials")
    if not isinstance(credentials, dict):
        raise ValueError("access_key_invalid_credentials")
    selected = {}
    for field in schema["access_key_fields"]:
        value = credentials.get(field)
        if not isinstance(value, str) or not value:
            raise ValueError("access_key_invalid_credentials")
        selected[field] = value
    try:
        if protocol == "xray_reality":
            uuid.UUID(selected["xray_uuid"])
        elif protocol == "hysteria2" and not 8 <= len(selected["hysteria_password"]) <= 256:
            raise ValueError
        elif protocol == "telemt" and re.fullmatch(
            r"[0-9a-fA-F]{32}", selected["telemt_secret"]
        ) is None:
            raise ValueError
    except (ValueError, AttributeError) as error:
        raise ValueError("access_key_invalid_credentials") from error
    return selected


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_access_key(
    user_id: str,
    device_name: str = "default",
    location: Optional[dict[str, str]] = None,
) -> dict[str, Any]:
    device_name = device_name.strip()
    if not device_name or len(device_name) > 128 or any(ord(char) < 32 for char in device_name):
        raise ValueError("invalid_device_name")
    timestamp = _now()
    return {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "device_name": device_name,
        "location": dict(location) if location else None,
        "state": "active",
        "disabled_reason": None,
        "credentials": {
            "xray_uuid": str(uuid.uuid4()),
            "hysteria_password": secrets.token_urlsafe(24),
            "telemt_secret": secrets.token_hex(16),
        },
        "endpoints": {},
        "quota": {
            "total_bytes": None,
            "upload_bytes": None,
            "download_bytes": None,
            "used_total_bytes": 0,
            "used_upload_bytes": 0,
            "used_download_bytes": 0,
            "max_ips": None,
            "ip_window_seconds": 300,
            "ip_action": "disable",
            "trusted_networks": [],
            "ip_limit_warning": False,
        },
        "validity": {
            "starts": "immediate",
            "duration_seconds": None,
            "activated_at": timestamp,
            "expires_at": None,
        },
        "created_at": timestamp,
        "updated_at": timestamp,
        "last_connected_at": None,
    }


class AccessKeyService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def list(self, user_id: Optional[str] = None) -> list[dict[str, Any]]:
        keys = self.store.load()["access_keys"]
        return [key for key in keys if user_id is None or key["user_id"] == user_id]

    def find(self, key_id: str) -> dict[str, Any]:
        matches = [key for key in self.list() if key["id"] == key_id]
        if len(matches) != 1:
            raise ValueError("access_key_not_found")
        return matches[0]

    def create(
        self,
        user_id: str,
        device_name: str = "default",
        location: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        data = self.store.load()
        if not any(user["id"] == user_id for user in data["users"]):
            raise ValueError("user_not_found")
        key = new_access_key(user_id, device_name, location)
        data["access_keys"].append(key)
        enqueue_access_key(data, key)
        append_audit(data, action="access_key.create", object_type="access_key",
                     object_id=key["id"], fields=["user_id", "device_name", "state"])
        self.store.save(data)
        return key

    def relocate(self, location: dict[str, str], language: str = "en") -> list[dict[str, Any]]:
        country = get_country(location.get("country", ""))
        if country is None:
            raise ValueError("country_invalid_iso")
        changed = []
        with self.store.transaction() as data:
            for key in data.get("access_keys", []):
                if not key.get("location"):
                    continue
                key["device_name"] = format_key_name(key["device_name"], country, language)
                key["location"] = dict(location)
                key["updated_at"] = _now()
                enqueue_access_key(data, key)
                append_audit(
                    data,
                    action="access_key.relocate",
                    object_type="access_key",
                    object_id=key["id"],
                    fields=["device_name", "location"],
                )
                changed.append(dict(key))
        return changed

    def default_for_user(self, user_id: str, key_id: Optional[str] = None) -> dict[str, Any]:
        data = self.store.load()
        now = datetime.now(timezone.utc)
        previous_states = {item["id"]: item["state"] for item in data["access_keys"]}
        for item in data["access_keys"]:
            self._evaluate(item, now)
            self._evaluate_ip_limit(data, item, now)
            if item["state"] != previous_states[item["id"]]:
                item["updated_at"] = _now()
                enqueue_access_key(data, item)
        self.store.save(data)
        if key_id:
            key = next((item for item in data["access_keys"] if item["id"] == key_id), None)
            if key is None:
                raise ValueError("access_key_not_found")
            if key["user_id"] != user_id:
                raise ValueError("access_key_wrong_user")
            if key["state"] != "active":
                raise ValueError("access_key_inactive")
            return key
        keys = [
            key for key in data["access_keys"]
            if key["user_id"] == user_id and key["state"] == "active"
        ]
        if not keys:
            raise ValueError("access_key_inactive")
        return keys[0]

    def set_state(self, key_id: str, state: str, reason: Optional[str] = None) -> dict[str, Any]:
        if state not in {"active", "disabled", "revoked"}:
            raise ValueError("access_key_invalid_state")
        data = self.store.load()
        key = next((item for item in data["access_keys"] if item["id"] == key_id), None)
        if key is None:
            raise ValueError("access_key_not_found")
        if key["state"] == "revoked" and state != "revoked":
            raise ValueError("access_key_revoked")
        key["state"] = state
        key["disabled_reason"] = reason if state != "active" else None
        key["updated_at"] = _now()
        if state == "active":
            self._evaluate(key, datetime.now(timezone.utc))
            self._evaluate_ip_limit(data, key, datetime.now(timezone.utc))
        append_audit(data, action=f"access_key.{state}", object_type="access_key",
                     object_id=key["id"], fields=["state", "disabled_reason"])
        enqueue_access_key(data, key, "revoke" if state == "revoked" else "upsert")
        self.store.save(data)
        return key

    def set_endpoint(self, key_id: str, protocol: str, endpoint: dict[str, Any]) -> dict[str, Any]:
        data = self.store.load()
        key = next((item for item in data["access_keys"] if item["id"] == key_id), None)
        if key is None:
            raise ValueError("access_key_not_found")
        key.setdefault("endpoints", {})[protocol] = endpoint
        key["updated_at"] = _now()
        enqueue_access_key(data, key)
        append_audit(data, action="access_key.endpoint.update", object_type="access_key",
                     object_id=key["id"], fields=["endpoint"])
        self.store.save(data)
        return key

    @staticmethod
    def _expiry(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("access_key_invalid_expiry") from error
        if parsed.tzinfo is None:
            raise ValueError("access_key_invalid_expiry")
        return parsed.astimezone(timezone.utc).isoformat()

    def set_limits(
        self,
        key_id: str,
        *,
        total_bytes: Optional[int] = None,
        upload_bytes: Optional[int] = None,
        download_bytes: Optional[int] = None,
        max_ips: Optional[int] = None,
        duration_seconds: Optional[int] = None,
        expires_at: Optional[str] = None,
        starts: Optional[str] = None,
        ip_window_seconds: Optional[int] = None,
        ip_action: Optional[str] = None,
        trusted_networks: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        if any(value is not None and value < 0 for value in (total_bytes, upload_bytes, download_bytes)):
            raise ValueError("access_key_invalid_limit")
        if max_ips is not None and not 0 <= max_ips <= 65_535:
            raise ValueError("access_key_invalid_limit")
        if duration_seconds is not None and duration_seconds <= 0:
            raise ValueError("access_key_invalid_duration")
        if duration_seconds is not None and expires_at is not None:
            raise ValueError("access_key_expiry_conflict")
        if starts is not None and starts not in {"immediate", "first_connect"}:
            raise ValueError("access_key_invalid_start")
        if ip_window_seconds is not None and not 30 <= ip_window_seconds <= 86_400:
            raise ValueError("access_key_invalid_ip_window")
        if ip_action is not None and ip_action not in {"warn", "temporary_block", "disable"}:
            raise ValueError("access_key_invalid_ip_action")
        normalized_networks = None
        if trusted_networks is not None:
            try:
                normalized_networks = sorted(
                    {str(ipaddress.ip_network(value, strict=False)) for value in trusted_networks}
                )
            except ValueError as error:
                raise ValueError("access_key_invalid_network") from error
        data = self.store.load()
        key = next((item for item in data["access_keys"] if item["id"] == key_id), None)
        if key is None:
            raise ValueError("access_key_not_found")
        quota = key.setdefault("quota", {})
        changed = []
        for field, value in (
            ("total_bytes", total_bytes),
            ("upload_bytes", upload_bytes),
            ("download_bytes", download_bytes),
            ("max_ips", max_ips),
        ):
            if value is not None:
                quota[field] = value or None
                changed.append(field)
        if ip_window_seconds is not None:
            quota["ip_window_seconds"] = ip_window_seconds
            changed.append("ip_window_seconds")
        if ip_action is not None:
            quota["ip_action"] = ip_action
            changed.append("ip_action")
        if normalized_networks is not None:
            quota["trusted_networks"] = normalized_networks
            changed.append("trusted_networks")
        validity = key.setdefault("validity", {})
        selected_start = starts or validity.get("starts", "immediate")
        if starts is not None:
            validity["starts"] = starts
            changed.append("starts")
        if duration_seconds is not None:
            validity["duration_seconds"] = duration_seconds
            if selected_start == "immediate":
                validity["activated_at"] = _now()
                validity["expires_at"] = (
                    datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
                ).isoformat()
            else:
                validity["activated_at"] = None
                validity["expires_at"] = None
            changed.extend(["duration_seconds", "expires_at"])
        if expires_at is not None:
            validity["starts"] = "immediate"
            validity["duration_seconds"] = None
            validity["activated_at"] = validity.get("activated_at") or _now()
            validity["expires_at"] = self._expiry(expires_at)
            changed.extend(["starts", "duration_seconds", "expires_at"])
        if not changed:
            raise ValueError("access_key_limits_empty")
        key["updated_at"] = _now()
        enqueue_access_key(data, key)
        append_audit(data, action="access_key.limits.update", object_type="access_key",
                     object_id=key["id"], fields=changed)
        self.store.save(data)
        return key

    def clear_limits(self, key_id: str) -> dict[str, Any]:
        data = self.store.load()
        key = next((item for item in data["access_keys"] if item["id"] == key_id), None)
        if key is None:
            raise ValueError("access_key_not_found")
        quota = key.setdefault("quota", {})
        for field in ("total_bytes", "upload_bytes", "download_bytes", "max_ips"):
            quota[field] = None
        quota["trusted_networks"] = []
        quota["ip_limit_warning"] = False
        validity = key.setdefault("validity", {})
        validity["starts"] = "immediate"
        validity["duration_seconds"] = None
        validity["expires_at"] = None
        validity["activated_at"] = validity.get("activated_at") or _now()
        if key.get("disabled_reason") in {"traffic", "expiry", "devices", "ip_limit"}:
            key["state"] = "active"
            key["disabled_reason"] = None
        key["updated_at"] = _now()
        enqueue_access_key(data, key)
        append_audit(
            data,
            action="access_key.limits.clear",
            object_type="access_key",
            object_id=key["id"],
            fields=["quota", "validity", "state"],
        )
        self.store.save(data)
        return key

    def extend(self, key_id: str, duration_seconds: int) -> dict[str, Any]:
        if duration_seconds <= 0:
            raise ValueError("access_key_invalid_duration")
        data = self.store.load()
        key = next((item for item in data["access_keys"] if item["id"] == key_id), None)
        if key is None:
            raise ValueError("access_key_not_found")
        validity = key.setdefault("validity", {})
        validity["duration_seconds"] = int(validity.get("duration_seconds") or 0) + duration_seconds
        expires_at = validity.get("expires_at")
        if expires_at:
            current = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            base = max(current, datetime.now(timezone.utc))
            validity["expires_at"] = (base + timedelta(seconds=duration_seconds)).isoformat()
        key["updated_at"] = _now()
        enqueue_access_key(data, key)
        append_audit(data, action="access_key.extend", object_type="access_key",
                     object_id=key["id"], fields=["duration_seconds", "expires_at"])
        self.store.save(data)
        return key

    def reset_traffic(self, key_id: str) -> dict[str, Any]:
        data = self.store.load()
        key = next((item for item in data["access_keys"] if item["id"] == key_id), None)
        if key is None:
            raise ValueError("access_key_not_found")
        quota = key.setdefault("quota", {})
        for field in ("used_total_bytes", "used_upload_bytes", "used_download_bytes"):
            quota[field] = 0
        timestamp = _now()
        quota["traffic_reset_at"] = timestamp
        key["updated_at"] = timestamp
        enqueue_access_key(data, key)
        append_audit(data, action="access_key.traffic.reset", object_type="access_key",
                     object_id=key["id"], fields=["traffic_counters"])
        self.store.save(data)
        return key

    @staticmethod
    def _evaluate(key: dict[str, Any], now: datetime) -> None:
        if key["state"] == "revoked":
            return
        if key.get("disabled_reason") == "ip_limit" and key.get("blocked_until"):
            blocked_until = datetime.fromisoformat(key["blocked_until"].replace("Z", "+00:00"))
            if blocked_until <= now:
                key["state"] = "active"
                key["disabled_reason"] = None
                key["blocked_until"] = None
        validity = key.get("validity", {})
        expires_at = validity.get("expires_at")
        if expires_at:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry <= now:
                key["state"] = "disabled"
                key["disabled_reason"] = "expiry"
                return
        quota = key.get("quota", {})
        exhausted = any(
            limit is not None and int(quota.get(used, 0)) >= int(limit)
            for limit, used in (
                (quota.get("total_bytes"), "used_total_bytes"),
                (quota.get("upload_bytes"), "used_upload_bytes"),
                (quota.get("download_bytes"), "used_download_bytes"),
            )
        )
        if exhausted:
            key["state"] = "disabled"
            key["disabled_reason"] = "traffic"

    @staticmethod
    def _is_trusted_ip(address: Any, key: dict[str, Any]) -> bool:
        networks = key.get("quota", {}).get("trusted_networks", [])
        return any(address in ipaddress.ip_network(network) for network in networks)

    @staticmethod
    def _active_ip_values(data: dict[str, Any], key: dict[str, Any], now: datetime) -> list[str]:
        window = int(key.get("quota", {}).get("ip_window_seconds", 300))
        cutoff = now - timedelta(seconds=window)
        values = {
            item["client_ip"]
            for item in data.get("ip_observations", [])
            if item["key_id"] == key["id"]
            and datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00")) >= cutoff
        }
        return sorted(values)

    def _evaluate_ip_limit(self, data: dict[str, Any], key: dict[str, Any], now: datetime) -> None:
        quota = key.get("quota", {})
        limit = quota.get("max_ips")
        active = self._active_ip_values(data, key, now)
        exceeded = limit is not None and len(active) > int(limit)
        quota["ip_limit_warning"] = bool(exceeded)
        if not exceeded or key["state"] == "revoked":
            return
        action = quota.get("ip_action", "disable")
        if action == "warn":
            return
        key["state"] = "disabled"
        key["disabled_reason"] = "ip_limit"
        if action == "temporary_block":
            window = int(quota.get("ip_window_seconds", 300))
            key["blocked_until"] = (now + timedelta(seconds=window)).isoformat()

    def record_usage(
        self,
        key_id: str,
        *,
        source_id: str,
        event_id: str,
        upload_bytes: int,
        download_bytes: int,
        connected: bool = False,
        client_ip: Optional[str] = None,
    ) -> tuple[dict[str, Any], bool]:
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", source_id):
            raise ValueError("usage_invalid_source")
        if not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", event_id):
            raise ValueError("usage_invalid_event")
        if not 0 <= upload_bytes <= 2**63 - 1 or not 0 <= download_bytes <= 2**63 - 1:
            raise ValueError("usage_invalid_bytes")
        address = None
        if client_ip is not None:
            try:
                address = ipaddress.ip_address(client_ip)
            except ValueError as error:
                raise ValueError("usage_invalid_ip") from error
        data = self.store.load()
        key = next((item for item in data["access_keys"] if item["id"] == key_id), None)
        if key is None:
            raise ValueError("access_key_not_found")
        duplicate = any(
            event["key_id"] == key_id
            and event["source_id"] == source_id
            and event["event_id"] == event_id
            for event in data.setdefault("usage_events", [])
        )
        if duplicate:
            return key, True
        timestamp = _now()
        data["usage_events"].append(
            {
                "key_id": key_id,
                "user_id": key["user_id"],
                "source_id": source_id,
                "event_id": event_id,
                "upload_bytes": upload_bytes,
                "download_bytes": download_bytes,
                "timestamp": timestamp,
            }
        )
        if address is not None and address.is_global and not self._is_trusted_ip(address, key):
            data.setdefault("ip_observations", []).append(
                {
                    "key_id": key_id,
                    "source_id": source_id,
                    "event_id": event_id,
                    "client_ip": str(address),
                    "observed_at": timestamp,
                }
            )
        quota = key.setdefault("quota", {})
        quota["used_upload_bytes"] = int(quota.get("used_upload_bytes", 0)) + upload_bytes
        quota["used_download_bytes"] = int(quota.get("used_download_bytes", 0)) + download_bytes
        quota["used_total_bytes"] = int(quota.get("used_total_bytes", 0)) + upload_bytes + download_bytes
        validity = key.setdefault("validity", {})
        if validity.get("starts") == "first_connect" and not validity.get("activated_at"):
            if connected or upload_bytes or download_bytes:
                activation = datetime.now(timezone.utc)
                validity["activated_at"] = activation.isoformat()
                duration = validity.get("duration_seconds")
                validity["expires_at"] = (
                    activation + timedelta(seconds=int(duration))
                ).isoformat() if duration else None
        previous_state = key["state"]
        if connected or address is not None:
            key["last_connected_at"] = timestamp
        key["updated_at"] = timestamp
        evaluated_at = datetime.now(timezone.utc)
        self._evaluate(key, evaluated_at)
        self._evaluate_ip_limit(data, key, evaluated_at)
        if key["state"] != previous_state:
            enqueue_access_key(data, key)
        self.store.save(data)
        return key, False

    def active_ips(self, key_id: str) -> dict[str, Any]:
        data = self.store.load()
        key = next((item for item in data["access_keys"] if item["id"] == key_id), None)
        if key is None:
            raise ValueError("access_key_not_found")
        values = self._active_ip_values(data, key, datetime.now(timezone.utc))
        return {"key_id": key_id, "active_ips": values, "count": len(values)}

    def clear_ips(self, key_id: str) -> dict[str, Any]:
        data = self.store.load()
        key = next((item for item in data["access_keys"] if item["id"] == key_id), None)
        if key is None:
            raise ValueError("access_key_not_found")
        data["ip_observations"] = [
            item for item in data.get("ip_observations", []) if item["key_id"] != key_id
        ]
        key.setdefault("quota", {})["ip_limit_warning"] = False
        self.store.save(data)
        return {"key_id": key_id, "active_ips": [], "count": 0}

    def usage(self, key_id: str) -> dict[str, Any]:
        key = self.find(key_id)
        reset_at = key.get("quota", {}).get("traffic_reset_at")
        events = [
            event
            for event in self.store.load().get("usage_events", [])
            if event["key_id"] == key_id and (reset_at is None or event["timestamp"] > reset_at)
        ]
        sources: dict[str, dict[str, int]] = {}
        for event in events:
            source = sources.setdefault(event["source_id"], {"upload_bytes": 0, "download_bytes": 0})
            source["upload_bytes"] += event["upload_bytes"]
            source["download_bytes"] += event["download_bytes"]
        return {"key_id": key_id, "quota": key["quota"], "sources": sources}

    def user_usage(self, user_id: str) -> dict[str, Any]:
        keys = self.list(user_id)
        return {
            "user_id": user_id,
            "upload_bytes": sum(int(key.get("quota", {}).get("used_upload_bytes", 0)) for key in keys),
            "download_bytes": sum(int(key.get("quota", {}).get("used_download_bytes", 0)) for key in keys),
            "total_bytes": sum(int(key.get("quota", {}).get("used_total_bytes", 0)) for key in keys),
            "keys": len(keys),
        }
