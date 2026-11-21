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

    # Namens-Index aus der Karten-DB: name(lower) → {grpIds}.
    # MTGA zählt Karten über ALLE Prints (verschiedene grpIds mit demselben
    # Kartennamen). Für die owned-Berechnung muss die Collection-Summe über
    # alle grpIds mit identischem Namen gebildet werden, nicht nur über die
    # eine grpId, die in der Deckliste steht.
    name_to_grp_ids: dict[str, set[int]] = {}
    if card_db:
        for gid, meta in card_db.items():
            n = _card_name_key(meta.get("name"))
            if n:
                name_to_grp_ids.setdefault(n, set()).add(int(gid))

    # Alle Karten des Decks aggregieren (MainDeck + Sideboard + CommandZone + Companions)
    all_card_entries = _collect_deck_cards(deck)
    resolved_cards = sum(e["quantity"] for e in all_card_entries)

    # Karten ohne Arena-ID (unresolved-arena-id, z.B. TRK über Scryfall nicht
    # auflösbar): zählen in totalCards mit, fließen aber NICHT in
    # owned/missing ein — ihre grpId ist unbekannt, kein Collection-Abgleich
    # möglich.
    unresolved_entries = _collect_unresolved_cards(deck)
    unresolved_cards = sum(int(e["quantity"]) for e in unresolved_entries)
    unknown_sets = sorted({str(e.get("set")) for e in unresolved_entries if e.get("set")})
    total_cards = resolved_cards + unresolved_cards

    owned_entries: list[dict[str, Any]] = []
    missing_entries: list[dict[str, Any]] = []

    for entry in all_card_entries:
        card_id = entry["cardId"]
        needed = entry["quantity"]
        zone = entry["zone"]

        meta = (card_db or {}).get(card_id, {})
        name = meta.get("name", f"#{card_id}")
        rarity = str(meta.get("rarity", "unknown")).lower()

        # Grundländer sind in MTGA unbegrenzt verfügbar → immer owned, nie fehlend.
        is_basic_land = _card_name_key(name) in _BASIC_LAND_NAMES
        if is_basic_land:
            owned_qty = needed
        else:
            owned_qty = _owned_across_prints(
                card_id, name, collection_cards, name_to_grp_ids
            )

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
    # Der Score wird nur auf der AUFLÖSBAREN Basis gerechnet (resolved_cards),
    # nicht auf total — sonst würde eine nicht auflösbare Karte den Score
    # drücken, ohne dass der Nutzer etwas dagegen tun kann. Die Basis ist über
    # scoreBasis transparent ausgewiesen. Ohne auflösbare Karten gibt es nichts
    # zu bewerten: leeres Deck → 100.0 (wie bisher), nur-unresolved → 0.0.
    if total_cards == 0:
        completion_score = 100.0
    elif resolved_cards == 0:
        completion_score = 0.0
    else:
        completion_score = round((total_owned / resolved_cards) * 100, 1)

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
    if any(e.get("reason") == "unresolved-arena-id" for e in unresolved_entries):
        warnings.append("arena-id-unbekannt")

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
            # Karten ohne Arena-ID: in totalCards enthalten, aber nicht in
            # ownedCards/missingCards bewertbar (siehe scoreBasis).
            "unresolvedCards": unresolved_cards,
            "unknownSets": unknown_sets,
            # Basis des completionScore: owned/resolved ist der Score, total
            # enthält auch die nicht auflösbaren Karten.
            "scoreBasis": {
                "owned": total_owned,
                "resolved": resolved_cards,
                "total": total_cards,
            },
        },
        "ownedCards": owned_entries,
        "missingCards": missing_entries,
        "unresolvedCards": unresolved_entries,
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

    Einträge mit ``arenaId: null`` (``reason: "unresolved-arena-id"`` — Karte
    über Set+CollectorNumber aufgenommen, aber ohne Arena-ID in der Karten-DB)
    landen im Feld ``unresolvedCards`` statt in den Zonen. So zählen sie in
    ``analyze_deck`` in ``totalCards`` mit, ohne owned/missing zu verfälschen.
    """
    def _convert_zone(cards: list[dict[str, Any]], zone: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        out: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        for card in cards:
            arena_id = card.get("arenaId")
            if arena_id is None:
                unresolved.append({
                    "name": card.get("name", "Unknown"),
                    "set": card.get("set", ""),
                    "collectorNumber": card.get("collectorNumber", ""),
                    "quantity": int(card.get("count", 1)),
                    "zone": zone,
                    "reason": card.get("reason", "unresolved-arena-id"),
                })
                continue
            try:
                card_id = int(arena_id)
            except (TypeError, ValueError):
                continue
            quantity = int(card.get("count", 1))
            out.append({"cardId": card_id, "quantity": quantity})
        return out, unresolved

    main_deck, unresolved_main = _convert_zone(arena_deck.get("mainboard", []), "mainDeck")
    sideboard, unresolved_side = _convert_zone(arena_deck.get("sideboard", []), "sideboard")
    unresolved_cards = unresolved_main + unresolved_side
    unresolved_cards += [
        {"name": item.get("name", "Unknown"), "quantity": item["count"],
         "zone": item.get("zone", "mainDeck"), "reason": item["reason"],
         "line": item.get("line", "")}
        for item in arena_deck.get("diagnostics", {}).get("unresolved", [])
        if item.get("reason") == "unknown-card-name" and "count" in item
    ]

    result = {
        "deckId": arena_deck.get("deckId"),
        "name": arena_deck.get("name", "Imported Deck"),
        "format": arena_deck.get("format", "unknown"),
        "mainDeck": main_deck,
        "sideboard": sideboard,
        "commandZone": [],
        "companions": [],
    }
    if unresolved_cards:
        result["unresolvedCards"] = unresolved_cards
    return result


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

def _card_name_key(name: Any) -> str:
    """Normalisiere einen Kartennamen für Print-übergreifendes Matching.

    MTGA speichert Grundländer und Reprints unter verschiedenen grpIds, aber
    mit identischem (oder fast identischem) Namen. Für die owned-Berechnung
    muss über alle Prints summiert werden.
    """
    if not name:
        return ""
    # Normierung: lower-case, stripped, ASCII-Doppel-Faces-Trenner "//" entfernen,
    # so dass z.B. "Swamp" == "swamp" und Reprints mit identischem Namen matchen.
    return str(name).strip().lower()


def _owned_across_prints(
    card_id: int,
    name: str,
    collection_cards: dict[int, int],
    name_to_grp_ids: dict[str, set[int]],
) -> int:
    """Berechne die owned-Menge über alle Prints (grpIds) einer Karte.

    MTGA zählt Karten über alle Prints mit demselben Namen. Die Deckliste
    referenziert nur eine grpId, aber in der Collection kann die Karte unter
    mehreren grpIds liegen (Reprints, verschiedene Sets). Wir summieren die
    Collection-Mengen über alle grpIds, deren Name mit dem der Deck-Karte
    übereinstimmt.

    Fallback: keine Karten-DB oder Name nicht gefunden → exakter grpId-Match
    (wie bisher).
    """
    name_key = _card_name_key(name)
    if name_key and name_key in name_to_grp_ids:
        return sum(
            collection_cards.get(gid, 0) for gid in name_to_grp_ids[name_key]
        )
    # Keine Karten-DB oder Name unbekannt → exakter grpId-Match (altes Verhalten).
    return collection_cards.get(card_id, 0)


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


def _collect_unresolved_cards(deck: dict[str, Any]) -> list[dict[str, Any]]:
    """Sammle Karten ohne Arena-ID aus ``deck["unresolvedCards"]``.

    Das Feld wird von ``arena_deck_to_deck_cards`` für Einträge mit
    ``arenaId: null`` (reason ``unresolved-arena-id``) befüllt. Gespeicherte
    Decks ohne das Feld haben keine unresolved Karten.

    Returns:
        Liste von Dicts mit name, set, collectorNumber, quantity, zone.
    """
    cards = deck.get("unresolvedCards")
    if not isinstance(cards, list):
        return []
    entries: list[dict[str, Any]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        try:
            quantity = int(card.get("quantity", 1))
        except (TypeError, ValueError):
            continue
        if quantity <= 0:
            continue
        entries.append({
            "name": str(card.get("name", "Unknown")),
            "set": str(card.get("set", "")),
            "collectorNumber": str(card.get("collectorNumber", "")),
            "quantity": quantity,
            "zone": str(card.get("zone", "")),
            "reason": str(card.get("reason", "unresolved-arena-id")),
            **({"line": card["line"]} if "line" in card else {}),
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