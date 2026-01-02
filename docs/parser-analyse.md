# Parser-Analyse: MTGA Collection-Rekonstruktion aus Logs

Hinweis: Offene Fragen für die fachliche Abklärung sind in `docs/Frank.md` gesammelt.

## Schritt 0 – Kontext-Validierung (kurz)

**Kurzfassung `docs/ai-context.md` (max. 10 Bullet Points):**
1. Lokales, deterministisches Tool für sammlungsbewusste Deck-Beratung ohne Cloud/LLM im Kernpfad.
2. Phase-1-Kernnutzen: robuste `collection.json`-Extraktion aus MTGA-Logs.
3. Reifegrad: frühe Planung/Phase 0–1, keine Implementierung.
4. Geplante Module: Log-Finder/Parser → Exporter → Regel-Engine → Formatter.
5. Offline-first, keine externen APIs im Kernpfad.
6. macOS/Windows primär, Linux optional; single-run Export.
7. Risiken: Log-Format-Drift, OS-Pfadvarianten, unvollständige Logs.
8. Offene Fragen: Snapshot vs. Deltas, Pfaderkennung, Schema-Versionierung.
9. Leitplanken: deterministisch, defensive Parser, klare Fehlermeldungen.
10. LLM optional und nicht kernpfadkritisch.

**Ziel „Collection final aus Logs“ – Klarheit:** Teilweise klar, aber nicht vollständig spezifiziert.

**Fehlende Aspekte im Kontext:**
- Wildcards/Inventar: gehören Wildcards/Vault/Gems/Gold in `collection.json`?
- Reprints/Varianten: Normalisierung von Reprints/Styles/Collector Numbers.
- Multi-Account: Verhalten bei mehreren Accounts in denselben Logs.
- Log-Quelle: `Player.log` vs. `output_log.txt` (Legacy).
- „Final“-Definition: Umgang mit Deltas ohne Snapshot.

## Schritt 1 – Kandidatenliste (FINAL)

**Startliste (validiert):**
- mtgatracker/mtgatracker
- mtgatool/mtgatool-desktop
- kelesi/mtga-utils
- gathering-gg/parser
- mvanotti/mtgassistant
- rconroy293/mtga-log-client (17Lands client)
- ibiza240/MTGAHelper-Windows-Client
- CodySchrank/MTG-Arena-Tool
- AdamManuel-dev/MagicTheGatheringArena-Tools

**Zusätzlich relevante Kandidaten (Collection/Inventory explizit):**
- Razviar/mtgap
- depr-quint/mtga
- Cynthion/MTGA-Collection-Optimizer

**Ausschlüsse (keine klare Collection-Rekonstruktion):**
- adewey/mtga-log-parser (generischer Parser, kein Collection-Export klar)
- gathering-gg/client (UI/Client, Parser in separat repo)
- Alex-Van-Buren/mtga_collection_managerv2 (Frontend, kein Log-Parser sichtbar)

## Schritt 2 – Technische Due Diligence (je Repo)

### mtgatracker/mtgatracker
1. **Zweck & Scope:** Electron-Tracker mit Log-Backend und Collection-Ansicht.
2. **Log-Quellen:** `output_log.txt` (LocalLow) via `app/mtgatracker_backend.py`.
3. **Collection-Extraktion:** `parse_get_player_cards_v3` in `app/parsers.py`, setzt `mtga_watch_app.collection`.
4. **Abhängigkeiten:** python-mtga; Backend-Server optional für Features.
5. **Robustheit:** Log-Tailing, kein explizites Rotation/Cache-Handling.
6. **Qualität:** keine offensichtlichen Tests für Collection-Parser.
7. **Lizenz:** LICENSE ohne SPDX in API-Metadaten (unklar).

### mtgatool/mtgatool-desktop
1. **Zweck & Scope:** Collection Browser + Deck Tracker.
2. **Log-Quellen:** `Player.log` über `src/utils/defaultLogUri.ts` (macOS/Windows/Linux/Wine).
3. **Collection-Extraktion:** `logEntrySwitch.ts` dispatcht `PlayerInventory.GetPlayerCardsV3` (`InPlayerInventoryGetPlayerCardsV3.ts`) und `Inventory.Updated` (`InventoryUpdated.ts`).
4. **Abhängigkeiten:** lokale DB; kein zwingender Upload für Collection.
5. **Robustheit:** `arena-log-watcher.ts` erkennt File-Recreate; `DetailedLogs.ts` prüft Log-Status.
6. **Qualität:** modulare TS-Architektur; Test-Logs vorhanden.
7. **Lizenz:** GPL-3.0.

