# ADR 0003: Output-Artefakte

## Status
Accepted

## Kontext
Phase 1 liefert ausschließlich lokale, deterministische Exporte. Downstream-Tools sollen ohne zusätzliche Services auf die Artefakte zugreifen können. Output muss stabil versionierbar und diff-freundlich sein.

## Entscheidung
- **Primärartefakt:** `collection.json` mit Kartenzählern (Arena-IDs) und Metadaten.
- **Metadaten:** Ein `metadata`-Block mit Quelle (`local-logs`), Zeitstempel, Log-Dateien und eventuellen Warnungen.
- **Keine Fremdanreicherung:** Keine Online-Services, keine Karten-Namen oder Sets ohne logbasierten Nachweis.
- **Optionaler Bericht:** Ein textueller `report.txt` nur für menschenlesbare Hinweise (Warnungen, Log-Status).

## Konsequenzen
- Exporte bleiben deterministisch und auditierbar.
- Karten-Namen/Set-Codes erfordern später eine explizite, getrennte Datenquelle.
- Tooling kann Artefakte im Git-Workflow leicht vergleichen.
