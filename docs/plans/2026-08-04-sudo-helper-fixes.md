# Sudo-Helper: Bugfix-Plan (Security + Funktionsfähigkeit)

> **Branch:** `feat/sudo-helper` (weiterarbeiten auf dem bestehenden Branch)
> **Assignee:** worker-heavy (GLM 5.2 via Ollama Cloud)
> **Basis:** Code-Review von `feat/sudo-helper` (Commits `f6cc189`..`e3ba9dc`) vom 2026-08-04.
> **Problem:** Der Sudo-Helper (T1–T6 im ursprünglichen Plan `docs/plans/2026-08-04-sudo-helper.md`) wurde gebaut, aber die Teile spielen nicht zusammen — die Kernfunktion ("sudo-freier Scan") läuft aktuell **nicht End-to-End**, und das `task_for_pid()`-Handling hat eine Privilege-Escalation-Lücke.

**Ziel dieses Plans:** Bestehenden Code reparieren, nicht neu schreiben. Reihenfolge ist absichtlich: erst Security-Fix (blockiert alles andere inhaltlich), dann Protokoll/Architektur richtigstellen, dann Build-Kette und Tests lauffähig machen.

---

## T1: Security-Fix — `task_for_pid()` nicht mehr client-gesteuert

**Objective:** Der Helper darf `task_for_pid()` nur noch auf den tatsächlichen MTGA-Prozess anwenden, niemals auf eine vom Client übergebene PID. Aktuell (`helper/main.c:245-250`) wird `pid` 1:1 aus dem JSON-Request übernommen und ungeprüft an `task_for_pid()` durchgereicht (`helper/main.c:106-114`) — jeder lokale Prozess kann den root-Daemon damit anweisen, einen Mach-Task-Port für **beliebige** PIDs auf dem System zu holen.

**Changes:**
- `helper/main.c`: PID nicht mehr aus dem Request lesen. Stattdessen serverseitig per `proc_listpids`/`proc_pidpath` (oder vergleichbar) nach einem Prozess mit bekanntem MTGA-Namen suchen (siehe `scanner/macos_paths.py::get_macos_mtga_process_names()` für die erwarteten Namen) und dessen PID intern verwenden.
- `scan`-Request braucht dann kein `pid`-Feld mehr vom Client (kann optional als `process_names`-Hint bleiben, aber nie als PID).
- Client (`scanner/helper_client.py`) entsprechend anpassen — sendet keine PID mehr, nur noch Prozessnamen-Hints.

**Files:**
- Modify: `helper/main.c`
- Modify: `scanner/helper_client.py`

**Verification:** `echo '{"action":"scan","pid":1}' | nc -U /tmp/mtga-helper.sock` darf **nicht** mehr gegen PID 1 auflösen (Server ignoriert das Feld komplett). Manueller Test: Helper mit laufendem MTGA-Prozess → Scan trifft den echten Prozess ohne dass eine PID übergeben wurde.

---

## T2: Security-Fix — Socket-Setup härten

**Objective:** Zwei Probleme beheben:
1. Race Condition: `bind()` läuft vor `chmod(0600)` (`helper/main.c:196-203`) — kurzes Zeitfenster mit zu offenen Rechten.
2. `/tmp` ist world-writable und ein schlechter Ort für den Socket eines root-Daemons.

**Changes:**
- `umask(0077)` vor `bind()` setzen, damit der Socket von Anfang an mit restriktiven Rechten entsteht (kein nachträgliches `chmod` nötig).
- Socket-Pfad von `/tmp/mtga-helper.sock` nach `/var/run/mtga-helper.sock` (oder ein dediziertes, root-only Verzeichnis wie `/Library/PrivilegedHelperTools/mtga-helper.sock`) verschieben. Alle Referenzen synchron halten: `helper/main.c`, `helper/launchd.plist`, `helper/install.sh`, `scanner/helper_client.py::DEFAULT_SOCK_PATH`, `server/app.py::_HELPER_SOCK_PATH`, `helper/tests/test_e2e.sh`.
- Entscheiden und dokumentieren, welcher Prozess/User den Socket überhaupt ansprechen darf (nur root, oder auch der Desktop-User?) — aktuelle `chmod 0600` erlaubt nur root selbst, was die CLI (als normaler User) aussperrt. Falls der normale User zugreifen soll: Peer-Credential-Check (`getpeereid()`) statt weiter Dateirechte, nicht einfach `0666`.

**Files:**
- Modify: `helper/main.c`, `helper/launchd.plist`, `helper/install.sh`, `scanner/helper_client.py`, `server/app.py`, `helper/tests/test_e2e.sh`

**Verification:** Nach Installation ist der Socket nicht mehr unter `/tmp`. `ls -la` auf den neuen Pfad zeigt korrekte Rechte direkt nach Start (kein Zeitfenster mit offenen Rechten prüfbar durch schnelles Polling während des Starts).

---

## T3: Protokoll-Redesign — Low-Level Read-Primitives statt Business-Logik im Helper

