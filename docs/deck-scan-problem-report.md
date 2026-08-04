# Deck Scan Problem Report

Datum: 2026-08-02

## Kurzfassung

Der `deck-scan`-Pfad auf macOS läuft aktuell nur teilweise über IL2CPP.
Der Pattern-Scan findet Deck-Kandidaten, aber die echte IL2CPP-Navigation
bricht beim automatischen Finden von `data_segment_base` ab.

## Beobachtetes Verhalten

Aktueller Ablauf:

1. Verbindung zu MTGA wird hergestellt.
2. Karten-DB wird geladen.
3. IL2CPP-Navigation versucht, `PAPA -> DecksManager -> _allDecks` zu finden.
4. Der Scan meldet:

```text
no data_segment_base could be discovered for class discovery
```

5. Danach läuft der Pattern-Scan weiter und findet Deck-Anker.
6. Exportiert wird am Ende nur der Pattern-basierte Teil.

## Reproduzierbarer Aufruf

```bash
sudo -E .venv/bin/python -m cli.main deck-scan --method auto --card-db --output out-decks
```

## Bisherige Erkenntnisse

- Der Fehler entsteht nicht beim Kartenexport.
- Der Fehler entsteht vor der eigentlichen IL2CPP-Klassenauflösung.
- Die IL2CPP-Klasse kann nur dann gefunden werden, wenn die Runtime-Basis
  von `GameAssembly` und daraus die passende `__DATA`-Basis korrekt ermittelt
  werden.
- Der Pattern-Scan bleibt als Fallback funktionsfähig.

## Vermutete Ursache

Die automatische Erkennung von `data_segment_base` ist auf dem konkreten
macOS-System noch nicht robust genug.

Mögliche Gründe:

- `GameAssembly` wird im Prozess nicht so gemeldet, wie der Finder es erwartet.
- Die Section-/Segment-Erkennung liefert nicht die erwartete `__DATA_CONST`
  oder `__DATA`-Adresse.
- Die indirekte Validierung der `TypeInfoTable` scheitert vorzeitig.

## Aktueller Stand im Code

- IL2CPP-Deckscan versucht jetzt automatisch, `data_segment_base` zu finden.
- Rank-Scan nutzt denselben Fallback.
- Es gibt einen Test für die Mach-O- und Modulerkennung.
- Der Live-Prozess auf diesem Mac liefert trotzdem noch keinen passenden
  `data_segment_base`-Treffer.

## Nächste Schritte

1. Die geladenen MTGA-Module und ihre Sections direkt ausgeben.
2. Den tatsächlichen `GameAssembly`-Namen und die Startadresse im Live-Prozess
   verifizieren.
3. Falls nötig die Erkennung auf einen engeren macOS-spezifischen Pfad
   umbauen.
4. Erst danach die IL2CPP-Navigation als primären Deck-Pfad aktivieren.

## Erwartetes Ergebnis

Wenn `data_segment_base` zuverlässig gefunden wird, sollte der Deck-Scan
ohne Pattern-Fallback direkt Decknamen und Kartenlisten aus dem IL2CPP-Speicher
lesen können.

## Lösung (2026-08-04)

Live-Debugging am laufenden MTGA-Prozess (macOS, ARM64, Rosetta 2) hat
gezeigt, dass die ursprüngliche `data_segment_base`/TypeInfoTable-Annahme
grundsätzlich nicht zutrifft:

- `Il2CppClass`-Structs und ihre `FieldInfo`-Arrays sind **heap-allokiert**
  zur Laufzeit, nicht Teil von GameAssembly.dylibs eigenen
  `__DATA`/`__DATA_CONST`-Segmenten. Ein vollständiger Scan beider Segmente
  fand null Treffer für bekannte Klassen-/Feldnamen.
- Die "zweites `__DATA`-Segment"-Logik (übernommen aus mtgatools
  Rust-Implementierung) beruhte auf einem Missverständnis von
  `vmmap`-Output — `pymem.get_modules(extended=True)` und direkter
  `vmmap -wide`-Vergleich zeigten nur ein echtes `__DATA`-Segment.
- `PAPA_HEAP_REGIONS` (ebenfalls aus mtgatool übernommen) enthält
  zuverlässig die `FieldInfo`-Metadaten-Arrays, aber **nicht** die lebenden
  Objekt-Instanzen — die liegen weit verstreut im allgemeinen Managed Heap.

**Neuer Ansatz** (`scanner/il2cpp_nav.py::discover_decks_manager_via_backref`):

1. String-Adresse von `"_allDecks"` in `global-metadata.dat` finden (per
   Byte-Suche, kein hardcodierter Datei-Offset).
2. In `PAPA_HEAP_REGIONS` nach einem `FieldInfo`-Eintrag suchen, der auf
   diese Adresse zeigt → `FieldInfo.parent` liefert direkt die
   `DeckDataProvider`-Klasse (kein TypeInfoTable nötig).
3. `Il2CppClass`-Structs eines Builds liegen gemeinsam in einer Heap-Arena —
   `DecksManager` wird per Namens-Backref in derselben Region gefunden.
4. Das echte `_deckDataProvider`-Feld-Offset wird aus `DecksManager`s
   eigenem `FieldInfo`-Array gelesen (nicht hardcodiert).
5. Beschreibbarer, nicht geteilter Prozessspeicher (`__DATA`/Shared Cache
   ausgeschlossen) wird nach einer Instanz durchsucht, deren
   Objekt-Header-Pointer auf die `DecksManager`-Klasse zeigt; validiert über
   das `_deckDataProvider`-Feld.

Ergebnis: `deck-scan --method il2cpp` liest jetzt direkt 21/21 echte Decks
mit korrekten Karten-IDs und Mengen, ohne Pattern-Scan-Fallback. Der alte
TypeInfoTable-Pfad bleibt als Fallback erhalten (u.a. für `rank_scanner.py`,
das PAPA/InventoryManager separat braucht und noch nicht auf den neuen
Ansatz umgestellt ist).
