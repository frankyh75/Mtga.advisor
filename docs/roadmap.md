# Roadmap

## Phase 0: Foundations
- Establish repository structure and documentation.
- Define data shapes for collection, decks, advisor output, and meta signals.
- Document advisor goals: cost-free access, optional LLM, and copy/paste support.

## Phase 1: Collection Export
- Support log-based MTG Arena collection export (local-only).
- Normalize card identifiers and quantities into `collection.json`.
- Validate parsing assumptions and edge cases across platforms.
- InventoryInfo-Snapshot erkennen (auch ohne PlayerInventory.GetPlayerCardsV3), cards bleiben unknown.
- run-report.json schreibt Log-Metadaten (Pfad, Groesse, mtime).

## Phase 2: Advisor Core (Rules + LLM Optional)
- Implement deterministic heuristics for deck improvement and wildcard guidance.
- Use collection + decklist + optional meta signals to produce advisory output.
- Support LLM integrations as opt-in enhancements or prompt workflows.
- Ensure explainability for each recommendation.
- See `docs/deck-analysis-roadmap.md` for the CardDB + deck analysis plan.

## Phase 3: Meta Signals & Updates
- Introduce optional ingestion of meta snapshots (no remote service required).
- Support versioned meta data and update history.
- Improve advisor weighting based on meta trends.

## Phase 4: Service Layer + MCP
- Add an optional service interface for multi-client access.
- Introduce MCP integration as an optional interface layer.
- Keep local-only and low-cost usage as first-class paths.

## Phase 5: Advisor UX Enhancements
- Improve advice formatting, summaries, and deck-centric views.
- Add quality checks for LLM-imported recommendations (when used).
- Preserve deterministic outputs as a baseline option.
