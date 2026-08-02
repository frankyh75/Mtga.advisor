"""Exportiert ein Deck als Arena-kompatiblen Text.

Format:
    4 Lightning Strike
    2 Shock

    3 Duress

Mainboard-Zeilen, Leerzeile, Sideboard-Zeilen.
Unterstützt beide Deck-Schemas:
  - arena-deck.v1  (manueller Import via `deck import`)
  - deck.v1        (Memory-Scan via `deck-scan`)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Schema-Konstanten
ARENA_DECK_SCHEMA = "arena-deck.v1"
DECK_V1_SCHEMA = "deck.v1"

# Standard-Suchpfade für Deck-Dateien
DEFAULT_DECK_DIRS = ["out", "out-decks"]


class DeckNotFoundError(FileNotFoundError):
    """Deck-ID wurde in keiner durchsuchten Datei gefunden."""


class DeckExportError(ValueError):
    """Deck-JSON ist malformed oder hat ein unbekanntes Schema."""


def export_deck_to_arena_text(deck: dict[str, Any]) -> str:
    """Konvertiere ein Deck-JSON in Arena-kompatiblen Text.

    Args:
        deck: Deck-Payload mit Schema ``arena-deck.v1`` oder ``deck.v1``.

    Returns:
        Arena-Text: Mainboard-Zeilen, Leerzeile, Sideboard-Zeilen.
        Sideboard fehlt → nur Mainboard ohne Leerzeile am Ende.

    Raises:
        DeckExportError: Unbekanntes Schema oder malformed Payload.
    """
    schema = deck.get("schema", "")
    if schema == ARENA_DECK_SCHEMA:
        mainboard = _extract_arena_v1_cards(deck, "mainboard")
        sideboard = _extract_arena_v1_cards(deck, "sideboard")
    elif schema == DECK_V1_SCHEMA:
        mainboard = _extract_deck_v1_cards(deck, "mainboard")
        sideboard = _extract_deck_v1_cards(deck, "sideboard")
    else:
        raise DeckExportError(
            f"Unbekanntes Deck-Schema: {schema!r}. "
            f"Erwartet: {ARENA_DECK_SCHEMA!r} oder {DECK_V1_SCHEMA!r}."
        )

    lines: list[str] = []
    for name, count in mainboard:
        lines.append(f"{count} {name}")
    if sideboard:
        lines.append("")  # Leerzeile zwischen Mainboard und Sideboard
        for name, count in sideboard:
            lines.append(f"{count} {name}")
    return "\n".join(lines) + "\n"


def load_deck_by_id(
    deck_id: str,
    search_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Suche ein Deck anhand seiner Deck-ID in den Suchpfaden.

    Durchsucht:
      1. ``arena_deck.json`` in jedem Suchpfad (arena-deck.v1, Match via deckId)
      2. ``decks/deck-*.json`` in jedem Suchpfad (deck.v1, Match via deckId)
      3. ``decks.json`` in jedem Suchpfad (beide Schemas, Match via deckId)

    Args:
        deck_id: Die zu suchende Deck-ID.
        search_dirs: Verzeichnisse, die durchsucht werden sollen.
                     Default: ``["out", "out-decks"]`` relativ zum CWD.

    Returns:
        Das erste Deck-JSON, dessen ``deckId`` matcht.

    Raises:
        DeckNotFoundError: Kein Deck mit dieser ID gefunden.
    """
    if search_dirs is None:
        search_dirs = [Path(d) for d in DEFAULT_DECK_DIRS]

    for base in search_dirs:
        # 1. arena_deck.json (Single-Deck-Datei aus `deck import`)
        arena_path = base / "arena_deck.json"
        if arena_path.exists():
            try:
                deck = json.loads(arena_path.read_text(encoding="utf-8"))
                if str(deck.get("deckId", "")) == str(deck_id):
                    return deck
            except (OSError, ValueError):
                pass

        # 2. decks/deck-*.json (einzelne Deck-Dateien aus `deck-scan`)
        decks_dir = base / "decks"
        if decks_dir.is_dir():
            for deck_file in sorted(decks_dir.glob("deck-*.json")):
                try:
                    deck = json.loads(deck_file.read_text(encoding="utf-8"))
                    if str(deck.get("deckId", "")) == str(deck_id):
                        return deck
                except (OSError, ValueError):
                    continue

        # 3. decks.json (Container-Datei)
        decks_json_path = base / "decks.json"
        if decks_json_path.exists():
            try:
                container = json.loads(decks_json_path.read_text(encoding="utf-8"))
                for deck_entry in container.get("decks", []):
                    if str(deck_entry.get("deckId", "")) == str(deck_id):
                        # Container-Einträge haben evtl. keine schema/mainboard/sideboard
                        # im arena-deck.v1-Format → als deck.v1 rekonstruieren
                        if "schema" in deck_entry:
                            return deck_entry
                        # Rekonstruiere als deck.v1-Kompatibel
                        return _reconstruct_deck_v1(deck_entry)
            except (OSError, ValueError):
                pass

    raise DeckNotFoundError(
        f"Deck mit ID {deck_id!r} nicht gefunden. "
        f"Durchsucht: {[str(d) for d in search_dirs]}"
    )


def _reconstruct_deck_v1(deck_entry: dict[str, Any]) -> dict[str, Any]:
    """Rekonstruiere ein deck.v1-Payload aus einem Container-Eintrag."""
    return {
        "schema": DECK_V1_SCHEMA,
        "deckId": deck_entry.get("deckId", ""),
        "name": deck_entry.get("name", "Unknown"),
        "source": deck_entry.get("source", "unknown"),
        "cards": deck_entry.get("cards", {}),
        "cardsById": deck_entry.get("cardsById", {}),
    }


def _extract_arena_v1_cards(
    deck: dict[str, Any],
    section: str,
) -> list[tuple[str, int]]:
    """Extrahiere (name, count) aus arena-deck.v1-Section.

    arena-deck.v1 hat ``mainboard`` und ``sideboard`` als Liste von Dicts:
        [{"arenaId": 12345, "name": "Lightning Strike", "count": 4, ...}]
    """
    cards = deck.get(section, [])
    if not isinstance(cards, list):
        return []
    result: list[tuple[str, int]] = []
    for entry in cards:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or f"ID:{entry.get('arenaId', '?')}"
        count = int(entry.get("count", 0))
        if count <= 0:
            continue
        result.append((str(name), count))
    return result


def _extract_deck_v1_cards(
    deck: dict[str, Any],
    section: str,
) -> list[tuple[str, int]]:
    """Extrahiere (name, count) aus deck.v1-Section.

    deck.v1 hat ``cards`` als Dict mit Section-Keys:
        {"mainboard": [{"cardId": 12345, "name": "Lightning Strike", "count": 4}]}
    """
    cards_by_section = deck.get("cards", {})
    if not isinstance(cards_by_section, dict):
        return []
    cards = cards_by_section.get(section, [])
    if not isinstance(cards, list):
        return []
    result: list[tuple[str, int]] = []
    for entry in cards:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name") or f"ID:{entry.get('cardId', '?')}"
        count = int(entry.get("count", 0))
        if count <= 0:
            continue
        result.append((str(name), count))
    return result