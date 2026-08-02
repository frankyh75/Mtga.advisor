# Phase 3 (T4+T5) — Deck-Karten Integration & Validation

## Übersicht

T4 integriert die in T1-T3 entwickelten Deck-Scanner in die bestehenden Komponenten:
- CLI (`cli/main.py`) — `deck-scan` Subcommand
- Dashboard (`server/app.py` + `dashboard.js`) — Deck-Detail-Panel mit Kartenanzeige
- LLM Advisor (`advisor/llm_advisor.py`) — Deck-Karten im LLM-Prompt
- Completion Advisor (`advisor/completion.py`) — akzeptiert deck.v1 Format

T5 validiert die Integration mit End-to-End Tests.

---

## T4a: CLI deck-scan Subcommand

**Datei:** `cli/main.py`

**Subcommand:** `deck-scan`

**Optionen:**
- `--output` (default: `out-decks`) — Ausgabeverzeichnis
- `--method` (`pattern`|`il2cpp`|`auto`, default: `auto`) — Scan-Methode
- `--anchors` (int list) — Anker-GrpIDs für Pattern-Scan
- `--card-db` (flag) — Lädt Karten-DB für Namensauflösung
- `--debug` (flag) — Scan-Statistiken

**Flow:**
1. Attach an MTGA-Prozess via `_attach_process`
2. IL2CPP-Navigation (bevorzugt bei `auto`/`il2cpp`): `scan_decks_il2cpp(PymemMemoryAdapter(pm))`
3. Pattern-Scan (Fallback bei `auto`, primär bei `pattern`): `scan_decks(pm, anchor_ids)`
4. Merge & Dedup der Ergebnisse
5. Schreibe Artefakte:
   - `decks-container.json` (Schema: `decks-container.v1`)
   - `decks/deck-{deckId}.json` (Schema: `deck.v1`)
   - `decks.json` (kompatibel mit Dashboard/Advisor)

**Beispiel:**
```bash
sudo python3 -m cli.main deck-scan --method auto --card-db --output out-decks
```

---

## T4b: Dashboard Deck-Detail-Panel

**Dateien:** `server/app.py`, `server/dashboard.js`

**Neue API-Endpunkte:**
- `GET /api/decks-container` — Liefert `decks-container.json` mit allen Decks und Karten
- `GET /api/deck/{deckId}` — Liefert individuelles Deck-File (`decks/deck-{deckId}.json`)

**Dashboard-Erweiterungen:**
- Deck-Detail-Panel (`#deck-detail` Section) mit:
  - Deck-Name und Metadaten (Quelle, Deck-ID, Kartenzahl)
  - Karten-Grid mit Mainboard/Sideboard/CommandZone/Companions
  - Jede Karte zeigt Name und Anzahl
- Bei Klick auf "Select" (oder Deck-Zeile): lädt Deck-Karten via `/api/deck/{deckId}` und öffnet Chat-Panel
- Schließen-Button blendet das Deck-Detail-Panel aus
- XSS-Safe: verwendet nur DOM-API (`createElement`, `textContent`), kein `innerHTML`

**CSS:**
- `.deck-cards-grid` — 2-Spalten Grid (1-Spalte auf Mobile)
- `.deck-pile` — Kartenliste pro Pile mit Border-Bottom-Trennung

---

## T4c: LLM Advisor mit Deck-Karten

**Datei:** `advisor/llm_advisor.py`

**Neue Funktion:** `_format_deck_cards_for_prompt(cards)`

Unterstützt zwei Karten-Formate:
1. **Named card lists** (aus deck-scan mit card_db): `{"mainboard": [{"cardId": 123, "name": "Lightning Bolt", "count": 4}, ...]}`
2. **grpId→qty dicts** (aus deck-scan ohne card_db): `{"mainboard": {"123": 4, "456": 2}}`

**Prompt-Erweiterung:**
Für jedes Deck (max 15) werden jetzt die Karten in den Prompt aufgenommen:
```
### Deck Name (Format)
  - DeckId: 1
  - Legal in: standard, historic
  - Mainboard (15 unique): 4x Lightning Bolt, 2x Counterspell, ...
  - Sideboard (7 unique): 2x Negate, 1x Mystical Dispute, ...
```

Das LLM kann so konkrete Crafting-Prioritäten und Deck-Optimierungen empfehlen, weil es weiss welche Karten im Deck sind.

