# ADR 0002: CLI-UX

## Status
Accepted

## Kontext
Phase 1 fokussiert auf einen einmaligen Export der Sammlung aus lokalen Logs. Die UX muss deterministisch, skriptbar und fehlertolerant sein. Nutzer:innen sollen klare Hinweise erhalten, wenn Logs fehlen oder unvollständig sind.

## Entscheidung
- **Ein-Zweck-CLI:** Eine klare Hauptaktion, z. B. `mtga-advisor export`.
- **Deterministische Ausgabe:** Erfolgreiche Runs liefern reproduzierbare Dateien ohne Nebenwirkungen.
- **Fehlerführung:** Explizite Fehlermeldungen mit Handlungsempfehlungen (z. B. „MTGA starten und erneut versuchen“).
- **Transparenz:** Ausgabe der gelesenen Log-Dateien und relevanter Metadaten (Quelle, Zeitstempel, Unsicherheiten).
- **Exit-Codes:** 0 bei Erfolg, >0 bei Fehlern/Unvollständigkeit.

## Konsequenzen
- Fokus auf „Batch“-Nutzung und CI-ähnliche Automationen.
- Keine interaktiven Menüs in Phase 1.
- Logs/Outputs sind gut auditierbar und nachvollziehbar.
