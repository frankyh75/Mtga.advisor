# Sudo-Helper: Folge-Plan #3 — Testsuite grün, toten Mock-Pfad entfernen

> **Branch:** `feat/sudo-helper` (weiterarbeiten)
> **Assignee:** worker-heavy (GLM 5.2 via Ollama Cloud)
> **Basis:** Code-Review der Commits `21a83fb..b6d87ad` vom 2026-08-04 (siehe `docs/plans/2026-08-04-sudo-helper-fixes-2.md` für Runde 2).

**Was Runde 2 erreicht hat (verifiziert, nicht mehr anfassen):**
- `MemoryBackend`-Protocol in `scanner/pattern_scanner.py` (`PymemBackend`) — sauber, reines Refactoring, Verhalten unverändert.
- `HelperBackend` + `RemoteMemoryAdapter` in `scanner/helper_client.py` — Architektur korrekt.
- `check_peer_credentials()`-Fallback (gid 20/80) entfernt.
- `helper/mtga-helper` aus Git entfernt, `.gitignore` korrekt.
- `make -C helper build` ohne Compiler-Warnungen.

**Was diese Runde kaputt macht:** Der komplette Testlauf steht aktuell auf **16 von 234 Tests rot** (`python3 -m pytest scanner/tests/ -q` → `16 failed, 218 passed`, live verifiziert). Die Architektur aus Runde 2 ist richtig, wurde aber committed, ohne die eigene Testsuite laufen zu lassen. Zusätzlich validiert der e2e-Test einen Pfad, den kein Produktionscode mehr benutzt. Diese Runde repariert das — keine neuen Features, nur die Baustelle von Runde 2 fertigstellen.

---

## T1: `import struct` fehlt in `scanner/helper_client.py` (Blocker, zuerst)

**Objective:** `RemoteMemoryAdapter.read_ptr()`/`read_u32()`/`read_i32()` (`scanner/helper_client.py:234-242`) rufen `struct.unpack_from(...)` auf, das Modul importiert `struct` aber nie. Live reproduziert:
```
NameError: name 'struct' is not defined
```
Betrifft jeden Deck-Scan und Rank-Scan über den Helper — beide sind dadurch aktuell nicht benutzbar. Der zugehörige Test existiert bereits und ist rot (`scanner/tests/test_helper_client.py::test_remote_memory_adapter_read_ptr`, `::test_remote_memory_adapter_read_u32`).

**Changes:**
- `scanner/helper_client.py`: `import struct` zum bestehenden Import-Block hinzufügen (Zeile ~19-25, neben `import json`, `import os`, `import socket`, `import base64`).

**Files:**
- Modify: `scanner/helper_client.py`

**Verification:** `python3 -m pytest scanner/tests/test_helper_client.py -q` → alle Tests grün (vorher: 2 failed, 24 passed).

---

## T2: `scan_collection_detailed()` entduplizieren

**Objective:** `scanner/memory_scanner.py::scan_collection_detailed()` hat aktuell zwei fast identische ~80-Zeilen-Blöcke: den Helper-Zweig (Zeile ~390-478, baut `HelperBackend`) und den direkten Zweig (Zeile ~480-560+, baut `PymemBackend(pm)`). Beide führen dieselbe Anker-Suche, denselben Scan, dasselbe Block-Parsing aus — nur die Backend-Konstruktion unterscheidet sich. Das war in Plan #2 (T2) explizit als "ein gemeinsamer Pfad, nur das Backend wechselt" spezifiziert und wurde stattdessen dupliziert. Das ist keine akute Fehlerquelle, aber jede künftige Änderung an der Scan-Logik (Bugfix, neue Heuristik) muss sonst zweimal gemacht werden — und genau solche Divergenzen haben in Runde 1/2 bereits zu Bugs geführt.

**Changes:**
- `scan_collection_detailed()` umbauen: am Anfang **nur** das Backend bestimmen —
  ```python
  if use_helper is None:
      use_helper = is_helper_available(sock_path)

  if use_helper:
      print_fn("🔄 Scan über Helper-Daemon (sudo-frei)...")
      backend: MemoryBackend = HelperBackend(sock_path)
  else:
      candidate_names = tuple(process_names) if process_names is not None else get_macos_mtga_process_names()
      if not candidate_names:
          candidate_names = (get_macos_mtga_process_name(),)
      pm = _attach_process(candidate_names, print_fn=print_fn)
      if pm is None:
          return None
      backend = PymemBackend(pm)
  ```
  — danach folgt **ein einziger** Codeblock (Karten-DB laden, `get_user_anchors`, Scan, Block-Parsing, Validierung), der `backend` benutzt, unabhängig davon woher er kommt. Die beiden aktuell separaten Kopien werden zu diesem einen Block zusammengeführt (die Textunterschiede zwischen den beiden Varianten — z. B. "Der Helper-Daemon läuft" vs. "Das Script mit sudo läuft" in der Fehlermeldung — können als eine gemeinsame, backend-neutrale Meldung formuliert werden, z. B. "Stelle sicher, dass MTGA läuft und der Helper installiert ist oder das Script mit sudo läuft").

