from __future__ import annotations

import copy
import json
import os
import re
import shutil
import socket
import secrets
import ssl
import subprocess
import tempfile
import uuid
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .reality import REALITY_FINGERPRINTS, RealityError, scan_target
from .services import control_service, service_status
from .store import Store
from .sync_queue import enqueue_node_operation
from .subscription_server import tls_context

MASK = "***"
SECRET_KEYS = {
    "id", "password", "privatekey", "private_key", "secret", "token",
    "shortids", "shortid", "publickey", "spiderx",
}
CRITICAL_PATHS = {
    "port", "protocol", "listen", "streamSettings.network", "streamSettings.security",
    "streamSettings.wsSettings.path", "streamSettings.xhttpSettings.path",
    "streamSettings.realitySettings.serverNames", "streamSettings.realitySettings.target",
    "streamSettings.tlsSettings.serverName", "settings.clients",
    "clientSettings.fingerprint",
}
SUPPORTED_PROTOCOLS = {"vless", "vmess", "trojan", "shadowsocks", "dokodemo-door", "http", "socks"}
SUPPORTED_NETWORKS = {"tcp", "raw", "ws", "grpc", "httpupgrade", "xhttp", "kcp", "quic"}
SUPPORTED_SECURITY = {"none", "tls", "reality"}


