# Sudo-Helper: Folge-Plan #2 — Collection-Scan tatsächlich sudo-frei machen

> **Branch:** `feat/sudo-helper` (weiterarbeiten)
> **Assignee:** worker-heavy (GLM 5.2 via Ollama Cloud)
> **Basis:** Code-Review der Commits `d383f3b..a51b92b` vom 2026-08-04 (siehe `docs/plans/2026-08-04-sudo-helper-fixes.md` für Runde 1).

**Was Runde 1 erreicht hat (verifiziert, nicht mehr anfassen):**
- `task_for_pid()` löst den Zielprozess serverseitig auf, nimmt keine Client-PID mehr (`helper/main.c:249`).
- Socket liegt unter `/var/run/mtga-helper.sock`, `umask()` vor `bind()`, Peer-Credential-Check via `getpeereid()`.
- Build-Kette (`make -C helper bundle/sign/verify/test`) funktioniert, `helper/tests/test_e2e.sh` läuft grün gegen das echte Binary (6/6, live verifiziert).

**Was weiterhin fehlt:** `helper/main.c` implementiert nur noch die Low-Level-Primitives `ping`/`status`/`shutdown`/`list_regions`/`read_memory`. `scanner/helper_client.py` sendet aber weiterhin `{"action":"scan"}`, `{"action":"deck_scan"}`, `{"action":"rank_scan"}` — Actions, die der Server nicht mehr kennt (`{"error":"Unknown action: scan"}`, live reproduziert). Der komplette "sudo-freie Scan"-Pfad (`cli/main.py::_run_scan_via_helper` → `scanner/helper_client.py::helper_scan_collection_detailed` → `request_scan()`) läuft ins Leere. **Das ist der eigentliche Zweck des gesamten Branches und muss in dieser Runde fertig werden.**

Dieser Plan schließt die Lücke, indem er die Scan-Logik, die heute fest an ein lokales `Pymem`-Objekt (= lokaler `task_for_pid`, braucht sudo) gekoppelt ist, hinter eine austauschbare Backend-Abstraktion zieht. Der Helper liefert dann nur noch rohe Bytes (`list_regions`/`read_memory`), die komplette Pattern-Matching- und IL2CPP-Navigationslogik bleibt unverändert in Python (`scanner/pattern_scanner.py`, `scanner/il2cpp_nav.py`) — kein Code wird dupliziert oder in C nachgebaut.

---

## T1: `MemoryBackend`-Abstraktion in `pattern_scanner.py`

**Objective:** Die Collection-Scan-Logik (`scan_process_memory*`, `_scan_region*`) von `pm: Pymem` auf ein austauschbares Backend umstellen. Aktuell ist `pm` an genau zwei Stellen gekoppelt: `_task_port(pm)` (Zeile 113) und darüber `_read_bytes_silent(pm, addr, size)` (Zeile 282) sowie `_iterate_writable_private_regions(pm)` (Zeile 232). Das sind die einzigen zwei Primitives, die eine Backend-Klasse kapseln muss.

**Changes:**
- Neu in `scanner/pattern_scanner.py`: ein `Protocol` (oder einfach Duck-Typing, kein `abc` nötig) `MemoryBackend` mit genau zwei Methoden:
  ```python
  class MemoryBackend(Protocol):
      def read_bytes(self, addr: int, size: int) -> bytes | None: ...
      def iterate_writable_private_regions(self) -> list[tuple[int, int]]: ...
  ```
