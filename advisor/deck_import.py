from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DECK_SCHEMA = "arena-deck.v1"
DECK_LINE_RE = re.compile(r"^(?P<count>\d+)\s+(?P<name>.+?)(?:\s+\([^)]+\)\s+\d+)?$")


def import_arena_deck(
    text: str,
    *,
    card_db: dict[int, dict[str, Any]],
    deck_format: str,
    name: str | None = None,
    collection_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Parse Arena text export into arena_deck.json payload.

    When ``collection_ids`` is provided, reprint resolution (same card name
    across multiple sets) prefers the grpId that actually exists in the
    player's collection. This ensures the imported deck uses IDs that match
    the collection, so downstream analysis correctly reports owned cards.
    If no candidate matches the collection, the alphabetically-last set is
    used as fallback (preserving the original behaviour).
    """
    mainboard: list[dict[str, Any]] = []
    sideboard: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    section = "mainboard"
    deck_name = name or "Imported Deck"
    # Keep the full card database and fail conservatively on same-name ambiguity.
    name_to_cards = _build_name_index(card_db)

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower == "deck":
            section = "mainboard"
            continue
        if lower == "sideboard":
            section = "sideboard"
            continue
        if lower.startswith("deck name:"):
            deck_name = line.split(":", 1)[1].strip() or deck_name
            continue

        parsed = _parse_deck_line(line)
        if parsed is None:
            unresolved.append({"line": line, "reason": "unparseable-line"})
            continue
        count, card_name = parsed
        candidates = name_to_cards.get(_normalize_name(card_name), [])
        if not candidates:
            unresolved.append({"line": line, "name": card_name, "reason": "unknown-card-name"})
            continue
        if len(candidates) > 1:
            # Same card name across multiple sets (reprints).
            # Prefer a grpId that exists in the player's collection so the
            # imported deck IDs match the collection IDs used downstream.
            # If no candidate is in the collection (or no collection given),
            # fall back to the alphabetically-last set (original behaviour).
            chosen = _resolve_reprint(candidates, collection_ids)
            entry = dict(chosen)
            entry["count"] = count
            if section == "sideboard":
                sideboard.append(entry)
            else:
                mainboard.append(entry)
            continue

        entry = dict(candidates[0])
        entry["count"] = count
        if section == "sideboard":
            sideboard.append(entry)
        else:
            mainboard.append(entry)

    payload = {
        "schema": DECK_SCHEMA,
        "deckId": _deck_id(deck_name, deck_format, mainboard, sideboard),
        "name": deck_name,
        "format": deck_format,
        "importedAt": _iso_now(),
        "mainboard": mainboard,
        "sideboard": sideboard,
        "diagnostics": {
            "unresolved": unresolved,
            "ambiguous": ambiguous,
            "warnings": _warnings(unresolved, ambiguous),
        },
    }
    return payload


def write_deck(deck: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "arena_deck.json"
    path.write_text(json.dumps(deck, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _build_name_index(card_db: dict[int, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for arena_id, meta in card_db.items():
        name = meta.get("name")
        if not name:
            continue
        key = _normalize_name(str(name))
        entry = {
            "arenaId": arena_id,
            "name": str(name),
            "set": meta.get("set", ""),
            "collectorNumber": meta.get("collector_number", ""),
            "rarity": meta.get("rarity", "unknown"),
        }
        index.setdefault(key, []).append(entry)

    for entries in index.values():
        entries.sort(key=lambda entry: (str(entry.get("set", "")), int(entry["arenaId"])))
    return index


def _parse_deck_line(line: str) -> tuple[int, str] | None:
    match = DECK_LINE_RE.match(line)
    if not match:
        return None
    return int(match.group("count")), match.group("name").strip()


def _resolve_reprint(
    candidates: list[dict[str, Any]],
    collection_ids: set[int] | None,
) -> dict[str, Any]:
    """Pick the best candidate for a reprinted card name.

    If ``collection_ids`` is provided and one or more candidates have an
    ``arenaId`` that exists in the collection, the first matching candidate
    (sorted by set then arenaId) is returned — guaranteeing the imported
    deck uses IDs that align with the player's collection.

    If no candidate matches the collection (or no collection is given), the
    alphabetically-last candidate is returned (original behaviour: newest
    set as a heuristic).
    """
    if collection_ids:
        for cand in candidates:
            if int(cand["arenaId"]) in collection_ids:
                return cand
    return candidates[-1]


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def _deck_id(
    name: str,
    deck_format: str,
    mainboard: list[dict[str, Any]],
    sideboard: list[dict[str, Any]],
) -> str:
    basis = json.dumps(
        {
            "name": name,
            "format": deck_format,
            "mainboard": mainboard,
            "sideboard": sideboard,
        },
        sort_keys=True,
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _warnings(unresolved: list[dict[str, Any]], ambiguous: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if unresolved:
        warnings.append("unresolved-deck-lines")
    if ambiguous:
        warnings.append("ambiguous-card-names")
    return warnings


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
