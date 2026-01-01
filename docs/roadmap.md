# Roadmap

## Phase 0: Foundations
- Establish repository structure and documentation.
- Define data shapes for collection, decks, advisor output, and meta signals.
- Document deterministic rules-first approach and constraints.

## Phase 1: Collection Export
- Support log-based MTG Arena collection export (local-only).
- Normalize card identifiers and quantities into `collection.json`.
- Validate parsing assumptions and edge cases across platforms.

## Phase 2: Rule-Based Advisor
- Implement deterministic heuristics for deck improvement suggestions.
- Use collection + decklist + meta signals to produce advisory output.
- Ensure explainability for each recommendation.

## Phase 3: Meta Signals & Updates
- Introduce optional ingestion of meta snapshots (no remote service required).
- Support versioned meta data and update history.
- Improve advisor weighting based on meta trends.

## Phase 4: Service Layer + MCP
- Add a remote service interface for multi-client access.
- Introduce MCP integration as an optional interface layer.
- Keep offline/local mode as a first-class path.

## Phase 5: Optional LLM Enhancements
- Add LLM-assisted explanations or summaries (opt-in).
- Preserve rule-based outputs as the authoritative source.
- Ensure no core functionality depends on LLM availability.
