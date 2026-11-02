from __future__ import annotations

import concurrent.futures
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import ssl
import struct
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import quote, urlencode

from .access_keys import protocol_credentials
from .store import Store

FIXED_FINGERPRINTS = ("firefox", "chrome", "safari", "ios", "android", "edge", "360", "qq")
RANDOM_FINGERPRINTS = ("random", "randomized", "randomizednoalpn")
FINGERPRINTS = FIXED_FINGERPRINTS + RANDOM_FINGERPRINTS + ("unsafe",)
REALITY_FINGERPRINTS = FIXED_FINGERPRINTS + RANDOM_FINGERPRINTS
MAX_TASKS = 512
MAX_CIDR_ADDRESSES = 256
MAX_CONCURRENCY = 32
MAX_TIMEOUT = 10.0
DEFAULT_DISCOVERY_TIMEOUT = 4.0
DEFAULT_REALITY_TARGET = "ya.ru"


class RealityError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blocked(address: str) -> bool:
    parsed = ipaddress.ip_address(address)
    return bool(parsed.is_private or parsed.is_loopback or parsed.is_link_local
                or parsed.is_unspecified or parsed.is_multicast or parsed.is_reserved)


def _resolve(host: str, port: int, allow_private: bool = False) -> list[str]:
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except OSError as error:
        raise RealityError("dns") from error
    if not addresses:
        raise RealityError("dns")
    if any(_blocked(item) for item in addresses) and not allow_private:
        raise RealityError("reality_private_target")
    # Prefer IPv4 on hosts without a working IPv6 route, but retain every
    # public address so a failed edge can be skipped during the TLS probe.
    return sorted(addresses, key=lambda value: (
        ipaddress.ip_address(value).version,
        int(ipaddress.ip_address(value)),
    ))


def _proxy_header(raw: socket.socket, version: int) -> bytes:
    source, destination = raw.getsockname(), raw.getpeername()
    source_ip, source_port = source[0], int(source[1])
    destination_ip, destination_port = destination[0], int(destination[1])
    family = socket.AF_INET6 if ":" in source_ip else socket.AF_INET
    if version == 1:
        protocol = "TCP6" if family == socket.AF_INET6 else "TCP4"
        return ("PROXY %s %s %s %d %d\r\n" % (
            protocol, source_ip, destination_ip, source_port, destination_port)).encode("ascii")
    if version == 2:
        packed = socket.inet_pton(family, source_ip) + socket.inet_pton(family, destination_ip)
        packed += struct.pack("!HH", source_port, destination_port)
        family_protocol = 0x21 if family == socket.AF_INET6 else 0x11
        return b"\r\n\r\n\x00\r\nQUIT\n" + bytes((0x21, family_protocol)) + struct.pack("!H", len(packed)) + packed
    return b""


def _name(parts: Any) -> str:
    return ", ".join("%s=%s" % (key, value) for group in (parts or ()) for key, value in group)


def _decode_certificate(der: bytes) -> dict[str, Any]:
    descriptor, filename = tempfile.mkstemp(prefix="xraydo-cert-", suffix=".pem")
    try:
        with os.fdopen(descriptor, "w", encoding="ascii") as handle:
            handle.write(ssl.DER_cert_to_PEM_cert(der))
        decoded = ssl._ssl._test_decode_cert(filename)  # type: ignore[attr-defined]
    finally:
        Path(filename).unlink(missing_ok=True)
    return {
        "issuer": _name(decoded.get("issuer")),
        "subject": _name(decoded.get("subject")),
        "sans": sorted({value for kind, value in decoded.get("subjectAltName", ()) if kind == "DNS"}),
        "not_after": decoded.get("notAfter"),
    }


def _chain_metrics(connection: ssl.SSLSocket, leaf: bytes) -> tuple[int, int]:
    ssl_object = getattr(connection, "_sslobj", None)
    getter = getattr(ssl_object, "get_unverified_chain", None)
    try:
        chain = getter() if getter else None
    except (ValueError, ssl.SSLError):
        chain = None
    if not chain:
        return 1, len(leaf)
    sizes = []
    for certificate in chain:
        try:
            data = certificate.public_bytes()
            sizes.append(len(data.encode("ascii") if isinstance(data, str) else data))
        except (AttributeError, TypeError, ValueError):
            pass
    return len(chain), sum(sizes) if sizes else len(leaf)


