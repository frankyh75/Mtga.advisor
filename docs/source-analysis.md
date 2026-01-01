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
  - Likely a utility library for parsing MTGA logs and extracting structured data.
  - Probably includes helpers for log watching and file location discovery.
- Reusable parts:
  - Approach to locating MTGA log files across OSes.
  - Log parsing heuristics for collection or match data.
  - Normalization patterns for card identifiers or names.
- Risks / limitations:
  - Fragility if it depends on undocumented log formats.
  - May be tightly coupled to a specific tool or workflow.
  - Potential maintenance burden if log formats change frequently.

## mtga_collection_managerv2
- Purpose:
  - Focused on exporting or managing MTGA collection data.
  - Likely provides a collection snapshot and inventory analysis.
- Reusable parts:
  - Strategies to derive owned cards from logs.
  - Handling of duplicates, card styles, or special printings.
  - UX ideas for presenting collection completeness.
- Risks / limitations:
  - Might rely on deprecated log fields or old client versions.
  - Could assume Windows-only paths or workflows.
  - Scope creep if it bundles unrelated features (economy, events, etc.).

## MTG_Meta_Deck_Suggestions
- Purpose:
  - Provides deck recommendations based on meta data or archetype lists.
  - Likely uses external data sources for decklists.
- Reusable parts:
  - Conceptual separation of meta-derived suggestions vs. owned-card constraints.
  - Filtering or scoring heuristics for deck suggestions.
  - Presentation format for recommended decklists and upgrade paths.
- Risks / limitations:
  - Meta dependence conflicts with Phase 1 deterministic-only scope.
  - External data sources may be unstable or rate-limited.
  - Recommendations may not align with purely log-derived data.

## MTGAHelper (tool/ecosystem; collection + progression ideas)
- Purpose:
  - Broad ecosystem tool for collection tracking, progression, and recommendations.
  - Likely combines log parsing with external services.
- Reusable parts:
  - Conceptual model for tracking progression and daily/weekly goals.
  - UX patterns for showing collection gaps and upgrade suggestions.
  - Ideas for summarizing constraints (wildcards, missing rares).
- Risks / limitations:
  - Heavy reliance on online accounts or external APIs.
  - Feature scope well beyond Phase 1 requirements.
  - Maintenance complexity and data privacy considerations.

## MTG Arena Tool (GUI tracker; learnings about UX + complexity)
- Purpose:
  - Desktop tracker with GUI overlays and collection analysis.
  - Emphasis on rich UI and multi-feature tracking.
- Reusable parts:
  - UX patterns for digestible summaries and quick recommendations.
  - Approaches to presenting match history vs. collection insights.
  - Interaction flows that keep advice explainable.
- Risks / limitations:
  - GUI complexity may not translate to a lightweight tool.
  - Overly coupled to Windows or specific packaging ecosystems.
  - Feature bloat can distract from core deterministic advice.

## mtga-tracker / arena tracker forks (general)
- Purpose:
  - Track matches, performance, and collection using logs.
  - Provide overlays and history dashboards.
- Reusable parts:
  - Log parsing patterns and error handling strategies.
  - Data modeling for match outcomes and deck usage.
  - Summarization ideas for recent performance.
- Risks / limitations:
  - Forks may diverge significantly and be stale.
  - Hidden dependencies on telemetry endpoints.
  - Varying licenses and quality of documentation.

## Any GUI-based MTGA tracker category (general)
- Purpose:
  - Offer visual dashboards for collection and match tracking.
  - Provide user-friendly summaries and recommendations.
- Reusable parts:
  - UI concepts for constraint-based deck selection.
  - Feedback patterns that build user trust (explanations, warnings).
  - Alerting or notification ideas for new log data.
- Risks / limitations:
  - GUI-first designs can mask underlying data assumptions.
  - May depend on overlays or permissions that complicate distribution.
  - Often optimized for power users, not minimal viable features.

## Any “log parsing” utility category (general)
- Purpose:
  - Provide parsers or watchers for MTGA log streams.
  - Convert raw logs into structured records.
- Reusable parts:
  - File tailing and incremental parsing strategies.
  - Robust parsing against partial writes and rotations.
  - Techniques for schema evolution or version tagging.
- Risks / limitations:
  - High fragility when log formats change.
  - Localization differences may break naive parsers.
  - Error handling may be insufficient for end-user reliability.

## Key Takeaways (Draft)
- Phase 1 should focus on local log-based collection export with minimal dependencies.
- Deterministic, rules-first advice must be explainable and tied to owned-card constraints.
- Reimplement file discovery and robust log tailing patterns; avoid heavy GUI patterns early.
- Avoid designs that require external accounts, telemetry APIs, or network services.
- Uncertain: which specific log fields consistently represent collection changes; needs confirmation.
- Uncertain: how to normalize card names/IDs across sets without external data; needs validation.
- Later phase: incorporate meta signals or decklist sources once base collection pipeline is stable.
- Later phase: consider richer UI dashboards only after data model is proven.
- Critical: document parsing assumptions and provide clear error reporting when logs are incomplete.
- Critical: keep scope tight to deterministic recommendations and user-owned cards.
