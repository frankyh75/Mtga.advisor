"""Tests für CLI-Integration des Sudo-Helpers.

Testet, dass der 'scan' und 'run' Befehl den Helper auto-detectet
und bei Nicht-Verfügbarkeit auf den direkten sudo-Weg fällt.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from cli.main import main  # noqa: E402


def test_scan_command_uses_helper_when_available(tmp_path, monkeypatch):
    """Wenn der Helper verfügbar ist, wird der Scan über den Helper ausgeführt."""
    import cli.main as cli_module
    from scanner.memory_scanner import MemoryScanResult

    # Helper als verfügbar mocken
    monkeypatch.setattr(cli_module, "_is_helper_available", lambda: True)

    # Helper-Scan mocken
    helper_result = MemoryScanResult(
        collection={100: 4, 200: 1},
        anchors=[],
        anchor_matches={},
        validation={"valid": True, "errors": [], "warnings": [], "cardsCount": 2, "totalCards": 5},
    )
    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        lambda debug=False: helper_result,
    )

    exit_code = main(["scan", "--output", str(tmp_path / "out")])
    assert exit_code == 0
    collection_path = tmp_path / "out" / "collection.json"
    assert collection_path.exists()
    payload = json.loads(collection_path.read_text(encoding="utf-8"))
    assert payload["source"] == "memory-scan"
    assert payload["cards"] == {"100": 4, "200": 1}


def test_scan_command_falls_back_to_direct_when_helper_unavailable(tmp_path, monkeypatch):
    """Wenn der Helper nicht verfügbar ist, wird der direkte Scan verwendet (mit Warnung)."""
    import cli.main as cli_module
    from scanner.memory_scanner import MemoryScanResult

    # Helper als nicht verfügbar mocken
    monkeypatch.setattr(cli_module, "_is_helper_available", lambda: False)

    # Direkten Scan mocken
    monkeypatch.setattr(
        cli_module,
        "scan_memory_collection_detailed",
        lambda debug=False: MemoryScanResult(
            collection={100: 4},
            anchors=[],
            anchor_matches={},
            validation={"valid": True, "errors": [], "warnings": [], "cardsCount": 1, "totalCards": 4},
        ),
    )

    exit_code = main(["scan", "--output", str(tmp_path / "out")])
    assert exit_code == 0
    assert (tmp_path / "out" / "collection.json").exists()


def test_scan_command_warns_when_helper_unavailable(tmp_path, monkeypatch, capsys):
    """Bei Fallback auf direkten Scan wird eine Warnung auf stderr ausgegeben."""
    import cli.main as cli_module
    from scanner.memory_scanner import MemoryScanResult

    monkeypatch.setattr(cli_module, "_is_helper_available", lambda: False)
    monkeypatch.setattr(
        cli_module,
        "scan_memory_collection_detailed",
        lambda debug=False: MemoryScanResult(
            collection={100: 4},
            anchors=[],
            anchor_matches={},
            validation={"valid": True, "errors": [], "warnings": [], "cardsCount": 1, "totalCards": 4},
        ),
    )

    main(["scan", "--output", str(tmp_path / "out")])
    captured = capsys.readouterr()
    assert "sudo" in captured.err.lower()
    assert "helper" in captured.err.lower()


def test_run_command_uses_helper_on_macos(tmp_path, monkeypatch):
    """Der 'run' Befehl nutzt auf macOS den Helper, wenn verfügbar."""
    import cli.main as cli_module
    from scanner.memory_scanner import MemoryScanResult

    monkeypatch.setattr(cli_module, "detect_platform", lambda: "macos")
    monkeypatch.setattr(cli_module, "_is_helper_available", lambda: True)

    helper_result = MemoryScanResult(
        collection={100: 4, 200: 1},
        anchors=[],
        anchor_matches={},
        validation={"valid": True, "errors": [], "warnings": [], "cardsCount": 2, "totalCards": 5},
    )
    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        lambda debug=False: helper_result,
    )

    exit_code = main(["run", "--output", str(tmp_path / "out")])
    assert exit_code == 0
    assert (tmp_path / "out" / "collection.json").exists()


def test_run_command_falls_back_on_macos_without_helper(tmp_path, monkeypatch):
    """Der 'run' Befehl fällt auf direkten Scan zurück, wenn Helper fehlt."""
    import cli.main as cli_module
    from scanner.memory_scanner import MemoryScanResult

    monkeypatch.setattr(cli_module, "detect_platform", lambda: "macos")
    monkeypatch.setattr(cli_module, "_is_helper_available", lambda: False)
    monkeypatch.setattr(
        cli_module,
        "scan_memory_collection_detailed",
        lambda debug=False: MemoryScanResult(
            collection={100: 4},
            anchors=[],
            anchor_matches={},
            validation={"valid": True, "errors": [], "warnings": [], "cardsCount": 1, "totalCards": 4},
        ),
    )

    exit_code = main(["run", "--output", str(tmp_path / "out")])
    assert exit_code == 0
    assert (tmp_path / "out" / "collection.json").exists()


def test_scan_command_returns_error_when_helper_fails_and_direct_fails(tmp_path, monkeypatch):
    """Wenn Helper nicht verfügbar und direkter Scan None liefert, exit code 1."""
    import cli.main as cli_module

    monkeypatch.setattr(cli_module, "_is_helper_available", lambda: False)
    monkeypatch.setattr(cli_module, "scan_memory_collection_detailed", lambda debug=False: None)

    exit_code = main(["scan", "--output", str(tmp_path / "out")])
    assert exit_code == 1


def test_scan_command_returns_error_when_helper_available_but_scan_fails(tmp_path, monkeypatch):
    """Wenn Helper verfügbar aber Helper-Scan None liefert, exit code 1."""
    import cli.main as cli_module

    monkeypatch.setattr(cli_module, "_is_helper_available", lambda: True)
    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        lambda debug=False: None,
    )

    exit_code = main(["scan", "--output", str(tmp_path / "out")])
    assert exit_code == 1


def test_run_command_uses_logs_on_macos_when_specified(tmp_path, monkeypatch):
    """Der 'run' Befehl mit --logs nutzt Log-Export, nicht Memory-Scan."""
    import cli.main as cli_module
    import parser.export as export_module

    times = iter(["2025-01-02T00:00:00Z", "2025-01-02T00:00:05Z"])
    monkeypatch.setattr(export_module, "_iso_now", lambda: next(times))
    monkeypatch.setattr(cli_module, "detect_platform", lambda: "macos")

    # Helper sollte nicht aufgerufen werden
    helper_called: list[bool] = []
    monkeypatch.setattr(cli_module, "_is_helper_available", lambda: helper_called.append(True) or True)

    exit_code = main([
        "run",
        "--log",
        str(Path("fixtures", "Player.log")),
        "--output",
        str(tmp_path / "out"),
    ])
    assert exit_code == 0
    assert (tmp_path / "out" / "collection.json").exists()
    # Helper wurde nicht für Log-basierten Export aufgerufen
    assert helper_called == []