def _tls_probe(address: str, port: int, sni: Optional[str], timeout: float,
               xver: int, verify: bool) -> dict[str, Any]:
    context = ssl.create_default_context() if verify else ssl._create_unverified_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols(["h2"])
    try:
        context.set_ecdh_curve("X25519")
    except (AttributeError, ValueError, ssl.SSLError) as error:
        raise RealityError("curve") from error
    started = time.monotonic()
    try:
        with socket.create_connection((address, port), timeout=timeout) as raw:
            raw.settimeout(timeout)
            if xver:
                raw.sendall(_proxy_header(raw, xver))
            with context.wrap_socket(raw, server_hostname=sni if verify else None) as connection:
                der = connection.getpeercert(binary_form=True)
                count, size = _chain_metrics(connection, der)
                return {"tls_version": connection.version(), "alpn": connection.selected_alpn_protocol(),
                        "curve": "X25519", "chain_certificates": count, "chain_size_bytes": size,
                        "latency_ms": round((time.monotonic() - started) * 1000, 1),
                        **_decode_certificate(der)}
    except socket.timeout as error:
        raise RealityError("timeout") from error
    except ssl.SSLCertVerificationError as error:
        raise RealityError("sni" if "hostname" in str(error).lower() else "certificate") from error
    except ssl.SSLError as error:
        raise RealityError("alpn" if "alpn" in str(error).lower() else "tls") from error
    except OSError as error:
        raise RealityError("tls") from error


def _non_wildcard_sans(values: Iterable[str]) -> list[str]:
    return sorted({value.rstrip(".").lower() for value in values if value and "*" not in value})


def scan_target(host: str, port: int = 443, timeout: float = MAX_TIMEOUT, *,
                sni: Optional[str] = None, xver: int = 0, allow_private: bool = False,
                node_id: str = "local") -> dict[str, Any]:
    host = host.strip().strip("[]")
    if not host or not 1 <= port <= 65535 or xver not in (0, 1, 2) or not 0 < timeout <= MAX_TIMEOUT:
        raise RealityError("invalid_target")
    addresses = _resolve(host, port, allow_private)
    selected_sni = sni.rstrip(".").lower() if sni else None
    address = addresses[0]

    def probe_addresses(probe_sni: Optional[str], verify: bool) -> tuple[str, dict[str, Any]]:
        deadline = time.monotonic() + timeout
        last_error: Optional[RealityError] = None
        for candidate in addresses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                return candidate, _tls_probe(
                    candidate, port, probe_sni, remaining, xver, verify
                )
            except RealityError as error:
                last_error = error
        if last_error is not None:
            raise last_error
        raise RealityError("timeout")

    if selected_sni is None:
        try:
            ipaddress.ip_address(host)
        except ValueError:
            selected_sni = host.rstrip(".").lower()
        else:
            address, initial = probe_addresses(None, False)
            candidates = _non_wildcard_sans(initial.get("sans", []))
            if not candidates:
                raise RealityError("sni")
            selected_sni = candidates[0]
    address, result = probe_addresses(selected_sni, True)
    if result.get("tls_version") != "TLSv1.3":
        raise RealityError("tls_version")
    if result.get("alpn") != "h2":
        raise RealityError("alpn")
    if result.get("curve") != "X25519":
        raise RealityError("curve")
    sans = result.get("sans", [])
    if selected_sni not in _non_wildcard_sans(sans) and not any(
            item.startswith("*.") and selected_sni.endswith(item[1:]) for item in sans):
        raise RealityError("sni")
    return {"target": "%s:%d" % (host, port), "host": host, "resolved_ip": address,
            "resolved_ips": addresses, "port": port, "server_name": selected_sni, "sni": selected_sni,
            "xver": xver, "node_id": node_id, "suitable": True, "reason": "verified",
            "checked_at": _now(), **result}


def scan_result(*args: Any, **kwargs: Any) -> dict[str, Any]:
    host = str(args[0] if args else kwargs.get("host", ""))
    port = int(args[1] if len(args) > 1 else kwargs.get("port", 443))
    try:
        return scan_target(*args, **kwargs)
    except RealityError as error:
        sni, node_id = kwargs.get("sni"), kwargs.get("node_id", "local")
        reason = "blocked_address" if str(error) == "reality_private_target" else str(error)
        return {"target": "%s:%d" % (host, port), "host": host, "port": port, "sni": sni,
                "server_name": sni, "node_id": node_id, "suitable": False,
                "reason": reason, "checked_at": _now()}


