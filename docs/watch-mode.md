# Watch-Mode — regelmäßiger Scan wenn MTGA aktiv

Der Watch-Mode (`mtga-advisor watch`) läuft als Hintergrundprozess und prüft
periodisch, ob MTGA läuft. Wenn MTGA erkannt wird, führt er automatisch den
kombinierten Scan (T3: Collection + Decks + Ranks) aus. Neue Decks und Karten
werden so automatisch erfasst, ohne manuellen Aufruf.

## Verwendung

```bash
# Standard: alle 5 Minuten prüfen
python -m cli.main watch

# Eigenes Intervall (z.B. 60 Sekunden)
python -m cli.main watch --interval 60

# Mit Status-Datei (für Monitoring)
python -m cli.main watch --state-file /tmp/mtga-watch-state.json

# Single-Shot (für Tests/CI): ein Scan, dann beenden
python -m cli.main watch --single-shot

# Debug-Output
python -m cli.main watch --debug
```

## Wie es funktioniert

1. **MTGA-Erkennung**: Der Watch-Loop prüft alle `interval` Sekunden, ob ein
   MTGA-Prozess läuft (via `pgrep -x MTGA`, fallback `ps aux | grep`).

2. **Kombinierter Scan**: Wenn MTGA erkannt wird, wird `scan_all()` aus
   `scanner/combined_scanner.py` aufgerufen — derselbe Scan wie bei
   `scan --all`. Das Ergebnis wird in `--output` geschrieben
   (collection.json, decks.json, ranks.json).

3. **De-Dup**: Ein `threading.Lock` verhindert überlappende Scans, falls ein
   Scan länger dauert als das Intervall.

4. **Sauberes Beenden**: SIGINT/SIGTERM beenden den Loop sauber. Der
   Signal-Handler wird bei Beendigung wiederhergestellt.

## Status-Datei

Mit `--state-file` kann ein JSON-Status geschrieben werden, der für externes
Monitoring nützlich ist:

```json
{
  "iterations": 42,
  "scansRun": 7,
  "scansSkipped": 0,
  "mtgaDetected": 7,
  "lastScanTime": 1785957302.49,
  "lastScanResult": "ok",
  "running": false
}
```

## LaunchAgent (User-Agent) einrichten

Die Vorlage liegt in `launchd/com.mtga.advisor.watch.plist`.

### Installation

```bash
# 1. Plist anpassen: WorkingDirectory und python3-Pfad prüfen
#    (Standard: /Users/agent/workspace/Mtga.advisor, /usr/bin/python3)

# 2. Plist kopieren
cp launchd/com.mtga.advisor.watch.plist \
    ~/Library/LaunchAgents/com.mtga.advisor.watch.plist

# 3. Agent laden
launchctl load ~/Library/LaunchAgents/com.mtga.advisor.watch.plist

# 4. Status prüfen
launchctl list | grep mtga.advisor
```

### Deinstallation

```bash
launchctl unload ~/Library/LaunchAgents/com.mtga.advisor.watch.plist
rm ~/Library/LaunchAgents/com.mtga.advisor.watch.plist
```

### Logs

```bash
tail -f /tmp/mtga-advisor-watch.log
```

## Architektur

```
scanner/watch.py
├── is_mtga_running()      # Prozess-Erkennung (pgrep/ps)
├── WatchConfig            # Konfiguration (interval, output, single_shot, ...)
├── WatchState             # Laufzeit-Status (iterations, scans_run, ...)
├── watch_loop()           # Haupt-Loop mit Lock + Signal-Handling
└── _run_combined_scan()   # Ruft combined_scanner.scan_all() auf
```

CLI-Integration in `cli/main.py`:
- `watch` Subparser mit `--interval`, `--output`, `--debug`,
  `--state-file`, `--single-shot`
- Dispatcher: `_run_watch()` → `watch_loop()`

## Tests

```bash
python -m pytest scanner/tests/test_watch.py -v
```

15 Tests, alle grün. Abgedeckt:
- `is_mtga_running` (pgrep match/no-match, Fallback, Multi-Name)
- `watch_loop` (single-shot, multi-iteration, dedup, error, interval-guard,
  state-file, stop-event)
- `WatchConfig` / `WatchState` (defaults, custom, serialization)