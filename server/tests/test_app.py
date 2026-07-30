from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[2]))

from server.app import _render_index, _render_missing_table  # noqa: E402


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

    html = _render_index(collection, run_report, deck, advisor_result)

    assert "MTGA Advisor" in html
    assert "Unique IDs" in html
    assert "Test Deck" in html
    assert "Completion" in html
    assert "Lightning Strike" in html
    assert "wildcards-unknown" in html


def test_render_missing_table_handles_absent_result() -> None:
    assert "Noch kein Advisor-Result" in _render_missing_table(None)
