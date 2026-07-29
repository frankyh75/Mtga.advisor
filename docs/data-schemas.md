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