- Neu: `class PymemBackend` — verschiebt die bestehende Logik aus `_task_port`/`_read_bytes_silent`/`_iterate_writable_private_regions` unverändert hinein (nur Umbenennung von freien Funktionen zu Methoden). Verhalten bleibt exakt identisch zum Status quo — reines Refactoring, keine Verhaltensänderung.
- Alle Funktionen, die aktuell `pm: Pymem` als ersten Parameter nehmen (`_scan_region`, `_scan_region_many`, `scan_process_memory`, `scan_process_memory_with_stats`, `scan_process_memory_many`, `scan_process_memory_many_with_stats`), auf `backend: MemoryBackend` umstellen. Intern werden `_read_bytes_silent(pm, ...)` → `backend.read_bytes(...)` und `_iterate_writable_private_regions(pm)` → `backend.iterate_writable_private_regions()`. Sonst ändert sich an der Scan-/Matching-Logik nichts.
- `scanner/memory_scanner.py::find_blocks(pm, addr)` ebenfalls auf `find_blocks(backend, addr)` umstellen (nutzt intern `_read_bytes_silent`, Zeile 64).
- `scanner/memory_scanner.py::scan_collection_detailed()` (direkter Pfad, ab Zeile ~400): `pm = _attach_process(...)` bleibt, aber vor dem eigentlichen Scan wird `backend = PymemBackend(pm)` gebaut und an alle Aufrufe von `scan_process_memory_many_with_stats(...)`/`find_blocks(...)` durchgereicht statt `pm` direkt.

**Files:**
- Modify: `scanner/pattern_scanner.py`
- Modify: `scanner/memory_scanner.py`

**Verification:** Bestehende Tests für `pattern_scanner.py`/`memory_scanner.py` laufen unverändert grün (reines Refactoring — falls Tests direkt `pm` an diese Funktionen übergeben, entweder Tests auf `PymemBackend(pm)` anpassen oder — falls `Pymem`-Objekte in Tests bereits `read_bytes`/Region-Iteration mocken — die Mocks entsprechend auf die neuen Methodennamen ummünzen). `python3 -m pytest scanner/tests/test_pattern_scanner.py scanner/tests/test_memory_scanner.py` → grün.

---

## T2: `HelperBackend` + Collection-Scan über den Helper (ersetzt kaputten `request_scan`-RPC)

**Objective:** Mit T1 kann jetzt ein zweites Backend gebaut werden, das dieselbe Scan-Logik gegen den Helper-Daemon fährt — ohne dass `pattern_scanner.py` davon weiß, dass die Bytes über einen UNIX-Socket statt lokalem `task_for_pid` kommen.

**Changes:**
- Neu in `scanner/helper_client.py`: `class HelperBackend`, implementiert `MemoryBackend` (T1):
  ```python
  class HelperBackend:
      def __init__(self, sock_path: str = DEFAULT_SOCK_PATH) -> None:
          self.sock_path = sock_path
          self._regions_cache: list[tuple[int, int]] | None = None

      def read_bytes(self, addr: int, size: int) -> bytes | None:
          # Helper deckelt read_memory bei 16 MB (MAX_READ_SIZE in helper/main.c) —
          # bei size > 16 MB in mehreren request_read_memory()-Aufrufen chunken
          # und zusammenfügen. Bei HelperError/HelperConnectionError → None
          # zurückgeben (Silent-Fail-Semantik wie _read_bytes_silent).
          ...

      def iterate_writable_private_regions(self) -> list[tuple[int, int]]:
          # request_list_regions() einmal aufrufen und cachen (self._regions_cache),
          # da list_regions pro Scan-Lauf mehrfach gebraucht wird (Anker-Suche
          # iteriert ggf. mehrfach über dieselben Regionen).
          ...
  ```
  `HELPER_READ_CHUNK = 16 * 1024 * 1024` als Konstante analog zu `MAX_READ_SIZE` in `helper/main.c:200` einführen, damit beide Seiten synchron bleiben.
- `scanner/memory_scanner.py::scan_collection_detailed()`: den kompletten Helper-Zweig (aktuell Zeile ~381-394, delegiert an `helper_scan_collection_detailed()`) ersetzen. Neuer Ablauf, wenn `use_helper` True ist:
  1. `backend = HelperBackend(sock_path)`
  2. Karten-DB laden, `name_to_id` bauen (identisch zum direkten Pfad)
  3. `get_user_anchors(name_to_id, input_fn=input_fn, print_fn=print_fn)` — **exakt derselbe interaktive Anker-Flow wie beim direkten Scan**, keine Änderung am UX.
  4. Ab hier ist der Code **identisch** zum direkten Pfad (Schritt 5 "Memory-Scan" bis Ende in `scan_collection_detailed`) — nur dass `backend` statt `PymemBackend(pm)` durchgereicht wird.
  5. Praktisch heißt das: die Verzweigung `if use_helper: ... else: ...` sollte nur noch bestimmen, **welches Backend gebaut wird** (`HelperBackend(sock_path)` vs. `PymemBackend(_attach_process(...))`), der gesamte Rest der Funktion (Anker, Scan, Block-Parsing, Validierung) läuft für beide Pfade durch denselben Code. Das eliminiert die separate, duplizierte (und kaputte) Implementierung in `helper_scan_collection_detailed()`.
