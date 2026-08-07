"""Deck-Analyse: bewertet ein Deck gegen Collection + Wildcards.

Liest ein Deck aus ``deck-cards.json`` (Schema ``deck-cards.v1``), prüft
gegen ``collection.json`` welche Karten owned/missing sind, berechnet den
Wildcard-Bedarf nach Seltenheit und gibt eine Craft-Priorität aus.

Schema der Ausgabe: ``advisor-analyze.v1``.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ANALYZE_SCHEMA = "advisor-analyze.v1"

# Seltenheits-Reihenfolge für Craft-Priorität (Mythic zuerst = höchste Priorität).
RARITY_PRIORITY = ("mythic", "rare", "uncommon", "common", "unknown")

# Wildcard-Typ → Seltenheits-Name Mapping.
_WILDCARD_KEYS = {
    "mythic": "mythics",
    "rare": "rares",
    "uncommon": "uncommons",
    "common": "commons",
}

# Grundländer (Basic Lands) sind in MTGA unbegrenzt verfügbar — sie zählen
# nie als fehlend und brauchen keine Wildcards. Erkennung über den Kartennamen.
_BASIC_LAND_NAMES = {"forest", "island", "swamp", "mountain", "plains"}


def analyze_deck(
    *,
    deck: dict[str, Any],
    collection: dict[str, Any],
    wildcards: dict[str, Any],
    card_db: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Bewerte ein Deck mit Kartenliste gegen Collection + Wildcards.

    Args:
        deck: Ein einzelnes Deck aus deck-cards.json (mit mainDeck, sideboard, etc.).
        collection: collection.json Inhalt (Schema collection.v1).
        wildcards: wildcards.json Inhalt (Schema wildcards.v1).
        card_db: Optional Karten-DB für Namens-/Seltenheits-Auflösung.

    Returns:
        Analyse-Ergebnis als Dict (Schema advisor-analyze.v1).
    """
    # Collection-Karten als {int(cardId): int(quantity)}
    collection_cards = {
        int(cid): int(qty)
        for cid, qty in collection.get("cards", {}).items()
    }

    # Wildcards aus wildcards.json
    wc = wildcards.get("wildcards", {})
    owned_wildcards = {
        "common": _safe_int(wc.get("commons", 0)),
        "uncommon": _safe_int(wc.get("uncommons", 0)),
        "rare": _safe_int(wc.get("rares", 0)),
        "mythic": _safe_int(wc.get("mythics", 0)),
    }

    # Alle Karten des Decks aggregieren (MainDeck + Sideboard + CommandZone + Companions)
    all_card_entries = _collect_deck_cards(deck)
    total_cards = sum(e["quantity"] for e in all_card_entries)

    owned_entries: list[dict[str, Any]] = []
    missing_entries: list[dict[str, Any]] = []

    for entry in all_card_entries:
        card_id = entry["cardId"]
        needed = entry["quantity"]
        zone = entry["zone"]
        owned_qty = collection_cards.get(card_id, 0)

        meta = (card_db or {}).get(card_id, {})
        name = meta.get("name", f"#{card_id}")
        rarity = str(meta.get("rarity", "unknown")).lower()

        # Grundländer sind in MTGA unbegrenzt verfügbar → immer owned, nie fehlend.
        is_basic_land = name.strip().lower() in _BASIC_LAND_NAMES
        if is_basic_land:
            owned_qty = needed

        missing_qty = max(0, needed - owned_qty)

        card_info = {
            "cardId": card_id,
            "name": name,
            "rarity": rarity,
            "zone": zone,
            "needed": needed,
            "owned": owned_qty,
            "missing": missing_qty,
        }

        if missing_qty > 0:
            missing_entries.append(card_info)
        else:
            owned_entries.append(card_info)

    # Wildcard-Bedarf nach Seltenheit
    wc_needed = {"common": 0, "uncommon": 0, "rare": 0, "mythic": 0, "unknown": 0}
    for entry in missing_entries:
        rarity = entry["rarity"]
        if rarity in wc_needed:
            wc_needed[rarity] += entry["missing"]
        else:
            wc_needed["unknown"] += entry["missing"]

    # Prüfen: reichen die Wildcards?
    wc_sufficiency: dict[str, dict[str, Any]] = {}
    for rarity_key, wc_key in _WILDCARD_KEYS.items():
        needed = wc_needed[rarity_key]
        have = owned_wildcards.get(rarity_key, 0)
        wc_sufficiency[rarity_key] = {
            "needed": needed,
            "owned": have,
            "sufficient": have >= needed,
            "shortfall": max(0, needed - have),
        }

    # Craft-Priorität: fehlende Karten nach Seltenheit sortiert (Mythic zuerst)
    craft_priority = _build_craft_priority(missing_entries, card_db)

    # Completion-Score: wieviele Karten des Decks sind in der Collection vorhanden?
    # Für jede Karte: min(owned, needed) → deck-relevant
    total_owned = sum(min(e["owned"], e["needed"]) for e in owned_entries + missing_entries)
    completion_score = 100.0 if total_cards == 0 else round(
        (total_owned / total_cards) * 100, 1
    )

    # Warnings
    warnings: list[str] = []
    if not collection.get("cards"):
        warnings.append("Collection ist leer oder fehlt.")
    if not wildcards.get("wildcards"):
        warnings.append("Wildcard-Daten fehlen.")
    if wc_needed.get("unknown", 0) > 0 and card_db is None:
        warnings.append("Karten-DB nicht geladen — Seltenheit für einige Karten unbekannt.")
    if any(not v["sufficient"] for v in wc_sufficiency.values()):
        insufficient = [r for r, v in wc_sufficiency.items() if not v["sufficient"]]
        warnings.append(f"Wildcard-Bestand nicht ausreichend für: {', '.join(insufficient)}")

    return {
        "schema": ANALYZE_SCHEMA,
        "generatedAt": _iso_now(),
        "deck": {
            "deckId": deck.get("deckId"),
            "name": deck.get("name", "Unknown"),
            "format": deck.get("format", "unknown"),
        },
        "summary": {
            "completionScore": completion_score,
            "totalCards": total_cards,
            "ownedCards": total_owned,
            "missingCards": sum(e["missing"] for e in missing_entries),
            "ownedUnique": len(owned_entries),
            "missingUnique": len(missing_entries),
        },
        "ownedCards": owned_entries,
        "missingCards": missing_entries,
        "wildcardNeed": {
            "needed": {k: v for k, v in wc_needed.items() if v > 0},
            "owned": owned_wildcards,
            "sufficiency": wc_sufficiency,
        },
        "craftPriority": craft_priority,
        "warnings": warnings,
    }


