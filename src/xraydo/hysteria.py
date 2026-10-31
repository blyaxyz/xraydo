from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .access_keys import protocol_credentials


class HysteriaError(ValueError):
    pass


def _run(arguments: list[str]) -> bytes:
    try:
        result = subprocess.run(arguments, capture_output=True, check=False, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HysteriaError("hysteria_tool_failed") from error
    if result.returncode != 0:
        raise HysteriaError("hysteria_tool_failed")
    return result.stdout


def generate_private_ca(server_name: str, pki_dir: Path) -> dict[str, Path]:
    try:
        address = ipaddress.ip_address(server_name)
        san = f"IP:{address}"
    except ValueError:
        if not server_name or any(char.isspace() for char in server_name):
            raise HysteriaError("hysteria_invalid_server")
        san = f"DNS:{server_name}"
    pki_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    os.chmod(pki_dir, 0o750)
    ca_key = pki_dir / "ca.key"
    ca_cert = pki_dir / "ca.crt"
    server_key = pki_dir / "server.key"
    server_cert = pki_dir / "server.crt"
    extension = pki_dir / ".server.ext"
    extension.write_text(f"subjectAltName={san}\nextendedKeyUsage=serverAuth\n", encoding="utf-8")
    try:
        if not ca_key.exists() or not ca_cert.exists():
            _run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(ca_key)])
            _run(["openssl", "req", "-x509", "-new", "-sha256", "-days", "3650", "-key", str(ca_key), "-subj", "/CN=xraydo private CA", "-out", str(ca_cert)])
        _run(["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072", "-out", str(server_key)])
        csr = pki_dir / ".server.csr"
        _run(["openssl", "req", "-new", "-key", str(server_key), "-subj", f"/CN={server_name}", "-out", str(csr)])
        _run(["openssl", "x509", "-req", "-sha256", "-days", "90", "-in", str(csr), "-CA", str(ca_cert), "-CAkey", str(ca_key), "-CAcreateserial", "-extfile", str(extension), "-out", str(server_cert)])
    finally:
        extension.unlink(missing_ok=True)
        (pki_dir / ".server.csr").unlink(missing_ok=True)
    os.chmod(ca_key, 0o600)
    os.chmod(server_key, 0o640)
    os.chmod(ca_cert, 0o644)
    os.chmod(server_cert, 0o644)
    try:
        os.chown(server_key, -1, pki_dir.stat().st_gid)
        os.chown(server_cert, -1, pki_dir.stat().st_gid)
    except PermissionError:
        pass
    return {"ca": ca_cert, "cert": server_cert, "key": server_key}


def certificate_pin(cert: Path) -> str:
    der = _run(["openssl", "x509", "-in", str(cert), "-outform", "DER"])
    return hashlib.sha256(der).hexdigest()


def certificate_matches(cert: Path, server_name: str) -> bool:
    option = "-checkip"
    try:
        ipaddress.ip_address(server_name)
    except ValueError:
        option = "-checkhost"
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", str(cert), "-noout", option, server_name],
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def select_masquerade(explicit: Optional[str] = None) -> str:
    targets = [explicit] if explicit else json.loads(
        (Path(__file__).with_name("data") / "masquerade_targets.json").read_text(encoding="utf-8")
    )
    for target in targets:
        if not target or urllib.parse.urlparse(target).scheme != "https":
            continue
        try:
            request = urllib.request.Request(target, method="HEAD", headers={"User-Agent": "xraydo/0.1"})
            with urllib.request.urlopen(request, timeout=5) as response:
                if 200 <= response.status < 500:
                    return target
        except urllib.error.HTTPError as error:
            if error.code < 500:
                return target
        except OSError:
            continue
    raise HysteriaError("hysteria_masquerade_failed")


def _port_available(port: int, socket_type: int, listen: str = "0.0.0.0") -> bool:
    try:
        address = listen.strip("[]")
        family = socket.AF_INET6 if ipaddress.ip_address(address).version == 6 else socket.AF_INET
        with socket.socket(family, socket_type) as listener:
            listener.bind((address, port))
        return True
    except (OSError, ValueError):
        return False


def _listen_address(listen: str, port: int) -> str:
    if not listen:
        return f":{port}"
    address = listen.strip("[]")
    try:
        version = ipaddress.ip_address(address).version
    except ValueError as error:
        raise HysteriaError("hysteria_invalid_listen") from error
    return f"[{address}]:{port}" if version == 6 else f"{address}:{port}"


