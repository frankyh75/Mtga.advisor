# Deck Advisor Roadmap v2 — Ist-Zustand + Sprint-Plan

Datum: 2026-08-04

## Verhältnis zu anderen Docs

- `docs/deck-advisor-roadmap.md` — ursprüngliches Zielbild (Screens, Datenmodell,
  Prompt-Struktur). Bleibt als Referenz für die langfristige Vision gültig.
- `docs/deck-scan-problem-report.md` — abgeschlossen: IL2CPP-Deck-Scan liefert
  jetzt zuverlässig echte Decks (21/21 im Test), keine offenen Punkte mehr.
- **Diese Datei** — realitätsgeprüfter Ist-Zustand plus konkreter,
  extern umsetzbarer Sprint-Plan. Coding erfolgt extern (GLM 5.2 / Hermes);
  diese Datei ist die Aufgabenliste dafür, nicht der Implementierungsort.

## Ist-Zustand (Stand 2026-08-04)

Server ist `server/app.py` (Python stdlib `BaseHTTPRequestHandler`, kein
Node/JS-Server — `server/dashboard.js` ist nur das Frontend-JS, HTML kommt
aus Python-Templates in `_render_index`).

### Vorhandene Endpoints (verifiziert)

| Endpoint | Datei:Zeile | Zweck |
|---|---|---|
| `GET /api/collection` | `app.py:1264` | rohes `collection.json` |
| `GET /api/decks` | `app.py:1272` | rohes `decks.json` |
| `GET /api/decks-container` | `app.py:1280` | Decks-Container |
| `GET /api/ranks` | `app.py:1288` | Rang-Daten |
| `GET /api/card-image/<grpId>` | `app.py:1297` | Karten-Bild |
| `GET /api/deck/<deckId>` | `app.py:1313` | einzelnes Deck (Singular-Route!) |
| `GET /api/deck-cards?deck_id=` | `app.py:1376` | Kartenliste eines Decks (Query-Param-Variante) |
| `GET /api/run-report` | `app.py:1325` | Scan-Report |
| `GET /api/advisor-result` | `app.py:1333` | vorberechnetes `advisor-result.json` (Batch-Advisor) |
| `GET/POST /api/config` | `app.py:1341`/`1478` | LLM-Konfiguration |
| `GET /api/meta` | `app.py:1348` | Meta-Daten (via `advisor/meta.py`) |
| `GET /api/history/snapshots`, `/api/history/diff` | `app.py:1363`/`1368` | Verlauf |
| `POST /api/history/save` | `app.py:1515` | Snapshot speichern |
| `POST /api/chat` | `app.py:1423` | LLM-Chat mit Deck-Kontext |

### Kritischer Bug (blockiert Deck-Detail + Chat)

`server/dashboard.js:294` und `:328` rufen `GET /api/decks/{deckKey}`
(**Plural**) auf. Diese Route existiert **nicht** — nur `GET /api/deck/<id>`
(Singular) und `GET /api/deck-cards?deck_id=`. Deck-Detail-Panel und
Chat-Panel laufen dadurch aktuell live gegen 404. Höchste Priorität, da alles
Weitere (Analyze/Improve/neue Suche) auf einem funktionierenden Deck-Detail
aufbaut.

### Frontend-Stand

Bereits vorhanden in `dashboard.js`/`_render_index`: Deck-Liste/Tabelle mit
Precon-Filter-Toggle, Deck-Detail-Panel, Chat-Panel (`#chat-panel`), Meta-,
Ranks- und History-Panels, LLM-Config-Formular. **Fehlt komplett:**
Deck-Namens-Suchfilter, Collection/Karten-Browser mit Suche, New-Deck-Builder-
Formular (Format/Farben/Archetyp/Rare-Limit/Budget), Analyze/Improve/Export-
Schnellaktionen, Advisor Result View.

### Backend-Advisor-Logik

`advisor/llm_advisor.py` ist ein **separater Batch-Pfad** (CLI-getrieben,
schreibt `advisor-result.json`) mit eigener Prompt-/Parse-Logik
(`_build_prompt`, `_call_llm`, `_parse_response`). Nutzt nicht die
geplanten `/api/advisor/*`-HTTP-Routen. `POST /api/advisor/build`,
`POST /api/advisor/analyze`, `POST /api/advisor/chat` existieren **nicht**,
auch nicht als Stub.

### Datenpipeline

Funktioniert: `out/collection.json` (via `scanner/memory_scanner.py`),
`out/decks.json` (via `cli/main.py`, jetzt mit vollständiger IL2CPP-Navigation).

