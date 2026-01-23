from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from carddb.lookup import CardLookup


@dataclass(frozen=True)
class DeckAnalysisResult:
    payload: dict
    coverage: float


def build_deck_analysis(deck: dict, lookup: CardLookup) -> DeckAnalysisResult:
    deck_id = deck.get("id", "")
    name = deck.get("name")
    fmt = deck.get("format")
    source = deck.get("source")
    analyzed_at = _iso_now()

    entries = _collect_entries(deck)
    unique_ids = {entry["cardId"] for entry in entries}
    total_unique = len(unique_ids)
    mapped_ids: set[int] = set()
    unknown_cards: list[int] = []
    cards: list[dict] = []

    for entry in entries:
        card_id = entry["cardId"]
        quantity = entry["quantity"]
        card = lookup.lookup_arena_id(card_id)
        if card is None:
            if card_id not in unknown_cards:
                unknown_cards.append(card_id)
            continue
        mapped_ids.add(card_id)
        cards.append(
            {
                "cardId": card_id,
                "quantity": quantity,
                "oracleId": card.get("oracleId"),
                "name": card.get("name"),
                "manaCost": card.get("manaCost"),
                "typeLine": card.get("typeLine"),
                "types": _extract_types(card.get("typeLine")),
                "oracleText": card.get("oracleText"),
                "keywords": card.get("keywords", []),
                "colorIdentity": card.get("colorIdentity", []),
                "legalities": card.get("legalities", {}),
            }
        )

    coverage = len(mapped_ids) / total_unique if total_unique else 0.0
    payload = {
        "schema": "deck-analysis.v1",
        "deckId": deck_id,
        "name": name,
        "format": fmt,
        "source": source,
        "analyzedAt": analyzed_at,
        "mappingCoverage": round(coverage, 4),
        "unknownCards": unknown_cards,
        "cards": cards,
        "diagnostics": {"coverageWarning": coverage < 0.9},
    }
    return DeckAnalysisResult(payload=payload, coverage=coverage)


def _collect_entries(deck: dict) -> list[dict]:
    entries: list[dict] = []
    for key in ("mainDeck", "sideboard", "commandZone", "companions"):
        for entry in deck.get(key, []) or []:
            if not isinstance(entry, dict):
                continue
            card_id = entry.get("cardId")
            quantity = entry.get("quantity")
            if isinstance(card_id, int) and isinstance(quantity, int):
                entries.append({"cardId": card_id, "quantity": quantity})
    return entries


def _extract_types(type_line: str | None) -> list[str]:
    if not type_line:
        return []
    primary = type_line.split("—", 1)[0].strip()
    if not primary:
        return []
    return [part for part in primary.split() if part]


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
