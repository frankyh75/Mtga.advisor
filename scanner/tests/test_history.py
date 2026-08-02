"""
test_history.py — Tests for the Collection-History module.

Tests cover:
  1. Snapshot saving (collection + decks)
  2. Snapshot listing
  3. Index management
  4. Collection diff (added, removed, increased, decreased, wildcards)
  5. Decks diff (added, removed, modified decks)
  6. Edge cases (no snapshots, single snapshot, missing files)
  7. Formatting helpers
  8. Deck card extraction from various formats
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Ensure we can import from scanner/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scanner.history import (
    save_snapshot,
    list_snapshots,
    diff_snapshots,
    format_diff_summary,
    format_snapshot_list,
    _diff_collections,
    _diff_decks,
    _extract_deck_card_counts,
    _normalize_card_dict,
    _safe_int,
    _find_snapshot,
    _timestamp_to_filename,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_collection(cards: dict[str, int], wildcards: dict[str, int] | None = None) -> dict:
    return {
        "schema": "collection.v1",
        "source": "memory-scan",
        "cards": cards,
        "wildcards": wildcards or {},
    }


def _make_decks(decks: list[dict]) -> dict:
    return {
        "schema": "decks.v1",
        "decks": decks,
    }


def _write_collection(path: Path, cards: dict[str, int], wildcards: dict | None = None) -> None:
    path.write_text(
        json.dumps(_make_collection(cards, wildcards), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_decks(path: Path, decks: list[dict]) -> None:
    path.write_text(
        json.dumps(_make_decks(decks), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# 1. Snapshot saving
# ---------------------------------------------------------------------------

def test_save_snapshot_creates_history_dir():
    """save_snapshot should create history/ subdir with timestamped files."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_collection(out / "collection.json", {"100": 2, "200": 3})
        _write_decks(out / "decks.json", [])

        history_dir = save_snapshot(output_dir=out)

        assert history_dir.exists()
        assert history_dir.name == "history"
        files = list(history_dir.glob("*.json"))
        # At least collection + decks + index
        assert len(files) >= 3


def test_save_snapshot_copies_collection():
    """Snapshot should contain a copy of collection.json."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_collection(out / "collection.json", {"100": 2, "200": 3})

        save_snapshot(output_dir=out)

        history_dir = out / "history"
        coll_files = list(history_dir.glob("*_collection.json"))
        assert len(coll_files) == 1
        data = json.loads(coll_files[0].read_text(encoding="utf-8"))
        assert data["cards"] == {"100": 2, "200": 3}


def test_save_snapshot_copies_decks():
    """Snapshot should contain a copy of decks.json."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_decks(out / "decks.json", [
            {"deckId": "d1", "name": "Test Deck", "cards": {"mainboard": [{"cardId": 100, "name": "Bolt", "count": 4}]}}
        ])

        save_snapshot(output_dir=out)

        history_dir = out / "history"
        deck_files = list(history_dir.glob("*_decks.json"))
        assert len(deck_files) == 1
        data = json.loads(deck_files[0].read_text(encoding="utf-8"))
        assert len(data["decks"]) == 1


def test_save_snapshot_updates_index():
    """Index should be updated with snapshot metadata."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_collection(out / "collection.json", {"100": 2})

        save_snapshot(output_dir=out)

        index_path = out / "history" / "index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text(encoding="utf-8"))
        assert index["schema"] == "history-index.v1"
        assert index["snapshotCount"] == 1
        assert len(index["snapshots"]) == 1
        assert "timestamp" in index["snapshots"][0]
        assert "collection" in index["snapshots"][0]["files"]


def test_save_snapshot_multiple():
    """Multiple snapshots should all be saved with different timestamps."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_collection(out / "collection.json", {"100": 2})

        save_snapshot(output_dir=out)
        time.sleep(1.1)  # Ensure different timestamp
        save_snapshot(output_dir=out)
        time.sleep(1.1)
        save_snapshot(output_dir=out)

        snapshots = list_snapshots(output_dir=out)
        assert len(snapshots) == 3
        # Timestamps should be different
        timestamps = [s["timestamp"] for s in snapshots]
        assert len(set(timestamps)) == 3


def test_save_snapshot_without_collection():
    """Snapshot should work even without collection.json (e.g., only decks)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_decks(out / "decks.json", [])

        save_snapshot(output_dir=out)

        snapshots = list_snapshots(output_dir=out)
        assert len(snapshots) == 1
        assert "decks" in snapshots[0]["files"]
        assert "collection" not in snapshots[0]["files"]


def test_save_snapshot_empty_output_dir():
    """Snapshot should work with empty output dir (no artifacts)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        save_snapshot(output_dir=out)
        snapshots = list_snapshots(output_dir=out)
        assert len(snapshots) == 1
        assert snapshots[0]["files"] == {}


