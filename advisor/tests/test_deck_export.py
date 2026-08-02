"""Tests für advisor.deck_export — Arena-Text-Export und Deck-Suche."""

from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from advisor.deck_export import (  # noqa: E402
    ARENA_DECK_SCHEMA,
    DECK_V1_SCHEMA,
    DeckExportError,
    DeckNotFoundError,
    export_deck_to_arena_text,
    load_deck_by_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _arena_v1_deck(
    *,
    deck_id: str = "abc123",
    name: str = "Test Deck",
    mainboard: list[dict] | None = None,
    sideboard: list[dict] | None = None,
) -> dict:
    return {
        "schema": ARENA_DECK_SCHEMA,
        "deckId": deck_id,
        "name": name,
        "format": "standard",
        "mainboard": mainboard if mainboard is not None else [
            {"arenaId": 67984, "name": "Lightning Strike", "count": 4},
            {"arenaId": 12345, "name": "Shock", "count": 2},
        ],
        "sideboard": sideboard if sideboard is not None else [
            {"arenaId": 99999, "name": "Duress", "count": 3},
        ],
    }


def _deck_v1_payload(
    *,
    deck_id: str = "mem-deck-1",
    name: str = "Memory Deck",
    mainboard: list[dict] | None = None,
    sideboard: list[dict] | None = None,
) -> dict:
    return {
        "schema": DECK_V1_SCHEMA,
        "deckId": deck_id,
        "name": name,
        "source": "il2cpp",
        "cards": {
            "mainboard": mainboard if mainboard is not None else [
                {"cardId": 67984, "name": "Lightning Strike", "count": 4},
                {"cardId": 54321, "name": "Opt", "count": 2},
            ],
            "sideboard": sideboard if sideboard is not None else [
                {"cardId": 99999, "name": "Duress", "count": 3},
            ],
        },
        "cardsById": {},
    }


# ---------------------------------------------------------------------------
# export_deck_to_arena_text — arena-deck.v1
# ---------------------------------------------------------------------------

class TestExportArenaV1:
    def test_basic_mainboard_and_sideboard(self):
        deck = _arena_v1_deck()
        text = export_deck_to_arena_text(deck)
        lines = text.rstrip("\n").split("\n")
        assert lines == ["4 Lightning Strike", "2 Shock", "", "3 Duress"]

    def test_no_sideboard(self):
        deck = _arena_v1_deck(sideboard=[])
        text = export_deck_to_arena_text(deck)
        lines = text.rstrip("\n").split("\n")
        assert lines == ["4 Lightning Strike", "2 Shock"]
        # Keine Leerzeile am Ende wenn kein Sideboard
        assert "" not in lines

    def test_empty_deck(self):
        deck = _arena_v1_deck(mainboard=[], sideboard=[])
        text = export_deck_to_arena_text(deck)
        assert text == "\n"

    def test_trailing_newline(self):
        deck = _arena_v1_deck()
        text = export_deck_to_arena_text(deck)
        assert text.endswith("\n")

    def test_cards_with_count_zero_skipped(self):
        deck = _arena_v1_deck(
            mainboard=[
                {"arenaId": 1, "name": "Lightning Strike", "count": 4},
                {"arenaId": 2, "name": "Shock", "count": 0},
            ],
            sideboard=[],
        )
        text = export_deck_to_arena_text(deck)
        lines = text.rstrip("\n").split("\n")
        assert lines == ["4 Lightning Strike"]

    def test_missing_name_uses_arena_id(self):
        deck = _arena_v1_deck(
            mainboard=[{"arenaId": 77777, "count": 2}],
            sideboard=[],
        )
        text = export_deck_to_arena_text(deck)
        assert "2 ID:77777" in text


# ---------------------------------------------------------------------------
# export_deck_to_arena_text — deck.v1
# ---------------------------------------------------------------------------

class TestExportDeckV1:
    def test_basic_mainboard_and_sideboard(self):
        deck = _deck_v1_payload()
        text = export_deck_to_arena_text(deck)
        lines = text.rstrip("\n").split("\n")
        assert lines == ["4 Lightning Strike", "2 Opt", "", "3 Duress"]

    def test_no_sideboard(self):
        deck = _deck_v1_payload(sideboard=[])
        text = export_deck_to_arena_text(deck)
        lines = text.rstrip("\n").split("\n")
        assert lines == ["4 Lightning Strike", "2 Opt"]

    def test_empty_cards(self):
        deck = _deck_v1_payload(mainboard=[], sideboard=[])
        text = export_deck_to_arena_text(deck)
        assert text == "\n"

    def test_missing_name_uses_card_id(self):
        deck = _deck_v1_payload(
            mainboard=[{"cardId": 88888, "count": 3}],
            sideboard=[],
        )
        text = export_deck_to_arena_text(deck)
        assert "3 ID:88888" in text


# ---------------------------------------------------------------------------
# export_deck_to_arena_text — Error handling
# ---------------------------------------------------------------------------

class TestExportErrors:
    def test_unknown_schema_raises(self):
        deck = {"schema": "unknown", "deckId": "x", "mainboard": []}
        with pytest.raises(DeckExportError, match="Unbekanntes Deck-Schema"):
            export_deck_to_arena_text(deck)

    def test_no_schema_raises(self):
        deck = {"deckId": "x", "mainboard": []}
        with pytest.raises(DeckExportError, match="Unbekanntes Deck-Schema"):
            export_deck_to_arena_text(deck)


# ---------------------------------------------------------------------------
# load_deck_by_id — Search logic
# ---------------------------------------------------------------------------

class TestLoadDeckById:
    def test_find_in_arena_deck_json(self, tmp_path):
        deck = _arena_v1_deck(deck_id="my-id")
        (tmp_path / "arena_deck.json").write_text(
            json.dumps(deck), encoding="utf-8"
        )
        result = load_deck_by_id("my-id", search_dirs=[tmp_path])
        assert result["deckId"] == "my-id"

    def test_find_in_decks_subdir(self, tmp_path):
        deck = _deck_v1_payload(deck_id="mem-1")
        decks_dir = tmp_path / "decks"
        decks_dir.mkdir()
        (decks_dir / "deck-mem-1.json").write_text(
            json.dumps(deck), encoding="utf-8"
        )
        result = load_deck_by_id("mem-1", search_dirs=[tmp_path])
        assert result["deckId"] == "mem-1"

    def test_find_in_decks_json_container(self, tmp_path):
        deck_entry = {
            "schema": DECK_V1_SCHEMA,
            "deckId": "container-1",
            "name": "Container Deck",
            "cards": {
                "mainboard": [{"cardId": 1, "name": "Shock", "count": 4}],
                "sideboard": [],
            },
        }
        container = {"schema": "decks.v1", "decks": [deck_entry]}
        (tmp_path / "decks.json").write_text(
            json.dumps(container), encoding="utf-8"
        )
        result = load_deck_by_id("container-1", search_dirs=[tmp_path])
        assert result["deckId"] == "container-1"

    def test_not_found_raises(self, tmp_path):
        with pytest.raises(DeckNotFoundError, match="nicht gefunden"):
            load_deck_by_id("nonexistent", search_dirs=[tmp_path])

    def test_search_multiple_dirs(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        # Deck in dir_b
        deck = _arena_v1_deck(deck_id="in-b")
        (dir_b / "arena_deck.json").write_text(
            json.dumps(deck), encoding="utf-8"
        )
        result = load_deck_by_id("in-b", search_dirs=[dir_a, dir_b])
        assert result["deckId"] == "in-b"

    def test_skip_malformed_json(self, tmp_path):
        # Schreibe kaputtes JSON, gefolgt von gültigem
        (tmp_path / "arena_deck.json").write_text("{not json", encoding="utf-8")
        decks_dir = tmp_path / "decks"
        decks_dir.mkdir()
        deck = _deck_v1_payload(deck_id="good")
        (decks_dir / "deck-good.json").write_text(
            json.dumps(deck), encoding="utf-8"
        )
        result = load_deck_by_id("good", search_dirs=[tmp_path])
        assert result["deckId"] == "good"

    def test_id_as_integer_string(self, tmp_path):
        """Deck-IDs aus Memory-Scan sind oft numerisch."""
        deck = _deck_v1_payload(deck_id="123456")
        decks_dir = tmp_path / "decks"
        decks_dir.mkdir()
        (decks_dir / "deck-123456.json").write_text(
            json.dumps(deck), encoding="utf-8"
        )
        result = load_deck_by_id("123456", search_dirs=[tmp_path])
        assert result["deckId"] == "123456"


# ---------------------------------------------------------------------------
# CLI Integration: `deck export <id>`
# ---------------------------------------------------------------------------

class TestCliDeckExport:
    def test_cli_export_to_stdout(self, tmp_path, capsys):
        from cli.main import main

        deck = _arena_v1_deck(deck_id="cli-test")
        (tmp_path / "arena_deck.json").write_text(
            json.dumps(deck), encoding="utf-8"
        )
        exit_code = main([
            "deck", "export", "cli-test",
            "--search-dir", str(tmp_path),
        ])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "4 Lightning Strike" in captured.out
        assert "2 Shock" in captured.out
        assert "3 Duress" in captured.out
        assert "\n\n" in captured.out  # Leerzeile zwischen Main/Side

    def test_cli_export_to_file(self, tmp_path, capsys):
        from cli.main import main

        deck = _deck_v1_payload(deck_id="file-test")
        decks_dir = tmp_path / "decks"
        decks_dir.mkdir()
        (decks_dir / "deck-file-test.json").write_text(
            json.dumps(deck), encoding="utf-8"
        )
        out_file = tmp_path / "export.txt"
        exit_code = main([
            "deck", "export", "file-test",
            "--search-dir", str(tmp_path),
            "--output", str(out_file),
        ])
        assert exit_code == 0
        content = out_file.read_text(encoding="utf-8")
        assert "4 Lightning Strike" in content
        assert "2 Opt" in content
        assert "3 Duress" in content

    def test_cli_export_not_found(self, tmp_path, capsys):
        from cli.main import main

        exit_code = main([
            "deck", "export", "nonexistent",
            "--search-dir", str(tmp_path),
        ])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "nicht gefunden" in captured.err

    def test_cli_export_unknown_schema(self, tmp_path, capsys):
        from cli.main import main

        bad_deck = {"schema": "garbage", "deckId": "bad"}
        (tmp_path / "arena_deck.json").write_text(
            json.dumps(bad_deck), encoding="utf-8"
        )
        exit_code = main([
            "deck", "export", "bad",
            "--search-dir", str(tmp_path),
        ])
        assert exit_code == 1
        captured = capsys.readouterr()
        assert "Unbekanntes Deck-Schema" in captured.err

    def test_cli_help_shows_export(self, capsys):
        from cli.main import main

        with pytest.raises(SystemExit):
            main(["deck", "--help"])
        captured = capsys.readouterr()
        assert "export" in captured.out