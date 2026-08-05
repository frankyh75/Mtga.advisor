# Deck Advisor Roadmap

Status: historische Zielbeschreibung. Die aktive Planung liegt jetzt in
`docs/deck-advisor-roadmap-v4.md`.

Datum: 2026-08-04

## Ziel

Die GUI soll zwei klar getrennte, aber gemeinsam unterlegte Workflows unterstützen:

1. Beratung für ein bestehendes Deck
2. Bau eines neuen Decks per LLM-Chat

Beide Flows sollen dieselbe Datenbasis nutzen:

- `collection.json`
- `decks.json` oder ein ausgewähltes Deck
- lokale Karten-DB
- Format-Parameter
- Constraints wie Rare-Limit oder Budget
- optional Meta-Daten

## Produktprinzip

Der Advisor soll nicht einfach "chatten", sondern strukturierte Entscheidungen treffen.

### Modi

- **Analyze Deck**
  - Input: vorhandenes Deck
  - Output: was fehlt, was raus kann, was priorisiert craftbar ist
- **Build Deck**
  - Input: Format, Farben, Archetyp, Rare-Limit, optional Budget
  - Output: neue Deckidee, Kernliste, Flex-Slots, nächste Schritte
- **Improve Deck**
  - Input: vorhandenes Deck plus Constraints
  - Output: konkrete Upgrade-Vorschläge

## GUI-Screens

### 1) Dashboard Home

Ziele:

- Collection-Status sichtbar machen
- vorhandene Decks anzeigen
- Einstieg in Deck-Analyse oder Deck-Bau

Elemente:

- Collection summary card
- Deck list
- Meta summary
- "Analyze Deck" CTA
- "Build New Deck" CTA

### 2) Deck Detail

Ziele:

- ein bestehendes Deck inspizieren
- direkt eine Beratung starten

Elemente:

- Deckname, Format, Farben, Legalität
- Kartenliste
- LLM-Chat-Panel mit Deck-Kontext
- Schnellaktionen:
  - Analyze
  - Improve
  - Export

### 3) New Deck Builder

Ziele:

- neues Deck vom Nutzer konstruieren lassen
- Constraints vor dem Chat festlegen

Elemente:

- Format dropdown
- Farbwahl oder Farbpräferenz
- Archetyp oder Spielstil
- `max rares`
- optional `max mythics`
- optional Budget
- optional "use only owned cards first"
- Start-Chat-Button

### 4) Advisor Result View

Ziele:

- Antworten des LLM in nutzbare Blöcke zerlegen

Blöcke:

- Summary
- Core cards
- Missing cards
- Craft priorities
- Cuts / flex slots
- Mana base notes
- Risk notes

## Backend-API

### Bestehende Endpoints

- `GET /api/decks`
- `GET /api/decks/{deckKey}`
- `POST /api/chat`
- `GET /api/meta`

### Neue oder erweiterte Endpoints

#### `POST /api/advisor/build`

Erzeugt einen neuen Deck-Entwurf.

Request:

```json
{
  "format": "standard",
  "colors": ["r", "g"],
  "archetype": "etali",
  "maxRares": 8,
  "maxMythics": 2,
  "budgetMode": "owned-first",
  "useMeta": true
}
```

Response:

```json
{
  "schema": "advisor-build.v1",
  "summary": {
    "deckConcept": "Etali blink/ramp",
    "confidence": "medium",
    "constraints": ["standard", "maxRares=8"]
  },
  "deckDraft": {
    "mainboard": [],
    "sideboard": [],
    "commandZone": []
  },
  "suggestions": [],
  "warnings": []
}
```

#### `POST /api/advisor/analyze`

Analysiert ein vorhandenes Deck mit Collection-Kontext.

Request:

```json
{
  "deckKey": "abc123",
  "format": "standard",
  "goal": "improve",
  "maxRares": 4
}
```

Response:

```json
{
  "schema": "advisor-analysis.v1",
  "summary": {},
  "missingCards": [],
  "craftPriorities": [],
  "cuts": [],
  "notes": []
}
```

#### `POST /api/advisor/chat`

LLM-Chat mit festem Kontext.

Request:

