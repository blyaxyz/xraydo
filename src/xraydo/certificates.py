from __future__ import annotations

import copy
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .inbounds import InboundService
from .nodes import NodeService
from .store import Store
from .sync_queue import enqueue_node_operation
from .tls_manager import TLSManager


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NodeCertificateService:
    """Own certificates per node and disclose a private key only to its assignee."""

    def __init__(self, store: Store, root: Optional[Path] = None) -> None:
        self.store = store
        self.root = (root or store.data_dir / "tls" / "nodes").resolve()

    def list(self, node_id: Optional[str] = None, *, include_deleted: bool = False) -> list[dict[str, Any]]:
        records = self.store.load().get("managed_certificates", [])
        return [self._public(item) for item in records
                if (node_id is None or item["node_id"] == node_id)
                and (include_deleted or item.get("state") != "deleted")]

    def find(self, certificate_id: str, *, include_deleted: bool = False) -> dict[str, Any]:
        item = next((item for item in self.store.load().get("managed_certificates", [])
                     if item["id"] == certificate_id
                     and (include_deleted or item.get("state") != "deleted")), None)
        if item is None:
            raise ValueError("certificate_not_found")
        return item

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        return {key: copy.deepcopy(value) for key, value in item.items()
                if key not in {"key", "versions"}}

    def issue(self, node_reference: str, *, domains: list[str], ips: list[str],
              days: int = 90, renew_percent: int = 67) -> dict[str, Any]:
        node_id = ("local" if node_reference in {"local", "standalone"}
                   else NodeService(self.store).find(node_reference)["id"])
        identifier = str(uuid.uuid4())
        manager = TLSManager(self.store, self.root / node_id / identifier)
        previous_subscription_tls = copy.deepcopy(
            self.store.load().get("settings", {}).get("subscription_tls")
        )
        status = manager.issue(domains=domains, ips=ips, days=days,
                               renew_percent=renew_percent)
        data = self.store.load()
        if previous_subscription_tls is None:
            data["settings"].pop("subscription_tls", None)
        else:
            data["settings"]["subscription_tls"] = previous_subscription_tls
        timestamp = _now()
        record = {
            "id": identifier, "node_id": node_id, "state": "active",
            "cert": status["cert"], "key": status["key"],
            "ca_cert": status["ca_cert"], "metadata": status["metadata"],
            "created_at": timestamp, "updated_at": timestamp,
            "revoked_at": None, "deleted_at": None,
        }
        data.setdefault("managed_certificates", []).append(record)
        self._audit(data, "certificate.issue", record, ["domains", "ips", "expires_at"])
        self.store.save(data)
        return self._public(record)

    def renew(self, certificate_id: str, *, force: bool = False) -> dict[str, Any]:
        current = self.find(certificate_id)
        if current.get("state") != "active":
            raise ValueError("certificate_not_active")
        manager = TLSManager(self.store, self.root / current["node_id"] / current["id"])
        previous_subscription_tls = copy.deepcopy(
            self.store.load().get("settings", {}).get("subscription_tls")
        )
        status = manager.renew(force=force)
        data = self.store.load()
        if previous_subscription_tls is None:
            data["settings"].pop("subscription_tls", None)
        else:
            data["settings"]["subscription_tls"] = previous_subscription_tls
        item = next(item for item in data["managed_certificates"] if item["id"] == certificate_id)
        item.update({"cert": status["cert"], "key": status["key"],
                     "ca_cert": status["ca_cert"], "metadata": status["metadata"],
                     "updated_at": _now()})
        if status.get("renewed"):
            self._queue_delivery(data, item, action="replace")
            self._audit(data, "certificate.renew", item, ["expires_at"])
        self.store.save(data)
        return {**self._public(item), "renewed": bool(status.get("renewed"))}

    def replace(self, certificate_id: str, *, cert: Path, key: Path,
                domains: list[str], ips: list[str], ca_cert: Optional[Path] = None,
                chain: Optional[Path] = None) -> dict[str, Any]:
        current = self.find(certificate_id)
        manager = TLSManager(self.store, self.root / current["node_id"] / current["id"])
        previous_subscription_tls = copy.deepcopy(
            self.store.load().get("settings", {}).get("subscription_tls")
        )
        status = manager.import_leaf(cert=cert, key=key, domains=domains, ips=ips,
                                     ca_cert=ca_cert, chain=chain)
        data = self.store.load()
        if previous_subscription_tls is None:
            data["settings"].pop("subscription_tls", None)
        else:
            data["settings"]["subscription_tls"] = previous_subscription_tls
        item = next(item for item in data["managed_certificates"] if item["id"] == certificate_id)
        item.update({"cert": status["cert"], "key": status["key"],
                     "ca_cert": status["ca_cert"], "metadata": status["metadata"],
                     "updated_at": _now(), "state": "active"})
        operation = self._queue_delivery(data, item, action="replace")
        self._audit(data, "certificate.replace", item, ["domains", "ips", "expires_at"])
        self.store.save(data)
        return {**self._public(item), "operation_id": operation.get("id") if operation else None}

    def assign(self, certificate_id: str, inbound_id: str, *, apply: bool = True) -> dict[str, Any]:
        certificate = self.find(certificate_id)
        if certificate.get("state") != "active":
            raise ValueError("certificate_not_active")
        inbound = InboundService(self.store).find(inbound_id, certificate["node_id"])
        if inbound.get("config", {}).get("streamSettings", {}).get("security") != "tls":
            raise ValueError("certificate_inbound_not_tls")
        data = self.store.load()
        assignments = data.setdefault("certificate_assignments", [])
        assignments[:] = [item for item in assignments
                          if not (item["node_id"] == certificate["node_id"]
                                  and item["inbound_id"] == inbound_id)]
        assignment = {"certificate_id": certificate_id, "node_id": certificate["node_id"],
                      "inbound_id": inbound_id, "created_at": _now()}
        assignments.append(assignment)
        operation = self._queue_delivery(data, certificate, action="assign",
                                         inbound_id=inbound_id) if apply else None
        self._audit(data, "certificate.assign", certificate, ["inbound_id", "node_id"])
        self.store.save(data)
        return {**assignment, "state": "pending_node_execution" if operation else "planned",
                "operation_id": operation.get("id") if operation else None}

    def revoke(self, certificate_id: str) -> dict[str, Any]:
        data = self.store.load()
        item = next((item for item in data.get("managed_certificates", [])
                     if item["id"] == certificate_id and item.get("state") != "deleted"), None)
        if item is None:
            raise ValueError("certificate_not_found")
        item["state"] = "revoked"; item["revoked_at"] = _now(); item["updated_at"] = _now()
        operation = self._queue_delivery(data, item, action="revoke")
        self._audit(data, "certificate.revoke", item, ["state"])
        self.store.save(data)
        return {**self._public(item), "operation_id": operation.get("id") if operation else None}

    def delete(self, certificate_id: str, *, force: bool = False) -> dict[str, Any]:
        data = self.store.load()
        item = next((item for item in data.get("managed_certificates", [])
                     if item["id"] == certificate_id), None)
        if item is None:
            raise ValueError("certificate_not_found")
        assigned = [a for a in data.get("certificate_assignments", [])
                    if a["certificate_id"] == certificate_id]
        if assigned and not force:
            raise ValueError("certificate_in_use")
        if item.get("state") == "active" and not force:
            raise ValueError("certificate_revoke_required")
        operation = self._queue_delivery(data, item, action="delete")
        item["state"] = "deleted"; item["deleted_at"] = _now(); item["updated_at"] = _now()
        data["certificate_assignments"] = [a for a in data.get("certificate_assignments", [])
                                           if a["certificate_id"] != certificate_id]
        try:
            key = Path(item["key"])
            if key.is_file():
                key.write_bytes(os.urandom(max(64, key.stat().st_size)))
                key.unlink()
        except OSError:
            pass
        self._audit(data, "certificate.delete", item, ["state"])
        self.store.save(data)
        return {**self._public(item), "operation_id": operation.get("id") if operation else None}

    def _queue_delivery(self, data: dict[str, Any], item: dict[str, Any], *, action: str,
                        inbound_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        if item["node_id"] == "local":
            return None
        payload: dict[str, Any] = {"certificate_id": item["id"], "inbound_id": inbound_id}
        if action not in {"revoke", "delete"}:
            payload.update({"certificate": Path(item["cert"]).read_text(encoding="utf-8"),
                            "private_key": Path(item["key"]).read_text(encoding="utf-8"),
                            "ca_certificate": (Path(item["ca_cert"]).read_text(encoding="utf-8")
                                               if item.get("ca_cert") else None),
                            "mode": "0600", "atomic": True})
        return enqueue_node_operation(data, item["node_id"], "tls_certificate", action,
                                      payload, resource_id=item["id"])

    @staticmethod
    def _audit(data: dict[str, Any], action: str, item: dict[str, Any], fields: list[str]) -> None:
        data.setdefault("audit_log", []).append({
            "id": str(uuid.uuid4()), "timestamp": _now(),
            "actor": os.environ.get("USER") or "local-admin", "action": action,
            "object_type": "certificate", "object_id": item["id"], "fields": fields,
        })