**Files:**
- Modify: `scanner/memory_scanner.py`

**Verification:** `scan_collection_detailed()` ist danach spürbar kürzer (ein Block statt zwei). `python3 -m pytest scanner/tests/test_memory_scanner.py -q` → weiterhin grün (Verhalten für den direkten Pfad unverändert).

---

## T3: `scanner/tests/test_scanner.py` — `FakePm` auf `MemoryBackend`-Protocol umstellen

**Objective:** Drei Tests schlagen fehl, weil `FakePm` (in `test_scan_process_memory_scans_full_regions`, `test_scan_process_memory_with_stats_reports_scan_scope`, `test_scan_process_memory_many_reads_region_once`, alle in `scanner/tests/test_scanner.py`) noch die alte Kopplung testet: sie monkeypatchen die Modulfunktionen `pattern_scanner._iterate_regions_with_error` und `pattern_scanner._read_bytes_silent`. Seit T1 aus Runde 2 rufen `scan_process_memory*` diese Funktionen aber nicht mehr auf — sie rufen `backend.iterate_readable_regions()` und `backend.read_bytes()` direkt auf dem übergebenen Objekt auf. Live reproduziert:
```
AttributeError: 'FakePm' object has no attribute 'iterate_readable_regions'
```

**Changes:** In allen drei Tests `FakePm` so erweitern, dass es das `MemoryBackend`-Protocol direkt implementiert, und die jetzt wirkungslosen `monkeypatch.setattr(pattern_scanner, "_iterate_regions_with_error", ...)`/`"_read_bytes_silent"`-Zeilen entfernen. Beispiel für `test_scan_process_memory_scans_full_regions`:
  ```python
  class FakeBackend:
      def read_bytes(self, addr: int, size: int) -> bytes:
          if addr == base_addr + 5:
              return b"x" + needle + b"y" * (size - 5)
          return b"x" * size

      def iterate_readable_regions(self) -> tuple[list[tuple[int, int]], int | None]:
          return [(base_addr, region_size)], None

  found = pattern_scanner.scan_process_memory(FakeBackend(), needle)
  ```
  Analog für die beiden anderen Tests (`iterate_readable_regions` liefert jeweils die Region, die vorher per `monkeypatch.setattr(pattern_scanner, "_iterate_regions_with_error", lambda pm: (...))` injiziert wurde; `read_bytes` ersetzt `fake_read_bytes_silent`).

**Files:**
- Modify: `scanner/tests/test_scanner.py`

**Verification:** `python3 -m pytest scanner/tests/test_scanner.py -q` → alle Tests grün.

---

## T4: `scanner/tests/test_memory_scanner_helper.py` neu schreiben

**Objective:** Alle 10 Tests in dieser Datei schlagen fehl — sie monkeypatchen `scanner.helper_client.helper_scan_collection_detailed` bzw. `helper_scan_collection`, Funktionen, die in Runde 2 (T2) korrekt gelöscht wurden, weil `scan_collection_detailed()` den Helper-Pfad seitdem selbst über `HelperBackend` abwickelt (siehe T2 in diesem Plan). Die Testdatei wurde beim Löschen dieser Funktionen nicht mitgezogen.

**Changes:** Datei neu ausrichten auf das tatsächliche Verhalten nach T2 — statt `helper_scan_collection_detailed` zu mocken, `HelperBackend` bzw. deren `read_bytes`/`iterate_readable_regions` mocken (oder `scanner.helper_client.is_helper_available` und die Anker-/DB-Funktionen, um den kompletten Ablauf gegen ein Fake-Backend zu fahren, analog zu T3). Zu prüfen bleibt weiterhin:
  - `use_helper=True` → Helper-Backend wird verwendet, kein `_attach_process`-Aufruf.
  - `use_helper=False` → Helper wird übersprungen, direkter Pfad läuft.
  - `use_helper=None` (Auto-Detect) → folgt `is_helper_available()`.
  - Fehlerfälle (Helper nicht erreichbar, keine Anker gefunden) liefern `None`.

**Files:**
- Modify: `scanner/tests/test_memory_scanner_helper.py`

**Verification:** `python3 -m pytest scanner/tests/test_memory_scanner_helper.py -q` → grün. Gesamtlauf: `python3 -m pytest scanner/tests/ -q` → `0 failed`.

---

## T5: Toten Mock-Pfad (`scan`/`deck_scan`/`rank_scan`) aus Helper + E2E-Test entfernen

**Objective:** `helper/main.c` hat einen `handle_mock_scan()`-Handler bekommen, der `action:scan/deck_scan/rank_scan` **nur im `--test-mode`** mit Fake-Daten beantwortet (`helper/main.c:479-521`, Dispatch-Gate `helper/main.c:643-646`), extra damit `helper/tests/test_e2e.sh` Tests 7-9 grün werden. Das Problem: **kein Python-Code sendet diese Actions mehr** — `scanner/helper_client.py` spricht seit Runde 2 nur noch `list_regions`/`read_memory` (über `HelperBackend`/`RemoteMemoryAdapter`). Der e2e-Test meldet "9/9" grün, testet dabei aber ein Protokoll, das mit dem tatsächlichen Scan-Ablauf nichts mehr zu tun hat — falsche Sicherheit, exakt das Muster, das schon in Runde 1 und 2 zu übersehenen Bugs geführt hat.

