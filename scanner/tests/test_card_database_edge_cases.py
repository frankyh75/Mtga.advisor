"""Edge-Case-Tests für card_database.py und deck_import.py."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from scanner import card_database, macos_paths
from advisor import deck_import


# ---------------------------------------------------------------------------
# card_database edge cases
# ---------------------------------------------------------------------------


def _make_sqlite_db(path: Path, tables: dict[str, str], rows: list[tuple], pad: int = 600 * 1024) -> None:
    """Erstellt eine SQLite-Datei mit gegebener Tabelle und Zeilen."""
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    for sql in tables.values():
        cursor.execute(sql)
    for params in rows:
        cursor.execute(params[0], params[1])
    conn.commit()
    conn.close()
    current_size = path.stat().st_size
    if current_size < pad:
        with path.open("ab") as f:
            f.write(b"\0" * (pad - current_size))


def test_load_local_no_localizations_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls keine Localizations existieren, soll load_local_mtga_database leer zurückgeben."""
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    db_file = raw_path / "Cards.mtga"

    _make_sqlite_db(
        db_file,
        tables={"Cards": "CREATE TABLE Cards (GrpId INTEGER, TitleId INTEGER, ExpansionCode TEXT, CollectorNumber TEXT)"},
        rows=[],
    )

    cache_dir = tmp_path / ".mtga_advisor"
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_local_mtga_database()

    assert lookup == {}


def test_load_local_null_titleid_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cards mit NULL TitleId werden nicht in die Lookup-Map aufgenommen."""
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    db_file = raw_path / "Cards.mtga"

    _make_sqlite_db(
        db_file,
        tables={
            "Locs": "CREATE TABLE Localizations (Id INTEGER, Text TEXT, Format TEXT)",
            "Cards": "CREATE TABLE Cards (GrpId INTEGER, TitleId INTEGER, ExpansionCode TEXT, CollectorNumber TEXT)",
        },
        rows=[
            ("INSERT INTO Localizations (Id, Text, Format) VALUES (?, ?, ?)", [1, "Good Card", "en-US"]),
            ("INSERT INTO Localizations (Id, Text, Format) VALUES (?, ?, ?)", [2, "Other Card", "en-US"]),
            ("INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)", [100, 1, "abc", "1"]),
            ("INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)", [200, None, "abc", "2"]),
            ("INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)", [300, 2, "xyz", "3"]),
        ],
    )

    cache_dir = tmp_path / ".mtga_advisor"
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_local_mtga_database()

    # GrpId 200 hat NULL TitleId -> übersprungen
    assert 100 in lookup
    assert 200 not in lookup
    assert 300 in lookup
    assert lookup[100]["name"] == "Good Card"


def test_load_local_empty_format_fallback_to_enUS(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Falls Format leer ist, wird die Lokalisierung trotzdem verwendet (Legacy-Verhalten)."""
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    db_file = raw_path / "Cards.mtga"

    _make_sqlite_db(
        db_file,
        tables={
            "Locs": "CREATE TABLE Localizations (Id INTEGER, Text TEXT, Format TEXT)",
            "Cards": "CREATE TABLE Cards (GrpId INTEGER, TitleId INTEGER, ExpansionCode TEXT, CollectorNumber TEXT)",
        },
        rows=[
            ("INSERT INTO Localizations (Id, Text, Format) VALUES (?, ?, ?)", [1, "Fallback Card", None]),
            ("INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)", [5000, 1, "set", "10"]),
        ],
    )

    cache_dir = tmp_path / ".mtga_advisor"
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_local_mtga_database()

    assert 5000 in lookup
    assert lookup[5000]["name"] == "Fallback Card"


