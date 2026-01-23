from __future__ import annotations

import json
import sqlite3


class CardLookup:
    def __init__(self, conn: sqlite3.Connection, mapping: dict[int, dict[str, str]] | None = None) -> None:
        self._conn = conn
        self._mapping = mapping or {}

    def lookup_arena_id(self, arena_id: int) -> dict | None:
        oracle = self._query_oracle_by_arena(arena_id)
        if oracle:
            return oracle
        oracle_id = self._query_printing_oracle_id(arena_id)
        if oracle_id:
            return self._query_oracle_by_id(oracle_id)
        mapping = self._mapping.get(arena_id, {})
        oracle_id = mapping.get("oracle_id")
        if oracle_id:
            return self._query_oracle_by_id(oracle_id)
        scryfall_id = mapping.get("scryfall_id")
        if scryfall_id:
            oracle_id = self._query_printing_oracle_by_scryfall(scryfall_id)
            if oracle_id:
                return self._query_oracle_by_id(oracle_id)
        return None

    def _query_oracle_by_arena(self, arena_id: int) -> dict | None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT oracle_id, name, mana_cost, type_line, oracle_text,
                   keywords_json, color_identity_json, legalities_json, arena_id, scryfall_id
            FROM cards_oracle
            WHERE arena_id = ?
            """,
            (arena_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_card(row)

    def _query_printing_oracle_id(self, arena_id: int) -> str | None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT oracle_id
            FROM cards_printings
            WHERE arena_id = ?
            """,
            (arena_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        value = row[0]
        if isinstance(value, str):
            return value
        return None

    def _query_printing_oracle_by_scryfall(self, scryfall_id: str) -> str | None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT oracle_id
            FROM cards_printings
            WHERE scryfall_id = ?
            """,
            (scryfall_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        value = row[0]
        if isinstance(value, str):
            return value
        return None

    def _query_oracle_by_id(self, oracle_id: str) -> dict | None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            SELECT oracle_id, name, mana_cost, type_line, oracle_text,
                   keywords_json, color_identity_json, legalities_json, arena_id, scryfall_id
            FROM cards_oracle
            WHERE oracle_id = ?
            """,
            (oracle_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return _row_to_card(row)


def _row_to_card(row: tuple) -> dict:
    (
        oracle_id,
        name,
        mana_cost,
        type_line,
        oracle_text,
        keywords_json,
        color_json,
        legal_json,
        arena_id,
        scryfall_id,
    ) = row
    return {
        "oracleId": oracle_id,
        "scryfallId": scryfall_id,
        "name": name,
        "manaCost": mana_cost,
        "typeLine": type_line,
        "oracleText": oracle_text,
        "keywords": _json_load(keywords_json) or [],
        "colorIdentity": _json_load(color_json) or [],
        "legalities": _json_load(legal_json) or {},
        "arenaId": arena_id,
    }


def _json_load(value: str | None) -> object | None:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None