# ---------------------------------------------------------------------------
# 2. Snapshot listing
# ---------------------------------------------------------------------------

def test_list_snapshots_empty():
    """list_snapshots should return empty list when no history exists."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshots = list_snapshots(output_dir=Path(tmp))
        assert snapshots == []


def test_list_snapshots_returns_entries():
    """list_snapshots should return all snapshot entries."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_collection(out / "collection.json", {"100": 2, "200": 3})
        _write_decks(out / "decks.json", [{"deckId": "d1", "name": "Test"}])

        save_snapshot(output_dir=out)

        snapshots = list_snapshots(output_dir=out)
        assert len(snapshots) == 1
        entry = snapshots[0]
        assert "timestamp" in entry
        assert "files" in entry
        assert "collectionStats" in entry
        assert entry["collectionStats"]["uniqueCards"] == 2
        assert entry["collectionStats"]["totalCards"] == 5
        assert "deckStats" in entry
        assert entry["deckStats"]["deckCount"] == 1


# ---------------------------------------------------------------------------
# 3. Collection diff
# ---------------------------------------------------------------------------

def test_diff_collections_added():
    """Diff should detect newly added cards."""
    older = _make_collection({"100": 2, "200": 3})
    newer = _make_collection({"100": 2, "200": 3, "300": 1})
    diff = _diff_collections(older, newer)

    assert diff["summary"]["added"] == 1
    assert diff["summary"]["unchanged"] == 2
    assert diff["added"] == [{"cardId": "300", "count": 1}]


def test_diff_collections_removed():
    """Diff should detect removed cards."""
    older = _make_collection({"100": 2, "200": 3})
    newer = _make_collection({"100": 2})
    diff = _diff_collections(older, newer)

    assert diff["summary"]["removed"] == 1
    assert diff["removed"] == [{"cardId": "200", "oldCount": 3}]


def test_diff_collections_increased():
    """Diff should detect increased card counts."""
    older = _make_collection({"100": 2})
    newer = _make_collection({"100": 4})
    diff = _diff_collections(older, newer)

    assert diff["summary"]["increased"] == 1
    assert diff["increased"][0]["delta"] == 2
    assert diff["increased"][0]["newCount"] == 4
    assert diff["increased"][0]["oldCount"] == 2


def test_diff_collections_decreased():
    """Diff should detect decreased card counts."""
    older = _make_collection({"100": 4})
    newer = _make_collection({"100": 1})
    diff = _diff_collections(older, newer)

    assert diff["summary"]["decreased"] == 1
    assert diff["decreased"][0]["delta"] == 3
    assert diff["decreased"][0]["newCount"] == 1
    assert diff["decreased"][0]["oldCount"] == 4


def test_diff_collections_unchanged():
    """Diff should report unchanged cards."""
    older = _make_collection({"100": 2, "200": 3})
    newer = _make_collection({"100": 2, "200": 3})
    diff = _diff_collections(older, newer)

    assert diff["summary"]["unchanged"] == 2
    assert diff["summary"]["added"] == 0
    assert diff["summary"]["removed"] == 0
    assert diff["summary"]["netChange"] == 0


def test_diff_collections_wildcards():
    """Diff should detect wildcard changes."""
    older = _make_collection({"100": 2}, {"wcRare": 3, "wcMythic": 1})
    newer = _make_collection({"100": 2}, {"wcRare": 5, "wcMythic": 1})
    diff = _diff_collections(older, newer)

    assert diff["wildcardDiff"] is not None
    assert "wcRare" in diff["wildcardDiff"]
    assert diff["wildcardDiff"]["wcRare"]["delta"] == 2
    assert "wcMythic" not in diff["wildcardDiff"]


def test_diff_collections_net_change():
    """Net change should be total added minus total removed."""
    older = _make_collection({"100": 2, "200": 3})
    newer = _make_collection({"100": 4, "300": 1})
    diff = _diff_collections(older, newer)

    # +2 (100 increased) + 1 (300 added) - 3 (200 removed) = 0
    assert diff["summary"]["totalAdded"] == 3
    assert diff["summary"]["totalRemoved"] == 3
    assert diff["summary"]["netChange"] == 0


def test_diff_collections_mixed():
    """Diff with all change types."""
    older = _make_collection({"100": 2, "200": 3, "300": 1})
    newer = _make_collection({"100": 4, "200": 3, "400": 2})
    diff = _diff_collections(older, newer)

    assert diff["summary"]["added"] == 1      # 400
    assert diff["summary"]["removed"] == 1   # 300
    assert diff["summary"]["increased"] == 1 # 100
    assert diff["summary"]["unchanged"] == 1 # 200