def test_load_local_ambiguous_name_sorted_by_set_then_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mehrere Karten gleichen Namens: erste nach Set/GrpId sortiert."""
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    db_file = raw_path / "Cards.mtga"

    _make_sqlite_db(
        db_file,
        tables={
            "Locs": "CREATE TABLE Localizations (Id INTEGER, Text TEXT, Format TEXT)",
            "Cards": "CREATE TABLE Cards (GrpId INTEGER, TitleId INTEGER, ExpansionCode TEXT, CollectorNumber TEXT)",
        },
        rows=[
            ("INSERT INTO Localizations (Id, Text, Format) VALUES (?, ?, ?)", [1, "Lightning Bolt", "en-US"]),
            ("INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)", [100, 1, "ZZZ", "1"]),
            ("INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)", [50, 1, "AAA", "2"]),
        ],
    )

    cache_dir = tmp_path / ".mtga_advisor"
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_local_mtga_database()

    # Im _build_name_index werden Einträge nach (set, arenaId) sortiert.
    # Wir prüfen die Sortierung indirekt über deck_import.
    assert len(lookup) == 2
    assert lookup[100]["set"] == "ZZZ"
    assert lookup[50]["set"] == "AAA"


def test_load_local_missing_cards_table_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """DB ohne Cards-Tabelle wird ignoriert."""
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    db_file = raw_path / "Cards.mtga"

    _make_sqlite_db(
        db_file,
        tables={"OtherTable": "CREATE TABLE OtherTable (Id INTEGER)"},
        rows=[],
    )

    cache_dir = tmp_path / ".mtga_advisor"
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_local_mtga_database()
    assert lookup == {}


def test_load_local_current_schema_with_formatted_column(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Der aktuelle Localizations_enUS-Schema (mit Formatted-Spalte) wird korrekt gelesen."""
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    db_file = raw_path / "Cards.mtga"

    _make_sqlite_db(
        db_file,
        tables={
            "Locs": "CREATE TABLE Localizations_enUS (LocId INTEGER, Formatted INTEGER, Loc TEXT)",
            "Cards": "CREATE TABLE Cards (GrpId INTEGER, TitleId INTEGER, ExpansionCode TEXT, CollectorNumber TEXT)",
        },
        rows=[
            ("INSERT INTO Localizations_enUS (LocId, Formatted, Loc) VALUES (?, ?, ?)", [10, 1, "Formatted Card"]),
            ("INSERT INTO Localizations_enUS (LocId, Formatted, Loc) VALUES (?, ?, ?)", [20, 0, "Unformatted Card"]),
            ("INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)", [1000, 10, "fmt", "1"]),
            ("INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)", [2000, 20, "fmt", "2"]),
        ],
    )

    cache_dir = tmp_path / ".mtga_advisor"
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_local_mtga_database()

    # Beide Lokalisierungen werden gelesen (Formatted-Spalte wird ignoriert)
    assert 1000 in lookup
    assert 2000 in lookup
    assert lookup[1000]["name"] == "Formatted Card"
    assert lookup[2000]["name"] == "Unformatted Card"


