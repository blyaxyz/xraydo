from __future__ import annotations

import hashlib
import ipaddress
import json
import os
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


def _port_available(port: int, socket_type: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket_type) as listener:
            listener.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False


def build_config(
    *, port: int, cert: Path, key: Path, username: str, password: str,
    obfs_type: str, obfs_password: Optional[str], stats_port: int,
    stats_secret: str, masquerade: str,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "listen": f":{port}",
        "tls": {"cert": str(cert), "key": str(key), "sniGuard": "dns-san"},
        "auth": {"type": "userpass", "userpass": {username: password}},
        "congestion": {"type": "bbr", "bbrProfile": "standard"},
        "ignoreClientBandwidth": True,
        "trafficStats": {"listen": f"127.0.0.1:{stats_port}", "secret": stats_secret},
        "masquerade": {
            "type": "proxy",
            "proxy": {"url": masquerade, "rewriteHost": True, "insecure": False},
        },
    }
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
) -> dict[str, Any]:
    if not 1 <= port <= 65535 or not 1 <= stats_port <= 65535:
        raise HysteriaError("hysteria_invalid_port")
    if not destination.exists():
        if not _port_available(port, socket.SOCK_DGRAM):
            raise HysteriaError("hysteria_port_busy")
        if not _port_available(stats_port, socket.SOCK_STREAM):
            raise HysteriaError("hysteria_stats_port_busy")
    if obfs_type not in {"salamander", "gecko", "none"}:
        raise HysteriaError("hysteria_invalid_obfs")
    if (cert is None) != (key is None):
        raise HysteriaError("hysteria_tls_pair_required")
    generated_ca: Optional[Path] = None
    if cert is None or key is None:
        pki = generate_private_ca(server, pki_dir)
        cert, key, generated_ca = pki["cert"], pki["key"], pki["ca"]
    if not cert.is_file() or not key.is_file():
        raise HysteriaError("hysteria_tls_missing")
    if not certificate_matches(cert, server):
        raise HysteriaError("hysteria_tls_name_mismatch")
    selected_masquerade = select_masquerade(masquerade)
    username = "u_" + user["id"].replace("-", "")[:16]
    password = protocol_credentials(user, "hysteria2")["hysteria_password"]
    obfs_password = secrets.token_urlsafe(32) if obfs_type != "none" else None
    stats_secret = secrets.token_urlsafe(32)
    pin = certificate_pin(cert)
    config = build_config(
        port=port, cert=cert, key=key, username=username, password=password,
        obfs_type=obfs_type, obfs_password=obfs_password, stats_port=stats_port,
        stats_secret=stats_secret,
        masquerade=selected_masquerade,
    )
    validate_and_install(binary, config, destination)
    host = f"[{server}]" if ":" in server and not server.startswith("[") else server
    query_values = {"sni": server, "pinSHA256": pin}
    if obfs_type != "none":
        query_values.update({"obfs": obfs_type, "obfs-password": obfs_password or ""})
    query = urllib.parse.urlencode(query_values)
    auth = urllib.parse.quote(f"{username}:{password}", safe="")
    uri = f"hysteria2://{auth}@{host}:{port}/?{query}#{urllib.parse.quote(user['name'])}"
    return {
        "config": str(destination), "server": server, "port": port,
        "username": username, "password": password, "obfs": obfs_type,
        "obfs_password": obfs_password, "stats_listen": f"127.0.0.1:{stats_port}",
        "stats_secret": stats_secret, "masquerade": selected_masquerade,
        "pin_sha256": pin, "ca_cert": str(generated_ca) if generated_ca else None,
        "uri": uri,
    }
