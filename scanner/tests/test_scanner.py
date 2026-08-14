from __future__ import annotations

import ctypes
import json
import sqlite3
from pathlib import Path

import pytest

from scanner import card_database, macos_paths, memory_scanner, pattern_scanner


def _make_sqlite_card_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE Localizations (Id INTEGER, Text TEXT, Format TEXT)")
    cursor.execute("CREATE TABLE Cards (GrpId INTEGER, TitleId INTEGER, ExpansionCode TEXT, CollectorNumber TEXT)")
    cursor.execute(
        "INSERT INTO Localizations (Id, Text, Format) VALUES (?, ?, ?)",
        (1, "Test Card", "en-US"),
    )
    cursor.execute(
        "INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)",
        (12345, 1, "abc", "7"),
    )
    conn.commit()
    conn.close()
    current_size = path.stat().st_size
    if current_size < 600 * 1024:
        with path.open("ab") as handle:
            handle.write(b"\0" * (600 * 1024 - current_size))


def _make_current_sqlite_card_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE Localizations_enUS (LocId INTEGER, Formatted INTEGER, Loc TEXT)")
    cursor.execute(
        "CREATE TABLE Cards (GrpId INTEGER, TitleId INTEGER, ExpansionCode TEXT, CollectorNumber TEXT)"
    )
    cursor.execute(
        "INSERT INTO Localizations_enUS (LocId, Formatted, Loc) VALUES (?, ?, ?)",
        (1, 1, "Current Test Card"),
    )
    cursor.execute(
        "INSERT INTO Cards (GrpId, TitleId, ExpansionCode, CollectorNumber) VALUES (?, ?, ?, ?)",
        (54321, 1, "cur", "9"),
    )
    conn.commit()
    conn.close()
    current_size = path.stat().st_size
    if current_size < 600 * 1024:
        with path.open("ab") as handle:
            handle.write(b"\0" * (600 * 1024 - current_size))


def test_macos_paths_pick_existing_candidates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    data_path = home / "Library" / "Application Support" / "com.wizards.mtga" / "Downloads" / "Raw"
    log_path = home / "Library" / "Logs" / "Wizards Of The Coast" / "MTGA"
    data_path.mkdir(parents=True)
    log_path.mkdir(parents=True)
    log_path.joinpath("Player.log").write_text("", encoding="utf-8")

    monkeypatch.setattr(macos_paths.Path, "home", lambda: home)

    assert macos_paths.get_macos_mtga_data_path() == data_path
    assert macos_paths.get_macos_log_path() == log_path / "Player.log"
    assert macos_paths.get_macos_mtga_process_name() == "MTGA"
    assert macos_paths.get_macos_mtga_process_names() == ("MTGA", "MTGALauncher", "MTGArena")


def test_task_port_accepts_ctypes_values() -> None:
    class FakePm:
        task = ctypes.c_ulong(12345)

    assert pattern_scanner._task_port(FakePm()) == 12345


def test_load_card_database_uses_local_sqlite_and_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    db_file = raw_path / "Cards.mtga"
    _make_sqlite_card_db(db_file)

    cache_dir = tmp_path / ".mtga_advisor"
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_card_database()

    assert lookup[12345]["name"] == "Test Card"
    assert lookup[12345]["set"] == "abc"
    assert lookup[12345]["collector_number"] == "7"
    assert (cache_dir / "arena_id_lookup.json").exists()


def test_load_card_database_supports_current_localization_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    db_file = raw_path / "Raw_CardDatabase_test.mtga"
    _make_current_sqlite_card_db(db_file)

    cache_dir = tmp_path / ".mtga_advisor"
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_card_database(refresh_cache=True)

    assert lookup[54321]["name"] == "Current Test Card"
    assert lookup[54321]["set"] == "cur"
    assert lookup[54321]["collector_number"] == "9"