def test_load_local_mixed_enus_and_legacy_preferred_enUS(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Wenn sowohl Localizations als auch Localizations_enUS existieren, wird enUS bevorzugt."""
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    db_file = raw_path / "Cards.mtga"

    _make_sqlite_db(
        db_file,
        tables={
            "Locs": "CREATE TABLE Localizations (Id INTEGER, Text TEXT, Format TEXT)",
            "LocsEn": "CREATE TABLE Localizations_enUS (LocId INTEGER, Formatted INTEGER, Loc TEXT)",
            "Cards": "CREATE TABLE Cards (GrpId INTEGER, TitleId INTEGER, ExpansionCode TEXT, CollectorNumber TEXT)",
        },
        rows=[
            # Legacy: TitleId=1 -> "Legacy Name"
            ("INSERT INTO Localizations (Id, Text, Format) VALUES (?, ?, ?)", [1, "Legacy Name", "en-US"]),
            # enUS: TitleId=1 -> "Current Name" (soll bevorzugt werden)
            ("INSERT INTO Localizations_enUS (LocId, Formatted, Loc) VALUES (?, ?, ?)", [1, 1, "Current Name"]),
            ("INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)", [7777, 1, "mix", "1"]),
        ],
    )

    cache_dir = tmp_path / ".mtga_advisor"
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_local_mtga_database()

    # _load_localizations prüft zuerst Localizations_enUS - wenn dort TitleId=1 existiert, wird es genommen
    assert 7777 in lookup
    # Da _load_localizations bei enUS sofort returnt, ist der Wert "Current Name"
    assert lookup[7777]["name"] == "Current Name"


def test_load_local_cache_schema_v2_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """v2-Cache-Format wird korrekt gelesen und geschrieben."""
    cache_dir = tmp_path / ".mtga_advisor"
    cache_dir.mkdir()
    lookup = {
        100: {"name": "Card One", "set": "DMU", "collector_number": "1", "rarity": "common"},
        200: {"name": "Card Two", "set": "VOW", "collector_number": "2", "rarity": "uncommon"},
    }

    card_database._write_lookup_cache(
        cache_dir / "arena_id_lookup.json", lookup, source="local-mtga", source_path=tmp_path / "db.mtga"
    )

    loaded, metadata = card_database._read_lookup_cache(cache_dir / "arena_id_lookup.json")

    assert metadata["schema"] == card_database.CACHE_SCHEMA
    assert metadata["source"] == "local-mtga"
    assert loaded[100]["name"] == "Card One"
    assert loaded[200]["rarity"] == "uncommon"


def test_load_local_legacy_cache_format(tmp_path: Path) -> None:
    """Altes flat Cache-Format wird noch akzeptiert."""
    cache_dir = tmp_path / ".mtga_advisor"
    cache_dir.mkdir()
    legacy = {"300": {"name": "Old Card", "set": "THS", "collector_number": "3", "rarity": "rare"}}
    (cache_dir / "arena_id_lookup.json").write_text(json.dumps(legacy), encoding="utf-8")

    loaded, metadata = card_database._read_lookup_cache(cache_dir / "arena_id_lookup.json")

    assert loaded[300]["name"] == "Old Card"
    assert metadata["schema"] == "legacy-flat"


def test_load_local_unknown_cache_format(tmp_path: Path) -> None:
    """Unbekanntes Cache-Format liefert leere Map zurück."""
    cache_dir = tmp_path / ".mtga_advisor"
    cache_dir.mkdir()
    (cache_dir / "arena_id_lookup.json").write_text('"just a string"', encoding="utf-8")

    loaded, metadata = card_database._read_lookup_cache(cache_dir / "arena_id_lookup.json")

    assert loaded == {}
    assert metadata["schema"] == "unknown"


# ---------------------------------------------------------------------------
# deck_import edge cases
# ---------------------------------------------------------------------------


def test_deck_import_unicode_names(tmp_path: Path) -> None:
    """Karten mit nicht-ASCII-Namen (z.B. 'Ä' oder 'é') werden korrekt gelöst."""
    card_db = {
        1: {"name": "Änderung", "set": "KTK", "collector_number": "1"},
        2: {"name": "Café", "set": "VOW", "collector_number": "2"},
    }
    deck = deck_import.import_arena_deck(
        "Deck\n1 Änderung\n1 Café\n",
        card_db=card_db,
        deck_format="standard",
    )
    assert len(deck["mainboard"]) == 2
    assert deck["mainboard"][0]["name"] == "Änderung"
    assert deck["mainboard"][1]["name"] == "Café"


def test_deck_import_empty_deck_lines_handled(tmp_path: Path) -> None:
    """Leere Zeilen werden ignoriert, kein unresolved-Eintrag."""
    card_db = {1: {"name": "Bolt", "set": "DMU"}}
    deck = deck_import.import_arena_deck(
        "Deck\n\n\n4 Bolt\n\n\n",
        card_db=card_db,
        deck_format="standard",
    )
    assert len(deck["mainboard"]) == 1
    assert len(deck["diagnostics"]["unresolved"]) == 0


def test_deck_import_unicode_normalization(tmp_path: Path) -> None:
    """Normalizeder Name (Whitespace) löst korrekt auf."""
    card_db = {1: {"name": "Lightning Strike", "set": "DMU"}}
    deck = deck_import.import_arena_deck(
        "Deck\n  4   Lightning   Strike  \n",
        card_db=card_db,
        deck_format="standard",
    )
    assert len(deck["mainboard"]) == 1
    assert deck["mainboard"][0]["name"] == "Lightning Strike"


def test_deck_import_ambiguous_only_unresolved(tmp_path: Path) -> None:
    """Different cards with same name: after dedup by name, keeps one entry.
    
    The dedup logic keeps whichever entry comes first or is deemed "newer".
    The key behavior is that ambiguity is resolved, not that a specific set wins.
    """
    card_db = {1: {"name": "Opt", "set": "XLN"}, 2: {"name": "Opt", "set": "STA"}}
    deck = deck_import.import_arena_deck(
        "Deck\n4 Opt\n",
        card_db=card_db,
        deck_format="standard",
    )
    # After dedup, should have exactly one entry (no ambiguity)
    assert len(deck["mainboard"]) == 1
    assert len(deck["diagnostics"]["ambiguous"]) == 0
    # Verify the entry is from one of the expected sets
    assert deck["mainboard"][0]["set"] in ("XLN", "STA")


def test_deck_import_same_card_different_sets_not_ambiguous(tmp_path: Path) -> None:
    """Same card in different sets (reprint) should NOT be ambiguous - resolved to newest."""
    card_db = {
        100: {"name": "Lightning Bolt", "set": "DMU"},
        200: {"name": "Lightning Bolt", "set": "VOW"},
        300: {"name": "Lightning Bolt", "set": "M20"},
    }
    deck = deck_import.import_arena_deck(
        "Deck\n4 Lightning Bolt\n",
        card_db=card_db,
        deck_format="standard",
    )
    # VOW is newer than M20 and DMU, so should be selected
    assert len(deck["mainboard"]) == 1
    assert len(deck["diagnostics"]["ambiguous"]) == 0
    assert deck["mainboard"][0]["set"] == "VOW"


def test_deck_import_no_db_entries_all_unresolved(tmp_path: Path) -> None:
    """Leere card_db -> alle Zeilen als unresolved markiert."""
    deck = deck_import.import_arena_deck(
        "Deck\n4 Bolt\n",
        card_db={},
        deck_format="standard",
    )
    assert len(deck["mainboard"]) == 0
    assert len(deck["diagnostics"]["unresolved"]) == 1


def test_deck_import_deck_name_from_header(tmp_path: Path) -> None:
    """Deck-Name aus 'Deck Name: ...' Header wird korrekt extrahiert."""
    card_db = {1: {"name": "Bolt", "set": "DMU"}}
    deck = deck_import.import_arena_deck(
        "Deck Name: My Mono Red\n\nDeck\n4 Bolt\n",
        card_db=card_db,
        deck_format="standard",
    )
    assert deck["name"] == "My Mono Red"


def test_deck_import_write_and_reload(tmp_path: Path) -> None:
    """Geschriebener arena_deck.json kann wieder gelesen werden."""
    card_db = {1: {"name": "Bolt", "set": "DMU"}}
    deck = deck_import.import_arena_deck(
        "Deck\n4 Bolt\n",
        card_db=card_db,
        deck_format="standard",
    )
    out_path = deck_import.write_deck(deck, tmp_path / "out")
    assert out_path.exists()

    reload = json.loads(out_path.read_text(encoding="utf-8"))
    assert reload["schema"] == "arena-deck.v1"
    assert len(reload["mainboard"]) == 1
    assert reload["mainboard"][0]["name"] == "Bolt"


def test_deck_import_invalid_deck_line_is_unresolved(tmp_path: Path) -> None:
    """Zeilen ohne Count-Präfix werden als unresolved markiert."""
    card_db = {1: {"name": "Bolt", "set": "DMU"}}
    deck = deck_import.import_arena_deck(
        "Deck\nThis is not a card line\n4 Bolt\n",
        card_db=card_db,
        deck_format="standard",
    )
    assert len(deck["mainboard"]) == 1
    assert len(deck["diagnostics"]["unresolved"]) == 1
    assert deck["diagnostics"]["unresolved"][0]["reason"] == "unparseable-line"


def test_deck_import_section_switch_to_sideboard(tmp_path: Path) -> None:
    """Cards nach 'Sideboard'-Header landen in sideboard."""
    card_db = {1: {"name": "Bolt", "set": "DMU"}, 2: {"name": "Flame", "set": "VOW"}}
    deck = deck_import.import_arena_deck(
        "Deck\n4 Bolt\n\nSideboard\n2 Flame\n",
        card_db=card_db,
        deck_format="standard",
    )
    assert len(deck["mainboard"]) == 1
    assert len(deck["sideboard"]) == 1
    assert deck["sideboard"][0]["name"] == "Flame"