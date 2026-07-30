# Mtga.advisor — Sync-Helper (Feature-Idee)

**Datum:** 2026-07-29
**Status:** draft
**Autor:** jarvis

## Problem

- Memory-Scanning (LLDB/pymem) erfordert, dass MTGA läuft und der User **nicht im Match** ist
- Log-Parsing allein liefert keine Vollcollection (nur Deltas)
- Automatischer Cron-Job scheitert, wenn MTGA nicht läuft oder der User mitten im Spiel ist

## Lösung: Manueller Sync-Helper

Der User drückt **aktiv** einen Button / führt ein Kommando aus, **wenn** er gerade:
- in der Deck-Ansicht ist
- zwischen zwei Matches
- am Sideboarden

Der Helper macht dann in einem Durchgang:

```
[User drückt Sync]
    │
    ├── 1. LLDB-Probe → Memory-Scan (Vollcollection)
    │      └── Nur wenn MTGA läuft & attachbar
    │
    ├── 2. Log-Parsing → Deltas aus Inventory.Updated
    │      └── Player.log + Player-prev.log
    │
    ├── 3. Merge → Neue collection.json
    │      └── Memory-Scan als Baseline + Log-Deltas
    │
    └── 4. Output → collection.json + run-report.json
```

## Trigger-Möglichkeiten

| Variante | Vorteil | Nachteil |
|----------|---------|----------|
| **CLI** `mtga-export sync` | Einfach, terminalbasiert | User muss Terminal öffnen |
| **macOS Menubar-App** | Immer sichtbar, ein Klick | Aufwändiger zu bauen |
| **Tastaturkürzel** (via Hammerspoon/Alfred) | Schnell, kein Mausklick | Erfordert Drittanbieter-Tool |
| **Raycast-Erweiterung** | Schön, native macOS | Raycast nötig |
| **Shortcuts.app** | Eingebaut, kein Extra-Tool | Eingeschränkte Automatisierung |

## CLI-Variante (MVP)

```bash
# Ein Befehl, alles
mtga-export sync

# Oder mit sudo für pymem-osx
sudo mtga-export sync
```

Der Befehl:
1. Prüft ob MTGA läuft (`pgrep MTGA`)
2. Versucht LLDB-Probe (bevorzugt, kein sudo)
3. Fallback: pymem-osx (sudo)
4. Parst aktuelle Logs
5. Merged Ergebnisse
6. Schreibt `collection.json`

## Architektur

```
cli/main.py sync
    │
    ├── scanner/lldb_probe.py    → Memory-Scan (Vollcollection)
    ├── parser/pipeline.py       → Log-Parsing (Deltas, Wildcards)
    ├── merge.py (NEU)           → Memory + Logs zusammenführen
    └── export.py                → collection.json schreiben
```

## Merge-Logik (`merge.py`)

```
Memory-Scan (Vollcollection)     Log-Parsing (Deltas)
        │                              │
        │  {grpId: qty}                │  {grpId: delta}
        │                              │
        └──────────┬───────────────────┘
                   │
            [Merge]
            Memory als Baseline
            Log-Deltas draufaddieren
            Wildcards aus Logs (falls vorhanden)
                   │
            collection.json
```

## Nächste Schritte

1. `merge.py` schreiben (Memory + Logs)
2. `cli/main.py` um `sync`-Befehl erweitern
3. macOS Menubar-App als Nice-to-have
