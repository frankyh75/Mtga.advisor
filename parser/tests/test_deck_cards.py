"""Tests für parser/deck_cards.py — DeckUpsertDeckV3-Extraktion."""

from __future__ import annotations

import json
from pathlib import Path

import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from parser.deck_cards import export_deck_cards, _parse_upsert_line  # noqa: E402


def _write_log(tmp_path: Path, content: str) -> Path:
    """Schreibe Log-Content in eine temporäre Datei."""
    path = tmp_path / "Player.log"
    path.write_text(content, encoding="utf-8")
    return path


def _make_upsert_line(
    deck_id: str = "ecc24960-test-0001",
    name: str = "TestDeck",
    fmt: str = "Historic",
    main_deck: list[dict] | None = None,
    sideboard: list[dict] | None = None,
) -> str:
    """Erstelle eine syntaktisch korrekte DeckUpsertDeckV3-Logzeile."""
    if main_deck is None:
        main_deck = [{"cardId": 91643, "quantity": 4}, {"cardId": 72086, "quantity": 2}]
    if sideboard is None:
        sideboard = []

    inner = {
        "Summary": {
            "DeckId": deck_id,
            "Name": name,
            "Attributes": [
                {"name": "Format", "value": fmt},
                {"name": "Version", "value": "1"},
            ],
        },
        "Deck": {
            "MainDeck": main_deck,
            "Sideboard": sideboard,
            "CommandZone": [],
            "Companions": [],
        },
        "ActionType": "Updated",
    }
    inner_json = json.dumps(inner)
    outer = {"id": "req-1", "request": inner_json}
    outer_json = json.dumps(outer)
    return f"[UnityCrossThreadLogger]==> DeckUpsertDeckV3 {outer_json}"


# ---------------------------------------------------------------------------
# Unit-Tests: _parse_upsert_line
# ---------------------------------------------------------------------------

def test_parse_upsert_line_basic() -> None:
    """Eine gültige DeckUpsertDeckV3-Zeile wird korrekt geparst."""
    line = _make_upsert_line(
        deck_id="abc-123",
        name="Victimize",
        fmt="Historic",
        main_deck=[{"cardId": 91643, "quantity": 4}, {"cardId": 72086, "quantity": 2}],
    )
    result = _parse_upsert_line(line)
    assert result is not None
    assert result["deckId"] == "abc-123"
    assert result["name"] == "Victimize"
    assert result["format"] == "Historic"
    assert result["mainDeck"] == [{"cardId": 91643, "quantity": 4}, {"cardId": 72086, "quantity": 2}]
    assert result["sideboard"] == []
    assert result["commandZone"] == []
    assert result["companions"] == []


def test_parse_upsert_line_no_marker() -> None:
    """Eine Zeile ohne DeckUpsertDeckV3-Marker wird ignoriert."""
    line = "[UnityCrossThreadLogger]==> SomeOtherEvent {\"id\":\"x\"}"
    result = _parse_upsert_line(line)
    assert result is None


def test_parse_upsert_line_no_json() -> None:
    """Eine Zeile ohne JSON-Block wird ignoriert."""
    line = "[UnityCrossThreadLogger]==> DeckUpsertDeckV3 no json here"
    result = _parse_upsert_line(line)
    assert result is None


def test_parse_upsert_line_missing_deck_id() -> None:
    """Wenn Summary.DeckId fehlt, wird None zurückgegeben."""
    inner = {"Summary": {"Name": "NoId"}, "Deck": {"MainDeck": []}}
    outer = {"id": "req-1", "request": json.dumps(inner)}
    line = f"[UnityCrossThreadLogger]==> DeckUpsertDeckV3 {json.dumps(outer)}"
    result = _parse_upsert_line(line)
    assert result is None


def test_parse_upsert_line_format_from_attributes() -> None:
    """Das Format wird aus Summary.Attributes extrahiert."""
    line = _make_upsert_line(fmt="Standard")
    result = _parse_upsert_line(line)
    assert result is not None
    assert result["format"] == "Standard"