def _split_target(value: str, default_port: int = 443) -> tuple[str, int]:
    value = value.strip()
    if value.startswith("["):
        match = re.fullmatch(r"\[([^]]+)\](?::(\d+))?", value)
        if not match:
            raise RealityError("invalid_target")
        return match.group(1), int(match.group(2) or default_port)
    if value.count(":") == 1 and value.rsplit(":", 1)[1].isdigit():
        host, raw_port = value.rsplit(":", 1)
        return host, int(raw_port)
    if not value:
        raise RealityError("invalid_target")
    return value, default_port


def expand_targets(values: Iterable[str], default_port: int = 443, *,
                   max_tasks: int = MAX_TASKS,
                   max_cidr_addresses: int = MAX_CIDR_ADDRESSES) -> list[tuple[str, int]]:
    if not 1 <= max_tasks <= MAX_TASKS or not 1 <= max_cidr_addresses <= MAX_CIDR_ADDRESSES:
        raise RealityError("scan_limit")
    targets = []
    for raw in values:
        host, port = _split_target(raw, default_port)
        try:
            network = ipaddress.ip_network(host, strict=False)
        except ValueError:
            targets.append((host, port)); continue
        if not network.is_global:
            raise RealityError("blocked_address")
        if network.num_addresses > max_cidr_addresses:
            raise RealityError("cidr_limit")
        for address in network.hosts():
            if _blocked(str(address)):
                raise RealityError("blocked_address")
            targets.append((str(address), port))
    unique = list(dict.fromkeys(targets))
    if len(unique) > max_tasks:
        raise RealityError("task_limit")
    return unique


def discover_targets(values: Iterable[str], *, sni: Optional[str] = None, xver: int = 0,
                     timeout: float = DEFAULT_DISCOVERY_TIMEOUT, concurrency: int = MAX_CONCURRENCY,
                     node_id: str = "local", max_tasks: int = MAX_TASKS,
                     max_cidr_addresses: int = MAX_CIDR_ADDRESSES) -> dict[str, Any]:
    if not 1 <= concurrency <= MAX_CONCURRENCY or not 0 < timeout <= MAX_TIMEOUT:
        raise RealityError("scan_limit")
    targets = expand_targets(values, max_tasks=max_tasks, max_cidr_addresses=max_cidr_addresses)
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(scan_result, host, port, timeout, sni=sni, xver=xver, node_id=node_id)
                   for host, port in targets]
        results = [future.result() for future in futures]
    best: dict[tuple[Any, ...], dict[str, Any]] = {}
    for result in results:
        key = (result["target"], result.get("sni"))
        current = best.get(key)
        score = (result.get("suitable", True), -float(result.get("latency_ms", 1e12)))
        old_score = ((current or {}).get("suitable", False), -float((current or {}).get("latency_ms", 1e12)))
        if current is None or score > old_score:
            best[key] = result
    ordered = sorted(best.values(), key=lambda item: (not item.get("suitable", True), float(item.get("latency_ms", 1e12))))
    return {"node_id": node_id, "recommended": [item for item in ordered if item.get("suitable", True)],
            "rejected": [item for item in ordered if not item.get("suitable", True)],
            "limits": {"tasks": max_tasks, "cidr_addresses": max_cidr_addresses,
                       "concurrency": concurrency, "timeout": timeout}}


def profile_target() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    candidates = json.loads(((Path(__file__).with_name("data") / "reality_targets.json").read_text(encoding="utf-8")))
    report = discover_targets(["%s:%s" % (item["host"], item.get("port", 443)) for item in candidates])
    if not report["recommended"]:
        raise RealityError("no_verified_target")
    selected = next(
        (
            item for item in report["recommended"]
            if (item.get("host") or _split_target(item["target"])[0])
            == DEFAULT_REALITY_TARGET
        ),
        report["recommended"][0],
    )
    alternatives = [item for item in report["recommended"] if item is not selected]
    selected["verified_alternatives"] = alternatives
    selected["failed_candidates"] = report["rejected"]
    return selected, alternatives


