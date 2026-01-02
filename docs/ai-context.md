# Projektkontext (AI)

## 1. Projektziel
- Lokales Tool, das MTG Arena-Spieler:innen eine sammlungsbewusste Deck-Beratung liefert, ohne externe Dienste; Kern ist ein deterministischer, regelbasierter Advisor.
- Zielnutzer: privacy-sensitive Spieler auf macOS/Windows, die nachvollziehbare Empfehlungen wollen; Kernnutzen ist eine verlässliche `collection.json`-Extraktion aus MTGA-Logs als Basis für spätere Ratschläge.
- Nicht: Live-Tracker/Overlay, Cloud-Service oder LLM-getriebenes Produkt; keine Abhängigkeit von Meta-Feeds in Phase 1.

## 2. Aktueller Entwicklungsstand
- Reifegrad: frühe Planung/Phase 0–1 (Dokumentation vorhanden, keine Implementierung).
- Vorhanden: Zieldefinition, Prinzipien, Beispiel-Datenformen (`collection.json`, `arena_deck.json`, `advisor_result.json`, `meta_signals.json`), Scope für Phase 1 (logbasierter Export).
- Experimentell/offen: keine Parser, keine CLI, kein Advisor; Log-Annahmen und Pfaderkennung sind nur dokumentiert, nicht validiert.

## 3. Technische Architektur
- Ordnerstruktur: `README.md` (Kurzüberblick); `docs/` mit Dev-Setup, Roadmap, Daten-Schemas, Source-Analysis, Phase-1-Scope; keine Code-Module vorhanden.
- Verantwortungen (geplant): Log-Finder/Parser (lokale MTGA-Logs → strukturierte Events), Exporter (`collection.json`), Regel-Engine (deterministische Empfehlungen), optionale Meta-Ingestion (offline/importiert), Ausgabe-Formatter (`advisor_result.json`).
- Datenflüsse: MTGA-Logs (Player.log, ggf. Rotation) → defensive Parser → normalisierte Karten-IDs/Owned-Counter → `collection.json` → (optional) Deck-Input + Meta-Signale → regelbasierter Advisor → erklärbare Empfehlungen.
- Entscheidungen: Offline-first, keine externen APIs für Kernpfad; deterministische Regeln mit Transparenz; LLM nur optional/ergänzend; macOS/Windows als Ziel, Linux optional; single-run Export statt dauerndem Daemon; Arena-native Card-IDs in Phase 1 (kein Mapping zu externen Datenquellen).

## 4. Roadmap (Ist-Sicht)
- Kurzfristig: Phase 1 – robuster, lokaler Log-Parser + `collection.json`; Risiken: Log-Format-Drift, OS-Pfadvarianten, unvollständige Logs/Staleness.
- **[Neu]** Phase 1a – Log-Ingestion mit Rotation + Snapshot-Cache (`Player.log` + `Player-prev.log`), Snapshot-first als Gatekeeper für `collection.completeness`.
- **[Neu]** Phase 1b – Parser-Härtung: „Detailed Logs“-Erkennung, unknown-event Telemetrie, klare Fehlermeldungen ohne stillen Fallback; Ausgabe mit 3-stufiger Vollständigkeit.
- Mittelfristig: Phase 2–3 – regelbasierter Advisor + optionale Meta-Imports; Risiken: Regelabdeckung vs. Erklärbarkeit, Meta-Datenqualität ohne Netzwerk, Schema-Versionierung.
- Langfristig: Phase 4–5 – Service-Layer/MCP + optionale LLM-Ergänzungen; Risiken: erhöhte Betriebs- und Datenschutzkomplexität, Wahrung eines voll-offline Pfads.

## 5. Leitplanken & Prinzipien
- Architekturprinzipien: deterministisch vor probabilistisch; erklärbare Regeln; lokale/offline-first Ausführung; defensive Parser mit klaren Fehlermeldungen.
- Technische No-Gos: verpflichtende Netzwerkaufrufe für Kernfunktionen; Abhängigkeit von unoffiziellen APIs; GUI/Overlay-Pflicht; stille Fallbacks ohne Warnung.
- Zu vermeidende Abhängigkeiten: gehostete Deck/Meta-Feeds im Kernpfad; Plattform-spezifische Annahmen (Windows-only Pfade); dauerhafte Daemons.
- Anforderungen: deterministische Outputs mit klaren Quellen; markierte Unsicherheit bei Teil- oder Stalldaten; erklärbare Empfehlungsschritte; bevorzugt vollständig offline.

## Phase-1 Entscheidungen (konsolidiert)

### Konservativitätsprinzip
- Das Tool ist **konservativ**: Es macht keine „craftbar“- oder „vollständig“-Aussagen, wenn Inputs nicht eindeutig vollständig sind.
- **Keine stillen Annahmen**: „unknown“ wird nie als 0 interpretiert.

### Outputs und Scope
- **collection.json (Kernartefakt, Phase 1):**
  - Primärinhalt: `ArenaCardId -> count` (Owned Cards + Counts).
  - Enthält zusätzlich **optional** Wildcards, aber strikt getrennt vom Karten-Block (siehe unten).
  - Muss Metadaten tragen: `schema`, `asOf`, `source`, `completeness`.

- **Wildcards im Kernpfad (Phase 1, für Ranking nutzbar):**
  - Wildcards werden **sofort** analysiert und dürfen in Empfehlungen verwendet werden, **aber nur**, wenn `wildcards.completeness == "complete"`.
  - Wenn Wildcards nicht vollständig sicher bestimmbar sind: Empfehlungen müssen als „What-if“ formuliert werden (keine harten Zusagen).

