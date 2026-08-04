# Deck-Scan Roadmap v3 — Zuverlässigkeit, Format, Aufräumen

Datum: 2026-08-04

## Verhältnis zu anderen Docs

- `docs/deck-scan-problem-report.md` — Ursprungsproblem (`data_segment_base`
  nicht auffindbar) + Lösung (Backref-Ansatz über FieldInfo-Strings) +
  Nachträge vom 2026-08-04 (dynamische Region-Enumeration, Arena-Coalescing,
  Pattern-Scan-ID-Kollision). Bleibt technische Referenz für den
  IL2CPP-Navigationsmechanismus.
- **Diese Datei** — nächste Runde konkreter Verbesserungen, extern
  umsetzbar. Coding erfolgt extern (GLM 5.2 / Hermes); diese Datei ist die
  Aufgabenliste dafür, nicht der Implementierungsort.

## Ist-Zustand (Stand 2026-08-04, nach Commits `4d8d3f5` + `f26c6fd` auf `main`)

- IL2CPP-Backref-Scan (`scanner/il2cpp_nav.py::discover_decks_manager_via_backref`)
  liefert zuverlässig 21/21 echte Decks mit aufgelösten Kartennamen
  (`card_db` wird jetzt auch im IL2CPP-Zweig durchgereicht, `cli/main.py`).
- `--debug` loggt mit Zeitstempeln nach `out/deck-scan-debug.log`
  (`cli/main.py:_run_deck_scan`), inkl. Feldnamen-Dump von `Client_Deck`/
  `DeckSummary` für die ersten drei `_allDecks`-Einträge.
- **Trotzdem beobachtete Instabilität:** Derselbe laufende MTGA-Prozess
  (identische PID) lieferte an mehreren aufeinanderfolgenden Scan-Läufen
  nicht immer ein IL2CPP-Ergebnis — der alte `data_segment_base`-Fehler kam
  zurück, obwohl Region-Enumeration und Arena-Coalescing das ursprüngliche
  ASLR-Problem klar behoben haben. Vermutete Ursache: Heap-Layout ändert
  sich innerhalb des laufenden Prozesses (z. B. GC-Kompaktierung,
  UI-Zustand). Nicht abschließend geklärt — nur 3 Datenpunkte bisher (1x
  Erfolg, 2x Fehlschlag, gleicher Prozess).
- **Format wird nicht gelesen.** `Il2CppDeckResult` hat nur `deck_id`,
  `name`, `piles`, `raw_address` — kein Format-Feld (Standard/Historic/
  Brawl/...). Die Dashboard-Anzeige "Format der Decks ist unklar" kommt
  daher.
- **Pattern-Scan-Fallback hat sich zweimal als Quelle für kaputte Daten
  erwiesen:** einmal als Phantom-"Unknown Deck" mit einer `commandZone`,
  die 20+ verschiedene Karten-IDs à `count: 5` enthielt (strukturell
  unplausibel — echte Command Zones haben 1-4 Karten mit `count: 1`),
  einmal durch eine ID-Kollisions-Dedup-Bug (mittlerweile gefixt, siehe
  `4d8d3f5`). Die grundsätzliche Datenqualität des Parsers
  (`scanner/deck_scanner.py::_parse_deck_block`) ist weiter ungeprüft.
- **`scanner/rank_scanner.py` nutzt noch den alten
  `data_segment_base`/`TypeInfoTable`-Pfad**, nicht den neuen
  Backref-Ansatz — potenziell dieselbe Fehlerklasse wie der alte
  Deck-Scan-Bug.
- **Kein Schutz gegen Datenverlust beim Schreiben:** Ein Scan-Lauf
  überschreibt `out/decks.json` sofort, unabhängig davon, ob das neue
  Ergebnis schlechter ist als das vorherige. Heute live erlebt: ein
  OOM-Crash während eines unnötigen Pattern-Scans zerstörte eine
  189-Deck-Liste und hinterließ nur 1 Phantom-Deck.

## Sprint 1 — Format + Zuverlässigkeit (höchster Hebel)

1. **Format-Feld finden und lesen.** `deck-scan --method il2cpp --debug`
   liefert seit Fix `f26c6fd` zuverlässig die echten Feldnamen von
   `Client_Deck`/`DeckSummary` in `out/deck-scan-debug.log` (für die
   ersten 3 `_allDecks`-Einträge). Feld identifizieren (vermutlich etwas
   wie `Format`/`_format`/`FormatType`), Offset dynamisch per
   `get_class_fields()` lesen — **kein Hardcoding**, gleiches Muster wie
   das bereits bestehende `_deckDataProvider`-Feld. In
   `Il2CppDeckResult` und die `decks.json`-Ausgabe aufnehmen
   (`_run_deck_scan` in `cli/main.py`).
