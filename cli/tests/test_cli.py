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
    run_report_expected["logMetadata"] = run_report_actual["logMetadata"]
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


def test_decks_command_outputs_preview(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log_path = tmp_path / "Player.log"
    log_path.write_text(
        """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] DeckGetDeckLists
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"Decks":{"deck-1":{"MainDeck":[{"cardId":100001,"quantity":2},{"cardId":100002,"quantity":1}],"Sideboard":[{"cardId":100003,"quantity":1}]}}}
""",
        encoding="utf-8",
    )

    exit_code = main(["decks", "--log", str(log_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Decks gefunden: 1" in captured.out
    assert "[saved]" in captured.out
    assert "main=3" in captured.out
    assert "side=1" in captured.out


def test_carddb_import_and_info(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db_path = tmp_path / "carddb.sqlite"
    oracle_path = _fixture_path("scryfall-oracle-mini.json")

    exit_code = main(
        [
            "carddb",
            "import",
            "--input",
            str(oracle_path),
            "--source",
            "oracle",
            "--db",
            str(db_path),
        ]
    )

    assert exit_code == 0

    exit_code = main(["carddb", "info", "--db", str(db_path)])
    captured = capsys.readouterr()
    assert exit_code == 0
    assert "oracle=2" in captured.out


def test_deck_analysis_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log_path = tmp_path / "Player.log"
    log_path.write_text(
        """[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] EventGetCoursesV2
[2025-01-01T12:00:00Z] [UnityCrossThreadLogger] {"Courses":[{"InternalEventName":"Historic_Ladder","CourseDeckSummary":{"DeckId":"deck-1","Name":"Last Played","Attributes":[{"name":"Format","value":"Explorer"}]},"CourseDeck":{"MainDeck":[{"cardId":100001,"quantity":2},{"cardId":100002,"quantity":1}],"Sideboard":[{"cardId":100003,"quantity":1}]}}]}
""",
        encoding="utf-8",
    )
    db_path = tmp_path / "carddb.sqlite"
    oracle_path = _fixture_path("scryfall-oracle-mini.json")
    main(
        [
            "carddb",
            "import",
            "--input",
            str(oracle_path),
            "--source",
            "oracle",
            "--db",
            str(db_path),
        ]
    )
    output_dir = tmp_path / "out"

    exit_code = main(
        [
            "deck-analysis",
            "--log",
            str(log_path),
            "--db",
            str(db_path),
            "--output",
            str(output_dir),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Deck-Analyse Summary" in captured.out
    assert (output_dir / "deck-analysis-summary.json").exists()
    assert (output_dir / "deck-analysis" / "deck-1.json").exists()
