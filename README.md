# Mtga.advisor

Mtga.advisor is an early-stage, private project focused on MTG Arena deck advising with collection-aware and meta-aware recommendations.

## Project Notes
- This is a private repository in an early planning phase.
- The focus is MTG Arena deck advising and deterministic recommendations.
- Cost-free access is a priority; LLM usage is optional (none/free/paid).
- Prompt-based workflows should remain available for free users.

## Scope-Lock (Phase 0/1)
- **Phase 0:** Planung, Quellenanalyse, und Festlegung der technischen Richtung. Kein produktiver Code erforderlich.
- **Phase 1:** Lokaler, deterministischer CLI-Export der MTGA-Sammlung aus Logs. Keine Online-Services, keine Meta-Logik, keine UI.
- **Explizit:** Keine Advisor-Logik in Phase 0/1.
- **Nicht in Scope:** Live-Tracking, Hintergrunddienste, externe Accounts, oder Meta-basierte Empfehlungen.

## Advisor Direction (Post-Phase 1)
- Advisor ist Kernziel nach Phase 1, mit Rules-Engine als Baseline.
- LLM-Integration bleibt optional; Prompt/Copy-Paste Workflows sind ein First-Class Pfad.
- Roadmap fÃ¼r Deck-Analyse + CardDB: `docs/deck-analysis-roadmap.md`.

## CLI-Kommandos
- `mtga-export collection`: Exportiert die lokale MTGA-Sammlung aus Logs in die definierten Output-Artefakte.
- `mtga-export decks`: Zeigt eine kurze Deckvorschau aus Logs (Quelle, Format, Kartenanzahl).
- `mtga-export carddb import`: Importiert eine lokale Scryfall Bulk JSON Datei in SQLite.
- `mtga-export carddb info`: Zeigt CardDB Statistiken und Metadaten.
- `mtga-export deck-analysis`: Erzeugt `out/deck-analysis/*.json` und `out/deck-analysis-summary.json`.
- `mtga-export serve`: Startet einen lokalen Server für die Ausgabe-Artefakte (nur lokal, ohne Online-Services).

## Nutzung (Lokal)
- Sammlung exportieren (Auto-Discovery der Logs auf macOS/Windows): `python -m cli.main collection --output out`
- Explizite Log-Pfade angeben (falls Auto-Discovery scheitert): `python -m cli.main collection --log /pfad/zu/Player.log --log /pfad/zu/Player-prev.log --output out`
- macOS Standardpfad: `~/Library/Logs/Wizards of the Coast/MTGA/`; Windows LocalLow: `%USERPROFILE%/AppData/LocalLow/Wizards Of The Coast/MTGA/`.
- Artefakte im Browser anzeigen: `python -m cli.main serve --output out --port 8000` und dann `http://127.0.0.1:8000/` öffnen.

## Architecture Decision Records (ADRs)
- [ADR 0001: Tech-Stack](docs/adr/0001-tech-stack.md)
- [ADR 0002: CLI-UX](docs/adr/0002-cli-ux.md)
- [ADR 0003: Output-Artefakte](docs/adr/0003-output-artefakte.md)
- [ADR 0004: Parser-Pipeline](docs/adr/0004-parser-pipeline.md)
- [ADR 0005: Advisor-Core und LLM-Option](docs/adr/0005-advisor-core-llm-option.md)
