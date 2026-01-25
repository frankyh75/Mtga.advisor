# Data Schemas (Draft)

These are illustrative JSON examples (not formal JSON Schema). They are intended to be stable, extensible shapes that can evolve with versioning.

## collection.json
```json
{
  "schema": "collection.v1",
  "source": "local-logs",
  "cards": { "12345": 2, "67890": 1 },
  "wildcards": {
    "common": 12,
    "uncommon": 9,
    "rare": 4,
    "mythic": 1
  },
  "diagnostics": {
    "collectionCompleteness": "complete|partial|unknown",
    "completeness": {
      "cards": "complete|partial|unknown",
      "wildcards": "complete|partial|unknown",
      "source": "complete|partial|unknown"
    },
    "warnings": ["<warning-code>"],
    "evidence": ["<event-type>"]
  },
  "cardsSeenInDecks": [12345, 67890],
  "decks": [
    {
      "id": "deck-uuid",
      "name": "Mono-Red Aggro",
      "format": "Standard",
      "mainDeck": [{ "cardId": 12345, "quantity": 4 }],
      "sideboard": [{ "cardId": 67890, "quantity": 2 }],
      "commandZone": [],
      "companions": []
    }
  ]
}
```

## run-report.json
```json
{
  "schema": "run-report.v1",
  "runId": "run-2025-01-01T12:00:00Z",
  "startedAt": "2025-01-01T12:00:00Z",
  "finishedAt": "2025-01-01T12:01:12Z",
  "source": "local-logs",
  "logs": ["C:/Users/.../Player.log", "C:/Users/.../Player-prev.log"],
  "logMetadata": [
    {
      "path": "C:/Users/.../Player.log",
      "sizeBytes": 123456,
      "mtime": "2025-01-01T12:00:00Z"
    }
  ],
  "outputs": {
    "collection": "out/collection.json",
    "rawSamples": "out/raw-samples"
  },
  "summary": {
    "cardsCount": 4,
    "wildcardsIncluded": true
  },
  "diagnostics": {
    "collectionCompleteness": "complete|partial|unknown",
    "completeness": {
      "cards": "complete|partial|unknown",
      "wildcards": "complete|partial|unknown",
      "source": "complete|partial|unknown"
    },
    "warnings": ["<warning-code>"],
    "evidence": ["<event-type>"]
  }
}
```

## arena_deck.json (parsed deck)
```json
{
  "schema_version": "0.1",
  "deck_id": "local-uuid-or-hash",
  "name": "Mono-Red Aggro",
  "format": "standard",
  "updated_at": "2025-01-01T12:00:00Z",
  "mainboard": [
    {
      "arena_id": 12345,
      "name": "Lightning Strike",
      "count": 4
    }
  ],
  "sideboard": [
    {
      "arena_id": 67890,
      "name": "Abrade",
      "count": 2
    }
  ],
  "metadata": {
    "source": "mtga_export",
    "notes": "optional user notes"
  }
}
```

## advisor_result.json
```json
{
  "schema_version": "0.1",
  "generated_at": "2025-01-01T12:00:00Z",
  "deck_id": "local-uuid-or-hash",
  "summary": {
    "score": 0.72,
    "confidence": "medium",
    "notes": "Rule-based evaluation only."
  },
  "recommendations": [
    {
      "type": "add",
      "arena_id": 11111,
      "name": "Play with Fire",
      "count": 2,
      "reason": "Improves early-game interaction",
      "constraints": {
        "requires_wildcards": false,
        "max_copies": 4
      }
    }
  ],
  "explanations": [
    {
      "rule_id": "curve.balance.v1",
      "text": "Deck is light on one-drops; add early threats."
    }
  ]
}
```

## deck-analysis.json
```json
{
  "schema": "deck-analysis.v1",
  "deckId": "deck-uuid",
  "name": "Example Deck",
  "format": "Standard",
  "source": "last_played",
  "analyzedAt": "2026-01-23T12:00:00Z",
  "mappingCoverage": 0.93,
  "unknownCards": [999999],
  "cards": [
    {
      "cardId": 70401,
      "scryfallId": "scryfall-uuid",
      "oracleId": "oracle-uuid",
      "name": "Lightning Strike",
      "manaCost": "{1}{R}",
      "typeLine": "Instant",
      "types": ["Instant"],
      "oracleText": "Lightning Strike deals 3 damage to any target.",
      "keywords": ["Burn"],
      "quantity": 4
    }
  ],
  "synergies": [
    {
      "id": "artifact-payoff.v1",
      "score": 0.25,
      "reason": "Multiple artifact payoffs suggest an artifact-centered game plan."
    }
  ],
  "recommendations": [
    {
      "id": "interaction-low.v1",
      "type": "add",
      "reason": "Interaction count is low; add removal or counterspells.",
      "confidence": "medium"
    }
  ],
  "diagnostics": {
    "coverageWarning": false
  }
}
```

## meta_signals.json
```json
{
  "schema_version": "0.1",
  "captured_at": "2025-01-01T12:00:00Z",
  "format": "standard",
  "source": {
    "type": "manual_import",
    "url": "https://example.com/meta-snapshot"
  },
  "archetypes": [
    {
      "name": "Mono-Red Aggro",
      "share": 0.12,
      "key_cards": [
        { "arena_id": 12345, "name": "Lightning Strike" }
      ],
      "notes": "High prevalence in BO1 queues."
    }
  ],
  "matchups": [
    {
      "archetype": "Mono-Red Aggro",
      "vs": "Azorius Control",
      "win_rate": 0.46,
      "sample_size": 1200
    }
  ]
}
```
