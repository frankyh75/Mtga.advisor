# Mtga.advisor — Feature-Erweiterungen (mtgatool-Inspiriert)

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Implement 7 neue Features aus der mtgatool-Analyse — Arena-Export, Card-Renderer, lokale Card-DB, Collection-History, Ranks, Meta-Daten, Sync-Server.

**Architecture:** Jedes Feature ist eigenständig, baut auf bestehenden Modulen auf (scanner, advisor, server, cli). Keine Breaking Changes.

**Tech Stack:** Python, pymem-osx, SQLite, Scryfall-API, MTGGoldfish, bestehende Mtga.advisor-Module

**Assignee:** worker-heavy (GLM 5.2 via Ollama Cloud)

---

## Task 1: Arena-Export

**Objective:** CLI-Befehl `deck export <id>` erzeugt Arena-kompatiblen Text (wie mtgatool's `getExportTxt()`).

**Files:**
- Modify: `cli/main.py` (neuer Subcommand `deck export`)
- Modify: `parser/decks.py` (neue Funktion `export_deck_to_arena_text`)
- Create: `parser/tests/test_arena_export.py`

**Format:**
```
4 Lightning Strike
2 Shock

3 Duress
```
Mainboard → Zeilen, Leerzeile, Sideboard → Zeilen. Karten aus Memory-Scan (pymem-osx) oder manuellem Import.

**Verification:** `python3 -m cli.main deck export <id>` → Arena-kompatibler String. Copy-Paste in MTGA → Deck lädt.

---

## Task 2: Card-Renderer im Dashboard

**Objective:** Dashboard zeigt Kartenbilder (Scryfall-API, lazy-load) neben Deck-Karten.

**Files:**
- Modify: `server/app.py` (neue Route `/api/card-image/<grp_id>`)
- Modify: `server/templates/` (HTML/CSS für Kartenbilder)
- Modify: `scanner/card_database.py` (Scryfall-Image-URL in DB)

**API:** `GET /api/card-image/12345` → redirect zu `https://c1.scryfall.com/cards/normal/front/...` oder 404.

**Dashboard:** In Deck-Ansicht: kleine Karten-Thumbnails (200x280px) neben jeder Karte. Lazy-load via IntersectionObserver.

**Verification:** Dashboard zeigt Karten-Thumbnails in Deck-Detail-Ansicht.

---

## Task 3: Lokale Card-Datenbank

**Objective:** Einmaliger Scryfall-Bulk-Download → SQLite-DB lokal. Kein API-Call mehr für Namensauflösung.

**Files:**
- Create: `scanner/card_db_builder.py` (Bulk-Download + SQLite-Import)
- Modify: `scanner/card_database.py` (SQLite statt API)
- Create: `scanner/tests/test_card_db_builder.py`

**Schema:**
```sql
CREATE TABLE cards (
    grp_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    set_code TEXT,
    collector_number TEXT,
    rarity TEXT,
    image_uri TEXT,
    mana_cost TEXT,
    cmc REAL,
    type_line TEXT,
    oracle_text TEXT,
    colors TEXT
);
```

**Bulk-Download:** `https://api.scryfall.com/bulk-data/default-cards` → ~500MB JSON → SQLite (~50MB).

**Verification:** `python3 -c "from scanner.card_database import load_card_database; db = load_card_database(); print(len(db))"` → ~25.000 Karten, keine API-Calls.

---

## Task 4: Collection-History

**Objective:** Jeder Sync speichert Timestamp + Snapshot. Dashboard zeigt Diff ("Was ist neu seit letzter Woche?").

**Files:**
- Create: `scanner/history.py` (Snapshot-Manager, Diff-Engine)
- Modify: `cli/main.py` (neuer Subcommand `history`)
- Modify: `server/app.py` (History-View im Dashboard)
- Create: `scanner/tests/test_history.py`

**Format:**
```
out/history/
├── 2026-08-01T10:00:00Z_collection.json
├── 2026-08-01T10:00:00Z_decks.json
├── 2026-08-02T14:30:00Z_collection.json
├── 2026-08-02T14:30:00Z_decks.json
└── index.json  (Metadaten)
```

**Diff-View:**
```
📈 +3 Karten seit letztem Sync
  +1x Sheoldred, the Apocalypse (neu)
  +2x Lightning Strike (jetzt 4x)
📉 -1 Karte
  -1x Duress (jetzt 2x)
```

**Verification:** `python3 -m cli.main history diff` → zeigt Änderungen seit letztem Sync.

---

## Task 5: Ranks + Account-Info

**Objective:** Memory-Scan für Rang-Daten (WrapperController → PlayerRankServiceWrapper → _combinedRankInfo).

**Files:**
- Modify: `scanner/deck_scanner.py` (neue Funktion `scan_ranks`)
- Modify: `server/app.py` (Rang-Anzeige im Dashboard)
- Create: `scanner/tests/test_ranks.py`

**Memory-Pfad:**
```
WrapperController.Instance
  → PlayerRankServiceWrapper
    → _combinedRankInfo
      → constructedClass (int: 0=Mythic, 1=Platinum, etc.)
      → limitedClass
      → level, step, wins, losses
```

**Dashboard:** Oben rechts: "🧢 Platinum #1274 (4-2 diese Saison)"

**Verification:** `sudo python3 -m cli.main ranks` → zeigt aktuellen Rang.

---

## Task 6: Meta-Daten (MTGGoldfish)

**Objective:** Scraper für aktuelle Meta-Daten (was wird gerade gespielt). LLM-Prompt bekommt Meta-Kontext.

**Files:**
- Create: `advisor/meta.py` (MTGGoldfish-Scraper, Caching)
- Modify: `advisor/llm_advisor.py` (Meta-Kontext in Prompt)
- Create: `advisor/tests/test_meta.py`

**Daten:**
```json
{
  "format": "Standard",
  "top_decks": [
    {"name": "Mono-Red Aggro", "share": 18.5, "winrate": 54.2},
    {"name": "Esper Control", "share": 12.3, "winrate": 51.8}
  ],
  "top_cards": [
    {"name": "Sheoldred", "decks": 42},
    {"name": "Sunfall", "decks": 38}
  ]
}
```

**LLM-Prompt-Erweiterung:**
```
Aktuelles Meta (Standard):
- Mono-Red Aggro: 18.5% (54.2% WR)
- Esper Control: 12.3% (51.8% WR)
...
```

**Verification:** `python3 -m cli.main advisor meta` → zeigt aktuelle Meta-Daten.

---

## Task 7: Sync-Server (Remote-Zugriff)

**Objective:** Dashboard via HTTP vom Handy/Tablet aus erreichbar (Tailscale oder LAN).

**Files:**
- Modify: `server/app.py` (Host-Konfiguration, CORS)
- Create: `server/sync_config.py` (Port, Auth, Tailscale-Detection)
- Create: `server/tests/test_sync.py`

**Features:**
- Dashboard auf `0.0.0.0:8000` (nicht nur localhost)
- Optional: Basic-Auth für Remote-Zugriff
- Tailscale-Auto-Detection: Wenn Tailscale-IP vorhanden, zeige QR-Code im Dashboard
- Status-Seite: `/health` → JSON mit Version, Sync-Status, letztem Update

**Verification:** Dashboard erreichbar via `http://<tailscale-ip>:8000` vom Handy.

---

## Dependencies

```
T1 (Arena-Export) — keine Dependencies
T2 (Card-Renderer) — keine Dependencies
T3 (Lokale Card-DB) — keine Dependencies
T4 (Collection-History) — keine Dependencies
T5 (Ranks) — keine Dependencies
T6 (Meta-Daten) — keine Dependencies
T7 (Sync-Server) — keine Dependencies
```

Alle 7 Tasks sind unabhängig und können parallel laufen.

## Erfolgskriterien

- [ ] `python3 -m cli.main deck export <id>` → Arena-kompatibler Text
- [ ] Dashboard zeigt Karten-Thumbnails
- [ ] `load_card_database()` ohne API-Call, ~25k Karten
- [ ] `python3 -m cli.main history diff` → Änderungen seit letztem Sync
- [ ] `sudo python3 -m cli.main ranks` → aktueller Rang
- [ ] `python3 -m cli.main advisor meta` → aktuelle Meta-Daten
- [ ] Dashboard via Tailscale-IP vom Handy erreichbar
- [ ] Alle Tests: `python3 -m unittest discover -s scanner/tests -v`
