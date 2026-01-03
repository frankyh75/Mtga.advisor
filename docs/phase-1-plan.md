# Phase 1 Gesamtplan (Mtga.advisor)

## A) Phase-1 Definition of Done (DoD) — Checkliste (DONE/NOT DONE)
**Phase 1 gilt als DONE, wenn ALLE Punkte erfüllt sind:**

1) **Log-Fund (macOS, Windows vorgesehen)**
   - [ ] macOS-Logpfad wird erkannt und nachvollziehbar geloggt (Quelle/Heuristik).
   - [ ] Windows-Logpfad ist als Ziel vorgesehen und als „nicht geprüft“ markiert, aber die Log-Finder-Logik ist vorbereitet (keine harte Fehlermeldung).
   - [ ] Bestätigter macOS-Pfad: `~/Library/Logs/Wizards of the Coast/MTGA/`.

2) **Snapshot-Erkennung**
   - [ ] Parser erkennt Snapshot-Events zuverlässig, ohne Annahmen über Log-Inhalte.
   - [ ] Snapshot wird als Basis für alle Deltas genutzt (keine Delta-Verarbeitung ohne Snapshot).

3) **Export-Artefakte**
   - [ ] `collection.json` wird erzeugt (auch bei Problemen).
   - [ ] `run-report.json` wird erzeugt (inkl. Warnungen/Fehlern).
   - [ ] Export erfolgt über **einen** CLI-Befehl: `mtga-export run`.

4) **Konservative Vollständigkeitsbewertung**
   - [ ] Vollständigkeit wird konservativ bewertet (lieber „unklar/unvollständig“ als „vollständig“).
   - [ ] Ergebnis wird im Report UND in der UI sichtbar, inkl. Begründung/Warnungen.

5) **Debug-Web-UI (localhost)**
   - [ ] UI zeigt Vollständigkeits-Badge, Zahlen, Warnungen.
   - [ ] UI hat ein Suchfeld.
   - [ ] Diagnostik ist vorhanden, aber einklappbar.
   - [ ] UI bleibt funktionsfähig bei Parsing-Problemen (Warnungen statt Abbruch).

---

## B) Log-Realitätscheck (Validierungsplan)

### 1) Zu überprüfende Eigenschaften realer Logs
- **Pfad-Realität**: tatsächlicher Log-Pfad unter macOS (bestätigt) und Windows (später).
- **Dateigröße & Rotationsverhalten**: Größe/Rotation/mehrere Dateien.
- **Alter & Aktualität**: Änderungszeit, ob Logs „alt“ bleiben können.
- **Snapshot-Events**: ob/wo im Log ein Snapshot-Signal vorkommt.
- **Detailed Logs**: ob und wie „Detailed Logs“ im Spiel aktiviert/ausgegeben werden.

### 2) Risiken für Parser & UX
- **Pfadabweichungen** → Log-Finder findet nichts, UI wirkt „leer“.
- **Große Logs/Rotation** → Parser-Performance, Partial-Parsing, unvollständige Ergebnisse.
- **Fehlende Snapshot-Events** → Keine Deltas möglich, Vollständigkeit „unklar“.
- **Detailed Logs deaktiviert** → Weniger Daten, mehr Warnungen.
- **Sehr alte Logs** → Veraltete Collection, Vertrauen sinkt.

### 3) Ergebnisse, die Umsetzung beeinflussen
- **Pfad-Findings** → Log-Finder-Heuristiken & Dokumentation der Quellen.
- **Rotation/Größe** → Parser-Strategie (Chunking, „latest only“, Warnungen).
- **Snapshot-Vorkommen** → Definition von „Erfolg“/„Warnung“ in Vollständigkeit.
- **Detailed-Log-Abhängigkeit** → UI-Warnungen & konservative Bewertung.

**OFFEN (gezielte Rückfragen)**
- Windows-Logpfad: noch nicht bestätigt.
- Snapshot-Signal: noch nicht bestätigt.

---

## C) Debug-Web-UI – Minimal-Konzept (Phase 1)

### 1) Zweck der UI (klar abgegrenzt)
- **Debug-Werkzeug**: Zeigt, ob Daten vorhanden sind und wo es klemmt.
- **Vertrauens-Werkzeug**: Macht Unsicherheiten explizit sichtbar.
- **Vorläufer** der späteren Web-App, jedoch **nur lokal** und **ohne Produktfeatures**.

### 2) Inhalte / Sektionen (max. 4–5)
1. **Vollständigkeit** (Ampel/Badge)
2. **Zahlen** (Gesamtanzahl, ggf. Rares/Mythics falls ableitbar)
3. **Warnungen** (klar, menschlich)
4. **Suchfeld** (ID-Suche, optional Namen wenn Mapping vorhanden)
5. **Diagnostik** (einklappbar; Parser-Stats, Log-Fund)

