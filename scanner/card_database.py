"""Karten-Datenbank: lokale .mtga SQLite + Scryfall-Fallback."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import requests

from .macos_paths import get_default_cache_dir, get_macos_mtga_data_path

LOOKUP_FILE = get_default_cache_dir() / "arena_id_lookup.json"


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

            if "Cards" not in tables or "Localizations" not in tables:
                conn.close()
                continue

            # Lokalisierung laden (bevorzugt en-US)
            loc_map: dict[int, str] = {}
            try:
                cursor.execute("SELECT Id, Text FROM Localizations WHERE Format LIKE '%en-US%' OR Format IS NULL")
                for lid, text in cursor.fetchall():
                    if text:
                        loc_map[lid] = text
            except sqlite3.Error:
                cursor.execute("SELECT Id, Text FROM Localizations")
                for lid, text in cursor.fetchall():
                    if text:
                        loc_map[lid] = text

            # Spalten prüfen
            cols = [row[1] for row in cursor.execute("PRAGMA table_info(Cards)")]
            has_set = "ExpansionCode" in cols
            has_cn = "CollectorNumber" in cols

            query = (
                f"SELECT GrpId, TitleId, "
                f"{'ExpansionCode' if has_set else 'NULL'}, "
                f"{'CollectorNumber' if has_cn else 'NULL'} "
                f"FROM Cards"
            )
            cursor.execute(query)
            rows = cursor.fetchall()

            for row in rows:
                grp_id = row[0]
                title_id = row[1]
                set_code = row[2] if row[2] else ""
                cn = str(row[3]) if row[3] else ""

                if title_id in loc_map:
                    lookup[grp_id] = {
                        "name": loc_map[title_id],
                        "set": set_code,
                        "collector_number": cn,
                    }

            conn.close()

            if len(lookup) > 1000:
                print(f"✅ {len(lookup)} Karten lokal geladen (aus {f.name})")
                return lookup

        except (sqlite3.Error, Exception):
            continue

    print(f"📊 {len(lookup)} Karten lokal geladen")
    return lookup


def fetch_scryfall_database() -> dict[int, dict[str, Any]]:
    """Lädt Kartendaten von der Scryfall-API (Bulk-Download)."""
    print("🌐 Lade Kartendaten von Scryfall API...")
    try:
        bulk_meta = requests.get(
            "https://api.scryfall.com/bulk-data/default-cards",
            timeout=30,
        ).json()
        cards_data = requests.get(bulk_meta["download_uri"], timeout=120).json()

        lookup: dict[int, dict[str, Any]] = {}
        for c in cards_data:
            arena_id = c.get("arena_id")
            if arena_id:
                lookup[arena_id] = {
                    "name": c.get("name", "Unknown"),
                    "set": c.get("set", "").upper(),
                    "collector_number": c.get("collector_number", ""),
                }
        return lookup
    except Exception as e:
        print(f"❌ Scryfall-Download fehlgeschlagen: {e}")
        return {}


def load_card_database() -> dict[int, dict[str, Any]]:
    """Orchestriert das Laden: Cache → Lokal → Scryfall.

    Reihenfolge:
      1. Cache-Datei (~/.mtga_advisor/arena_id_lookup.json)
      2. Lokale .mtga SQLite-Dateien
      3. Scryfall Bulk-API (Fallback)
    """
    cache_dir = get_default_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)

    # 1. Cache
    if LOOKUP_FILE.exists():
        try:
            print("📦 Lade gecachte Karten-DB...")
            with LOOKUP_FILE.open("r", encoding="utf-8") as f:
                data = json.load(f)
            return {int(k): v for k, v in data.items() if isinstance(v, dict)}
        except Exception:
            print("⚠ Cache beschädigt, lade neu...")

    # 2. Lokale DB
    lookup = load_local_mtga_database()

    # 3. Scryfall Fallback
    if not lookup:
        print("⚠ Keine lokale DB gefunden. Lade von Scryfall...")
        lookup = fetch_scryfall_database()

    # Cache schreiben
    if lookup:
        try:
            with LOOKUP_FILE.open("w", encoding="utf-8") as f:
                json.dump({str(k): v for k, v in lookup.items()}, f)
            print("💾 Karten-DB gecached")
        except Exception:
            pass

    return lookup
