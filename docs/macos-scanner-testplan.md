# macOS Scanner — Testplan

**Ziel:** Memory-Scanning der MTGA-Collection via `pymem-osx` auf macOS testen.
**Branch:** `main` (Commit `93e0d3d`)
**Ausführender:** Frank auf MacBook (MTGA aktiv)

---

## Voraussetzungen

- macOS (Apple Silicon oder Intel)
- MTGA installiert und lauffähig (Steam oder Standalone)
- Python 3.8+
- `sudo` (erforderlich für `task_for_pid`)

## Setup

```bash
# 1. Repo klonen (falls nicht vorhanden)
cd ~/workspace
git clone https://github.com/frankyh75/Mtga.advisor.git
cd Mtga.advisor

# 2. Dependencies installieren
pip install pymem-osx requests

# 3. Aktuellen Stand prüfen
git log --oneline -3
# → sollte 93e0d3d oder neuer zeigen
```

## Test 1: Karten-DB laden (ohne MTGA)

Prüft, ob die Karten-Datenbank lokal oder via Scryfall geladen werden kann.

```bash
python3 -c "
from scanner.card_database import load_card_database
db = load_card_database()
print(f'Karten in DB: {len(db)}')
if db:
    sample = list(db.items())[0]
    print(f'Beispiel: grpId={sample[0]} → {sample[1]}')
"
```

**Erwartet:** `Karten in DB: > 10000` (aus lokalen `.mtga`-Dateien oder Scryfall)

**Wenn fehlschlägt:**
- Keine `.mtga`-Dateien gefunden → Fallback auf Scryfall (dauert ~30s)
- Scryfall auch nicht → Internetverbindung prüfen

## Test 2: macOS-Pfade prüfen

```bash
python3 -c "
from scanner.macos_paths import get_macos_mtga_data_path, get_macos_log_path, get_macos_mtga_process_name
print(f'Data path: {get_macos_mtga_data_path()}')
print(f'Log path:  {get_macos_log_path()}')
print(f'Process:   {get_macos_mtga_process_name()}')
"
```

**Erwartet:** Data path existiert (wenn MTGA installiert), Log path existiert (wenn MTGA gelaufen), Process name = `MTGA`

**Wenn fehlschlägt:**
- Data path = `None` → MTGA nicht in Standardpfaden installiert
- Pfade in `scanner/macos_paths.py` anpassen

## Test 3: pymem-osx Grundfunktion (mit laufendem MTGA)

```bash
# MTGA starten und in Decks-Ansicht gehen
# Dann in SEPARATEM Terminal:
sudo python3 -c "
from pymem import Pymem
pm = Pymem('MTGA')
print(f'PID: {pm.pid}')
print(f'Base: {hex(pm.get_base_address())}')
"
```

**Erwartet:** PID und Base-Adresse werden ausgegeben.

**Wenn fehlschlägt:**
- `pymem.exception.ProcessNotFound` → MTGA läuft nicht oder heisst anders
  - Alternative Prozessnamen testen: `MTGALauncher`, `MTGArena`, `MTGA`
- `OSError: Operation not permitted` → `sudo` fehlt
- `Segmentation fault` → Rosetta 2-Kompatibilitätsproblem

## Test 4: Pattern-Scanner (mit laufendem MTGA)

```bash
sudo python3 -c "
from pymem import Pymem
from scanner.pattern_scanner import scan_process_memory
import struct

pm = Pymem('MTGA')
# 0x0001C0A8 = Beispiel-Karten-ID (Llanowar Elves)
needle = struct.pack('<I', 114001)
found = scan_process_memory(pm, needle)
print(f'Fundstellen für Karten-ID 114001: {len(found)}')
if found:
    print(f'Erste Adresse: {hex(found[0])}')
"
```

**Erwartet:** `Fundstellen: > 0` (wenn Karte in Collection)

**Wenn fehlschlägt:**
- `0` Fundstellen → Pattern-Scanner findet keine Regionen
  - `mach_vm_region()`-Iteration liefert nichts → ctypes-Schnittstelle prüfen
  - Karten-ID existiert nicht in dieser Collection → andere ID probieren
- `struct.error` → falsche Pack-Format

## Test 5: Vollständiger Collection-Scan

```bash
# MTGA läuft, Decks-Ansicht geöffnet, Collection gescrollt
sudo python3 -m scanner.memory_scanner
```

**Erwartet:** Interaktive Abfrage von 5 Anker-Karten, dann Scan, dann Ausgabe:
```
✅ 12345 unique Einträge gefunden!
📊 Collection: 50000 Karten, 12345 unique IDs
```

**Wenn fehlschlägt:**
- `❌ MTGA läuft nicht` → Prozessname falsch (siehe Test 3)
- `❌ Keine Anker-Karten im Speicher gefunden` → Pattern-Scanner findet nichts (siehe Test 4)
- `❌ Keine validen Datenblöcke gefunden` → `find_blocks()` parst nichts
  - Möglicherweise anderes Speicher-Layout unter Rosetta 2
  - `find_blocks()`-Logik in `scanner/memory_scanner.py` anpassen

## Fehlerdiagnose

### Pattern-Scanner findet keine Regionen

Häufigstes Problem. Teste die Region-Iteration isoliert:

```bash
sudo python3 -c "
from pymem import Pymem
from scanner.pattern_scanner import _iterate_regions

pm = Pymem('MTGA')
regions = _iterate_regions(pm)
print(f'Gefundene Regionen: {len(regions)}')
for addr, size in regions[:5]:
    print(f'  {hex(addr)} - {hex(addr+size)} ({size/1024/1024:.1f} MB)')
"
```

Wenn `0 Regionen` → `mach_vm_region()`-ctypes-Aufruf ist falsch.
Wenn `> 0 Regionen` → Pattern-Scan müsste funktionieren, Problem liegt im `data.find()`.

### Prozessname unbekannt

```bash
ps aux | grep -i mtg
```

Damit den exakten Prozessnamen finden und in `scanner/macos_paths.py` anpassen.

### pymem-osx installieren

```bash
pip install pymem-osx
# Bei Problemen:
pip install --upgrade pip setuptools
pip install pymem-osx --no-cache-dir
```

## Erfolgskriterien

- [ ] Test 1: Karten-DB geladen (> 10000 Einträge)
- [ ] Test 2: macOS-Pfade korrekt
- [ ] Test 3: pymem-osx attached an MTGA
- [ ] Test 4: Pattern-Scanner findet Karten-IDs
- [ ] Test 5: Collection wird vollständig extrahiert
- [ ] Output: `collection.json` mit gleichem Schema wie Log-Parsing
