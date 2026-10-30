from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from .store import Store


def _normalize_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("subscription_tls_pin_invalid")
    normalized = value.replace(":", "").strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
        raise ValueError("subscription_tls_pin_invalid")
    return normalized


def _hysteria_pin(uri: str, configured_pin: Optional[str]) -> tuple[str, Optional[str]]:
    parsed = urllib.parse.urlsplit(uri)
    if parsed.scheme not in {"hysteria2", "hy2"}:
        return uri, None
    try:
        parameters = urllib.parse.parse_qsl(
            parsed.query, keep_blank_values=True, max_num_fields=64
        )
    except ValueError as error:
        raise ValueError("subscription_tls_pin_invalid") from error
    uri_pins = [value for name, value in parameters if name == "pinSHA256"]
    if len(uri_pins) > 1:
        raise ValueError("subscription_tls_pin_invalid")
    uri_pin = _normalize_sha256(uri_pins[0]) if uri_pins else None
    metadata_pin = _normalize_sha256(configured_pin) if configured_pin else None
    if uri_pin and metadata_pin and uri_pin != metadata_pin:
        raise ValueError("subscription_tls_pin_mismatch")
    pin = metadata_pin or uri_pin
    if metadata_pin and not uri_pin:
        parameters.append(("pinSHA256", metadata_pin))
        parsed = parsed._replace(query=urllib.parse.urlencode(parameters))
        uri = urllib.parse.urlunsplit(parsed)
    return uri, pin


def _with_remark(uri: str, remark: str) -> str:
    parsed = urllib.parse.urlsplit(uri)
    if not parsed.scheme:
        return uri
    return urllib.parse.urlunsplit(
        parsed._replace(fragment=urllib.parse.quote(remark, safe=""))
    )


class SubscriptionService:
    def __init__(self, store: Store) -> None:
        self.store = store

    def build(self, user_id: str) -> dict[str, Any]:
        data = self.store.load()
        user = next((item for item in data["users"] if item["id"] == user_id), None)
        if user is None:
            raise ValueError("user_not_found")
        if not user.get("enabled", True) or user.get("subscription_state", "active") != "active":
            raise ValueError("subscription_inactive")
        identity = {"user_id": user["id"], "remark": user["name"]}
        entries = []
        mtproto = []
        for key in data.get("access_keys", []):
            if key["user_id"] != user_id or key["state"] != "active":
                continue
            for protocol, endpoint in key.get("endpoints", {}).items():
                if not endpoint.get("enabled", True):
                    continue
                node_id = endpoint.get("node_id", "local")
                uri = endpoint.get("uri")
                if uri:
                    pin = None
                    if protocol == "hysteria2":
                        uri, pin = _hysteria_pin(uri, endpoint.get("pin_sha256"))
                    uri = _with_remark(uri, identity["remark"])
                    entry = {
                        "access_key_id": key["id"],
                        "node_id": node_id,
                        "protocol": protocol,
                        "remark": identity["remark"],
                        "uri": uri,
                    }
                    if pin:
                        entry["tls"] = {
                            "verification": "leaf-sha256",
                            "pin_sha256": pin,
                        }
                    entries.append(entry)
                if protocol == "telemt":
                    for field in ("tg_uri", "https_uri"):
                        if endpoint.get(field):
                            mtproto.append(
                                {
                                    "access_key_id": key["id"],
                                    "node_id": node_id,
                                    "format": field,
                                    "uri": endpoint[field],
                                }
                            )
        entries.sort(key=lambda item: (item["node_id"], item["protocol"], item["access_key_id"]))
        mtproto.sort(key=lambda item: (item["node_id"], item["format"], item["access_key_id"]))
        plain = "\n".join(item["uri"] for item in entries)
        encoded = base64.b64encode(plain.encode()).decode() if plain else ""
        return {
            "user_id": identity["user_id"],
            "entries": entries,
            "mtproto": mtproto,
            "plain": plain,
            "base64": encoded,
        }

    def base_url(self) -> Optional[str]:
        return self.store.load()["settings"].get("subscription_base_url")

    def set_base_url(self, value: Optional[str]) -> Optional[str]:
        if value is not None:
            value = value.strip().rstrip("/")
            parsed = urllib.parse.urlsplit(value)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("subscription_invalid_url")
        data = self.store.load()
        data["settings"]["subscription_base_url"] = value
        self.store.save(data)
        return value

    def url(self, token: str) -> str:
        base = self.base_url()
        if not base:
            raise ValueError("subscription_url_missing")
        return f"{base}/sub/{urllib.parse.quote(token, safe='')}"

    def qr(self, token: str, *, ascii_mode: bool = False, width: Optional[int] = None) -> str:
        binary = shutil.which("qrencode")
        if binary is None:
            raise ValueError("qrencode_not_installed")
        url = self.url(token)
        plain = ascii_mode or bool(os.environ.get("NO_COLOR"))
        output_type = "ASCII" if plain else "ANSIUTF8"
        result = subprocess.run(
            [binary, "-t", output_type, "-o", "-"],
            input=url,
            text=True,
            capture_output=True,
            check=False,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise ValueError("qrencode_failed")
        lines = result.stdout.rstrip("\n").splitlines()
        ansi = re.compile(r"\x1b\[[0-9;]*m")
        qr_width = max((len(ansi.sub("", line)) for line in lines), default=0)
        terminal_width = width or shutil.get_terminal_size((80, 24)).columns
        if terminal_width >= qr_width + len(url) + 3:
            middle = len(lines) // 2
            visible = len(ansi.sub("", lines[middle]))
            lines[middle] += " " * (qr_width - visible + 3) + url
        else:
            lines.extend(["", url])
        return "\n".join(lines) + "\n"

    def render(self, user_id: str, output_format: str) -> str:
        subscription = self.build(user_id)
        if output_format == "json":
            return json.dumps(subscription, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if output_format not in {"plain", "base64"}:
            raise ValueError("subscription_invalid_format")
        content = subscription[output_format]
        if not content:
            raise ValueError("subscription_empty")
        return content + "\n"

    def export(self, user_id: str, output_format: str, destination: Path) -> Path:
        content = self.render(user_id, output_format)
        destination = destination.expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix=".subscription-", dir=destination.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return destination
