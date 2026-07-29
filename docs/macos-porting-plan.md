# macOS-Portierung: Memory-Scanning für MTGA Collection

**Basiert auf:** NthPhantom10/MTGA-collection-exporter (v2.0, März 2026)
**macOS-Äquivalent:** pgkt04/pymem-osx (pure Python, Mach Kernel APIs via ctypes)
**Datum:** 2026-07-29

## 1. Architektur-Vergleich

```
NthPhantom10 (Windows)              →   macOS-Portierung
─────────────────────────────────────────────────────────────
pymem (C-Extension, Windows-API)    →   pymem-osx (ctypes, Mach-API)
pymem.Pymem("MTGA.exe")             →   pymem.Pymem("MTGA")  (anderer Prozessname)
pymem.pattern_scan_all()             →   eigener Pattern-Scanner (pymem-osx hat keinen)
get_local_mtga_path() (Windows-Pfade) →   macOS-Pfade (~/Library/Application Support/...)
fetch_scryfall_database()            →   identisch (HTTP-API, plattformunabhängig)
```

## 2. Neue Dateien im Repo

```
mtga-advisor/
├── scanner/                    # NEU: Memory-Scanning-Modul
│   ├── __init__.py
│   ├── memory_scanner.py       # macOS Memory-Scanning (pymem-osx)
│   ├── pattern_scanner.py      # Pattern-Scan (Ersatz für pymem.pattern_scan_all)
│   ├── card_database.py        # Karten-DB: lokal (.mtga) + Scryfall-Fallback
│   └── macos_paths.py          # macOS-spezifische Pfade
├── cli/
│   └── main.py                 # ERWEITERT: neuer Befehl "scan"
├── parser/                     # bleibt unverändert (Log-Parsing für Deltas)
└── requirements.txt            # ERWEITERT: +pymem-osx, +requests
```

## 3. Detaillierte Implementierung

### 3.1 `scanner/macos_paths.py` — macOS-Pfade finden

```python
# MTGA-Installationspfade auf macOS
# Standalone (Epic): /Applications/MTGA.app/...
# Steam:           ~/Library/Application Support/Steam/steamapps/common/MTGA/
# Logs:            ~/Library/Logs/Wizards Of The Coast/MTGA/Player.log
# Local DB (.mtga): ~/Library/Application Support/MTGA/MTGA_Data/Downloads/Raw/

def get_macos_mtga_data_path() -> Path | None:
    """Findet den Raw-Daten-Ordner mit .mtga SQLite-Dateien."""
    candidates = [
        Path.home() / "Library" / "Application Support" / "MTGA" / "MTGA_Data" / "Downloads" / "Raw",
        Path("/Applications/MTGA.app/Contents/Resources/MTGA_Data/Downloads/Raw"),
        # Steam:
        Path.home() / "Library" / "Application Support" / "Steam" / "steamapps" / "common" / "MTGA" / "MTGA_Data" / "Downloads" / "Raw",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None
```

### 3.2 `scanner/pattern_scanner.py` — Pattern-Scan (kritischster Teil)

`pymem-osx` hat **kein** `pattern_scan_all()`. Wir müssen selbst implementieren:

```python
import struct
from pymem import Pymem

def scan_process_memory(pm: Pymem, needle: bytes) -> list[int]:
    """
    Scannt den gesamten adressierbaren Speicher des Prozesses nach 'needle'.
    
    pymem-osx hat keine process_modules-Enumeration wie Windows pymem.
    Stattdessen: mach_vm_region() iterieren, jede Region lesen, Pattern suchen.
    
    Optimierung: Nur lesbare + private Regionen scannen (keine shared libraries).
    """
    addresses = []
    # TODO: mach_vm_region()-Iteration via ctypes
    # Für jede Region: pm.read_bytes(addr, size) → needle in data
    return addresses
```

**Wichtig:** NthPhantom10 sucht nach `struct.pack('<I', anchor_card_id)` — also der 4-Byte Integer der Karten-ID im Speicher. Der Pattern-Scanner muss den gesamten Heap durchsuchen.

### 3.3 `scanner/memory_scanner.py` — Hauptlogik