### kelesi/mtga-utils
1. **Zweck & Scope:** CLI-Exporter für Collection.
2. **Log-Quellen:** `Player.log` (LocalLow) via `mtga_log.py`.
3. **Collection-Extraktion:** sucht `PlayerInventory.GetPlayerCardsV3` und liest letzten JSON-Block.
4. **Abhängigkeiten:** python-mtga; optional Scryfall-Fallback.
5. **Robustheit:** kein Rotation/Cache; `Detailed Logs` erforderlich.
6. **Qualität:** kleiner Code, Test-Log vorhanden.
7. **Lizenz:** MIT.

### gathering-gg/parser
1. **Zweck & Scope:** Go-Parser-Library + CLI.
2. **Log-Quellen:** `output_log.txt` (Windows LogDir gesetzt, mac/linux leer).
3. **Collection-Extraktion:** `collection.go` → `ParseCollection()` aus Segment `PlayerInventoryGetPlayerCards`.
4. **Abhängigkeiten:** CLI nutzt Server, Library offline verwendbar.
5. **Robustheit:** Segment-Parser (`log.go`), aber kein Rotation/Cache.
6. **Qualität:** Tests (`collection_test.go`, `inventory_test.go`).
7. **Lizenz:** MIT.

### mvanotti/mtgassistant
1. **Zweck & Scope:** Collection Exporter + Deck Helper.
2. **Log-Quellen:** `output_log.txt` Standardpfad in `collectionexporter/main.go`.
3. **Collection-Extraktion:** Präfix-Parsing in `collectionfinder/collection_finder.go` (`PlayerInventory.GetPlayerCardsV3`).
4. **Abhängigkeiten:** lokale `MTGA_Data/Downloads` für CardDB.
5. **Robustheit:** JSON-Decoder über Stream; keine Rotation.
6. **Qualität:** keine Tests sichtbar.
7. **Lizenz:** MIT.

### rconroy293/mtga-log-client (17Lands)
1. **Zweck & Scope:** Log-Follower mit Server-Upload.
2. **Log-Quellen:** `Player.log` + `Player-prev.log` (Windows/macOS/Steam/Wine) in `mtga_follower.py`.
3. **Collection-Extraktion:** `__handle_collection` bei `PlayerInventory.GetPlayerCardsV3`.
4. **Abhängigkeiten:** Token + REST-Upload verpflichtend.
5. **Robustheit:** Rotation-Pfade, Zeitstempel-Parsing, Reconnect-Handling.
6. **Qualität:** strukturierter Parser, Tests unklar.
7. **Lizenz:** GPL-3.0.

### ibiza240/MTGAHelper-Windows-Client
1. **Zweck & Scope:** Windows Tracker + Server-Sync.
2. **Log-Quellen:** OutputLog via `ReaderMtgaOutputLog`.
3. **Collection-Extraktion:** `GetPlayerCardsV3` + `GetPlayerInventory` + `InventoryUpdated` Converter.
4. **Abhängigkeiten:** Server-Sync vorgesehen.
5. **Robustheit:** Converter-Architektur, breite Event-Abdeckung.
6. **Qualität:** große Modellschicht, strukturiert.
7. **Lizenz:** MIT.

### CodySchrank/MTG-Arena-Tool
1. **Zweck & Scope:** Deck Tracker + Collection Manager.
2. **Log-Quellen:** `output_log.txt` Pfade in `window_background/background.js`.
3. **Collection-Extraktion:** `PlayerInventory.GetPlayerCardsV3` via `checkJsonWithStart`.
4. **Abhängigkeiten:** optionale Server-Komponenten (mtgatool.com).
5. **Robustheit:** einfache Parsing-Logik, keine Rotation.
6. **Qualität:** geringe Testabdeckung.
7. **Lizenz:** CC0-1.0.

### AdamManuel-dev/MagicTheGatheringArena-Tools
1. **Zweck & Scope:** macOS-CLI für Collection-Export.
2. **Log-Quellen:** `Player.log` + `Player-prev.log` in `src/lib/logs.ts`.
3. **Collection-Extraktion:** `extractOwnedFromLog()` auf `InventoryInfo`/`GetPlayerCardsV3`, Regex-Fallback.
4. **Abhängigkeiten:** optional Scryfall; Offline-Modus möglich.
5. **Robustheit:** Snapshot-Caching + Retry-Backoff, Edge-Case-Doku.
6. **Qualität:** Tests vorhanden (`test/unit/logs*.test.js`).
7. **Lizenz:** keine Lizenzdatei (unklar).

### Razviar/mtgap
1. **Zweck & Scope:** Pro Tracker mit Server-Sync.
2. **Log-Quellen:** `Player.log` (mac/windows) in `src/app/log-parser/log_parser.ts`.
3. **Collection-Extraktion:** Event-Metadaten über Server (`getParsingMetadata`), Collection-Upload implizit.
4. **Abhängigkeiten:** Server zwingend.
5. **Robustheit:** Chunked Parsing + `Detailed Logs`-Check.
6. **Qualität:** große TS-Architektur; Tests unklar.
7. **Lizenz:** keine Lizenzdatei (unklar).

