from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import ssl
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Mapping, Optional

from .nodes import NodeService
from .store import Store
from .sync_queue import SyncQueueService


PROTOCOL_VERSION = 1
MAX_MESSAGE_BYTES = 1024 * 1024
MAX_CLOCK_SKEW_SECONDS = 120
NONCE_LIFETIME_SECONDS = 300
KNOWN_CAPABILITIES = {"access_key", "hysteria_user", "telemt_user"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _run(command: list[str]) -> None:
    try:
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as error:
        raise ValueError("control_openssl_missing") from error
    except subprocess.CalledProcessError as error:
        raise ValueError("control_pki_failed") from error


def _fingerprint_pem(pem: str) -> str:
    der = ssl.PEM_cert_to_DER_cert(pem)
    return hashlib.sha256(der).hexdigest()


def _fingerprint_der(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def request_signature(
    secret: str, method: str, path: str, timestamp: str, nonce: str, body: bytes
) -> str:
    digest = hashlib.sha256(body).hexdigest()
    message = "\n".join((method.upper(), path, timestamp, nonce, digest)).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class ControlPKI:
    """Private CA dedicated to the node control plane."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.ca_key = self.root / "ca.key"
        self.ca_cert = self.root / "ca.crt"
        self.server_key = self.root / "server.key"
        self.server_cert = self.root / "server.crt"

    def initialize(self, public_host: str, days: int = 365) -> dict[str, str]:
        if not 1 <= days <= 825:
            raise ValueError("control_invalid_days")
        if not re.fullmatch(r"[A-Za-z0-9.:-]{1,253}", public_host):
            raise ValueError("control_invalid_host")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.root, 0o700)
        if not self.ca_key.exists():
            config = self.root / "ca.cnf"
            config.write_text(
                "[req]\nprompt=no\ndistinguished_name=dn\nx509_extensions=ca\n"
                "[dn]\nCN=xraydo control plane CA\n[ca]\n"
                "basicConstraints=critical,CA:true,pathlen:0\n"
                "keyUsage=critical,keyCertSign,cRLSign\nsubjectKeyIdentifier=hash\n",
                encoding="utf-8",
            )
            _run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt",
                  "rsa_keygen_bits:3072", "-out", str(self.ca_key)])
            _run(["openssl", "req", "-new", "-x509", "-sha256", "-days", "3650",
                  "-key", str(self.ca_key), "-config", str(config), "-out", str(self.ca_cert)])
            config.unlink(missing_ok=True)
        if not self.server_key.exists() or not self.server_cert.exists():
            self._issue_server(public_host, days)
        os.chmod(self.ca_key, 0o600)
        os.chmod(self.server_key, 0o600)
        return self.paths()

    def _issue_server(self, host: str, days: int) -> None:
        config = self.root / "server.cnf"
        san = f"IP:{host}" if re.fullmatch(r"[0-9a-fA-F:.]+", host) else f"DNS:{host}"
        config.write_text(
            "[req]\nprompt=no\ndistinguished_name=dn\nreq_extensions=ext\n"
            f"[dn]\nCN={host}\n[ext]\nsubjectAltName={san}\n"
            "basicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n",
            encoding="utf-8",
        )
        csr = self.root / "server.csr"
        _run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt",
              "rsa_keygen_bits:3072", "-out", str(self.server_key)])
        _run(["openssl", "req", "-new", "-key", str(self.server_key), "-config",
              str(config), "-out", str(csr)])
        _run(["openssl", "x509", "-req", "-sha256", "-days", str(days), "-in", str(csr),
              "-CA", str(self.ca_cert), "-CAkey", str(self.ca_key), "-CAcreateserial",
              "-extfile", str(config), "-extensions", "ext", "-out", str(self.server_cert)])
        csr.unlink(missing_ok=True)
        config.unlink(missing_ok=True)

    def issue_node(self, node_id: str, csr_pem: str, days: int = 30) -> str:
        if not re.fullmatch(r"[0-9a-f-]{36}", node_id) or len(csr_pem) > 65536:
            raise ValueError("control_invalid_enrollment")
        self.initialize_from_existing()
        with tempfile.TemporaryDirectory(dir=self.root) as directory:
            temporary = Path(directory)
            csr = temporary / "node.csr"
            cert = temporary / "node.crt"
            ext = temporary / "node.ext"
            csr.write_text(csr_pem, encoding="utf-8")
            ext.write_text(
                "basicConstraints=critical,CA:false\nkeyUsage=critical,digitalSignature\n"
                "extendedKeyUsage=clientAuth\n",
                encoding="utf-8",
            )
            _run(["openssl", "req", "-in", str(csr), "-noout", "-verify"])
            _run(["openssl", "x509", "-req", "-sha256", "-days", str(days), "-in", str(csr),
                  "-CA", str(self.ca_cert), "-CAkey", str(self.ca_key), "-CAcreateserial",
                  "-subj", f"/CN=xraydo-node:{node_id}", "-extfile", str(ext), "-out", str(cert)])
            return cert.read_text(encoding="utf-8")

    def initialize_from_existing(self) -> None:
        if not all(path.is_file() for path in (self.ca_key, self.ca_cert)):
            raise ValueError("control_pki_not_initialized")

    def paths(self) -> dict[str, str]:
        return {"ca": str(self.ca_cert), "cert": str(self.server_cert), "key": str(self.server_key)}


class ControlPlaneService:
    def __init__(self, store: Store, pki: Optional[ControlPKI] = None) -> None:
        self.store = store
        self.pki = pki or ControlPKI(store.data_dir / "control-pki")
        self.queue = SyncQueueService(store)

    def create_bootstrap(self, node_reference: str, ttl_seconds: int = 600) -> dict[str, Any]:
        if not 30 <= ttl_seconds <= 3600:
            raise ValueError("control_invalid_ttl")
        node = NodeService(self.store).find(node_reference)
        token = secrets.token_urlsafe(32)
        now = _now()
        data = self.store.load()
        data.setdefault("control_bootstrap_tokens", []).append({
            "id": secrets.token_hex(12), "node_id": node["id"],
            "digest": hashlib.sha256(token.encode()).hexdigest(),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
            "used_at": None,
        })
        self.store.save(data)
        return {"node_id": node["id"], "token": token,
                "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat()}

    def enroll(self, node_id: str, token: str, csr: str) -> dict[str, Any]:
        if len(token) > 256:
            raise ValueError("control_invalid_bootstrap")
        data = self.store.load()
        node = next((item for item in data.get("nodes", []) if item["id"] == node_id), None)
        if node is None or not node.get("enabled", True):
            raise ValueError("control_invalid_bootstrap")
        digest = hashlib.sha256(token.encode()).hexdigest()
        now = _now()
        record = next((item for item in data.get("control_bootstrap_tokens", [])
                       if item["node_id"] == node_id and hmac.compare_digest(item["digest"], digest)), None)
        if record is None or record.get("used_at") or datetime.fromisoformat(record["expires_at"]) < now:
            raise ValueError("control_invalid_bootstrap")
        cert = self.pki.issue_node(node_id, csr)
        secret = secrets.token_urlsafe(32)
        record["used_at"] = now.isoformat()
        for credential in data.setdefault("node_credentials", []):
            if credential["node_id"] == node_id and credential.get("status") == "active":
                credential["status"] = "revoked"
                credential["revoked_at"] = now.isoformat()
        data["node_credentials"].append({
            "node_id": node_id, "fingerprint": _fingerprint_pem(cert), "secret": secret,
            "status": "active", "issued_at": now.isoformat(), "revoked_at": None,
        })
        self.store.save(data)
        return {"protocol": PROTOCOL_VERSION, "node_id": node_id, "certificate": cert,
                "ca": self.pki.ca_cert.read_text(encoding="utf-8"), "secret": secret,
                "capabilities": sorted(KNOWN_CAPABILITIES)}

    def authenticate(
        self, method: str, path: str, headers: Mapping[str, str], body: bytes,
        peer_fingerprint: str, peer_node_id: str,
    ) -> str:
        if len(body) > MAX_MESSAGE_BYTES:
            raise ValueError("control_message_too_large")
        node_id = headers.get("X-Xraydo-Node", "")
        if node_id != peer_node_id:
            raise ValueError("control_node_mismatch")
        if headers.get("X-Xraydo-Protocol") != str(PROTOCOL_VERSION):
            raise ValueError("control_incompatible_protocol")
        timestamp = headers.get("X-Xraydo-Timestamp", "")
        nonce = headers.get("X-Xraydo-Nonce", "")
        signature = headers.get("X-Xraydo-Signature", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{20,128}", nonce):
            raise ValueError("control_invalid_signature")
        try:
            sent_at = datetime.fromtimestamp(int(timestamp), timezone.utc)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("control_invalid_signature")
        now = _now()
        if abs((now - sent_at).total_seconds()) > MAX_CLOCK_SKEW_SECONDS:
            raise ValueError("control_expired_message")
        data = self.store.load()
        node = next((item for item in data.get("nodes", []) if item["id"] == node_id), None)
        credential = next((item for item in data.get("node_credentials", [])
                           if item["node_id"] == node_id and item.get("status") == "active"
                           and hmac.compare_digest(item["fingerprint"], peer_fingerprint)), None)
        if node is None or not node.get("enabled", True) or credential is None:
            raise ValueError("control_untrusted_node")
        expected = request_signature(credential["secret"], method, path, timestamp, nonce, body)
        if not hmac.compare_digest(signature, expected):
            raise ValueError("control_invalid_signature")
        kept = []
        cutoff = now - timedelta(seconds=NONCE_LIFETIME_SECONDS)
        replay = False
        for item in data.setdefault("control_nonces", []):
            try:
                fresh = datetime.fromisoformat(item["seen_at"]) >= cutoff
            except (KeyError, TypeError, ValueError):
                fresh = False
            if fresh:
                kept.append(item)
                replay = replay or (item.get("node_id") == node_id and item.get("nonce") == nonce)
        if replay:
            raise ValueError("control_replay")
        kept.append({"node_id": node_id, "nonce": nonce, "seen_at": now.isoformat()})
        data["control_nonces"] = kept[-10000:]
        self.store.save(data)
        return node_id

    def poll(self, node_id: str, request: dict[str, Any]) -> dict[str, Any]:
        capabilities = request.get("capabilities")
        if not isinstance(capabilities, list) or not all(isinstance(item, str) for item in capabilities):
            raise ValueError("control_invalid_capabilities")
        supported = set(capabilities) & KNOWN_CAPABILITIES
        if not supported:
            raise ValueError("control_no_common_capabilities")
        limit = request.get("limit", 50)
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("sync_invalid_limit")
        data = self.store.load()
        node = next(item for item in data["nodes"] if item["id"] == node_id)
        node["agent_version"] = str(request.get("agent_version", "unknown"))[:64]
        node["capabilities"] = sorted(supported)
        node["protocol_version"] = PROTOCOL_VERSION
        node["updated_at"] = _now().isoformat()
        self.store.save(data)
        operations = self.queue.claim(node_id, limit, supported_types=supported, lease_seconds=60)
        return {"protocol": PROTOCOL_VERSION, "operations": operations,
                "unsupported_pending": self._unsupported_count(node_id, supported)}

    def _unsupported_count(self, node_id: str, supported: set[str]) -> int:
        return sum(item["node_id"] == node_id and item["state"] == "pending"
                   and item.get("resource_type") not in supported
                   for item in self.store.load().get("node_operations", []))

    def acknowledge(self, node_id: str, request: dict[str, Any]) -> dict[str, Any]:
        operation_id = request.get("operation_id")
        lease_id = request.get("lease_id")
        status = request.get("status")
        if not isinstance(operation_id, str) or not isinstance(lease_id, str):
            raise ValueError("control_invalid_ack")
        if status == "applied":
            operation = self.queue.finish(operation_id, result=request.get("result") or {},
                                          node_id=node_id, lease_id=lease_id)
        elif status == "failed":
            operation = self.queue.find(operation_id)
            delay = min(3600, 2 ** min(int(operation.get("attempts", 1)), 10))
            operation = self.queue.finish(operation_id, error=str(request.get("error", "agent failure")),
                                          node_id=node_id, lease_id=lease_id,
                                          retry_after_seconds=delay)
        else:
            raise ValueError("control_invalid_ack")
        return {"operation_id": operation["id"], "state": operation["state"]}

    def rotate(self, node_id: str, csr: str) -> dict[str, Any]:
        cert = self.pki.issue_node(node_id, csr)
        data = self.store.load()
        now = _now().isoformat()
        active = next((item for item in data.get("node_credentials", [])
                       if item["node_id"] == node_id and item.get("status") == "active"), None)
        if active is None:
            raise ValueError("control_untrusted_node")
        active["status"] = "revoked"
        active["revoked_at"] = now
        data["node_credentials"].append({
            "node_id": node_id, "fingerprint": _fingerprint_pem(cert),
            "secret": active["secret"], "status": "active", "issued_at": now, "revoked_at": None,
        })
        self.store.save(data)
        return {"certificate": cert, "ca": self.pki.ca_cert.read_text(encoding="utf-8")}

    def revoke(self, node_reference: str) -> dict[str, Any]:
        node = NodeService(self.store).find(node_reference)
        data = self.store.load()
        now = _now().isoformat()
        count = 0
        for item in data.get("node_credentials", []):
            if item["node_id"] == node["id"] and item.get("status") == "active":
                item["status"] = "revoked"
                item["revoked_at"] = now
                count += 1
        node_record = next(item for item in data["nodes"] if item["id"] == node["id"])
        node_record["enabled"] = False
        node_record["connection_state"] = "offline"
        node_record["updated_at"] = now
        self.store.save(data)
        return {"node_id": node["id"], "revoked_credentials": count, "trusted": False}

    def reconcile(self, node_id: str, capabilities: set[str]) -> dict[str, Any]:
        latest: dict[str, dict[str, Any]] = {}
        for operation in self.store.load().get("node_operations", []):
            if operation["node_id"] != node_id or operation.get("resource_type") not in capabilities:
                continue
            key = f"{operation['resource_type']}:{operation['resource_id']}"
            if key not in latest or operation["created_at"] > latest[key]["created_at"]:
                latest[key] = operation
        resources = {
            key: {"revision": item["revision"], "action": item["action"],
                  "payload": item.get("payload", {})}
            for key, item in sorted(latest.items())
            if item.get("action") not in {"delete", "revoke"}
        }
        return {"resources": resources, "digest": hashlib.sha256(_json_bytes(resources)).hexdigest()}


def _peer_node_id(handler: BaseHTTPRequestHandler) -> tuple[str, str]:
    der = handler.connection.getpeercert(binary_form=True)  # type: ignore[attr-defined]
    decoded = handler.connection.getpeercert()  # type: ignore[attr-defined]
    if not der or not decoded:
        raise ValueError("control_client_certificate_required")
    common_names = [value for group in decoded.get("subject", ()) for key, value in group
                    if key == "commonName"]
    if len(common_names) != 1 or not common_names[0].startswith("xraydo-node:"):
        raise ValueError("control_invalid_client_certificate")
    return common_names[0].split(":", 1)[1], _fingerprint_der(der)


def serve_control_plane(
    service: ControlPlaneService, listen: str, port: int, *, max_requests_per_minute: int = 120
) -> None:
    counters: dict[str, tuple[int, int]] = {}

    class Handler(BaseHTTPRequestHandler):
        server_version = "xraydo-control/1"

        def log_message(self, format: str, *args: object) -> None:
            # stderr is intentionally journal-friendly and contains no bodies.
            super().log_message(format, *args)

        def _reply(self, status: int, payload: dict[str, Any]) -> None:
            body = _json_bytes(payload)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0 or length > MAX_MESSAGE_BYTES:
                    raise ValueError("control_message_too_large")
                body = self.rfile.read(length)
                request = json.loads(body or b"{}")
                if not isinstance(request, dict):
                    raise ValueError("control_invalid_message")
                if self.path == "/v1/bootstrap":
                    result = service.enroll(str(request.get("node_id", "")),
                                            str(request.get("token", "")),
                                            str(request.get("csr", "")))
                    self._reply(200, result)
                    return
                peer_node, fingerprint = _peer_node_id(self)
                minute = int(time.time() // 60)
                bucket_minute, count = counters.get(peer_node, (minute, 0))
                count = count + 1 if bucket_minute == minute else 1
                counters[peer_node] = (minute, count)
                if count > max_requests_per_minute:
                    raise ValueError("control_rate_limited")
                node_id = service.authenticate("POST", self.path, self.headers, body,
                                               fingerprint, peer_node)
                if self.path == "/v1/poll":
                    result = service.poll(node_id, request)
                elif self.path == "/v1/result":
                    result = service.acknowledge(node_id, request)
                elif self.path == "/v1/rotate":
                    result = service.rotate(node_id, str(request.get("csr", "")))
                elif self.path == "/v1/reconcile":
                    caps = request.get("capabilities", [])
                    if not isinstance(caps, list):
                        raise ValueError("control_invalid_capabilities")
                    result = service.reconcile(node_id, set(caps) & KNOWN_CAPABILITIES)
                elif self.path == "/v1/heartbeat":
                    result = {"node": NodeService(service.store).heartbeat(
                        node_id, str(request.get("state", "healthy")),
                        error=request.get("error"), latency_ms=request.get("latency_ms"))}
                else:
                    self._reply(404, {"error": "not_found"})
                    return
                self._reply(200, result)
            except (ValueError, json.JSONDecodeError) as error:
                code = str(error)
                status = 429 if code == "control_rate_limited" else 401 if code.startswith("control_") else 400
                self._reply(status, {"error": code})
            except Exception:
                self._reply(500, {"error": "control_internal_error"})

    paths = service.pki.paths()
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(paths["cert"], paths["key"])
    context.load_verify_locations(paths["ca"])
    context.verify_mode = ssl.CERT_OPTIONAL
    server = HTTPServer((listen, port), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