def build_config(
    *, port: int, cert: Path, key: Path, username: str, password: str,
    obfs_type: str, obfs_password: Optional[str], stats_port: int,
    stats_secret: str, masquerade: str, listen: str = "",
    bandwidth_up: Optional[str] = None, bandwidth_down: Optional[str] = None,
    congestion: str = "bbr", ignore_client_bandwidth: bool = True,
    disable_udp: bool = False, udp_idle_timeout: str = "60s",
    users: Optional[dict[str, str]] = None, acme: Optional[dict[str, Any]] = None,
    ech_key_path: Optional[Path] = None,
) -> dict[str, Any]:
    if congestion not in {"bbr", "reno"}:
        raise HysteriaError("hysteria_invalid_congestion")
    if not udp_idle_timeout or not re.fullmatch(r"[1-9][0-9]*(?:ms|s|m|h)", udp_idle_timeout):
        raise HysteriaError("hysteria_invalid_udp_timeout")
    credentials = dict(users or {})
    credentials[username] = password
    config: dict[str, Any] = {
        "listen": _listen_address(listen, port),
        "auth": {"type": "userpass", "userpass": credentials},
        "congestion": {"type": congestion},
        "ignoreClientBandwidth": ignore_client_bandwidth,
        "disableUDP": disable_udp,
        "udpIdleTimeout": udp_idle_timeout,
        "trafficStats": {"listen": f"127.0.0.1:{stats_port}", "secret": stats_secret},
        "masquerade": {
            "type": "proxy",
            "proxy": {"url": masquerade, "rewriteHost": True, "insecure": False},
        },
    }
    if acme:
        config["acme"] = acme
    else:
        config["tls"] = {"cert": str(cert), "key": str(key), "sniGuard": "dns-san"}
    bandwidth = {key: value for key, value in (("up", bandwidth_up), ("down", bandwidth_down)) if value}
    if bandwidth:
        config["bandwidth"] = bandwidth
    if ech_key_path:
        config["ech"] = {"keyPath": str(ech_key_path)}
    if obfs_type != "none":
        config["obfs"] = {"type": obfs_type, obfs_type: {"password": obfs_password}}
    return config


