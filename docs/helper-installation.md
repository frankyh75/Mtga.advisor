# Helper-Installation — Sudo-freier Memory-Scanner

> **Zweck:** Einmalige Installation eines SMJobBless-Helper-Tools, das als LaunchDaemon unter root läuft. Danach funktioniert `mtga-export run` ohne `sudo`-Prompt.

## Voraussetzungen

- macOS 12+ (getestet auf macOS 26 / Tahoe)
- Xcode Command Line Tools: `xcode-select --install`
- MTGA ist installiert und läuft beim Scan
- Admin-Rechte für die einmalige Installation

## Architektur

```
┌─────────────────────┐      UNIX Socket       ┌──────────────────────┐
│  Mac App / CLI      │ ◄──────────────────────►│  Helper-Tool (root)  │
│  (User, kein sudo)  │   request/response      │  task_for_pid()     │
└─────────────────────┘                         │  pymem-osx          │
                                                 └──────────────────────┘
```

Der Helper lauscht auf einem UNIX Socket (`/var/run/mtga-helper.sock`), empfängt JSON-Kommandos und führt `task_for_pid()` + Memory-Read im root-Kontext aus. Die Mac App / CLI kommuniziert als normaler User darüber.

## Schritt 1: Helper bauen

```bash
cd /pfad/zu/Mtga.advisor
make -C helper
```

Das erzeugt `helper/mtga-helper` (das Binary) und `helper/mtga-helper.bundle` (das SMJobBless-kompatible Bundle).

**Voraussetzung:** Code-Signing-Zertifikat. Für Entwicklung kann ein self-signed Zertifikat genutzt werden:

```bash
# Self-signed Developer ID Certificate erstellen (einmalig)
# In Keychain Access: Certificate Assistant → Create a Certificate...
# Name: "MTGA Helper Dev", Type: Code Signing
```

## Schritt 2: Helper installieren (einmalig mit sudo)

```bash
sudo ./helper/install.sh
```

Das Skript:
1. Kopiert das Bundle nach `/Library/PrivilegedHelperTools/mtga-helper.bundle`
2. Installiert die LaunchDaemon plist nach `/Library/LaunchDaemons/com.mtga.helper.plist`
3. Lädt den Daemon: `launchctl load /Library/LaunchDaemons/com.mtga.helper.plist`
4. Verifiziert, dass der Daemon läuft

**Alternative: SMJobBless() (für App-Store-kompatiblen Weg)**

Wenn die Mac App den Helper installieren soll (statt `install.sh`):

```swift
// In der Menubar-App:
SMJobBless(kSMDomainSystemLaunchd, "com.mtga.helper" as CFString, authRef, nil)
```

Dafür müssen Info.plist und Helper-Info.plist korrekt konfiguriert sein (siehe `helper/Info.plist`).

## Schritt 3: Installation verifizieren

```bash
# Daemon sichtbar?
launchctl list | grep mtga-helper

# Socket erreichbar?
echo '{"action":"ping"}' | nc -U /var/run/mtga-helper.sock
# Erwartet: {"status":"ok"}
```

## Schritt 4: CLI ohne sudo nutzen

```bash
# Statt früher: sudo -E .venv/bin/python -m cli.main run --output out
# Jetzt:
.venv/bin/python -m cli.main run --output out
```

Die CLI erkennt automatisch, ob der Helper läuft:
- **Helper aktiv:** Scan läuft über Helper (kein sudo nötig)
- **Helper inaktiv:** Warnung + Fallback auf direkten sudo-Weg

## Schritt 5: Dashboard-Status prüfen

Im lokalen Dashboard (`http://127.0.0.1:8000/`) wird der Helper-Status angezeigt:
- **Grün:** Helper läuft, Scans funktionieren ohne sudo
- **Rot:** Helper nicht installiert oder nicht erreichbar — Install-Button wird angezeigt

API-Endpoint: `GET /api/helper/status` → `{"running": true, "socket": "/var/run/mtga-helper.sock"}`

## Helper deinstallieren

```bash
sudo launchctl unload /Library/LaunchDaemons/com.mtga.helper.plist
sudo rm -rf /Library/PrivilegedHelperTools/mtga-helper.bundle
sudo rm /Library/LaunchDaemons/com.mtga.helper.plist
rm -f /var/run/mtga-helper.sock
```

## Troubleshooting

### Helper startet nicht

```bash
# Logs prüfen
log show --predicate 'process == "mtga-helper"' --last 5m

# Manuell starten mit Debug-Output
sudo /Library/PrivilegedHelperTools/mtga-helper.bundle/Contents/MacOS/mtga-helper --debug
```

### Socket nicht erreichbar

```bash
# Socket existiert?
ls -la /var/run/mtga-helper.sock

# Berechtigungen prüfen
# Der Socket muss für den User lesbar/schreibbar sein
```

### Code-Signing schlägt fehl

SMJobBless erfordert, dass die App und der Helper mit kompatiblen Zertifikaten signiert sind. Die `Info.plist` des Helpers muss den `SMPrivilegedExecutables`-Eintrag enthalten, der den Designated Requirement der App referenziert.

```bash
# Designated Requirement der App auslesen
codesign -d -r- /pfad/zu/app

# Helper-Signatur prüfen
codesign -d -r- /Library/PrivilegedHelperTools/mtga-helper.bundle
```

### MTGA-Prozess nicht gefunden

Der Helper löst die PID des MTGA-Prozesses serverseitig auf (proc_listpids). Die CLI sendet nur Prozessnamen-Hints:

```bash
# Manuell PID finden
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

### Scan
```json
// Request
{"action":"scan","process_names":["MTGA"]}
// Response
{"cards":{"12345":4,"67890":2},"decks":[...],"stats":{"anchorCount":5,"scanTime":1.2}}
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

- Der Helper läuft als root — nur das Bundle in `/Library/PrivilegedHelperTools/` wird akzeptiert
- Der UNIX Socket hat Berechtigungen `0660` (rw-rw----). Peer-Credential-Check via `getpeereid()` verifiziert die UID des Clients — nur root und der Console-User (Desktop-User) dürfen zugreifen
- Der Helper validiert alle JSON-Kommandos und lehnt unbekannte Aktionen ab
- Keine Remote-Netzwerk-Schnittstelle — nur lokaler UNIX Socket
- Code-Signing verhindert Manipulation des Helper-Bundles