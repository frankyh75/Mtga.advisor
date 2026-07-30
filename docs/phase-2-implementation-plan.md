# Phase 2 Implementation Plan

**Status:** planned
**Prerequisite:** Phase 1/1.1 collection export and local card metadata validation are green.

## Goal

Phase 2 delivers a deterministic, explainable advisor for a single imported deck and the local collection.

The first MVP is CLI-first. GUI, premium flows, meta imports, and multi-deck comparison come later.

## Non-Goals

- No live tracking.
- No overlay.
- No cloud account.
- No LLM-dependent recommendation path.
- No hard craft recommendations while wildcard completeness is `unknown`.
- No meta/winrate ranking in the MVP.

## Inputs

- `collection.json`: owned card quantities and completeness diagnostics.
- Local MTGA card database: name, rarity, type text, mana value, colors, set, legal/rebalance flags where available.
- `arena_deck.json`: normalized target decklist.
- Optional user parameters:
  - format: `standard|pioneer|historic|timeless|alchemy|brawl`
  - mode: `bo1|bo3`
  - advice style: `conservative` initially only

## Outputs

- `advisor-result.json`
- Human-readable CLI summary

Minimal result shape:

```json
{
  "schema": "advisor-result.v1",
  "deck": {
    "name": "Example Deck",
    "format": "standard"
  },
  "summary": {
    "completionScore": 83.3,
    "confidence": "high",
    "missingCards": 12,
    "hardCraftAdviceAllowed": false
  },
  "recommendations": [
    {
      "type": "missing-card",
      "arenaId": 12345,
      "name": "Example Card",
      "needed": 2,
      "owned": 1,
      "rarity": "rare",
      "reasons": ["missing-copies", "mainboard"]
    }
  ],
  "warnings": ["wildcards-unknown"],
  "evidence": ["collection.v1", "local-mtga-card-db"]
}
```

## Work Packages

### 1. Card Metadata API

**Goal:** provide a stable in-process lookup for Phase 2 rules.

Tasks:
- Extend `scanner.card_database` output with fields needed by advisor rules.
- Normalize rarity, colors, mana value, type text, token/digital/rebalanced flags.
- Add tests against synthetic current-schema MTGA DB.

Acceptance:
- Advisor can ask for metadata by Arena ID without touching SQLite directly.
- Missing metadata returns explicit `unknown`, never guessed values.

### 2. Deck Import

**Goal:** convert Arena text decklists to `arena_deck.json`.

Tasks:
- Parse Arena export text:
  - `Deck`
  - `Sideboard`
  - quantity + card name
- Resolve names via local card DB.
- Preserve unresolved lines in diagnostics.
- Support explicit format parameter.

Acceptance:
- `mtga-export deck import --file deck.txt --format standard --output out`
- Writes `arena_deck.json`.
- Fails conservatively when a card name maps ambiguously.

### 3. Completion Advisor

**Goal:** answer “how complete is this deck with my collection?”

Tasks:
- Compare required vs owned copies.
- Separate mainboard and sideboard.
- Compute completion score.
- Generate missing-card list with names and rarity.

Acceptance:
- `mtga-export advisor complete --collection out/collection.json --deck out/arena_deck.json --output out`
- Writes `advisor-result.json`.
- Produces deterministic output with tests.

### 4. Craft Guardrails

**Goal:** provide safe craft information without overclaiming.

Tasks:
- If `wildcards.completeness != complete`, mark craft advice as What-if only.
- Group missing cards by rarity.
- Include reasons, not rankings pretending to know meta strength.

Acceptance:
- Missing rares/mythics are visible.
- Hard “craft this now” language is not emitted unless wildcards are complete.

### 5. Format Checks

**Goal:** make legality a first-class advisor input.

Tasks:
- Introduce format config objects.
- Start with permissive MVP if local legality is incomplete.
- Emit `format-legality-unknown` warnings when required data is missing.

Acceptance:
- Advisor output includes confidence impact from unknown legality.
- Illegal cards can be flagged once local metadata supports it.

### 6. CLI UX

**Goal:** one clear Phase-2 command path.

Proposed commands:

```bash
python -m cli.main deck import --file deck.txt --format standard --output out
python -m cli.main advisor complete --collection out/collection.json --deck out/arena_deck.json --output out
```

Acceptance:
- Commands have useful `--help`.
- Errors are actionable.
- Output files are stable and diff-friendly.

## Phase 2 MVP Definition of Done

- `arena_deck.json` schema documented.
- `advisor-result.json` schema documented.
- CLI deck import works for a normal Arena-exported decklist.
- Completion advisor works from local `collection.json`.
- Missing cards are grouped by rarity.
- Every recommendation has machine-readable reasons.
- Wildcard-unknown state is respected.
- Tests cover parser, resolver, completion score, and diagnostics.

## Deferred After MVP

- Multi-deck comparison.
- Upgrade path optimization.
- Jump-In advisor.
- Meta snapshots.
- GUI cards/search views.
- Premium gating.
