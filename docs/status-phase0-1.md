# Projektstatus Mtga.advisor – Phase 0 & 1

## 1. Überblick für Nicht-Techniker (Pflichtlektüre)
- Das Projekt ist ein **lokales** Werkzeug, das aus MTG Arena‑Logdateien eine zuverlässige Übersicht der eigenen Kartensammlung erstellt.
- Nach Phase 1 kann man **ohne Internet** eine Sammlung als Datei exportieren und im Browser verständlich ansehen.
- Der Export basiert **nur** auf lokalen Logdateien; es gibt **keinen Cloud‑Zwang** und keine externen Datenquellen.
- Ergebnis ist eine klare Datei (`collection.json`), die später als Grundlage für Empfehlungen dienen kann.
- Es gibt eine Anzeige der Datenqualität (vollständig/teilweise/unbekannt) und Hinweise, wenn Logs fehlen oder veraltet sind.
- **Bewusst noch nicht möglich:** Deck‑Empfehlungen, Meta‑Analysen, Match‑Statistiken oder Live‑Overlay im Spiel.
- **Bewusst noch nicht möglich:** Kartennamen/Bilder oder externe Datenanreicherung – es bleiben reine Karten‑IDs.
- Der Weg ist bewusst offline, um **Datenschutz**, **Nachvollziehbarkeit** und **Determinismus** zu sichern.
- Der aktuelle Stand ist **dokumentiert und abgegrenzt** (Phase‑0‑Grundlagen, Phase‑1‑Scope).
- **OFFEN:** Der tatsächliche Implementierungs‑/Validierungsstand der Parser‑Logik ist aus den Dokumenten nicht eindeutig ersichtlich.

## 2. Was in Phase 0 erreicht wurde
- Die **Grundlagen sind festgelegt**: Repository‑Struktur und Dokumentation sind definiert.
- Die **Datenformen** (z. B. Sammlung/Export) sind beschrieben.
- Der **Ansatz ist entschieden**: lokal, deterministisch, ohne Cloud‑Abhängigkeit.
- Die **Phasen‑Leitplanken** sind klar: Phase 1 liefert ausschließlich die Sammlungsextraktion.

## 3. Was Phase 1 konkret liefern soll
- **Was mit den MTGA‑Logs passiert:**
  - Lokale MTGA‑Logdateien werden eingelesen.
  - Aus den Logs wird ein Sammlungs‑Snapshot abgeleitet.
  - Das Ergebnis wird als `collection.json` (plus `run-report.json`) exportiert.
- **Was der Nutzer am Ende im Browser sieht:**
  - Eine lesbare Ansicht der JSON‑Sammlung.
  - Eine Statusanzeige zur Vollständigkeit (z. B. vollständig/teilweise/unbekannt).
  - Warnungen, wenn Logs fehlen, veraltet oder unvollständig sind.
- **Explizit NICHT Teil von Phase 1:**
  - Keine Empfehlungen, keine Meta‑Signale, keine Deck‑Verbesserungen.
  - Keine Live‑Synchronisation oder Hintergrund‑Überwachung.
  - Keine externen Datenquellen, keine Cloud‑API, keine Kartengrafiken.

## 4. Offene Punkte & Risiken (verständlich)
- **OFFEN:** Wie zuverlässig und vollständig die Logs auf allen Zielplattformen (Windows/macOS, Linux optional) verfügbar sind.
- **OFFEN:** Ob die Logs immer einen vollständigen Sammlungs‑Snapshot enthalten oder nur Teil‑Updates.
- **OFFEN:** Wie stark das Log‑Format driftet und wie robust der Parser dagegen ist.
- **OFFEN:** Ob „Detailed Logs“ (erweiterte Logs) aktiv sein müssen und wie das erkannt wird.
- **OFFEN:** Wie alt eine akzeptable Log‑Basis sein darf (Stichwort „veraltete Daten“).

## 5. Technischer Anhang (optional, für Entwickler / spätere Referenz)
- **Parser‑Pipeline (grob):**
  - Log‑Segmente erkennen → Events labeln → je Event‑Typ parsern.
  - Snapshot ist zwingend; Deltas werden nur auf Snapshot‑Basis angewendet.
- **Artefakte:**
  - `collection.json` (Sammlung, Vollständigkeit, Quelle)
  - `run-report.json` (Laufbericht, gelesene Logs, Diagnostik)
- **Lokaler Webserver:**
  - Dient nur der **Anzeige** der exportierten JSON und der Warnungen.
  - Kein Cloud‑Dienst, keine externe Abhängigkeit.

## 6. Abgleich mit ai-context.md
- **Neu oder präziser gegenüber `docs/ai-context.md`:**
  - Konkrete Ausgabedateien neben `collection.json` (z. B. `run-report.json`, `raw-samples/`).
  - UI‑Beschreibung für die JSON‑Ansicht und Warnungsanzeige im Browser.
  - Ausführliche Failure‑Modes (fehlende Logs, Format‑Drift, veraltete Daten).
  - Detaillierte Phase‑1‑Nicht‑Ziele (z. B. keine Bilder, keine externen Quellen).
- **Als veraltet zu markieren:**
  - **Keine erkennbaren veralteten Aussagen** in `docs/ai-context.md`.
  - `ai-context.md` bleibt die Leitplanke; dieses Statusdokument ergänzt Details aus den Phase‑1‑Spezifikationen.
