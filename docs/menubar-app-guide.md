# Mtga.advisor — macOS Menubar App

**Sync deine MTGA-Collection mit einem Klick.**

## Installation

```bash
# 1. Ins Repo wechseln
cd /Users/frankhermann/Documents/mtga-advisor

# 2. Abhängigkeiten installieren
.venv/bin/python -m pip install rumps pillow

# 3. Starten
.venv/bin/python menubar/app.py
```

## Bedienung

Nach dem Start siehst du ein **"M"-Icon** in der Menubar (oben rechts).

| Klick | Aktion |
|-------|--------|
| **Sync Now** | Startet Sync in separatem Thread |
| **Status** | Zeigt aktuellen Status |
| **Open Output Folder** | Öffnet `out/` im Finder |
| **Quit** | Beendet die App |

## Sync — Was passiert?

1. **Memory-Scan** — nutzt den produktiven `pymem-osx` Scannerpfad
2. **Log-Parsing** — Liest Deltas aus `Player.log`
3. **Merge** — Memory als Baseline + Log-Deltas
4. **Output** — `out/collection.json`

Nach erfolgreichem Sync erscheint eine macOS-Notification:
> "42000 cards synced to collection.json"

## Fehlerfälle

| Problem | Reaktion |
|---------|----------|
| MTGA läuft nicht | Memory-Scan übersprungen, nur Logs |
| Keine Logs gefunden | Nur Memory-Scan |
| Memory-Scan ohne Rechte | Fehlermeldung; final ist ein privileged helper geplant |
| Sync läuft bereits | Notification "Already syncing" |

## Autostart (optional)

Damit die App automatisch startet:

```bash
# LaunchAgent erstellen
mkdir -p ~/Library/LaunchAgents
cat > ~/Library/LaunchAgents/com.mtga.sync.plist << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.mtga.sync</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/frankhermann/Documents/mtga-advisor/.venv/bin/python</string>
        <string>/Users/frankhermann/Documents/mtga-advisor/menubar/app.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
EOF

# Aktivieren
launchctl load ~/Library/LaunchAgents/com.mtga.sync.plist
```

## Voraussetzungen

- macOS 12+
- Python 3.8+
- MTGA (für Memory-Scan)
- Für Vollscan aktuell `task_for_pid()`-Rechte; final ist ein privileged helper vorgesehen.
