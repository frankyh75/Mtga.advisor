# Mtga.advisor — macOS Menubar App

**Datum:** 2026-07-29
**Technologie:** Python + [rumps](https://github.com/jaredks/rumps) (Ridiculously Uncomplicated macOS Python Statusbar apps)

## Architektur

```
Menubar (rumps)
    │
    ├── [Sync Now] → lldb_probe.py + parser/pipeline.py + merge
    ├── [Status]   → Letzter Sync, Collection-Größe
    ├── [Open Output] → Finder: collection.json
    └── [Quit]
```

## Abhängigkeiten

```bash
pip install rumps
```

Kein Xcode, kein Swift, kein Electron. `rumps` ist pure Python mit PyObjC-Bridge — erzeugt native macOS Menubar-Apps.

## Features

- **Icon in der Menubar** (MTGA-Logo oder Custom-Icon)
- **Klick → Sync** — LLDB-Probe + Log-Parsing + Merge
- **Status-Anzeige** — "Syncing...", "Last sync: 2 min ago (42,000 cards)", "Error: MTGA not running"
- **Submenu** — Letzte Sync-Details, Output-Ordner öffnen
- **Autostart** via LaunchAgent (optional)

## Code-Skizze

```python
import rumps
import threading
from pathlib import Path

class MtgaSyncApp(rumps.App):
    def __init__(self):
        super().__init__("MTGA", icon="icon.png")
        self.menu = [
            rumps.MenuItem("Sync Now", callback=self.sync),
            None,
            rumps.MenuItem("Status: Ready"),
            None,
            rumps.MenuItem("Open Output Folder", callback=self.open_output),
            rumps.MenuItem("Quit", callback=self.quit),
        ]
        self.sync_thread = None

    @rumps.clicked("Sync Now")
    def sync(self, sender):
        if self.sync_thread and self.sync_thread.is_alive():
            return  # schon am syncen
        self.menu["Status: Ready"].title = "Status: Syncing..."
        self.sync_thread = threading.Thread(target=self._do_sync)
        self.sync_thread.start()

    def _do_sync(self):
        try:
            # 1. LLDB-Probe
            # 2. Log-Parsing
            # 3. Merge
            # 4. collection.json schreiben
            rumps.notification("MTGA Sync", "Done", f"{count} cards synced")
        except Exception as e:
            rumps.notification("MTGA Sync", "Error", str(e))
        finally:
            self.menu["Status: Ready"].title = f"Status: Last sync {time_ago}"

if __name__ == "__main__":
    MtgaSyncApp().run()
```

## Build & Distribution

```bash
# Direkt ausführen (für Entwicklung)
python menubar/app.py

# Als .app bündeln (py2app)
pip install py2app
python setup.py py2app
# → dist/Mtga Sync.app
```

## Nächste Schritte

1. `menubar/app.py` schreiben
2. Icon besorgen (MTGA-Logo oder Platzhalter)
3. Sync-Logik integrieren (LLDB + Logs + Merge)
4. Testen
5. Optional: LaunchAgent für Autostart
