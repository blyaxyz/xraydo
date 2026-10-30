from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any, Optional


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
