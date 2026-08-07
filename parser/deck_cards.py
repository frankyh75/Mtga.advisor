"""Deck-Kartenlisten-Export aus MTGA-Logs (DeckUpsertDeckV3-Event).

Extrahiert die vollständigen Kartenlisten (MainDeck, Sideboard, CommandZone,
Companions) aus ``DeckUpsertDeckV3``-Events in ``Player.log``-Dateien und
schreibt sie als ``deck-cards.json`` (Schema ``deck-cards.v1``).

Das ``DeckUpsertDeckV3``-Event ist eine ``[UnityCrossThreadLogger]==>
DeckUpsertDeckV3 {...}``-Zeile, deren ``request``-Feld ein **stringified
JSON** ist (doppelt escaped). Die innere JSON-Struktur enthält:

- ``Summary``: DeckId, Name, Format (via Attributes)
- ``Deck``: MainDeck/Sideboard/CommandZone/Companions als
  ``[{cardId, quantity}, ...]``

Dies ist der gleiche Weg, den Untapped.gg / Arena Tutor / MTGA Pro Tracker
nutzen, um Deck-Kartenlisten zu erhalten.

Referenz: references/deck-card-lists-via-upsert-event.md
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any, Iterable

from .decks import _extract_json_block, _iso_now


# Der Marker, der in der Log-Zeile vor dem JSON-Block steht.
_UPSERT_MARKER = "DeckUpsertDeckV3"


@dataclass(frozen=True)
class DeckCardExportPaths:
    """Pfade der Deck-Kartenlisten-Export-Artefakte."""

    deck_cards: Path
    run_report: Path


def export_deck_cards(paths: Iterable[Path], output_dir: Path) -> DeckCardExportPaths:
    """Extrahiere Deck-Kartenlisten aus DeckUpsertDeckV3-Events.

    Liest die Logs zeilenweise (nur Zeilen mit ``DeckUpsertDeckV3``),
    parst das äußere JSON, dann das ``request``-Feld als inneres JSON und
    extrahiert ``Summary.DeckId``, ``Summary.Name``, ``Summary.Format`` und
    ``Deck.MainDeck``/``Sideboard``/``CommandZone``/``Companions``.

    Letztes Vorkommen pro DeckId gewinnt (wie bei Decks/Wildcards).

    Args:
        paths: Zu parsende Log-Dateien.
        output_dir: Ausgabeverzeichnis.

    Returns:
        DeckCardExportPaths mit Pfaden zu deck-cards.json und run-report.
    """
    sorted_paths = sorted({Path(path) for path in paths})
    output_dir.mkdir(parents=True, exist_ok=True)

    decks, warnings = _extract_deck_cards_from_lines(sorted_paths)

    payload = _build_deck_cards_payload(decks, sorted_paths, warnings)
    deck_cards_path = output_dir / "deck-cards.json"
    _write_json(deck_cards_path, payload)

    run_report_path = output_dir / "run-report-deck-cards.json"
    _write_json(run_report_path, _build_run_report(sorted_paths, deck_cards_path, warnings))

    return DeckCardExportPaths(deck_cards=deck_cards_path, run_report=run_report_path)


# ---------------------------------------------------------------------------
# Extraktion
# ---------------------------------------------------------------------------

def _extract_deck_cards_from_lines(
    paths: list[Path],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Zeilenweiser Pfad: sammle Deck-Kartenlisten aus DeckUpsertDeckV3-Events.

    Returns:
        Tuple von (decks, warnings). ``decks`` ist eine Liste von Dicts
        mit deckId, name, format, mainDeck, sideboard, commandZone, companions.
    """
    seen: dict[str, dict[str, Any]] = {}  # deckId -> deck (letzter gewinnt)
    order: list[str] = []
    warnings: list[str] = []

    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    # Quick filter: nur Zeilen mit DeckUpsertDeckV3 verarbeiten
                    if _UPSERT_MARKER not in raw_line:
                        continue

                    deck = _parse_upsert_line(raw_line)
                    if deck is None:
                        continue

                    deck_id = deck["deckId"]
                    if deck_id not in seen:
                        order.append(deck_id)
                    seen[deck_id] = deck
        except OSError as exc:
            warnings.append(f"{path.name}: konnte nicht gelesen werden ({exc})")

    return [seen[key] for key in order], warnings


