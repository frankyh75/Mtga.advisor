# Deck Advisor Roadmap v4 — Aktive Planung

Datum: 2026-08-05

## Zweck

Diese Datei ist die aktuelle Arbeitsplanung für zwei zusammenhängende Themen:

1. Deck-Beratung für bestehende Decks
2. Neuer-Deck-Builder per LLM-Chat

Der Scanner-Kern ist weitgehend gelöst. Offene Arbeit liegt jetzt vor allem
in der Advisor-UX, in gespeicherten Entwürfen und in der Bedienbarkeit auf
macOS.

## Bereits erledigt

- [x] Collection-Export ist stabil.
- [x] Deck-Scan liest echte Decks mit Kartennamen.
- [x] Pattern-Scan ist nur noch Fallback, nicht mehr der Hauptpfad.
- [x] Scan-Ergebnisse werden gegen schlechte Überschreibungen abgesichert.
- [x] `decks.json` / `decks-container.json` / Einzel-Deckdateien sind vorhanden.
- [x] Dashboard und Chat-API existieren bereits.

## Was jetzt noch Sinn ergibt

### 1. Build-Flow für neue Decks

Ziel: ein LLM-gestützter "Neues Deck bauen"-Flow in der GUI.

Pflichtfelder:

- Format-Auswahl
- Farbauswahl oder Farbpalette
- Archetyp oder Deckidee
- `max rares`
- optional `max mythics`
- optional Budget
- optional "nur vorhandene Karten zuerst"

Erwartung:

- kein freier Chat ohne Leitplanken
- strukturierte Ausgabe mit Kernkarten, Flex-Slots, Lücken und Craft-Prioritäten

### 2. Analyze-Flow für bestehende Decks

Ziel: ein vorhandenes Deck mit Collection, Format und Meta bewerten.

Erwartung:

- welche Karten fehlen
- welche Cuts sinnvoll sind
- welche Karten am ehesten gecraftet werden sollten
- klare Trennung zwischen "owned" und "missing"

### 3. Collection- und Kartenbrowser

Ziel: die gesamte Sammlung mit Suche und Filtern sichtbar machen.

Sinnvolle Filter:

- Name
- Typ
- Farbe
- Seltenheit
- Owned / missing

### 4. Drafts und Verlauf

Ziel: gebaute Deckideen und Beratungen speichern.

Erwartung:

- Drafts lokal speichern
- Drafts vergleichen
- Analyseverlauf pro Deck nachvollziehen

### 5. Scanner-Bedienung ohne jedes Mal `sudo`

Status:

- Live-Memory-Zugriff auf macOS braucht aktuell weiterhin privilegierten
  Attach.
- Ein wirklich sudo-freier Live-Scan ist im Repo noch nicht belegt.

Sinnvolle Optionen:

- Wrapper-Skript mit einmaligem `sudo -v`
- kleiner privilegierter Helper
- LaunchDaemon/Helper-Bundle mit klar abgegrenzten Rechten

Nicht sinnvoll:

- den Root-Bedarf einfach in der Doku verstecken
- mehrere manuelle `sudo`-Eingaben pro Workflow-Schritt

## Nicht mehr aktiv

- `data_segment_base`-Jagd als Hauptproblem des Deck-Scans
- Pattern-Scan als bevorzugte Lösung
- neue Scanner-Grundlagen, bevor die Advisor-UX steht

## Priorität (verbindlich, Stand 2026-08-06)

**Franks Ziel:** Erst den Memory-Scanner robust bekommen (inkl. Sudo-Helper),
dann die GUI stück für stück verbessern.

1. **Memory-Scanner robust + Sudo-Helper** (Basis — alles andere baut darauf auf)
   - Helper-Daemon auf MacBook Air installieren + Socket verifizieren
   - Scan-Stabilität: Heap-Instabilität, Anker-Zuverlässigkeit, Dedup
   - `rank_scanner.py` auf Backref-Ansatz migrieren (falls Ranks gehoben werden)
2. **New-Deck-Builder** (LLM-gestützt, strukturierte Ausgabe)
3. **Analyze-Flow** (bestehende Decks bewerten: fehlende Karten, Cuts, Craft)
4. **Collection-/Karten-Browser** (Suche, Filter)
5. **Drafts & Verlauf** (lokal speichern, vergleichen)

> **Regel:** GUI-Verbesserungen erst, wenn der Memory-Scanner + Sudo-Helper
> stabil laufen. Scanner-Robustheit hat Vorrang vor Advisor-UX.

## Abgrenzung

- Diese Roadmap ersetzt die älteren Advisor-/Scanner-Planungsdokumente
  als aktive Liste.
- Historische Details bleiben in den älteren Dateien erhalten, sind aber
  nicht mehr die To-do-Liste.
