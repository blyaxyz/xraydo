from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional, Sequence


class CoreServiceError(ValueError):
    pass


def _run(command: Sequence[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CoreServiceError("service_command_failed") from error


def init_system() -> str:
    if shutil.which("systemctl") and Path("/run/systemd/system").is_dir():
        return "systemd"
    if shutil.which("rc-service"):
        return "openrc"
    raise CoreServiceError("service_manager_missing")


def _service_name(core: str) -> str:
    if core not in {"xray", "hysteria", "telemt"}:
        raise CoreServiceError("service_unknown_core")
    return f"xraydo-{core}"


def control_service(core: str, action: str) -> dict[str, str]:
    if action not in {"start", "stop", "restart"}:
        raise CoreServiceError("service_invalid_action")
    manager = init_system()
    service = _service_name(core)
    command = ["systemctl", action, service] if manager == "systemd" else ["rc-service", service, action]
    result = _run(command)
    if result.returncode != 0:
        if os.geteuid() != 0:
            raise CoreServiceError("service_permission_denied")
        raise CoreServiceError("service_action_failed")
    return {"core": core, "service": service, "manager": manager, "action": action}


def _parse_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator:
            properties[key] = value
    return properties


def service_status(core: str) -> dict[str, Any]:
    manager = init_system()
    service = _service_name(core)
    if manager == "systemd":
        result = _run(
            [
                "systemctl",
                "show",
                service,
                "--no-page",
                "--property=LoadState,ActiveState,SubState,MainPID,NRestarts,ExecMainStartTimestamp",
            ]
        )
        properties = _parse_properties(result.stdout)
        if result.returncode != 0 or properties.get("LoadState") == "not-found":
            raise CoreServiceError("service_not_installed")
        active = properties.get("ActiveState", "unknown")
        return {
            "core": core,
            "service": service,
            "manager": manager,
            "state": active,
            "substate": properties.get("SubState", "unknown"),
            "pid": int(properties.get("MainPID", "0") or 0),
            "restarts": int(properties.get("NRestarts", "0") or 0),
            "started_at": properties.get("ExecMainStartTimestamp") or None,
        }

    result = _run(["rc-service", service, "status"])
    output = (result.stdout or result.stderr).strip()
    lowered = output.casefold()
    missing = "does not exist" in lowered or "not found" in lowered
    if missing:
        raise CoreServiceError("service_not_installed")
    return {
        "core": core,
        "service": service,
        "manager": manager,
        "state": "active" if result.returncode == 0 else "inactive",
        "substate": "running" if result.returncode == 0 else "stopped",
        "pid": None,
        "restarts": None,
        "started_at": None,
    }


def service_logs(core: str, lines: int = 100) -> dict[str, Any]:
    if not 1 <= lines <= 10_000:
        raise CoreServiceError("service_invalid_lines")
    manager = init_system()
    service = _service_name(core)
    if manager == "systemd":
        result = _run(["journalctl", "--unit", service, "--no-pager", "--lines", str(lines)])
        if result.returncode != 0:
            raise CoreServiceError("service_logs_failed")
        output = result.stdout
    else:
        log_file = Path(f"/var/log/{service}.log")
        if log_file.is_file():
            try:
                output = "\n".join(log_file.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
            except OSError as error:
                raise CoreServiceError("service_logs_failed") from error
        elif shutil.which("logread"):
            result = _run(["logread"])
            if result.returncode != 0:
                raise CoreServiceError("service_logs_failed")
            matching = [line for line in result.stdout.splitlines() if service in line or core in line]
            output = "\n".join(matching[-lines:])
        else:
            raise CoreServiceError("service_logs_unavailable")
    return {"core": core, "service": service, "manager": manager, "lines": lines, "logs": output.rstrip()}


def core_version(core: str) -> dict[str, Optional[str]]:
    binary_name = {"xray": "xray", "hysteria": "hysteria", "telemt": "telemt"}.get(core)
    if binary_name is None:
        raise CoreServiceError("service_unknown_core")
    binary = shutil.which(binary_name)
    if binary is None:
        raise CoreServiceError(f"{core}_not_installed")
    arguments = [binary, "version"] if core in {"xray", "hysteria"} else [binary, "--version"]
    result = _run(arguments, timeout=10)
    if result.returncode != 0:
        raise CoreServiceError("service_version_failed")
    lines = (result.stdout or result.stderr).strip().splitlines()
    return {"core": core, "binary": binary, "version": lines[0] if lines else None}