### Normalisierung und Schlüssel
- **Arena IDs sind die Quelle der Wahrheit (Phase 1):**
  - `ArenaCardId` ist Primärschlüssel für Collection-Zählung.
  - Keine automatische Normalisierung über Set/CollectorNumber/Oracle in Phase 1.
  - Reprints/Varianten/Styles werden nicht zusammengelegt, solange kein expliziter Equivalence-Layer existiert (Phase 2+).

### Kanonische Log-Quellen
- **Kanonisch:** `Player.log` (primäre Quelle).
- **Rotation/Caching:** `Player-prev.log` wird einbezogen (Snapshot-Cache + Reproduzierbarkeit).
- **Fallback:** `output_log.txt` nur Best-effort, klar als unsicher markiert. Kein stiller Ersatz, keine „complete“-Claims daraus.

### Snapshot + Delta Semantik (Collection final)
- **Snapshot-first:** „complete“ Collection erfordert einen Snapshot-Event (z. B. GetPlayerCardsV3 oder äquivalent).
- **Delta-Events** werden nur angewendet, wenn ein Snapshot als Basis existiert.
- Fehlt der Snapshot: `collection.completeness != "complete"` (Warnung, What-if Modus).

### Vollständigkeit (3-stufig)
- Outputs nutzen explizit:
  - `complete` (vollständig und belastbar)
  - `partial` (teilweise, nur eingeschränkt verwendbar)
  - `unknown` (nicht bestimmbar)
- Diese Vollständigkeit gilt getrennt für:
  - Karten (`cards`)
  - Wildcards (`wildcards`)
  - Quelle/Log-Lage (`source`)

### Empfehlung für JSON-Struktur (Phase 1)
- collection.json soll mindestens folgendes ermöglichen:

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

## 6. Offene Fragen & Annahmen
- Pfaderkennung für MTGA-Logs auf macOS/Windows muss getestet; Umgang mit unterschiedlichen Installationspfaden unklar.
- Schema-Versionierung und Drift-Erkennung für Log-Parser und Outputs fehlen; Bedarf an minimalem Baseline-Sample unklar.
- Umgang mit Kartenvarianten/Styles/Finish in Logs (nur IDs vs. weitere Felder) ist offen; Naming/Normalization nur teilweise dokumentiert.

## 7. Umgang mit AI
- AI hält sich strikt an dieses Dokument; keine stillen Annahmen.
- Bei fehlenden Informationen: nachfragen und Unsicherheiten markieren.
- Vorschläge müssen deterministisch begründet und roadmap-konform sein; keine „Best Practices“ ohne Bezug zu Logs/Scope.
- LLM-Einsatz ist optional und darf Kernpfad (lokaler Export + regelbasierter Advisor) nicht beeinflussen.

## 8. Erkenntnisse aus externer Repo-Analyse
- Empfohlene Architekturentscheidungen:
  - Snapshot-first + Delta: primärer Collection-Snapshot aus `PlayerInventory.GetPlayerCardsV3`, danach `Inventory.Updated`-Deltas anwenden; bei fehlendem Snapshot nur warnen (kein stilles Fallback).
  - Log-Ingestion mit Rotation/Caching: kombiniere `Player.log` + `Player-prev.log`, halte lokale Snapshots (z. B. letzte 3–5) für Reproduzierbarkeit und Offline-Betrieb.
  - Parser-Pipeline mit Event-Dispatch: robustes Log-Splitting (UnityCrossThreadLogger/Client GRE) → Event-Label-Dispatch → typed Parser pro Event (GetPlayerCards, Inventory, Decks).
  - Explizite „Detailed Logs“-Prüfung mit klaren Fehlermeldungen und UX-Hinweisen, nicht still weiterarbeiten.
  - Defensive JSON-Extraktion: mehrzeilige Payloads, partielle JSON-Blöcke, Mehrfach-Events pro Chunk.
  - Lokale IDs als Quelle der Wahrheit: Arena IDs als Primärschlüssel; externe Mapping/Name-Auflösung nur optionaler, separater Pfad.
  - Parser-Pipeline mit Event-Dispatch: robustes Log-Splitting (UnityCrossThreadLogger/Client GRE) → Event-Label-Dispatch → typed Parser pro Event (GetPlayerCards, Inventory, Decks).
  - Defensive JSON-Extraktion: mehrzeilige Payloads, partielle JSON-Blöcke, Mehrfach-Events pro Chunk.
- Bewusste Nicht-Ziele:
  - Kein verpflichtender Upload/Cloud-Sync; keine Server-Abhängigkeit im Kernpfad.
  - Keine Overlay-/In-Game-UI-Pflicht in Phase 1.
  - Kein Zwang zu Scryfall/externen APIs für die Collection-Rekonstruktion.
- Risiken & offene Fragen (aus Repo-Befunden abgeleitet):
  - Log-Drift: Eventnamen/Strukturen ändern (z. B. Payload-Layouts), daher Parser-Versionierung & „unknown event“ Telemetrie notwendig.
  - „Collection final“ abhängig von letztem Snapshot-Event; reine Deltas ohne Snapshot liefern keine sichere Vollständigkeit.
  - Plattformpfade variieren (Windows/macOS, Steam/Wine); Pfaderkennung muss getestet und konfigurierbar sein.
  - Umgang mit Varianten (Styles, Reprints, Sondereditionen) bleibt unklar, wenn nur Arena-ID + Count vorliegt.
