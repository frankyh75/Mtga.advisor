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