def fingerprint_profiles(binary: Optional[str] = None) -> dict[str, Any]:
    version = None
    if binary:
        try:
            process = subprocess.run([binary, "version"], text=True, capture_output=True, timeout=5, check=False)
            lines = (process.stdout or process.stderr).splitlines()
            version = lines[0].strip() or None if lines else None
        except (OSError, subprocess.TimeoutExpired):
            pass
    explanations = {"random": "random fixed browser profile per connection",
                    "randomized": "randomized ClientHello with ALPN",
                    "randomizednoalpn": "randomized ClientHello without ALPN; may be incompatible",
                    "unsafe": "not supported by REALITY"}
    def supported(name: str) -> bool:
        if name == "unsafe":
            return False
        if not binary or not version:
            return True
        config = {"inbounds": [{"listen": "127.0.0.1", "port": 10801, "protocol": "dokodemo-door",
                                "settings": {"address": "127.0.0.1"}}],
                  "outbounds": [{"protocol": "vless", "settings": {"vnext": [{"address": "example.com",
                    "port": 443, "users": [{"id": "11111111-1111-4111-8111-111111111111",
                                                "encryption": "none"}]}]},
                    "streamSettings": {"security": "reality", "realitySettings": {
                        "serverName": "example.com", "fingerprint": name,
                        "publicKey": "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB", "shortId": "00"}}}]}
        descriptor, filename = tempfile.mkstemp(prefix="xraydo-utls-", suffix=".json")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(config, handle)
            process = subprocess.run([binary, "run", "-test", "-format", "json", "-c", filename],
                                     text=True, capture_output=True, timeout=5, check=False)
            return process.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
        finally:
            Path(filename).unlink(missing_ok=True)
    profiles = [{"name": name, "group": "randomization" if name in RANDOM_FINGERPRINTS else "fixed",
                 "recommended": name == "firefox", "supported": supported(name),
                 "explanation": explanations.get(name, "fixed browser profile")} for name in FINGERPRINTS]
    return {"xray_version": version, "profiles": profiles}


