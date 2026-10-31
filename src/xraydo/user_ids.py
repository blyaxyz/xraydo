from __future__ import annotations

import hashlib
import secrets
from collections.abc import Collection


USER_ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
USER_ID_LENGTH = 7


def is_short_user_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == USER_ID_LENGTH
        and all(character in USER_ID_ALPHABET for character in value)
        and any(character.isalpha() for character in value)
    )


def new_user_id(existing: Collection[str]) -> str:
    for _attempt in range(128):
        candidate = "".join(
            secrets.choice(USER_ID_ALPHABET) for _index in range(USER_ID_LENGTH)
        )
        if candidate not in existing and any(character.isalpha() for character in candidate):
            return candidate
    raise RuntimeError("user id space exhausted")


def migrated_user_id(source: str, existing: Collection[str]) -> str:
    """Produce a stable seven-character ID while resolving unlikely collisions."""
    for attempt in range(128):
        digest = hashlib.sha256(f"{source}\0{attempt}".encode()).digest()
        number = int.from_bytes(digest[:5], "big")
        characters = []
        for _index in range(USER_ID_LENGTH):
            characters.append(USER_ID_ALPHABET[number % len(USER_ID_ALPHABET)])
            number //= len(USER_ID_ALPHABET)
        candidate = "".join(characters)
        if candidate not in existing and any(character.isalpha() for character in candidate):
            return candidate
    raise RuntimeError("user id migration collision")
