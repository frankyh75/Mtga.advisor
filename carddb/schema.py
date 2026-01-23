from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Iterable


SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS cards_oracle (
        oracle_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        mana_cost TEXT,
        type_line TEXT,
        oracle_text TEXT,
        keywords_json TEXT,
        color_identity_json TEXT,
        legalities_json TEXT,
        arena_id INTEGER,
        scryfall_id TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS cards_printings (
        scryfall_id TEXT PRIMARY KEY,
        oracle_id TEXT NOT NULL,
        name TEXT NOT NULL,
        set_code TEXT,
        collector_number TEXT,
        lang TEXT,
        arena_id INTEGER
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS carddb_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cards_oracle_name ON cards_oracle(name)",
    "CREATE INDEX IF NOT EXISTS idx_cards_oracle_arena ON cards_oracle(arena_id)",
    "CREATE INDEX IF NOT EXISTS idx_cards_printings_oracle ON cards_printings(oracle_id)",
    "CREATE INDEX IF NOT EXISTS idx_cards_printings_arena ON cards_printings(arena_id)",
)


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path.as_posix())
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in SCHEMA_STATEMENTS:
        conn.execute(stmt)
    conn.commit()


def write_meta(conn: sqlite3.Connection, values: dict[str, str]) -> None:
    for key, value in values.items():
        conn.execute(
            "INSERT OR REPLACE INTO carddb_meta(key, value) VALUES(?, ?)",
            (key, value),
        )
    conn.commit()


def read_meta(conn: sqlite3.Connection, keys: Iterable[str] | None = None) -> dict[str, str]:
    cursor = conn.cursor()
    if keys is None:
        cursor.execute("SELECT key, value FROM carddb_meta")
        rows = cursor.fetchall()
    else:
        key_list = list(keys)
        if not key_list:
            return {}
        placeholders = ", ".join("?" for _ in key_list)
        cursor.execute(
            f"SELECT key, value FROM carddb_meta WHERE key IN ({placeholders})",
            key_list,
        )
        rows = cursor.fetchall()
    return {row[0]: row[1] for row in rows}
