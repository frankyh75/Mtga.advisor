from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))

from server.app import _render_index  # noqa: E402


def test_render_index_includes_collection_deck_and_advisor_summary() -> None:
    collection = {
        "source": "memory-scan",
        "cards": {"100": 2, "200": 1},
        "diagnostics": {"completeness": {"cards": "complete"}, "warnings": []},
    }
    run_report = {
        "diagnostics": {"completeness": {"cards": "complete"}, "warnings": []},
    }
    deck = {
        "name": "Test Deck",
        "format": "standard",
        "mainboard": [{"count": 4}],
        "sideboard": [{"count": 1}],
        "diagnostics": {"warnings": []},
    }
    advisor_result = {
        "summary": {
            "completionScore": 60.0,
            "missingCards": 2,
            "hardCraftAdviceAllowed": False,
        },
        "recommendations": [
            {
                "name": "Lightning Strike",
                "needed": 2,
                "owned": 2,
                "rarity": "common",
                "reasons": ["missing-copies", "mainboard"],
            }
        ],
        "warnings": ["wildcards-unknown"],
    }

    decks = {
        "schema": "decks.v1",
        "decks": [
            {
                "name": "Control",
                "deckId": "1",
                "format": "standard",
                "colors": ["U"],
                "cardCount": 60,
            }
        ],
    }

    html = _render_index(collection, run_report, deck, advisor_result, decks)

    assert "MTGA Advisor" in html
    assert "Unique IDs" in html
    assert "Control" in html
    assert "wildcards-unknown" in html
    assert "decks.json" in html


def test_render_index_handles_missing_decks_and_advisor() -> None:
    html = _render_index(None, None, None, None, None)

    assert "Decks" in html
    assert "decks.json nicht gefunden" in html
    assert "advisor-result.json nicht gefunden" in html
