from __future__ import annotations

import json
import ipaddress
import os
import unicodedata
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional


DEFAULT_GEOIP_URL = "https://api.country.is/"
DEFAULT_RDAP_URL = "https://rdap.org/ip/{ip}"


def normalize_country_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    searchable = "".join(
        "" if unicodedata.combining(character) else character if character.isalnum() else " "
        for character in normalized
    )
    return " ".join(searchable.split())


def load_countries() -> list[dict[str, Any]]:
    path = Path(__file__).with_name("data") / "countries.json"
    with path.open(encoding="utf-8") as handle:
        countries = json.load(handle)
    if not isinstance(countries, list):
        raise RuntimeError("invalid countries data")
    return countries


def get_country(iso: str) -> Optional[dict[str, Any]]:
    wanted = iso.strip().upper()
    return next((country for country in load_countries() if country["iso"] == wanted), None)


def detect_countries(server_name: str) -> list[dict[str, Any]]:
    normalized_name = normalize_country_text(server_name)
    if not normalized_name:
        return []
    padded_name = f" {normalized_name} "
    matches: list[tuple[int, dict[str, Any]]] = []
    for country in load_countries():
        candidates = [country["iso"], *country["names"].values(), *country["aliases"]]
        scores = []
        for candidate in candidates:
            normalized_alias = normalize_country_text(candidate)
            if normalized_alias and f" {normalized_alias} " in padded_name:
                scores.append(len(normalized_alias.split()) * 1000 + len(normalized_alias))
        if scores:
            matches.append((max(scores), country))
    if not matches:
        return []
    best_score = max(score for score, _ in matches)
    return sorted(
        (country for score, country in matches if score == best_score),
        key=lambda country: country["iso"],
    )


def _leading_flag(value: str) -> Optional[str]:
    if len(value) < 2:
        return None
    if all(0x1F1E6 <= ord(character) <= 0x1F1FF for character in value[:2]):
        return value[:2]
    return None


def format_remark(name: str, country: dict[str, Any]) -> str:
    flag = country["flag"]
    if name.startswith(flag):
        return name
    existing = _leading_flag(name)
    unflagged = name[len(existing):].lstrip() if existing else name
    return f"{flag} {unflagged}"


def format_key_name(
    name: str,
    country: dict[str, Any],
    language: str = "en",
    max_length: int = 128,
) -> str:
    """Build a stable client label: flag, localized country, then key name."""
    base = name.strip()
    existing = _leading_flag(base)
    if existing:
        base = base[len(existing):].lstrip()
        if " · " in base:
            base = base.split(" · ", 1)[1].strip()
    else:
        for known_name in (*country["names"].values(), country["iso"]):
            prefix = f"{known_name} ·"
            if base.casefold().startswith(prefix.casefold()):
                base = base[len(prefix):].lstrip()
                break
    base = base or "default"
    localized_country = country["names"].get(language, country["names"]["en"])
    prefix = f"{country['flag']} {localized_country} · "
    available = max_length - len(prefix)
    if available < 1:
        raise ValueError("invalid_device_name")
    return prefix + base[:available].rstrip()


def detect_public_location(
    *,
    timeout: float = 2.0,
    url: Optional[str] = None,
    rdap_url: Optional[str] = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Optional[dict[str, str]]:
    """Resolve public IP, preferring its authoritative RDAP network country."""
    endpoint = url or os.environ.get("XRAYDO_GEOIP_URL", DEFAULT_GEOIP_URL)
    if endpoint.casefold() in {"off", "none", "disabled"}:
        return None
    try:
        request = urllib.request.Request(
            endpoint,
            headers={"Accept": "application/json", "User-Agent": "xraydo/geoip"},
        )
        with opener(request, timeout=timeout) as response:
            payload = json.loads(response.read(4097))
        if not isinstance(payload, dict):
            return None
        address = str(payload.get("ip", "")).strip()
        geoip_iso = str(payload.get("country", "")).strip().upper()
        parsed_address = ipaddress.ip_address(address)
        if not parsed_address.is_global or get_country(geoip_iso) is None:
            return None
        resolved_rdap_url = rdap_url or os.environ.get(
            "XRAYDO_RDAP_URL", DEFAULT_RDAP_URL
        )
        if resolved_rdap_url.casefold() not in {"off", "none", "disabled"}:
            rdap_endpoint = resolved_rdap_url.format(ip=str(parsed_address))
            try:
                rdap_request = urllib.request.Request(
                    rdap_endpoint,
                    headers={"Accept": "application/rdap+json, application/json", "User-Agent": "xraydo/geoip"},
                )
                with opener(rdap_request, timeout=timeout) as response:
                    raw_rdap = response.read(1_048_577)
                if len(raw_rdap) <= 1_048_576:
                    rdap_payload = json.loads(raw_rdap)
                    rdap_iso = str(rdap_payload.get("country", "")).strip().upper()
                    if get_country(rdap_iso) is not None:
                        return {
                            "ip": str(parsed_address),
                            "country": rdap_iso,
                            "geoip_country": geoip_iso,
                            "source": rdap_endpoint,
                            "version": "2",
                        }
            except (OSError, ValueError, TypeError, json.JSONDecodeError, KeyError):
                pass
        return {
            "ip": str(parsed_address),
            "country": geoip_iso,
            "geoip_country": geoip_iso,
            "source": endpoint,
            "version": "2",
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
