from __future__ import annotations

from pathlib import Path
import json

import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from parser import export as export_module  # noqa: E402
from parser.export import STALE_SNAPSHOT_WARNING, export_collection  # noqa: E402


def _write_log(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "Player.log"
    path.write_text(content, encoding="utf-8")
    return path


def _fixture_path(*parts: str) -> Path:
    return Path("fixtures", *parts)


def test_export_collection_matches_golden_samples(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter(["2025-01-02T00:00:00Z", "2025-01-02T00:00:05Z"])
    monkeypatch.setattr(export_module, "_iso_now", lambda: next(times))

    logs = [_fixture_path("Player-prev.log"), _fixture_path("Player.log")]
    export_paths = export_collection(logs, tmp_path / "out")

    assert export_paths.collection.read_text(encoding="utf-8") == _fixture_path(
        "golden",
        "collection.json",
    ).read_text(encoding="utf-8")

    run_report_actual = json.loads(export_paths.run_report.read_text(encoding="utf-8"))
    run_report_golden = json.loads(
        _fixture_path("golden", "run-report.json").read_text(encoding="utf-8")
    )
    run_report_golden["outputs"] = run_report_actual["outputs"]
    assert run_report_actual == run_report_golden

    golden_samples = _fixture_path("golden", "raw-samples")
    for sample_path in sorted(golden_samples.iterdir()):
        actual_path = export_paths.raw_samples_dir / sample_path.name
        assert actual_path.read_text(encoding="utf-8") == sample_path.read_text(encoding="utf-8")


def test_export_collection_flags_stale_snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] PlayerInventory.GetPlayerCardsV3
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"playerId":"anon","cards":[{"id":100001,"quantity":2}],"asOf":"2025-01-01T12:00:00Z"}
"""
    times = iter(["2025-02-01T00:00:00Z", "2025-02-01T00:00:05Z"])
    monkeypatch.setattr(export_module, "_iso_now", lambda: next(times))

    export_paths = export_collection([_write_log(tmp_path, log)], tmp_path / "out")
    payload = json.loads(export_paths.collection.read_text(encoding="utf-8"))

    assert STALE_SNAPSHOT_WARNING in payload["diagnostics"]["warnings"]