def test_parse_upsert_line_no_format_attribute() -> None:
    """Ohne Format-Attribut wird 'unknown' zurückgegeben."""
    inner = {
        "Summary": {"DeckId": "x", "Name": "Test", "Attributes": []},
        "Deck": {"MainDeck": [], "Sideboard": [], "CommandZone": [], "Companions": []},
    }
    outer = {"id": "r", "request": json.dumps(inner)}
    line = f"[UnityCrossThreadLogger]==> DeckUpsertDeckV3 {json.dumps(outer)}"
    result = _parse_upsert_line(line)
    assert result is not None
    assert result["format"] == "unknown"


def test_parse_upsert_line_with_sideboard() -> None:
    """Sideboard-Karten werden korrekt extrahiert."""
    line = _make_upsert_line(
        sideboard=[{"cardId": 12345, "quantity": 2}],
    )
    result = _parse_upsert_line(line)
    assert result is not None
    assert result["sideboard"] == [{"cardId": 12345, "quantity": 2}]


def test_parse_upsert_line_quantity_defaults_to_1() -> None:
    """Wenn quantity fehlt, wird es auf 1 gesetzt."""
    inner = {
        "Summary": {"DeckId": "x", "Name": "T", "Attributes": []},
        "Deck": {"MainDeck": [{"cardId": 42}], "Sideboard": [], "CommandZone": [], "Companions": []},
    }
    outer = {"id": "r", "request": json.dumps(inner)}
    line = f"[UnityCrossThreadLogger]==> DeckUpsertDeckV3 {json.dumps(outer)}"
    result = _parse_upsert_line(line)
    assert result is not None
    assert result["mainDeck"] == [{"cardId": 42, "quantity": 1}]


def test_parse_upsert_line_invalid_card_id_skipped() -> None:
    """Ungültige cardId-Werte werden übersprungen."""
    inner = {
        "Summary": {"DeckId": "x", "Name": "T", "Attributes": []},
        "Deck": {
            "MainDeck": [{"cardId": -1, "quantity": 1}, {"cardId": "abc", "quantity": 1}, {"cardId": 100, "quantity": 1}],
            "Sideboard": [],
            "CommandZone": [],
            "Companions": [],
        },
    }
    outer = {"id": "r", "request": json.dumps(inner)}
    line = f"[UnityCrossThreadLogger]==> DeckUpsertDeckV3 {json.dumps(outer)}"
    result = _parse_upsert_line(line)
    assert result is not None
    # Nur die gültige cardId=100 bleibt
    assert result["mainDeck"] == [{"cardId": 100, "quantity": 1}]


# ---------------------------------------------------------------------------
# Integration-Tests: export_deck_cards
# ---------------------------------------------------------------------------