```python
from pymem import Pymem
from .pattern_scanner import scan_process_memory
from .card_database import load_card_database

def scan_collection(pm: Pymem, anchors: list[tuple[int, int, str]], db: dict) -> dict[int, int]:
    """
    1. Für jede Anker-Karte: Pattern-Scan nach ihrer grpId
    2. Um jede Fundstelle: 4MB Block lesen
    3. Block in (grpId, quantity)-Paare parsen
    4. Besten Block (meiste Einträge) als Collection zurückgeben
    """
    matches = []
    for grp_id, qty, name in anchors:
        needle = struct.pack('<I', grp_id)
        found = scan_process_memory(pm, needle)
        matches.extend(found)
    
    # Um jede Fundstelle 4MB lesen und nach (k,v)-Paaren scannen
    candidates = []
    for addr in matches:
        block_start = max(0, addr - 1024*1024)
        data = pm.read_bytes(block_start, 4*1024*1024)
        ints = struct.unpack(f'<{len(data)//4}I', data)
        # ... gleiche Logik wie NthPhantom10's find_blocks()
    
    return max(candidates, key=len)  # Block mit meisten Karten
```

### 3.4 `scanner/card_database.py` — Karten-DB

```python
# Gleiche Logik wie NthPhantom10, aber:
# - macOS-Pfade für .mtga-Dateien (siehe macos_paths.py)
# - Scryfall-Fallback identisch (HTTP-API)
# - Cache-Datei: ~/.mtga_advisor/arena_id_lookup.json
```

### 3.5 CLI-Erweiterung (`cli/main.py`)

Neuer Subcommand `scan`:

```
mtga-export scan [--sudo] [--anchors NAME QTY ...]
```

- `--sudo`: Hinweis, dass `sudo` nötig ist (pymem-osx braucht root)
- `--anchors`: Optional 5 Karten + Mengen (sonst interaktiv)
- Output: Gleiches `collection.json`-Format wie Log-Parsing

## 4. Abhängigkeiten

```txt
# requirements.txt ERWEITERT
pymem-osx>=0.1.0    # macOS Memory Access (pip install pymem-osx)
requests>=2.31.0    # Scryfall API (schon indirekt da, aber explizit machen)
```

## 5. Risiken & offene Fragen

| Risiko | Lösung |
|--------|--------|
| **Pattern-Scan auf macOS langsam** | Heap ist größer als Windows. Optimierung: nur private/anon-Regionen scannen, shared libs überspringen. |
| **`sudo` erforderlich** | `task_for_pid()` braucht Root. Workaround: `sudo`-Wrapper-Script oder LaunchDaemon mit `com.apple.system-task-ports` entitlement. |
| **MTGA-Prozessname auf macOS** | Vermutlich `MTGA` (nicht `MTGA.exe`). Steam-Version könnte anders heißen. |
| **.mtga-Dateien auf macOS** | Pfad unbekannt. Möglicherweise anders als Windows. Muss getestet werden. |
| **pymem-osx Pattern-Scan fehlt** | Eigenbau nötig. `mach_vm_region()` + `mach_vm_read()` in Schleife. |
| **Apple Silicon Rosetta?** | MTGA läuft unter Rosetta 2 (x86_64). Memory-Layout könnte anders sein. |

## 6. Test-Strategie

1. **Unit-Tests:** Pattern-Scanner mit synthetischem Memory-Block testen
2. **Integration auf MacBook:** `pymem-osx` installieren, an MTGA attach-en, Prozessname verifizieren
3. **Anker-Karten:** 5 bekannte Karten aus Franks Collection eingeben
4. **Vollscan:** Komplette Collection extrahieren, mit Scryfall-DB matchen
5. **Output-Format:** Gleiches `collection.json`-Schema wie Log-Parsing

## 7. Build & Deployment

```bash
# Auf Mac Studio bauen
cd /Users/agent/workspace/Mtga.advisor
pip install pymem-osx requests
python -m pytest  # Unit-Tests laufen lassen

# Auf MacBook deployen
git push origin main
# Auf MacBook: git pull + sudo python -m cli.main scan
```

## 8. Nächste Schritte (Priorisiert)

1. **Phase 1 — Foundation:** `scanner/macos_paths.py` + `scanner/card_database.py` schreiben
2. **Phase 2 — Pattern-Scanner:** `scanner/pattern_scanner.py` mit `mach_vm_region()`-Iteration
3. **Phase 3 — Memory Scanner:** `scanner/memory_scanner.py` mit Block-Parsing
4. **Phase 4 — CLI:** `cli/main.py` um `scan`-Befehl erweitern
5. **Phase 5 — Test auf MacBook:** Frank deployed und testet
6. **Phase 6 — Integration:** Memory-Scan-Output in bestehendes `collection.json`-Schema mappen
