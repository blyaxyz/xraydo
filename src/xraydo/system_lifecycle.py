from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


def _candidate_installers() -> list[Path]:
    candidates = []
    override = os.environ.get("XRAYDO_INSTALLER")
    if override:
        candidates.append(Path(override))
    module = Path(__file__).resolve()
    candidates.extend([
        module.parents[2] / "install.sh",  # source checkout or installed stable copy
        Path(sys.argv[0]).resolve().parent.parent / "lib/xraydo/install.sh",
        Path("/usr/local/lib/xraydo/install.sh"),
    ])
    unique = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def find_installer(explicit: Optional[Path] = None) -> Path:
    candidates = [explicit] if explicit else _candidate_installers()
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            info = candidate.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError("system_installer_unsafe")
        if info.st_mode & 0o022:
            raise ValueError("system_installer_unsafe")
        if info.st_uid not in {0, os.geteuid()}:
            raise ValueError("system_installer_unsafe")
        if not os.access(candidate, os.X_OK):
            raise ValueError("system_installer_not_executable")
        return candidate.resolve()
    raise ValueError("system_installer_missing")


class SystemLifecycleService:
    def __init__(self, installer: Optional[Path] = None) -> None:
        self.installer = find_installer(installer)

    def status(self) -> dict[str, Any]:
        completed = subprocess.run(
            [str(self.installer), "--version"], text=True, capture_output=True, check=False
        )
        if completed.returncode:
            raise ValueError("system_installer_failed")
        return {"installer": str(self.installer), "installer_version": completed.stdout.strip()}

    def execute(
        self, action: str, *, prefix: Path = Path("/usr/local"), dry_run: bool = False,
        confirmed: bool = False, language: Optional[str] = None,
    ) -> dict[str, Any]:
        if action not in {"update", "uninstall", "purge"}:
            raise ValueError("system_invalid_action")
        if not prefix.is_absolute() or prefix == Path("/"):
            raise ValueError("system_invalid_prefix")
        if not dry_run and not confirmed:
            raise ValueError("confirmation_required")
        command = [str(self.installer), f"--{action}", "--prefix", str(prefix), "--non-interactive"]
        if language:
            command.extend(["--lang", language])
        command.append("--dry-run" if dry_run else "--yes")
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        return {
            "action": action, "prefix": str(prefix), "dry_run": dry_run,
            "returncode": completed.returncode, "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
