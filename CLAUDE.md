# Mtga.advisor — Projektkontext für AI-Agenten

> **Stand: 2026-08-06.** Dieses Dokument ist die aktuelle, verbindliche
> Projektbeschreibung. Es ersetzt die veraltete Log-basierte Perspektive in
> `docs/ai-context.md` (siehe unten).

## Projektziel

Lokales, deterministisches Tool für **sammlungsbewusste Deck-Beratung** für
MTG Arena. Kern: die eigene Kartensammlung (`collection.json`) zuverlässig
erfassen und daraus Deck-Empfehlungen ableiten. Offline-first, keine Cloud/LLM
im Kernpfad.

## Architektur-Überblick (Stand 2026-08)

```
MTGA läuft
   │
   ├─ (Daemon als root, LaunchDaemon)  helper/mtga-helper  [C]
   │    └─ UNIX-Socket /var/run/mtga-helper.sock
   │         └─ task_for_pid → MTGA-Speicher lesen (root nötig)
   │
   └─ mtga-advisor watch              ← scanner/watch.py (T4)
        └─ scan --all                 ← scanner/combined_scanner.py (T3)
             ├─ Collection  (scanner/memory_scanner.py)
             ├─ Decks       (scanner/deck_scanner.py)
             └─ Ranks       (scanner/rank_scanner.py)
        Fallback: ohne Helper → Pymem direkt (braucht sudo)
```

### Wichtige Erkenntnis (2026-08-06)

**Der Log-Scanner kann die Collection NICHT mehr liefern.** Der Endpoint
`PlayerInventory.GetPlayerCardsV3` (einzige Log-Quelle für besessene Karten)
wurde **im August 2021 entfernt und nie ersetzt**. Der Log-Scanner
(`parser/`) extrahiert nur noch **Decks** (StartHook `DeckSummaries`) und
**Ranks** (`RankGetCombinedRankInfo`).

**Konsequenz:** Die Collection kommt **ausschließlich** aus dem Memory-Scanner.
`docs/parser-analyse.md` ist als veraltet markiert. Details siehe
`docs/parser-wechsel-macos-scanner.md` und die Recherche-Notiz im Obsidian Vault.

## Module

| Modul | Zweck |
|-------|-------|
| `scanner/memory_scanner.py` | **Collection** aus MTGA-Speicher (Anker-basiert, `find_blocks`) |
| `scanner/pattern_scanner.py` | MemoryBackend-Protocol, PymemBackend, Scan-Primitives |
| `scanner/helper_client.py` | Helper-Socket-Client, `HelperBackend`, `PersistentHelperBackend` |
| `scanner/combined_scanner.py` | `scan_all()` — Collection+Decks+Ranks über 1 persistente Verbindung |
| `scanner/deck_scanner.py` | Decks via IL2CPP-Backref (`Client_Deck`) |
| `scanner/rank_scanner.py` | Ranks + Account |
| `scanner/il2cpp_nav.py` | IL2CPP-Navigation, `Il2CppDeckResult` |
| `scanner/watch.py` | Watch-Mode: pollt MTGA, führt kombinierten Scan aus |
| `scanner/card_database.py` | Lokale Karten-DB (Arena-ID → Name) |
| `parser/` | Log-Parser: **nur Decks + Ranks** (Collection tot) |
| `cli/main.py` | CLI: `scan`, `scan --all`, `watch`, `decks`, `ranks` |
| `helper/main.c` | C-Daemon (root), `task_for_pid`, UNIX-Socket |
| `helper/install.sh` | Installation via `launchctl load` (macOS 15.6) |
| `helper/install-commands.sh` | Gibt sudo-Befehle als echo aus (Frank führt aus) |

## CLI-Kommandos

```bash
# Kombinierter Scan (Collection + Decks + Ranks) über 1 Helper-Verbindung
mtga-advisor scan --all

# Einzelne Scans
mtga-advisor scan --no-decks --no-ranks   # nur Collection
mtga-advisor scan --no-collection        # nur Decks+Ranks

# Watch-Mode (regelmäßiger Scan wenn MTGA aktiv)
mtga-advisor watch --interval 300

# Decks aus Logs (StartHook)
mtga-advisor decks
```

## Helper-Daemon (sudo-frei)

- **Zweck von sudo:** Nur einmalig bei Installation, um den C-Daemon als root
  (LaunchDaemon) zu installieren. Root ist nötig für `task_for_pid()` (MTGA-
  Speicher lesen). Danach läuft der Daemon dauerhaft ohne Passwort.
- **Installation (macOS 15.6):** `launchctl load` (SMJobBless ist deprecated).
  Siehe `docs/helper-installation.md`.
- **Deployment-Ziel:** MacBook Air (User `frankhermann`, MTGA aktuell).
  Projektpfad dort: `/Users/frankhermann/Documents/mtga-advisor`.
- **Socket:** `/var/run/mtga-helper.sock`.

## Tests

```bash
# Alle Tests
python3 -m pytest scanner/tests/ -q

# Helper-E2E (braucht kein MTGA, Test-Modus)
bash helper/tests/test_e2e.sh
```

Stand 2026-08-06: **256 Tests grün** (scanner/tests/).

## Git-Workflow

- **Branch:** Entwicklung auf `feat/sudo-helper`. Merge in `main` **nur** nach
  explizitem "merge" von Frank.
- **Commit-Author:** `Jarvis <jarvis@local>` (für Jarvis-Commits).
- **Force-Push:** Nur nach Franks explizitem OK.
- **Kanban:** Ein Board pro Repo (`mtga-advisor`), nicht pro Run/Sprint.

## Wichtige Regeln für Agenten

1. **Collection nur via Memory-Scan** — nicht in Log-basierte Collection-
   Extraktion investieren (tot seit 2021).
2. **Keine Secrets** — keine API-Keys/Tokens in Commits.
3. **Tests grün halten** — vor Commit `pytest scanner/tests/` ausführen.
4. **Helper-Installation** braucht sudo — Worker darf kein sudo ausführen,
   erzeugt stattdessen Skripte/Doku für Frank.
5. **`find_blocks()` Dedup** — `scan_collection_detailed()` dedupliziert
   `candidates` vor `max()`. Nicht entfernen.

## Veraltete Dokumente

- `docs/ai-context.md` — beschreibt noch Log-basierte Collection als Kernpfad.
  **Veraltet.** Die Collection kommt aus dem Memory-Scanner.
- `docs/parser-analyse.md` — Log-Collection-Analyse. **Veraltet** (GetPlayerCardsV3
  entfernt). Nur noch für Decks/Ranks relevant.
- `docs/phase-1-collection-export.md` — Log-basierter Plan. **Veraltet.**

## Referenzen

- Recherche-Notiz (Obsidian Vault): `05-Recherche/mtga-advisor-scanner-recherche-2026.md`
- Roadmap v3: `docs/deck-scan-roadmap-v3.md`
- Helper-Installation: `docs/helper-installation.md`
- Watch-Mode: `docs/watch-mode.md`
