# Helper-Installation — Sudo-freier Memory-Scanner

> **Zweck:** Einmalige Installation eines LaunchDaemon, der als root läuft und
> über einen UNIX Socket Memory-Scans für MTGA durchführt. Danach funktioniert
> `mtga-export run` ohne `sudo`-Prompt.

## Voraussetzungen

- macOS 12+ (getestet auf macOS 26 / Tahoe)
- Xcode Command Line Tools: `xcode-select --install`
- MTGA ist installiert und läuft beim Scan
- Admin-Rechte für die einmalige Installation (root via sudo)

## Architektur

```
┌─────────────────────┐      UNIX Socket       ┌──────────────────────┐
│  Mac App / CLI      │ ◄──────────────────────►│  Helper-Daemon (root)│
│  (User, kein sudo)  │   request/response      │  task_for_pid()      │
└─────────────────────┘                         │  /var/run/mtga-helper.sock
                                                 └──────────────────────┘
```

Der Helper lauscht auf einem UNIX Socket (`/var/run/mtga-helper.sock`),
empfängt JSON-Kommandos und führt `task_for_pid()` + Memory-Read im
root-Kontext aus. Die Mac App / CLI kommuniziert als normaler User darüber.

## Installationsweg

Ab T1 (Commit 2a54ab7) wird der Helper über **launchctl load** installiert,
nicht mehr über das deprecated `SMJobBless`-Verfahren. Es wird nur das nackte
Binary (`helper/build/mtga-helper`) plus eine LaunchDaemon-plist benötigt —
kein signiertes `.app`-Bundle.

### Variante A: install.sh (empfohlen, automatisiert)

`helper/install.sh` übernimmt alle Schritte: Binary kopieren, plist
installieren, Daemon laden, Status verifizieren.

```bash
cd /pfad/zu/Mtga.advisor
make -C helper build          # Binary erzeugen (als normaler User)
sudo ./helper/install.sh      # Installation als root
```

`install.sh --status` prüft Daemon, Socket und Binary. `--uninstall` entfernt
alles.

### Variante B: install-commands.sh (manuell, Copy-Paste)

Wenn die Installation Schritt für Schritt nachvollzogen werden soll,
druckt `helper/install-commands.sh` alle Befehle als echo aus, die man dann
per Copy-Paste in ein root-Terminal einfügt. Das Skript selbst braucht kein
sudo und ändert nichts am System.

```bash
# Alle Befehle für Installation + Verifikation ausgeben
./helper/install-commands.sh

# Nur Installation
./helper/install-commands.sh --install

# Nur Verifikation
./helper/install-commands.sh --verify

# Nur Deinstallation
./helper/install-commands.sh --uninstall
```

### Variante C: SMJobBless (legacy, nicht empfohlen)

Für App-Store-kompatible Verteilung kann die Menubar-App den Helper über
`SMJobBless` installieren. Dafür wird ein signiertes Bundle benötigt
(`make -C helper` erzeugt `helper/build/mtga-helper.bundle`). Dieser Weg ist
deprecated seit macOS 13 und wird nur noch als Fallback gepflegt.

```swift
SMJobBless(kSMDomainSystemLaunchd, "com.mtga.helper" as CFString, authRef, nil)
```

## Installation Schritt für Schritt

### Schritt 1: Helper bauen

```bash
cd /pfad/zu/Mtga.advisor
make -C helper build
```

Erzeugt `helper/build/mtga-helper` (das nackte Binary).

### Schritt 2: Binary und Plist installieren (mit sudo)

```bash
sudo mkdir -p /Library/PrivilegedHelperTools
sudo cp helper/build/mtga-helper /Library/PrivilegedHelperTools/mtga-helper
sudo chmod 755 /Library/PrivilegedHelperTools/mtga-helper
sudo chown root:wheel /Library/PrivilegedHelperTools/mtga-helper

sudo cp helper/launchd.plist /Library/LaunchDaemons/com.mtga.helper.plist
sudo chmod 644 /Library/LaunchDaemons/com.mtga.helper.plist
sudo chown root:wheel /Library/LaunchDaemons/com.mtga.helper.plist
```

### Schritt 3: Daemon laden

