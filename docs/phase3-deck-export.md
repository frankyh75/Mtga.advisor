# Phase 3: Deck-Export-Design

## Reality Check

- `decks.json` ist aktuell nur ein Summary-Export aus `StartHook`.
- Wir haben in den Logs noch keine belastbare Quelle für vollständige Kartenlisten pro Deck gefunden.
- Ein hoher `deckCount` kann historische oder interne Deck-/Course-Snapshots enthalten und ist deshalb nicht gleichbedeutend mit der aktuell sichtbaren Arena-Deckliste.

## Quellen

### Deck-Summaries (automatisch aus Logs)
- **Quelle:** StartHook-Events in `Player.log`
- **Was geliefert wird:** Name, DeckId, DeckTileId, Beschreibung, Mana-Farben, Format-Legalities, Companion-Gültigkeit
- **Limitierung:** KEINE Karten-Zusammensetzung (welche Karten im Deck sind)
- **Export-Format:** `decks.json` (schema: `decks.v1`)
- **Export-Pfad:** `{output}/decks.json`

### Vollständige Decklisten (aktuell offen)
- **Status:** Noch nicht belastbar aus MTGA-Logs oder Memory exportiert.
- **Bekannte Möglichkeit:** Arena-Textdeckliste (aus MTGA Deck-Editor kopiert)
- **Format:** Arena-Standard-Text (`4 Lightning Strike\nDeck\nSideboard\n...`)
- **Export-Format:** `arena_deck.json` (schema: `arena-deck.v1`)
- **Import-Pfad:** `python -m cli.main deck import --file <path> --format standard`

## Container-Format für mehrere Decks

### Zielverzeichnis
```
out/decks/
├── index.json          # Container-Index (alle Decks, Verlinkung)
├── <deck-id>.json      # Einzelne Deck-Summary (als JSON)
├── <deck-id>.txt       # Arena-Textdeckliste (falls vorhanden)
└── <deck-id>.arena.json  # Vollständige Deckliste (falls importiert)
```

### index.json Struktur
```json
{
  "schema": "decks-container.v1",
  "generatedAt": "2025-01-02T00:00:00Z",
  "deckCount": 5,
  "decks": [
    {
      "deckId": "abc123",
      "name": "Mono-Red Aggro",
      "hasFullList": true,
      "fullListPath": "abc123.arena.json",
      "summaryPath": "abc123.json",
      "textListPath": "abc123.txt"
    }
  ]
}
```

## Pfadtrennung

| Artefakt | Pfad | Quelle | Zweck |
|----------|------|--------|-------|
| `out/decks.json` | Summary-Index | Log-Export | Schneller Überblick, Navigation |
| `out/decks/` | Container-Verzeichnis | Log-Export | Strukturierte Deck-Verwaltung |
| `out/decks/index.json` | Container-Index | Log-Export | Verlinkung aller Decks |
| `out/decks/<id>.json` | Einzelne Summary | Log-Export | Deck-Metadaten |
| `out/decks/<id>.txt` | Arena-Text | Manueller Import | Deck-Liste im Arena-Format |
| `out/decks/<id>.arena.json` | Vollständige Liste | Manueller Import | Normalisierte Deckliste |
| `out/arena_deck.json` | Einzelne Liste | Manueller Import | Legacy-Single-Deck-Export |

**Hinweis:** Der aktuelle Log-Export liefert nur Summaries. Echte Kartenlisten erscheinen hier nur, wenn sie manuell importiert wurden oder eine neue belastbare Quelle gefunden ist.

## CLI-Workflow

```bash
# 1. Log-Export (Summaries)
python -m cli.main decks --output out

# 2. Container-Export (strukturiert)
python -m cli.main decks container --output out/decks

# 3. Einzelne Deck-Liste importieren
python -m cli.main deck import --file mydeck.txt --format standard

# 4. Container aktualisieren (verlinkt importierte Decks)
python -m cli.main decks container --output out/decks --refresh

# 5. Einzelne Deck-Liste anzeigen
python -m cli.main decks show --deck-id <id> --output out/decks
```

## Implementierungsschritte

1. [x] Container-Export-Logik in `parser/decks.py`
2. [x] CLI-Subcommand `decks container`
3. [x] CLI-Subcommand `decks show`
4. [x] CLI-Subcommand `decks list`
5. [x] Tests für Container-Export
6. [x] Integrationstest: Summaries + manueller Import + Container
7. [ ] Belastbare Quelle für vollständige Decklisten finden

## Erfolgskriterien

- `decks container` generiert korrekten Container mit index.json
- `decks list` zeigt alle Decks aus dem Container an
- `decks show --deck-id <id>` zeigt Details eines Decks
- Importierte Decklisten werden korrekt im Container verlinkt
- Testabdeckung > 90% für neue Logik
- Summary-Exports werden nicht mehr als vollständige Decklisten missverstanden