def arena_deck_to_deck_cards(arena_deck: dict[str, Any]) -> dict[str, Any]:
    """Konvertiere ein ``import_arena_deck``-Payload (Schema ``arena-deck.v1``)
    in das ``deck-cards.v1``-Format, das ``analyze_deck`` erwartet.

    Mapping: ``mainboard`` → ``mainDeck``, ``sideboard`` → ``sideboard``,
    ``arenaId`` → ``cardId``, ``count`` → ``quantity``.
    ``commandZone``/``companions`` bleiben leer (Arena-Textexport kennt sie nicht).
    """
    def _convert_zone(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for card in cards:
            arena_id = card.get("arenaId")
            if arena_id is None:
                continue
            try:
                card_id = int(arena_id)
            except (TypeError, ValueError):
                continue
            quantity = int(card.get("count", 1))
            out.append({"cardId": card_id, "quantity": quantity})
        return out

    return {
        "deckId": arena_deck.get("deckId"),
        "name": arena_deck.get("name", "Imported Deck"),
        "format": arena_deck.get("format", "unknown"),
        "mainDeck": _convert_zone(arena_deck.get("mainboard", [])),
        "sideboard": _convert_zone(arena_deck.get("sideboard", [])),
        "commandZone": [],
        "companions": [],
    }


def load_deck_cards(
    deck_cards_path: Path,
    deck_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    """Lade ein einzelnes Deck aus deck-cards.json.

    Args:
        deck_cards_path: Pfad zu deck-cards.json.
        deck_id: Optionale Deck-ID zum Filtern.
        name: Optionaler Deck-Name zum Filtern (wird ignoriert, wenn deck_id gegeben).

    Returns:
        Das Deck-Dict oder raises ValueError wenn nicht gefunden.
    """
    data = json.loads(deck_cards_path.read_text(encoding="utf-8"))
    decks = data.get("decks", [])

    for d in decks:
        if deck_id and d.get("deckId") == deck_id:
            return d
        if name and not deck_id and d.get("name", "").lower() == name.lower():
            return d

    # Partial match als Fallback
    if deck_id:
        for d in decks:
            if d.get("deckId", "").startswith(deck_id):
                return d

    raise ValueError(
        f"Deck nicht gefunden in {deck_cards_path}: "
        f"deckId={deck_id!r}, name={name!r}"
    )


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _collect_deck_cards(deck: dict[str, Any]) -> list[dict[str, Any]]:
    """Sammle alle Karten aus MainDeck, Sideboard, CommandZone, Companions.

    Returns:
        Liste von Dicts mit cardId, quantity, zone.
    """
    entries: list[dict[str, Any]] = []
    zone_map = {
        "mainDeck": deck.get("mainDeck", []),
        "sideboard": deck.get("sideboard", []),
        "commandZone": deck.get("commandZone", []),
        "companions": deck.get("companions", []),
    }
    for zone_name, cards in zone_map.items():
        if not isinstance(cards, list):
            continue
        for card in cards:
            if not isinstance(card, dict):
                continue
            card_id = card.get("cardId")
            if card_id is None:
                continue
            try:
                card_id = int(card_id)
            except (TypeError, ValueError):
                continue
            if card_id <= 0:
                continue
            quantity = int(card.get("quantity", 1))
            if quantity <= 0:
                continue
            entries.append({
                "cardId": card_id,
                "quantity": quantity,
                "zone": zone_name,
            })
    return entries


def _build_craft_priority(
    missing_entries: list[dict[str, Any]],
    card_db: dict[int, dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Baue die Craft-Prioritäts-Liste, sortiert nach Seltenheit (Mythic zuerst)."""
    # Sortiere: Mythic → Rare → Uncommon → Common → Unknown
    def rarity_rank(rarity: str) -> int:
        try:
            return RARITY_PRIORITY.index(rarity)
        except ValueError:
            return RARITY_PRIORITY.index("unknown")

    sorted_entries = sorted(
        missing_entries,
        key=lambda e: (rarity_rank(e["rarity"]), -e["missing"], e["name"]),
    )

    # Gruppiere nach Seltenheit
    priority_groups: dict[str, list[dict[str, Any]]] = {}
    for entry in sorted_entries:
        rarity = entry["rarity"]
        priority_groups.setdefault(rarity, []).append({
            "cardId": entry["cardId"],
            "name": entry["name"],
            "missing": entry["missing"],
            "zone": entry["zone"],
        })

    result: list[dict[str, Any]] = []
    for rarity in RARITY_PRIORITY:
        if rarity not in priority_groups:
            continue
        cards = priority_groups[rarity]
        result.append({
            "rarity": rarity,
            "totalMissing": sum(c["missing"] for c in cards),
            "cardCount": len(cards),
            "cards": cards,
        })
    return result


def _safe_int(value: Any) -> int:
    """Konvertiere einen Wert sicher in int (0 bei fehlendem/falschem Typ)."""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")