# Deck-Scan Roadmap v3 — Zuverlässigkeit, Format, Aufräumen

Status: größtenteils erledigt. Die aktive Weiterplanung liegt in
`docs/deck-advisor-roadmap-v4.md`; diese Datei bleibt als technische
Historie und Restliste.

Datum: 2026-08-04

## Verhältnis zu anderen Docs

- `docs/deck-scan-problem-report.md` — Ursprungsproblem (`data_segment_base`
  nicht auffindbar) + Lösung (Backref-Ansatz über FieldInfo-Strings) +
  Nachträge vom 2026-08-04 (dynamische Region-Enumeration, Arena-Coalescing,
  Pattern-Scan-ID-Kollision). Bleibt technische Referenz für den
  IL2CPP-Navigationsmechanismus.
- **Diese Datei** — nächste Runde konkreter Verbesserungen, extern
  umsetzbar. Coding erfolgt extern (GLM 5.2 / Hermes); diese Datei ist die
  Aufgabenliste dafür, nicht der Implementierungsort.

## Ist-Zustand (Stand 2026-08-05)

- [x] IL2CPP-Backref-Scan ist der primäre Weg und liefert echte Decks mit
  Kartenauflösung.
- [x] `--debug` schreibt nachvollziehbare Discovery-Logs.
- [x] Region-Coalescing und dynamische Heap-Enumeration sind umgesetzt.
- [x] Retry vor Fallback ist umgesetzt.
- [x] Schreibschutz/Backup gegen schlechte Scan-Ergebnisse ist umgesetzt.
- [x] Pattern-Scan-ID-Kollision ist behoben.
- [ ] `scanner/rank_scanner.py` hängt noch am älteren Entdeckungspfad.
- [ ] Komfortweg ohne jedes Mal `sudo` ist noch nicht gelöst.
- [ ] Format-Erkennung ist nur dann sinnvoll, wenn die zugrunde liegende
  Struktur verlässlich identifiziert werden kann.

## Nicht mehr aktiv

- `data_segment_base`-Debugging für Deck-Scan ist nicht mehr der
  Hauptfokus.
- Pattern-Scan als primärer Workaround ist überholt; nur noch als
  Fallback interessant.
- Die alte "alles in einem Scan per `sudo`"-Idee ist nur dann sinnvoll,
  wenn sie als echte Komfortschicht gebaut wird.

## Restliste

1. `rank_scanner.py` auf den Backref-Ansatz migrieren, falls Ranks in den
   gleichen Privileg-/Stabilitätsstandard wie Decks gehoben werden sollen.
2. Einen sauberen Privileged-Helper oder Wrapper bauen, damit `sudo`
   nicht bei jedem manuellen Aufruf eingegeben werden muss.
3. Die Format-Erkennung nur dann weiterverfolgen, wenn ein verlässliches
   Feld im Live-Layout gefunden wird.

## Offene Fragen

- Soll der Privileged-Helper nur den Scanner starten, oder gleich auch
  die Dashboard-/Sync-Commands bündeln?
- Reicht ein dokumentierter Wrapper mit `sudo -v`-Vorlauf, oder lohnt sich
  der Aufwand für einen echten Helper/Daemon mit Entitlement?