### depr-quint/mtga
1. **Zweck & Scope:** Go-Parser-Library für `output_log.txt`.
2. **Log-Quellen:** Standardpfad in README; Parser in `incoming.go`.
3. **Collection-Extraktion:** Callbacks `OnGetPlayerCards`/`OnGetPlayerInventory`.
4. **Abhängigkeiten:** keine (Library).
5. **Robustheit:** modularer Parser; kein Rotation/Cache.
6. **Qualität:** klarer API-Schnitt; Tests unklar.
7. **Lizenz:** Apache-2.0.

### Cynthion/MTGA-Collection-Optimizer
1. **Zweck & Scope:** Collection/Deck-Tracker.
2. **Log-Quellen:** `output_log.txt` in `Settings.cs`, Commands in `appsettings.json`.
3. **Collection-Extraktion:** `LogParser.ParsePlayerCards()` auf `PlayerInventory.GetPlayerCardsV3`.
4. **Abhängigkeiten:** lokale Data-Files, keine externen APIs zwingend.
5. **Robustheit:** `Detailed Logs`-Check; Rotation nicht ersichtlich.
6. **Qualität:** C#-Struktur solide; Tests unklar.
7. **Lizenz:** keine Lizenzdatei (unklar).

## Schritt 3 – Standardisiertes Scoring (0–5)

**Kriterien:** 1) Collection-Fidelity, 2) Determinismus & Offline, 3) Parser-Resilienz, 4) Plattform, 5) Code-Qualität, 6) Lizenz, 7) Doku

| Repo | 1 | 2 | 3 | 4 | 5 | 6 | 7 | Gesamt |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| mtgatracker/mtgatracker | 3 | 3 | 2 | 2 | 2 | 1 | 2 | **15** |
| mtgatool/mtgatool-desktop | 4 | 4 | 4 | 4 | 3 | 2 | 3 | **24** |
| kelesi/mtga-utils | 3 | 3 | 2 | 2 | 2 | 5 | 3 | **20** |
| gathering-gg/parser | 3 | 3 | 3 | 2 | 4 | 5 | 3 | **23** |
| mvanotti/mtgassistant | 3 | 4 | 2 | 2 | 3 | 5 | 3 | **22** |
| rconroy293/mtga-log-client | 3 | 1 | 4 | 4 | 3 | 2 | 3 | **20** |
| ibiza240/MTGAHelper-Windows-Client | 4 | 1 | 4 | 2 | 4 | 5 | 2 | **22** |
| CodySchrank/MTG-Arena-Tool | 3 | 3 | 2 | 2 | 2 | 5 | 2 | **19** |
| AdamManuel-dev/MagicTheGatheringArena-Tools | 4 | 3 | 4 | 2 | 4 | 1 | 4 | **22** |
| Razviar/mtgap | 3 | 0 | 3 | 3 | 3 | 0 | 2 | **14** |
| depr-quint/mtga | 3 | 4 | 3 | 2 | 3 | 5 | 3 | **23** |
| Cynthion/MTGA-Collection-Optimizer | 4 | 3 | 2 | 2 | 3 | 0 | 2 | **16** |

## Schritt 4 – Ranking & Empfehlung

### Top 5 Ranking
1. **mtgatool/mtgatool-desktop** – beste Kombination aus Snapshot+Delta, Event-Dispatch und OS-Pfade.
2. **gathering-gg/parser** – solide Segment-Parser-Architektur + Tests.
3. **depr-quint/mtga** – modularer Parser mit Collection/Inventory Callbacks (Apache-2.0).
4. **mvanotti/mtgassistant** – einfacher, lokaler Exporter (Go).
5. **AdamManuel-dev/MagicTheGatheringArena-Tools** – Snapshot-Caching & Retry-Backoff (Lizenz unklar).

### Empfehlung (Designs/Patterns übernehmen)
- Snapshot + Delta (GetPlayerCardsV3 + Inventory.Updated).
- Rotation-aware Ingestion (`Player.log` + `Player-prev.log`) + Snapshot-Cache.
- Event-Dispatch-Pipeline (Segmentierung → Label → Parser pro Event).
- Defensive JSON-Extraktion (mehrzeilige Payloads, partielle Blöcke, Multi-Events pro Chunk).

### Empfehlung (bewusst vermeiden)
- Pflicht-Server-Upload oder Token-Abhängigkeiten.
- Scryfall/Externe APIs im Kernpfad.
- Stille Fallbacks ohne Warnung.

### Ableitung für unser Projekt
- **Minimal viable parser design:** Log-Finder → Log-Split → Event-Dispatch → Snapshot+Delta → `collection.json`.
- **Datenmodell:** Arena-ID + Count als Primärschlüssel, Zusatzfelder optional.
- **Failure-Modes:** fehlender Snapshot, Detailed Logs disabled, Log-Drift, Rotation/Truncation.