**Objective:** Der C-Helper soll so wenig privilegierten Code wie möglich enthalten (kleinere Angriffsfläche, leichter zu auditieren). Aktuell versucht `scan_process()` (`helper/main.c:106-154`) Scan-Logik direkt in C nachzubauen, kommt aber nie über einen Region-Count hinaus (`"note":"Vollständiger Pattern-Scan folgt in T3"`) — die eigentliche Pattern-Matching-Logik existiert bereits vollständig in Python (`scanner/pattern_scanner.py`, `scanner/il2cpp_nav.py`) und soll dort bleiben.

**Changes:** Helper-Protokoll auf reine Primitives umstellen:
- `{"action":"ping"}` → unverändert.
- `{"action":"list_regions"}` → Liste der VM-Regionen des (serverseitig aufgelösten, siehe T1) Zielprozesses: `[{"address":.., "size":..}, ...]`.
- `{"action":"read_memory","address":..,"size":..}` → liest `size` Bytes ab `address` aus dem Zielprozess, Base64-kodiert in der Response. `size` serverseitig deckeln (z.B. max 16 MB pro Request), um DoS/Speicherexplosion zu vermeiden.
- `ping`/`shutdown` bleiben wie sind. `scan` als High-Level-Action **entfernen** — die Logik wandert nach Python (siehe T4).

**Files:**
- Modify: `helper/main.c` (ersetzt `scan_process()` durch `list_regions()`/`read_memory()`)

**Verification:** `echo '{"action":"list_regions"}' | nc -U <sock>` liefert eine JSON-Liste von Regionen für den laufenden MTGA-Prozess. `read_memory` mit einer Adresse aus dieser Liste liefert die erwartete Byte-Anzahl (Base64-dekodiert prüfen).

---

## T4: Python-Integration — RemoteMemoryAdapter statt neuer Server-Actions

**Objective:** `scanner/helper_client.py` sendet aktuell `status`, `deck_scan`, `rank_scan` — Actions, die der C-Server nie implementiert hat (`helper/main.c:240-256` kennt nur `ping`/`shutdown`/`scan`). Statt diese im C-Server nachzubauen: einen `RemoteMemoryAdapter` in Python schreiben, der dieselbe Schnittstelle wie `PymemMemoryAdapter` (siehe `scanner/il2cpp_nav.py`) erfüllt, aber `read_memory`/`list_regions` über den Helper-Socket (T3) statt über `pymem-osx` direkt anspricht. Bestehende Scan-Module (`scanner/pattern_scanner.py`, `scanner/il2cpp_nav.py`, `scanner/rank_scanner.py`) bleiben dadurch unverändert nutzbar — sie bekommen nur einen anderen Adapter injiziert, wenn kein sudo verfügbar ist.

**Changes:**
- Neu: `scanner/helper_client.py` → Klasse `RemoteMemoryAdapter`, die das Adapter-Interface von `PymemMemoryAdapter` implementiert (gleiche Methodensignaturen, siehe `scanner/il2cpp_nav.py`).
- `helper_scan_collection_detailed()`, `helper_scan_decks()`, `helper_scan_ranks()` bauen jetzt aus `RemoteMemoryAdapter` + den bestehenden Scan-Funktionen (statt eigener Response-Parsing-Logik gegen nicht existierende Server-Actions).
- `get_status()` kann lokal aus `ping()` + ggf. `launchctl list` abgeleitet werden — der Helper selbst muss keine `status`-Action anbieten.
- `cli/main.py::_run_deck_scan` und `_run_ranks` ebenfalls auf Auto-Detect (Helper vs. Fallback mit sudo-Warnung) umstellen, analog zu `_run_scan` (aktuell nutzen nur Collection-Scans den Helper, Deck-/Rank-Scan gehen weiterhin direkt über `pymem-osx` und brauchen daher immer noch sudo).

**Files:**
- Modify: `scanner/helper_client.py`
- Modify: `cli/main.py`
- Modify: `scanner/tests/test_helper_client.py` (Mock-Server muss `list_regions`/`read_memory` statt `scan` mocken)

**Verification:** `python3 -m cli.main scan` (ohne sudo, Helper installiert) liefert eine `collection.json` mit echten Karten (nicht nur Region-Count). `python3 -m cli.main deck-scan` und `python3 -m cli.main ranks` funktionieren ebenfalls ohne sudo, wenn der Helper läuft.

---

## T5: Build-Kette reparieren

**Objective:** `helper/install.sh` erwartet `helper/build/mtga-helper.bundle` (`helper/install.sh:32`) und prüft darauf mit `codesign`, aber `helper/Makefile` erzeugt nur `build/mtga-helper` — kein Bundle, kein Signing-Target. `sudo ./helper/install.sh` bricht deshalb immer bei `check_prerequisites` ab.

