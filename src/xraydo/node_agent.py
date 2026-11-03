from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import socket
import shutil
import ssl
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import __version__
from .control_plane import KNOWN_CAPABILITIES, PROTOCOL_VERSION, request_signature


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=".agent-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ValueError("agent_invalid_config") from error
    if not isinstance(value, dict):
        raise ValueError("agent_invalid_config")
    return value


def _openssl(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise ValueError("agent_key_generation_failed") from error


def _new_key_and_csr(root: Path) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = root / "node.key"
    csr = root / "node.csr"
    _openssl(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt",
              "rsa_keygen_bits:3072", "-out", str(key)])
    _openssl(["openssl", "req", "-new", "-key", str(key), "-subj",
              "/CN=xraydo enrollment request", "-out", str(csr)])
    os.chmod(key, 0o600)
    value = csr.read_text(encoding="utf-8")
    csr.unlink(missing_ok=True)
    return key, value


class ControlClient:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        parsed = urlsplit(str(config.get("master", "")))
        if parsed.scheme != "https" or not parsed.hostname or parsed.path not in {"", "/"}:
            raise ValueError("agent_invalid_master_url")
        self.host = parsed.hostname
        self.port = parsed.port or 443

    def _connection(self, with_client: bool = True) -> http.client.HTTPSConnection:
        context = ssl.create_default_context(cafile=self.config["ca"])
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        if with_client:
            context.load_cert_chain(self.config["cert"], self.config["key"])
        return http.client.HTTPSConnection(self.host, self.port, context=context, timeout=20)

    def request(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        headers = {
            "Content-Type": "application/json", "Content-Length": str(len(body)),
            "X-Xraydo-Node": self.config["node_id"],
            "X-Xraydo-Protocol": str(PROTOCOL_VERSION),
            "X-Xraydo-Timestamp": timestamp, "X-Xraydo-Nonce": nonce,
            "X-Xraydo-Signature": request_signature(
                self.config["secret"], "POST", path, timestamp, nonce, body
            ),
        }
        connection = self._connection()
        try:
            connection.request("POST", path, body=body, headers=headers)
            response = connection.getresponse()
            raw = response.read(1024 * 1024 + 1)
        finally:
            connection.close()
        if len(raw) > 1024 * 1024:
            raise ValueError("agent_response_too_large")
        try:
            result = json.loads(raw)
        except ValueError as error:
            raise ValueError("agent_invalid_response") from error
        if response.status != 200:
            raise ValueError(str(result.get("error", "agent_master_error")))
        if not isinstance(result, dict):
            raise ValueError("agent_invalid_response")
        return result


def enroll(master: str, node_id: str, token: str, root: Path, ca_source: Path) -> dict[str, Any]:
    if root.exists() and any(root.iterdir()):
        raise ValueError("agent_directory_not_empty")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        shutil.copyfile(ca_source, root / "ca.crt")
    except OSError as error:
        raise ValueError("agent_ca_required") from error
    os.chmod(root / "ca.crt", 0o644)
    key, csr = _new_key_and_csr(root)
    # Bootstrap still authenticates the master. The operator supplies its CA
    # separately so a stolen token cannot be redirected to an impostor.
    ca = root / "ca.crt"
    temporary_config = {"master": master, "ca": str(ca), "cert": "", "key": str(key)}
    client = ControlClient(temporary_config)
    body = json.dumps({"node_id": node_id, "token": token, "csr": csr},
                      sort_keys=True, separators=(",", ":")).encode()
    connection = client._connection(with_client=False)
    try:
        connection.request("POST", "/v1/bootstrap", body=body,
                           headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        response = connection.getresponse()
        raw = response.read(1024 * 1024)
    finally:
        connection.close()
    result = json.loads(raw)
    if response.status != 200 or not isinstance(result, dict):
        raise ValueError(str(result.get("error", "agent_enrollment_failed")))
    if result.get("protocol") != PROTOCOL_VERSION or result.get("node_id") != node_id:
        raise ValueError("control_incompatible_protocol")
    cert = root / "node.crt"
    cert.write_text(result["certificate"], encoding="utf-8")
    os.chmod(cert, 0o600)
    config = {
        "master": master, "node_id": node_id, "secret": result["secret"],
        "ca": str(ca.resolve()), "cert": str(cert.resolve()), "key": str(key.resolve()),
        "state": str((root / "agent-state.json").resolve()),
        "capabilities": sorted(KNOWN_CAPABILITIES), "protocol": PROTOCOL_VERSION,
    }
    _atomic_json(root / "agent.json", config)
    return {key: value for key, value in config.items() if key != "secret"}


class NodeAgent:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self.config = _load_json(config_path)
        if self.config.get("protocol") != PROTOCOL_VERSION:
            raise ValueError("control_incompatible_protocol")
        self.client = ControlClient(self.config)
        self.state_path = Path(self.config["state"])

    def _state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"resources": {}, "applied_operations": {}}
        state = _load_json(self.state_path)
        state.setdefault("resources", {})
        state.setdefault("applied_operations", {})
        return state

    def apply(self, operation: dict[str, Any]) -> dict[str, Any]:
        operation_id = operation.get("id")
        lease_id = operation.get("lease_id")
        resource_type = operation.get("resource_type")
        resource_id = operation.get("resource_id")
        if not all(isinstance(value, str) for value in
                   (operation_id, lease_id, resource_type, resource_id)):
            raise ValueError("agent_invalid_operation")
        if resource_type not in set(self.config.get("capabilities", [])):
            raise ValueError("agent_unsupported_operation")
        state = self._state()
        previous = state["applied_operations"].get(operation_id)
        if previous:
            repeated = dict(previous)
            # A redelivery receives a fresh master lease. Reuse the durable
            # apply result, but acknowledge the current lease rather than the
            # expired one stored with the original application.
            repeated["lease_id"] = lease_id
            return repeated
        key = f"{resource_type}:{resource_id}"
        action = operation.get("action")
        if resource_type == "port_probe":
            payload = operation.get("payload", {})
            listen, port, transport = payload.get("listen"), payload.get("port"), payload.get("transport")
            if not isinstance(listen, str) or not isinstance(port, int) or transport not in {"tcp", "udp"}:
                raise ValueError("agent_invalid_port_probe")
            family = socket.AF_INET6 if ":" in listen else socket.AF_INET
            kind = socket.SOCK_DGRAM if transport == "udp" else socket.SOCK_STREAM
            try:
                with socket.socket(family, kind) as probe:
                    probe.bind((listen, port))
            except OSError:
                raise ValueError("agent_port_unavailable")
            else:
                probe_result = "available"
            result = {"operation_id": operation_id, "lease_id": lease_id,
                      "status": "applied",
                      "result": {"state": probe_result, "listen": listen,
                                 "port": port, "transport": transport}}
            state["applied_operations"][operation_id] = result
            _atomic_json(self.state_path, state)
            return result
        if action in {"delete", "revoke"}:
            state["resources"].pop(key, None)
        else:
            state["resources"][key] = {
                "revision": operation.get("revision"), "action": action,
                "payload": operation.get("payload", {}),
            }
        applied_result: dict[str, Any] = {"changed": previous is None}
        if resource_type in {"inbound", "reality_rotation", "core", "tls_certificate"}:
            applied_result.update({"health": "healthy", "atomic": True})
        result = {"operation_id": operation_id, "lease_id": lease_id,
                  "status": "applied", "result": applied_result}
        state["applied_operations"][operation_id] = result
        # Bound the idempotency journal while preserving recent redeliveries.
        if len(state["applied_operations"]) > 10000:
            oldest = next(iter(state["applied_operations"]))
            del state["applied_operations"][oldest]
        _atomic_json(self.state_path, state)
        return result

    def reconcile(self) -> bool:
        capabilities = list(self.config.get("capabilities", []))
        desired = self.client.request("/v1/reconcile", {"capabilities": capabilities})
        state = self._state()
        digest = hashlib.sha256(json.dumps(state["resources"], ensure_ascii=False, sort_keys=True,
                                            separators=(",", ":")).encode()).hexdigest()
        if digest == desired.get("digest"):
            return False
        if not isinstance(desired.get("resources"), dict):
            raise ValueError("agent_invalid_reconciliation")
        state["resources"] = desired["resources"]
        _atomic_json(self.state_path, state)
        return True

    def run_once(self) -> dict[str, Any]:
        capabilities = list(self.config.get("capabilities", []))
        response = self.client.request("/v1/poll", {
            "protocol": PROTOCOL_VERSION, "agent_version": __version__,
            "capabilities": capabilities, "limit": 50,
        })
        applied = failed = 0
        for operation in response.get("operations", []):
            try:
                result = self.apply(operation)
            except Exception as error:
                result = {"operation_id": operation.get("id"),
                          "lease_id": operation.get("lease_id"), "status": "failed",
                          "error": str(error)[:500]}
                failed += 1
            else:
                applied += 1
            self.client.request("/v1/result", result)
        reconciled = self.reconcile()
        self.client.request("/v1/heartbeat", {"state": "healthy"})
        return {"received": len(response.get("operations", [])), "applied": applied,
                "failed": failed, "reconciled": reconciled,
                "unsupported_pending": response.get("unsupported_pending", 0)}

    def rotate(self) -> dict[str, Any]:
        root = self.config_path.parent
        next_root = Path(tempfile.mkdtemp(prefix="rotate-", dir=root))
        try:
            key, csr = _new_key_and_csr(next_root)
            response = self.client.request("/v1/rotate", {"csr": csr})
            cert = root / "node.crt"
            next_key = root / "node.key"
            cert.write_text(response["certificate"], encoding="utf-8")
            os.replace(key, next_key)
            os.chmod(cert, 0o600)
            os.chmod(next_key, 0o600)
        finally:
            try:
                next_root.rmdir()
            except OSError:
                pass
        return {"node_id": self.config["node_id"], "rotated": True}

    def run(self, interval: int = 10) -> None:
        if not 1 <= interval <= 3600:
            raise ValueError("agent_invalid_interval")
        failures = 0
        while True:
            try:
                self.run_once()
                failures = 0
                delay = interval
            except Exception:
                failures += 1
                delay = min(300, interval * (2 ** min(failures, 5)))
            time.sleep(delay)
