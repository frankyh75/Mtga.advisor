from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Iterable

from .schema import ensure_schema, write_meta


@dataclass(frozen=True)
class ImportResult:
    source: str
    cards_inserted: int
    meta_written: dict[str, str]


def import_bulk_json(
    conn: sqlite3.Connection,
    json_path: Path,
    *,
    source: str,
    meta: dict[str, str] | None = None,
) -> ImportResult:
    ensure_schema(conn)
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Bulk file must be a JSON array.")

    if source == "oracle":
        inserted = _import_oracle(conn, payload)
    elif source == "default":
        inserted = _import_default(conn, payload)
    else:
        raise ValueError("source must be 'oracle' or 'default'")

    meta_written: dict[str, str] = {}
    if meta:
        meta_written.update(meta)
    meta_written["source"] = source
    meta_written["input"] = json_path.as_posix()
    write_meta(conn, meta_written)

    return ImportResult(source=source, cards_inserted=inserted, meta_written=meta_written)


def _import_oracle(conn: sqlite3.Connection, cards: Iterable[dict]) -> int:
    inserted = 0
    for card in cards:
        if not isinstance(card, dict):
            continue
        oracle_id = card.get("oracle_id")
        name = card.get("name")
        if not isinstance(oracle_id, str) or not isinstance(name, str):
            continue
        row = (
            oracle_id,
            name,
            card.get("mana_cost"),
            card.get("type_line"),
            card.get("oracle_text"),
            _json_or_none(card.get("keywords")),
            _json_or_none(card.get("color_identity")),
            _json_or_none(card.get("legalities")),
            _int_or_none(card.get("arena_id")),
            card.get("id"),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO cards_oracle(
                oracle_id, name, mana_cost, type_line, oracle_text,
                keywords_json, color_identity_json, legalities_json,
                arena_id, scryfall_id
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        inserted += 1
    conn.commit()
    return inserted


def _import_default(conn: sqlite3.Connection, cards: Iterable[dict]) -> int:
    inserted = 0
    for card in cards:
        if not isinstance(card, dict):
            continue
        scryfall_id = card.get("id")
        oracle_id = card.get("oracle_id")
        name = card.get("name")
        if not isinstance(scryfall_id, str) or not isinstance(oracle_id, str) or not isinstance(name, str):
            continue
        row = (
            scryfall_id,
            oracle_id,
            name,
            card.get("set"),
            card.get("collector_number"),
            card.get("lang"),
            _int_or_none(card.get("arena_id")),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO cards_printings(
                scryfall_id, oracle_id, name, set_code, collector_number, lang, arena_id
            ) VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            row,
        )
        inserted += 1
    conn.commit()
    return inserted


def _json_or_none(value: object) -> str | None:
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None
