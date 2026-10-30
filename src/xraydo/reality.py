from __future__ import annotations

import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

FINGERPRINTS = (
    "firefox",
    "chrome",
    "safari",
    "ios",
    "android",
    "edge",
    "360",
    "qq",
    "random",
    "randomized",
    "randomizednoalpn",
)


class RealityError(ValueError):
    pass


def _public_addresses(host: str, port: int) -> list[str]:
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, port)})
    except OSError as error:
        raise RealityError("reality_dns_failed") from error
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if parsed.is_private or parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified:
            raise RealityError("reality_private_target")
    return addresses


def scan_target(host: str, port: int = 443, timeout: float = 10.0) -> dict[str, Any]:
    if not host or not 1 <= port <= 65535:
        raise RealityError("reality_invalid_target")
    addresses = _public_addresses(host, port)
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2"])
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as connection:
                certificate = connection.getpeercert()
                tls_version = connection.version()
                alpn = connection.selected_alpn_protocol()
    except (OSError, ssl.SSLError) as error:
        raise RealityError("reality_tls_failed") from error
    if tls_version != "TLSv1.3":
        raise RealityError("reality_tls13_required")
    if alpn != "h2":
        raise RealityError("reality_h2_required")
    sans = sorted(value for kind, value in certificate.get("subjectAltName", ()) if kind == "DNS")
    return {
        "target": f"{host}:{port}",
        "server_name": host,
        "resolved_ips": addresses,
        "tls_version": tls_version,
        "alpn": alpn,
        "sans": sans,
        "latency_ms": round((time.monotonic() - started) * 1000, 1),
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def profile_target() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates_path = Path(__file__).with_name("data") / "reality_targets.json"
    candidates = json.loads(candidates_path.read_text(encoding="utf-8"))
    successes: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    for candidate in candidates:
        try:
            successes.append(scan_target(candidate["host"], int(candidate.get("port", 443)), 4.0))
        except RealityError as error:
            failures.append(
                {
                    "target": f"{candidate['host']}:{candidate.get('port', 443)}",
                    "reason": str(error),
                }
            )
    if not successes:
        raise RealityError("reality_no_verified_target")
    # Keep the documented first candidate when it passes; alternatives are
    # ranked by measured latency for diagnostics and future interactive choice.
    selected = successes[0]
    alternatives = sorted(successes[1:], key=lambda item: item["latency_ms"])
    selected["verified_alternatives"] = alternatives
    selected["failed_candidates"] = failures
    return selected, alternatives


def generate_x25519(binary: str) -> tuple[str, str]:
    try:
        result = subprocess.run(
            [binary, "x25519"], text=True, capture_output=True, check=False, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RealityError("xray_keygen_failed") from error
    if result.returncode != 0:
        raise RealityError("xray_keygen_failed")
    private = re.search(r"^PrivateKey:\s*(\S+)", result.stdout, re.MULTILINE)
    public = re.search(r"^Password \(PublicKey\):\s*(\S+)", result.stdout, re.MULTILINE)
    if not private or not public:
        raise RealityError("xray_keygen_failed")
    return private.group(1), public.group(1)


def build_server_config(
    *,
    user_id: str,
    user_name: str,
    listen_port: int,
    target: str,
    server_name: str,
    private_key: str,
    short_id: str,
) -> dict[str, Any]:
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [
            {
                "tag": "vless-reality",
                "listen": "0.0.0.0",
                "port": listen_port,
                "protocol": "vless",
                "settings": {
                    "clients": [
                        {
                            "id": user_id,
                            "email": user_name,
                            "flow": "xtls-rprx-vision",
                        }
                    ],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "target": target,
                        "xver": 0,
                        "serverNames": [server_name],
                        "privateKey": private_key,
                        "shortIds": [short_id],
                    },
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            }
        ],
        "outbounds": [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "blocked", "protocol": "blackhole"},
        ],
    }


def validate_and_install(binary: str, config: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        try:
            os.chown(temporary, -1, destination.parent.stat().st_gid)
        except PermissionError:
            pass
        result = subprocess.run(
            [binary, "run", "-test", "-format", "json", "-c", str(temporary)],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
        if result.returncode != 0:
            raise RealityError("xray_config_invalid")
        if destination.exists():
            shutil.copy2(destination, destination.with_suffix(".json.previous"))
        os.replace(temporary, destination)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RealityError("xray_config_invalid") from error
    finally:
        temporary.unlink(missing_ok=True)


def configure_reality(
    *,
    binary: str,
    destination: Path,
    user: dict[str, Any],
    target_host: Optional[str],
    target_port: int,
    listen_port: int,
    fingerprint: str,
) -> dict[str, Any]:
    if fingerprint not in FINGERPRINTS:
        raise RealityError("reality_invalid_fingerprint")
    if not 1 <= listen_port <= 65535:
        raise RealityError("reality_invalid_port")
    if not destination.exists():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("0.0.0.0", listen_port))
        except OSError as error:
            raise RealityError("reality_port_busy") from error
    if target_host is None:
        scan, _alternatives = profile_target()
    else:
        scan = scan_target(target_host, target_port)
    private_key, public_key = generate_x25519(binary)
    short_id = secrets.token_hex(8)
    spider_x = "/" + secrets.token_urlsafe(8)
    config = build_server_config(
        user_id=user["credentials"]["xray_uuid"],
        user_name=user["name"],
        listen_port=listen_port,
        target=scan["target"],
        server_name=scan["server_name"],
        private_key=private_key,
        short_id=short_id,
    )
    validate_and_install(binary, config, destination)
    return {
        "config": str(destination),
        "listen_port": listen_port,
        "target": scan,
        "public_key": public_key,
        "short_id": short_id,
        "fingerprint": fingerprint,
        "spider_x": spider_x,
    }
