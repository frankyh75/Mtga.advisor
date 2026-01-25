from __future__ import annotations

from pathlib import Path

import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from parser.pipeline import parse_collection  # noqa: E402


def _write_log(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "Player.log"
    path.write_text(content)
    return path


def test_snapshot_then_delta_applies_updates(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] PlayerInventory.GetPlayerCardsV3
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"playerId":"anon","cards":[{"id":100001,"quantity":2}],"wildcards":{"rare":1},"asOf":"2025-01-01T12:00:00Z"}
[2025-01-01T12:01:12Z] [UnityCrossThreadLogger] Inventory.Updated
[2025-01-01T12:01:12Z] [UnityCrossThreadLogger] {"delta":{"cards":[{"id":100001,"quantity":+1},{"id":100002,"quantity":+2}],"wildcards":{"rare":-1}}}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.snapshot_seen is True
    assert report.cards == {100001: 3, 100002: 2}
    assert report.wildcards == {"rare": 0}
    assert report.wildcards_baseline_present is True
    assert report.pending_wildcard_deltas is None
    assert report.completeness["cards"] == "complete"


def test_inventory_info_snapshot_marks_snapshot_seen(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] InventoryInfo
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"InventoryInfo":{"WildCardCommons":3,"WildCardUnCommons":2,"WildCardRares":1,"WildCardMythics":0,"Changes":[]}}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.snapshot_seen is True
    assert report.cards is None
    assert report.wildcards == {
        "common": 3,
        "uncommon": 2,
        "rare": 1,
        "mythic": 0,
    }
    assert report.completeness["cards"] == "unknown"
    assert report.completeness["wildcards"] == "complete"
    assert report.completeness["source"] == "unknown"
    assert "InventoryInfo" in report.evidence


def test_inventory_info_does_not_apply_card_deltas(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] InventoryInfo
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"InventoryInfo":{"WildCardCommons":3,"WildCardUnCommons":2,"WildCardRares":1,"WildCardMythics":0,"Changes":[]}}
[2025-01-01T12:01:12Z] [UnityCrossThreadLogger] Inventory.Updated
[2025-01-01T12:01:12Z] [UnityCrossThreadLogger] {"delta":{"cards":[{"id":100001,"quantity":+1}],"wildcards":{"rare":-1}}}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.cards is None
    assert report.wildcards == {
        "common": 3,
        "uncommon": 2,
        "rare": 0,
        "mythic": 0,
    }
    assert report.pending_wildcard_deltas is None


def test_delta_before_snapshot_is_ignored(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] Inventory.Updated
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"delta":{"cards":[{"id":100001,"quantity":+1}],"wildcards":{"rare":-1}}}
[2025-01-01T12:05:00Z] [UnityCrossThreadLogger] PlayerInventory.GetPlayerCardsV3
[2025-01-01T12:05:00Z] [UnityCrossThreadLogger] {"playerId":"anon","cards":[{"id":100001,"quantity":2}],"wildcards":{"rare":1},"asOf":"2025-01-01T12:05:00Z"}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.cards == {100001: 2}
    assert report.wildcards == {"rare": 0}
    assert report.wildcards_baseline_present is True
    assert "delta-before-snapshot" in report.warnings


def test_unknown_not_zero_without_snapshot(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] Inventory.Updated
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"delta":{"cards":[{"id":100001,"quantity":+1}]}}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.snapshot_seen is False
    assert report.cards is None
    assert report.wildcards is None
    assert report.completeness["cards"] == "unknown"
    assert report.collection_completeness == "unknown"
    assert "missing-snapshot" in report.warnings
    assert "detailed-logs-missing" in report.warnings


def test_snapshot_without_wildcards_keeps_unknown_after_delta(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] PlayerInventory.GetPlayerCardsV3
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"playerId":"anon","cards":[{"id":100001,"quantity":2}],"asOf":"2025-01-01T12:00:00Z"}
[2025-01-01T12:01:12Z] [UnityCrossThreadLogger] Inventory.Updated
[2025-01-01T12:01:12Z] [UnityCrossThreadLogger] {"delta":{"cards":[{"id":100001,"quantity":+1}],"wildcards":{"rare":-1}}}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.cards == {100001: 3}
    assert report.wildcards is None
    assert report.wildcards_baseline_present is False
    assert report.pending_wildcard_deltas == {"rare": -1}


def test_no_logs_returns_unknown_completeness() -> None:
    report = parse_collection([])

    assert report.snapshot_seen is False
    assert report.cards is None
    assert report.wildcards is None
    assert report.completeness["cards"] == "unknown"
    assert report.collection_completeness == "unknown"
    assert "missing-snapshot" in report.warnings
    assert "detailed-logs-missing" in report.warnings


