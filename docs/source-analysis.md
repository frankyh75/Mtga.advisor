# Source Analysis (Placeholder)

## Research Questions
- Which MTGA log files, directories, and filename patterns are most reliable across updates (e.g., Player.log vs. other telemetry)?
- What fields in logs are sufficient for reconstructing collection state without relying on unofficial APIs?
- How do existing tools handle rotation, reprints, and card name normalization?
- What event and match-level data is consistently logged and useful for deterministic advice?
- What is the minimal viable set of parsed data needed for Phase 1 deck advice?
- How do tools distinguish between owned cards, crafted cards, and temporary/event-provided cards?
- What are common failure modes in log parsing (format drift, partial logs, localization) and how are they mitigated?
- Which tools provide purely local/offline workflows vs. services that require accounts or network calls?
- What licensing constraints or design choices limit reuse of ideas (not code) for a new implementation?
- What UX patterns help users trust deterministic recommendations (e.g., explainability, constraint summaries)?

## Selection Criteria
- Log parsing approach is resilient to format changes and clearly documents assumptions.
- Collection export can be derived from logs without external services or APIs.
- Deterministic rules are explainable and map cleanly to user-visible constraints.
- Scope is tight enough for Phase 1 (no live meta dependency, minimal telemetry).
- Implementation complexity is reasonable for a lightweight, maintainable tool.
- Cross-platform viability or a clear path for platform-specific limitations.
- Data model aligns with MTGA card identifiers and supports set/format filtering.
- Risks are understood and mitigations are practical (e.g., fallbacks, error reporting).

## mtga-utils
- Purpose:
  - Utility library for parsing MTGA logs and extracting structured data.
  - Includes helpers for log watching and file location discovery across OSes.
- Reusable parts:
  - Approach to locating MTGA log files across OSes.
  - Log parsing heuristics for collection or match data that can be scoped to collection snapshots.
  - Normalization patterns for card identifiers or names.
- Ignore/defer:
  - Any integrations beyond local log reading or dependencies on upstream services.
  - Code paths tied to overlays or live telemetry that exceed single-run export needs.
- Risks / limitations:
  - Coupling to undocumented log formats; must add schema checks and graceful degradation.
  - Tight coupling to a specific tool or workflow can hide assumptions; requires audit before reuse.
  - Maintenance risk if log formats change without version tagging.
- Phase 1 Relevance
  - Useful for path discovery and tailing patterns; adapt to a one-shot export instead of continuous watching.
  - Borrow defensive parsing approaches while stripping non-collection logic.
  - Skip any UI or network hooks to keep the workflow local-only.

## mtga_collection_managerv2
- Purpose:
  - Focused on exporting or managing MTGA collection data.
  - Provides collection snapshots and inventory analysis rooted in log data.
- Reusable parts:
  - Strategies to derive owned cards from logs, including baseline vs. delta handling.
  - Handling of duplicates, card styles, or special printings where present in logs.
  - UX ideas for presenting collection completeness without external metadata.
- Ignore/defer:
  - Any integrations with external deck sources or economy simulators.
  - Platform-specific shortcuts (e.g., Windows-only assumptions) that reduce portability.
- Risks / limitations:
  - Possible reliance on deprecated log fields; confirm against current log samples.
  - Windows-first path assumptions could break macOS support; requires abstraction.
  - Bundled features (economy/event analytics) add noise for a minimal export path.
- Phase 1 Relevance
  - Reuse collection derivation patterns, focusing on snapshot completeness and delta fallbacks.
  - Extract file path discovery pieces but harden for macOS/Linux parity.
  - Drop bundled economy or deck analytics to keep the export narrow.

## MTG_Meta_Deck_Suggestions
- Purpose:
  - Provides deck recommendations based on meta data or archetype lists.
  - Uses external data sources for decklists.
- Reusable parts:
  - Conceptual separation of meta-derived suggestions vs. owned-card constraints.
  - Filtering or scoring heuristics for deck suggestions.
  - Presentation format for recommended decklists and upgrade paths.
- Ignore/defer:
  - All meta-driven recommendation logic for Phase 1 (out of scope).
  - Dependencies on external data feeds or account-based services.
- Risks / limitations:
  - Meta dependence conflicts with deterministic-only Phase 1 scope.
  - External data sources may be unstable or rate-limited, creating maintenance risk.
  - Recommendations cannot be validated against purely log-derived data.
- Phase 1 Relevance
  - Only keep the separation principle between owned cards and candidate decks as a conceptual guardrail.
  - All functional code is deferred; no direct reuse while Phase 1 remains offline and deterministic.

## MTGAHelper (tool/ecosystem; collection + progression ideas)
- Purpose:
  - Broad ecosystem tool for collection tracking, progression, and recommendations.
  - Combines log parsing with external services and online accounts.
