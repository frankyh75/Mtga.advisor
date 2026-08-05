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


def test_decks_command_exports_decks_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter(["2025-01-02T00:00:00Z", "2025-01-02T00:00:05Z"])
    import parser.decks as decks_module

    monkeypatch.setattr(decks_module, "_iso_now", lambda: next(times))

    log_dir = tmp_path / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA"
    log_dir.mkdir(parents=True)
    for name in ("Player.log", "Player-prev.log"):
        source = _fixture_path(name)
        log_dir.joinpath(name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    exit_code = main(
        [
            "decks",
            "--platform",
            "macos",
            "--macos-logs",
            str(log_dir),
            "--output",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "out" / "decks.json").exists()
    assert (tmp_path / "out" / "run-report-decks.json").exists()


def test_scan_command_exports_memory_scan_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cli.main as cli_module
    from scanner.memory_scanner import MemoryScanResult

    monkeypatch.setattr(
        cli_module,
        "scan_memory_collection_detailed",
        lambda debug=False: MemoryScanResult(
            collection={100: 4, 200: 1},
            anchors=[],
            anchor_matches={},
            validation={"valid": True, "errors": [], "warnings": [], "cardsCount": 2, "totalCards": 5},
        ),
    )

    exit_code = main(["scan", "--output", str(tmp_path / "memory")])

    assert exit_code == 0
    payload = json.loads((tmp_path / "memory" / "collection.json").read_text(encoding="utf-8"))
    assert payload["source"] == "memory-scan"
    assert payload["cards"] == {"100": 4, "200": 1}


def test_deck_scan_auto_runs_pattern_on_il2cpp_warnings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cli.main as cli_module
    from scanner.deck_scanner import DeckScanResult
    from scanner.il2cpp_nav import Il2CppDeckResult, Il2CppScanResult

    calls: list[list[int]] = []

    monkeypatch.setattr(cli_module, "scan_decks_il2cpp", lambda adapter, debug=False: Il2CppScanResult(
        decks=[Il2CppDeckResult(deck_id=1, name="Tiny Deck", piles={1: {100: 1}}, raw_address=0)],
        warnings=["partial parse"],
    ))
    monkeypatch.setattr(
        cli_module,
        "scan_decks",
        lambda pm, anchor_ids: calls.append(list(anchor_ids)) or DeckScanResult(decks=[], warnings=["fallback"]),
    )
    monkeypatch.setattr("scanner.memory_scanner._attach_process", lambda names: object())

    exit_code = main(["deck-scan", "--method", "auto", "--output", str(tmp_path / "out")])

    assert exit_code == 0
    assert calls == [[100]]


def test_deck_scan_backups_existing_outputs_on_unplausible_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cli.main as cli_module
    from scanner.il2cpp_nav import Il2CppDeckResult, Il2CppScanResult

    out = tmp_path / "out"
    decks_dir = out / "decks"
    decks_dir.mkdir(parents=True)
    (out / "decks-container.json").write_text("old container", encoding="utf-8")
    (out / "decks.json").write_text("old decks", encoding="utf-8")
    (decks_dir / "deck-1.json").write_text("old deck", encoding="utf-8")

    monkeypatch.setattr(
        cli_module,
        "scan_decks_il2cpp",
        lambda adapter, debug=False: Il2CppScanResult(
            decks=[Il2CppDeckResult(deck_id=1, name="Tiny Deck", piles={1: {100: 1}}, raw_address=0)],
            warnings=[],
        ),
    )
    monkeypatch.setattr(cli_module, "scan_decks", lambda pm, anchor_ids: pytest.fail("pattern scan should not run"))
    monkeypatch.setattr("scanner.memory_scanner._attach_process", lambda names: object())

    exit_code = main(["deck-scan", "--method", "auto", "--output", str(out)])

    assert exit_code == 0
    assert (out / "decks-container.json.bak").read_text(encoding="utf-8") == "old container"
    assert (out / "decks.json.bak").read_text(encoding="utf-8") == "old decks"
    assert (decks_dir / "deck-1.json.bak").read_text(encoding="utf-8") == "old deck"
    container = json.loads((out / "decks-container.json").read_text(encoding="utf-8"))
    assert container["schema"] == "decks-container.v1"


def test_run_command_uses_memory_scan_on_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cli.main as cli_module

    called: list[str] = []
    monkeypatch.setattr(cli_module, "detect_platform", lambda: "macos")
    monkeypatch.setattr(cli_module, "_run_scan", lambda args: called.append("scan") or 0)

    exit_code = main(["run", "--output", str(tmp_path / "out")])

    assert exit_code == 0
    assert called == ["scan"]


def test_validate_command_checks_existing_collection_and_writes_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cli.main as cli_module

    out = tmp_path / "out"
    out.mkdir()
    (out / "collection.json").write_text(
        json.dumps({"cards": {"100": 4, "200": 1}}),
        encoding="utf-8",
    )
    refresh_values: list[bool] = []

    def fake_load_card_database(*, refresh_cache: bool = False) -> dict[int, dict[str, str]]:
        refresh_values.append(refresh_cache)
        return {100: {"name": "A"}, 200: {"name": "B"}}

    monkeypatch.setattr(cli_module, "load_card_database", fake_load_card_database)

    exit_code = main(["validate", "--output", str(out), "--refresh-card-db"])

    assert exit_code == 0
    assert refresh_values == [True]
    report = json.loads((out / "validation-report.json").read_text(encoding="utf-8"))
    assert report["schema"] == "validation-report.v1"
    assert report["validation"]["valid"] is True


def test_deck_import_command_writes_arena_deck(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cli.main as cli_module

    deck_file = tmp_path / "deck.txt"
    deck_file.write_text("Deck\n4 Lightning Strike\n", encoding="utf-8")
    monkeypatch.setattr(
        cli_module,
        "load_card_database",
        lambda: {100: {"name": "Lightning Strike", "rarity": "common"}},
    )

    exit_code = main(
        [
            "deck",
            "import",
            "--file",
            str(deck_file),
            "--format",
            "standard",
            "--output",
            str(tmp_path / "out"),
        ]
    )

    assert exit_code == 0
    payload = json.loads((tmp_path / "out" / "arena_deck.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "arena-deck.v1"
    assert payload["mainboard"][0]["arenaId"] == 100


def test_advisor_complete_command_writes_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import cli.main as cli_module

    collection = tmp_path / "collection.json"
    deck = tmp_path / "arena_deck.json"
    out = tmp_path / "out"
    collection.write_text(
        json.dumps(
            {
                "cards": {"100": 2},
                "diagnostics": {
                    "completeness": {
                        "cards": "complete",
                        "wildcards": "unknown",
                        "source": "complete",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    deck.write_text(
        json.dumps(
            {
                "schema": "arena-deck.v1",
                "deckId": "abc",
                "name": "Test Deck",
                "format": "standard",
                "mainboard": [{"arenaId": 100, "name": "Lightning Strike", "count": 4}],
                "sideboard": [],
                "diagnostics": {"warnings": []},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cli_module,
        "load_card_database",
        lambda: {100: {"name": "Lightning Strike", "rarity": "common"}},
    )

    exit_code = main(
        [
            "advisor",
            "complete",
            "--collection",
            str(collection),
            "--deck",
            str(deck),
            "--output",
            str(out),
        ]
    )

    assert exit_code == 0
    payload = json.loads((out / "advisor-result.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "advisor-result.v1"
    assert payload["summary"]["missingCards"] == 2


def test_decks_container_command_reads_decks_json_and_writes_container(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "decks.json").write_text(
        json.dumps(
            {
                "schema": "decks.v1",
                "decks": [
                    {
                        "name": "Control",
                        "deckId": "abc123",
                        "deckTileId": 1,
                        "description": None,
                        "attributes": {"Format": "standard"},
                        "formatLegalities": {"standard": True},
                        "isCompanionValid": True,
                        "mana": "U",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(["decks", "container", "--output", str(out / "decks")])

    assert exit_code == 0
    assert (out / "decks" / "index.json").exists()
    assert (out / "decks" / "abc123.json").exists()