def validate_and_install(binary: str, config: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    validation = dict(config)
    validation["listen"] = "127.0.0.1:0"
    validation["trafficStats"] = dict(config["trafficStats"])
    validation["trafficStats"]["listen"] = "127.0.0.1:0"
    descriptor, temporary_name = tempfile.mkstemp(prefix=".config-", suffix=".yaml", dir=destination.parent)
    temporary = Path(temporary_name)
    final: Optional[Path] = None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(validation, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o640)
        process = subprocess.Popen([binary, "server", "-c", str(temporary)], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            process.wait(timeout=1.5)
            raise HysteriaError("hysteria_config_invalid")
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=5)
        final_descriptor, final_name = tempfile.mkstemp(prefix=".final-", suffix=".yaml", dir=destination.parent)
        with os.fdopen(final_descriptor, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        final = Path(final_name)
        os.chmod(final, 0o640)
        try:
            os.chown(final, -1, destination.parent.stat().st_gid)
        except PermissionError:
            pass
        if destination.exists():
            shutil.copy2(destination, destination.with_suffix(".yaml.previous"))
        os.replace(final, destination)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise HysteriaError("hysteria_config_invalid") from error
    finally:
        temporary.unlink(missing_ok=True)
        if final is not None:
            final.unlink(missing_ok=True)


def configure_hysteria(
    *, binary: str, destination: Path, pki_dir: Path, user: dict[str, Any],
    server: str, port: int = 1935, stats_port: int = 9999,
    cert: Optional[Path] = None, key: Optional[Path] = None,
    masquerade: Optional[str] = None, obfs_type: str = "salamander",
    listen: str = "0.0.0.0", bandwidth_up: Optional[str] = None,
    bandwidth_down: Optional[str] = None, congestion: str = "bbr",
    ignore_client_bandwidth: bool = True, disable_udp: bool = False,
    udp_idle_timeout: str = "60s", acme: Optional[dict[str, Any]] = None,
    ech_key_path: Optional[Path] = None, existing_users: Optional[dict[str, str]] = None,
    ech_config: Optional[str] = None, allow_insecure: bool = False,
    remark: Optional[str] = None,
) -> dict[str, Any]:
    if not 1 <= port <= 65535 or not 1 <= stats_port <= 65535:
        raise HysteriaError("hysteria_invalid_port")
    _listen_address(listen, port)
    if obfs_type not in {"salamander", "gecko", "none"}:
        raise HysteriaError("hysteria_invalid_obfs")
    if acme and (cert is not None or key is not None):
        raise HysteriaError("hysteria_tls_acme_conflict")
    if (cert is None) != (key is None):
        raise HysteriaError("hysteria_tls_pair_required")
    if not destination.exists():
        if not _port_available(port, socket.SOCK_DGRAM, listen):
            raise HysteriaError("hysteria_port_busy")
        if not _port_available(stats_port, socket.SOCK_STREAM):
            raise HysteriaError("hysteria_stats_port_busy")
    generated_ca: Optional[Path] = None
    if acme:
        domains = acme.get("domains", [])
        if not isinstance(domains, list) or server not in domains:
            raise HysteriaError("hysteria_acme_domain_mismatch")
        cert = Path("/dev/null")
        key = Path("/dev/null")
    elif cert is None or key is None:
        pki = generate_private_ca(server, pki_dir)
        cert, key, generated_ca = pki["cert"], pki["key"], pki["ca"]
    if not acme and (not cert.is_file() or not key.is_file()):
        raise HysteriaError("hysteria_tls_missing")
    if not acme and not certificate_matches(cert, server):
        raise HysteriaError("hysteria_tls_name_mismatch")
    if ech_key_path is not None and not ech_key_path.is_file():
        raise HysteriaError("hysteria_ech_key_missing")
    if bool(ech_key_path) != bool(ech_config):
        raise HysteriaError("hysteria_ech_pair_required")
    selected_masquerade = select_masquerade(masquerade)
    identity = str(user.get("access_key_id") or user["id"])
    username = "k_" + identity.replace("-", "")
    password = protocol_credentials(user, "hysteria2")["hysteria_password"]
    obfs_password = secrets.token_urlsafe(32) if obfs_type != "none" else None
    stats_secret = secrets.token_urlsafe(32)
    pin = certificate_pin(cert) if not acme else None
    config = build_config(
        port=port, cert=cert, key=key, username=username, password=password,
        obfs_type=obfs_type, obfs_password=obfs_password, stats_port=stats_port,
        stats_secret=stats_secret,
        masquerade=selected_masquerade,
        listen=listen, bandwidth_up=bandwidth_up, bandwidth_down=bandwidth_down,
        congestion=congestion, ignore_client_bandwidth=ignore_client_bandwidth,
        disable_udp=disable_udp, udp_idle_timeout=udp_idle_timeout,
        users=existing_users, acme=acme, ech_key_path=ech_key_path,
    )
    validate_and_install(binary, config, destination)
    host = f"[{server}]" if ":" in server and not server.startswith("[") else server
    query_values = {"sni": server, "alpn": "h3"}
    if pin:
        query_values["pinSHA256"] = pin
    if allow_insecure:
        query_values["insecure"] = "1"
    if obfs_type != "none":
        query_values.update({"obfs": obfs_type, "obfs-password": obfs_password or ""})
    query = urllib.parse.urlencode(query_values)
    auth = urllib.parse.quote(f"{username}:{password}", safe="")
    endpoint_remark = remark or user["name"]
    uri = f"hysteria2://{auth}@{host}:{port}/?{query}#{urllib.parse.quote(endpoint_remark)}"
    client_config = {"server": f"{host}:{port}", "auth": f"{username}:{password}",
                     "tls": {"sni": server, "insecure": allow_insecure},
                     "quic": {"alpn": ["h3"]}}
    if pin:
        client_config["tls"]["pinSHA256"] = pin
    if ech_config:
        client_config["tls"]["ech"] = ech_config
    if obfs_type != "none":
        client_config["obfs"] = {"type": obfs_type, obfs_type: {"password": obfs_password}}
    return {
        "config": str(destination), "server": server, "port": port,
        "username": username, "password": password, "obfs": obfs_type,
        "obfs_password": obfs_password, "stats_listen": f"127.0.0.1:{stats_port}",
        "stats_secret": stats_secret, "masquerade": selected_masquerade,
        "pin_sha256": pin, "ca_cert": str(generated_ca) if generated_ca else None,
        "uri": uri, "client_config": client_config, "alpn": "h3",
        "insecure": allow_insecure,
        "warning": "TLS certificate verification is disabled" if allow_insecure else None,
    }
