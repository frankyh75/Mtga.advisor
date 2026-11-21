# Spec: Decklisten aus dem Arena-Speicher lesen (Deckmanager)

Datum: 2026-11-21 · Autor: Jarvis · Status: Entwurf zur Umsetzung

## Ausgangsfrage

Ist der Deckmanager eine Sackgasse? **Nein.** Die alte Sackgasse
(`no data_segment_base` → IL2CPP-Navigation bricht ab, `docs/deck-scan-problem-report.md`,
2026-08-02) betrifft den Pfad *PAPA → DecksManager → _allDecks* über Segment-Erkennung.
Dieser Pfad wird nicht mehr benutzt. Der Metadaten-Weg, mit dem die Sammlung
nachweislich gelesen wurde (8.610 unique / 17.876 Kopien, 2026-11-21), gilt für Decks genauso.

## Was belegt ist (aus der laufenden Arena-Metadata, 2026-11-21)

- Klassennamen vorhanden: `DecksManager`, `DeckManager`, `DeckInfo`, `DeckSummary`,
  `Deck`, `DeckCard`, `DeckCardList`, `DeckFormat`, `DeckType`, `DeckValidation`.
- Feldnamen vorhanden: `mainDeck`, `sideboard`, `deckId`, `deckName`, `deckType`,
  `format`, `lastUpdated`, `commandZone`, `companions`, `attachments`, `cardId`, `quantity`.
- Backing-Fields vorhanden: `<Decks>`, `<Deck>`, `<DeckId>`, `<DeckCard>`, `<DeckConfig>`.
- Die Sammlung ließ sich mit derselben Technik dekodieren (Klasse im Metadata suchen,
  Instanz über Klassadresse finden, Feldpfad lesen) — ohne feste Adressen.

## Was offen ist (die zwei echten Risiken)

1. **Lazy-Load:** Ob Arena die Decklisten schon im Manager hält, bevor die Decks-Ansicht
   geöffnet wurde, ist ungeprüft. Falls nicht: Warteschleife mit Szenen-Trigger
   (Deck-View / Deck-Builder im `Player.log`) statt Einmal-Messung.
2. **Feld-Offsets:** `get_class_fields` ist für diese Client-Version nachweislich unbrauchbar
   (`field_count = 131076`, `NavigationReadError 0x202ffff`). Offsets müssen über
   Werte-Hypothesen empirisch bestimmt werden — so wie beim Sammlungs-Dictionary.

## Vorgehen

1. **Einstieg ohne feste Adressen:** Klassennamen `DecksManager` (Fallback `DeckManager`,
   `DeckInfo`) im Metadata-Blob suchen; nur das Ergebnis cachen, nie die Adresse über
   einen Arena-Neustart hinweg.
2. **Instanz finden:** Heap-Bereiche nach Objekten durchsuchen, deren Klassenzeiger auf die
   gefundene Klasse zeigt (gleiche Routine wie beim Sammlungsscan).
3. **Feldpfad:** `<Decks>k__BackingField` → Container → `DeckInfo` (`deckId`, `deckName`,
   `format`) → Deck-DTO → `mainDeck` / `sideboard` als Liste von `{cardId, quantity}`,
   dazu `commandZone`, `companions`, `attachments`.
4. **Container-Layouts:** `List<T>` = `_items`-Zeiger + `_size` (nicht mit dem
   Dictionary-Layout der Sammlung verwechseln); Strings über die vorhandenen
   IL2CPP-Lesehelfer.
5. **Ausgabe:** `out/deck-cards.json` im bestehenden Schema `deck-cards.v1`
   (via `parser.deck_cards.upsert_deck_cards`), zusätzlich `out/decks.json`-Summary.
   Damit erscheinen die Decks ohne Zusatzarbeit in der bestehenden GUI-Deckliste.

## Validierung — Struktur hart, keine Anker

Harte Prüfungen (sonst **kein** Schreiben, Reason `struct_mismatch`):

- mindestens 1 Deck, mindestens 1 Karte
- `cardId` in der Karten-DB **oder** im Band 1.000–2.000.000
- `quantity` 1–250 (Grundländer erlaubt große Mengen)
- `deckId` ist ein 16-stelliger Hex-String
- `deckName` nicht leer und druckbar

Nur Warnungen (Reason `deck_size_implausible`): Deckgröße außerhalb 60 (± 15 Sideboard),
40 (Limited), 100 (Commander/Brawl).

Es gibt **keine** Anker-Karten für Decks — Decks ändern sich legal. Stattdessen:
zwei aufeinanderfolgende Läufe dürfen nicht plötzlich 0 Decks liefern, solange der
vorherige Lauf Decks hatte (Reason `regression_suspected`).

## Warten statt messen

Gleiche Warteschleife wie beim Collection-Watcher (`docs/spec-collection-watcher.md`):
Poll alle 5 s, Sofort-Trigger bei Szenenwechsel im `Player.log`, im Duell nicht messen.
Zustände: `no_process`, `no_metadata`, `no_class`, `no_instance`, `struct_mismatch`,
`regression_suspected`. Nie eine leere Datei schreiben.

## Nicht-Ziele

- Keine festen Offsets pro Unity-Version (Ursache des Fremd-Tool-Fehlers Issue #4).
- Kein Pattern-Scan (liefert nur Anker, keine Kartenlisten).
- Kein Log-Parsing (`DeckGetDeckLists` seit August 2021 nicht mehr im Log, geprüft: 0 Treffer).
- Kein GPL-Code aus fremden Readern.

## Akzeptanzkriterien

1. Ein Aufruf im Wartemodus schreibt `deck-cards.json` mit allen Decks des Kontos und
   vollständigen Kartenlisten (`mainDeck`, `sideboard` mit `cardId` + `quantity`).
2. Zweiter Aufruf bei unverändertem Zustand antwortet in < 15 s.
3. Ohne geöffnete Decks-Ansicht: entweder korrektes Ergebnis oder klare Wartemeldung —
   niemals eine leere oder halbe Datei.
4. Unittest mit synthetischem Speicher (Fixture), plus ein Live-Lauf gegen Arena.
5. Die GUI zeigt die gelesenen Decks ohne Codeänderung (Schema `deck-cards.v1`).