- Reusable parts:
  - Conceptual model for tracking progression and daily/weekly goals.
  - UX patterns for showing collection gaps and upgrade suggestions without overwhelming detail.
  - Ideas for summarizing constraints (wildcards, missing rares).
- Ignore/defer:
  - Account-based sync and any telemetry or analytics backends.
  - Complex progression tracking beyond a single collection export.
- Risks / limitations:
  - Heavy reliance on online accounts or external APIs conflicts with offline scope.
  - Feature scope goes beyond collection export, increasing maintenance load.
  - Data privacy considerations make reuse unsuitable for a local-first tool.
- Phase 1 Relevance
  - Only the constraint summarization UX concepts translate; implementation stays local and minimal.
  - Skip progression and account sync entirely until after a stable offline export exists.

## MTG Arena Tool (GUI tracker; learnings about UX + complexity)
- Purpose:
  - Desktop tracker with GUI overlays and collection analysis.
  - Emphasis on rich UI and multi-feature tracking.
- Reusable parts:
  - UX patterns for digestible summaries and quick recommendations.
  - Approaches to presenting match history vs. collection insights.
  - Interaction flows that keep advice explainable.
- Ignore/defer:
  - Overlay integrations and GUI-heavy workflows.
  - Packaging or installer complexity tied to Windows-specific ecosystems.
- Risks / limitations:
  - GUI complexity may not translate to a lightweight tool.
  - Overly coupled to Windows or specific packaging ecosystems.
  - Feature bloat can distract from core deterministic advice.
- Phase 1 Relevance
  - Use only lightweight UX ideas for summarizing collection status; no overlays.
  - Defer GUI and installer patterns until after CLI export is stable.

## mtga-tracker / arena tracker forks (general)
- Purpose:
  - Track matches, performance, and collection using logs.
  - Provide overlays and history dashboards.
- Reusable parts:
  - Log parsing patterns and error handling strategies.
  - Data modeling for match outcomes and deck usage.
  - Summarization ideas for recent performance.
- Ignore/defer:
  - Match performance analytics and overlays.
  - Telemetry endpoints or background sync assumptions.
- Risks / limitations:
  - Forks may diverge significantly and be stale.
  - Hidden dependencies on telemetry endpoints.
  - Varying licenses and quality of documentation.
- Phase 1 Relevance
  - Reuse only defensive log parsing patterns applicable to collection events.
  - Ignore match-tracking logic and overlays; focus on file handling and error surfacing.

## Any GUI-based MTGA tracker category (general)
- Purpose:
  - Offer visual dashboards for collection and match tracking.
  - Provide user-friendly summaries and recommendations.
- Reusable parts:
  - UI concepts for constraint-based deck selection.
  - Feedback patterns that build user trust (explanations, warnings).
  - Alerting or notification ideas for new log data.
- Ignore/defer:
  - GUI-first flows, overlays, and notification systems.
  - Permissions or install requirements that complicate a CLI-only experience.
- Risks / limitations:
  - GUI-first designs can mask underlying data assumptions.
  - May depend on overlays or permissions that complicate distribution.
  - Often optimized for power users, not minimal viable features.
- Phase 1 Relevance
  - Only retain communication patterns (warnings, explanations) for CLI messaging.
  - Defer dashboards and notifications until after export correctness is proven.

## Any “log parsing” utility category (general)
- Purpose:
  - Provide parsers or watchers for MTGA log streams.
  - Convert raw logs into structured records.
- Reusable parts:
  - File tailing and incremental parsing strategies.
  - Robust parsing against partial writes and rotations.
  - Techniques for schema evolution or version tagging.
- Ignore/defer:
  - Long-running daemons or services; Phase 1 is single-run.
  - Features tied to match telemetry or analytics storage.
- Risks / limitations:
  - High fragility when log formats change.
  - Localization differences may break naive parsers.
  - Error handling may be insufficient for end-user reliability.
- Phase 1 Relevance
  - Adopt incremental parsing with explicit rotation handling to keep exports stable.
  - Apply schema version tagging or validation where possible to detect drift early.

## Key Takeaways (Draft)
- Phase 1 centers on local log-based collection export with no external dependencies or accounts.
- Defensive log parsing and rotation handling from utility repos are useful; GUI, overlay, and meta-driven logic are deferred.
- Collection derivation patterns are valuable only when clearly tied to current log fields; stale or Windows-only assumptions must be audited.
- Meta recommendations, progression tracking, and online services remain out of scope until after a reliable offline export is proven.
- Card ID normalization and authoritative collection events still need validation against current logs; document assumptions and fail loudly on drift.
