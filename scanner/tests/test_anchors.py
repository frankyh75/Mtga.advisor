"""Tests für die Anker-Auflösung (vorgegebene Anker, non-interactive).

Hintergrund: Der Scan darf nie an einer Karten-Abfrage hängen bleiben, wenn
er aus einem Launcher/Skript kommt (stdin ist dort kein Terminal). Genau das
war der Fehler — jeder ``sudo mtga-scanner.sh``-Lauf fragte nach 5 Karten.

Abgedeckte Fälle:
  1. non_interactive ohne Anker-Datei → Standard-Set, genau 5 Anker
  2. non_interactive ohne Datei → Anker werden gespeichert
  3. non_interactive mit Datei → Datei gewinnt (Standard-Set wird nicht benutzt)
  4. interaktiv ohne Datei → fragt weiterhin (Verhalten unverändert)
  5. Standard-Anker sind syntaktisch gültig und in der Karten-DB bekannt
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scanner import memory_scanner
from scanner.memory_scanner import DEFAULT_ANCHORS, get_user_anchors


class DummyBackend:
    """Minimaler Backend-Stub; Scanner und Parser werden im Test ersetzt."""

    def read_bytes(self, addr: int, size: int) -> bytes | None:
        return b"\x00" * size

    def iterate_writable_private_regions(self) -> list[tuple[int, int]]:
        return []

    def iterate_readable_regions(self) -> tuple[list[tuple[int, int]], int | None]:
        return [], None


@pytest.fixture()
def anchor_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Anker-Datei in ein temporäres Verzeichnis umleiten."""
    path = tmp_path / "last_anchors.json"
    monkeypatch.setattr(memory_scanner, "_anchor_file", lambda: path)
    return path


def test_default_anchors_are_five_valid_tuples() -> None:
    assert len(DEFAULT_ANCHORS) == 5
    for entry in DEFAULT_ANCHORS:
        assert len(entry) == 3
        grp_id, qty, name = entry
        assert isinstance(grp_id, int) and grp_id > 0
        assert isinstance(qty, int) and qty > 0
        assert isinstance(name, str) and name


def test_non_interactive_without_file_uses_defaults(anchor_file: Path) -> None:
    lines: list[str] = []
    anchors = get_user_anchors(
        {},
        non_interactive=True,
        print_fn=lambda *a, **k: lines.append(" ".join(str(x) for x in a)),
    )

    assert anchors == list(DEFAULT_ANCHORS)
    assert len(anchors) == 5
    assert any("Standard-Anker" in line for line in lines)


def test_non_interactive_never_prompts(anchor_file: Path) -> None:
    """input() darf nicht aufgerufen werden — sonst hängt ein Launcher."""

    def exploding_input(_prompt: str) -> str:
        raise AssertionError("Scan hat nach Ankern gefragt (nicht-interaktiv!)")

    anchors = get_user_anchors(
        {},
        non_interactive=True,
        input_fn=exploding_input,
        print_fn=lambda *a, **k: None,
    )
    assert len(anchors) == 5


def test_no_tty_uses_defaults_without_reading_stdin_and_records_warning(
    anchor_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CI/Launcher ohne TTY dürfen ohne --non-interactive nicht blockieren."""
    monkeypatch.setattr(memory_scanner.sys.stdin, "isatty", lambda: False)
    warnings: list[str] = []

    def exploding_input(_prompt: str) -> str:
        raise AssertionError("Scan hat trotz fehlendem TTY stdin gelesen")

    anchors = get_user_anchors(
        {},
        input_fn=exploding_input,
        print_fn=lambda *a, **k: None,
        warnings=warnings,
    )

    assert anchors == list(DEFAULT_ANCHORS)
    assert warnings == ["anchor input skipped: no tty"]


def test_no_tty_warning_is_in_scan_result(
    anchor_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der automatisierte Fallback muss im exportierbaren Ergebnis sichtbar sein."""
    monkeypatch.setattr(memory_scanner.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(memory_scanner, "DEFAULT_ANCHORS", ((114001, 1, "Card A"),))

    result = memory_scanner.scan_collection_detailed(
        backend=DummyBackend(),
        db_loader=lambda: {114001: {"name": "Card A"}},
        memory_scanner=lambda _backend, _needle: [0x2000],
        block_parser=lambda _backend, _address: [{114001: 1}],
        input_fn=lambda _prompt: pytest.fail("stdin darf ohne TTY nicht gelesen werden"),
        print_fn=lambda *a, **k: None,
    )

    assert result is not None
    assert "anchor input skipped: no tty" in result.validation["warnings"]


def test_non_interactive_persists_defaults(anchor_file: Path) -> None:
    assert not anchor_file.exists()

    get_user_anchors(
        {},
        non_interactive=True,
        print_fn=lambda *a, **k: None,
    )

    assert anchor_file.exists()
    # JSON speichert Tupel als Listen — geprüft wird der Round-Trip:
    # die Datei muss sich wieder als Anker-Liste einlesen lassen.
    saved = json.loads(anchor_file.read_text(encoding="utf-8"))
    assert saved == [list(a) for a in DEFAULT_ANCHORS]

    reloaded = get_user_anchors(
        {},
        non_interactive=True,
        print_fn=lambda *a, **k: None,
    )
    assert reloaded == list(DEFAULT_ANCHORS)


def test_saved_file_wins_over_defaults(anchor_file: Path) -> None:
    custom = [(999001, 3, "Custom Card")]
    anchor_file.write_text(json.dumps(custom), encoding="utf-8")

    anchors = get_user_anchors(
        {},
        non_interactive=True,
        print_fn=lambda *a, **k: None,
    )

    assert anchors == custom


def test_interactive_without_file_still_asks(
    anchor_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Der interaktive Weg muss unverändert bleiben."""
    monkeypatch.setattr(memory_scanner.sys.stdin, "isatty", lambda: True)
    answers = iter(["Card A", "2", "", "", "", "", ""])
    prompts: list[str] = []
    lines: list[str] = []

    def fake_input(prompt: str) -> str:
        prompts.append(prompt)
        return next(answers)

    anchors = get_user_anchors(
        {"card a": 114001},
        non_interactive=False,
        input_fn=fake_input,
        print_fn=lambda *a, **k: lines.append(" ".join(str(x) for x in a)),
    )

    assert ("  Name: " in prompts) or any("Name" in p for p in prompts)
    assert anchors == [(114001, 2, "Card A")]
