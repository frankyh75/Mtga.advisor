# ADR 0003: Output-Artefakte

## Status
Accepted

## Kontext
Phase 1 liefert ausschließlich lokale, deterministische Exporte. Downstream-Tools sollen ohne zusätzliche Services auf die Artefakte zugreifen können. Output muss stabil versionierbar und diff-freundlich sein.

## Entscheidung
- **Output-Struktur:** `out/collection.json`, `out/run-report.json`, `out/raw-samples/`.
- **Primärartefakt:** `collection.json` mit Kartenzählern (Arena-IDs) und Diagnostics.
- **Run-Report:** `run-report.json` enthält Zeitstempel, Log-Dateien und eine Zusammenfassung des Runs.
- **Deterministisch:** `collection.json` bleibt ohne Zeitstempel; Zeitangaben ausschließlich im Report.
- **Keine Fremdanreicherung:** Keine Online-Services, keine Karten-Namen oder Sets ohne logbasierten Nachweis.
- **Raw-Samples:** Einzelne JSON-Dateien pro relevante Event-Payload zur Nachvollziehbarkeit.

## Konsequenzen
- Exporte bleiben deterministisch und auditierbar.
- Karten-Namen/Set-Codes erfordern später eine explizite, getrennte Datenquelle.
- Tooling kann Artefakte im Git-Workflow leicht vergleichen.
