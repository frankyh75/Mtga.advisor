# Parser-Wechsel: Vom Log-Parsing zum Memory-Scanning

**Datum:** 2026-07-29
**Status:** Phase 0/1 → Neuausrichtung

## Warum der Wechsel?

Bisher basierte Mtga.advisor auf der Annahme, dass MTGA beim Betreten der Collection
die Kartendaten in den `Player.log` schreibt. **Diese Annahme war falsch.**

### Was passiert ist

| Datum | Ereignis |
|-------|----------|
| Vor Aug 2021 | `PlayerInventory.GetPlayerCardsV3` schrieb Collection in Log |
| **Aug 2021** | **Wizards entfernt `GetPlayerCardsV3` aus dem Log** |
| Seither | Kein Ersatz — Collection-Daten erscheinen nicht mehr im Log |

### Was stattdessen im Log ist

| Event | Enthält | Für Collection nutzbar? |
|-------|---------|------------------------|
| `StartHook` | Gold, Gems, Wildcards, **DeckSummariesV2** | ⚠️ Nur Decks, nicht Vollcollection |
| `Inventory.Updated` | Deltas (neue Karten) | ⚠️ Nur Deltas, kein Snapshot |
| GRE Messages | GameState | ❌ Nur während Matches |
| `RankGetCombinedRankInfo` | Rank | ❌ |

## Neuer Ansatz: Memory-Scanning

Statt Logs zu parsen, wird der **Arbeitsspeicher des laufenden MTGA-Prozesses**
nach Kartendaten durchsucht.

### Technologie

- **Windows:** `pymem` (C-Extension, Windows-API)
- **macOS:** `pymem-osx` (pure Python, Mach Kernel APIs via ctypes)
- **Vorbild:** `NthPhantom10/MTGA-collection-exporter` (v2.0, MIT License)

### Funktionsweise

```
MTGA-Prozess (läuft)
    │
    ├── pymem-osx attached via task_for_pid()
    │
    ├── User gibt 5 Anker-Karten ein (garantiert in Collection)
    │
    ├── Pattern-Scanner sucht Anker-IDs im Heap
    │   └── mach_vm_region() → Regionen iterieren
    │   └── data.find(needle) → Pattern in jeder Region
    │
    ├── Um jede Fundstelle: 4MB Block lesen
    │
    ├── Block parsen: (grpId, quantity)-Paare extrahieren
    │   └── Kriterium: 1000 ≤ grpId < 500000, 1 ≤ qty ≤ 400
    │
    └── Bester Block (meiste Einträge) = Collection
```

### Voraussetzungen

- MTGA muss **laufen**
- Collection-Tab muss **geladen** sein (vorher scrollen)
- `sudo` erforderlich (für `task_for_pid`)
- Apple Silicon (M1-M4) oder Intel macOS

## Auswirkungen auf die Architektur

### Neu: `scanner/` Modul

```
scanner/
├── __init__.py
├── macos_paths.py       # macOS-Pfade für MTGA-Daten
├── card_database.py     # Karten-DB (.mtga + Scryfall)
├── pattern_scanner.py   # mach_vm_region()-basierter Pattern-Scan
└── memory_scanner.py    # Hauptlogik (Anker, Block-Parsing)
```

### Bestehend: `parser/` Modul (bleibt)

Der Log-Parser bleibt erhalten für:
- **Wildcard-Deltas** aus `Inventory.Updated`
- **DeckSummariesV2** aus `StartHook` (Fallback/Validierung)
- **Rank** und andere Metadaten

### Hybrid-Ansatz

```
Initial:  Memory-Scan → Vollcollection
Danach:   Log-Parsing → Deltas (neue Karten aus Boostern/Drafts)
Fallback: DeckSummariesV2 → Teilcollection (nur Decks)
```

## Output-Format

Unverändert: `collection.json` (Schema `collection.v1`)

```json
{
  "schema": "collection.v1",
  "source": "memory-scan",
  "cards": {
    "114001": 4,
    "114002": 2
  },
  "wildcards": {},
  "diagnostics": {
    "completeness": "complete",
    "warnings": []
  }
}
```

## Offene Punkte

- [ ] Pattern-Scanner unter Rosetta 2 testen (MTGA läuft als x86_64)
- [ ] Performance optimieren (Heap kann mehrere GB gross sein)
- [ ] `sudo`-Wrapper für bequemeren Aufruf
- [ ] Automatische Prozess-Erkennung (Steam vs. Standalone)
- [ ] Integration: Memory-Scan-Output in bestehende Pipeline
