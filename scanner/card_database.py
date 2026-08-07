"""Karten-Datenbank: lokale .mtga SQLite + Scryfall-Fallback."""

from __future__ import annotations

import json
import sqlite3
import gzip
from pathlib import Path
from typing import Any

from .macos_paths import get_default_cache_dir, get_macos_mtga_data_path

CACHE_SCHEMA = "arena-id-lookup.v2"
CACHE_SOURCE_LOCAL = "local-mtga"
CACHE_SOURCE_SCRYFALL = "scryfall"


def _lookup_file() -> Path:
    return get_default_cache_dir() / "arena_id_lookup.json"


def load_local_mtga_database() -> dict[int, dict[str, Any]]:
    """Scannt lokale .mtga SQLite-Dateien nach Kartendefinitionen.

    MTGA speichert Karten-Daten in .mtga-Dateien (SQLite) unter
    MTGA_Data/Downloads/Raw/. Die grösste Datei enthält die Cards-Tabelle.
    """
    raw_path = get_macos_mtga_data_path()
    if not raw_path:
        print("⚠ Lokale MTGA-Installation nicht gefunden.")
        return {}

    print(f"📂 Scanne lokale MTGA-Dateien: {raw_path}")
    lookup: dict[int, dict[str, Any]] = {}

    all_files = sorted(
        list(raw_path.glob("*.mtga")),
        key=lambda f: f.stat().st_size,
        reverse=True,
    )

    for f in all_files:
        if f.stat().st_size < 500 * 1024:
            continue  # zu klein, enthält keine Cards-Tabelle

        try:
            conn = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
            cursor = conn.cursor()

            tables = {row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}

            if "Cards" not in tables or not ({"Localizations", "Localizations_enUS"} & tables):
                conn.close()
                continue

            # Lokalisierung laden (bevorzugt en-US). MTGA nutzt je nach Version
            # entweder Localizations(Id, Text, Format) oder Localizations_enUS(LocId, Loc).
            loc_map = _load_localizations(cursor, tables)

            # Spalten prüfen
            cols = [row[1] for row in cursor.execute("PRAGMA table_info(Cards)")]
            has_set = "ExpansionCode" in cols
            has_cn = "CollectorNumber" in cols
            has_rarity = "Rarity" in cols

            query = (
                f"SELECT GrpId, TitleId, "
                f"{'ExpansionCode' if has_set else 'NULL'}, "
                f"{'CollectorNumber' if has_cn else 'NULL'}, "
                f"{'Rarity' if has_rarity else 'NULL'} "
                f"FROM Cards"
            )
            cursor.execute(query)
            rows = cursor.fetchall()

            for row in rows:
                grp_id = row[0]
                title_id = row[1]
                set_code = row[2] if row[2] else ""
                cn = str(row[3]) if row[3] else ""
                rarity = _rarity_label(row[4]) if has_rarity else "unknown"

                if title_id in loc_map:
                    lookup[grp_id] = {
                        "name": loc_map[title_id],
                        "set": set_code,
                        "collector_number": cn,
                        "rarity": rarity,
                    }

            conn.close()

            if len(lookup) > 1000:
                print(f"✅ {len(lookup)} Karten lokal geladen (aus {f.name})")
                return lookup

        except (sqlite3.Error, Exception):
            continue

    print(f"📊 {len(lookup)} Karten lokal geladen")
    return lookup


def _load_localizations(cursor: sqlite3.Cursor, tables: set[str]) -> dict[int, str]:
    """Lädt englische Karten-Titel aus bekannten MTGA-Lokalisierungsschemas."""
    loc_map: dict[int, str] = {}
    if "Localizations_enUS" in tables:
        cols = {row[1] for row in cursor.execute("PRAGMA table_info(Localizations_enUS)")}
        if {"LocId", "Loc"}.issubset(cols):
            cursor.execute("SELECT LocId, Loc FROM Localizations_enUS")
            for lid, text in cursor.fetchall():
                if text:
                    loc_map[int(lid)] = str(text)
            return loc_map

    if "Localizations" in tables:
        cols = {row[1] for row in cursor.execute("PRAGMA table_info(Localizations)")}
        if {"Id", "Text", "Format"}.issubset(cols):
            cursor.execute("SELECT Id, Text FROM Localizations WHERE Format LIKE '%en-US%' OR Format IS NULL")
            for lid, text in cursor.fetchall():
                if text:
                    loc_map[int(lid)] = str(text)
            return loc_map
        if {"Id", "Text"}.issubset(cols):
            cursor.execute("SELECT Id, Text FROM Localizations")
            for lid, text in cursor.fetchall():
                if text:
                    loc_map[int(lid)] = str(text)
    return loc_map