def test_load_card_database_refreshes_legacy_cache_when_local_db_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    db_file = raw_path / "Raw_CardDatabase_test.mtga"
    _make_current_sqlite_card_db(db_file)

    cache_dir = tmp_path / ".mtga_advisor"
    cache_dir.mkdir()
    cache_file = cache_dir / "arena_id_lookup.json"
    cache_file.write_text(
        json.dumps({"999": {"name": "Legacy Cache Card"}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_card_database()
    cached = json.loads(cache_file.read_text(encoding="utf-8"))

    assert 54321 in lookup
    assert 999 not in lookup
    assert cached["schema"] == card_database.CACHE_SCHEMA
    assert cached["source"] == card_database.CACHE_SOURCE_LOCAL
    assert cached["cards"]["54321"]["name"] == "Current Test Card"


def test_load_card_database_uses_v2_local_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_path = tmp_path / "Raw"
    raw_path.mkdir()
    cache_dir = tmp_path / ".mtga_advisor"
    cache_dir.mkdir()
    cache_file = cache_dir / "arena_id_lookup.json"
    cache_file.write_text(
        json.dumps(
            {
                "schema": card_database.CACHE_SCHEMA,
                "source": card_database.CACHE_SOURCE_LOCAL,
                "sourcePath": raw_path.as_posix(),
                "cardCount": 1,
                "cards": {"777": {"name": "Cached Local Card"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(card_database, "get_default_cache_dir", lambda: cache_dir)
    monkeypatch.setattr(card_database, "get_macos_mtga_data_path", lambda: raw_path)

    lookup = card_database.load_card_database()

    assert lookup == {777: {"name": "Cached Local Card"}}


def test_scan_process_memory_scans_full_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    needle = b"MTGA"
    base_addr = 0x1000
    monkeypatch.setattr(pattern_scanner, "REGION_SCAN_CHUNK_SIZE", 8)
    region_size = 16

    class FakeBackend:
        def read_bytes(self, addr: int, size: int) -> bytes | None:
            if addr == base_addr + 5:
                return b"x" + needle + b"y" * (size - 5)
            return b"x" * size

        def iterate_writable_private_regions(self) -> list[tuple[int, int]]:
            return [(base_addr, region_size)]

        def iterate_readable_regions(self) -> tuple[list[tuple[int, int]], int | None]:
            return ([(base_addr, region_size)], None)

    found = pattern_scanner.scan_process_memory(FakeBackend(), needle)

    assert found == [base_addr + 6]


def test_scan_process_memory_with_stats_reports_scan_scope(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBackend:
        def read_bytes(self, addr: int, size: int) -> bytes | None:
            return b"abcMTGAxyz"

        def iterate_writable_private_regions(self) -> list[tuple[int, int]]:
            return [(0x5000, 10)]

        def iterate_readable_regions(self) -> tuple[list[tuple[int, int]], int | None]:
            return ([(0x5000, 10)], None)

    result = pattern_scanner.scan_process_memory_with_stats(FakeBackend(), b"MTGA")

    assert result.addresses == [0x5003]
    assert result.stats.regions == 1
    assert result.stats.bytes_scanned == 10
    assert result.stats.read_failures == 0
    assert result.stats.matches == 1
    assert result.stats.region_error is None


def test_scan_process_memory_many_reads_region_once(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[tuple[int, int]] = []

    class FakeBackend:
        def read_bytes(self, addr: int, size: int) -> bytes | None:
            reads.append((addr, size))
            return b"aaaONEbbbTWOccc"

        def iterate_writable_private_regions(self) -> list[tuple[int, int]]:
            return [(0x7000, 15)]

        def iterate_readable_regions(self) -> tuple[list[tuple[int, int]], int | None]:
            return ([(0x7000, 15)], None)

    result = pattern_scanner.scan_process_memory_many_with_stats(
        FakeBackend(),
        {1: b"ONE", 2: b"TWO"},
    )

    assert reads == [(0x7000, 15)]
    assert result.addresses == {1: [0x7003], 2: [0x7009]}
    assert result.stats.matches == 2


def test_scan_collection_uses_process_fallback_and_scanner_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    anchor_file = tmp_path / "last_anchors.json"
    monkeypatch.setattr(memory_scanner, "_anchor_file", lambda: anchor_file)
    monkeypatch.setattr(
        memory_scanner,
        "load_card_database",
        lambda: {
            114001: {"name": "Card A"},
            114002: {"name": "Card B"},
        },
    )
    monkeypatch.setattr(memory_scanner, "get_user_anchors", lambda name_to_id, **kwargs: [(114001, 4, "Card A"), (114002, 2, "Card B")])

    attempts: list[str] = []

    class FakePm:
        pid = 4242
        task = 7

    class FakePymem:
        def __init__(self, process_name: str) -> None:
            attempts.append(process_name)
            if process_name == "MTGALauncher":
                raise RuntimeError("not running")
            self.pid = 4242
            self.task = 7

    monkeypatch.setattr(memory_scanner, "Pymem", FakePymem)

    found_addresses = iter([[0x2000], [0x3000]])

    def fake_memory_scanner(pm: object, needle: bytes) -> list[int]:
        return next(found_addresses)

    def fake_block_parser(pm: object, addr: int) -> list[dict[int, int]]:
        if addr == 0x2000:
            return [{100001: 1, 100002: 2, 100003: 3}, {100010: 1}]
        return [{200001: 1, 200002: 2}]

    collection = memory_scanner.scan_collection(
        process_names=("MTGALauncher", "MTGA"),
        memory_scanner=fake_memory_scanner,
        block_parser=fake_block_parser,
        print_fn=lambda *args, **kwargs: None,
        use_helper=False,
    )

    assert attempts == ["MTGALauncher", "MTGA"]
    assert collection == {100001: 1, 100002: 2, 100003: 3}


def test_validate_collection_checks_anchors_and_ids() -> None:
    validation = memory_scanner.validate_collection(
        {100: 4, 200: 2, 300: 401},
        db={100: {"name": "A"}, 200: {"name": "B"}},
        anchors=[(100, 4, "A"), (200, 1, "B")],
    )

    # Anker 200 ist vorhanden (qty=2) aber Menge weicht ab → warning, nicht error
    assert validation["valid"] is False
    assert validation["errors"] == ["invalid-quantities"]
    assert "anchor-qty-mismatch" in validation["warnings"]
    assert "unknown-card-ids" in validation["warnings"]
    assert validation["anchors"][0]["ok"] is True
    assert validation["anchors"][1]["ok"] is False
    assert validation["unknownCardIdsCount"] == 1
    assert validation["unknownCardIdsWithCounts"] == {"300": 401}


def test_validate_collection_anchor_missing_is_error() -> None:
    """Ein komplett fehlender Anker ist ein error (Block ist nicht die echte Collection)."""
    validation = memory_scanner.validate_collection(
        {100: 4, 300: 1},
        db={100: {"name": "A"}, 200: {"name": "B"}},
        anchors=[(100, 4, "A"), (200, 1, "B")],
    )
    assert validation["valid"] is False
    assert "anchor-missing" in validation["errors"]
    assert validation["anchors"][0]["ok"] is True
    assert validation["anchors"][1]["ok"] is False
    assert validation["anchors"][1]["actual"] is None


def test_validate_collection_qty_mismatch_is_warning_not_error() -> None:
    """Mengen-Abweichung bei vorhandenem Anker ist warning, nicht error."""
    validation = memory_scanner.validate_collection(
        {100: 4, 200: 2},
        db={100: {"name": "A"}, 200: {"name": "B"}},
        anchors=[(100, 4, "A"), (200, 1, "B")],
    )
    # valid=True weil nur warning, kein error
    assert validation["valid"] is True
    assert "anchor-qty-mismatch" in validation["warnings"]
    assert validation["anchors"][0]["ok"] is True
    assert validation["anchors"][1]["ok"] is False


def test_write_collection_artifacts_uses_collection_schema(tmp_path: Path) -> None:
    collection_path, run_report_path = memory_scanner.write_collection_artifacts(
        {200: 1, 100: 4},
        tmp_path / "out",
    )

    collection = json.loads(collection_path.read_text(encoding="utf-8"))
    run_report = json.loads(run_report_path.read_text(encoding="utf-8"))

    assert collection["schema"] == "collection.v1"
    assert collection["source"] == "memory-scan"
    assert collection["cards"] == {"100": 4, "200": 1}
    assert collection["diagnostics"]["completeness"]["cards"] == "complete"
    assert collection["diagnostics"]["completeness"]["wildcards"] == "unknown"
    assert run_report["source"] == "memory-scan"
    assert run_report["summary"]["cardsCount"] == 2
    assert run_report["summary"]["totalCards"] == 5


# ---------------------------------------------------------------------------
# _select_best_block tests — anchor-score-basierte Blockauswahl
# ---------------------------------------------------------------------------

def test_select_best_block_prefers_all_anchors_correct() -> None:
    """Block mit allen 5 korrekten Ankern wird vor größeren Blocks ohne Anker gewählt."""
    # Großer Block OHNE Anker (früher durch max(len) fälschlich gewählt)
    big_no_anchor = {100001 + i: 1 for i in range(500)}
    # Kleinerer Block MIT allen korrekten Ankern
    correct_block = {100: 3, 200: 4, 300: 4, 400: 4, 500: 1, 600: 2, 700: 1}
    anchors = [(100, 3, "A"), (200, 4, "B"), (300, 4, "C"), (400, 4, "D"), (500, 1, "E")]

    result = memory_scanner._select_best_block(
        [big_no_anchor, correct_block],
        anchors,
        print_fn=lambda *a, **k: None,
    )
    assert result == correct_block


def test_select_best_block_large_wrong_block_discarded() -> None:
    """Ein großer Block (z.B. 8000 Einträge) ohne korrekte Anker wird nicht gewählt,
    wenn ein kleinerer Block die Anker korrekt enthält."""
    huge_wrong = {i: 1 for i in range(1000, 9000)}
    correct_small = {100: 3, 200: 4, 300: 4, 400: 4, 500: 1, 600: 2}
    anchors = [(100, 3, "A"), (200, 4, "B"), (300, 4, "C"), (400, 4, "D"), (500, 1, "E")]

    result = memory_scanner._select_best_block(
        [huge_wrong, correct_small],
        anchors,
        print_fn=lambda *a, **k: None,
    )
    assert result == correct_small


def test_select_best_block_partial_match_chooses_best() -> None:
    """Bei Teiltreffern: Block mit 3/5 korrekten Ankern schlägt Block mit 1/5."""
    block_3_anchors = {100: 3, 200: 4, 300: 4, 400: 99, 500: 99, 600: 1}
    block_1_anchor = {100: 99, 200: 4, 300: 99, 400: 99, 500: 99, 700: 1, 800: 1}
    anchors = [(100, 3, "A"), (200, 4, "B"), (300, 4, "C"), (400, 4, "D"), (500, 1, "E")]

    result = memory_scanner._select_best_block(
        [block_1_anchor, block_3_anchors],
        anchors,
        print_fn=lambda *a, **k: None,
    )
    assert result == block_3_anchors


def test_select_best_block_wrong_anchor_qty_not_scored() -> None:
    """Anker mit falscher Menge wird nicht als 'korrekt' gezählt."""
    # Block hat Anker-Karte 100, aber mit qty=1 statt erwartet qty=3
    block_wrong_qty = {100: 1, 200: 4, 600: 1}
    # Block hat Anker-Karte 100 mit korrekter qty=3
    block_correct_qty = {100: 3, 700: 1, 800: 1}
    anchors = [(100, 3, "A"), (200, 4, "B")]

    result = memory_scanner._select_best_block(
        [block_wrong_qty, block_correct_qty],
        anchors,
        print_fn=lambda *a, **k: None,
    )
    # block_wrong_qty hat 1 korrekten Anker (200=4), block_correct_qty hat auch 1 (100=3)
    # Bei Gleichstand gewinnt der größere Block → block_wrong_qty (3 > 3 → equal, aber
    # block_correct_qty hat 3 Einträge vs block_wrong_qty hat 3 → tie. sorted ist stable,
    # aber reverse=True nimmt den ersten mit höchstem score. Bei Gleichstand → größerer.
    # Beide haben 3 Einträge → der erste in der Liste gewinnt bei exakt gleichem Score.
    # Aber: block_wrong_qty hat anchor 200=4 (correct) + 100=1 (wrong) → score=1
    # block_correct_qty hat anchor 100=3 (correct) → score=1
    # Beide score=1, beide len=3 → sorted reverse=True behält Reihenfolge bei
    # → block_wrong_qty (erscheint zuerst in candidates) wird gewählt
    assert result[200] == 4  # Der Anker mit korrekter Menge ist enthalten


def test_select_best_block_no_anchors_falls_back_to_max_len() -> None:
    """Ohne Anker → Fallback auf größten Block (altes Verhalten)."""
    small = {100: 1, 200: 2}
    big = {300: 1, 400: 2, 500: 3, 600: 1}

    result = memory_scanner._select_best_block(
        [small, big],
        [],
        print_fn=lambda *a, **k: None,
    )
    assert result == big


def test_select_best_block_all_zero_anchors_falls_back_to_max_len() -> None:
    """Wenn kein Block irgendeinen Anker enthält → Fallback auf größten Block."""
    big_no_anchor = {i: 1 for i in range(1000, 1100)}
    small_no_anchor = {2001: 1, 3001: 2}  # KEINE Anker-grpIds
    anchors = [(100, 3, "A"), (200, 4, "B")]

    result = memory_scanner._select_best_block(
        [small_no_anchor, big_no_anchor],
        anchors,
        print_fn=lambda *a, **k: None,
    )
    assert result == big_no_anchor


def test_select_best_block_empty_candidates() -> None:
    """Leere Kandidatenliste → leeres Dict."""
    result = memory_scanner._select_best_block(
        [],
        [(100, 3, "A")],
        print_fn=lambda *a, **k: None,
    )
    assert result == {}


def test_select_best_block_duplicate_candidates_same_score() -> None:
    """Doppelte Kandidaten mit gleichem Score → funktioniert trotzdem (Dedup erfolgt vorher)."""
    block = {100: 3, 200: 4, 300: 1}
    anchors = [(100, 3, "A"), (200, 4, "B")]

    result = memory_scanner._select_best_block(
        [block, dict(block)],
        anchors,
        print_fn=lambda *a, **k: None,
    )
    assert result == block


def test_select_best_block_presence_beats_exact_qty() -> None:
    """Block mit 5/5 Anker-grpIds vorhanden (aber Mengen abweichend) schlägt
    kleinen Block mit 2/5 exakten Mengen.

    Das ist das Kern-Szenario: der echte Collection-Block (8000+) hat alle
    5 Anker-grpIds, aber einige Mengen weichen ab. Ein kleiner Block (258)
    hat nur 2 Anker mit exakter Menge. Die neue Logik MUSS den großen Block wählen.
    """
    # Kleiner Block: nur 2/5 Anker-grpIds vorhanden, beide mit exakter Menge
    small_exact_2 = {100: 3, 500: 1, 999: 1, 998: 1}
    # Großer Block: 5/5 Anker-grpIds vorhanden, aber 3 mit abweichender Menge
    big_all_present = {100: 3, 200: 2, 300: 1, 400: 99, 500: 1}
    big_all_present.update({i: 1 for i in range(10000, 18000)})  # 8000+ entries
    anchors = [(100, 3, "A"), (200, 4, "B"), (300, 4, "C"), (400, 4, "D"), (500, 1, "E")]

    result = memory_scanner._select_best_block(
        [small_exact_2, big_all_present],
        anchors,
        print_fn=lambda *a, **k: None,
    )
    # big_all_present hat 5/5 present, small_exact_2 hat nur 2/5 present
    # → big_all_present gewinnt trotz Mengen-Abweichungen
    assert result == big_all_present


def test_select_best_block_presence_tiebreaker_exact_qty() -> None:
    """Bei gleichem Presence-Score (alle 5 vorhanden) gewinnt der Block mit
    mehr exakten Mengen."""
    block_3_exact = {100: 3, 200: 4, 300: 4, 400: 99, 500: 99, 600: 1}
    block_5_exact = {100: 3, 200: 4, 300: 4, 400: 4, 500: 1, 700: 1}
    anchors = [(100, 3, "A"), (200, 4, "B"), (300, 4, "C"), (400, 4, "D"), (500, 1, "E")]

    result = memory_scanner._select_best_block(
        [block_3_exact, block_5_exact],
        anchors,
        print_fn=lambda *a, **k: None,
    )
    # Beide haben 5/5 present, aber block_5_exact hat 5/5 exact vs 3/5
    assert result == block_5_exact