**Changes:**
- `helper/Makefile`: Targets `bundle` (baut `build/mtga-helper.bundle/Contents/{MacOS,Info.plist}` Struktur aus `helper/Info.plist` + dem kompilierten Binary) und `sign` (codesign mit Dev-Zertifikat, referenziert `helper/Entitlements.plist`) ergänzen.
- `main()` in `helper/main.c` muss die CLI-Argumente tatsächlich auswerten (`--sock <path>`, `--test-mode`) statt sie zu ignorieren (`helper/main.c:271`, `argc`/`argv` sind aktuell `__attribute__((unused))`) — das bricht sonst weiterhin `helper/tests/test_e2e.sh`, das genau diese Flags übergibt.
- `--test-mode`: liefert deterministische Mock-Daten statt echtem `task_for_pid`, damit der E2E-Test ohne laufendes MTGA durchläuft (so im Kommentar von `helper/tests/test_e2e.sh:12` bereits vorausgesetzt, aber nie implementiert).
- Zwei widersprüchliche LaunchDaemon-Plists bereinigen: `helper/com.mtga.helper.plist` (referenziert `/usr/local/libexec/mtga-helper`, wird von nichts verwendet) entweder entfernen oder klar als Alternative zu `helper/launchd.plist` dokumentieren — aktuell nutzt nur `install.sh` `launchd.plist`.

**Files:**
- Modify: `helper/Makefile`, `helper/main.c`
- Delete or document: `helper/com.mtga.helper.plist`

**Verification:** `make -C helper && make -C helper bundle && make -C helper sign` (mit lokalem Dev-Zertifikat) → `helper/build/mtga-helper.bundle` existiert und `codesign -dv` läuft ohne Fehler durch. `sudo ./helper/install.sh` kommt über `check_prerequisites` hinaus.

---

## T6: Tests + Repo-Hygiene

**Objective:** E2E-Test lauffähig machen und das bereits kompilierte Binary aus Git entfernen.

**Changes:**
- `helper/tests/test_e2e.sh` gegen das reparierte Binary laufen lassen (Abhängigkeit von T3/T5) und sicherstellen, dass alle 5 Checks tatsächlich grün sind — nicht nur zufällig durch `grep`-Substring-Treffer (z.B. Test 5 prüft `grep -q '"error"'`, was auch `"status":"error"` matcht — funktional ok, aber sollte präzise auf `"message"` oder einen expliziten Error-Key prüfen).
- `helper/build/mtga-helper` (kompiliertes Mach-O-Binary, aktuell eingecheckt) aus Git entfernen (`git rm --cached`) und `helper/build/` in `.gitignore` aufnehmen. Das Binary wird nicht gebraucht — `install.sh` erwartet ohnehin das `.bundle` aus T5, nicht diese Datei.
- `scanner/tests/test_helper_client.py`: `MockHelperServer` an das neue Protokoll (T3) anpassen (`list_regions`/`read_memory` statt `scan`).

**Files:**
- Modify: `helper/tests/test_e2e.sh`
- Modify: `.gitignore`
- Delete: `helper/build/mtga-helper` (aus Git-Historie des Branches, per `git rm --cached`)
- Modify: `scanner/tests/test_helper_client.py`

**Verification:** `bash helper/tests/test_e2e.sh` → exit 0, alle 5/5 Tests bestehen gegen das echte Binary (nicht gemockt). `git ls-files helper/build/` → leer.

---

## Dependencies

```
T1 (task_for_pid Fix)       — keine (blockiert inhaltlich alles andere, zuerst)
T2 (Socket härten)          — keine, parallel zu T1 möglich
T3 (Protokoll-Redesign)     — T1 (PID-Auflösung muss vorher stehen)
T4 (Python-Integration)     — T3 (braucht die neuen Primitives)
T5 (Build-Kette)            — T3 (main.c ändert sich durch T3, --test-mode gehört zu demselben main.c)
T6 (Tests + Hygiene)        — T4, T5 (testet das fertige Zusammenspiel)
```

## Erfolgskriterien

- [ ] Helper führt `task_for_pid()` nie auf eine client-übergebene PID aus
- [ ] Socket liegt nicht mehr in `/tmp`, keine Rechte-Race beim Start
- [ ] `python3 -m cli.main scan` liefert ohne sudo echte Kartendaten (nicht nur Region-Count)
- [ ] `python3 -m cli.main deck-scan` und `ranks` funktionieren ebenfalls ohne sudo bei installiertem Helper
- [ ] `sudo ./helper/install.sh` läuft vollständig durch (Bundle wird gefunden)
- [ ] `bash helper/tests/test_e2e.sh` → 5/5 Tests grün gegen das echte Binary
- [ ] Kein kompiliertes Binary mehr in Git (`helper/build/` in `.gitignore`)
- [ ] `docs/helper-installation.md` an den neuen Socket-Pfad und das neue Protokoll angepasst

## Referenz

Vollständige Fund-Liste mit Zeilenangaben: siehe Code-Review-Zusammenfassung in der Konversation vom 2026-08-04 (Branch `feat/sudo-helper`, Commits `f6cc189`..`e3ba9dc`).
