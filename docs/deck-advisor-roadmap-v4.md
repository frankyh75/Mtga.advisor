# Deck Advisor Roadmap v4 — Aktive Planung

Datum: 2026-08-05 (aktualisiert 2026-08-07)

## Zweck

Diese Datei ist die aktuelle Arbeitsplanung für zwei zusammenhängende Themen:

1. Deck-Beratung für bestehende Decks
2. Neuer-Deck-Builder per LLM-Chat

Der Scanner-Kern ist weitgehend gelöst. Offene Arbeit liegt jetzt vor allem
in der Zusammenführung der Datenquellen (Collection, Deck-Karten, Wildcards)
und in der Advisor-UX.

## Bereits erledigt

- [x] Collection-Export ist stabil (Memory-Scan, Anker-basiert).
- [x] Deck-Scan liest echte Decks (IL2CPP-Backref war tot gegen 2026-Build;
  **Log-Scan `DeckSummaries` liefert jetzt 187 Decks in <3s**).
- [x] Sudo-Helper robust (Socket 0666, mach_vm_read_overwrite, Submap-Traversal,
  newline-Protokoll, launchctl bootstrap).
- [x] `decks.json` (187 Summaries), `ranks.json`, Wildcards im Parser vorhanden.
- [x] Dashboard und Chat-API existieren bereits.

## Ist-Zustand (verifiziert, 2026-08-07)

| Datenquelle | Status | Verfügbar? |
|-------------|--------|-----------|
| **Decks (Summaries)** | Log-Scan `DeckSummaries` | ✅ 187 Decks, <3s (`out/decks.json`) |
| **Wildcards** | StartHook `InventoryInfo` | ✅ 188C/68U/9R/18M (Parser extrahiert schon) |
| **Collection (owned)** | Memory-Scan, Anker-basiert | ❌ `collection.json` fehlt |
| **Deck-Kartenlisten** | nicht in Summaries | ❌ nur Name/ID/Format |
| **Ranks** | Backref Memory-Scan | ✅ funktioniert |

> **Kern-Erkenntnis:** `decks.json` enthält nur Summaries (Name, deckId, Format),
> NICHT die Kartenlisten. Für Beratung ("fehlende Karten / Cuts / Craft") brauchen wir
> pro Deck die Kartensammlung. Das ist die zentrale offene Lücke.

## Was jetzt noch Sinn ergibt

### 1. Collection-Scan verifizieren (→ `collection.json`)
- Memory-Scan war der einzige Weg (Log-Endpoint `GetPlayerCardsV3` seit 2021 tot).
- Anker-Fundstellen waren 0 — vermutlich weil die **Collection-Ansicht** (nicht
  Decks-Ansicht) geöffnet sein muss. Test zuerst, bevor Code geändert wird.

### 2. Deck-Kartenlisten beschaffen
- `decks.json` enthält nur Summaries (Name/ID/Format), keine Kartenlisten.
- Prüfen: Log-Events `InDeckUpdateDeckV3`/`Deck.Update` mit Karten? (Community-konsistent.)
- Oder `deck import` (Arena-Text) für manuelle Listen.

### 3. Wildcards integrieren
- StartHook `InventoryInfo` → eigenes `out/wildcards.json` (Schema wildcards.v1).
- Wichtig für `max rares/mythics` im Deck-Builder.

### 4. Build-Flow für neue Decks (LLM)
Pflichtfelder: Format, Farben, Archetyp, `max rares`, optional `max mythics`, Budget,
"nur vorhandene Karten zuerst".

### 5. Analyze-Flow für bestehende Decks
Mit Collection + Kartenliste: owned/missing, Cuts, Craft-Prioritäten, Wildcard-Bedarf.

### 6. Collection- und Kartenbrowser
Suche/Filter: Name, Typ, Farbe, Seltenheit, Owned/missing.

### 7. Drafts und Verlauf
Lokal speichern, vergleichen, Analyse-Verlauf pro Deck.

### 8. Scanner-Bedienung ohne sudo
- Helper-Daemon ist gebaut und funktioniert sudo-frei (Socket /var/run/mtga-helper.sock).

## Priorität (verbindlich, Stand 2026-08-07)

**Franks Ziel:** Endlich Beratung. Reihenfolge: Daten-Lücken schließen → Advisor-Flow.

1. **[x] Deck-Scan (Logs)** — 187 Decks, <3s. **Erledigt.**
2. **Collection-Scan verifizieren** — MTGA in Collection-Ansicht, Anker prüfen → `collection.json`
3. **Wildcards extrahieren** — StartHook-Inventory → `out/wildcards.json`
4. **Deck-Kartenlisten beschaffen** — Log-Events untersuchen, Karten pro Deck
5. **Advisor-Analyze-Flow** — owned/missing, Cuts, Craft, Wildcard-Bedarf
6. **New-Deck-Builder (LLM)** — `max rares/mythics` = Wildcard-Budget
7. **GUI stück für stück** — Collection-Browser, Kartenbrowser, Drafts

> **Regel:** Beratung braucht zuerst die drei Datenquellen (Collection, Deck-Karten,
> Wildcards). Detaillierter Task-Plan: `.hermes/plans/2026-08-07-roadmap-v4-scans-beratung.md`

## Nicht mehr aktiv

- `data_segment_base`-Jagd als Hauptproblem des Deck-Scans (toter Ansatz gegen 2026-Build).
- Pattern-Scan als bevorzugte Lösung.
- IL2CPP-Memory-Navigation für Decks (von der Community aufgegeben, 0 Treffer).
- neue Scanner-Grundlagen, bevor die Advisor-UX steht.

## Abgrenzung

- Diese Roadmap ersetzt die älteren Advisor-/Scanner-Planungsdokumente
  als aktive Liste.
- Historische Details bleiben in den älteren Dateien erhalten, sind aber
  nicht mehr die To-do-Liste.
