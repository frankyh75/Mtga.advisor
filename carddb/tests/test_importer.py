from __future__ import annotations

from pathlib import Path

import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from carddb import import_bulk_json, open_db, read_meta  # noqa: E402


def _fixture_path(name: str) -> Path:
    return Path("fixtures", name)


def test_import_oracle_bulk(tmp_path: Path) -> None:
    db_path = tmp_path / "carddb.sqlite"
    conn = open_db(db_path)
    result = import_bulk_json(
        conn,
        _fixture_path("scryfall-oracle-mini.json"),
        source="oracle",
        meta={"bulk_date": "2026-01-23"},
    )

    assert result.cards_inserted == 2
    meta = read_meta(conn)
    assert meta["bulk_date"] == "2026-01-23"
    assert meta["source"] == "oracle"

    cursor = conn.cursor()
    cursor.execute("SELECT name, arena_id FROM cards_oracle ORDER BY arena_id")
    rows = cursor.fetchall()
    assert rows == [("Test Card One", 100001), ("Test Card Two", 100002)]


def test_import_default_bulk(tmp_path: Path) -> None:
    db_path = tmp_path / "carddb.sqlite"
    conn = open_db(db_path)
    result = import_bulk_json(
        conn,
        _fixture_path("scryfall-default-mini.json"),
        source="default",
        meta={"bulk_date": "2026-01-23"},
    )

    assert result.cards_inserted == 2
    meta = read_meta(conn)
    assert meta["bulk_date"] == "2026-01-23"
    assert meta["source"] == "default"

    cursor = conn.cursor()
    cursor.execute("SELECT name, set_code FROM cards_printings ORDER BY scryfall_id")
    rows = cursor.fetchall()
    assert rows == [("Test Card One", "tst"), ("Test Card Two", "tst")]
