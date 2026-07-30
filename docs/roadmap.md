# Roadmap

## Phase 0: Foundations
- Establish repository structure and documentation.
- Define data shapes for collection, decks, advisor output, and meta signals.
- Document deterministic rules-first approach and constraints.

## Phase 1: Collection Export
- Support local MTG Arena collection export.
- macOS primary path: Memory-scan the running MTGA process and write `collection.json`.
- Log parser remains for wildcards, inventory deltas, metadata, and partial fallbacks.
- Normalize card identifiers and quantities into `collection.json`.
- Validate parsing assumptions and edge cases across platforms.
- Emit `run-report.json` and `validation-report.json` for diagnosability.

## Phase 1.1: Card Metadata Coverage
- **Status:** implemented for the current macOS/Epic install path.
- Resolve unknown Arena card IDs from scanner output.
- Improve local MTGA data path discovery and cache refresh behavior.
- Keep collection export valid even when metadata is incomplete.
- Current validation result: local `Raw_CardDatabase_*.mtga` resolves the previous 181 unknown IDs.

## Phase 2: Rule-Based Advisor
- Implement deterministic heuristics for deck improvement suggestions.
- Use collection + decklist + meta signals to produce advisory output.
- Ensure explainability for each recommendation.
- Start with a CLI-first MVP before GUI/premium flows.
- Implementation plan: `docs/phase-2-implementation-plan.md`.

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