def generate_x25519(binary: str) -> tuple[str, str]:
    try:
        result = subprocess.run([binary, "x25519"], text=True, capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RealityError("xray_keygen_failed") from error
    private = re.search(r"^PrivateKey:\s*(\S+)", result.stdout, re.MULTILINE)
    public = re.search(r"^Password \(PublicKey\):\s*(\S+)", result.stdout, re.MULTILINE)
    if result.returncode != 0 or not private or not public:
        raise RealityError("xray_keygen_failed")
    return private.group(1), public.group(1)


def build_server_config(*, user_id: str, stats_id: str, listen_port: int, target: str,
                        server_name: str, private_key: str, short_id: str, xver: int = 0) -> dict[str, Any]:
    return {"log": {"loglevel": "warning"}, "inbounds": [{"tag": "vless-reality",
        "listen": "0.0.0.0", "port": listen_port, "protocol": "vless",
        "settings": {"clients": [{"id": user_id, "email": stats_id, "flow": "xtls-rprx-vision"}], "decryption": "none"},
        "streamSettings": {"network": "tcp", "security": "reality", "realitySettings": {
            "show": False, "target": target, "xver": xver, "serverNames": [server_name],
            "privateKey": private_key, "shortIds": [short_id]}},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"], "routeOnly": True}}],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}, {"tag": "blocked", "protocol": "blackhole"}]}


def validate_and_install(binary: str, config: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".config-", suffix=".json", dir=destination.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2); handle.write("\n"); handle.flush(); os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        try:
            os.chown(temporary, -1, destination.parent.stat().st_gid)
        except PermissionError:
            pass
        result = subprocess.run([binary, "run", "-test", "-format", "json", "-c", str(temporary)], text=True,
                                capture_output=True, check=False, timeout=15)
        if result.returncode != 0:
            raise RealityError("xray_config_invalid")
        if destination.exists():
            shutil.copy2(destination, destination.with_suffix(".json.previous"))
        os.replace(temporary, destination)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RealityError("xray_config_invalid") from error
    finally:
        temporary.unlink(missing_ok=True)


def _audit(store: Optional[Store], action: str, node_id: str, target: str, fields: list[str]) -> None:
    if store is None:
        return
    data = store.load()
    data.setdefault("audit_log", []).append({"id": str(uuid.uuid4()), "timestamp": _now(),
        "actor": "local-admin", "object_type": "reality_target", "object_id": "%s:%s" % (node_id, target),
        "action": action, "fields": fields})
    store.save(data)


def _record_check(store: Optional[Store], result: dict[str, Any]) -> None:
    if store is None:
        return
    data = store.load(); checks = data.setdefault("reality_checks", [])
    checks.append({key: result.get(key) for key in ("checked_at", "node_id", "target", "resolved_ip", "latency_ms", "suitable", "reason")})
    del checks[:-200]; store.save(data)


def _vless_uri(user_id: str, server: str, port: int, public_key: str, fingerprint: str,
               sni: str, short_id: str, spider_x: str, remark: str) -> str:
    query = urlencode({"type": "tcp", "security": "reality", "pbk": public_key, "fp": fingerprint,
                       "sni": sni, "sid": short_id, "spx": spider_x, "flow": "xtls-rprx-vision"})
    return "vless://%s@%s:%d?%s#%s" % (user_id, server, port, query, quote(remark))


def configure_reality(*, binary: str, destination: Path, user: dict[str, Any],
                      target_host: Optional[str], target_port: int, listen_port: int,
                      fingerprint: str, sni: Optional[str] = None, xver: int = 0,
                      server: Optional[str] = None, node_id: str = "local",
                      force_unverified_target: bool = False, force_confirmation: Optional[str] = None,
                      allow_private_single: bool = False, private_confirmation: Optional[str] = None,
                      store: Optional[Store] = None) -> dict[str, Any]:
    if fingerprint not in REALITY_FINGERPRINTS:
        raise RealityError("invalid_fingerprint")
    compatibility = next(
        item for item in fingerprint_profiles(binary)["profiles"] if item["name"] == fingerprint
    )
    if not compatibility["supported"]:
        raise RealityError("invalid_fingerprint")
    if not 1 <= listen_port <= 65535:
        raise RealityError("invalid_port")
    access_key_id = user.get("access_key_id")
    if not isinstance(access_key_id, str) or not access_key_id:
        raise RealityError("reality_access_key_id_required")
    credentials = protocol_credentials(user, "xray_reality")
    if not destination.exists():
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("0.0.0.0", listen_port))
        except OSError as error:
            raise RealityError("reality_port_busy") from error
    target_host = target_host or DEFAULT_REALITY_TARGET
    sni = sni or target_host
    exact = "%s/%s" % (target_host, sni)
    if allow_private_single and private_confirmation != exact:
        raise RealityError("private_confirmation_required")
    scan = scan_result(target_host, target_port, MAX_TIMEOUT, sni=sni, xver=xver,
                       allow_private=allow_private_single, node_id=node_id)
    _record_check(store, scan)
    if allow_private_single:
        _audit(store, "reality.private_target_allowed", node_id, exact, ["target", "sni"])
    if not scan.get("suitable", True):
        if scan.get("reason") == "blocked_address":
            raise RealityError("blocked_address")
        if not force_unverified_target or force_confirmation != exact:
            raise RealityError("unverified_target")
        _audit(store, "reality.force_unverified", node_id, exact, ["target", "sni", "reason"])
        scan.update({"host": target_host, "port": target_port, "target": "%s:%d" % (target_host, target_port),
                     "server_name": sni, "sni": sni})
    private_key, public_key = generate_x25519(binary)
    short_id, spider_x = secrets.token_hex(8), "/" + secrets.token_urlsafe(8)
    config = build_server_config(user_id=credentials["xray_uuid"], stats_id="access-key:%s" % access_key_id,
        listen_port=listen_port, target=scan["target"], server_name=scan["server_name"],
        private_key=private_key, short_id=short_id, xver=xver)
    validate_and_install(binary, config, destination)
    state, health = ("verified" if scan.get("suitable", True) else "unverified"), {"state": "not_required"}
    if state == "unverified":
        health_result = scan_result(scan["host"], scan["port"], MAX_TIMEOUT, sni=scan["server_name"], xver=xver,
                                    allow_private=allow_private_single, node_id=node_id)
        failed = not health_result.get("suitable", False)
        health = {"state": "failed" if failed else "healthy", "reason": health_result.get("reason"),
                  "rollback_available": failed, "previous_config": str(destination.with_suffix(".json.previous"))}
    connect_server = server or node_id
    return {"config": str(destination), "listen_port": listen_port, "target": scan,
        "public_key": public_key, "short_id": short_id, "fingerprint": fingerprint, "spider_x": spider_x,
        "stats_id": "access-key:%s" % access_key_id, "node_id": node_id, "verification_state": state,
        "health_check": health, "uri": _vless_uri(credentials["xray_uuid"], connect_server, listen_port,
            public_key, fingerprint, scan["server_name"], short_id, spider_x, user.get("name", "REALITY"))}
