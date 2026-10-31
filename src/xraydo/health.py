from __future__ import annotations

import os
import shutil
import subprocess
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
        report[core] = {
            "installed": installed,
            "configured": configured,
            "state": state,
            "version": _version(core, binary) if binary else None,
        }
    return report
