from __future__ import annotations

import ipaddress
import json
import os
import pwd
import re
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from .access_keys import protocol_credentials

from .reality import profile_target, scan_target


class TelemtError(ValueError):
    pass


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def select_tls_domain(explicit: Optional[str] = None) -> tuple[str, dict[str, Any]]:
    if explicit:
        result = scan_target(explicit, 443)
    else:
        result, _ = profile_target()
    return result["server_name"], result


def build_config(
    *,
    port: int,
    api_port: int,
    api_secret: str,
    tls_domain: str,
    username: str,
    user_secret: str,
    public_host: str,
    data_dir: Path,
    mode: str = "tls",
    ad_tag: Optional[str] = None,
    listener_ips: Optional[list[str]] = None,
    ipv4: bool = True,
    ipv6: bool = False,
    prefer_ip: int = 4,
    use_middle_proxy: bool = True,
    first_byte_timeout: int = 300,
    handshake_timeout: int = 60,
    keepalive: int = 15,
    ack_timeout: int = 90,
    users: Optional[dict[str, str]] = None,
    mask_host: Optional[str] = None,
    mask_port: int = 443,
    unknown_sni_action: str = "reject_handshake",
) -> str:
    value = _toml_string
    ad_tag_line = f"ad_tag = {value(ad_tag)}\n" if ad_tag else ""
    listeners = listener_ips or (["0.0.0.0"] if ipv4 else []) + (["::"] if ipv6 else [])
    listener_sections = "\n\n".join(
        f"[[server.listeners]]\nip = {value(listener)}" for listener in listeners
    )
    user_map = dict(users or {})
    user_map[username] = user_secret
    user_lines = "\n".join(f"{value(name)} = {value(secret)}" for name, secret in user_map.items())
    mask_target = mask_host or tls_domain
    return f"""[general]
use_middle_proxy = {str(use_middle_proxy).lower()}
log_level = "normal"
{ad_tag_line}

[general.modes]
classic = {str(mode == "classic").lower()}
secure = {str(mode == "secure").lower()}
tls = {str(mode == "tls").lower()}

[general.links]
show = [{value(username)}]
public_host = {value(public_host)}
public_port = {port}

[server]
port = {port}

[server.api]
enabled = true
listen = "127.0.0.1:{api_port}"
whitelist = ["127.0.0.1/32", "::1/128"]
minimal_runtime_enabled = false
minimal_runtime_cache_ttl_ms = 1000

{listener_sections}

[censorship]
tls_domain = {value(tls_domain)}

[access.users]
{user_lines}
"""


