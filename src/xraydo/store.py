from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def default_data_dir() -> Path:
    override = os.environ.get("XRAYDO_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if os.geteuid() == 0:
        return Path("/var/lib/xraydo")
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "xraydo"


class Store:
    """Small atomic JSON store; schema is deliberately explicit and versioned."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = data_dir or default_data_dir()
        self.path = self.data_dir / "state.json"

    @staticmethod
    def initial() -> dict[str, Any]:
        return {"schema_version": 1, "settings": {}, "users": []}

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return self.initial()
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if data.get("schema_version") != 1:
            raise RuntimeError("unsupported state schema")
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".state-", dir=self.data_dir)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
