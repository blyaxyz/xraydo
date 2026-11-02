from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.parse
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

OFFICIAL_REPOSITORY = "telemt/telemt"
REPOSITORY_URL = "https://github.com/telemt/telemt.git"
RELEASE_API = "https://api.github.com/repos/telemt/telemt/releases"
RUST_VERSION = (1, 88)
TARGETS = {
    ("x86_64", "gnu"): "x86_64-unknown-linux-gnu",
    ("aarch64", "gnu"): "aarch64-unknown-linux-gnu",
}


class TelemtManagerError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(arguments, text=True, capture_output=True, check=False, **kwargs)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TelemtManagerError("telemt_command_failed") from error


def _request_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "xraydo/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.HTTPError) as error:
        raise TelemtManagerError("telemt_release_metadata_failed") from error


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "xraydo/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=90) as response, destination.open("wb") as output:
            shutil.copyfileobj(response, output)
    except (OSError, urllib.error.HTTPError) as error:
        raise TelemtManagerError("telemt_download_failed") from error


def _libc() -> str:
    name, _version = platform.libc_ver()
    normalized = name.casefold()
    return "gnu" if normalized in {"glibc", "gnu libc"} else normalized or "unknown"


def platform_capabilities() -> dict[str, Any]:
    machine = platform.machine().casefold()
    libc = _libc()
    target = TARGETS.get((machine, libc))
    return {"os": platform.system().casefold(), "machine": machine, "libc": libc,
            "target": target, "compatible": platform.system() == "Linux" and target is not None,
            "features": {"classic": True, "secure": True, "fake_tls": True,
                         "users_api": True, "hot_reload": True, "traffic_stats": True}}


def release(version: Optional[str] = None) -> dict[str, Any]:
    if version and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version) is None:
        raise TelemtManagerError("telemt_invalid_version")
    url = RELEASE_API + ("/tags/" + version if version else "/latest")
    data = _request_json(url)
    capabilities = platform_capabilities()
    if not capabilities["compatible"]:
        raise TelemtManagerError("telemt_platform_unsupported")
    target = capabilities["target"]
    architecture = "x86_64" if target.startswith("x86_64") else "aarch64"
    asset_name = "telemt-%s-linux-%s.tar.gz" % (architecture, capabilities["libc"])
    asset = next((item for item in data.get("assets", []) if item.get("name") == asset_name), None)
    if asset is None:
        raise TelemtManagerError("telemt_artifact_missing")
    digest = str(asset.get("digest") or "")
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        raise TelemtManagerError("telemt_checksum_missing")
    return {"version": data["tag_name"], "commit": data.get("target_commitish"),
            "asset": asset_name, "url": asset["browser_download_url"],
            "sha256": digest.split(":", 1)[1].lower(), "target": target,
            "source": "https://github.com/%s/releases/tag/%s" % (OFFICIAL_REPOSITORY, data["tag_name"]),
            "kind": "release", "profile": "release"}


