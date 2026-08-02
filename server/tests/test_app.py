from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))

from advisor.llm_config import LLMConfig  # noqa: E402
from server.app import _dashboard_js, _render_index  # noqa: E402


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
                "deckKey": "1",
                "format": "standard",
                "colors": ["U"],
                "cardCount": 60,
                "isPrecon": False,
            }
        ],
    }

    llm_config = LLMConfig()
    html = _render_index(collection, run_report, deck, advisor_result, decks, llm_config)

    assert "MTGA Advisor" in html
    assert "Unique IDs" in html
    assert "Control" in html
    assert "wildcards-unknown" in html
    assert "decks.json" in html
    assert "Precons ausblenden" in html
    assert "data-deck-key='1'" in html
    assert '<script src="/dashboard.js" defer></script>' in html
    assert "const decksData" not in html
    assert "document.getElementById('config-toggle')" not in html


def test_render_index_handles_missing_decks_and_advisor() -> None:
    html = _render_index(None, None, None, None, None, LLMConfig())

    assert "Decks" in html
    assert "decks.json nicht gefunden" in html
    assert "advisor-result.json nicht gefunden" in html


def test_dashboard_script_is_loaded_from_asset() -> None:
    js = _dashboard_js()

    assert "document.addEventListener(\"DOMContentLoaded\"" in js
    assert "fetch(\"/api/chat\"" in js
    assert "innerHTML" not in js