```json
{
  "mode": "build",
  "format": "standard",
  "deckKey": null,
  "prompt": "Ich will Etali bauen, max 8 rares, eher blink als combat.",
  "constraints": {
    "maxRares": 8,
    "maxMythics": 2,
    "colors": ["r", "g"]
  }
}
```

## Datenmodell

### `advisor-context.v1`

Interner Kontext, der an den LLM geht.

```json
{
  "schema": "advisor-context.v1",
  "collection": {
    "complete": true,
    "cards": {}
  },
  "deck": {},
  "format": "standard",
  "constraints": {
    "maxRares": 8,
    "maxMythics": 2,
    "budgetMode": "owned-first",
    "colors": ["r", "g"]
  },
  "cardDb": {},
  "meta": {}
}
```

### `advisor-build.v1`

Für neue Deckideen.

```json
{
  "schema": "advisor-build.v1",
  "summary": {},
  "coreCards": [],
  "flexCards": [],
  "landBase": [],
  "craftPriorities": [],
  "warnings": []
}
```

### `advisor-analysis.v1`

Für bestehende Decks.

```json
{
  "schema": "advisor-analysis.v1",
  "summary": {},
  "missingCards": [],
  "cuts": [],
  "upgrades": [],
  "warnings": []
}
```

## Prompt-Struktur

Der Prompt sollte immer dieselbe Reihenfolge haben:

1. Rolle
2. Ziel
3. Format
4. Constraints
5. Collection-Context
6. Deck-Context
7. Meta-Context
8. Aufgabenformat

### Build-Prompt

Muss den LLM dazu zwingen:

- nur legale Karten vorzuschlagen
- Rare-Limit zu respektieren
- owned-first zu bevorzugen, wenn verlangt
- einen klaren Core zu definieren
- Flex-Slots und Mana-Basis zu benennen

### Analyze-Prompt

Muss den LLM dazu zwingen:

- das Deck als bestehendes Objekt zu behandeln
- fehlende Karten zu priorisieren
- Kraftaufwand gegen Nutzen zu bewerten
- Cut-Vorschläge zu erklären

## Priorisierte Implementierungsreihenfolge

### Phase 1: GUI stabilisieren

1. Merge-Konflikte in `server/dashboard.js` entfernen
2. bestehende Deck-Details wieder stabil lauffähig machen
3. Chat-Panel mit Deck-Kontext sicher halten
4. vorhandene API-Antworten vereinheitlichen

### Phase 2: Neue Deck-Erstellung

1. UI-Form für Format, Farben, Rare-Limit, Budget
2. neuer Backend-Endpoint `POST /api/advisor/build`
3. LLM-Kontext bauen
4. Antwort in strukturierte Blöcke zerlegen
5. Ergebnis als Entwurf speicherbar machen

### Phase 3: Bestehende Deck-Beratung

1. `POST /api/advisor/analyze`
2. Missing-Cards-Logik mit Collection-Context
3. Craft-Prioritäten pro Rarity
4. Cut-/Upgrade-Vorschläge
5. klare Confidence-Anzeige

### Phase 4: Qualität und UX

1. Prompt-Tests und Fixtures
2. UI-Tests für Format/Rare-Limit-Formular
3. bessere Karten-Rendering-Ansichten
4. Ergebnisspeicherung und Verlauf

## 2-Sprint-Plan

### Sprint 1

- GUI-Konflikte bereinigen
- Deck-Chat stabilisieren
- Build-Formular einführen
- erstes `/api/advisor/build`
- strukturiertes Build-Response-Format

### Sprint 2

- Analyze-Flow ergänzen
- Constraints härter durchsetzen
- Craft-Prioritäten und Upgrade-Hinweise verbessern
- Entwurf speichern und wieder laden
- Prompt-/UI-Tests ergänzen

## Erfolgsdefinition

Der Advisor gilt als brauchbar, wenn ein Nutzer in der GUI:

- ein Format auswählt
- ein Rare-Limit setzt
- ein neues Deck per Chat startet
- eine erste tragfähige Liste erhält
- dieselbe Oberfläche nutzen kann, um ein bestehendes Deck zu verbessern

## Nicht-Ziele

- kein Live-Gameplay
- kein Overlay
- keine Winrate-Versprechen ohne belastbare Daten
- keine Empfehlung außerhalb der gewählten Farbidentität
- keine harte Craft-Empfehlung ohne ausreichende Evidenz