# ---------------------------------------------------------------------------
# 4. Decks diff
# ---------------------------------------------------------------------------

def test_diff_decks_added():
    """Diff should detect newly added decks."""
    older = _make_decks([{"deckId": "d1", "name": "Deck 1"}])
    newer = _make_decks([
        {"deckId": "d1", "name": "Deck 1"},
        {"deckId": "d2", "name": "Deck 2"},
    ])
    diff = _diff_decks(older, newer)

    assert diff["summary"]["added"] == 1
    assert diff["added"][0]["deckId"] == "d2"
    assert diff["added"][0]["name"] == "Deck 2"


def test_diff_decks_removed():
    """Diff should detect removed decks."""
    older = _make_decks([
        {"deckId": "d1", "name": "Deck 1"},
        {"deckId": "d2", "name": "Deck 2"},
    ])
    newer = _make_decks([{"deckId": "d1", "name": "Deck 1"}])
    diff = _diff_decks(older, newer)

    assert diff["summary"]["removed"] == 1
    assert diff["removed"][0]["deckId"] == "d2"


def test_diff_decks_modified():
    """Diff should detect modified decks (card changes)."""
    older = _make_decks([{
        "deckId": "d1",
        "name": "Deck 1",
        "cards": {"mainboard": [{"cardId": 100, "name": "Bolt", "count": 4}]},
    }])
    newer = _make_decks([{
        "deckId": "d1",
        "name": "Deck 1",
        "cards": {"mainboard": [
            {"cardId": 100, "name": "Bolt", "count": 3},
            {"cardId": 200, "name": "Shock", "count": 1},
        ]},
    }])
    diff = _diff_decks(older, newer)

    assert diff["summary"]["modified"] == 1
    mod = diff["modified"][0]
    assert mod["deckId"] == "d1"
    assert mod["cardChanges"]["added"][0]["cardId"] == "200"
    assert mod["cardChanges"]["changed"][0]["delta"] == -1


def test_diff_decks_unchanged():
    """Diff should not report unchanged decks as modified."""
    deck = {"deckId": "d1", "name": "Deck 1", "cards": {"mainboard": [{"cardId": 100, "count": 4}]}}
    older = _make_decks([deck])
    newer = _make_decks([deck])
    diff = _diff_decks(older, newer)

    assert diff["summary"]["added"] == 0
    assert diff["summary"]["removed"] == 0
    assert diff["summary"]["modified"] == 0


# ---------------------------------------------------------------------------
# 5. Full diff_snapshots
# ---------------------------------------------------------------------------

def test_diff_snapshots_last_two():
    """diff_snapshots should diff the last two snapshots by default."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)

        # Snapshot 1
        _write_collection(out / "collection.json", {"100": 2, "200": 3})
        save_snapshot(output_dir=out)
        time.sleep(1.1)

        # Snapshot 2
        _write_collection(out / "collection.json", {"100": 4, "200": 3, "300": 1})
        save_snapshot(output_dir=out)

        diff = diff_snapshots(output_dir=out)

        assert "error" not in diff
        assert diff["collectionDiff"]["summary"]["added"] == 1
        assert diff["collectionDiff"]["summary"]["increased"] == 1
        assert diff["collectionDiff"]["summary"]["unchanged"] == 1


def test_diff_snapshots_specific():
    """diff_snapshots should accept specific timestamps."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)

        _write_collection(out / "collection.json", {"100": 2})
        save_snapshot(output_dir=out)
        time.sleep(1.1)
        ts1 = list_snapshots(output_dir=out)[0]["timestamp"]

        _write_collection(out / "collection.json", {"100": 4})
        save_snapshot(output_dir=out)
        time.sleep(1.1)
        ts2 = list_snapshots(output_dir=out)[1]["timestamp"]

        _write_collection(out / "collection.json", {"100": 4, "200": 1})
        save_snapshot(output_dir=out)

        diff = diff_snapshots(output_dir=out, older=ts1, newer=ts2)
        assert "error" not in diff
        assert diff["collectionDiff"]["summary"]["increased"] == 1


