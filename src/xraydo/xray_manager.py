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
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .nodes import NodeService
from .store import Store
from .sync_queue import enqueue_node_operation
from .rollout import RolloutService


OFFICIAL_REPOSITORY = "XTLS/Xray-core"


class XrayManagerError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_arch() -> str:
    machine = platform.machine().lower()
    mapping = {"x86_64": "64", "amd64": "64", "aarch64": "arm64-v8a",
               "arm64": "arm64-v8a", "armv7l": "arm32-v7a"}
    if platform.system().lower() != "linux" or machine not in mapping:
        raise XrayManagerError("xray_platform_unsupported")
    return mapping[machine]


def _json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                   "User-Agent": "xraydo"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.HTTPError) as error:
        raise XrayManagerError("xray_release_lookup_failed") from error


def release(version: Optional[str] = None) -> dict[str, Any]:
    tag = version if not version or version.startswith("v") else "v" + version
    endpoint = (f"https://api.github.com/repos/{OFFICIAL_REPOSITORY}/releases/latest"
                if not tag else f"https://api.github.com/repos/{OFFICIAL_REPOSITORY}/releases/tags/{tag}")
    data = _json(endpoint)
    name = f"Xray-linux-{_asset_arch()}.zip"
    asset = next((item for item in data.get("assets", []) if item.get("name") == name), None)
    digest_asset = next((item for item in data.get("assets", [])
                         if item.get("name") in {name + ".dgst", name + ".sha256"}), None)
    if not asset or not digest_asset:
        raise XrayManagerError("xray_checksum_missing")
    request = urllib.request.Request(digest_asset["browser_download_url"],
                                     headers={"User-Agent": "xraydo"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            digest_text = response.read(8192).decode("ascii", "strict")
    except (OSError, UnicodeError, urllib.error.HTTPError) as error:
        raise XrayManagerError("xray_release_lookup_failed") from error
    matches = [word.lower() for word in digest_text.replace("=", " ").split()
               if len(word) == 64 and all(char in "0123456789abcdefABCDEF" for char in word)]
    if not matches:
        raise XrayManagerError("xray_checksum_missing")
    return {"version": data["tag_name"], "published_at": data.get("published_at"),
            "asset": name, "url": asset["browser_download_url"], "sha256": matches[-1],
            "source": f"https://github.com/{OFFICIAL_REPOSITORY}/releases/tag/{data['tag_name']}"}


class XrayManager:
    def __init__(self, store: Store, cache_dir: Optional[Path] = None) -> None:
        self.store = store
        self.cache_dir = cache_dir or store.data_dir / "artifacts" / "xray"

    def _node_id(self, reference: str) -> str:
        return "local" if reference in {"local", "standalone"} else NodeService(self.store).find(reference)["id"]

    def _obtain(self, metadata: dict[str, Any]) -> Path:
        target = self.cache_dir / metadata["version"] / metadata["asset"]
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not target.exists():
            descriptor, filename = tempfile.mkstemp(prefix=".xray-", dir=target.parent)
            os.close(descriptor); temporary = Path(filename)
            try:
                request = urllib.request.Request(metadata["url"], headers={"User-Agent": "xraydo"})
                with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as out:
                    shutil.copyfileobj(response, out)
                if _sha256(temporary) != metadata["sha256"]:
                    raise XrayManagerError("xray_checksum_mismatch")
                os.replace(temporary, target)
            except (OSError, urllib.error.HTTPError) as error:
                raise XrayManagerError("xray_download_failed") from error
            finally:
                temporary.unlink(missing_ok=True)
        if _sha256(target) != metadata["sha256"]:
            raise XrayManagerError("xray_checksum_mismatch")
        return target

    def install(self, reference: str = "local", *, version: Optional[str] = None,
                binary_path: Path = Path("/usr/local/bin/xray")) -> dict[str, Any]:
        node_id = self._node_id(reference)
        if node_id != "local":
            data = self.store.load()
            operation = enqueue_node_operation(data, node_id, "core", "install", {
                "core": "xray", "repository": OFFICIAL_REPOSITORY, "version": version,
                "binary_path": str(binary_path), "verify_sha256": True,
                "resolve_release_on_node": True, "atomic": True,
            }, resource_id="xray")
            self._audit(data, "install_queued", node_id, ["version", "sha256"])
            self.store.save(data)
            return {"node_id": node_id, "state": "pending_node_execution",
                    "operation_id": operation["id"], "version": version or "latest"}
        metadata = release(version); archive = self._obtain(metadata)
        try:
            with zipfile.ZipFile(archive) as bundle:
                info = next((item for item in bundle.infolist()
                             if Path(item.filename).name.lower() == "xray"), None)
                if info is None or info.file_size > 256 * 1024 * 1024:
                    raise XrayManagerError("xray_archive_invalid")
                extracted = bundle.read(info)
        except (OSError, zipfile.BadZipFile) as error:
            raise XrayManagerError("xray_archive_invalid") from error
        binary_path.parent.mkdir(parents=True, exist_ok=True)
        data = self.store.load()
        current = next((item for item in data.get("node_cores", [])
                        if item["node_id"] == node_id and item["core"] == "xray"), None)
        previous_version = current.get("version") if current else None
        backup = None
        if binary_path.is_file():
            backup = self.cache_dir / "backups" / (previous_version or "unknown") / uuid.uuid4().hex
            backup.parent.mkdir(parents=True, exist_ok=True, mode=0o700); shutil.copy2(binary_path, backup)
        descriptor, filename = tempfile.mkstemp(prefix=".xray-install-", dir=binary_path.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(extracted); handle.flush(); os.fsync(handle.fileno())
            os.chmod(temporary, 0o755)
            result = subprocess.run([str(temporary), "version"], capture_output=True, text=True,
                                    timeout=10, check=False)
            if result.returncode != 0 or metadata["version"].lstrip("v") not in result.stdout + result.stderr:
                raise XrayManagerError("xray_binary_validation_failed")
            os.replace(temporary, binary_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            if backup:
                shutil.copy2(backup, binary_path)
            raise
        record = {"node_id": node_id, "core": "xray", "enabled": True,
                  "version": metadata["version"], "binary": str(binary_path),
                  "sha256": hashlib.sha256(extracted).hexdigest(), "source": metadata["source"],
                  "architecture": platform.machine(), "updated_at": _now()}
        if current is None:
            record["created_at"] = _now(); data.setdefault("node_cores", []).append(record)
        else:
            current.update(record); record = current
        data.setdefault("core_versions", []).append({
            "id": str(uuid.uuid4()), "node_id": node_id, "core": "xray",
            "version": metadata["version"], "sha256": metadata["sha256"],
            "binary_sha256": hashlib.sha256(extracted).hexdigest(), "source": metadata["source"],
            "artifact": str(archive), "backup": str(backup) if backup else None,
            "backup_sha256": _sha256(backup) if backup else None,
            "action": "install" if previous_version is None else "update",
            "previous_version": previous_version, "created_at": _now(), "verified": True,
        })
        self._audit(data, "install" if previous_version is None else "update", node_id,
                    ["version", "sha256", "source"])
        self.store.save(data)
        return {"node_id": node_id, "state": "installed", "core": record, "release": metadata}

    def version(self, reference: str = "local") -> dict[str, Any]:
        node_id = self._node_id(reference)
        record = next((item for item in self.store.load().get("node_cores", [])
                       if item["node_id"] == node_id and item["core"] == "xray"), None)
        return {"node_id": node_id, "state": "installed" if record else "not_installed", "core": record}

    def versions(self, reference: str = "local") -> dict[str, Any]:
        targets = (["local"] + [node["id"] for node in NodeService(self.store).list()
                                if node.get("enabled", True)]
                   if reference == "all" else [reference])
        return {"complete": True, "results": [self.version(target) for target in targets]}

    def check_update(self, reference: str = "local") -> dict[str, Any]:
        current = self.version(reference); latest = release()
        installed = (current.get("core") or {}).get("version")
        return {"node_id": current["node_id"], "installed": installed,
                "latest": latest["version"], "update_available": installed != latest["version"],
                "release": latest}

    def check_updates(self, reference: str = "local") -> dict[str, Any]:
        targets = (["local"] + [node["id"] for node in NodeService(self.store).list()
                                if node.get("enabled", True)]
                   if reference == "all" else [reference])
        return {"complete": True, "results": [self.check_update(target) for target in targets]}

    def install_many(self, reference: str = "all", *, version: Optional[str] = None,
                     canary: int = 1, max_unavailable: int = 1,
                     confirm: bool = True) -> dict[str, Any]:
        targets = ([node["id"] for node in NodeService(self.store).list()
                    if node.get("enabled", True)] if reference == "all" else [self._node_id(reference)])
        if not targets:
            return {"complete": True, "results": []}
        plan = RolloutService(self.store).plan(resource_type="core", action="install",
            targets=[{"node_id": node_id, "resource_id": "xray", "payload": {
                "core": "xray", "version": version, "repository": OFFICIAL_REPOSITORY,
                "resolve_release_on_node": True, "verify_sha256": True, "atomic": True,
            }} for node_id in targets], canary=min(canary, len(targets)),
            max_unavailable=min(max_unavailable, len(targets)))
        return RolloutService(self.store).start(plan, confirm=confirm)

    def history(self, reference: str = "local") -> list[dict[str, Any]]:
        node_id = self._node_id(reference)
        return [item for item in self.store.load().get("core_versions", [])
                if item.get("node_id") == node_id and item.get("core") == "xray"]

    def rollback(self, reference: str = "local", *, version: Optional[str] = None,
                 binary_path: Path = Path("/usr/local/bin/xray")) -> dict[str, Any]:
        node_id = self._node_id(reference); data = self.store.load()
        if node_id != "local":
            operation = enqueue_node_operation(data, node_id, "core", "rollback",
                {"core": "xray", "version": version, "verify_cached_sha256": True}, "xray")
            self.store.save(data)
            return {"node_id": node_id, "state": "pending_node_execution", "operation_id": operation["id"]}
        candidates = [item for item in data.get("core_versions", [])
                      if item.get("node_id") == node_id and item.get("core") == "xray"
                      and item.get("backup") and (version is None or item.get("previous_version") == version)]
        if not candidates:
            raise XrayManagerError("xray_rollback_unavailable")
        selected = candidates[-1]; backup = Path(selected["backup"])
        if (not backup.is_file() or not selected.get("backup_sha256")
                or _sha256(backup) != selected["backup_sha256"]):
            raise XrayManagerError("xray_rollback_unavailable")
        shutil.copy2(backup, binary_path); os.chmod(binary_path, 0o755)
        result = subprocess.run([str(binary_path), "version"], capture_output=True, text=True,
                                timeout=10, check=False)
        if result.returncode != 0:
            raise XrayManagerError("xray_binary_validation_failed")
        current = next(item for item in data["node_cores"]
                       if item["node_id"] == node_id and item["core"] == "xray")
        current["version"] = selected.get("previous_version") or "restored"
        current["sha256"] = _sha256(binary_path); current["updated_at"] = _now()
        data.setdefault("core_versions", []).append({
            "id": str(uuid.uuid4()), "node_id": node_id, "core": "xray",
            "version": current["version"], "sha256": current["sha256"],
            "source": selected.get("source"), "artifact": str(backup),
            "action": "rollback", "previous_version": selected.get("version"),
            "created_at": _now(), "verified": True,
        })
        self._audit(data, "rollback", node_id, ["version", "sha256"]); self.store.save(data)
        return {"node_id": node_id, "state": "rolled_back", "core": current}

    def backup(self, reference: str, config_path: Path, output: Path) -> dict[str, Any]:
        node_id = self._node_id(reference)
        if node_id != "local":
            data = self.store.load(); operation = enqueue_node_operation(data, node_id, "core", "backup",
                {"core": "xray", "config_path": str(config_path),
                 "encrypted_transport_required": True}, "xray")
            self.store.save(data)
            return {"node_id": node_id, "state": "pending_node_execution", "operation_id": operation["id"]}
        try:
            config = config_path.read_text(encoding="utf-8")
        except OSError as error:
            raise XrayManagerError("xray_backup_failed") from error
        document = {"format": "xraydo-xray-backup-v1", "created_at": _now(),
                    "node_id": node_id, "core": self.version(reference).get("core"),
                    "config": config, "config_sha256": hashlib.sha256(config.encode()).hexdigest()}
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, filename = tempfile.mkstemp(prefix=".xray-backup-", dir=output.parent)
        temporary = Path(filename)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, ensure_ascii=False, indent=2); handle.write("\n")
                handle.flush(); os.fsync(handle.fileno())
            os.chmod(temporary, 0o600); os.replace(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)
        return {"node_id": node_id, "state": "created", "path": str(output),
                "sha256": _sha256(output), "contains_secrets": True, "mode": "0600"}

    @staticmethod
    def _audit(data: dict[str, Any], action: str, node_id: str, fields: list[str]) -> None:
        data.setdefault("audit_log", []).append({"id": str(uuid.uuid4()), "timestamp": _now(),
            "actor": os.environ.get("USER") or "local-admin", "action": "xray." + action,
            "object_type": "node_core", "object_id": node_id + ":xray", "fields": fields})