**Changes:**
- `helper/main.c`: `handle_mock_scan()` und das zugehörige Dispatch-Gate (`g_test_mode && (strcmp(action,"scan")==0 || ...)`) entfernen. Der Helper kennt danach im `--test-mode` weiterhin nur `ping`/`status`/`shutdown`/`list_regions`/`read_memory` (mit Mock-Daten für die letzten beiden) — konsistent mit dem echten Protokoll.
- `helper/tests/test_e2e.sh`: Tests 7-9 (scan/deck_scan/rank_scan) entfernen. Stattdessen dort verifizieren, was tatsächlich zählt: dass die *Python-Seite* mit dem laufenden `--test-mode`-Helper zusammenarbeitet. Konkret ein neuer Abschnitt, der ein kleines Python-Snippet gegen den laufenden Test-Socket ausführt:
  ```bash
  info "Test 7: HelperBackend liest über den echten Helper-Prozess..."
  BACKEND_CHECK=$(python3 -c "
import sys
sys.path.insert(0, '$PROJECT_ROOT')
from scanner.helper_client import HelperBackend
b = HelperBackend('$SOCK_PATH')
regions = b.iterate_readable_regions()
assert regions[0], 'keine Regionen'
addr, size = regions[0][0]
data = b.read_bytes(addr, min(size, 64))
assert data is not None and len(data) > 0, 'read_bytes lieferte nichts'
print('OK')
" 2>&1)
  if [[ "$BACKEND_CHECK" == "OK" ]]; then
      pass "HelperBackend funktioniert gegen den echten Helper-Prozess"
  else
      fail "HelperBackend-Check fehlgeschlagen: $BACKEND_CHECK"
  fi
  ```
  Das prüft den tatsächlichen Produktionscodepfad (`HelperBackend`, das `scan_collection_detailed()` benutzt), nicht ein Attrappen-Protokoll.
- Test-Zähler in `test_e2e.sh` entsprechend anpassen (Kommentar-Header + finale Erfolgsmeldung).

**Files:**
- Modify: `helper/main.c`
- Modify: `helper/tests/test_e2e.sh`

**Verification:** `bash helper/tests/test_e2e.sh` → alle Tests grün, und zwar gegen `HelperBackend` aus `scanner/helper_client.py`, nicht gegen einen Mock, den nur der Test selbst kennt.

---

## T6: Optional / niedrige Priorität

Nicht blockierend, aber offen — bei Kapazität mitnehmen:

- `cli/main.py::_run_deck_scan()` und `_run_ranks()` nutzen weiterhin nie `RemoteMemoryAdapter`/`helper_scan_decks()`/`helper_scan_ranks()` (nur `_run_scan()` wurde in Runde 2 umgestellt). Auto-Detect analog zu `_run_scan()` ergänzen, damit `deck-scan`/`ranks` ebenfalls ohne sudo laufen, wenn der Helper verfügbar ist.
- `helper/main.c`: `sb_free()` und `find_process()` sind weiterhin ungenutzt, nur mit `__attribute__((unused))` stummgeschaltet statt entfernt (`helper/main.c:210`, `helper/main.c:267`). Falls sie wirklich niemand mehr braucht: löschen statt stummschalten.

**Files:**
- Modify: `cli/main.py`
- Modify: `helper/main.c`

---

## Dependencies

```
T1 (struct-Import)                — keine, zuerst (Blocker)
T2 (Scan-Dedup)                   — keine, unabhängig von T1
T3 (test_scanner.py Fix)          — keine, unabhängig
T4 (test_memory_scanner_helper.py)— T2 (testet den deduplizierten Code)
T5 (Toter Mock-Pfad raus)         — T2 (E2E-Test soll den echten HelperBackend-Pfad prüfen)
T6 (optional)                     — keine
```

## Erfolgskriterien

- [ ] `python3 -m pytest scanner/tests/ -q` → `0 failed` (aktuell: 16 failed)
- [ ] Deck-Scan und Rank-Scan über `RemoteMemoryAdapter` funktionieren ohne `NameError`
- [ ] `scan_collection_detailed()` hat keinen duplizierten Scan-Code mehr (ein Pfad, zwei Backends)
- [ ] `helper/main.c` beantwortet `action:scan/deck_scan/rank_scan` in keinem Modus mehr (auch nicht `--test-mode`)
- [ ] `helper/tests/test_e2e.sh` prüft den echten `HelperBackend`-Pfad aus `scanner/helper_client.py`, nicht ein Attrappen-Protokoll
- [ ] `bash helper/tests/test_e2e.sh` → alle Tests grün

## Referenz

Vollständige Review-Zusammenfassung von Runde 3 (Commits `21a83fb..b6d87ad`) inkl. Live-Testläufen (`pytest`, `make -C helper build`, `test_e2e.sh`): siehe Konversation vom 2026-08-04.