def test_diff_snapshots_single_snapshot():
    """diff_snapshots should return error with only one snapshot."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_collection(out / "collection.json", {"100": 2})
        save_snapshot(output_dir=out)

        diff = diff_snapshots(output_dir=out)
        assert "error" in diff


def test_diff_snapshots_no_snapshots():
    """diff_snapshots should return error with no snapshots."""
    with tempfile.TemporaryDirectory() as tmp:
        diff = diff_snapshots(output_dir=Path(tmp))
        assert "error" in diff


def test_diff_snapshots_not_found():
    """diff_snapshots should return error for non-existent timestamp."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)
        _write_collection(out / "collection.json", {"100": 2})
        save_snapshot(output_dir=out)
        time.sleep(1.1)
        _write_collection(out / "collection.json", {"100": 3})
        save_snapshot(output_dir=out)

        diff = diff_snapshots(output_dir=out, older="1999-01-01")
        assert "error" in diff


# ---------------------------------------------------------------------------
# 6. Deck card extraction
# ---------------------------------------------------------------------------

def test_extract_deck_card_counts_scan_format():
    """Extract cards from deck-scan format (cards as dict of piles)."""
    deck = {
        "cards": {
            "mainboard": [
                {"cardId": 100, "name": "Bolt", "count": 4},
                {"cardId": 200, "name": "Shock", "count": 2},
            ],
            "sideboard": [
                {"cardId": 300, "name": "Duress", "count": 3},
            ],
        },
    }
    counts = _extract_deck_card_counts(deck)
    assert counts == {"100": 4, "200": 2, "300": 3}


def test_extract_deck_card_counts_cardsbyid_format():
    """Extract cards from cardsById format."""
    deck = {
        "cardsById": {
            "mainboard": {"100": 4, "200": 2},
            "sideboard": {"300": 3},
        },
    }
    counts = _extract_deck_card_counts(deck)
    assert counts == {"100": 4, "200": 2, "300": 3}


def test_extract_deck_card_counts_log_format():
    """Extract cards from log-export format (mainDeck as flat list)."""
    deck = {
        "mainDeck": [
            {"grpId": 100, "quantity": 4},
            {"grpId": 200, "quantity": 2},
        ],
        "sideboard": [
            {"grpId": 300, "quantity": 3},
        ],
    }
    counts = _extract_deck_card_counts(deck)
    assert counts == {"100": 4, "200": 2, "300": 3}


def test_extract_deck_card_counts_empty():
    """Extract cards from empty deck."""
    counts = _extract_deck_card_counts({})
    assert counts == {}


def test_extract_deck_card_counts_flat_list():
    """Extract cards from flat card list format."""
    deck = {
        "cards": [
            {"cardId": 100, "count": 4},
            {"cardId": 200, "count": 2},
        ],
    }
    counts = _extract_deck_card_counts(deck)
    assert counts == {"100": 4, "200": 2}


def test_extract_deck_card_counts_aggregates_piles():
    """Cards in different piles should be aggregated by cardId."""
    deck = {
        "cards": {
            "mainboard": [{"cardId": 100, "count": 2}],
            "sideboard": [{"cardId": 100, "count": 1}],
        },
    }
    counts = _extract_deck_card_counts(deck)
    assert counts == {"100": 3}


# ---------------------------------------------------------------------------
# 7. Helper functions
# ---------------------------------------------------------------------------

def test_normalize_card_dict():
    """_normalize_card_dict should convert all values to int."""
    result = _normalize_card_dict({"100": "2", "200": 3, "300": "abc"})
    assert result == {"100": 2, "200": 3}
    assert "300" not in result


def test_normalize_card_dict_non_dict():
    """_normalize_card_dict should return empty dict for non-dict input."""
    assert _normalize_card_dict(None) == {}
    assert _normalize_card_dict([]) == {}
    assert _normalize_card_dict("string") == {}


def test_safe_int():
    """_safe_int should handle various input types."""
    assert _safe_int(5) == 5
    assert _safe_int("5") == 5
    assert _safe_int("abc") == 0
    assert _safe_int(None) == 0
    assert _safe_int("abc", default=-1) == -1


def test_timestamp_to_filename():
    """_timestamp_to_filename should replace colons with dashes."""
    assert _timestamp_to_filename("2026-08-01T10:00:00Z") == "2026-08-01T10-00-00Z"


def test_find_snapshot_exact():
    """_find_snapshot should find by exact timestamp."""
    snapshots = [
        {"timestamp": "2026-08-01T10:00:00Z", "files": {}},
        {"timestamp": "2026-08-02T14:30:00Z", "files": {}},
    ]
    result = _find_snapshot(snapshots, "2026-08-01T10:00:00Z")
    assert result is not None
    assert result["timestamp"] == "2026-08-01T10:00:00Z"