def test_malformed_json_is_ignored(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] PlayerInventory.GetPlayerCardsV3
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"playerId":"anon","cards":[{"id":100001,"quantity":2}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.snapshot_seen is False
    assert report.cards is None
    assert "missing-snapshot" in report.warnings


def test_detailed_logs_enabled_suppresses_warning(tmp_path: Path) -> None:
    log = """DETAILED LOGS: ENABLED
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] PlayerInventory.GetPlayerCardsV3
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"playerId":"anon","cards":[{"id":100001,"quantity":2}],"wildcards":{"rare":1},"asOf":"2025-01-01T12:00:00Z"}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert "detailed-logs-missing" not in report.warnings


def test_decklist_fallback_marks_partial(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] DeckGetDeckLists
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"Decks":{"deck-1":{"MainDeck":[{"cardId":100001,"quantity":2},{"cardId":100002,"quantity":1}],"Sideboard":[{"cardId":100003,"quantity":1}]}}}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.snapshot_seen is False
    assert report.cards is None
    assert report.completeness["cards"] == "partial"
    assert report.completeness["source"] == "partial"
    assert report.collection_completeness == "partial"
    assert report.cards_seen_in_decks == {100001, 100002, 100003}
    assert report.decks == [
        {
            "id": "deck-1",
            "mainDeck": [
                {"cardId": 100001, "quantity": 2},
                {"cardId": 100002, "quantity": 1},
            ],
            "sideboard": [{"cardId": 100003, "quantity": 1}],
            "commandZone": [],
            "companions": [],
            "source": "saved",
        }
    ]
    assert "decklist-fallback" in report.warnings


def test_course_deck_extraction_marks_last_played(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] EventGetCoursesV2
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"Courses":[{"InternalEventName":"Historic_Ladder","CourseDeckSummary":{"DeckId":"deck-1","Name":"Test Deck","Attributes":[{"name":"Format","value":"Explorer"},{"name":"LastPlayed","value":"\\"2026-01-02T00:00:00Z\\""}]},"CourseDeck":{"MainDeck":[{"cardId":100001,"quantity":2}],"Sideboard":[{"cardId":100002,"quantity":1}],"CommandZone":[{"cardId":100003,"quantity":1}],"Companions":[{"cardId":100004,"quantity":1}]}}]}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.decks == [
        {
            "id": "deck-1",
            "name": "Test Deck",
            "format": "Explorer",
            "lastPlayed": "2026-01-02T00:00:00Z",
            "mainDeck": [{"cardId": 100001, "quantity": 2}],
            "sideboard": [{"cardId": 100002, "quantity": 1}],
            "commandZone": [],
            "companions": [],
            "source": "last_played",
        }
    ]


def test_event_deck_is_marked_optional(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] EventGetCoursesV2
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"Courses":[{"InternalEventName":"DualColorPrecons","CourseDeckSummary":{"DeckId":"deck-1","Name":"?=?Loc/Decks/Precon/Test","Attributes":[{"name":"Format","value":"Standard"}]},"CourseDeck":{"MainDeck":[{"cardId":100001,"quantity":2}]}}]}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.decks == [
        {
            "id": "deck-1",
            "name": "?=?Loc/Decks/Precon/Test",
            "format": "Standard",
            "mainDeck": [{"cardId": 100001, "quantity": 2}],
            "sideboard": [],
            "commandZone": [],
            "companions": [],
            "source": "event",
        }
    ]


def test_saved_deck_overrides_last_played(tmp_path: Path) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] EventGetCoursesV2
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"Courses":[{"InternalEventName":"Historic_Ladder","CourseDeckSummary":{"DeckId":"deck-1","Name":"Last Played","Attributes":[{"name":"Format","value":"Explorer"}]},"CourseDeck":{"MainDeck":[{"cardId":100001,"quantity":2}]}}]}
[2025-01-01T12:05:00Z] [UnityCrossThreadLogger] DeckGetDeckLists
[2025-01-01T12:05:00Z] [UnityCrossThreadLogger] {"Decks":{"deck-1":{"MainDeck":[{"cardId":100002,"quantity":4}],"Sideboard":[]}}}
"""
    report = parse_collection([_write_log(tmp_path, log)])

    assert report.decks == [
        {
            "id": "deck-1",
            "name": "Last Played",
            "format": "Explorer",
            "mainDeck": [{"cardId": 100002, "quantity": 4}],
            "sideboard": [],
            "commandZone": [],
            "companions": [],
            "source": "saved",
        }
    ]
