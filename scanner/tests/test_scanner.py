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

    assert validation["valid"] is False
    assert validation["errors"] == ["anchor-mismatch", "invalid-quantities"]
    assert validation["warnings"] == ["unknown-card-ids"]
    assert validation["anchors"][0]["ok"] is True
    assert validation["anchors"][1]["ok"] is False
    assert validation["unknownCardIdsCount"] == 1
    assert validation["unknownCardIdsWithCounts"] == {"300": 401}


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
