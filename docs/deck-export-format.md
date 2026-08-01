# Complete Deck List Export Format

## Overview

This document defines the format for complete deck list exports.

## Source

### DeckSummaries (existing)
- **Event**: `StartHook`
- **Data**: Deck metadata only (name, deck_id, description, attributes, legalities, mana)
- **Format**: `decks.json`
- **Limitation**: Does NOT contain actual card composition

### Complete Deck Lists (new)
- **Source**: `Deck.Updated` or similar events that contain the actual cards in each deck
- **Format**: Individual JSON files per deck or container format
- **Purpose**: Full deck composition with all cards

## Format

### Single Deck File

```json
{
  "schema": "deck.v1",
  "deckId": "abc123",
  "name": "Mono-Red Aggro",
  "description": "Fast aggro deck",
  "mana": "R",
  "cards": {
    "mainboard": [
      {
        "cardId": 100001,
        "name": "Lightning Strike",
        "count": 4
      },
      {
        "cardId": 100002,
        "name": "Shock",
        "count": 4
      }
    ],
    "sideboard": [
      {
        "cardId": 100003,
        "name": "Flame Channel",
        "count": 2
      }
    ]
  },
  "attributes": {
    "format": "standard",
    "isFavorite": true
  },
  "exportedAt": "2025-01-02T00:00:00Z"
}
```

### Container Format (all decks in one file)

```json
{
  "schema": "decks-container.v1",
  "exportedAt": "2025-01-02T00:00:00Z",
  "decks": [
    {
      "deckId": "abc123",
      "name": "Mono-Red Aggro",
      "description": "Fast aggro deck",
      "mana": "R",
      "cards": {
        "mainboard": [...],
        "sideboard": [...]
      }
    }
  ]
}
```

## File Structure

### Option 1: One file per deck
```
out/
├── decks.json              # List of all deck IDs and metadata
├── deck-abc123.json        # Complete deck list for deck with ID abc123
├── deck-def456.json
└── ...
```

### Option 2: Container file
```
out/
├── decks.json              # List of all deck IDs and metadata
└── decks-all.json          # Container with all complete deck lists
```

### Option 3: Hybrid (recommended)
```
out/
├── decks.json              # List of all deck IDs and metadata
├── decks/
│   ├── index.json          # Container index with all deck summaries
│   ├── deck-abc123.json    # Complete deck list for deck with ID abc123
│   ├── deck-def456.json
│   └── ...
```

## Implementation Plan

1. **Identify the source event**: Find which MTGA event contains the actual deck composition
   - Likely `Deck.Updated` or `Deck.ListUpdated`
   - May need to track inventory changes and correlate with deck changes

2. **Define the data model**:
   - Deck ID (from StartHook)
   - Deck name and metadata
   - Mainboard cards with counts
   - Sideboard cards with counts

3. **Implement export**:
   - Parse the deck composition event
   - Generate individual deck files
   - Generate container file

4. **CLI integration**:
   - Add `--export-decks` flag to the existing export command
   - Add `--deck-format single|container|hybrid` option

## Next Steps

1. Research MTGA log events to find deck composition data
2. Implement deck composition parser
3. Add export functionality to the existing pipeline
4. Test with real MTGA logs