def _parse_upsert_line(line: str) -> dict[str, Any] | None:
    """Parse eine einzelne DeckUpsertDeckV3-Zeile.

    Die Zeile hat das Format:
    ``[UnityCrossThreadLogger]==> DeckUpsertDeckV3 {"id":"...","request":"<escaped-json>"}``

    1. Finde das äußere JSON (ab erstem ``{``).
    2. ``json.loads`` → äußeres Objekt mit ``id`` und ``request`` (String).
    3. ``json.loads(request_string)`` → inneres Objekt mit ``Summary`` und ``Deck``.
    4. Extrahiere deckId, name, format, MainDeck, Sideboard, CommandZone, Companions.
    """
    # Finde den Marker und schneide ab dem ersten '{' danach
    marker_idx = line.find(_UPSERT_MARKER)
    if marker_idx == -1:
        return None

    json_start = line.find("{", marker_idx)
    if json_start == -1:
        return None

    # Verwende den bestehenden JSON-Block-Extraktor für das äußere JSON
    outer_json = _extract_json_block_from(line, json_start)
    if outer_json is None:
        return None

    # request ist ein String, der selbst JSON enthält
    request_str = outer_json.get("request")
    if not isinstance(request_str, str):
        return None

    try:
        inner = json.loads(request_str)
    except json.JSONDecodeError:
        return None

    if not isinstance(inner, dict):
        return None

    summary = inner.get("Summary", {})
    deck_data = inner.get("Deck", {})

    if not isinstance(summary, dict) or not isinstance(deck_data, dict):
        return None

    deck_id = summary.get("DeckId")
    if not deck_id:
        return None
    deck_id = str(deck_id)

    name = summary.get("Name", deck_id)
    if not isinstance(name, str):
        name = str(name) if name is not None else deck_id

    format_name = _extract_format_from_summary(summary)

    main_deck = _normalize_card_list(deck_data.get("MainDeck", []))
    sideboard = _normalize_card_list(deck_data.get("Sideboard", []))
    command_zone = _normalize_card_list(deck_data.get("CommandZone", []))
    companions = _normalize_card_list(deck_data.get("Companions", []))

    return {
        "deckId": deck_id,
        "name": name,
        "format": format_name,
        "mainDeck": main_deck,
        "sideboard": sideboard,
        "commandZone": command_zone,
        "companions": companions,
    }


def _extract_json_block_from(line: str, start: int) -> dict[str, Any] | None:
    """Extrahiere den JSON-Block ab Position *start* aus der Zeile.

    Verwendet den bestehenden _extract_json_block, aber positioniert ab *start*.
    """
    # _extract_json_block sucht selbst ab line.find("{"), aber wir wollen
    # ab einer bestimmten Position starten. Da _extract_json_block intern
    # line.find("{") verwendet (was die erste Klammer findet), und der
    # Marker-Präfix keine geschweiften Klammern enthält, ist das sicher.
    return _extract_json_block(line)


def _normalize_card_list(cards: Any) -> list[dict[str, Any]]:
    """Normalisiere eine Kartenliste aus dem DeckUpsertDeckV3-Event.

    Die Liste besteht aus ``{"cardId": 91643, "quantity": 1}``-Objekten.
    """
    if not isinstance(cards, list):
        return []

    result: list[dict[str, Any]] = []
    for entry in cards:
        if not isinstance(entry, dict):
            continue
        card_id = entry.get("cardId")
        if card_id is None:
            continue
        try:
            card_id = int(card_id)
        except (TypeError, ValueError):
            continue
        if card_id <= 0:
            continue
        quantity = entry.get("quantity", 1)
        try:
            quantity = int(quantity)
        except (TypeError, ValueError):
            quantity = 1
        if quantity <= 0:
            continue
        result.append({"cardId": card_id, "quantity": quantity})
    return result


def _extract_format_from_summary(summary: dict[str, Any]) -> str:
    """Extrahiere das Format aus dem Summary-Objekt.

    Das Format steht in ``Summary.Attributes`` als Liste von
    ``{"name": "Format", "value": "Historic"}``-Objekten.
    """
    attributes = summary.get("Attributes", [])
    if isinstance(attributes, list):
        for attr in attributes:
            if isinstance(attr, dict):
                name = attr.get("name", "")
                value = attr.get("value", "")
                if name == "Format" and isinstance(value, str) and value:
                    return value
    return "unknown"


# ---------------------------------------------------------------------------
# Payload-Builder
# ---------------------------------------------------------------------------

def _build_deck_cards_payload(
    decks: list[dict[str, Any]],
    log_paths: list[Path],
    warnings: list[str],
) -> dict:
    """Baue das deck-cards.json Payload (Schema deck-cards.v1)."""
    return {
        "schema": "deck-cards.v1",
        "source": "local-logs",
        "decks": decks,
        "diagnostics": {
            "deckCount": len(decks),
            "logs": [p.as_posix() for p in log_paths],
            "warnings": warnings,
        },
    }


def _build_run_report(
    log_paths: list[Path],
    deck_cards_path: Path,
    warnings: list[str],
) -> dict:
    """Baue den run-report für Deck-Kartenlisten-Export."""
    ts = _iso_now()
    return {
        "schema": "run-report-deck-cards.v1",
        "runId": f"run-deck-cards-{ts}",
        "startedAt": ts,
        "finishedAt": ts,
        "source": "local-logs",
        "logs": [p.as_posix() for p in log_paths],
        "outputs": {
            "deckCards": deck_cards_path.as_posix(),
        },
        "diagnostics": {
            "warnings": warnings,
            "evidence": ["deck-upsert-v3-parser"],
        },
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )