# Plan: Scanner-Stabilisierung — Collection & Deck-Scan (Priorität)

## Ausgangslage

- Aktueller Branch `main` == `origin/main`.
- **Collection-Memory-Scanner:** erzeugt 7963 unique Einträge, aber die Anker-Validierung schlägt fehl (`anchor-mismatch`) → `valid: false`. Das ist der **Kern-Blocker**.
- **Deck-Scan:** `DeckSummaries` liefert 187 Decks zuverlässig. Vollständige Kartenlisten kommen aus `DeckUpsertDeckV3` (Log) + manuellem Arena-Text-Import.
- **GUI** ist bereits weit voraus (Import-Button, Badge, Filter, Pagination, Sortierung) — siehe Session-Protokoll.
- **Advisor-Analyse** (owned/missing, Wildcard-Bedarf) ist implementiert und funktioniert end-to-end mit importierten Decklisten.

## Ziel (Priorität: Collection & Deck-Scan)

Eine **verlässliche, validierte Datenbasis** für Collection und Decks herstellen.
Alles andere (GUI-Ausbau, New-Deck-Builder) baut erst darauf auf.

1. **Collection-Scan bestehen seine eigenen Ankerprüfungen** → `valid: true`.
2. **Ungültige Collection-Exports** dürfen nicht als vollständig behandelt werden.
3. **Deck-Summaries + vollständige Deck-Kartenlisten** sauber über `deckId` zusammenführen.
4. **Advisor-Analyse** end-to-end mit validierten Collection- und Deckdaten.

---

## PRIORITÄT 1: Collection-Scan stabilisieren (Kern-Blocker)

### 1.1 Anker-Verifikation (Live gegen MTGA)
- MTGA in **Collection-Ansicht** öffnen.
- Die **tatsächlichen Mengen** der 5 Anker-Karten prüfen:
  Atraxa Grand Unifier, One with the Multiverse, Fable of the Mirror-Breaker,
  Portal to Phyrexia, Ulamog the Defiler.
- Gespeicherte Anker-Datei (`~/.mtga_advisor/last_anchors.json`) mit korrekten Mengen aktualisieren.
- **Dokumentieren:** erwartete Menge vs. erkannte Menge pro Anker.

### 1.2 Blockauswahl korrigieren
- Blockauswahl **nicht** ausschließlich über `max(len(block))` entscheiden.
- Kandidaten bevorzugen, die **mehrere Anker mit korrekten Mengen** enthalten.
- Teiltreffer und widersprüchliche Kandidaten explizit als **unsicher** markieren.
- Große, aber falsche Speicherblöcke verwerfen.
- Ungültige Ergebnisse **nicht** als vollständige Collection exportieren.

### 1.3 Collection-Validierung
- Nach jedem Scan: Anker-Mengen gegen Export prüfen.
- Nur bei Übereinstimmung `valid: true` melden.
- Bei Mismatch: Warnung + kein vollständiger Export.

### 1.4 Tests für Collection-Scanner
- korrekter Collection-Block
- doppelte Kandidaten
- Teiltreffer
- falsche Anker-Mengen
- großer, aber falscher Speicherblock
- **Dedup-Fix in `find_blocks()` nicht entfernen** (schützt vor verfälschtem `max()`).

---

## PRIORITÄT 2: Deck-Scan stabilisieren

### 2.1 Deck-Summaries (stabil)
- `DeckSummaries` als stabilen Weg für Deck-Metadaten beibehalten.
- `isPrecon`-Erkennung: nur über den **Namen**, nicht die Description (Fix bereits drin).
- Precons vs. eigene Decks sauber trennen.

### 2.2 Vollständige Deck-Kartenlisten
- `DeckUpsertDeckV3` (Log) + manueller Arena-Text-Import als Quellen für Kartenlisten.
- Mainboard, Sideboard, Command Zone, Companions korrekt exportieren.
- Doppelte Updates, leere Listen, beschädigte Logzeilen absichern.

### 2.3 Daten zusammenführen (`decks.json` + `deck-cards.json`)
- Über `deckId` verbinden.
- Erfassungsstatus pro Deck:
  - Summary vorhanden
  - Kartenliste vorhanden
  - Kartenliste möglicherweise veraltet
  - keine vollständige Kartenliste
- Fehlende Kartenlisten in CLI/GUI eindeutig anzeigen.
- Keine vollständige Deckanalyse anbieten, wenn Kartenliste fehlt (oder nur mit Warnung).

### 2.4 Tests für Deck-Scanner
- DeckSummaries-Parsing (Precons, eigene Decks, Duplikate)
- `DeckUpsertDeckV3`-Parsing (Mainboard/Sideboard/Zonen, letzter gewinnt)
- Zusammenführung über `deckId`
- `upsert_deck_cards` (merge, kein Duplikat)

---

## PRIORITÄT 3: Advisor-Analyse auf validierter Basis

- Verlässliche Collection + echte Decklisten verwenden.
- `owned`/`missing` gegen bekannte Bestände prüfen.
- Raritäten + Wildcard-Bedarf prüfen (Rarity aus lokaler MTGA-DB, Mapping 1-4).
- Grundländer, Sideboard, Sonderzonen testen.
- Mind. 2 echte Deckanalysen end-to-end.
- Analyseergebnisse + Warnungen auf Plausibilität prüfen.

---

## PRIORITÄT 4: GUI erst danach weiterbauen

Erst wenn Collection validiert + Deckdaten zuverlässig sind:
- Collection-Browser mit Suche/Filtern
- Deck-Analyseansicht mit fehlenden Karten
- Craft- und Wildcard-Prioritäten
- Watch-Modus zur automatischen Aktualisierung (bereits gebaut, nicht-interaktiv)

---

## Erfolgskriterien

- Collection-Scan meldet **`valid: true`**.
- Alle Anker stimmen mit erwarteten Mengen überein.
- Ungültige Collection wird **nicht** als vollständig exportiert.
- Mehrere reale Decks haben vollständige Kartenlisten.
- `decks.json` + `deck-cards.json` korrekt über `deckId` verbunden.
- Advisor-Analyse funktioniert end-to-end mit validierten Daten.
- Relevante Test-Suite läuft vollständig grün.

## Nicht Bestandteil

- Kein Force-Push ohne Freigabe.
- Kein Merge nach `main` ohne ausdrückliche Freigabe.
- Keine Log-basierte Collection-Extraktion (Endpoint seit 2021 tot).
- **Kein GUI-Ausbau vor stabiler Collection + Deck-Scan** (bewusste Abweichung von der bisherigen GUI-Vorarbeit; GUI bleibt erhalten, wird aber nicht erweitert, bis die Datenbasis validiert ist).
