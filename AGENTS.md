# Mtga.advisor — Agent-Kontext (AGENTS.md)

> **Stand: 2026-08-06.** Dieses Dokument wird von Hermes/Codex/Agenten beim
> Start gelesen. Es erklärt den aktuellen Stand und verweist auf die
> verbindlichen Detail-Dokumente.

## Projekt in einem Satz

Lokales, deterministisches Tool für **sammlungsbewusste Deck-Beratung** für
MTG Arena: Kartensammlung erfassen (`collection.json`) → Deck-Empfehlungen.
Offline-first, keine Cloud/LLM im Kernpfad.

## Wichtigste Erkenntnis (2026-08-06)

**Collection kommt NUR aus dem Memory-Scanner.** Der Log-Endpoint
`PlayerInventory.GetPlayerCardsV3` wurde im August 2021 entfernt und nie
ersetzt. Der Log-Scanner (`parser/`) extrahiert nur noch **Decks** (StartHook)
und **Ranks** (`RankGetCombinedRankInfo`). Nicht in Log-basierte
Collection-Extraktion investieren.

## Dokument-Hierarchie (verbindlich)

| Datei | Rolle |
|-------|-------|
| **`CLAUDE.md`** | **Aktuelle, verbindliche Projektbeschreibung** (Architektur, Module, CLI, Regeln) |
| **`docs/deck-advisor-roadmap-v4.md`** | **Aktive Arbeitsplanung** (Advisor-UX, New-Deck-Builder, Analyze-Flow) |
| `docs/deck-scan-roadmap-v3.md` | Scanner-Historie + Restliste (größtenteils erledigt) |
| `docs/helper-installation.md` | Helper-Daemon-Installation (launchctl, macOS 15.6) |
| `docs/watch-mode.md` | Watch-Mode-Doku |
| `docs/ai-context.md` | ⚠️ **VERALTET** (Log-Collection ist tot) |
| `docs/parser-analyse.md` | ⚠️ **VERALTET** (GetPlayerCardsV3 entfernt) |

**Lese-Reihenfolge für neue Agenten:** `CLAUDE.md` → `deck-advisor-roadmap-v4.md`
→ bei Scanner-Arbeit `deck-scan-roadmap-v3.md` + `helper-installation.md`.

## Aktueller Stand (2026-08-06)

### Erledigt
- ✅ Collection-Export stabil (Memory-Scan, Anker-basiert)
- ✅ Deck-Scan liest echte Decks mit Kartennamen (IL2CPP-Backref)
- ✅ Pattern-Scan nur noch Fallback
- ✅ Schreibschutz gegen schlechte Überschreibungen
- ✅ Sudo-Helper-Daemon (C, root, `task_for_pid`, UNIX-Socket)
- ✅ `install.sh` auf `launchctl load` umgestellt (macOS 15.6)
- ✅ Kombinierter Scan `scan --all` (Collection+Decks+Ranks, 1 Verbindung)
- ✅ Watch-Mode (`mtga-advisor watch`)
- ✅ Dedup-Fix in `scan_collection_detailed()`
- ✅ 256 Tests grün

### Offen (Roadmap v4, Priorität)
1. **New-Deck-Builder** (LLM-gestützt, strukturierte Ausgabe)
2. **Analyze-Flow** (bestehende Decks bewerten: fehlende Karten, Cuts, Craft)
3. **Collection-/Karten-Browser** (Suche, Filter)
4. **Drafts & Verlauf** (lokal speichern, vergleichen)
5. **Komfortweg für privilegierte macOS-Scans** (Helper läuft, aber Installation
   auf MacBook Air steht noch aus)

## Git-Workflow

- **Branch:** Entwicklung auf `feat/sudo-helper`. Merge in `main` **nur** nach
  explizitem "merge" von Frank.
- **Commit-Author:** `Jarvis <jarvis@local>` (Jarvis-Commits).
- **Force-Push:** Nur nach Franks explizitem OK.
- **Kanban:** Ein Board pro Repo (`mtga-advisor`), nicht pro Run/Sprint.

## Regeln für Agenten

1. **Collection nur via Memory-Scan** — nicht in Log-Collection investieren.
2. **Keine Secrets** — keine API-Keys/Tokens in Commits.
3. **Tests grün halten** — vor Commit `python3 -m pytest scanner/tests/ -q`.
4. **Helper-Installation braucht sudo** — Worker darf kein sudo ausführen,
   erzeugt Skripte/Doku für Frank.
5. **`find_blocks()` Dedup** nicht entfernen (schützt vor verfälschtem `max()`).
6. **Deployment-Ziel:** MacBook Air (User `frankhermann`, MTGA aktuell).
   Projektpfad dort: `/Users/frankhermann/Documents/mtga-advisor`.

## Referenzen

- Recherche-Notiz (Obsidian Vault): `05-Recherche/mtga-advisor-scanner-recherche-2026.md`
- Repo: `github.com/frankyh75/Mtga.advisor` (🔒 privat)
