from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .nodes import NodeService
from .access_keys import AccessKeyService
from .services import control_service, core_version, service_logs, service_status
from .store import Store
from .sync_queue import enqueue_node_operation

OFFICIAL_REPOSITORY = "HyNetworks/hysteria"
RELEASE_API = "https://api.github.com/repos/%s/releases" % OFFICIAL_REPOSITORY
SUPPORTED_ARCHITECTURES = {
    "x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64",
    "i386": "386", "i686": "386", "armv7l": "armv7", "riscv64": "riscv64",
    "loongarch64": "loong64", "s390x": "s390x",
}


class HysteriaManagerError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(data: dict[str, Any], action: str, object_type: str, object_id: str,
           fields: list[str]) -> None:
    data.setdefault("audit_log", []).append({"id": str(uuid.uuid4()), "timestamp": _now(),
        "actor": os.environ.get("USER") or "local-admin", "action": "hysteria." + action,
        "object_type": object_type, "object_id": object_id, "fields": fields})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "xraydo/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.HTTPError) as error:
        raise HysteriaManagerError("hysteria_release_metadata_failed") from error


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "xraydo/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except (OSError, urllib.error.HTTPError) as error:
        raise HysteriaManagerError("hysteria_download_failed") from error


def traffic_api(listen: str, secret: str, endpoint: str, *, payload: Optional[Any] = None) -> Any:
    host, separator, raw_port = listen.rpartition(":")
    if not separator or host not in {"127.0.0.1", "localhost", "[::1]"} or not raw_port.isdigit():
        raise HysteriaManagerError("hysteria_stats_not_loopback")
    if endpoint not in {"traffic", "online", "kick", "dump/streams"}:
        raise HysteriaManagerError("hysteria_stats_invalid_endpoint")
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request("http://%s/%s" % (listen, endpoint), data=data,
        method="POST" if endpoint == "kick" else "GET",
        headers={"Authorization": secret, "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.HTTPError) as error:
        raise HysteriaManagerError("hysteria_stats_failed") from error


def platform_capabilities() -> dict[str, Any]:
    machine = platform.machine().casefold()
    architecture = SUPPORTED_ARCHITECTURES.get(machine)
    return {
        "os": platform.system().casefold(), "machine": machine, "architecture": architecture,
        "compatible": platform.system() == "Linux" and architecture is not None,
        "features": {
            "quic": True, "udp": True, "userpass": True, "salamander": True,
            "traffic_stats": True, "acme": True, "ech": True,
        },
    }


def _normalize_version(value: Optional[str]) -> Optional[str]:
    if value is None or value == "latest":
        return None
    value = value.strip()
    if value.startswith("app/"):
        return value
    if value.startswith("v"):
        return "app/" + value
    raise HysteriaManagerError("hysteria_invalid_version")


def release(version: Optional[str] = None) -> dict[str, Any]:
    tag = _normalize_version(version)
    data = _request_json(RELEASE_API + ("/tags/" + tag.replace("/", "%2F") if tag else "/latest"))
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        raise HysteriaManagerError("hysteria_release_metadata_failed")
    capabilities = platform_capabilities()
    if not capabilities["compatible"]:
        raise HysteriaManagerError("hysteria_platform_unsupported")
    name = "hysteria-linux-" + capabilities["architecture"]
    asset = next((item for item in data.get("assets", []) if item.get("name") == name), None)
    if asset is None:
        raise HysteriaManagerError("hysteria_artifact_missing")
    digest = str(asset.get("digest") or "")
    if not digest.startswith("sha256:") or len(digest) != 71:
        raise HysteriaManagerError("hysteria_checksum_missing")
    return {"version": data["tag_name"], "published_at": data.get("published_at"),
            "asset": name, "url": asset["browser_download_url"], "sha256": digest.split(":", 1)[1],
            "source": "https://github.com/%s/releases/tag/%s" % (OFFICIAL_REPOSITORY, data["tag_name"])}


class HysteriaManager:
    def __init__(self, store: Store, cache_dir: Optional[Path] = None) -> None:
        self.store = store
        self.cache_dir = cache_dir or store.data_dir / "artifacts" / "hysteria2"

    def _node_id(self, reference: str) -> str:
        return "local" if reference in {"local", "standalone"} else NodeService(self.store).find(reference)["id"]

    def audit_configuration(self, node_id: str, key_id: str, *, queued: bool) -> None:
        data = self.store.load()
        _audit(data, "config_queued" if queued else "config_applied", "access_key", key_id,
               ["node_id", "auth.userpass", "tls", "quic"])
        self.store.save(data)

    def capabilities(self, reference: str = "local") -> dict[str, Any]:
        node_id = self._node_id(reference)
        data = self.store.load()
        configured = next((item for item in data.get("node_cores", [])
                           if item["node_id"] == node_id and item["core"] == "hysteria2"), None)
        if node_id == "local":
            capabilities = platform_capabilities()
        elif configured:
            capabilities = {
                "os": configured.get("os"),
                "machine": configured.get("machine"),
                "architecture": configured.get("architecture"),
                "compatible": configured.get("compatible", True),
                "features": configured.get("capabilities", {}),
            }
        else:
            capabilities = {"os": None, "machine": None, "architecture": None,
                            "compatible": None, "features": {}}
        return {"node_id": node_id, **capabilities, "installed": bool(configured),
                "core": configured}

    def _record(self, data: dict[str, Any], node_id: str, metadata: dict[str, Any],
                binary: Path, action: str, previous: Optional[str]) -> dict[str, Any]:
        timestamp = _now()
        record = next((item for item in data.setdefault("node_cores", [])
                       if item["node_id"] == node_id and item["core"] == "hysteria2"), None)
        values = {"node_id": node_id, "core": "hysteria2", "enabled": True,
                  "version": metadata["version"], "binary": str(binary), "sha256": metadata["sha256"],
                  "source": metadata["source"], "architecture": platform_capabilities()["architecture"],
                  "capabilities": platform_capabilities()["features"], "updated_at": timestamp}
        if record is None:
            values["created_at"] = timestamp
            data["node_cores"].append(values); record = values
        else:
            record.update(values)
        data.setdefault("core_versions", []).append({"id": str(uuid.uuid4()), "node_id": node_id,
            "core": "hysteria2", "version": metadata["version"], "sha256": metadata["sha256"],
            "source": metadata["source"], "action": action, "previous_version": previous,
            "artifact": str(self._cached_path(metadata)), "created_at": timestamp})
        data.setdefault("audit_log", []).append({"id": str(uuid.uuid4()), "timestamp": timestamp,
            "actor": os.environ.get("USER") or "local-admin", "action": "hysteria.%s" % action,
            "object_type": "node_core", "object_id": "%s:hysteria2" % node_id,
            "fields": ["version", "sha256", "source"]})
        return record

    def _cached_path(self, metadata: dict[str, Any]) -> Path:
        version = metadata["version"].replace("/", "-")
        return self.cache_dir / version / metadata["asset"]

    def _obtain(self, metadata: dict[str, Any]) -> Path:
        target = self._cached_path(metadata)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.exists():
            descriptor, filename = tempfile.mkstemp(prefix=".hysteria-", dir=target.parent)
            os.close(descriptor); temporary = Path(filename)
            try:
                _download(metadata["url"], temporary)
                if _sha256(temporary) != metadata["sha256"]:
                    raise HysteriaManagerError("hysteria_checksum_mismatch")
                os.chmod(temporary, 0o700); os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        elif _sha256(target) != metadata["sha256"]:
            raise HysteriaManagerError("hysteria_checksum_mismatch")
        return target

    def install(self, reference: str = "local", *, version: Optional[str] = None,
                binary_path: Path = Path("/usr/local/bin/hysteria")) -> dict[str, Any]:
        node_id = self._node_id(reference)
        if node_id != "local":
            data = self.store.load()
            operation = enqueue_node_operation(data, node_id, "core", "install", {
                "core": "hysteria2", "repository": OFFICIAL_REPOSITORY,
                "version": _normalize_version(version), "binary_path": str(binary_path),
                "resolve_release_on_node": True, "verify_sha256": True,
                "platform_check": True,
            }, resource_id="hysteria2")
            _audit(data, "install_queued", "node_core", "%s:hysteria2" % node_id,
                   ["version", "source", "sha256"])
            self.store.save(data)
            return {"node_id": node_id, "state": "pending_node_execution", "operation_id": operation["id"],
                    "version": _normalize_version(version) or "latest"}
        metadata = release(version)
        artifact = self._obtain(metadata)
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        data = self.store.load()
        previous_record = next((item for item in data.get("node_cores", [])
                                if item["node_id"] == node_id and item["core"] == "hysteria2"), None)
        previous = previous_record.get("version") if previous_record else None
        backup = None
        if binary_path.exists():
            backup = self.cache_dir / "backups" / (previous or "unknown") / ("hysteria-" + uuid.uuid4().hex)
            backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            shutil.copy2(binary_path, backup)
        descriptor, filename = tempfile.mkstemp(prefix=".hysteria-install-", dir=binary_path.parent)
        temporary = Path(filename)
        try:
            os.close(descriptor); shutil.copy2(artifact, temporary); os.chmod(temporary, 0o755)
            process = subprocess.run([str(temporary), "version"], capture_output=True, text=True,
                                     timeout=10, check=False)
            if process.returncode != 0 or metadata["version"].split("/", 1)[-1] not in (process.stdout + process.stderr):
                raise HysteriaManagerError("hysteria_binary_validation_failed")
            os.replace(temporary, binary_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            if backup:
                shutil.copy2(backup, binary_path)
            raise
        record = self._record(data, node_id, metadata, binary_path, "install" if previous is None else "update", previous)
        self.store.save(data)
        return {"node_id": node_id, "state": "installed", "core": record, "release": metadata}

    def install_many(self, reference: str, **kwargs: Any) -> dict[str, Any]:
        targets = ["local"] if reference != "all" else ["local"] + [node["id"] for node in NodeService(self.store).list() if node.get("enabled", True)]
        if reference != "all":
            targets = [reference]
        results = []
        previous_operation: Optional[str] = None
        for target in targets:
            try:
                installed = self.install(target, **kwargs)
                operation_id = installed.get("operation_id")
                if operation_id:
                    data = self.store.load()
                    operation = next(item for item in data["node_operations"]
                                     if item["id"] == operation_id)
                    operation["depends_on"] = previous_operation
                    self.store.save(data)
                    previous_operation = operation_id
                results.append(installed)
            except ValueError as error:
                results.append({"node_id": target, "state": "error", "error": str(error)})
                break
        return {"complete": bool(results) and all(item["state"] in {"installed", "pending_node_execution"} for item in results),
                "results": results}

    def version(self, reference: str = "local") -> dict[str, Any]:
        node_id = self._node_id(reference)
        if node_id != "local":
            record = next((item for item in self.store.load().get("node_cores", [])
                           if item["node_id"] == node_id and item["core"] == "hysteria2"), None)
            return {"node_id": node_id, "state": "recorded" if record else "not_installed", "core": record}
        try:
            actual = core_version("hysteria")
        except ValueError:
            actual = {"core": "hysteria", "binary": None, "version": None}
        return {"node_id": node_id, "state": "installed" if actual["version"] else "not_installed", **actual}

    def versions(self, reference: str = "local") -> dict[str, Any]:
        targets = (["local"] + [node["id"] for node in NodeService(self.store).list()
                                if node.get("enabled", True)]
                   if reference == "all" else [reference])
        results = [self.version(target) for target in targets]
        return {"complete": True, "results": results}

    def check_update(self, reference: str = "local") -> dict[str, Any]:
        current = self.version(reference)
        latest = release()
        installed = ((current.get("core") or {}).get("version") if isinstance(current.get("core"), dict)
                     else current.get("version"))
        return {"node_id": current["node_id"], "installed": installed, "latest": latest["version"],
                "update_available": installed != latest["version"], "release": latest}

    def check_updates(self, reference: str = "local") -> dict[str, Any]:
        targets = (["local"] + [node["id"] for node in NodeService(self.store).list()
                                if node.get("enabled", True)]
                   if reference == "all" else [reference])
        results = [self.check_update(target) for target in targets]
        return {"complete": True, "results": results}

    def rollback(self, reference: str = "local", version: Optional[str] = None,
                 binary_path: Path = Path("/usr/local/bin/hysteria")) -> dict[str, Any]:
        node_id = self._node_id(reference)
        data = self.store.load()
        history = [item for item in data.get("core_versions", [])
                   if item["node_id"] == node_id and item["core"] == "hysteria2"]
        if node_id != "local":
            operation = enqueue_node_operation(data, node_id, "core", "rollback", {
                "core": "hysteria2", "version": version, "verify_cached_sha256": True,
            }, resource_id="hysteria2")
            _audit(data, "rollback_queued", "node_core", "%s:hysteria2" % node_id,
                   ["version", "sha256"])
            self.store.save(data)
            return {"node_id": node_id, "state": "pending_node_execution", "operation_id": operation["id"]}
        candidates = [item for item in history[:-1] if version is None or item["version"] == _normalize_version(version)]
        if not candidates:
            raise HysteriaManagerError("hysteria_rollback_unavailable")
        selected = candidates[-1]; artifact = Path(selected["artifact"])
        if not artifact.is_file() or _sha256(artifact) != selected["sha256"]:
            raise HysteriaManagerError("hysteria_checksum_mismatch")
        shutil.copy2(artifact, binary_path); os.chmod(binary_path, 0o755)
        control_service("hysteria", "restart")
        current = next(item for item in data["node_cores"] if item["node_id"] == node_id and item["core"] == "hysteria2")
        previous = current["version"]
        metadata = {"version": selected["version"], "sha256": selected["sha256"],
                    "source": selected["source"], "asset": artifact.name}
        record = self._record(data, node_id, metadata, binary_path, "rollback", previous)
        self.store.save(data)
        return {"node_id": node_id, "state": "rolled_back", "core": record}

    def service(self, action: str, reference: str = "local", lines: int = 100) -> dict[str, Any]:
        node_id = self._node_id(reference)
        if node_id != "local":
            data = self.store.load()
            operation = enqueue_node_operation(data, node_id, "core", action,
                {"core": "hysteria2", "lines": lines}, resource_id="hysteria2")
            if action in {"start", "stop", "restart"}:
                _audit(data, "service_" + action + "_queued", "node_core",
                       "%s:hysteria2" % node_id, ["service_state"])
            self.store.save(data)
            return {"node_id": node_id, "state": "pending_node_execution", "operation_id": operation["id"]}
        if action in {"start", "stop", "restart"}:
            result = {"node_id": node_id, **control_service("hysteria", action)}
            data = self.store.load()
            _audit(data, "service_" + action, "node_core", "local:hysteria2",
                   ["service_state"])
            self.store.save(data)
            return result
        if action == "status":
            return {"node_id": node_id, **service_status("hysteria")}
        if action == "logs":
            return {"node_id": node_id, **service_logs("hysteria", lines)}
        raise HysteriaManagerError("hysteria_invalid_action")

    def service_many(self, action: str, reference: str, lines: int = 100) -> dict[str, Any]:
        targets = (["local"] + [node["id"] for node in NodeService(self.store).list() if node.get("enabled", True)]
                   if reference == "all" else [reference])
        results = []
        for target in targets:
            try:
                results.append(self.service(action, target, lines))
            except ValueError as error:
                results.append({"node_id": target, "state": "error", "error": str(error)})
        return {"complete": bool(results) and not any(item.get("state") == "error" for item in results),
                "results": results}

    def diagnostics(self, reference: str = "local") -> dict[str, Any]:
        node_id = self._node_id(reference); timestamp = _now()
        capabilities = self.capabilities(reference)
        if node_id == "local":
            try:
                status = service_status("hysteria")
            except ValueError as error:
                status = {"state": "error", "error": str(error)}
        else:
            status = {"state": "pending_node_report"}
        result = {"node_id": node_id, "checked_at": timestamp, "capabilities": capabilities,
                  "service": status, "traffic_api_limitations": [
                      "online counts client instances, not active proxy streams",
                      "kick does not prevent automatic reconnect; auth must also be revoked",
                      "client IP and UDP attribution are not guaranteed by aggregate traffic endpoints",
                  ]}
        data = self.store.load(); data.setdefault("hysteria_diagnostics", []).append(result)
        del data["hysteria_diagnostics"][:-200]
        data.setdefault("audit_log", []).append({"id": str(uuid.uuid4()), "timestamp": timestamp,
            "actor": os.environ.get("USER") or "local-admin", "action": "hysteria.diagnostics",
            "object_type": "node_core", "object_id": "%s:hysteria2" % node_id,
            "fields": ["service", "capabilities"]})
        self.store.save(data)
        return result

    def backup(self, reference: str, config_path: Path, output: Path) -> dict[str, Any]:
        node_id = self._node_id(reference)
        if node_id != "local":
            data = self.store.load()
            operation = enqueue_node_operation(data, node_id, "core", "backup", {
                "core": "hysteria2", "config_path": str(config_path),
                "encrypted_transport_required": True,
            }, resource_id="hysteria2")
            _audit(data, "backup_queued", "node_core", "%s:hysteria2" % node_id,
                   ["config_path", "encrypted_transport"])
            self.store.save(data)
            return {"node_id": node_id, "state": "pending_node_execution", "operation_id": operation["id"]}
        try:
            config = config_path.read_text(encoding="utf-8")
        except OSError as error:
            raise HysteriaManagerError("hysteria_backup_failed") from error
        core = next((item for item in self.store.load().get("node_cores", [])
                     if item["node_id"] == node_id and item["core"] == "hysteria2"), None)
        document = {"format": "xraydo-hysteria-backup-v1", "created_at": _now(),
                    "node_id": node_id, "core": core, "config": config,
                    "config_sha256": hashlib.sha256(config.encode()).hexdigest()}
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix=".hysteria-backup-", dir=output.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2)
                handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
            os.chmod(temporary, 0o600); os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        data = self.store.load()
        _audit(data, "backup", "node_core", "%s:hysteria2" % node_id,
               ["config_sha256", "backup_sha256"])
        self.store.save(data)
        return {"node_id": node_id, "state": "created", "path": str(output),
                "sha256": _sha256(output), "contains_secrets": True, "mode": "0600"}

    def collect_usage(self, reference: str, listen: str, secret: str,
                      *, clear: bool = False) -> dict[str, Any]:
        node_id = self._node_id(reference)
        endpoint = "traffic?clear=1" if clear else "traffic"
        # The API query parameter is fixed here and never user-controlled.
        if clear:
            host, separator, raw_port = listen.rpartition(":")
            if (not separator or host not in {"127.0.0.1", "localhost", "[::1]"}
                    or not raw_port.isdigit()):
                raise HysteriaManagerError("hysteria_stats_not_loopback")
            request = urllib.request.Request("http://%s/traffic?clear=1" % listen,
                headers={"Authorization": secret, "Accept": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=5) as response:
                    stats = json.load(response)
            except (OSError, ValueError, urllib.error.HTTPError) as error:
                raise HysteriaManagerError("hysteria_stats_failed") from error
        else:
            stats = traffic_api(listen, secret, endpoint)
        online = traffic_api(listen, secret, "online")
        keys = self.store.load().get("access_keys", [])
        by_username = {"k_" + key["id"].replace("-", ""): key for key in keys}
        applied, unknown = [], []
        service = AccessKeyService(self.store)
        timestamp = int(datetime.now(timezone.utc).timestamp())
        for username, values in (stats or {}).items():
            key = by_username.get(username)
            if key is None:
                unknown.append(username); continue
            # Server RX is client upload; server TX is client download.
            upload = int(values.get("rx", values.get("upload", 0)))
            download = int(values.get("tx", values.get("download", 0)))
            service.record_usage(key["id"], source_id="hysteria:%s" % node_id,
                event_id="stats:%s:%s:%s:%s" % (timestamp, username, upload, download),
                upload_bytes=upload, download_bytes=download,
                connected=bool((online or {}).get(username)))
            applied.append({"access_key_id": key["id"], "upload_bytes": upload,
                            "download_bytes": download, "online_instances": int((online or {}).get(username, 0))})
        return {"node_id": node_id, "applied": applied, "unknown_clients": unknown,
                "limitations": ["aggregate API does not reliably expose client IPs",
                                "online count is client instances, not proxy streams"]}

    def revoke_key(self, key_id: str, reference: str = "all") -> dict[str, Any]:
        data = self.store.load()
        key = next((item for item in data.get("access_keys", []) if item["id"] == key_id), None)
        if key is None:
            raise HysteriaManagerError("access_key_not_found")
        targets = [item for item in data.get("node_cores", [])
                   if item["core"] == "hysteria2" and item.get("enabled", True)
                   and (reference == "all" or item["node_id"] == self._node_id(reference))]
        username = "k_" + key_id.replace("-", "")
        results = []
        for core in targets:
            operation = enqueue_node_operation(data, core["node_id"], "hysteria_user", "revoke", {
                "access_key_id": key_id, "username": username,
                "remove_from_userpass": True, "kick_after_config_commit": True,
            }, resource_id=key_id)
            results.append({"node_id": core["node_id"], "state": "pending_node_execution",
                            "operation_id": operation["id"]})
        data.setdefault("audit_log", []).append({"id": str(uuid.uuid4()), "timestamp": _now(),
            "actor": os.environ.get("USER") or "local-admin", "action": "hysteria.key_revoke",
            "object_type": "access_key", "object_id": key_id,
            "fields": ["auth.userpass", "trafficStats.kick"]})
        self.store.save(data)
        return {"complete": bool(results) and all(item["state"] == "pending_node_execution" for item in results),
                "results": results}