- `scanner/helper_client.py::helper_scan_collection()`, `helper_scan_collection_detailed()`, `request_scan()`: **entfernen** (werden durch den vereinheitlichten Pfad in T2 überflüssig — siehe T4 für die genaue Aufräum-Liste).
- `cli/main.py::_run_scan_via_helper()` / `_run_scan()`: prüfen, ob nach der Vereinheitlichung noch zwei getrennte Funktionen nötig sind, oder ob `_run_scan()` direkt `scan_memory_collection_detailed(debug=..., use_helper=None)` aufrufen kann (Auto-Detect passiert dann intern in `scan_collection_detailed`, wie es der `--helper`-Flag in `memory_scanner.py::main()` bereits vorsieht).

**Files:**
- Modify: `scanner/helper_client.py`
- Modify: `scanner/memory_scanner.py`
- Modify: `cli/main.py`

**Verification:** Mit laufendem Helper im `--test-mode` (liefert Mock-Regionen/-Bytes, siehe `helper/main.c:handle_list_regions`/`handle_read_memory`): `python3 -m cli.main scan --output /tmp/scan-test` mit `MTGA_HELPER_SOCK` auf den Test-Socket gesetzt → Scan läuft durch den Helper-Pfad, ohne `{"error":"Unknown action"}`. Ein manueller End-to-End-Test mit echtem MTGA-Prozess und installiertem Helper (kein `--test-mode`) muss zusätzlich echte Kartendaten liefern (`collection.json` mit >0 Karten) — das ist der eigentliche Beweis, dass das Feature fertig ist.

---

## T3: `RemoteMemoryAdapter` für Deck-Scan + Rank-Scan über den Helper

**Objective:** Im Gegensatz zum Collection-Scan (T1/T2) ist die IL2CPP-Navigation (`scanner/il2cpp_nav.py`) bereits sauber hinter einem Adapter-Protokoll gekapselt — `PymemMemoryAdapter` (`scanner/il2cpp_nav.py:1272`) implementiert `.pid`, `.read_bytes(addr, size)`, `.read_ptr`, `.read_u32`, `.read_i32`, `.read_string`. `scan_decks_il2cpp(adapter)` und `scan_ranks_and_account(adapter, ...)` sind bereits adapter-agnostisch. Hier reicht ein neuer Adapter, kein Refactoring der Navigationslogik.

**Changes:**
- Neu in `scanner/helper_client.py`: `class RemoteMemoryAdapter`, gleiche Schnittstelle wie `PymemMemoryAdapter`:
  ```python
  class RemoteMemoryAdapter:
      def __init__(self, sock_path: str = DEFAULT_SOCK_PATH) -> None:
          self.sock_path = sock_path

      @property
      def pid(self) -> int:
          return get_status(self.sock_path).pid or 0

      def read_bytes(self, addr: int, size: int) -> bytes:
          try:
              return request_read_memory(self.sock_path, address=addr, size=size)
          except (HelperError, HelperConnectionError, HelperProtocolError):
              return b"\x00" * size   # gleiche Fail-Semantik wie PymemMemoryAdapter.read_bytes

      def read_ptr(self, addr: int) -> int: ...   # identisch zu PymemMemoryAdapter (struct.unpack "<Q")
      def read_u32(self, addr: int) -> int: ...   # identisch ("<I")
      def read_i32(self, addr: int) -> int: ...   # identisch ("<i")
      def read_string(self, addr: int) -> str: ...  # identisch (0-terminierter String, max 256 Bytes)
  ```
  (Die vier letzten Methoden 1:1 aus `PymemMemoryAdapter` kopieren — reine Byte-Interpretation, keine pm-Abhängigkeit.)