## Neue Anforderungen (dieser Runde)

1. **Deck-Suchfilter** — Textfeld über der Deck-Liste, filtert nach
   Deckname (Client-seitig, kein neuer Endpoint nötig — `decks.json` ist
   bereits komplett im Dashboard geladen).
2. **Collection/Karten-Browser mit Suche** — neue, eigenständige Ansicht
   (nicht an ein einzelnes Deck gebunden), durchsucht die volle Sammlung
   (`collection.json` + Karten-DB für Namen/Text/Typ). Eigene Phase, siehe
   Sprint 2/3 unten — größerer Baustein als der Deck-Namensfilter, da er
   echte Kartendaten (Name, Farbe, Typ, Rarity) braucht, nicht nur grpIds.

## Sprint 1 — Stabilisieren + kleine neue Bausteine

1. **Bugfix `/api/decks/{deckKey}` → `/api/deck/<id>`**: Frontend-Aufrufe
   in `dashboard.js:294`/`:328` korrigieren (oder Backend-Alias ergänzen).
   Kleinster Fix, größter Hebel — entsperrt Deck-Detail und Chat.
2. **`server/dashboard.js`-Merge-Konflikt committen**: Arbeitsverzeichnis ist
   bereits konfliktfrei (keine `<<<<<<<`-Marker mehr), aber nie eingecheckt.
   Vor jeder weiteren Arbeit an der Datei sauber committen.
3. **Chat-Panel-Regressionstest** mit echten, vollständigen `decks.json`/
   `collection.json` (jetzt dank IL2CPP-Fix verlässlich) end-to-end
   durchklicken — weitere stille Brüche vor neuen Endpoints finden.
4. **Deck-Namens-Suchfilter**: Textfeld über der Deck-Liste in `dashboard.js`,
   filtert die bereits geladene `decks.json`-Liste client-seitig nach Name.
5. **`POST /api/advisor/build` als Stub**: Request/Response-Schema exakt wie
   in `docs/deck-advisor-roadmap.md` (Zeilen 117–153) beschrieben, zunächst
   ohne echte LLM-Logik — nur damit das Builder-Formular dagegen entwickelt
   werden kann.
6. **New-Deck-Builder-Formular**: Format-Dropdown, Farbwahl, Archetyp,
   `maxRares`, optional Budget/„owned-first" — reiner UI-Baustein gegen den
   Stub aus Punkt 5.

## Sprint 2 — Analyze-Flow + Collection-Browser

7. **`POST /api/advisor/analyze`** mit echter Collection-Context-Logik
   (`missingCards`, `craftPriorities`, `cuts`) — kann Prompt-/Parse-Logik
   aus `advisor/llm_advisor.py` wiederverwenden statt neu zu bauen.
8. **Advisor Result View**: strukturierte Blöcke (Summary/Core/Missing/
   Craft/Cuts/Mana/Risk) im Frontend.
9. **Collection/Karten-Browser mit Suche**: neue Ansicht über
   `/api/collection` (bereits vorhanden) + Karten-DB-Join für
   Name/Text/Typ/Farbe-Filter. Braucht ggf. eine Erweiterung des
   Endpoints oder client-seitiges Filtern, falls Kartendetails schon im
   Payload stecken — vor Implementierung prüfen, was `/api/collection`
   aktuell tatsächlich zurückgibt (grpId-Liste vs. angereicherte Objekte).
10. **Analyze/Improve/Export-Schnellaktionen** im Deck-Detail-Panel.

## Sprint 3 — Qualität, Persistenz, UX

11. Entwurf speichern/laden (neues Artefakt, z.B. `out/advisor-drafts/`).
12. Prompt-Fixtures + Tests für Build-/Analyze-Prompts.
13. UI-Tests für Rare-Limit/Format-Formular und beide neuen Suchfilter.
14. Bessere Karten-Rendering-Ansichten (Bilder, Rarity-Farbcodierung) im
    neuen Collection-Browser.

## Offene Fragen (vor Sprint 2 zu klären)

- Enthält `/api/collection`s aktuelle Antwort bereits Kartennamen/Text/Typ,
  oder nur grpIds gegen die lokale Karten-DB? Bestimmt, ob der
  Collection-Browser rein client-seitig filtern kann oder einen erweiterten
  Endpoint braucht.
- Soll der Deck-Namensfilter (Sprint 1) auch auf Precon-Decks wirken, oder
  nur auf eigene (der bestehende Precon-Toggle bleibt ja erhalten)?
