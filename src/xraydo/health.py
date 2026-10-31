from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

CORE_CONFIGS = {
    "xray": "config.json",
    "hysteria": "config.yaml",
    "telemt": "telemt.toml",
}


def parse_core_version(core: str, output: str) -> Optional[str]:
    lines = output.strip().splitlines()
    if core == "hysteria":
        for line in lines:
            label, separator, value = line.partition(":")
            if separator and label.strip() == "Version" and value.strip():
                return value.strip()
    return lines[0] if lines else None


def _run(arguments: list[str]) -> Optional[subprocess.CompletedProcess[str]]:
    try:
        return subprocess.run(
            arguments,
            text=True,
            capture_output=True,
            check=False,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _version(core: str, binary: str) -> Optional[str]:
    arguments = [binary, "--version"] if core == "telemt" else [binary, "version"]
    result = _run(arguments)
    if result is None or result.returncode != 0:
        return None
    return parse_core_version(core, result.stdout or result.stderr)


def _service_state(core: str) -> str:
    service = f"xraydo-{core}"
    if shutil.which("systemctl") and Path("/run/systemd/system").is_dir():
        result = _run(["systemctl", "is-active", service])
        if result:
            state = result.stdout.strip()
            if state == "active":
                return "running"
            if state:
                return state
        return "unknown"
    if shutil.which("rc-service"):
        result = _run(["rc-service", service, "status"])
        if result and result.returncode == 0:
            return "running"
        return "stopped"
    return "unknown"


def _xray_protocol(binary: str, config: Path) -> dict[str, Any]:
    validation = _run([binary, "run", "-test", "-config", str(config)])
    if validation is None or validation.returncode != 0:
        return {"state": "failed", "check": "config", "detail": "xray_config_invalid"}
    try:
        document = json.loads(config.read_text(encoding="utf-8"))
        inbounds = document.get("inbounds") or []
        listeners: list[tuple[str, int]] = []
        for inbound in inbounds:
            if not isinstance(inbound, dict) or not isinstance(inbound.get("port"), int):
                continue
            host = str(inbound.get("listen") or "127.0.0.1").strip("[]")
            if host in {"0.0.0.0", ""}:
                host = "127.0.0.1"
            elif host == "::":
                host = "::1"
            listeners.append((host, inbound["port"]))
        if not listeners:
            return {"state": "failed", "check": "inbound", "detail": "xray_no_inbound"}
        failures = []
        for host, port in listeners:
            try:
                with socket.create_connection((host, port), timeout=2):
                    return {
                        "state": "healthy", "check": "config+inbound",
                        "endpoint": f"{host}:{port}",
                    }
            except OSError:
                failures.append(f"{host}:{port}")
        return {
            "state": "failed", "check": "inbound", "detail": "unreachable",
            "endpoints": failures,
        }
    except (OSError, ValueError, TypeError):
        return {"state": "failed", "check": "config", "detail": "xray_config_unreadable"}


def _hysteria_protocol(config: Path) -> dict[str, Any]:
    try:
        document = json.loads(config.read_text(encoding="utf-8"))
        traffic = document["trafficStats"]
        listen = str(traffic["listen"])
        secret = str(traffic["secret"])
        host, separator, port = listen.rpartition(":")
        if not separator or host not in {"127.0.0.1", "localhost", "[::1]"} or not port.isdigit():
            raise ValueError
        request = urllib.request.Request(
            f"http://{listen}/traffic",
            headers={"Authorization": secret, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            json.load(response)
        return {
            "state": "healthy", "check": "authenticated-traffic-api",
            "endpoint": listen,
        }
    except (OSError, KeyError, TypeError, ValueError, urllib.error.HTTPError):
        return {
            "state": "failed", "check": "authenticated-traffic-api",
            "detail": "hysteria_api_unreachable",
        }


def _telemt_protocol(binary: str, config: Path) -> dict[str, Any]:
    result = _run([binary, "healthcheck", str(config), "--mode", "liveness"])
    if result is not None and result.returncode == 0:
        return {"state": "healthy", "check": "native-liveness"}
    return {"state": "failed", "check": "native-liveness", "detail": "telemt_liveness_failed"}


def _protocol_health(core: str, binary: str, config: Path, service_state: str) -> dict[str, Any]:
    if service_state != "running":
        return {"state": "not_running", "check": "not_attempted"}
    if core == "xray":
        return _xray_protocol(binary, config)
    if core == "hysteria":
        return _hysteria_protocol(config)
    return _telemt_protocol(binary, config)


def core_health() -> dict[str, dict[str, Any]]:
    config = Path(os.environ.get("XRAYDO_CONFIG", "/etc/xraydo/config.json"))
    config_root = config.parent / "cores"
    report: dict[str, dict[str, Any]] = {}
    for core, config_name in CORE_CONFIGS.items():
        binary = shutil.which(core)
        config_file = config_root / core / config_name
        installed = binary is not None
        configured = config_file.is_file()
        if not installed:
            state = "not_installed"
        elif not configured:
            state = "config_missing"
        else:
            state = _service_state(core)
        protocol = (
            _protocol_health(core, binary, config_file, state)
            if binary and configured
            else {"state": "not_configured", "check": "not_attempted"}
        )
        report[core] = {
            "installed": installed,
            "configured": configured,
            "state": state,
            "version": _version(core, binary) if binary else None,
            "protocol": protocol,
        }
    return report


def overall_health(cores: dict[str, dict[str, Any]]) -> str:
    unhealthy = any(
        health.get("configured")
        and health.get("protocol", {}).get("state") != "healthy"
        for health in cores.values()
    )
    return "degraded" if unhealthy else "ready"