2. **Retry statt sofortigem Pattern-Fallback.**
   `discover_decks_manager_via_backref` bei Fehlschlag 2-3x mit kurzer
   Pause (z. B. 1-2s) erneut versuchen, bevor `--method auto` auf den
   unzuverlässigen Pattern-Scan zurückfällt. Ziel: die beobachtete
   Lauf-zu-Lauf-Instabilität abfedern, ohne den Pattern-Scan zu bemühen.
   Kalibrierung der Retry-Anzahl/Pause anhand echter Beobachtung (siehe
   offene Frage unten).
3. **Schreibschutz gegen Regressionen.** Vor dem Überschreiben von
   `out/decks.json`: prüfen, ob das neue Ergebnis plausibel ist (z. B.
   Deck-Anzahl nicht drastisch kleiner als beim vorherigen Lauf, oder
   mindestens ein Deck mit ≥ 40 Mainboard-Karten). Bei Verdacht: warnen
   und die alte Datei nach `out/decks.json.bak` sichern statt
   stillschweigend zu überschreiben. Hätte den heutigen Datenverlust
   verhindert.

## Sprint 2 — Pattern-Scan-Fallback klären

4. **Datenqualitäts-Audit Pattern-Scan.** Mit realen Testdaten prüfen, ob
   `merge_deck_results`/`_parse_deck_block`
   (`scanner/deck_scanner.py`) plausible Decks liefern oder strukturell
   Rauschen erzeugen (wie das heute beobachtete Phantom-Deck). Falls
   strukturell unzuverlässig: entweder den Parser reparieren (striktere
   Validierung der Pile-Struktur, z. B. Plausibilitätsgrenzen für
   `count`-Wiederholungen pro Pile) oder den Pattern-Scan als Fallback
   ganz entfernen, sobald IL2CPP + Retry (Sprint 1) zuverlässig genug ist.
5. **`rank_scanner.py` auf den Backref-Ansatz migrieren.** Nutzt noch
   `data_segment_base`/`TypeInfoTable` (gleiche Fehlerklasse wie der alte
   Deck-Scan-Pfad). PAPA-/`InventoryManager`-Navigation analog zu
   `discover_decks_manager_via_backref` umbauen (String-Backref statt
   `data_segment_base`-Rateversuch).

## Sprint 3 — Komfort (optional, niedrigere Priorität)

6. **Deck-Scan in Collection-/Rank-Scan integrieren** — ein gemeinsamer
   `sudo`-Aufruf statt drei getrennter CLI-Befehle mit je eigener
   Passwortabfrage.
7. **Watch-Mode:** optionaler Hintergrundprozess, der bei erkannten
   Deck-Änderungen automatisch neu scannt. Setzt voraus, dass die
   `DecksManager`-Instanzadresse zwischen Aufrufen im selben Prozess
   stabil bleibt — das erst verifizieren, nicht annehmen (siehe die in
   diesem Dokument beschriebene Lauf-zu-Lauf-Instabilität).

## Offene Fragen (vor Sprint 1 zu klären)

- Ist das Format tatsächlich direkt auf `DeckSummary`/`Client_Deck`
  lesbar, oder muss es aus einer anderen Struktur (z. B. einem
  `FormatFilter`/`CardStyle`-Objekt) kommen? Erst mit dem
  `--debug`-Feldnamen-Fund entscheiden, nicht vorab annehmen.
- Wie oft/wie lange sollte der Retry in Sprint-1-Punkt 2 versuchen, bevor
  auf Pattern-Scan zurückgefallen wird? Aktuell nur 3 Datenpunkte
  (1x sofort erfolgreich, 2x sofort fehlgeschlagen, gleicher Prozess) —
  zu wenig, um eine Pausenzeit fundiert zu wählen. Braucht mehr
  Beobachtung, bevor eine Zahl festgeschrieben wird.

## Erfolgskriterien

- [ ] `decks.json` enthält für jedes Deck ein `format`-Feld mit
      plausiblem Wert (Standard/Historic/Brawl/...)
- [ ] `deck-scan --method auto` liefert bei 3 aufeinanderfolgenden Läufen
      (gleicher MTGA-Prozess) mindestens 2x IL2CPP-Erfolg ohne
      Pattern-Fallback
- [ ] `out/decks.json` wird nie mit einem Ergebnis überschrieben, das
      drastisch weniger Decks/Karten enthält als der vorherige Lauf, ohne
      explizite Warnung
- [ ] Entscheidung zu Pattern-Scan-Fallback getroffen und dokumentiert
      (repariert oder entfernt)
- [ ] `rank_scanner.py` nutzt denselben Backref-Ansatz wie
      `il2cpp_nav.py` (kein `data_segment_base` mehr)
- [ ] Alle Tests grün: `python3 -m pytest scanner/tests/ cli/tests/ -v`
