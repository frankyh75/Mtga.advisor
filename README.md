# Mtga.advisor

Mtga.advisor is an early-stage, private project focused on MTG Arena deck advising with collection-aware and meta-aware recommendations.

## Project Notes
- This is a private repository in an active implementation phase.
- The current focus is the collection ingestion pipeline for MTG Arena.
- LLM usage is optional and not required for core functionality.

## Scope-Lock (Phase 0/1)
- **Phase 0:** Planung, Quellenanalyse und Festlegung der technischen Richtung.
- **Phase 1:** Lokale, deterministische Collection-Erfassung als Grundlage fuer spaetere Empfehlungen.
- **Aktueller Plan:** Log-Parsing bleibt fuer Deltas und Metadaten erhalten, aber die Vollcollection soll auf macOS per Memory-Scanning aus dem laufenden MTGA-Prozess kommen.
- **Explizit:** Keine Advisor-Logik in Phase 0/1.
- **Nicht in Scope:** Live-Tracking, Hintergrunddienste, externe Accounts oder Meta-basierte Empfehlungen.

## CLI-Kommandos
- `mtga-export run`: Kanonischer Phase-1-Export. Nutzt auf macOS den Memory-Scan, sonst den Log-Export.
- `mtga-export collection`: Exportiert die lokale MTGA-Sammlung aus Logs in die definierten Output-Artefakte.
- `mtga-export scan`: Exportiert die macOS-Vollcollection per Memory-Scan aus dem laufenden MTGA-Prozess.
- `mtga-export validate`: Validiert ein bestehendes `collection.json`-Artefakt.
- `mtga-export deck import`: Importiert eine Arena-Textdeckliste als `arena_deck.json`.
- `mtga-export advisor complete`: Berechnet Deck-Completion und fehlende Karten.
- `mtga-export serve`: Startet das lokale Dashboard fuer die Ausgabe-Artefakte.

## Nutzung (Lokal)
- Kanonischer Export: `sudo -E .venv/bin/python -m cli.main run --output out`
- Log-basierter Export: `python -m cli.main collection --output out`
- Explizite Log-Pfade angeben, falls Auto-Discovery scheitert: `python -m cli.main collection --log /pfad/zu/Player.log --log /pfad/zu/Player-prev.log --output out`
- Lokales Dashboard starten: `python -m cli.main serve --output out --port 8000` und dann `http://127.0.0.1:8000/` oeffnen.
- macOS Memory-Scan: MTGA starten, in die Decks-Ansicht wechseln, dann `sudo -E .venv/bin/python -m cli.main scan --output out-memory`
- Bestehenden Export validieren: `python -m cli.main validate --output out-memory` schreibt zusätzlich `validation-report.json`.
- Karten-DB-Cache bei der Validierung neu aufbauen: `python -m cli.main validate --output out-memory --refresh-card-db`
- Der Karten-DB-Cache nutzt `arena-id-lookup.v2`; alte/Scryfall-Caches werden ersetzt, sobald die lokale MTGA-DB verfügbar ist.
- Deck importieren: `python -m cli.main deck import --file deck.txt --format standard --output out`
- Completion berechnen: `python -m cli.main advisor complete --collection out/collection.json --deck out/arena_deck.json --output out`
- Voller Phase-2-Flow: erst `run`, dann `deck import`, dann `advisor complete`, danach `serve`.

## Lokales Dashboard
- Das Dashboard ist bewusst ein lokaler Web-Viewer statt einer nativen App: weniger macOS-Signierungs-/TCC-Komplexitaet, leichter testbar, und vom Menubar-Helper spaeter direkt oeffenbar.
- Es zeigt `collection.json`, `run-report.json`, `arena_deck.json` und `advisor-result.json` in einer zusammengefassten Ansicht.
- API-Endpunkte: `/api/collection`, `/api/run-report`, `/api/deck`, `/api/advisor-result`.
- Das Dashboard erzeugt keine Empfehlungen selbst; `advisor-result.json` bleibt das kanonische regelbasierte Ergebnis.

## Phase-1 Abschlussstand
- Der macOS-Memory-Scanner sucht mehrere Ankerkarten in einem Speicher-Durchlauf und reduziert damit die Scan-Zeit gegenüber einem separaten Vollscan pro Anker.
- `collection.json` bleibt das kanonische Artefakt für spätere Advisor-Logik.
- `run-report.json` enthält Scan-Statistiken, Anchor-Matches und Validierungsergebnis.
- Wildcards bleiben beim reinen Memory-Scan bewusst `unknown`; harte Craft-Empfehlungen sind erst erlaubt, wenn Wildcards aus Logs/Inventory vollständig validiert sind.
- Unbekannte Karten-IDs werden im `validation-report.json` vollständig mit Mengen ausgewiesen; diese blockieren nicht den Collection-Export, aber spätere Namen-/Metadaten-Funktionen.
- `sudo` ist aktuell weiterhin erforderlich, weil `pymem-osx` über `task_for_pid()` auf den MTGA-Prozess zugreift.

## Architektur
- `parser/`: Log-basierte Extraktion von Snapshots, Deltas und Metadaten.
- `scanner/`: macOS Memory-Scanning mit `pymem-osx`, Pattern-Scan und Karten-Datenbank.
- `docs/parser-wechsel-macos-scanner.md`: Begründung fuer den Wechsel zum Memory-Scanning.
- `docs/macos-scanner-testplan.md`: Testplan fuer die Scanner-Validierung auf macOS.
- `docs/phase-2-implementation-plan.md`: CLI-first Plan fuer den regelbasierten Advisor.

## Architecture Decision Records (ADRs)
- [ADR 0001: Tech-Stack](docs/adr/0001-tech-stack.md)
- [ADR 0002: CLI-UX](docs/adr/0002-cli-ux.md)
- [ADR 0003: Output-Artefakte](docs/adr/0003-output-artefakte.md)
- [ADR 0004: Parser-Pipeline](docs/adr/0004-parser-pipeline.md)
