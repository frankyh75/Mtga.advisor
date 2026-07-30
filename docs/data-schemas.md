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

## run-report.json
```json
{
  "schema": "run-report.v1",
  "runId": "run-2025-01-01T12:00:00Z",
  "startedAt": "2025-01-01T12:00:00Z",
  "finishedAt": "2025-01-01T12:01:12Z",
  "source": "local-logs",
  "logs": ["C:/Users/.../Player.log", "C:/Users/.../Player-prev.log"],
  "outputs": {
    "collection": "out/collection.json",
    "rawSamples": "out/raw-samples"
  },
  "summary": {
    "cardsCount": 4,
    "wildcardsIncluded": true
  },
  "diagnostics": {
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

## validation-report.json
```json
{
  "schema": "validation-report.v1",
  "collection": "out/collection.json",
  "validation": {
    "valid": true,
    "errors": [],
    "warnings": ["unknown-card-ids"],
    "cardsCount": 7871,
    "totalCards": 16275,
    "invalidQuantities": {},
    "unknownCardIdsCount": 181,
    "unknownCardIds": ["47035"],
    "unknownCardIdsWithCounts": {
      "47035": 1
    },
    "unknownCardIdsTruncated": true,
    "anchors": [
      {
        "arenaId": 105645,
        "name": "Quantum Reduction",
        "expected": 1,
        "actual": 1,
        "ok": true
      }
    ]
  }
}
```

## arena_deck.json (parsed deck)
```json
{
  "schema": "arena-deck.v1",
  "deckId": "local-hash",
  "name": "Mono-Red Aggro",
  "format": "standard",
  "importedAt": "2025-01-01T12:00:00Z",
  "mainboard": [
    {
      "arenaId": 12345,
      "name": "Lightning Strike",
      "count": 4,
      "rarity": "common",
      "set": "DMU",
      "collectorNumber": "137"
    }
  ],
  "sideboard": [
    {
      "arenaId": 67890,
      "name": "Abrade",
      "count": 2
    }
  ],
  "diagnostics": {
    "unresolved": [],
    "ambiguous": [],
    "warnings": []
  }
}
```

## advisor_result.json
```json
{
  "schema": "advisor-result.v1",
  "generatedAt": "2025-01-01T12:00:00Z",
  "deck": {
    "deckId": "local-hash",
    "name": "Mono-Red Aggro",
    "format": "standard"
  },
  "summary": {
    "completionScore": 83.3,
    "confidence": "high",
    "missingCards": 12,
    "missingUniqueCards": 4,
    "hardCraftAdviceAllowed": false
  },
  "recommendations": [
    {
      "type": "missing-card",
      "arenaId": 11111,
      "name": "Play with Fire",
      "needed": 2,
      "owned": 2,
      "required": 4,
      "rarity": "uncommon",
      "zones": ["mainboard"],
      "reasons": ["missing-copies", "mainboard"],
      "craftAdvice": "what-if"
    }
  ],
  "missingByRarity": {
    "rare": 4,
    "uncommon": 8
  },
  "warnings": ["wildcards-unknown"],
  "evidence": ["collection.v1", "arena-deck.v1", "local-mtga-card-db"]
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
