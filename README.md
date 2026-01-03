# Mtga.advisor

Mtga.advisor is an early-stage, private project focused on MTG Arena deck advising with collection-aware and meta-aware recommendations.

## Project Notes
- This is a private repository in an early planning phase.
- The focus is MTG Arena deck advising and deterministic recommendations.
- LLM usage is optional and not required for core functionality.

## Scope-Lock (Phase 0/1)
- **Phase 0:** Planung, Quellenanalyse, und Festlegung der technischen Richtung. Kein produktiver Code erforderlich.
- **Phase 1:** Lokaler, deterministischer CLI-Export der MTGA-Sammlung aus Logs. Keine Online-Services, keine Meta-Logik, keine UI.
- **Nicht in Scope:** Live-Tracking, Hintergrunddienste, externe Accounts, oder Meta-basierte Empfehlungen.

## Architecture Decision Records (ADRs)
- [ADR 0001: Tech-Stack](docs/adr/0001-tech-stack.md)
- [ADR 0002: CLI-UX](docs/adr/0002-cli-ux.md)
- [ADR 0003: Output-Artefakte](docs/adr/0003-output-artefakte.md)
- [ADR 0004: Parser-Pipeline](docs/adr/0004-parser-pipeline.md)
