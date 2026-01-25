# ADR 0005: Advisor-Core und LLM-Option

## Status
Accepted

## Kontext
Der Advisor ist ein Kernziel nach Phase 1. Nutzer sollen Deck-Optimierung und Wildcard-Beratung erhalten, auch ohne bezahlte Abos. LLMs koennen Mehrwert liefern, sind aber nicht zwingend verfuegbar (free/paid/none).

## Entscheidung
- **Baseline:** Eine deterministische Rules-Engine liefert immer nutzbare Empfehlungen.
- **LLM-Option:** LLM-Integration ist opt-in (API oder Prompt/Copy-Paste Workflow).
- **Datenfluss:** Advisor arbeitet auf Export-Artefakten (z. B. `collection.json`, `decks.json`) und schreibt `advice.json`.
- **Kostenfokus:** Kein Kernpfad darf von bezahlten Abos abhaengig sein.

## Konsequenzen
- Advisor-Schnittstellen muessen Rules-Engine und LLM-Engine trennen koennen.
- Prompt-Workflows und Import-Validierung benoetigen klare Formate.
- Empfehlungstexte sollten erklart und nachvollziehbar bleiben.