### 3) Sofort sichtbare Informationen
- Vollständigkeits-Badge
- Kernzahlen (z. B. Gesamtanzahl)
- Aktuelle Warnungen
- Suchfeld

### 4) Sekundär / einklappbar
- Log-Fund-Details (Pfad, Alter, Größe)
- Parser-Statistik (Events, Snapshot gefunden?)
- Export-Zeitstempel, Laufdauer

### 5) Was die UI explizit NICHT tut
- **Kein** Live-Tracking
- **Keine** Online-Enrichment-Abfragen
- **Keine** Nutzer-Accounts oder Cloud-Sync
- **Keine** In-Game Overlay-Funktionen

---

## D) Kartennamen (DE/EN) – Optionales Enrichment (Modell #2)

### 1) Format von `card-name-map.json`
```json
{
  "version": "1.0",
  "source": {
    "name": "Scryfall Bulk Data",
    "url": "...",
    "downloaded_at": "YYYY-MM-DDTHH:MM:SSZ"
  },
  "mapping": {
    "12345": { "en": "Card Name", "de": "Kartenname" },
    "67890": { "en": "Other Name", "de": null }
  }
}
```
- `mapping` Keys = ArenaCardId als String
- `en`/`de` können `null` sein

### 2) Build-/Update-Workflow (CLI + UI, manuell)
- **CLI**: `mtga-export names update` (oder ähnlich)
- **UI-Aktion**: Button „Kartennamen lokal verfügbar machen“
  - Bestätigung erforderlich
  - Download offizieller Bulk-Quelle
  - Lokal Map bauen & speichern

### 3) Sicherheits- & Robustheitsregeln
- Abbruch bei Netzfehlern, mit klarer Warnung
- Vorhandenes Mapping bleibt erhalten (kein „leeres Überschreiben“)
- Mapping ist optional; Parser/Vollständigkeit funktionieren ohne Mapping
- **Keine** CardIds oder Namen erfinden

### 4) UI-Verhalten
- **Suchfeld**: DE/EN, case-insensitive
- Anzeige: `Name (falls vorhanden) | ArenaCardId | Count`
- Fehlende Namen klar markieren (z. B. „Name unbekannt“)

### 5) Abgrenzung zu Phase 2
- **Keine** Live-API
- **Kein** On-Demand Fetching
- **Kein** Cache-Refresh im Hintergrund

---

## E) Umsetzungsplanung & Tickets

### Ticket 1 — Parser-Grundlage
**Ziel:** Log-Events robust parsen, Snapshot erkennen.

**Akzeptanzkriterien:**
- Snapshot-Erkennung vorhanden.
- Parser arbeitet ohne Annahmen über Log-Inhalte.
- Fehler führen zu Warnungen, nicht zu Abbruch.

**Abhängigkeiten:** Log-Realitätscheck (B).

**Priorität:** Hoch.

---

### Ticket 2 — Log-Finder & Ingestion
**Ziel:** Logs finden & einlesen (macOS zuerst).

**Akzeptanzkriterien:**
- macOS-Pfad wird erkannt & dokumentiert (`~/Library/Logs/Wizards of the Coast/MTGA/`).
- Windows-Pfad vorbereitet (ggf. „nicht geprüft“).
- Log-Fund-Status im `run-report.json` sichtbar.

**Abhängigkeiten:** Realitätscheck-Pfadinfo.

**Priorität:** Hoch.

---

### Ticket 3 — Export-Artefakte
**Ziel:** `collection.json` + `run-report.json` erzeugen.

**Akzeptanzkriterien:**
- CLI: `mtga-export run` erzeugt beide Dateien.
- Warnungen/Fehler im Report enthalten.
- Export auch bei Problemen verfügbar.

**Abhängigkeiten:** Parser, Log-Finder.

**Priorität:** Hoch.

---

### Ticket 4 — Debug-Web-UI
**Ziel:** Lokale UI für Debug/Vertrauen.

**Akzeptanzkriterien:**
- Vollständigkeits-Badge, Zahlen, Warnungen sichtbar.
- Suchfeld vorhanden.
- Diagnostik einklappbar.
- Funktioniert auch bei Parse-Fehlern.

**Abhängigkeiten:** Export-Artefakte.

**Priorität:** Mittel.

---

### Ticket 5 — Optionales Names-Enrichment
**Ziel:** Lokales Mapping via Bulk-Download (opt-in).

**Akzeptanzkriterien:**
- Default OFF.
- Explizite Aktivierung mit Bestätigung.
- Mapping lokal, keine Live-API.
- UI zeigt Namen, wenn vorhanden.

**Abhängigkeiten:** UI, Export-Artefakte.

**Priorität:** Niedrig.