def fetch_scryfall_database() -> dict[int, dict[str, Any]]:
    """Lädt Kartendaten von der Scryfall-API (Bulk-Download)."""
    try:
        import requests
    except ImportError:
        print("❌ requests ist nicht installiert. Scryfall-Fallback nicht verfügbar.")
        return {}

    print("🌐 Lade Kartendaten von Scryfall API...")
    try:
        headers = {
            "Accept": "application/json",
            "User-Agent": "Mtga.advisor/0.1",
        }
        bulk_response = requests.get(
            "https://api.scryfall.com/bulk-data/default-cards",
            timeout=30,
            headers=headers,
        )
        bulk_response.raise_for_status()
        bulk_meta = bulk_response.json()
        download_uri = bulk_meta.get("download_uri") or bulk_meta.get("jsonl_download_uri")
        if not download_uri:
            raise ValueError("Scryfall bulk metadata enthält keine Download-URL")

        lookup: dict[int, dict[str, Any]] = {}
        cards_response = requests.get(download_uri, timeout=120, headers=headers, stream=True)
        cards_response.raise_for_status()

        if str(download_uri).endswith(".jsonl.gz"):
            with gzip.GzipFile(fileobj=cards_response.raw) as gz:
                for raw_line in gz:
                    if not raw_line.strip():
                        continue
                    _add_scryfall_card(lookup, json.loads(raw_line))
            return lookup

        for c in cards_response.json():
            _add_scryfall_card(lookup, c)
        return lookup
    except Exception as e:
        print(f"❌ Scryfall-Download fehlgeschlagen: {e}")
        return {}


def _add_scryfall_card(lookup: dict[int, dict[str, Any]], card: dict[str, Any]) -> None:
    """Übernimmt eine Scryfall-Karte, falls sie eine Arena-ID hat."""
    arena_id = card.get("arena_id")
    if arena_id:
        lookup[arena_id] = {
            "name": card.get("name", "Unknown"),
            "set": card.get("set", "").upper(),
            "collector_number": card.get("collector_number", ""),
            "rarity": card.get("rarity", "unknown"),
        }


# MTGA Rarity-Spalte: 1=common, 2=uncommon, 3=rare, 4=mythic
_RARITY_MAP = {1: "common", 2: "uncommon", 3: "rare", 4: "mythic"}


def _rarity_label(value: Any) -> str:
    """Map MTGA Rarity-Int auf ein Label (common/uncommon/rare/mythic)."""
    try:
        return _RARITY_MAP.get(int(value), "unknown")
    except (TypeError, ValueError):
        return "unknown"


def _read_lookup_cache(path: Path) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Reads current and legacy cache formats."""
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and data.get("schema") == CACHE_SCHEMA:
        cards = data.get("cards", {})
        metadata = {
            "schema": data.get("schema"),
            "source": data.get("source"),
            "sourcePath": data.get("sourcePath"),
            "cardCount": data.get("cardCount"),
        }
        return {int(k): v for k, v in cards.items() if isinstance(v, dict)}, metadata

    if isinstance(data, dict):
        # Legacy flat cache: {"123": {...}}.
        return {int(k): v for k, v in data.items() if isinstance(v, dict)}, {
            "schema": "legacy-flat",
            "source": "unknown",
            "sourcePath": None,
            "cardCount": len(data),
        }

    return {}, {"schema": "unknown", "source": "unknown", "sourcePath": None, "cardCount": 0}


def _write_lookup_cache(
    path: Path,
    lookup: dict[int, dict[str, Any]],
    *,
    source: str,
    source_path: Path | None,
) -> None:
    payload = {
        "schema": CACHE_SCHEMA,
        "source": source,
        "sourcePath": source_path.as_posix() if source_path else None,
        "cardCount": len(lookup),
        "cards": {str(k): v for k, v in sorted(lookup.items())},
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, sort_keys=True)


def load_card_database(*, refresh_cache: bool = False) -> dict[int, dict[str, Any]]:
    """Orchestriert das Laden: Cache → Lokal → Scryfall.

    Reihenfolge:
      1. Cache-Datei (~/.mtga_advisor/arena_id_lookup.json)
      2. Lokale .mtga SQLite-Dateien
      3. Scryfall Bulk-API (Fallback)
    """
    cache_dir = get_default_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 1. Cache
    lookup_file = _lookup_file()

    local_path = get_macos_mtga_data_path()

    if lookup_file.exists() and not refresh_cache:
        try:
            print("📦 Lade gecachte Karten-DB...")
            lookup, metadata = _read_lookup_cache(lookup_file)
            cache_source = metadata.get("source")
            # A local MTGA card DB has better Arena-ID coverage than Scryfall/legacy caches.
            if local_path and cache_source != CACHE_SOURCE_LOCAL:
                print("⚠ Cache ist nicht aus lokaler MTGA-DB; lade lokale Karten-DB neu...")
            else:
                return lookup
        except Exception:
            print("⚠ Cache beschädigt, lade neu...")
    elif refresh_cache:
        print("🔄 Erneuere Karten-DB-Cache...")

    # 2. Lokale DB
    lookup = load_local_mtga_database()
    source = CACHE_SOURCE_LOCAL if lookup else CACHE_SOURCE_SCRYFALL
    source_path = local_path if lookup else None

    # 3. Scryfall Fallback
    if not lookup:
        print("⚠ Keine lokale DB gefunden. Lade von Scryfall...")
        lookup = fetch_scryfall_database()
        source = CACHE_SOURCE_SCRYFALL
        source_path = None

    # Cache schreiben
    if lookup:
        try:
            _write_lookup_cache(lookup_file, lookup, source=source, source_path=source_path)
            print("💾 Karten-DB gecached")
        except Exception as exc:
            print(f"⚠ Karten-DB-Cache konnte nicht geschrieben werden: {exc}")

    return lookup