---

## T4d: Completion Advisor mit deck.v1 Format

**Datei:** `advisor/completion.py`

**Neue Funktion:** `_normalize_deck_cards(deck)`

Erkennt automatisch das Deck-Format:
1. **arena-deck.v1** (aus `deck import`): `deck["mainboard"] = [{"arenaId": 123, ...}]` → Passthrough
2. **deck.v1 / decks-container.v1** (aus Memory-Scan): `deck["cards"]["mainboard"] = [{"cardId": 123, ...}]` oder `{"123": 4}` → Normalisierung zu `arenaId`-Format

**`build_completion_advice`** akzeptiert jetzt beide Formate. Die Normalisierung konvertiert `cardId` → `arenaId` und stellt `count`, `name`, `rarity` bereit.

---

## T4e: Tests

**Datei:** `advisor/tests/test_deck_integration.py` (10 Tests)

| Test | Beschreibung |
|------|-------------|
| `test_normalize_deck_cards_from_deck_v1_named_list` | deck.v1 mit Named-Lists → korrekte Normalisierung |
| `test_normalize_deck_cards_from_decks_container_grpId_dict` | grpId→qty Dict → korrekte Normalisierung |
| `test_normalize_deck_cards_arena_deck_v1_passthrough` | arena-deck.v1 → unverändert |
| `test_completion_advice_with_deck_v1_format` | build_completion_advice mit deck.v1 |
| `test_format_deck_cards_named_list` | _format_deck_cards_for_prompt mit Named-Lists |
| `test_format_deck_cards_grpId_dict` | _format_deck_cards_for_prompt mit grpId→qty |
| `test_build_prompt_includes_deck_cards` | _build_prompt enthält Kartennamen |
| `test_api_decks_container_endpoint` | GET /api/decks-container liefert JSON |
| `test_api_deck_individual_endpoint` | GET /api/deck/{deckId} liefert Deck-JSON |
| `test_api_deck_not_found` | GET /api/deck/nonexistent → 404 |

**Test-Results:**
- 10 neue Tests: alle grün
- 3 bestehende Server-Tests: alle grün
- 9 bestehende CLI-Tests: alle grün
- 3 bestehende Advisor-Tests: alle grün
- 2 pre-existing Failures in `scanner/tests/test_card_database_edge_cases.py` (nicht durch T4 verursacht)

---

## T5: Integration-Test

**Datei:** `advisor/tests/test_deck_integration.py` — enthält End-to-End Integration Tests

Der Integration-Test verifiziert den kompletten Flow:
1. Deck-Daten im deck.v1 Format (wie vom Memory-Scanner produziert)
2. Server serviert die Daten via API-Endpunkte
3. Completion-Advisor verarbeitet die Deck-Karten
4. LLM-Prompt enthält die Kartennamen

---

## Datenformate

### deck.v1 (Memory-Scan Output)
```json
{
  "schema": "deck.v1",
  "deckId": "abc123",
  "name": "My Deck",
  "source": "il2cpp",
  "cards": {
    "mainboard": [
      {"cardId": 70001, "name": "Lightning Bolt", "count": 4},
      {"cardId": 70002, "name": "Counterspell", "count": 2}
    ],
    "sideboard": [
      {"cardId": 70003, "name": "Negate", "count": 2}
    ]
  },
  "cardsById": {
    "mainboard": {"70001": 4, "70002": 2},
    "sideboard": {"70003": 2}
  }
}
```

### decks-container.v1
```json
{
  "schema": "decks-container.v1",
  "exportedAt": "2026-08-02T09:30:00Z",
  "decks": [
    {
      "deckId": "abc123",
      "name": "My Deck",
      "cards": {
        "mainboard": {"70001": 4, "70002": 2},
        "sideboard": {"70003": 2}
      }
    }
  ],
  "warnings": []
}
```

### decks.v1 (Dashboard/Advisor kompatibel)
```json
{
  "schema": "decks.v1",
  "exportedAt": "2026-08-02T09:30:00Z",
  "decks": [
    {
      "deckId": "abc123",
      "name": "My Deck",
      "source": "il2cpp",
      "cards": {
        "mainboard": [{"cardId": 70001, "name": "Lightning Bolt", "count": 4}]
      },
      "cardsById": {
        "mainboard": {"70001": 4}
      }
    }
  ]
}
```