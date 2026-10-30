from __future__ import annotations

from typing import Any

from .store import Store


ROLES = ("standalone", "master", "node")


class RoleService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def get(self) -> str:
        return str(self.store.load()["settings"].get("role", "standalone"))

    def set(self, role: str) -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError("invalid_role")
        data = self.store.load()
        data["settings"]["role"] = role
        self.store.save(data)
        return {"role": role}
