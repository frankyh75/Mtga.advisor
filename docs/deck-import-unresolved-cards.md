# Deck-Import: Karten mit fehlender Arena-ID (unresolved-arena-id)

**Stand: 2026-11-21**

## Problem

Arena-Deck-Exports enthalten für jede Karte Set-Code und Collector-Number
(z. B. `4 U.S.S. Discovery, at Black Alert (TRK) 221`). Die interne Karten-DB
wird aus Scryfall-Bulk-Daten gespeist, die für manche Sets (z. B. **TRK**
„Throne of Eldraine – Kingdom" / Fan-Produktion) **keine `arena_id`**
liefern. Vor dieser Änderung wurden solche Karten stillschweigend aus der
Gesamtzahl ausgeschlossen – ein 60-Karten-Deck wurde als 56 gezählt, der
`completionScore` war auf einer zu kleinen Basis berechnet.

## Architektur

### `_parse_deck_line` (F1)

```python
def _parse_deck_line(line: str) -> tuple[int, str, str | None, str | None] | None:
```

Parst `"<count> <name> [(<SET>) <CN>]"` → `(count, name, set_code, collector_number)`.
`set_code` und `collector_number` sind `None`, wenn kein Arena-Suffix vorliegt.

### Auflösungsreihenfolge

1. **Name in Karten-DB** – `name_to_cards` liefert Kandidaten.
2. **Mehrdutigkeit** – `_resolve_reprint` (alphabetisch letztes Set).
3. **Name nicht gefunden** – aber `set` + `collectorNumber` vorhanden:
   - Karte wird **ohne** Arena-ID aufgenommen (`arenaId: null`,
     `reason: "unresolved-arena-id"`).
   - Zählt in `totalCards`, **nicht** in `ownedCards`/`missingCards`.
   - Eigner Zähler `unresolvedCards`; Warnung `arena-id-unbekannt`;
     `unknownSets` enthält den Set-Code.
4. **Name nicht gefunden, kein Set/CN** – landet in `diagnostics.unresolved`
   mit `reason: "unknown-card-name"`. Zählt in `totalCards` über die
   `parsed_card_count`-Assertion mit.

### Namens-Treffer haben Vorrang

Set/CN werden **nicht** als harte Konsistenzprüfung für bereits per Name
aufgelöste Karten verwendet. Nur der Fallback (kein Namenseintrag in der DB)
nutzt Set+CN. Dies ist notwendig, damit der echte Fall (60 total, 56 resolved,
4 unresolved TRK) korrekt ergibt; eine globale Set/CN-Prüfung würde
fälschlich weitere `identity-mismatch`-Karten erzeugen.

### Konsistenz-Assertion

```python
tracked = sum(count in mainboard) + sum(count in sideboard)
dropped = sum(count in unresolved diagnostics)
assert tracked + dropped == parsed_card_count
```

Garantiert: Keine Karte verschwindet stillschweigend.

## `analyze_deck` und `scoreBasis`

```python
total_cards = resolved_cards + unresolved_cards
```

`completionScore` wird **nur auf `resolved_cards`** berechnet – nicht auf
`total_cards`, damit nicht auflösbare Karten den Score nicht drücken:

```python
if total_cards == 0:
    completion_score = 100.0
elif resolved_cards == 0:
    completion_score = 0.0
else:
    completion_score = round((total_owned / resolved_cards) * 100, 1)
```

### `scoreBasis`-Feld

```json
{
    "scoreBasis": {
        "owned": 56,
        "resolved": 56,
        "total": 60
    }
}
```

- `owned`: tatsächlich in Collection vorhandene Karten (aus `ownedCards`).
- `resolved`: Karten, die per Arena-ID in der DB gefunden wurden.
- `total`: alle importierten Karten inkl. `unresolved-arena-id`.

## Unresolved-Card-Eintrag

```json
{
    "arenaId": null,
    "name": "U.S.S. Discovery, at Black Alert",
    "set": "TRK",
    "collectorNumber": "221",
    "rarity": "unknown",
    "count": 4,
    "reason": "unresolved-arena-id"
}
```

Wird von `arena_deck_to_deck_cards()` nach `deck["unresolvedCards"]`
gemappt und von `analyze_deck()` via `_collect_unresolved_cards()` aggregiert.

## API-Response (`POST /api/deck-import`)

```json
{
    "summary": {
        "totalCards": 60,
        "ownedCards": 56,
        "missingCards": 0,
        "completionScore": 100.0,
        "unresolvedCards": 4,
        "unknownSets": ["TRK"],
        "scoreBasis": { "owned": 56, "resolved": 56, "total": 60 }
    },
    "unresolvedCards": [
        {
            "name": "U.S.S. Discovery, at Black Alert",
            "set": "TRK",
            "collectorNumber": "221",
            "quantity": 4,
            "zone": "mainDeck",
            "reason": "unresolved-arena-id"
        }
    ],
    "diagnostics": {
        "unresolvedCards": 4,
        "unresolvedArenaIdCards": 4,
        "unknownSets": ["TRK"],
        "warnings": ["arena-id-unbekannt"]
    },
    "saved": true
}
```

## Live-Nachweis (2026-11-21)

Eingabe: `/Users/agent/.hermes/profiles/jarvis/cache/scratch/frank_deck_v2.txt`
(24 card lines, 60 Karten, enthält `4 U.S.S. Discovery, at Black Alert (TRK) 221`)

```
Deck file: 24 card lines
Card DB: 19627 entries
mainboard cards: 60
sideboard cards: 0
unresolvedCards: 4
unresolvedArenaIdCards: 4
unknownSets: ['TRK']
warnings: ['arena-id-unbekannt']
```

Unresolved-Entry:
```json
{
    "arenaId": null,
    "name": "U.S.S. Discovery, at Black Alert",
    "set": "TRK",
    "collectorNumber": "221",
    "rarity": "unknown",
    "count": 4,
    "reason": "unresolved-arena-id"
}
```

## Karten-DB-Refresh-Report (F2)

Bei `--refresh-card-db` (bzw. `load_card_database(refresh_cache=True)`) wird
ein Report ausgegeben:

```
📊 Refresh-Report: source=scryfall, cardCount=19627
Sets ohne Arena-ID in den gelieferten Quelldaten (Top 10; Anzahl Einträge):
TRK: 142, FRA: 98, ...
Hinweis: Diese Einträge sind nicht notwendigerweise Arena-Karten;
vollständig fehlende Sets sind nicht erkennbar.
Ein Refresh ergänzt fehlende Arena-IDs nicht notwendigerweise,
wenn die Quelle sie nicht liefert.
```

Dies macht sichtbar, dass ein Scryfall-Refresh TRK/Lücken nicht schließt.

## EACCES-Diagnose (F2)

`get_macos_mtga_data_path()` prüft neben den Standardpfaden auch
`/Users/<jeder User>/Library/Application Support/Wizards Of The Coast/MTGA/...`
und `/Users/Shared/Epic Games/MagicTheGathering/**`. Existiert ein Pfad, ist
aber nicht lesbar (EACCES), wird ein `PermissionError` geworfen mit Hinweis auf
benötigte POSIX-ACL/x-Rechte – **ohne** automatische Rechteänderung.

## Tests

- `test_deck_import_unresolved.py` – 13 Tests: verschiedene Szenarien mit
  `unresolved-arena-id`, `unknown-card-name`, `identity-mismatch`.
- `test_import_identity_extra.py` – 3 Tests: Set/CN-Konsistenzprüfung.
- `test_deck_import_api.py` – 1 API-End-to-End-Test über `MtgaAdvisorServer`.
- `test_cache_permission_fallback.py` – 2 Tests: Cache-Lesen bei EACCES.
- `test_card_database_refresh_report.py` – 8 Tests: Refresh-Report-Ausgabe.
- `test_macos_path_discovery.py` – 7 Tests: Pfad-Discovery mit PermissionError.
- Gesamt: 617 Tests (volle Suite).

## Nicht Teil dieser Änderung

- Rechte/ACL auf `Wizards Of The Coast` ändern (FRANK: manuell).
- Scryfall-Cache manuell patchen oder TRK-Arena-IDs raten.
- GUI-Rendering (eigenes Task: `t_db224e11`).