- `scanner/helper_client.py::helper_scan_decks()`: statt (kaputtem) `request_deck_scan()` jetzt `scan_decks_il2cpp(RemoteMemoryAdapter(sock_path))` aufrufen (Import aus `scanner.il2cpp_nav`), Ergebnis ins bestehende Dict-Format konvertieren (wie es `cli/main.py::_run_deck_scan` für den lokalen Pfad bereits tut, Zeile ~1672-1683 — dieselbe `_piles_to_card_dict`/`_piles_to_cards_by_id`-Logik wiederverwenden).
- Neu: `helper_scan_ranks()` in `scanner/helper_client.py`, analog: `scan_ranks_and_account(RemoteMemoryAdapter(sock_path), read_account=...)`.
- `cli/main.py::_run_deck_scan()` und `_run_ranks()`: Auto-Detect ergänzen (Helper verfügbar? → `RemoteMemoryAdapter` statt `PymemMemoryAdapter(pm)`), analog zum bereits vorhandenen Muster in `_run_scan()`.
- `request_deck_scan()`/`request_rank_scan()` in `scanner/helper_client.py` entfernen (totes RPC-Protokoll, siehe T4).

**Files:**
- Modify: `scanner/helper_client.py`
- Modify: `cli/main.py`

**Verification:** `python3 -m cli.main deck-scan` und `python3 -m cli.main ranks` funktionieren bei installiertem/laufendem Helper ohne sudo (mit echtem MTGA-Prozess: tatsächliche Decks/Ränge in der Ausgabe, nicht nur "kein Fehler").

---

## T4: Totes RPC-Protokoll entfernen + echter Integrationstest

**Objective:** Nach T2/T3 sind `request_scan()`, `request_deck_scan()`, `request_rank_scan()`, `helper_scan_collection()`, `helper_scan_collection_detailed()` (alte Version) obsolet — sie sprechen ein Protokoll, das der Server nie wieder implementieren wird. Sie jetzt stehen zu lassen lädt zum nächsten "sieht fertig aus, ist aber tot"-Bug ein (genau das Problem dieser Review-Runde). Zusätzlich: **kein bestehender Test hätte den `Unknown action: scan`-Bug gefangen**, weil alle Tests (`scanner/tests/test_helper_client.py`, `scanner/tests/test_memory_scanner_helper.py`) am Python-Funktionsrand mocken, nie den echten Socket-Roundtrip gegen das kompilierte Binary. Das muss sich ändern.

**Changes:**
- Entfernen: `request_scan`, `request_deck_scan`, `request_rank_scan` und alle noch darauf verweisenden Tests in `scanner/tests/test_helper_client.py`.
- Entfernen in `helper/main.c`: totes Gerüst aus einem verworfenen Ansatz — `find_process()` (Mehrfachnamen-Variante, nie aufgerufen, `helper/main.c:277`), die unbenutzten Konstanten `CARD_ID_MIN`/`CARD_ID_MAX`/`CARD_QTY_MIN`/`CARD_QTY_MAX`/`BLOCK_MIN_CARDS`/`BLOCK_MAX_MISSES`/`MAX_ANCHORS`/`MAX_PROCESS_NAMES` (`helper/main.c:51-63`, waren ein abgebrochener Versuch, `find_blocks()` aus `memory_scanner.py:46` in C nachzubauen — diese Logik bleibt laut T1/T2 bewusst in Python), sowie `sb_free` falls weiterhin unbenutzt. `make -C helper build` muss danach ohne `-Wunused-function`-Warnungen durchlaufen.
- Neu: `scanner/tests/test_helper_integration.py` — ein Test, der den echten `helper/mtga-helper` (via `make -C helper test`, siehe `helper/Makefile:test`-Target) im `--test-mode` als Subprozess startet, `HelperBackend`/`RemoteMemoryAdapter` **echt** dagegen laufen lässt (kein Mock) und prüft, dass `read_bytes`/`iterate_writable_private_regions`/`read_ptr` etc. funktionieren. Mit `pytest.mark.skipif` überspringen, wenn `helper/mtga-helper` nicht existiert (z. B. in CI ohne macOS-Runner) — aber lokal auf macOS muss er laufen.