```bash
# Primärweg (funktioniert auf macOS 15.6):
sudo launchctl load /Library/LaunchDaemons/com.mtga.helper.plist

# Fallback für macOS-Versionen, die 'load' nicht mehr unterstützen:
sudo launchctl bootstrap system /Library/LaunchDaemons/com.mtga.helper.plist
```

### Schritt 4: Warten und verifizieren

```bash
sleep 2
# Siehe Verify-Anleitung unten
```

## Verify-Anleitung

Die Verifikation prüft, dass Daemon, Socket und Binary korrekt installiert
sind und der Ping-Test erfolgreich durchgeht. Alle Befehle sind auch in
`helper/install-commands.sh --verify` enthalten.

### VERIFY A: Daemon in launchctl sichtbar?

```bash
sudo launchctl list com.mtga.helper
# Erwartet: Ausgabe mit PID, Label, LastExitStatus=0

# Alternative Anzeige:
sudo launchctl list | grep mtga
```

Wenn der Daemon nicht sichtbar ist:
- plist-Syntax prüfen: `sudo plutil -lint /Library/LaunchDaemons/com.mtga.helper.plist`
- Logs prüfen: `log show --predicate 'process == "mtga-helper"' --last 5m`
- Manuell neu laden: `sudo launchctl unload /Library/LaunchDaemons/com.mtga.helper.plist && sudo launchctl load /Library/LaunchDaemons/com.mtga.helper.plist`

### VERIFY B: Socket existiert?

```bash
ls -la /var/run/mtga-helper.sock
# Erwartet: srw-rw----  ...  root  wheel  /var/run/mtga-helper.sock
```

**OnDemand-Hinweis:** Die plist hat `KeepAlive=false` (socket-triggered
OnDemand). Der Socket wird evtl. erst bei der ersten Client-Verbindung
erstellt. Falls der Socket hier noch fehlt, mit VERIFY C den Start triggern.

### VERIFY C: Ping über Socket senden

```bash
echo '{"action":"ping"}' | nc -U -w 5 /var/run/mtga-helper.sock
# Erwartet: {"status":"ok","version":"1.0"}
```

Falls der Socket bei OnDemand noch nicht existiert, triggert das nc-Kommando
den Daemon-Start. Evtl. 2x ausführen, bis der Socket erscheint.

### VERIFY D: Binary und Plist am Zielort?

```bash
ls -la /Library/PrivilegedHelperTools/mtga-helper
ls -la /Library/LaunchDaemons/com.mtga.helper.plist
```

### VERIFY E: plist validieren (Syntax)

```bash
sudo plutil -lint /Library/LaunchDaemons/com.mtga.helper.plist
# Erwartet: OK
```

### VERIFY F: Logs prüfen (bei Fehlern)

```bash
log show --predicate 'process == "mtga-helper"' --last 5m
tail -50 /var/log/mtga-helper.log 2>/dev/null || echo '(kein log)'
tail -50 /var/log/mtga-helper.err.log 2>/dev/null || echo '(kein err-log)'
```

### Ergebnis-Bewertung

| Check | OK | Bedeutung |
|-------|----|-----------|
| A: Daemon geladen | ✅ | launchctl hat die plist akzeptiert |
| B: Socket da | ✅ | Daemon läuft und hat Socket erstellt |
| C: Ping ok | ✅ | Helper antwortet korrekt auf JSON |
| D: Binary + Plist | ✅ | Dateien korrekt installiert |
| E: plist valid | ✅ | Syntax ist korrekt |

Wenn A ✅ und C ✅: **Helper betriebsbereit.**
Wenn Socket fehlt aber Daemon läuft: OnDemand — bei erster Verbindung (Ping)
startet der Daemon und erstellt den Socket.

## CLI ohne sudo nutzen

```bash
# Statt früher: sudo -E .venv/bin/python -m cli.main run --output out
# Jetzt:
.venv/bin/python -m cli.main run --output out
```

Die CLI erkennt automatisch, ob der Helper läuft:
- **Helper aktiv:** Scan läuft über Helper (kein sudo nötig)
- **Helper inaktiv:** Warnung + Fallback auf direkten sudo-Weg

## Dashboard-Status