class InboundError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(value: Any, key: str = "") -> Any:
    folded = key.replace("-", "").replace("_", "").casefold()
    if folded in SECRET_KEYS:
        if isinstance(value, list):
            return [MASK for _ in value]
        return MASK
    if isinstance(value, dict):
        return {item_key: _redact(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _preserve_masked(old: Any, new: Any, path: str = "") -> Any:
    if new == MASK:
        return copy.deepcopy(old)
    if isinstance(old, dict) and isinstance(new, dict):
        merged = copy.deepcopy(old)
        for key, value in new.items():
            child_path = "%s.%s" % (path, key) if path else key
            merged[key] = _preserve_masked(old.get(key), value, child_path)
        return merged
    if isinstance(old, list) and isinstance(new, list):
        result = []
        for index, value in enumerate(new):
            previous = old[index] if index < len(old) else None
            result.append(_preserve_masked(previous, value, path))
        if path == "settings.clients":
            identities = {
                (value.get("email"), value.get("id")) for value in result if isinstance(value, dict)
            }
            for previous in old:
                if not isinstance(previous, dict):
                    continue
                identity = (previous.get("email"), previous.get("id"))
                if identity not in identities and not any(
                    isinstance(value, dict)
                    and (value.get("email") == previous.get("email")
                         or value.get("id") == previous.get("id"))
                    for value in result
                ):
                    result.append(copy.deepcopy(previous))
        return result
    return copy.deepcopy(new)


def _paths(old: Any, new: Any, prefix: str = "") -> list[dict[str, Any]]:
    if isinstance(old, dict) and isinstance(new, dict):
        changes = []
        for key in sorted(set(old) | set(new)):
            path = "%s.%s" % (prefix, key) if prefix else key
            changes.extend(_paths(old.get(key), new.get(key), path))
        return changes
    if old != new:
        key = prefix.rsplit(".", 1)[-1]
        return [{"path": prefix, "old": _redact(old, key), "new": _redact(new, key)}]
    return []


def _validate(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise InboundError("inbound_invalid_config")
    protocol = config.get("protocol")
    port = config.get("port")
    listen = config.get("listen", "0.0.0.0")
    if protocol not in SUPPORTED_PROTOCOLS:
        raise InboundError("inbound_invalid_protocol")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise InboundError("inbound_invalid_port")
    try:
        socket.inet_pton(socket.AF_INET6 if ":" in listen else socket.AF_INET, listen)
    except (OSError, TypeError):
        if listen not in {"localhost"}:
            raise InboundError("inbound_invalid_listen")
    stream = config.setdefault("streamSettings", {})
    network = stream.get("network", "tcp")
    security = stream.get("security", "none")
    if network not in SUPPORTED_NETWORKS:
        raise InboundError("inbound_invalid_transport")
    if security not in SUPPORTED_SECURITY:
        raise InboundError("inbound_invalid_security")
    if security == "reality":
        reality = stream.get("realitySettings")
        if not isinstance(reality, dict) or not reality.get("target") or not reality.get("serverNames"):
            raise InboundError("inbound_invalid_reality")
    if security == "tls":
        certificates = stream.get("tlsSettings", {}).get("certificates", [])
        if not certificates:
            raise InboundError("inbound_certificate_required")
        for certificate in certificates:
            for field in ("certificateFile", "keyFile"):
                if not isinstance(certificate.get(field), str) or not certificate[field]:
                    raise InboundError("inbound_certificate_required")
    sniffing = config.get("sniffing")
    if sniffing is not None and not isinstance(sniffing, dict):
        raise InboundError("inbound_invalid_sniffing")


def _target(reality: dict[str, Any]) -> tuple[str, int]:
    value = str(reality["target"])
    if value.startswith("[") and "]:" in value:
        host, port = value[1:].rsplit("]:", 1)
        return host, int(port)
    if value.count(":") == 1 and value.rsplit(":", 1)[1].isdigit():
        host, port = value.rsplit(":", 1)
        return host, int(port)
    return value, 443


def _validate_binary(binary: str, document: dict[str, Any], directory: Path) -> None:
    descriptor, filename = tempfile.mkstemp(prefix=".inbound-validate-", suffix=".json", dir=directory)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False)
        process = subprocess.run([binary, "run", "-test", "-format", "json", "-c", filename],
                                 capture_output=True, text=True, timeout=20, check=False)
        if process.returncode != 0:
            raise InboundError("inbound_core_validation_failed")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InboundError("inbound_core_validation_failed") from error
    finally:
        Path(filename).unlink(missing_ok=True)


def _listener_healthy(config: dict[str, Any]) -> bool:
    network = config.get("streamSettings", {}).get("network", "tcp")
    if network in {"kcp", "quic"}:
        return True  # UDP has no portable connect-time listener acknowledgement.
    listen = config.get("listen", "0.0.0.0")
    host = "::1" if ":" in listen else "127.0.0.1"
    if listen not in {"0.0.0.0", "::"}:
        host = listen
    try:
        with socket.create_connection((host, config["port"]), timeout=2):
            return True
    except OSError:
        return False


def _hot_add_users(binary: str, endpoint: str, tag: str, clients: list[dict[str, Any]]) -> None:
    for client in clients:
        descriptor, filename = tempfile.mkstemp(prefix="xraydo-api-user-", suffix=".json")
        try:
            payload = {"inboundTag": tag, "user": copy.deepcopy(client)}
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            process = subprocess.run([binary, "api", "adu", "--server=" + endpoint, filename],
                                     capture_output=True, text=True, timeout=10, check=False)
            if process.returncode != 0:
                raise InboundError("inbound_hot_reload_failed")
        except (OSError, subprocess.TimeoutExpired) as error:
            raise InboundError("inbound_hot_reload_failed") from error
        finally:
            Path(filename).unlink(missing_ok=True)


def load_document(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        raise InboundError("inbound_editor_read_failed") from error
    if path.suffix.casefold() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as error:
            raise InboundError("inbound_yaml_unavailable") from error
        value = yaml.safe_load(text)
    else:
        try:
            value = json.loads(text)
        except ValueError as error:
            raise InboundError("inbound_invalid_config") from error
    if not isinstance(value, dict):
        raise InboundError("inbound_invalid_config")
    return value


def apply_assignments(config: dict[str, Any], assignments: list[str]) -> dict[str, Any]:
    result = copy.deepcopy(config)
    for assignment in assignments:
        path, separator, raw = assignment.partition("=")
        parts = [part for part in path.split(".") if part]
        if not separator or not parts:
            raise InboundError("inbound_invalid_assignment")
        try:
            value = json.loads(raw)
        except ValueError:
            value = raw
        target = result
        for part in parts[:-1]:
            child = target.setdefault(part, {})
            if not isinstance(child, dict):
                raise InboundError("inbound_invalid_assignment")
            target = child
        target[parts[-1]] = value
    return result


class InboundService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def list(self, *, node_id: Optional[str] = None, core: Optional[str] = None,
             protocol: Optional[str] = None, port: Optional[int] = None,
             state: Optional[str] = None, remark: Optional[str] = None) -> list[dict[str, Any]]:
        items = self.store.load().get("inbounds", [])
        filtered = [item for item in items
                    if (node_id is None or item["node_id"] == node_id)
                    and (core is None or item["core"] == core)
                    and (protocol is None or item["config"].get("protocol") == protocol)
                    and (port is None or item["config"].get("port") == port)
                    and (state is None or item["state"] == state)
                    and (remark is None or remark.casefold() in item["remark"].casefold())]
        return [self._summary(item) for item in sorted(filtered, key=lambda item: (item["node_id"], item["remark"], item["id"]))]

    @staticmethod
    def _summary(item: dict[str, Any]) -> dict[str, Any]:
        stream = item["config"].get("streamSettings", {})
        return {key: item.get(key) for key in ("id", "node_id", "core", "remark", "state", "revision", "updated_at")} | {
            "protocol": item["config"].get("protocol"), "listen": item["config"].get("listen", "0.0.0.0"),
            "port": item["config"].get("port"), "transport": stream.get("network", "tcp"),
            "security": stream.get("security", "none"),
        }

    def find(self, inbound_id: str, node_id: Optional[str] = None, *, include_deleted: bool = False) -> dict[str, Any]:
        matches = [item for item in self.store.load().get("inbounds", [])
                   if item["id"] == inbound_id and (node_id is None or item["node_id"] == node_id)
                   and (include_deleted or item["state"] != "deleted")]
        if not matches:
            raise InboundError("inbound_not_found")
        if len(matches) != 1:
            raise InboundError("inbound_ambiguous")
        return matches[0]

    def register(self, node_id: str, config: dict[str, Any], *, core: str = "xray",
                 remark: Optional[str] = None, inbound_id: Optional[str] = None,
                 config_index: Optional[int] = None, api_endpoint: Optional[str] = None) -> dict[str, Any]:
        _validate(config)
        data = self.store.load()
        identifier = inbound_id or str(uuid.uuid4())
        if any(item["id"] == identifier and item["node_id"] == node_id for item in data.get("inbounds", [])):
            raise InboundError("inbound_duplicate")
        self._check_conflict(data, node_id, config, None)
        timestamp = _now()
        item = {"id": identifier, "node_id": node_id, "core": core,
                "remark": (remark or config.get("tag") or identifier).strip(), "state": "enabled",
                "revision": 1, "config": copy.deepcopy(config), "created_at": timestamp,
                "updated_at": timestamp, "deleted_at": None, "purge_after": None,
                "config_index": config_index,
                "api_endpoint": api_endpoint,
                "client_settings": ({"fingerprint": "firefox"}
                                    if config.get("streamSettings", {}).get("security") == "reality" else {}),
                "last_apply": {"state": "registered", "at": timestamp}}
        data.setdefault("inbounds", []).append(item)
        self._snapshot(data, item, "register", "working")
        self.store.save(data)
        return self.show(identifier, node_id)

    def guided_wss_tls(
        self, *, node_id: str, port: int, public_address: str,
        sni: list[str], http_host: str, certificate: dict[str, str],
        clients: Optional[list[dict[str, Any]]] = None, path: Optional[str] = None,
        alpn: Optional[list[str]] = None, listen: str = "0.0.0.0",
        remark: str = "WSS/TLS", register: bool = True,
    ) -> dict[str, Any]:
        """Build a safe WebSocket/TLS inbound while keeping client/server names separate."""
        if not 1 <= port <= 65535:
            raise InboundError("inbound_invalid_port")
        if not public_address.strip() or any(ord(char) < 33 for char in public_address):
            raise InboundError("inbound_invalid_public_address")
        names = sorted({value.strip().rstrip(".").lower() for value in sni if value.strip()})
        if not names or any("/" in value or len(value) > 253 for value in names):
            raise InboundError("inbound_invalid_sni")
        host = http_host.strip()
        if not host or any(char in host for char in "\r\n/ "):
            raise InboundError("inbound_invalid_http_host")
        cert = Path(certificate.get("certificateFile", ""))
        key = Path(certificate.get("keyFile", ""))
        for name in names:
            try:
                tls_context(cert, key, name)
            except ValueError as error:
                raise InboundError("inbound_certificate_san_mismatch") from error
        selected_alpn = alpn or ["http/1.1"]
        if not selected_alpn or any(value not in {"http/1.1", "h2"} for value in selected_alpn):
            raise InboundError("inbound_invalid_alpn")
        if path is None:
            path = "/" + secrets.token_urlsafe(18).rstrip("=")
        if (not path.startswith("/") or len(path) > 256 or "?" in path or "#" in path
                or any(ord(char) < 33 or ord(char) > 126 for char in path)):
            raise InboundError("inbound_invalid_path")
        config = {
            "listen": listen, "port": port, "protocol": "vless", "tag": remark,
            "settings": {"clients": copy.deepcopy(clients or []), "decryption": "none"},
            "streamSettings": {
                "network": "ws", "security": "tls",
                "tlsSettings": {"serverName": names[0], "alpn": selected_alpn,
                                "certificates": [{"certificateFile": str(cert),
                                                  "keyFile": str(key)}]},
                "wsSettings": {"path": path, "headers": {"Host": host}},
            },
        }
        result: dict[str, Any] = {"config": config, "client": {
            "address": public_address, "port": port, "sni": names[0],
            "host": host, "path": path, "alpn": selected_alpn,
        }}
        if register:
            created = self.register(node_id, config, remark=remark)
            data = self.store.load()
            item = next(value for value in data["inbounds"]
                        if value["id"] == created["id"] and value["node_id"] == node_id)
            item["client_settings"] = copy.deepcopy(result["client"])
            self.store.save(data)
            result["inbound"] = self.show(created["id"], node_id)
        return result

    def probe_port(self, node_id: str, *, listen: str, port: int,
                   transport: str = "tcp", current_id: Optional[str] = None) -> dict[str, Any]:
        if transport not in {"tcp", "udp"} or not 1 <= port <= 65535:
            raise InboundError("inbound_invalid_port_probe")
        data = self.store.load()
        def address_conflicts(existing: str) -> bool:
            wildcard = {"0.0.0.0", "::", "[::]", ""}
            same_family = (":" in existing) == (":" in listen)
            return same_family and (existing == listen or existing in wildcard or listen in wildcard)

        conflicts = [item for item in data.get("inbounds", [])
                     if item["node_id"] == node_id and item.get("state") != "deleted"
                     and item["id"] != current_id
                     and address_conflicts(item["config"].get("listen", "0.0.0.0"))
                     and item["config"].get("port") == port
                     and ((item["config"].get("streamSettings", {}).get("network")
                           in {"kcp", "quic"}) == (transport == "udp"))]
        if conflicts:
            return {"node_id": node_id, "listen": listen, "port": port,
                    "transport": transport, "state": "managed_conflict",
                    "inbound_id": conflicts[0]["id"]}
        if node_id != "local":
            operation = enqueue_node_operation(data, node_id, "port_probe", "probe", {
                "listen": listen, "port": port, "transport": transport,
            }, resource_id=f"{transport}:{listen}:{port}")
            self.store.save(data)
            return {"node_id": node_id, "listen": listen, "port": port,
                    "transport": transport, "state": "pending_node_execution",
                    "operation_id": operation["id"]}
        family = socket.AF_INET6 if ":" in listen else socket.AF_INET
        kind = socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM
        try:
            with socket.socket(family, kind) as probe:
                probe.bind((listen, port))
        except OSError:
            return {"node_id": node_id, "listen": listen, "port": port,
                    "transport": transport, "state": "external_process"}
        return {"node_id": node_id, "listen": listen, "port": port,
                "transport": transport, "state": "available"}

    def change_port(self, inbound_id: str, port: int, *, node_id: Optional[str] = None,
                    apply: bool = False, binary: Optional[str] = None,
                    config_path: Optional[Path] = None) -> dict[str, Any]:
        item = self.find(inbound_id, node_id)
        if not 1 <= port <= 65535:
            raise InboundError("inbound_invalid_port")
        if item["config"].get("port") == port:
            return {"state": "unchanged", "inbound_id": item["id"],
                    "node_id": item["node_id"], "port": port}
        config = copy.deepcopy(item["config"])
        config["port"] = port
        network = config.get("streamSettings", {}).get("network", "tcp")
        transport = "udp" if network in {"kcp", "quic"} else "tcp"
        if not apply:
            return self.edit(item["id"], config, node_id=item["node_id"],
                             expected_revision=item["revision"])
        probe = self.probe_port(item["node_id"], listen=config.get("listen", "0.0.0.0"),
                                port=port, transport=transport, current_id=item["id"])
        if probe["state"] in {"managed_conflict", "external_process"}:
            raise InboundError("inbound_port_unavailable")
        if item["node_id"] != "local":
            data = self.store.load()
            operation = enqueue_node_operation(data, item["node_id"], "inbound", "edit", {
                "inbound_id": item["id"], "expected_revision": item["revision"],
                "config": config, "client_settings": item.get("client_settings", {}),
                "apply_time_validation": True, "health_required": True,
                "rollback_on_failure": True,
            }, resource_id=item["id"])
            operation["depends_on"] = probe["operation_id"]
            self.store.save(data)
            return {"state": "pending_node_execution", "operation_id": operation["id"],
                    "probe_operation_id": probe["operation_id"], "port": port,
                    "node_id": item["node_id"], "inbound_id": item["id"]}
        return self.edit(item["id"], config, node_id=item["node_id"],
                         expected_revision=item["revision"], apply=True,
                         binary=binary, config_path=config_path)

    def change_sni(self, inbound_id: str, sni: str, *, node_id: Optional[str] = None,
                   apply: bool = False, binary: Optional[str] = None,
                   config_path: Optional[Path] = None) -> dict[str, Any]:
        item = self.find(inbound_id, node_id)
        try:
            normalized = sni.strip().rstrip(".").encode("idna").decode().lower()
        except UnicodeError as error:
            raise InboundError("inbound_invalid_sni") from error
        if (not normalized or len(normalized) > 253
                or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                           for label in normalized.split("."))):
            raise InboundError("inbound_invalid_sni")
        config = copy.deepcopy(item["config"])
        stream = config.get("streamSettings", {})
        security = stream.get("security")
        current_sni = ((stream.get("realitySettings", {}).get("serverNames") or [None])[0]
                       if security == "reality" else stream.get("tlsSettings", {}).get("serverName"))
        if current_sni == normalized and item.get("client_settings", {}).get("sni") == normalized:
            return {"state": "unchanged", "inbound_id": item["id"],
                    "node_id": item["node_id"], "sni": normalized}
        if security == "reality":
            stream["realitySettings"]["serverNames"] = [normalized]
        elif security == "tls":
            tls = stream.setdefault("tlsSettings", {})
            tls["serverName"] = normalized
            if item["node_id"] == "local" and apply:
                for certificate in tls.get("certificates", []):
                    try:
                        tls_context(Path(certificate["certificateFile"]),
                                    Path(certificate["keyFile"]), normalized)
                    except (KeyError, ValueError) as error:
                        raise InboundError("inbound_certificate_san_mismatch") from error
        else:
            raise InboundError("inbound_sni_unsupported")
        client_settings = copy.deepcopy(item.get("client_settings", {}))
        client_settings["sni"] = normalized
        if not apply:
            planned = self.edit(item["id"], config, node_id=item["node_id"],
                                expected_revision=item["revision"])
            planned["sni"] = normalized
            return planned
        if item["node_id"] != "local":
            data = self.store.load()
            operation = enqueue_node_operation(data, item["node_id"], "inbound", "edit", {
                "inbound_id": item["id"], "expected_revision": item["revision"],
                "config": config, "client_settings": client_settings,
                "validate_san": normalized if security == "tls" else None,
                "apply_time_validation": True, "health_required": True,
                "rollback_on_failure": True,
            }, resource_id=item["id"])
            self.store.save(data)
            return {"state": "pending_node_execution", "operation_id": operation["id"],
                    "sni": normalized, "node_id": item["node_id"],
                    "inbound_id": item["id"]}
        result = self.edit(item["id"], config, node_id=item["node_id"],
                           expected_revision=item["revision"], apply=True,
                           binary=binary, config_path=config_path)
        data = self.store.load()
        stored = next(value for value in data["inbounds"]
                      if value["id"] == item["id"] and value["node_id"] == item["node_id"])
        stored["client_settings"] = client_settings
        self._update_subscriptions(data, stored)
        self.store.save(data)
        result["sni"] = normalized
        return result

    def rotation_plan(self, inbound_id: str, *, node_id: Optional[str] = None) -> dict[str, Any]:
        item = self.find(inbound_id, node_id)
        reality = item["config"].get("streamSettings", {}).get("realitySettings")
        if not isinstance(reality, dict):
            raise InboundError("inbound_not_reality")
        previous_ids = list(reality.get("shortIds", []))
        previous_spider = item.get("client_settings", {}).get("spider_x", "/")
        return {"rotation_id": str(uuid.uuid4()), "inbound_id": item["id"],
                "node_id": item["node_id"], "base_revision": item["revision"],
                "previous": {"short_ids": previous_ids, "spider_x": previous_spider},
                "proposed": {"short_ids": [secrets.token_hex(8)],
                             "spider_x": "/" + secrets.token_urlsafe(12).rstrip("=")},
                "impact": {"subscription_update_after_ack": True,
                           "connections_may_drop": True}}

    def rotate_reality(self, plan: dict[str, Any], *, confirm: bool = False,
                       binary: Optional[str] = None, config_path: Optional[Path] = None) -> dict[str, Any]:
        if not confirm:
            return {"state": "confirmation_required", "plan": copy.deepcopy(plan)}
        item = self.find(plan["inbound_id"], plan["node_id"])
        if item["revision"] != plan.get("base_revision"):
            raise InboundError("inbound_revision_conflict")
        new_config = copy.deepcopy(item["config"])
        new_config["streamSettings"]["realitySettings"]["shortIds"] = plan["proposed"]["short_ids"]
        client_settings = copy.deepcopy(item.get("client_settings", {}))
        client_settings["spider_x"] = plan["proposed"]["spider_x"]
        if item["node_id"] != "local":
            data = self.store.load()
            operation = enqueue_node_operation(data, item["node_id"], "reality_rotation", "apply", {
                "rotation_id": plan["rotation_id"], "inbound_id": item["id"],
                "base_revision": item["revision"], "config": new_config,
                "client_settings": client_settings, "atomic": True,
                "rollback_on_failure": True, "health_required": True,
            }, resource_id=item["id"])
            self.store.save(data)
            return {"state": "pending_node_execution", "operation_id": operation["id"],
                    "rotation_id": plan["rotation_id"]}
        if not binary or not config_path:
            raise InboundError("inbound_apply_context_required")
        result = self.edit(item["id"], new_config, node_id=item["node_id"],
                           expected_revision=item["revision"], apply=True,
                           binary=binary, config_path=config_path)
        if result.get("state") != "applied":
            raise InboundError("reality_rotation_failed")
        data = self.store.load(); stored = next(value for value in data["inbounds"]
            if value["id"] == item["id"] and value["node_id"] == item["node_id"])
        stored["client_settings"] = client_settings; self._update_subscriptions(data, stored)
        self.store.save(data)
        return {"state": "applied", "rotation_id": plan["rotation_id"], "result": result}

    def show(self, inbound_id: str, node_id: Optional[str] = None, *, secrets: bool = False) -> dict[str, Any]:
        data = self.store.load(); item = self.find(inbound_id, node_id, include_deleted=True)
        client_ids = self._client_ids(item["config"])
        keys = [key for key in data.get("access_keys", []) if key["id"] in client_ids]
        users_by_id = {user["id"]: user for user in data.get("users", [])}
        associated = [{"access_key_id": key["id"], "user_id": key["user_id"],
                       "user": users_by_id.get(key["user_id"], {}).get("name"), "state": key["state"]} for key in keys]
        result = self._summary(item)
        result.update({"config": copy.deepcopy(item["config"]) if secrets else _redact(item["config"]),
                       "client_settings": copy.deepcopy(item.get("client_settings", {})),
                       "associated_users": associated, "last_apply": item.get("last_apply"),
                       "deleted_at": item.get("deleted_at"), "purge_after": item.get("purge_after")})
        return result

    @staticmethod
    def _client_ids(config: dict[str, Any]) -> set[str]:
        result = set()
        for client in config.get("settings", {}).get("clients", []):
            email = client.get("email", "")
            if isinstance(email, str) and email.startswith("access-key:"):
                result.add(email.split(":", 1)[1])
        return result

    @staticmethod
    def _check_conflict(data: dict[str, Any], node_id: str, config: dict[str, Any], current_id: Optional[str]) -> None:
        listen, port = config.get("listen", "0.0.0.0"), config["port"]
        transport = config.get("streamSettings", {}).get("network", "tcp")
        for item in data.get("inbounds", []):
            other = item["config"]
            if item["node_id"] != node_id or item["id"] == current_id or item["state"] == "deleted":
                continue
            if other.get("port") == port and other.get("listen", "0.0.0.0") == listen \
                    and other.get("streamSettings", {}).get("network", "tcp") == transport:
                raise InboundError("inbound_port_conflict")

    @staticmethod
    def _snapshot(data: dict[str, Any], item: dict[str, Any], action: str, status: str) -> dict[str, Any]:
        snapshot = {"id": str(uuid.uuid4()), "inbound_id": item["id"], "node_id": item["node_id"],
                    "revision": item["revision"], "action": action, "status": status,
                    "config": copy.deepcopy(item["config"]), "remark": item["remark"],
                    "client_settings": copy.deepcopy(item.get("client_settings", {})),
                    "state": item["state"], "created_at": _now()}
        data.setdefault("inbound_versions", []).append(snapshot)
        return snapshot

    def plan(self, inbound_id: str, new_config: dict[str, Any], *, node_id: Optional[str] = None,
             expected_revision: Optional[int] = None, remark: Optional[str] = None,
             fingerprint: Optional[str] = None) -> dict[str, Any]:
        data = self.store.load(); item = self.find(inbound_id, node_id)
        if expected_revision is not None and item["revision"] != expected_revision:
            raise InboundError("inbound_revision_conflict")
        merged = _preserve_masked(item["config"], new_config)
        _validate(merged); self._check_conflict(data, item["node_id"], merged, item["id"])
        changes = _paths(item["config"], merged)
        client_settings = copy.deepcopy(item.get("client_settings", {}))
        if fingerprint is not None:
            if fingerprint not in REALITY_FINGERPRINTS:
                raise InboundError("invalid_fingerprint")
            previous_fingerprint = client_settings.get("fingerprint")
            client_settings["fingerprint"] = fingerprint
            if previous_fingerprint != fingerprint:
                changes.append({"path": "clientSettings.fingerprint",
                                "old": previous_fingerprint, "new": fingerprint})
        changed_paths = {change["path"] for change in changes}
        breaking = sorted(path for path in changed_paths if any(path == critical or path.startswith(critical + ".") for critical in CRITICAL_PATHS))
        client_ids = self._client_ids(item["config"]) | self._client_ids(merged)
        endpoint_count = sum(1 for key in data.get("access_keys", []) for endpoint in key.get("endpoints", {}).values()
                             if endpoint.get("inbound_id") == item["id"] and endpoint.get("node_id", "local") == item["node_id"])
        only_clients = bool(changes) and all(change["path"].startswith("settings.clients") for change in changes)
        subscription_only = bool(changes) and all(change["path"].startswith("clientSettings.") for change in changes)
        old_clients = item["config"].get("settings", {}).get("clients", [])
        new_clients = merged.get("settings", {}).get("clients", [])
        old_serialized = {json.dumps(client, sort_keys=True) for client in old_clients}
        additions_only = old_serialized.issubset({json.dumps(client, sort_keys=True) for client in new_clients})
        hot_reload = bool(only_clients and additions_only and item["core"] == "xray" and item.get("api_endpoint"))
        return {"inbound_id": item["id"], "node_id": item["node_id"], "base_revision": item["revision"],
                "remark": remark if remark is not None else item["remark"], "config": merged,
                "diff": changes, "impact": {"nodes": [item["node_id"]], "access_keys": sorted(client_ids),
                    "users": len(client_ids), "subscription_links": endpoint_count,
                    "restart_required": not hot_reload and not subscription_only, "hot_reload_supported": hot_reload,
                    "subscription_only": subscription_only,
                    "connections_may_drop": not hot_reload and not subscription_only,
                    "breaking_client_fields": breaking},
                "client_settings": client_settings,
                "warnings": (["existing client configurations will stop working"] if breaking else [])}

    def _check_reality(self, config: dict[str, Any], node_id: str) -> Optional[dict[str, Any]]:
        stream = config.get("streamSettings", {})
        if stream.get("security") != "reality":
            return None
        settings = stream["realitySettings"]
        host, port = _target(settings)
        sni = settings["serverNames"][0]
        return scan_target(host, port, sni=sni, xver=int(settings.get("xver", 0)), node_id=node_id)

    @staticmethod
    def _check_local_resources(old: dict[str, Any], new: dict[str, Any]) -> None:
        stream = new.get("streamSettings", {})
        if stream.get("security") == "tls":
            for certificate in stream.get("tlsSettings", {}).get("certificates", []):
                cert, key = Path(certificate["certificateFile"]), Path(certificate["keyFile"])
                if not cert.is_file() or not key.is_file():
                    raise InboundError("inbound_certificate_missing")
                if key.stat().st_mode & 0o077:
                    raise InboundError("inbound_certificate_permissions")
                try:
                    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                    context.load_cert_chain(str(cert), str(key))
                except (OSError, ssl.SSLError) as error:
                    raise InboundError("inbound_certificate_invalid") from error
        old_socket = (old.get("listen", "0.0.0.0"), old.get("port"), old.get("streamSettings", {}).get("network", "tcp"))
        new_socket = (new.get("listen", "0.0.0.0"), new.get("port"), stream.get("network", "tcp"))
        if old_socket == new_socket:
            return
        family = socket.AF_INET6 if ":" in new_socket[0] else socket.AF_INET
        kind = socket.SOCK_DGRAM if new_socket[2] in {"kcp", "quic"} else socket.SOCK_STREAM
        try:
            with socket.socket(family, kind) as listener:
                listener.bind((new_socket[0], new_socket[1]))
        except OSError as error:
            raise InboundError("inbound_port_unavailable") from error

    def edit(self, inbound_id: str, new_config: dict[str, Any], *, node_id: Optional[str] = None,
             expected_revision: Optional[int] = None, remark: Optional[str] = None,
             fingerprint: Optional[str] = None,
             apply: bool = False, binary: Optional[str] = None, config_path: Optional[Path] = None,
             restart: Callable[[str, str], dict[str, str]] = control_service,
             health: Callable[[str], dict[str, Any]] = service_status,
             listener_health: Callable[[dict[str, Any]], bool] = _listener_healthy,
             hot_reload: Callable[[str, str, str, list[dict[str, Any]]], None] = _hot_add_users) -> dict[str, Any]:
        plan = self.plan(inbound_id, new_config, node_id=node_id,
                         expected_revision=expected_revision, remark=remark,
                         fingerprint=fingerprint)
        item = self.find(inbound_id, node_id)
        if item["node_id"] != "local" and not apply:
            return {"state": "planned", "plan": self._public_plan(plan), "reality_check": None}
        if item["node_id"] != "local":
            data = self.store.load()
            operation = enqueue_node_operation(data, item["node_id"], "inbound", "edit", {
                "inbound_id": item["id"], "expected_revision": plan["base_revision"],
                "remark": plan["remark"], "config": plan["config"], "apply_time_validation": True,
                "client_settings": plan["client_settings"],
            }, resource_id=item["id"])
            self.store.save(data)
            return {"state": "pending_node_execution", "plan": self._public_plan(plan), "operation_id": operation["id"]}
        reality_check = self._check_reality(plan["config"], item["node_id"])
        if not apply:
            return {"state": "planned", "plan": self._public_plan(plan), "reality_check": reality_check}
        if plan["impact"].get("subscription_only"):
            return self._apply_subscription_only(item, plan)
        if not binary or not config_path:
            raise InboundError("inbound_apply_context_required")
        self._check_local_resources(item["config"], plan["config"])
        return self._apply_local(item, plan, binary, config_path, restart, health,
                                 listener_health, hot_reload, reality_check)

    @staticmethod
    def _public_plan(plan: dict[str, Any]) -> dict[str, Any]:
        result = {key: copy.deepcopy(value) for key, value in plan.items() if key != "config"}
        result["config"] = _redact(plan["config"])
        return result

    def _apply_subscription_only(self, original: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
        data = self.store.load()
        item = next(value for value in data["inbounds"]
                    if value["id"] == original["id"] and value["node_id"] == original["node_id"])
        if item["revision"] != plan["base_revision"]:
            raise InboundError("inbound_revision_conflict")
        self._snapshot(data, item, "edit.before", "working")
        item["client_settings"] = copy.deepcopy(plan["client_settings"])
        item["revision"] += 1; item["updated_at"] = _now()
        item["last_apply"] = {"state": "applied", "at": item["updated_at"],
                              "method": "subscription-only", "health": "not-required"}
        self._snapshot(data, item, "edit.after", "working")
        self._update_subscriptions(data, item)
        self._audit(data, item, "inbound.edit", [change["path"] for change in plan["diff"]])
        self.store.save(data)
        return {"state": "applied", "inbound": self.show(item["id"], item["node_id"]),
                "plan": self._public_plan(plan), "method": "subscription-only",
                "rollback_performed": False}

    def _apply_local(self, original: dict[str, Any], plan: dict[str, Any], binary: str, path: Path,
                     restart: Callable[[str, str], dict[str, str]], health: Callable[[str], dict[str, Any]],
                     listener_health: Callable[[dict[str, Any]], bool],
                     hot_reload: Callable[[str, str, str, list[dict[str, Any]]], None],
                     reality_check: Optional[dict[str, Any]]) -> dict[str, Any]:
        if self.find(original["id"], original["node_id"])["revision"] != plan["base_revision"]:
            raise InboundError("inbound_revision_conflict")
        try:
            document = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"inbounds": [], "outbounds": [{"protocol": "freedom"}]}
        except (OSError, ValueError) as error:
            raise InboundError("inbound_config_read_failed") from error
        inbounds = document.setdefault("inbounds", [])
        stored_index = original.get("config_index")
        index = stored_index if isinstance(stored_index, int) and 0 <= stored_index < len(inbounds) else None
        if index is None:
            matches = [position for position, value in enumerate(inbounds)
                       if value.get("tag") == original["config"].get("tag")]
            if len(matches) > 1:
                raise InboundError("inbound_ambiguous")
            index = matches[0] if matches else None
        applied_config = copy.deepcopy(plan["config"])
        if index is None:
            inbounds.append(applied_config)
        else:
            inbounds[index] = applied_config
        path.parent.mkdir(parents=True, exist_ok=True)
        _validate_binary(binary, document, path.parent)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".xray-config-", suffix=".json", dir=path.parent)
        temporary, backup = Path(temporary_name), path.with_suffix(path.suffix + ".inbound-previous")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            os.chmod(temporary, 0o640)
            if path.exists():
                shutil.copy2(path, backup)
            os.replace(temporary, path)
            method = "hot-reload" if plan["impact"]["hot_reload_supported"] else "restart"
            if method == "hot-reload":
                old_clients = original["config"].get("settings", {}).get("clients", [])
                old_values = {json.dumps(client, sort_keys=True) for client in old_clients}
                additions = [client for client in plan["config"].get("settings", {}).get("clients", [])
                             if json.dumps(client, sort_keys=True) not in old_values]
                hot_reload(binary, original["api_endpoint"], plan["config"].get("tag", ""), additions)
            else:
                restart(original["core"], "restart")
            status = health(original["core"])
            if status.get("state") != "active" or not listener_health(plan["config"]):
                raise InboundError("inbound_health_failed")
        except Exception:
            temporary.unlink(missing_ok=True)
            if backup.exists():
                shutil.copy2(backup, path)
                try:
                    restart(original["core"], "restart")
                except ValueError:
                    pass
            raise
        data = self.store.load()
        item = next(value for value in data["inbounds"] if value["id"] == original["id"] and value["node_id"] == original["node_id"])
        if item["revision"] != plan["base_revision"]:
            if backup.exists():
                shutil.copy2(backup, path)
                restart(original["core"], "restart")
            raise InboundError("inbound_revision_conflict")
        self._snapshot(data, item, "edit.before", "working")
        item["config"], item["remark"] = copy.deepcopy(plan["config"]), plan["remark"]
        item["client_settings"] = copy.deepcopy(plan["client_settings"])
        item["config_index"] = index if index is not None else len(inbounds) - 1
        item["revision"] += 1; item["updated_at"] = _now()
        item["last_apply"] = {"state": "applied", "at": item["updated_at"], "method": method,
                              "health": "active", "listener": "reachable", "reality_check": reality_check}
        self._snapshot(data, item, "edit.after", "working")
        self._update_subscriptions(data, item)
        self._audit(data, item, "inbound.edit", [change["path"] for change in plan["diff"]])
        self.store.save(data)
        return {"state": "applied", "inbound": self.show(item["id"], item["node_id"]),
                "plan": self._public_plan(plan), "method": method, "rollback_performed": False}

    @staticmethod
    def _update_subscriptions(data: dict[str, Any], item: dict[str, Any]) -> None:
        for key in data.get("access_keys", []):
            for endpoint in key.get("endpoints", {}).values():
                if endpoint.get("inbound_id") == item["id"] and endpoint.get("node_id", "local") == item["node_id"]:
                    endpoint["inbound_revision"] = item["revision"]
                    endpoint["enabled"] = item["state"] == "enabled"
                    public_port = item.get("config", {}).get("port")
                    uri = endpoint.get("uri")
                    if isinstance(public_port, int):
                        endpoint["port"] = public_port
                        if isinstance(uri, str):
                            parsed_port = urllib.parse.urlsplit(uri)
                            if parsed_port.hostname:
                                credentials = (parsed_port.netloc.rsplit("@", 1)[0] + "@"
                                               if "@" in parsed_port.netloc else "")
                                host = (f"[{parsed_port.hostname}]" if ":" in parsed_port.hostname
                                        else parsed_port.hostname)
                                uri = urllib.parse.urlunsplit(
                                    parsed_port._replace(netloc=f"{credentials}{host}:{public_port}"))
                                endpoint["uri"] = uri
                    fingerprint = item.get("client_settings", {}).get("fingerprint")
                    spider_x = item.get("client_settings", {}).get("spider_x")
                    client_sni = item.get("client_settings", {}).get("sni")
                    short_ids = item.get("config", {}).get("streamSettings", {}).get(
                        "realitySettings", {}).get("shortIds", [])
                    if fingerprint or spider_x or short_ids or client_sni:
                        endpoint["fingerprint"] = fingerprint
                        uri = endpoint.get("uri")
                        if isinstance(uri, str):
                            parsed = urllib.parse.urlsplit(uri)
                            query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
                            replacements = {"fp": fingerprint, "spx": spider_x,
                                            "sid": short_ids[0] if short_ids else None,
                                            "sni": client_sni}
                            query = [(name, replacements.get(name) or value) for name, value in query]
                            if fingerprint and not any(name == "fp" for name, _value in query):
                                query.append(("fp", fingerprint))
                            if spider_x and not any(name == "spx" for name, _value in query):
                                query.append(("spx", spider_x))
                            if short_ids and not any(name == "sid" for name, _value in query):
                                query.append(("sid", short_ids[0]))
                            if client_sni and not any(name == "sni" for name, _value in query):
                                query.append(("sni", client_sni))
                            endpoint["uri"] = urllib.parse.urlunsplit(
                                parsed._replace(query=urllib.parse.urlencode(query))
                            )
                    key["updated_at"] = _now()

    @staticmethod
    def _audit(data: dict[str, Any], item: dict[str, Any], action: str, fields: list[str]) -> None:
        data.setdefault("audit_log", []).append({"id": str(uuid.uuid4()), "timestamp": _now(),
            "actor": os.environ.get("XRAYDO_ACTOR") or os.environ.get("USER") or "local-admin",
            "action": action, "object_type": "inbound", "object_id": "%s:%s" % (item["node_id"], item["id"]),
            "fields": sorted(set(fields))})

    def set_state(self, inbound_id: str, state: str, *, node_id: Optional[str] = None,
                  expected_revision: Optional[int] = None, grace_seconds: int = 86400,
                  apply: bool = False, binary: Optional[str] = None,
                  config_path: Optional[Path] = None,
                  restart: Callable[[str, str], dict[str, str]] = control_service,
                  health: Callable[[str], dict[str, Any]] = service_status,
                  listener_health: Callable[[dict[str, Any]], bool] = _listener_healthy) -> dict[str, Any]:
        if state not in {"enabled", "disabled", "deleted"} or not 60 <= grace_seconds <= 2_592_000:
            raise InboundError("inbound_invalid_state")
        data = self.store.load(); target = self.find(inbound_id, node_id, include_deleted=True)
        item = next(value for value in data["inbounds"] if value["id"] == target["id"] and value["node_id"] == target["node_id"])
        if expected_revision is not None and item["revision"] != expected_revision:
            raise InboundError("inbound_revision_conflict")
        if item["node_id"] != "local":
            operation = enqueue_node_operation(data, item["node_id"], "inbound", state, {
                "inbound_id": item["id"], "expected_revision": item["revision"],
                "desired_state": state, "grace_seconds": grace_seconds,
            }, resource_id=item["id"])
            self.store.save(data)
            return {"id": item["id"], "node_id": item["node_id"],
                    "state": "pending_node_execution", "desired_state": state,
                    "operation_id": operation["id"], "revision": item["revision"]}
        if apply:
            if not binary or not config_path:
                raise InboundError("inbound_apply_context_required")
            self._apply_local_state(item, state, binary, config_path, restart, health, listener_health)
        self._snapshot(data, item, "state.before", "working")
        item["state"] = state; item["revision"] += 1; item["updated_at"] = _now()
        if state == "deleted":
            item["deleted_at"] = item["updated_at"]
            item["purge_after"] = (datetime.now(timezone.utc) + timedelta(seconds=grace_seconds)).isoformat()
        else:
            item["deleted_at"] = item["purge_after"] = None
        self._update_subscriptions(data, item); self._snapshot(data, item, "state.after", "working")
        self._audit(data, item, "inbound.%s" % state, ["state"]); self.store.save(data)
        return self.show(item["id"], item["node_id"])

    @staticmethod
    def _apply_local_state(item: dict[str, Any], desired_state: str, binary: str, path: Path,
                           restart: Callable[[str, str], dict[str, str]],
                           health: Callable[[str], dict[str, Any]],
                           listener_health: Callable[[dict[str, Any]], bool]) -> None:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise InboundError("inbound_config_read_failed") from error
        inbounds = document.setdefault("inbounds", [])
        stored_index = item.get("config_index")
        positions = [index for index, value in enumerate(inbounds)
                     if value.get("tag") == item["config"].get("tag")]
        index = stored_index if isinstance(stored_index, int) and 0 <= stored_index < len(inbounds) else None
        if index is None and len(positions) > 1:
            raise InboundError("inbound_ambiguous")
        if index is None and positions:
            index = positions[0]
        if desired_state == "enabled":
            if index is None:
                inbounds.append(copy.deepcopy(item["config"]))
        elif index is not None:
            inbounds.pop(index)
        path.parent.mkdir(parents=True, exist_ok=True)
        _validate_binary(binary, document, path.parent)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".xray-state-", suffix=".json", dir=path.parent)
        temporary, backup = Path(temporary_name), path.with_suffix(path.suffix + ".state-previous")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            os.chmod(temporary, 0o640); shutil.copy2(path, backup); os.replace(temporary, path)
            restart(item["core"], "restart")
            status = health(item["core"])
            listener_ok = desired_state != "enabled" or listener_health(item["config"])
            if status.get("state") != "active" or not listener_ok:
                raise InboundError("inbound_health_failed")
        except Exception:
            temporary.unlink(missing_ok=True)
            if backup.exists():
                shutil.copy2(backup, path)
                try:
                    restart(item["core"], "restart")
                except ValueError:
                    pass
            raise

    def restore(self, inbound_id: str, *, node_id: Optional[str] = None,
                apply: bool = False, binary: Optional[str] = None,
                config_path: Optional[Path] = None) -> dict[str, Any]:
        item = self.find(inbound_id, node_id, include_deleted=True)
        if item["state"] != "deleted" or datetime.fromisoformat(item["purge_after"]) <= datetime.now(timezone.utc):
            raise InboundError("inbound_restore_expired")
        return self.set_state(inbound_id, "disabled", node_id=item["node_id"],
                              expected_revision=item["revision"], apply=apply,
                              binary=binary, config_path=config_path)

    def clone(self, inbound_id: str, target_node_id: str, *, node_id: Optional[str] = None,
              port: Optional[int] = None, remark: Optional[str] = None) -> dict[str, Any]:
        source = self.find(inbound_id, node_id); config = copy.deepcopy(source["config"])
        if port is not None:
            config["port"] = port
        for certificate in config.get("streamSettings", {}).get("tlsSettings", {}).get("certificates", []):
            if target_node_id != source["node_id"] and any(Path(certificate[field]).is_absolute() for field in ("certificateFile", "keyFile")):
                raise InboundError("inbound_clone_certificate_recheck_required")
        return self.register(target_node_id, config, core=source["core"], remark=remark or "%s (clone)" % source["remark"])

    def history(self, inbound_id: str, *, node_id: Optional[str] = None) -> list[dict[str, Any]]:
        item = self.find(inbound_id, node_id, include_deleted=True)
        versions = [value for value in self.store.load().get("inbound_versions", [])
                    if value["inbound_id"] == item["id"] and value["node_id"] == item["node_id"]]
        result = []
        for index, version in enumerate(versions):
            previous = versions[index - 1]["config"] if index else {}
            settings_diff = _paths(
                versions[index - 1].get("client_settings", {}) if index else {},
                version.get("client_settings", {}), "clientSettings"
            )
            result.append({key: version[key] for key in ("id", "revision", "action", "status", "created_at")} | {
                "diff": _paths(previous, version["config"]) + settings_diff,
                "remark": version["remark"], "state": version["state"]})
        return list(reversed(result))

    def rollback(self, inbound_id: str, revision: int, *, node_id: Optional[str] = None,
                 expected_revision: Optional[int] = None, apply: bool = False,
                 binary: Optional[str] = None, config_path: Optional[Path] = None) -> dict[str, Any]:
        item = self.find(inbound_id, node_id, include_deleted=True)
        if expected_revision is not None and item["revision"] != expected_revision:
            raise InboundError("inbound_revision_conflict")
        candidates = [value for value in self.store.load().get("inbound_versions", [])
                      if value["inbound_id"] == item["id"] and value["node_id"] == item["node_id"]
                      and value["revision"] == revision]
        if not candidates:
            raise InboundError("inbound_revision_not_found")
        snapshot = candidates[-1]
        result = self.edit(item["id"], snapshot["config"], node_id=item["node_id"],
                           expected_revision=item["revision"], remark=snapshot["remark"],
                           fingerprint=snapshot.get("client_settings", {}).get("fingerprint"),
                           apply=apply, binary=binary, config_path=config_path)
        result["state"] = "rolled_back" if result["state"] == "applied" else "rollback_planned"
        result["target_revision"] = revision
        return result

    def bulk_plan(self, inbound_ids: list[tuple[str, str]], transform: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        results = []
        for node_id, inbound_id in inbound_ids:
            try:
                item = self.find(inbound_id, node_id)
                plan = self.plan(inbound_id, transform(copy.deepcopy(item["config"])), node_id=node_id,
                                 expected_revision=item["revision"])
                results.append({"node_id": node_id, "inbound_id": inbound_id, "state": "planned", "plan": self._public_plan(plan)})
            except ValueError as error:
                results.append({"node_id": node_id, "inbound_id": inbound_id, "state": "error", "error": str(error)})
        return {"complete": bool(results) and all(item["state"] == "planned" for item in results), "results": results}