def _available_tcp_port(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False


def _ephemeral_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def validate_and_install(
    binary: str,
    final_config: str,
    validation_config: str,
    destination: Path,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".telemt-", suffix=".toml", dir=destination.parent)
    temporary = Path(temporary_name)
    final: Optional[Path] = None
    process: Optional[subprocess.Popen[bytes]] = None
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(validation_config)
        os.chmod(temporary, 0o640)
        process = subprocess.Popen([binary, str(temporary)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        healthy = False
        for _ in range(20):
            if process.poll() is not None:
                break
            probe = subprocess.run(
                [binary, "healthcheck", str(temporary), "--mode", "liveness"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=3,
            )
            if probe.returncode == 0:
                healthy = True
                break
            time.sleep(0.1)
        if not healthy:
            raise TelemtError("telemt_config_invalid")
        process.terminate()
        process.wait(timeout=5)
        final_descriptor, final_name = tempfile.mkstemp(prefix=".final-", suffix=".toml", dir=destination.parent)
        final = Path(final_name)
        with os.fdopen(final_descriptor, "w", encoding="utf-8") as handle:
            handle.write(final_config)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(final, 0o640)
        try:
            os.chown(final, -1, destination.parent.stat().st_gid)
        except PermissionError:
            pass
        if destination.exists():
            shutil.copy2(destination, destination.with_suffix(".toml.previous"))
        os.replace(final, destination)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise TelemtError("telemt_config_invalid") from error
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        temporary.unlink(missing_ok=True)
        if final is not None:
            final.unlink(missing_ok=True)


def configure_telemt(
    *,
    binary: str,
    destination: Path,
    data_dir: Path,
    user: dict[str, Any],
    public_host: str,
    port: int = 8443,
    api_port: int = 9091,
    tls_domain: Optional[str] = None,
    mode: str = "tls",
    ad_tag: Optional[str] = None,
    listener_ips: Optional[list[str]] = None,
    ipv4: bool = True,
    ipv6: bool = False,
    prefer_ip: int = 4,
    use_middle_proxy: bool = True,
    first_byte_timeout: int = 300,
    handshake_timeout: int = 60,
    keepalive: int = 15,
    ack_timeout: int = 90,
    existing_users: Optional[dict[str, str]] = None,
    mask_host: Optional[str] = None,
    mask_port: int = 443,
    unknown_sni_action: str = "reject_handshake",
) -> dict[str, Any]:
    if not 1 <= port <= 65535 or not 1 <= api_port <= 65535:
        raise TelemtError("telemt_invalid_port")
    if mode not in {"tls", "secure", "classic"}:
        raise TelemtError("telemt_invalid_mode")
    if ad_tag is not None and re.fullmatch(r"[0-9a-fA-F]{32}", ad_tag) is None:
        raise TelemtError("telemt_invalid_ad_tag")
    if not ipv4 and not ipv6:
        raise TelemtError("telemt_no_ip_family")
    if prefer_ip not in {4, 6} or (prefer_ip == 4 and not ipv4) or (prefer_ip == 6 and not ipv6):
        raise TelemtError("telemt_invalid_ip_preference")
    selected_listeners = listener_ips or (["0.0.0.0"] if ipv4 else []) + (["::"] if ipv6 else [])
    try:
        parsed_listeners = [ipaddress.ip_address(listener) for listener in selected_listeners]
    except ValueError as error:
        raise TelemtError("telemt_invalid_listener") from error
    if any(address.version == 4 and not ipv4 for address in parsed_listeners):
        raise TelemtError("telemt_invalid_listener")
    if any(address.version == 6 and not ipv6 for address in parsed_listeners):
        raise TelemtError("telemt_invalid_listener")
    if any(not 1 <= timeout <= 86_400 for timeout in (first_byte_timeout, handshake_timeout, keepalive, ack_timeout)):
        raise TelemtError("telemt_invalid_timeout")
    if not 1 <= mask_port <= 65535 or unknown_sni_action not in {"mask", "reject_handshake"}:
        raise TelemtError("telemt_invalid_mask")
    if mask_host is not None and (not mask_host.strip() or any(char.isspace() for char in mask_host)):
        raise TelemtError("telemt_invalid_mask")
    if not destination.exists():
        if not _available_tcp_port(port):
            raise TelemtError("telemt_port_busy")
        if not _available_tcp_port(api_port):
            raise TelemtError("telemt_api_port_busy")
    selected_domain, target_report = select_tls_domain(tls_domain)
    identity = str(user.get("access_key_id") or user["id"])
    username = "k_" + identity.replace("-", "")
    user_secret = protocol_credentials(user, "telemt")["telemt_secret"]
    api_secret = secrets.token_urlsafe(32)
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o750)
    os.chmod(data_dir, 0o750)
    try:
        account = pwd.getpwnam("xraydo-telemt")
        os.chown(data_dir, account.pw_uid, account.pw_gid)
    except (KeyError, PermissionError):
        pass
    final_config = build_config(
        port=port,
        api_port=api_port,
        api_secret=api_secret,
        tls_domain=selected_domain,
        username=username,
        user_secret=user_secret,
        public_host=public_host,
        data_dir=data_dir,
        mode=mode,
        ad_tag=ad_tag.lower() if ad_tag else None,
        listener_ips=selected_listeners,
        ipv4=ipv4,
        ipv6=ipv6,
        prefer_ip=prefer_ip,
        use_middle_proxy=use_middle_proxy,
        first_byte_timeout=first_byte_timeout,
        handshake_timeout=handshake_timeout,
        keepalive=keepalive,
        ack_timeout=ack_timeout,
        users=existing_users, mask_host=mask_host, mask_port=mask_port,
        unknown_sni_action=unknown_sni_action,
    )
    validation_port = _ephemeral_port()
    validation_api_port = _ephemeral_port()
    validation_config = build_config(
        port=validation_port,
        api_port=validation_api_port,
        api_secret=api_secret,
        tls_domain=selected_domain,
        username=username,
        user_secret=user_secret,
        public_host="127.0.0.1",
        data_dir=data_dir,
        mode=mode,
        ad_tag=ad_tag.lower() if ad_tag else None,
        listener_ips=selected_listeners,
        ipv4=ipv4,
        ipv6=ipv6,
        prefer_ip=prefer_ip,
        use_middle_proxy=use_middle_proxy,
        first_byte_timeout=first_byte_timeout,
        handshake_timeout=handshake_timeout,
        keepalive=keepalive,
        ack_timeout=ack_timeout,
        users=existing_users, mask_host=mask_host, mask_port=mask_port,
        unknown_sni_action=unknown_sni_action,
    )
    validate_and_install(binary, final_config, validation_config, destination)
    if mode == "tls":
        connection_secret = "ee" + user_secret + selected_domain.encode("idna").hex()
    elif mode == "secure":
        connection_secret = "dd" + user_secret
    else:
        connection_secret = user_secret
    query = urllib.parse.urlencode({"server": public_host, "port": port, "secret": connection_secret})
    return {
        "config": str(destination),
        "server": public_host,
        "port": port,
        "username": username,
        "user_secret": user_secret,
        "mode": mode,
        "ad_tag": ad_tag.lower() if ad_tag else None,
        "listeners": selected_listeners,
        "network": {"ipv4": ipv4, "ipv6": ipv6, "prefer": prefer_ip},
        "use_middle_proxy": use_middle_proxy,
        "timeouts": {
            "first_byte": first_byte_timeout,
            "handshake": handshake_timeout,
            "keepalive": keepalive,
            "ack": ack_timeout,
        },
        "connection_secret": connection_secret,
        "fake_tls_secret": connection_secret if mode == "tls" else None,
        "tls_domain": selected_domain,
        "target_check": target_report,
        "mask": {"host": mask_host or selected_domain, "port": mask_port,
                 "unknown_sni_action": unknown_sni_action},
        "api_listen": f"127.0.0.1:{api_port}",
        "api_auth_header": "Bearer " + api_secret,
        "tg_uri": "tg://proxy?" + query,
        "https_uri": "https://t.me/proxy?" + query,
    }