Im lokalen Dashboard (`http://127.0.0.1:8000/`) wird der Helper-Status
angezeigt:
- **Grün:** Helper läuft, Scans funktionieren ohne sudo
- **Rot:** Helper nicht installiert oder nicht erreichbar — Install-Button
  wird angezeigt

API-Endpoint: `GET /api/helper/status` →
`{"running": true, "socket": "/var/run/mtga-helper.sock"}`

## Helper deinstallieren

```bash
sudo launchctl unload /Library/LaunchDaemons/com.mtga.helper.plist 2>/dev/null || true
sudo launchctl bootout system/com.mtga.helper 2>/dev/null || true
sudo rm -f /Library/PrivilegedHelperTools/mtga-helper
sudo rm -f /Library/LaunchDaemons/com.mtga.helper.plist
sudo rm -f /var/run/mtga-helper.sock
sudo rm -f /var/log/mtga-helper.log /var/log/mtga-helper.err.log
```

Oder einfacher:

```bash
sudo ./helper/install.sh --uninstall
```

## Troubleshooting

### Helper startet nicht (Daemon nicht in launchctl list)

```bash
# plist-Syntax prüfen
sudo plutil -lint /Library/LaunchDaemons/com.mtga.helper.plist

# Logs prüfen
log show --predicate 'process == "mtga-helper"' --last 5m

# Manuell entladen und neu laden
sudo launchctl unload /Library/LaunchDaemons/com.mtga.helper.plist
sudo launchctl load /Library/LaunchDaemons/com.mtga.helper.plist

# Falls 'load' nicht funktioniert (neueres macOS):
sudo launchctl bootout system/com.mtga.helper 2>/dev/null || true
sudo launchctl bootstrap system /Library/LaunchDaemons/com.mtga.helper.plist
```

### Socket nicht erreichbar

```bash
# Socket existiert?
ls -la /var/run/mtga-helper.sock

# Bei OnDemand (KeepAlive=false): Socket wird evtl. erst bei erster
# Verbindung erstellt. Ping senden, um Start zu triggern:
echo '{"action":"ping"}' | nc -U -w 5 /var/run/mtga-helper.sock
# Evtl. 2x ausführen

# Berechtigungen prüfen
# Der Socket muss für den User lesbar/schreibbar sein (0660)
ls -la /var/run/mtga-helper.sock
```

### Manuell starten mit Debug-Output

```bash
sudo /Library/PrivilegedHelperTools/mtga-helper --sock /var/run/mtga-helper.sock --debug
```

### MTGA-Prozess nicht gefunden

Der Helper löst die PID des MTGA-Prozesses serverseitig auf
(`proc_listpids`). Die CLI sendet nur Prozessnamen-Hints:

```bash
pgrep -f "MTGA"
# oder
ps aux | grep -i "mtga\|magic" | grep -v grep
```

## JSON-Protokoll

### Ping
```json
// Request
{"action":"ping"}
// Response
{"status":"ok","version":"1.0"}
```

### list_regions
```json
// Request
{"action":"list_regions"}
// Response
{"status":"ok","regions":[{"address":4294967296,"size":1048576},...]}
```

### read_memory
```json
// Request
{"action":"read_memory","address":4294967296,"size":4096}
// Response
{"status":"ok","data":"<base64>","bytes_read":4096}
```

### Status
```json
// Request
{"action":"status"}
// Response
{"running":true,"pid":12345,"uptime":3600}
```

### Error
```json
{"error":"task_for_pid failed: permission denied"}
```

## Sicherheitshinweise

- Der Daemon läuft als root — nur das Binary in
  `/Library/PrivilegedHelperTools/` wird akzeptiert
- Der UNIX Socket hat Berechtigungen `0660` (rw-rw----). Peer-Credential-Check
  via `getpeereid()` verifiziert die UID des Clients — nur root und der
  Console-User (Desktop-User) dürfen zugreifen
- Der Helper validiert alle JSON-Kommandos und lehnt unbekannte Aktionen ab
- Keine Remote-Netzwerk-Schnittstelle — nur lokaler UNIX Socket
- Code-Signing verhindert Manipulation des Helper-Bundles (nur bei
  SMJobBless-Variante relevant)