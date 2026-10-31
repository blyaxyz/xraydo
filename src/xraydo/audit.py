from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


def audit_actor(explicit: Optional[str] = None) -> str:
    value = (explicit or os.environ.get("XRAYDO_ACTOR") or os.environ.get("USER")
             or "local-admin").strip()
    if not value or len(value) > 128 or any(ord(character) < 32 for character in value):
        raise ValueError("invalid_audit_actor")
    return value


def append_audit(
    data: dict[str, Any], *, action: str, object_type: str, object_id: str,
    fields: list[str], actor: Optional[str] = None,
) -> dict[str, Any]:
    """Append a value-free event; callers may name fields but never include values."""
    if not action or not object_type or not object_id:
        raise ValueError("invalid_audit_event")
    event = {
        "id": str(uuid.uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actor": audit_actor(actor),
        "action": action,
        "object_type": object_type,
        "object_id": object_id,
        "fields": sorted(set(fields)),
    }
    data.setdefault("audit_log", []).append(event)
    return event