**Files:**
- Modify: `scanner/helper_client.py`, `scanner/tests/test_helper_client.py`
- Modify: `helper/main.c`
- Create: `scanner/tests/test_helper_integration.py`

**Verification:** `python3 -m pytest scanner/tests/test_helper_integration.py -v` → grün, startet nachweislich einen echten Subprozess (im Test-Log sichtbar). `make -C helper build 2>&1 | grep -i warning` → leer.

---

## T5: Security-Nacharbeit + Repo-Hygiene

**Objective:** Zwei kleinere, aber offene Punkte aus der Runde-2-Review.

**Changes:**
- `helper/main.c::check_peer_credentials()` (Zeile ~460): der Fallback "erlaube gid 20 (staff) oder 80 (admin), wenn Konsolen-UID nicht ermittelbar" erlaubt de facto praktisch jeden lokalen Standard-macOS-Useraccount (gid 20 ist die Standard-Primärgruppe). Entweder den Fallback ganz entfernen (Verbindung ablehnen, wenn `/dev/console` nicht einem echten User gehört) oder — falls Headless-Betrieb ein echtes Anwendungsszenario ist — explizit den erwarteten User über eine Config/ENV-Variable beim Helper-Start festlegen, statt auf Gruppenmitgliedschaft zu vertrauen.
- `helper/mtga-helper` (kompiliertes Mach-O-Binary, aktuell in Git getrackt, `git ls-files helper/` bestätigt das) aus dem Repo entfernen: `git rm --cached helper/mtga-helper`, und `.gitignore` um `helper/mtga-helper` ergänzen (die bestehenden Einträge `build/`, `helper/build/`, `*.bundle/` decken diesen Pfad nicht ab, weil `make -C helper test` das Binary direkt nach `helper/mtga-helper` kopiert, nicht nach `helper/build/`).

**Files:**
- Modify: `helper/main.c`
- Modify: `.gitignore`
- Remove from Git: `helper/mtga-helper`

**Verification:** `git ls-files helper/ | grep -v tests` zeigt keine Binärdatei mehr. Peer-Credential-Test: Verbindung von einem User außerhalb der Konsolen-Session (bzw. dokumentiertem erlaubten User) wird abgelehnt.

---

## Dependencies

```
T1 (MemoryBackend-Abstraktion)      — keine, zuerst
T2 (HelperBackend + Scan-Fix)       — T1 (braucht die Abstraktion)
T3 (RemoteMemoryAdapter)            — keine (il2cpp_nav.py ist bereits adapter-basiert), parallel zu T1/T2 möglich
T4 (Cleanup + Integrationstest)     — T2, T3 (testet das fertige Zusammenspiel, entfernt das, was T2/T3 ersetzen)
T5 (Security + Hygiene)             — keine, jederzeit parallel möglich
```

## Erfolgskriterien

- [ ] `python3 -m cli.main scan` liefert bei installiertem Helper + laufendem MTGA **echte Kartendaten ohne sudo** (nicht nur "kein Fehler")
- [ ] `python3 -m cli.main deck-scan` und `ranks` funktionieren ebenso ohne sudo über den Helper
- [ ] Kein Client-Code sendet mehr `action: scan|deck_scan|rank_scan` an den Helper (Protokoll-Konsistenz)
- [ ] Ein echter Integrationstest startet den kompilierten Helper als Subprozess und schlägt fehl, wenn Client/Server-Protokoll auseinanderlaufen
- [ ] `make -C helper build` ohne Compiler-Warnungen
- [ ] `check_peer_credentials()`-Fallback erlaubt nicht mehr pauschal jeden Standard-User
- [ ] `helper/mtga-helper` nicht mehr in Git getrackt

## Referenz

Vollständige Review-Zusammenfassung von Runde 2 (Commits `d383f3b..a51b92b`) inkl. Live-Reproduktion des `Unknown action: scan`-Fehlers: siehe Konversation vom 2026-08-04.