def api_request(listen: str, auth_header: str, method: str, path: str,
                payload: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    host, separator, port = listen.rpartition(":")
    if not separator or host not in {"127.0.0.1", "localhost", "[::1]"} or not port.isdigit():
        raise TelemtManagerError("telemt_api_not_loopback")
    if not path.startswith("/v1/") or ".." in path:
        raise TelemtManagerError("telemt_api_invalid_path")
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request("http://%s%s" % (listen, path), data=data, method=method,
        headers={"Authorization": auth_header, "Content-Type": "application/json",
                 "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
    except (OSError, ValueError, urllib.error.HTTPError) as error:
        raise TelemtManagerError("telemt_api_failed") from error
    if not isinstance(result, dict) or result.get("ok") is False:
        raise TelemtManagerError("telemt_api_failed")
    return result


class TelemtManager:
    def __init__(self, store: Store, cache_dir: Optional[Path] = None) -> None:
        self.store = store
        self.cache_dir = cache_dir or store.data_dir / "artifacts" / "telemt"

    def _node_id(self, reference: str) -> str:
        return "local" if reference in {"local", "standalone"} else NodeService(self.store).find(reference)["id"]

    def _audit(self, data: dict[str, Any], action: str, object_id: str, fields: list[str]) -> None:
        data.setdefault("audit_log", []).append({"id": str(uuid.uuid4()), "timestamp": _now(),
            "actor": os.environ.get("USER") or "local-admin", "action": "telemt." + action,
            "object_type": "node_core", "object_id": object_id, "fields": fields})

    def audit_configuration(self, node_id: str, key_id: str, *, queued: bool) -> None:
        data = self.store.load()
        self._audit(data, "config_queued" if queued else "config_applied", key_id,
                    ["node_id", "access.users", "censorship", "server.listeners"])
        self.store.save(data)

    def preflight(self, target: Optional[str] = None, profile: str = "release") -> dict[str, Any]:
        capabilities = platform_capabilities()
        selected_target = target or capabilities["target"]
        if not capabilities["compatible"] or selected_target not in TARGETS.values():
            raise TelemtManagerError("telemt_platform_unsupported")
        if profile not in {"release", "economy"}:
            raise TelemtManagerError("telemt_invalid_build_profile")
        commands = {name: shutil.which(name) for name in ("git", "cargo", "rustc")}
        if not all(commands.values()):
            raise TelemtManagerError("telemt_build_tools_missing")
        rustc = _run([commands["rustc"], "--version"], timeout=10)
        match = re.search(r"rustc\s+([0-9]+)\.([0-9]+)", rustc.stdout)
        if rustc.returncode or not match or tuple(map(int, match.groups())) < RUST_VERSION:
            raise TelemtManagerError("telemt_rust_too_old")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(self.cache_dir).free
        required = 1024**3 if profile == "economy" else 2 * 1024**3
        if free < required:
            raise TelemtManagerError("telemt_build_disk_low")
        return {"target": selected_target, "profile": profile, "rustc": rustc.stdout.strip(),
                "free_bytes": free, "required_bytes": required, "commands": commands}

    def build(self, ref: str, *, target: Optional[str] = None, profile: str = "release") -> dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", ref) or ref.startswith("-") or ".." in ref:
            raise TelemtManagerError("telemt_invalid_version")
        preflight = self.preflight(target, profile)
        with tempfile.TemporaryDirectory(prefix="xraydo-telemt-build-") as directory:
            source = Path(directory) / "source"
            clone = _run([preflight["commands"]["git"], "clone", "--filter=blob:none", "--no-checkout",
                          REPOSITORY_URL, str(source)], timeout=300)
            if clone.returncode:
                raise TelemtManagerError("telemt_source_fetch_failed")
            fetch = _run([preflight["commands"]["git"], "-C", str(source), "fetch", "--depth", "1",
                          "origin", ref], timeout=300)
            if fetch.returncode or _run([preflight["commands"]["git"], "-C", str(source), "checkout",
                                         "--detach", "FETCH_HEAD"], timeout=60).returncode:
                raise TelemtManagerError("telemt_source_fetch_failed")
            commit_result = _run([preflight["commands"]["git"], "-C", str(source), "rev-parse", "HEAD"], timeout=10)
            commit = commit_result.stdout.strip()
            lock = source / "Cargo.lock"
            if not re.fullmatch(r"[0-9a-f]{40}", commit) or not lock.is_file():
                raise TelemtManagerError("telemt_lock_missing")
            artifact_dir = self.cache_dir / "source" / commit / preflight["target"] / profile
            artifact = artifact_dir / "telemt"
            if not artifact.exists():
                environment = os.environ.copy()
                if profile == "economy":
                    environment.update({"CARGO_BUILD_JOBS": "1", "CARGO_PROFILE_RELEASE_LTO": "off",
                                        "CARGO_PROFILE_RELEASE_CODEGEN_UNITS": "8"})
                build = _run([preflight["commands"]["cargo"], "build", "--locked", "--release",
                              "--target", preflight["target"]], cwd=source, env=environment, timeout=7200)
                compiled = source / "target" / preflight["target"] / "release" / "telemt"
                if build.returncode or not compiled.is_file():
                    raise TelemtManagerError("telemt_build_failed")
                artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
                shutil.copy2(compiled, artifact); os.chmod(artifact, 0o700)
            metadata = {"id": str(uuid.uuid4()), "kind": "source", "version": ref,
                "commit": commit, "target": preflight["target"], "profile": profile,
                "cargo_lock_sha256": _sha256(lock), "sha256": _sha256(artifact),
                "artifact": str(artifact), "source": REPOSITORY_URL, "created_at": _now(),
                "provenance": {"rustc": preflight["rustc"], "cargo_locked": True,
                               "build_host": platform.node()}}
        data = self.store.load()
        existing = next((item for item in data.setdefault("telemt_artifacts", [])
                         if all(item.get(key) == metadata[key] for key in ("commit", "target", "profile"))), None)
        if existing:
            metadata["id"] = existing["id"]; existing.update(metadata)
        else:
            data["telemt_artifacts"].append(metadata)
        self._audit(data, "build", metadata["id"], ["commit", "target", "profile", "sha256"])
        self.store.save(data)
        return metadata

    def _release_artifact(self, metadata: dict[str, Any]) -> Path:
        destination = self.cache_dir / "release" / metadata["version"] / metadata["asset"]
        binary = destination.parent / "telemt"
        manifest = destination.parent / "manifest.json"
        if binary.exists() and manifest.exists():
            try:
                cached = json.loads(manifest.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                cached = {}
            if cached.get("archive_sha256") == metadata["sha256"] and cached.get("binary_sha256") == _sha256(binary):
                metadata["binary_sha256"] = cached["binary_sha256"]
                return binary
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _download(metadata["url"], destination)
        if _sha256(destination) != metadata["sha256"]:
            raise TelemtManagerError("telemt_checksum_mismatch")
        with tarfile.open(destination, "r:gz") as archive:
            members = [item for item in archive.getmembers() if item.isfile() and Path(item.name).name == "telemt"]
            if len(members) != 1 or Path(members[0].name).is_absolute() or ".." in Path(members[0].name).parts:
                raise TelemtManagerError("telemt_archive_invalid")
            source = archive.extractfile(members[0])
            if source is None:
                raise TelemtManagerError("telemt_archive_invalid")
            with binary.open("wb") as output:
                shutil.copyfileobj(source, output)
        os.chmod(binary, 0o700)
        metadata["binary_sha256"] = _sha256(binary)
        manifest.write_text(json.dumps({"archive_sha256": metadata["sha256"],
                                        "binary_sha256": metadata["binary_sha256"]}), encoding="utf-8")
        os.chmod(manifest, 0o600)
        return binary

    def _artifact(self, artifact_id: str) -> dict[str, Any]:
        artifact = next((item for item in self.store.load().get("telemt_artifacts", [])
                         if item["id"] == artifact_id), None)
        if artifact is None or not Path(artifact["artifact"]).is_file():
            raise TelemtManagerError("telemt_artifact_not_found")
        if _sha256(Path(artifact["artifact"])) != artifact["sha256"]:
            raise TelemtManagerError("telemt_checksum_mismatch")
        return artifact

    def install(self, reference: str = "local", *, version: Optional[str] = None,
                artifact_id: Optional[str] = None,
                binary_path: Path = Path("/usr/local/bin/telemt")) -> dict[str, Any]:
        node_id = self._node_id(reference)
        if artifact_id:
            metadata = self._artifact(artifact_id)
        elif node_id == "local":
            metadata = release(version)
            binary = self._release_artifact(metadata)
            metadata["artifact"] = str(binary); metadata["sha256"] = _sha256(binary)
        else:
            metadata = {"kind": "release", "version": version or "latest",
                        "source": OFFICIAL_REPOSITORY, "resolve_on_node": True}
        if node_id != "local":
            data = self.store.load()
            payload = {"core": "telemt", "artifact": metadata, "binary_path": str(binary_path),
                       "verify_sha256_after_transfer": True, "build_on_node": False}
            operation = enqueue_node_operation(data, node_id, "core", "install", payload, resource_id="telemt")
            self._audit(data, "install_queued", "%s:telemt" % node_id,
                        ["version", "commit", "target", "sha256"])
            self.store.save(data)
            return {"node_id": node_id, "state": "pending_node_execution", "operation_id": operation["id"]}
        artifact = Path(metadata["artifact"])
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".telemt-install-", dir=binary_path.parent)
        os.close(descriptor); temporary = Path(temporary_name)
        try:
            shutil.copy2(artifact, temporary); os.chmod(temporary, 0o755)
            check = _run([str(temporary), "--version"], timeout=10)
            if check.returncode:
                raise TelemtManagerError("telemt_binary_validation_failed")
            os.replace(temporary, binary_path)
        finally:
            temporary.unlink(missing_ok=True)
        data = self.store.load()
        current = next((item for item in data.get("node_cores", [])
                        if item["node_id"] == "local" and item["core"] == "telemt"), None)
        previous = current.get("version") if current else None
        record = {"node_id": "local", "core": "telemt", "enabled": True,
                  "version": metadata.get("version"), "commit": metadata.get("commit"),
                  "target": metadata.get("target"), "profile": metadata.get("profile"),
                  "sha256": _sha256(binary_path), "artifact": str(artifact),
                  "source": metadata["source"], "updated_at": _now(),
                  "capabilities": platform_capabilities()["features"]}
        if current: current.update(record)
        else: data.setdefault("node_cores", []).append(record)
        data.setdefault("core_versions", []).append({**record, "previous_version": previous,
                                                      "created_at": _now()})
        self._audit(data, "install" if previous is None else "update", "local:telemt",
                    ["version", "commit", "target", "sha256"])
        self.store.save(data)
        return {"node_id": "local", "state": "installed", "core": record}

    def install_many(self, reference: str, **kwargs: Any) -> dict[str, Any]:
        targets = (["local"] + [item["id"] for item in NodeService(self.store).list()
                                if item.get("enabled", True)] if reference == "all" else [reference])
        results, dependency = [], None
        for target in targets:
            try:
                result = self.install(target, **kwargs)
                if result.get("operation_id"):
                    data = self.store.load()
                    operation = next(item for item in data["node_operations"] if item["id"] == result["operation_id"])
                    operation["depends_on"] = dependency; self.store.save(data)
                    dependency = result["operation_id"]
                results.append(result)
            except ValueError as error:
                results.append({"node_id": target, "state": "error", "error": str(error)}); break
        return {"complete": bool(results) and not any(item["state"] == "error" for item in results),
                "results": results}

    def versions(self, reference: str = "local") -> dict[str, Any]:
        targets = (["local"] + [item["id"] for item in NodeService(self.store).list()
                                if item.get("enabled", True)] if reference == "all" else [reference])
        data = self.store.load(); results = []
        for target in targets:
            node_id = self._node_id(target)
            record = next((item for item in data.get("node_cores", [])
                           if item["node_id"] == node_id and item["core"] == "telemt"), None)
            if node_id == "local":
                try: actual = core_version("telemt")
                except ValueError: actual = {"version": None}
                results.append({"node_id": node_id, "record": record, **actual})
            else: results.append({"node_id": node_id, "record": record,
                                  "version": record.get("version") if record else None})
        return {"complete": True, "results": results}

    def check_updates(self, reference: str = "local") -> dict[str, Any]:
        latest = release()
        versions = self.versions(reference)["results"]
        return {"complete": True, "latest": latest["version"], "results": [
            {**item, "update_available": item.get("version") != latest["version"]} for item in versions]}

    def rollback(self, reference: str = "local", *, binary_path: Path = Path("/usr/local/bin/telemt")) -> dict[str, Any]:
        node_id = self._node_id(reference); data = self.store.load()
        history = [item for item in data.get("core_versions", [])
                   if item["node_id"] == node_id and item["core"] == "telemt"]
        if node_id != "local":
            operation = enqueue_node_operation(data, node_id, "core", "rollback",
                {"core": "telemt", "verify_cached_sha256": True}, resource_id="telemt")
            self.store.save(data)
            return {"node_id": node_id, "state": "pending_node_execution", "operation_id": operation["id"]}
        if len(history) < 2:
            raise TelemtManagerError("telemt_rollback_unavailable")
        selected = history[-2]; artifact = Path(selected["artifact"])
        if not artifact.is_file() or _sha256(artifact) != selected["sha256"]:
            raise TelemtManagerError("telemt_checksum_mismatch")
        shutil.copy2(artifact, binary_path); os.chmod(binary_path, 0o755)
        control_service("telemt", "restart")
        current = next(item for item in data["node_cores"] if item["node_id"] == "local" and item["core"] == "telemt")
        current.update({key: selected.get(key) for key in ("version", "commit", "target", "profile", "sha256", "artifact")})
        data.setdefault("core_versions", []).append({**current, "previous_version": history[-1].get("version"),
                                                      "created_at": _now(), "action": "rollback"})
        self._audit(data, "rollback", "local:telemt", ["version", "commit", "sha256"])
        self.store.save(data)
        return {"node_id": "local", "state": "rolled_back", "core": current}

    def service(self, action: str, reference: str = "local", lines: int = 100) -> dict[str, Any]:
        node_id = self._node_id(reference)
        if node_id != "local":
            data = self.store.load(); operation = enqueue_node_operation(data, node_id, "core", action,
                {"core": "telemt", "lines": lines}, resource_id="telemt"); self.store.save(data)
            return {"node_id": node_id, "state": "pending_node_execution", "operation_id": operation["id"]}
        if action in {"start", "stop", "restart"}: return {"node_id": node_id, **control_service("telemt", action)}
        if action == "status": return {"node_id": node_id, **service_status("telemt")}
        if action == "logs": return {"node_id": node_id, **service_logs("telemt", lines)}
        raise TelemtManagerError("telemt_invalid_action")

    def service_many(self, action: str, reference: str, lines: int = 100) -> dict[str, Any]:
        targets = (["local"] + [item["id"] for item in NodeService(self.store).list()
                                if item.get("enabled", True)] if reference == "all" else [reference])
        return {"complete": True, "results": [self.service(action, item, lines) for item in targets]}

    def sync_key(self, key_id: str, reference: str = "all", action: str = "upsert",
                 mode: str = "tls") -> dict[str, Any]:
        if mode not in {"classic", "secure", "tls"}:
            raise TelemtManagerError("telemt_invalid_mode")
        data = self.store.load(); key = next((item for item in data.get("access_keys", []) if item["id"] == key_id), None)
        if key is None: raise TelemtManagerError("access_key_not_found")
        targets = [item for item in data.get("node_cores", []) if item["core"] == "telemt"
                   and item.get("enabled", True) and (reference == "all" or item["node_id"] == self._node_id(reference))]
        username = "k_" + key_id.replace("-", "")
        results = []
        for core in targets:
            operation = enqueue_node_operation(data, core["node_id"], "telemt_user", action, {
                "username": username, "secret": (key["credentials"]["telemt_secret"]
                                                     if action == "upsert" else None),
                "access_key_id": key_id, "api_preferred": True, "atomic_config_fallback": True,
                "controlled_restart_if_required": True, "mode": mode,
            }, resource_id=key_id)
            results.append({"node_id": core["node_id"], "operation_id": operation["id"],
                            "state": "pending_node_execution"})
        self._audit(data, "key_" + action, key_id, ["access.users", "api", "reload"])
        self.store.save(data)
        return {"complete": bool(results), "results": results}

    def diagnostics(self, reference: str = "local", *, listen: Optional[str] = None,
                    auth_header: Optional[str] = None) -> dict[str, Any]:
        node_id = self._node_id(reference)
        status = service_status("telemt") if node_id == "local" else {"state": "pending_node_report"}
        api = None
        if listen and auth_header:
            api = {"health": api_request(listen, auth_header, "GET", "/v1/health"),
                   "system": api_request(listen, auth_header, "GET", "/v1/system/info"),
                   "stats": api_request(listen, auth_header, "GET", "/v1/stats/summary")}
        result = {"node_id": node_id, "checked_at": _now(), "service": status, "api": api,
                  "api_policy": {"user_mutations": "hot admission update",
                                 "config_reload": "explicit reload when supported",
                                 "fallback": "atomic config plus controlled restart"}}
        data = self.store.load(); data.setdefault("telemt_diagnostics", []).append(result)
        del data["telemt_diagnostics"][:-200]
        self._audit(data, "diagnostics", "%s:telemt" % node_id, ["service", "api", "stats"])
        self.store.save(data); return result

    def collect_usage(self, reference: str, listen: str, auth_header: str) -> dict[str, Any]:
        node_id = self._node_id(reference)
        response = api_request(listen, auth_header, "GET", "/v1/users")
        users = response.get("data") or []
        if not isinstance(users, list):
            raise TelemtManagerError("telemt_api_failed")
        data = self.store.load()
        keys = {"k_" + item["id"].replace("-", ""): item for item in data.get("access_keys", [])}
        cursors = data.setdefault("telemt_usage_cursors", [])
        updates, unknown = [], []
        for item in users:
            if not isinstance(item, dict) or not isinstance(item.get("username"), str):
                continue
            username = item["username"]; key = keys.get(username)
            if key is None:
                unknown.append(username); continue
            total = max(0, int(item.get("total_octets", 0)))
            cursor = next((row for row in cursors if row["node_id"] == node_id
                           and row["username"] == username), None)
            previous = int(cursor.get("total_octets", 0)) if cursor else 0
            delta = total - previous if total >= previous else total
            if cursor is None:
                cursor = {"node_id": node_id, "username": username}; cursors.append(cursor)
            cursor.update({"total_octets": total, "updated_at": _now()})
            updates.append({"access_key_id": key["id"], "total_octets": total, "delta_bytes": delta,
                            "current_connections": int(item.get("current_connections", 0))})
        self.store.save(data)
        service = AccessKeyService(self.store)
        for update in updates:
            if update["delta_bytes"]:
                service.record_usage(update["access_key_id"], source_id="telemt:%s" % node_id,
                    event_id="users:%s:%s" % (node_id, update["total_octets"]),
                    upload_bytes=0, download_bytes=update["delta_bytes"],
                    connected=update["current_connections"] > 0)
        return {"node_id": node_id, "applied": updates, "unknown_users": unknown,
                "limitations": ["Telemt total_octets does not split upload and download",
                                "connection and IP accuracy depends on enabled runtime telemetry"]}

    def backup(self, reference: str, config_path: Path, output: Path) -> dict[str, Any]:
        node_id = self._node_id(reference)
        if node_id != "local":
            data = self.store.load(); operation = enqueue_node_operation(data, node_id, "core", "backup",
                {"core": "telemt", "config_path": str(config_path), "mode": "0600"}, resource_id="telemt")
            self._audit(data, "backup_queued", "%s:telemt" % node_id, ["config_path", "mode"])
            self.store.save(data); return {"node_id": node_id, "state": "pending_node_execution",
                                           "operation_id": operation["id"]}
        try: config = config_path.read_text(encoding="utf-8")
        except OSError as error: raise TelemtManagerError("telemt_backup_failed") from error
        document = {"format": "xraydo-telemt-backup-v1", "created_at": _now(), "node_id": node_id,
                    "config": config, "config_sha256": hashlib.sha256(config.encode()).hexdigest()}
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".telemt-backup-", dir=output.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2); handle.write("\n")
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(temporary, 0o600); os.replace(temporary, output)
        finally: temporary.unlink(missing_ok=True)
        data = self.store.load(); self._audit(data, "backup", "local:telemt",
                                              ["config_sha256", "backup_sha256"])
        self.store.save(data)
        return {"node_id": node_id, "state": "created", "path": str(output),
                "sha256": _sha256(output), "mode": "0600", "contains_secrets": True}
