from __future__ import annotations

from pathlib import Path

import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from analysis.deck_analysis import build_deck_analysis  # noqa: E402
from carddb import CardLookup, import_bulk_json, open_db  # noqa: E402


def test_build_deck_analysis_maps_cards(tmp_path: Path) -> None:
    db_path = tmp_path / "carddb.sqlite"
    conn = open_db(db_path)
    import_bulk_json(
        conn,
        Path("fixtures/scryfall-oracle-mini.json"),
        source="oracle",
    )
    deck = {
        "id": "deck-1",
        "name": "Test Deck",
        "format": "Explorer",
        "source": "saved",
        "mainDeck": [{"cardId": 100001, "quantity": 2}],
        "sideboard": [{"cardId": 100002, "quantity": 1}],
        "commandZone": [],
        "companions": [],
    }

    result = build_deck_analysis(deck, lookup=CardLookup(conn))

    assert result.payload["deckId"] == "deck-1"
    assert result.payload["mappingCoverage"] == 1.0
    assert result.payload["unknownCards"] == []
    assert result.payload["cards"][0]["name"] == "Test Card One"
