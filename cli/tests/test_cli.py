from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from cli.main import main  # noqa: E402


def _fixture_path(*parts: str) -> Path:
    return Path("fixtures", *parts)


def test_collection_command_exports_with_explicit_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter(["2025-01-02T00:00:00Z", "2025-01-02T00:00:05Z"])
    import parser.export as export_module

    monkeypatch.setattr(export_module, "_iso_now", lambda: next(times))

    exit_code = main(
        [
            "collection",
            "--log",
            str(_fixture_path("Player-prev.log")),
            "--log",
            str(_fixture_path("Player.log")),
            "--output",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    collection_path = tmp_path / "out" / "collection.json"
    run_report_path = tmp_path / "out" / "run-report.json"
    assert collection_path.exists()
    assert run_report_path.exists()

    collection_actual = json.loads(collection_path.read_text(encoding="utf-8"))
    collection_expected = json.loads(
        _fixture_path("golden", "collection.json").read_text(encoding="utf-8")
    )
    assert collection_actual == collection_expected

    run_report_actual = json.loads(run_report_path.read_text(encoding="utf-8"))
    run_report_expected = json.loads(
        _fixture_path("golden", "run-report.json").read_text(encoding="utf-8")
    )
    run_report_expected["outputs"] = run_report_actual["outputs"]
    assert run_report_actual == run_report_expected


def test_collection_command_errors_on_missing_log(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    missing_log = tmp_path / "missing.log"

    exit_code = main(["collection", "--log", str(missing_log), "--output", str(tmp_path / "out")])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Logdatei(en) nicht gefunden" in captured.err


def test_collection_command_discovers_logs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter(["2025-01-02T00:00:00Z", "2025-01-02T00:00:05Z"])
    import parser.export as export_module

    monkeypatch.setattr(export_module, "_iso_now", lambda: next(times))

    log_dir = tmp_path / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA"
    log_dir.mkdir(parents=True)
    for name in ("Player.log", "Player-prev.log"):
        source = _fixture_path(name)
        log_dir.joinpath(name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    exit_code = main(
        [
            "collection",
            "--platform",
            "macos",
            "--macos-logs",
            str(log_dir),
            "--output",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "out" / "collection.json").exists()
