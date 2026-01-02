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
- Mittelfristig: Phase 2–3 – regelbasierter Advisor + optionale Meta-Imports; Risiken: Regelabdeckung vs. Erklärbarkeit, Meta-Datenqualität ohne Netzwerk, Schema-Versionierung.
- Langfristig: Phase 4–5 – Service-Layer/MCP + optionale LLM-Ergänzungen; Risiken: erhöhte Betriebs- und Datenschutzkomplexität, Wahrung eines voll-offline Pfads.

## 5. Leitplanken & Prinzipien
- Architekturprinzipien: deterministisch vor probabilistisch; erklärbare Regeln; lokale/offline-first Ausführung; defensive Parser mit klaren Fehlermeldungen.
- Technische No-Gos: verpflichtende Netzwerkaufrufe für Kernfunktionen; Abhängigkeit von unoffiziellen APIs; GUI/Overlay-Pflicht; stille Fallbacks ohne Warnung.
- Zu vermeidende Abhängigkeiten: gehostete Deck/Meta-Feeds im Kernpfad; Plattform-spezifische Annahmen (Windows-only Pfade); dauerhafte Daemons.
- Anforderungen: deterministische Outputs mit klaren Quellen; markierte Unsicherheit bei Teil- oder Stalldaten; erklärbare Empfehlungsschritte; bevorzugt vollständig offline.

## 6. Offene Fragen & Annahmen
- Noch unvalidiert, welche Log-Events stabil alle Kartenbestände liefern (Snapshot vs. Deltas) und wie Rotation/Archiv-Logs einbezogen werden.
- Pfaderkennung für MTGA-Logs auf macOS/Windows muss getestet; Umgang mit unterschiedlichen Installationspfaden unklar.
- Schema-Versionierung und Drift-Erkennung für Log-Parser und Outputs fehlen; Bedarf an minimalem Baseline-Sample unklar.
- Umgang mit Kartenvarianten/Styles/Finish in Logs (nur IDs vs. weitere Felder) ist offen; Naming/Normalization nur teilweise dokumentiert.

## 7. Umgang mit AI
- AI hält sich strikt an dieses Dokument; keine stillen Annahmen.
- Bei fehlenden Informationen: nachfragen und Unsicherheiten markieren.
- Vorschläge müssen deterministisch begründet und roadmap-konform sein; keine „Best Practices“ ohne Bezug zu Logs/Scope.
- LLM-Einsatz ist optional und darf Kernpfad (lokaler Export + regelbasierter Advisor) nicht beeinflussen.
