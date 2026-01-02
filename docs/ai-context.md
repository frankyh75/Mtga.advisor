# docs/ai-context.md

## Projektziel
- Lokales Tool für MTG-Arena-Spieler:innen, das eine sammlungsbewusste Deck-Beratung ermöglicht.
- Kernpfad: deterministischer, regelbasierter Advisor auf Basis eines zuverlässigen Exports der eigenen Sammlung aus MTGA-Logs.
- Zielgruppe: privacy-sensitive Nutzer:innen auf macOS/Windows; nachvollziehbare, erklärbare Empfehlungen.
- Nicht-Ziele (Phase 1): Live-Tracker/Overlay, Cloud-Service, verpflichtende externe Meta-Feeds oder LLM-getriebene Entscheidungen.

## Phase-1 Scope (konservativ)
- **In Scope**: Einmaliger, lokaler Export der Sammlung aus MTGA-Logs zu `collection.json`.
- **Out of Scope**: Meta-Importe, Deck-Empfehlungen, Live-Telemetrie, GUI-Overlay, externe APIs.
- **Offline-first**: Keine Netzwerkaufrufe im Kernpfad.
- **Deterministisch & erklärbar**: Ausgabe muss auf Logs zurückführbar sein; keine stillen Annahmen.

## Kanonische Log-Quellen
- **Primärquelle**: `Player.log`.
- **Rotation/Caching**: `Player-prev.log` wird einbezogen (Snapshot-Cache + Reproduzierbarkeit).
- **Fallback**: `output_log.txt` nur „best effort“, klar als unsicher markiert; keine „complete“-Claims daraus.

## Snapshot- und Delta-Semantik
- **Snapshot-first**: „complete“ setzt einen Snapshot-Event voraus (z. B. GetPlayerCardsV3 oder äquivalent).
- **Delta-Events** dürfen nur angewendet werden, wenn ein Snapshot als Basis existiert.
- **Ohne Snapshot**: `collection.completeness` darf nicht `complete` sein; Ausgabe muss Warnung/What-if signalisieren.

## Vollständigkeits-Semantik (3-stufig)
- **Werte**: `complete`, `partial`, `unknown`.
- **Geltungsbereiche**: getrennt für `cards`, `wildcards`, `source`.
- **Regel**: `unknown` wird niemals als 0 interpretiert.

## Wildcards-Entscheidung (sofort nutzbar, aber guarded)
- Wildcards werden **sofort** extrahiert und dürfen im Ranking/Advice verwendet werden, **nur wenn** `wildcards.completeness == "complete"`.
- Wenn Wildcards nicht sicher bestimmt sind: Empfehlungen müssen als **What-if** formuliert werden (keine harten Zusagen).

## Normalisierung und Schlüssel
- **ArenaCardId ist Primärschlüssel** der Sammlung in Phase 1.
- Keine automatische Normalisierung auf externe IDs oder Set/CollectorNumber.
- Reprints/Varianten/Styles werden nicht zusammengelegt, solange kein expliziter Equivalence-Layer existiert (Phase 2+).

## collection.json (Phase-1 Kernartefakt)
- Pflichtfelder: `schema`, `asOf`, `source`, `completeness`, `cards`.
- Wildcards sind optional, aber strikt getrennt und mit eigener Vollständigkeit versehen.

```json
{
  "schema": "collection.v1",
  "asOf": "ISO-8601",
  "source": {
    "log": "Player.log",
    "usedPrevLog": true,
    "detailedLogs": "true|false|unknown",
    "confidence": "high|medium|low"
  },
  "completeness": "complete|partial|unknown",
  "cards": { "12345": 2, "67890": 1 },
  "wildcards": {
    "common": 12,
    "uncommon": 9,
    "rare": 4,
    "mythic": 1,
    "asOf": "ISO-8601",
    "completeness": "complete|partial|unknown",
    "evidence": ["<event-type>", "<event-type>"]
  }
}
```

## Fehler- und Warnverhalten
- **Fehlende Logs**: Abbruch mit klarer Meldung (keine Ausgabe).
- **Teilweise Logs**: Ausgabe möglich, aber `completeness` < `complete` und Warnung.
- **Format-Drift**: klare Fehlermeldung, kein stilles Trunkieren.
- **Stale Data**: Warnung, falls Snapshot zu alt.

## Erkenntnisse aus externer Repo-Analyse
- **Parser-Pipeline**: Event-Dispatch nach robustem Log-Splitting (UnityCrossThreadLogger/Client GRE), dann typed Parser pro Event.
- **Defensive JSON-Extraktion**: Mehrzeilige Payloads, partielle JSON-Blöcke, mehrere Events pro Chunk.
- **Rotation-Handling**: Snapshot-Cache + Delta-Apply (nur mit Snapshot-Basis).
- **Nicht-Ziele bestätigt**: Keine Online-Accounts, keine Overlay-/GUI-Pflicht, keine Pflicht-APIs.
- **Risiken**: Log-Drift (Eventnamen/Strukturen), Plattformpfade variieren; daher Parser-Versionierung, Unknown-Event-Telemetrie, konfigurierbare Pfade.

## Leitplanken für spätere Phasen
- Kernpfad bleibt offline und deterministisch.
- LLM-Einsatz ist optional und darf den Kernpfad nicht beeinflussen.
- Meta-Signale dürfen nur als zusätzliche Eingabe erfolgen und sind klar vom Kernpfad getrennt.