def test_export_deck_cards_single_deck(tmp_path: Path) -> None:
    """export_deck_cards schreibt deck-cards.json mit einem Deck."""
    log = _make_upsert_line(
        deck_id="deck-1",
        name="Test Deck",
        fmt="Standard",
        main_deck=[{"cardId": 1, "quantity": 4}],
    )
    log_path = tmp_path / "Player.log"
    log_path.write_text(log + "\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    paths = export_deck_cards([log_path], out_dir)

    assert paths.deck_cards == out_dir / "deck-cards.json"
    assert paths.deck_cards.exists()

    data = json.loads(paths.deck_cards.read_text(encoding="utf-8"))
    assert data["schema"] == "deck-cards.v1"
    assert data["source"] == "local-logs"
    assert len(data["decks"]) == 1
    deck = data["decks"][0]
    assert deck["deckId"] == "deck-1"
    assert deck["name"] == "Test Deck"
    assert deck["format"] == "Standard"
    assert deck["mainDeck"] == [{"cardId": 1, "quantity": 4}]
    assert deck["sideboard"] == []
    assert data["diagnostics"]["deckCount"] == 1
    assert data["diagnostics"]["warnings"] == []


def test_export_deck_cards_multiple_decks(tmp_path: Path) -> None:
    """Mehrere Decks aus mehreren Zeilen werden extrahiert."""
    log1 = _make_upsert_line(deck_id="deck-a", name="Deck A", main_deck=[{"cardId": 1, "quantity": 4}])
    log2 = _make_upsert_line(deck_id="deck-b", name="Deck B", main_deck=[{"cardId": 2, "quantity": 2}])
    log_path = tmp_path / "Player.log"
    log_path.write_text(log1 + "\n" + log2 + "\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    export_deck_cards([log_path], out_dir)

    data = json.loads((out_dir / "deck-cards.json").read_text(encoding="utf-8"))
    assert data["diagnostics"]["deckCount"] == 2
    deck_ids = [d["deckId"] for d in data["decks"]]
    assert "deck-a" in deck_ids
    assert "deck-b" in deck_ids


def test_export_deck_cards_last_wins(tmp_path: Path) -> None:
    """Bei mehreren Updates desselben Decks gewinnt das letzte."""
    log1 = _make_upsert_line(deck_id="deck-1", name="V1", main_deck=[{"cardId": 1, "quantity": 2}])
    log2 = _make_upsert_line(deck_id="deck-1", name="V2", main_deck=[{"cardId": 1, "quantity": 4}])
    log_path = tmp_path / "Player.log"
    log_path.write_text(log1 + "\n" + log2 + "\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    export_deck_cards([log_path], out_dir)

    data = json.loads((out_dir / "deck-cards.json").read_text(encoding="utf-8"))
    assert len(data["decks"]) == 1
    deck = data["decks"][0]
    assert deck["name"] == "V2"
    assert deck["mainDeck"] == [{"cardId": 1, "quantity": 4}]


def test_export_deck_cards_no_events(tmp_path: Path) -> None:
    """Ohne DeckUpsertDeckV3-Events ist deckCount 0."""
    log = "Some random log line\n[UnityCrossThreadLogger]==> OtherEvent {\"id\":\"x\"}\n"
    log_path = tmp_path / "Player.log"
    log_path.write_text(log, encoding="utf-8")

    out_dir = tmp_path / "out"
    export_deck_cards([log_path], out_dir)

    data = json.loads((out_dir / "deck-cards.json").read_text(encoding="utf-8"))
    assert data["diagnostics"]["deckCount"] == 0
    assert data["decks"] == []


def test_export_deck_cards_ignores_non_upsert_lines(tmp_path: Path) -> None:
    """Nur DeckUpsertDeckV3-Zeilen werden verarbeitet, andere ignoriert."""
    upsert = _make_upsert_line(deck_id="d1", name="D1", main_deck=[{"cardId": 5, "quantity": 3}])
    other = '[UnityCrossThreadLogger]==> OtherEvent {"id":"x","request":"{}"}'
    log_path = tmp_path / "Player.log"
    log_path.write_text(upsert + "\n" + other + "\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    export_deck_cards([log_path], out_dir)

    data = json.loads((out_dir / "deck-cards.json").read_text(encoding="utf-8"))
    assert data["diagnostics"]["deckCount"] == 1
    assert data["decks"][0]["deckId"] == "d1"


def test_export_deck_cards_creates_run_report(tmp_path: Path) -> None:
    """Ein run-report-deck-cards.json wird geschrieben."""
    log = _make_upsert_line()
    log_path = tmp_path / "Player.log"
    log_path.write_text(log + "\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    paths = export_deck_cards([log_path], out_dir)

    assert paths.run_report.exists()
    report = json.loads(paths.run_report.read_text(encoding="utf-8"))
    assert report["schema"] == "run-report-deck-cards.v1"
    assert "deckCards" in report["outputs"]


def test_export_deck_cards_empty_log(tmp_path: Path) -> None:
    """Eine leere Log-Datei führt zu 0 Decks ohne Fehler."""
    log_path = tmp_path / "empty.log"
    log_path.write_text("", encoding="utf-8")

    out_dir = tmp_path / "out"
    export_deck_cards([log_path], out_dir)

    data = json.loads((out_dir / "deck-cards.json").read_text(encoding="utf-8"))
    assert data["diagnostics"]["deckCount"] == 0