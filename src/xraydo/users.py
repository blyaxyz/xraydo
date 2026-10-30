from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from .store import Store


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def create(self, name: str) -> tuple[dict[str, Any], str]:
        name = name.strip()
        if not name or len(name) > 64 or any(ord(char) < 32 for char in name):
            raise ValueError("invalid_name")
        data = self.store.load()
        if any(item["name"].casefold() == name.casefold() for item in data["users"]):
            raise ValueError("duplicate_name")
        token = secrets.token_urlsafe(32)
        timestamp = _now()
        user = {
            "id": str(uuid.uuid4()),
            "name": name,
            "enabled": True,
            "created_at": timestamp,
            "updated_at": timestamp,
            "subscription_token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "credentials": {"xray_uuid": str(uuid.uuid4())},
        }
        data["users"].append(user)
        self.store.save(data)
        return user, token

    def list(self) -> list[dict[str, Any]]:
        return list(self.store.load()["users"])

    def find(self, reference: str) -> dict[str, Any]:
        matches = [
            item
            for item in self.list()
            if item["id"] == reference or item["name"].casefold() == reference.casefold()
        ]
        if len(matches) != 1:
            raise ValueError("user_not_found")
        return matches[0]

    def set_enabled(self, reference: str, enabled: bool) -> dict[str, Any]:
        data = self.store.load()
        for item in data["users"]:
            if item["id"] == reference or item["name"].casefold() == reference.casefold():
                item["enabled"] = enabled
                item["updated_at"] = _now()
                self.store.save(data)
                return item
        raise ValueError("user_not_found")
