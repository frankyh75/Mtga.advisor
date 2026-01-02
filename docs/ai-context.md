# Projektkontext (AI) – Normativer Kontext

## Projektziel
- Lokales, deterministisches Tool für sammlungsbewusste Deck-Beratung ohne Cloud/LLM im Kernpfad.
- Phase-1-Kernnutzen: verlässliche `collection.json`-Extraktion aus MTGA-Logs als Basis für spätere Empfehlungen.

## Phase-1 Scope (konservativ)
- **Nur** Log-basierte Sammlungsextraktion + strukturierter Export (`collection.json`).
- Kein Live-Overlay, kein Dauer-Daemon, keine externen APIs im Kernpfad.
- Zielplattformen: macOS/Windows (Linux optional).
- Unklare Punkte werden als **unknown/offen** markiert; keine stillen Annahmen.
- **Konservativitätsprinzip:** Keine „craftbar“-/„complete“-Aussagen ohne vollständige Evidenz.

## Phase-1 Roadmap (normativ; kurz)
- **Phase 1:** Log-Finder/Parser → Snapshot+Delta → `collection.json`.
- **Phase 1a:** Rotation/Caching (`Player.log` + `Player-prev.log`), Snapshot-Cache, Snapshot-first Gatekeeping.
- **Phase 1b:** Parser-Härtung (Detailed-Logs-Check, unknown-event Telemetrie, klare Fehlermeldungen).

## Kanonische Log-Quellen
- **Kanonisch:** `Player.log`.
- **Rotation/Caching:** `Player-prev.log` einbeziehen (Reproduzierbarkeit/Snapshot-Cache).
- **Best-effort:** `output_log.txt` nur unsicher, **nie** als Grundlage für „complete“-Claims.

## Snapshot- und Delta-Semantik
- **Snapshot-first:** „complete“ Collection erfordert Snapshot-Event (z. B. GetPlayerCardsV3 o. ä.).
- **Deltas** dürfen **nur** auf Snapshot-Basis angewendet werden.
- Ohne Snapshot: `collection.completeness != "complete"`.

## Vollständigkeits-Semantik (3-stufig)
- Statuswerte: `complete`, `partial`, `unknown`.
- Getrennt für: `cards`, `wildcards`, `source`.
- **Unknown ist nicht 0.** Keine stillen Annahmen.

## Wildcards-Entscheidung (sofort nutzbar, guarded)
- Wildcards werden **sofort** extrahiert.
- Harte Aussagen/Ranking **nur**, wenn `wildcards.completeness == "complete"`.
- Sonst ausschließlich „What-if“ Aussagen.

## Normalisierung und Schlüssel
- **ArenaCardId ist Primärschlüssel.**
- Keine Reprint/Variant/Style-Normalisierung in Phase 1.
- Externe Mapping-Layer erst ab Phase 2+.

## `collection.json` (Phase-1 Kernartefakt)
- Enthält: `schema`, `asOf`, `source`, `collection.completeness`, `completeness` (cards/wildcards/source), `cards`, `wildcards`.
- Minimal-Beispiel:

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
  "collection": {
    "completeness": "complete|partial|unknown"
  },
  "completeness": {
    "cards": "complete|partial|unknown",
    "wildcards": "complete|partial|unknown",
    "source": "complete|partial|unknown"
  },
  "cards": { "12345": 2, "67890": 1 },
  "wildcards": {
    "common": 12,
    "uncommon": 9,
    "rare": 4,
    "mythic": 1,
    "asOf": "ISO-8601",
    "completeness": "complete|partial|unknown",
    "evidence": ["<event-type>"]
  }
}
```

## Erkenntnisse aus externer Repo-Analyse (Essenz)
- Snapshot+Delta als robuste Rekonstruktion (Snapshot ist Gatekeeper).
- Rotation-aware Ingestion (`Player.log` + `Player-prev.log`).
- Event-Dispatch-Pipeline (Segment → Label → Parser pro Event).
- Defensive JSON-Extraktion (mehrzeilige Payloads, partielle Blöcke, Multi-Events).
- „Detailed Logs“-Prüfung mit klarer UX und Fehlermeldung.
- Arena-IDs als Primärschlüssel; externe Mappings optional/später.
- Keine stillen Fallbacks; unknown-event Telemetrie empfohlen.
- Best-effort-Quellen (z. B. `output_log.txt`) sind unsicher und nicht „complete“.

Details/Belege (Events, Fundstellen, Beispiele) siehe `docs/parser-analyse.md`.

| Repo | Fundstelle | Confidence |
|---|---|---|
| _(optional)_ | _(optional)_ | _(optional)_ |

## Umgang mit AI (kurz, verbindlich)
- AI hält sich strikt an dieses Dokument; keine stillen Annahmen.
- Unklarheiten als **unknown/offen** markieren.
- Empfehlungen nur innerhalb Phase-1 Scope und mit expliziter Evidenzschicht in `docs/parser-analyse.md`.
