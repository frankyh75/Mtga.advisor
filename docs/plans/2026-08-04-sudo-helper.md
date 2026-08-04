# Sudo-Helper: SMJobBless-basierter Memory-Scanner

> **Branch:** `feat/sudo-helper`
> **Assignee:** worker-heavy (GLM 5.2 via Ollama Cloud)
> **Problem:** `pymem-osx` braucht `task_for_pid()` → root. Aktuell nur via `sudo python3 -m cli.main run` lösbar.

**Goal:** Ein SMJobBless-Helper-Tool, das einmalig mit root installiert wird und per XPC/UNIX-Socket mit der Mac App kommuniziert. Danach kein sudo mehr für den User.

**Architecture:**
```
┌─────────────────────┐      UNIX Socket       ┌──────────────────────┐
│  Mac App / CLI      │ ◄──────────────────────►│  Helper-Tool (root)  │
│  (User, kein sudo)  │   request/response      │  task_for_pid()     │
└─────────────────────┘                         │  pymem-osx          │
                                                 └──────────────────────┘
```

---

## T1: Helper-Tool Binary (C)

**Objective:** Kleines C-Programm, das:
- Auf einem UNIX Socket (`/tmp/mtga-helper.sock`) horcht
- JSON-Kommandos empfängt: `{"action":"scan","pid":<mtga_pid>}`
- `task_for_pid()` aufruft und Memory-Scan macht
- Ergebnis als JSON zurückgibt

**Files:**
- Create: `helper/main.c` (Socket-Server + task_for_pid + Memory-Read)
- Create: `helper/Makefile` (kompiliert zu `mtga-helper`)
- Create: `helper/launchd.plist` (SMJobBless-kompatibel)

**Verification:** `make -C helper` → Binary existiert. Manuell starten, `echo '{"action":"ping"}' | nc -U /tmp/mtga-helper.sock` → `{"status":"ok"}`

---

## T2: SMJobBless-Integration

**Objective:** SMJobBless-Setup:
- `Info.plist` mit `SMPrivilegedExecutables`
- Code-Signing-Entitlements
- Einmalige Installation via `SMJobBless()` oder `launchctl load`
- Helper läuft als `LaunchDaemon` unter root

**Files:**
- Create: `helper/Info.plist` (SMJobBless-konform)
- Create: `helper/install.sh` (Installations-Script)
- Modify: `helper/Makefile` (Code-Signing + Bundle-Erstellung)

**Verification:** `sudo ./helper/install.sh` → Helper läuft als Daemon. `launchctl list | grep mtga-helper` → sichtbar.

---

## T3: Python-Client-Bibliothek

**Objective:** Python-Modul, das mit dem Helper-Tool über UNIX Socket kommuniziert. Ersetzt `pymem-osx`-Direktaufrufe.

**Files:**
- Create: `scanner/helper_client.py` (Socket-Client, Request/Response-Protokoll)
- Modify: `scanner/memory_scanner.py` (nutzt helper_client statt direktem pymem-osx)
- Create: `scanner/tests/test_helper_client.py`

**Verification:** Helper läuft → `python3 -c "from scanner.helper_client import scan; print(scan())"` → Collection-Daten ohne sudo.

---

## T4: CLI-Integration

**Objective:** `python3 -m cli.main run` funktioniert ohne sudo. Erkennt automatisch ob Helper läuft, sonst Fallback auf alten sudo-Weg mit Warnung.

**Files:**
- Modify: `cli/main.py` (run-Befehl: helper_client statt sudo)
- Modify: `scanner/__init__.py` (Export helper_client)

**Verification:** `python3 -m cli.main run` → Collection + Decks ohne sudo-Prompt.

---

## T5: Dashboard-Integration + Status-Anzeige

**Objective:** Dashboard zeigt Helper-Status an (grün/rot). Button "Helper installieren" für Erstsetup.

**Files:**
- Modify: `server/app.py` (neuer Endpoint `GET /api/helper/status`)
- Modify: `server/dashboard.js` (Status-Anzeige + Install-Button)
- Create: `server/tests/test_helper_status.py`

**Verification:** Dashboard → Helper-Status sichtbar. Ohne Helper: roter Hinweis + Install-Button.

---

## T6: Dokumentation + Tests

**Objective:** README-Update, Installations-Guide, End-to-End-Test.

**Files:**
- Modify: `README.md` (neue Sektion: Helper-Installation)
- Create: `docs/helper-installation.md` (Schritt-für-Schritt)
- Create: `helper/tests/test_e2e.sh` (Helper starten → scan → Ergebnis prüfen)

**Verification:** `bash helper/tests/test_e2e.sh` → exit 0.

---

## Dependencies

```
T1 (Helper Binary) — keine
T2 (SMJobBless) — T1 (braucht das Binary)
T3 (Python Client) — T1 (braucht laufenden Helper)
T4 (CLI Integration) — T3 (braucht den Client)
T5 (Dashboard) — T3 (braucht Status vom Client)
T6 (Doku + Tests) — T1..T5 (alles muss fertig sein)
```

## Erfolgskriterien

- [ ] `make -C helper` → `mtga-helper` Binary
- [ ] Helper läuft als LaunchDaemon unter root
- [ ] `python3 -c "from scanner.helper_client import scan; print(scan())"` → Daten ohne sudo
- [ ] `python3 -m cli.main run` → kein sudo nötig
- [ ] Dashboard zeigt Helper-Status
- [ ] `bash helper/tests/test_e2e.sh` → exit 0
- [ ] README aktualisiert