def test_find_snapshot_prefix():
    """_find_snapshot should find by timestamp prefix."""
    snapshots = [
        {"timestamp": "2026-08-01T10:00:00Z", "files": {}},
        {"timestamp": "2026-08-02T14:30:00Z", "files": {}},
    ]
    result = _find_snapshot(snapshots, "2026-08-01")
    assert result is not None
    assert result["timestamp"] == "2026-08-01T10:00:00Z"


def test_find_snapshot_not_found():
    """_find_snapshot should return None for non-existent timestamp."""
    snapshots = [{"timestamp": "2026-08-01T10:00:00Z", "files": {}}]
    assert _find_snapshot(snapshots, "1999-01-01") is None


# ---------------------------------------------------------------------------
# 8. Formatting helpers
# ---------------------------------------------------------------------------

def test_format_diff_summary_with_changes():
    """format_diff_summary should produce readable output."""
    diff = {
        "older": "2026-08-01T10:00:00Z",
        "newer": "2026-08-02T14:30:00Z",
        "collectionDiff": {
            "summary": {
                "added": 1, "removed": 0, "increased": 1, "decreased": 0,
                "unchanged": 2, "totalAdded": 3, "totalRemoved": 0, "netChange": 3,
            },
            "added": [{"cardId": "300", "count": 1}],
            "removed": [],
            "increased": [{"cardId": "100", "oldCount": 2, "newCount": 4, "delta": 2}],
            "decreased": [],
            "wildcardDiff": {"wcRare": {"old": 3, "new": 5, "delta": 2}},
        },
        "decksDiff": {
            "summary": {"added": 1, "removed": 0, "modified": 0},
            "added": [{"deckId": "d2", "name": "New Deck"}],
            "removed": [],
            "modified": [],
        },
    }
    output = format_diff_summary(diff)
    assert "2026-08-01T10:00:00Z" in output
    assert "2026-08-02T14:30:00Z" in output
    assert "Collection" in output
    assert "+1 neu" in output
    assert "Card 300" in output
    assert "Wildcards" in output
    assert "Decks" in output
    assert "New Deck" in output


def test_format_diff_summary_error():
    """format_diff_summary should handle error case."""
    diff = {"error": "Weniger als 2 Snapshots"}
    output = format_diff_summary(diff)
    assert "ERROR" in output


def test_format_snapshot_list_empty():
    """format_snapshot_list should handle empty list."""
    output = format_snapshot_list([])
    assert "Keine" in output


def test_format_snapshot_list_with_entries():
    """format_snapshot_list should format entries."""
    snapshots = [
        {
            "timestamp": "2026-08-01T10:00:00Z",
            "files": {"collection": "2026-08-01T10-00-00Z_collection.json"},
            "collectionStats": {"uniqueCards": 100, "totalCards": 500},
            "deckStats": {"deckCount": 5},
        },
        {
            "timestamp": "2026-08-02T14:30:00Z",
            "files": {"collection": "2026-08-02T14-30-00Z_collection.json"},
            "collectionStats": {"uniqueCards": 105, "totalCards": 520},
            "deckStats": {"deckCount": 6},
        },
    ]
    output = format_snapshot_list(snapshots)
    assert "2 Snapshots" in output
    assert "2026-08-01T10:00:00Z" in output
    assert "2026-08-02T14:30:00Z" in output
    assert "latest" in output
    assert "100 unique" in output
    assert "5 decks" in output


# ---------------------------------------------------------------------------
# 9. Integration: full save → list → diff cycle
# ---------------------------------------------------------------------------

def test_full_cycle_save_list_diff():
    """Full cycle: save two snapshots, list them, diff them."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp)

        # First snapshot
        _write_collection(out / "collection.json", {"100": 2, "200": 3})
        _write_decks(out / "decks.json", [
            {"deckId": "d1", "name": "Deck 1", "cards": {"mainboard": [{"cardId": 100, "count": 2}]}}
        ])
        save_snapshot(output_dir=out)
        time.sleep(1.1)

        # Second snapshot
        _write_collection(out / "collection.json", {"100": 4, "200": 3, "300": 1})
        _write_decks(out / "decks.json", [
            {"deckId": "d1", "name": "Deck 1", "cards": {"mainboard": [{"cardId": 100, "count": 4}]}}
        ])
        save_snapshot(output_dir=out)

        # List
        snapshots = list_snapshots(output_dir=out)
        assert len(snapshots) == 2

        # Diff
        diff = diff_snapshots(output_dir=out)
        assert "error" not in diff
        assert diff["collectionDiff"]["summary"]["added"] == 1
        assert diff["collectionDiff"]["summary"]["increased"] == 1
        assert diff["decksDiff"]["summary"]["modified"] == 1


if __name__ == "__main__":
    import unittest
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    runner.run(suite)