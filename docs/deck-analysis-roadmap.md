# Deck Analysis & CardDB Roadmap (Plan)

This document captures the architecture plan and roadmap to reach **concrete deck analysis** with card understanding, synergy detection, and recommendations. It is written to be readable by humans and AI tools.

## Executive Summary
- Add an offline CardDB (Scryfall bulk → SQLite) to enrich MTGA decklists with real card meaning.
- Map MTGA `cardId` to CardDB entries, track `mappingCoverage`, and warn if coverage < 90%.
- Export `deck-analysis.json` per deck with enriched cards, synergies, and recommendations.
- Keep the core pipeline offline-first; updates to CardDB are optional/manual.

## Goals
- Analyze a **specific deck** with clear card understanding (types, rules text, costs, keywords).
- Recognize **synergies** and produce **actionable recommendations**.
- Stay **offline-first**: no mandatory network calls in the core workflow.

## Non-Goals (Phase 1.x)
- No live syncing, no always-on daemon.
- No external API calls in the core parser flow.
- No UI work beyond existing JSON outputs.

## Inputs
- MTGA logs (`Player.log`, `Player-prev.log`) → deck lists + collection snapshot.
- Local CardDB derived from **Scryfall Bulk Data** (offline cache).

## Outputs (New)
- `deck-analysis.json` (per deck) with:
  - `deckId`, `name`, `format`
  - card list with enriched fields (name, mana_cost, types, keywords, oracle_text)
  - `mappingCoverage` and `unknownCards`
  - `synergies[]` and `recommendations[]`
- Optional: `deck-analysis-summary.json` (index of analyzed decks)
- CLI: `mtga-export deck-analysis` writes outputs to `out/deck-analysis/` and `out/deck-analysis-summary.json`.
- CLI: `mtga-export carddb import` and `mtga-export carddb info` manage local SQLite CardDB.

### Example: deck-analysis.json
```json
{
  "schema": "deck-analysis.v1",
  "deckId": "49ba3f72-b079-4a9d-8e41-56c457be1fc0",
  "name": "Learn from the Land",
  "format": "Standard",
  "analyzedAt": "2026-01-23T12:00:00Z",
  "mappingCoverage": 0.93,
  "unknownCards": [999999],
  "cards": [
    {
      "cardId": 70401,
      "name": "Lightning Strike",
      "manaCost": "{1}{R}",
      "types": ["Instant"],
      "oracleText": "Lightning Strike deals 3 damage to any target.",
      "keywords": ["damage", "burn"],
      "quantity": 4
    }
  ],
  "synergies": [
    {
      "id": "burn-density.v1",
      "score": 0.72,
      "reason": "High density of cheap direct damage spells supports aggressive game plan."
    }
  ],
  "recommendations": [
    {
      "type": "add",
      "cardId": 123456,
      "count": 2,
      "reason": "Improves early interaction; fits mana curve.",
      "confidence": "medium"
    }
  ],
  "diagnostics": {
    "coverageWarning": false
  }
}
```

## Architecture Overview
### Data Flow
1) **Log Parse**
   - Extract deck lists (`Decks` object in logs).
   - Extract collection snapshot (`GetPlayerCardsV3`) when present.

2) **CardDB (Offline)**
   - Download Scryfall Bulk data **manually or optional update command**.
   - Build local CardDB (SQLite recommended) with indexes.

3) **Mapping**
   - Map MTGA `cardId` → CardDB entry.
   - Track coverage percentage.

4) **Deck Analysis**
   - Enrich cards with card data.
   - Run synergy rules.
   - Generate recommendations + explanations.

5) **Export**
   - Write `deck-analysis.json` per deck.

## CardDB Strategy
### Source: Scryfall Bulk Data
- Use bulk endpoint metadata to obtain current `download_uri`.
- Primary datasets:
  - **Oracle Cards** for rules text normalization.
  - **Default Cards** for printing-specific fields (if needed).

### Storage
- **SQLite** (preferred): fast local lookup + indexing.
- JSON kept only as **import source** or cache.

### Suggested Indexes
- `oracle_id`
- `name`
- `scryfall_id`
- `collector_number` + `set`
- `arena_id` or equivalent (if present in bulk data)

## MTGA cardId → Scryfall Mapping
### Strategy A (Preferred)
- Use Scryfall `arena_id` (if present) for direct mapping.

### Strategy B (Fallback)
- Use local mapping table (imported from known MTGA → Scryfall mappings).

### Risks
- Alchemy variants, tokens, and rebalanced cards may not map cleanly.
- Coverage may be <100%; must warn and label unknowns.

## Completeness & Warnings
- `mappingCoverage` required in output.
- Warn if coverage < 90%.
- Never claim full understanding if mapping is partial.

## Roadmap
### Phase 1.5 – Deck Extraction Fallback (done)
- Decklists extracted when snapshot missing.
- `cardsSeenInDecks` + `decks` output for fallback.

### Phase 2 – CardDB Integration
- Add CardDB builder/import command.
- Add deck → card enrichment layer.
- Output `deck-analysis.json` (no synergies yet).
- Implement deck-analysis CLI with CardDB lookup (SQLite, arena_id mapping).

### Phase 2.5 – Synergy Rules (initial set)
- Implement ~10 rule-based synergy detectors.
- Add recommendations with clear reasoning strings.

### Phase 3 – Advisor Expansion
- Format-aware tuning.
- Optional LLM add-on (non-core), rule-first fallback.

## Open Questions (Must Answer Before Implementation)
1) Deck source priority: saved decks vs last played vs event decks?
2) Analyze only MainDeck or include Sideboard/CommandZone?
3) CardDB storage: SQLite preferred or JSON-only?
4) Warn when mapping coverage < 90%?
5) Output language for analysis: German or English?
6) How many synergy rules to start with (5/10/20)?

## Answers (Confirmed)
1) Deck source priority: Saved Decks > Last Played; Event Decks optional (explicitly marked).
2) Zones: MainDeck + Sideboard; CommandZone/Companions only for formats that use them.
3) CardDB storage: SQLite.
4) Warn when mapping coverage < 90%: Yes, include `mappingCoverage` in output.
5) Output language: German (user-facing), internal IDs/keywords in English.
6) Initial synergy rules: 10.
