from __future__ import annotations

import csv
from pathlib import Path


def load_mapping(path: Path) -> dict[int, dict[str, str]]:
    if not path.exists():
        return {}
    mapping: dict[int, dict[str, str]] = {}
    with path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row:
                continue
            card_id = _parse_int(row.get("cardId"))
            if card_id is None:
                continue
            entry: dict[str, str] = {}
            scryfall_id = _parse_str(row.get("scryfall_id"))
            oracle_id = _parse_str(row.get("oracle_id"))
            if scryfall_id:
                entry["scryfall_id"] = scryfall_id
            if oracle_id:
                entry["oracle_id"] = oracle_id
            if entry:
                mapping[card_id] = entry
    return mapping


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _parse_str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
