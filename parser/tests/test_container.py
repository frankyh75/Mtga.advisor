"""Tests für Container-Export-Logik."""

from __future__ import annotations

from pathlib import Path

import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from parser.decks import (  # noqa: E402
    export_container,
    show_deck,
    list_decks,
    ContainerPaths,
)
from parser.start_hook import DeckSummary  # noqa: E402


def test_export_container_creates_index(tmp_path: Path) -> None:
    """Container-Export erstellt index.json."""
    decks = [
        DeckSummary(
            name="Test Deck 1",
            deck_id="abc123",
            deck_tile_id=456,
            description="Test Description",
            attributes={},
            format_legalities={"standard": True},
            is_companion_valid=True,
            mana="R",
        ),
        DeckSummary(
            name="Test Deck 2",
            deck_id="def456",
            deck_tile_id=789,
            description=None,
            attributes={"key": "value"},
            format_legalities={},
            is_companion_valid=False,
            mana="WU",
        ),
    ]

    deck_dir = tmp_path / "decks"
    paths = export_container(deck_dir, decks)

    assert paths.deck_dir == deck_dir
    assert paths.index == deck_dir / "index.json"
    assert paths.index.exists()

    import json
    index_data = json.loads(paths.index.read_text(encoding="utf-8"))
    assert index_data["schema"] == "decks-container.v1"
    assert index_data["deckCount"] == 2
    assert len(index_data["decks"]) == 2


def test_export_container_creates_individual_files(tmp_path: Path) -> None:
    """Container-Export erstellt separate Dateien pro Deck."""
    decks = [
        DeckSummary(
            name="Test Deck",
            deck_id="abc123",
            deck_tile_id=456,
            description=None,
            attributes={},
            format_legalities={},
            is_companion_valid=None,
            mana="R",
        ),
    ]

    deck_dir = tmp_path / "decks"
    paths = export_container(deck_dir, decks)

    # Überprüfe ob die Summary-Datei existiert (basierend auf deck_id)
    summary_path = deck_dir / "abc123.json"
    assert summary_path.exists()

    import json
    summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary_data["name"] == "Test Deck"
    assert summary_data["deckId"] == "abc123"
    assert summary_data["schema"] == "deck-summary.v1"


def test_export_container_refresh_overwrites(tmp_path: Path) -> None:
    """Container-Export mit refresh=True überschreibt existierende Dateien."""
    decks1 = [
        DeckSummary(
            name="Original",
            deck_id="abc123",
            deck_tile_id=456,
            description=None,
            attributes={},
            format_legalities={},
            is_companion_valid=None,
            mana="R",
        ),
    ]

    deck_dir = tmp_path / "decks"
    paths1 = export_container(deck_dir, decks1)

    # Ändere die Summary-Datei (basierend auf deck_id)
    summary_path = deck_dir / "abc123.json"
    summary_data = {"name": "Modified", "deckId": "abc123"}
    summary_path.write_text(
        __import__("json").dumps(summary_data, indent=2),
        encoding="utf-8",
    )

    # Export mit refresh=True
    decks2 = [
        DeckSummary(
            name="Updated",
            deck_id="abc123",
            deck_tile_id=789,
            description="New Description",
            attributes={},
            format_legalities={},
            is_companion_valid=False,
            mana="WU",
        ),
    ]
    paths2 = export_container(deck_dir, decks2, refresh=True)

    # Überprüfe ob die Datei aktualisiert wurde
    summary_data = __import__("json").loads(summary_path.read_text(encoding="utf-8"))
    assert summary_data["name"] == "Updated"
    assert summary_data["deckTileId"] == 789


def test_list_decks_returns_index(tmp_path: Path) -> None:
    """list_decks gibt den Container-Index zurück."""
    # Erstelle zuerst einen Container
    decks = [
        DeckSummary(
            name="Test Deck",
            deck_id="abc123",
            deck_tile_id=456,
            description=None,
            attributes={},
            format_legalities={},
            is_companion_valid=None,
            mana="R",
        ),
    ]

    deck_dir = tmp_path / "decks"
    export_container(deck_dir, decks)

    # Teste list_decks
    index_data = list_decks(deck_dir)
    assert "schema" in index_data
    assert index_data["deckCount"] == 1
    assert len(index_data["decks"]) == 1


def test_list_decks_returns_error_for_missing_container(tmp_path: Path) -> None:
    """list_decks gibt Fehler zurück wenn kein Container existiert."""
    index_data = list_decks(tmp_path / "nonexistent")
    assert index_data["schema"] == "error"
    assert "Kein Container gefunden" in index_data["message"]


def test_show_deck_returns_deck_details(tmp_path: Path) -> None:
    """show_deck gibt Deck-Details zurück."""
    # Erstelle zuerst einen Container
    decks = [
        DeckSummary(
            name="Test Deck",
            deck_id="abc123",
            deck_tile_id=456,
            description="Test Description",
            attributes={"key": "value"},
            format_legalities={"standard": True},
            is_companion_valid=True,
            mana="R",
        ),
    ]

    deck_dir = tmp_path / "decks"
    export_container(deck_dir, decks)

    # Teste show_deck
    deck_data = show_deck("abc123", deck_dir)
    assert deck_data is not None
    assert deck_data["name"] == "Test Deck"
    assert deck_data["deckId"] == "abc123"
    assert deck_data["description"] == "Test Description"
    assert deck_data["isCompanionValid"] is True


def test_show_deck_returns_none_for_missing_deck(tmp_path: Path) -> None:
    """show_deck gibt None zurück wenn Deck nicht gefunden wird."""
    deck_dir = tmp_path / "decks"
    deck_dir.mkdir(parents=True)

    deck_data = show_deck("nonexistent", deck_dir)
    assert deck_data is None


def test_export_container_handles_deck_without_id(tmp_path: Path) -> None:
    """Container-Export funktioniert auch mit Decks ohne DeckId."""
    decks = [
        DeckSummary(
            name="Deck without ID",
            deck_id=None,
            deck_tile_id=None,
            description=None,
            attributes={},
            format_legalities={},
            is_companion_valid=None,
            mana=None,
        ),
    ]

    deck_dir = tmp_path / "decks"
    paths = export_container(deck_dir, decks)

    assert paths.index.exists()

    import json
    index_data = json.loads(paths.index.read_text(encoding="utf-8"))
    assert index_data["deckCount"] == 1
    # Deck ohne ID nutzt den Originalnamen als deckId (nicht slugifiziert)
    assert index_data["decks"][0]["deckId"] == "Deck without ID"
    # Aber der Dateiname ist slugifiziert
    summary_path = deck_dir / "deck-without-id.json"
    assert summary_path.exists()
