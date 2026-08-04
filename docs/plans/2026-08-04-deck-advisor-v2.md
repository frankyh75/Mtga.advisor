# Deck Advisor Roadmap v2 — Implementation Plan

> **Branch:** `feat/deck-advisor-v2`
> **Assignee:** worker-heavy (GLM 5.2 via Ollama Cloud)
> **Basierend auf:** `docs/deck-advisor-roadmap-v2.md`

**Goal:** Sprint 1 (Stabilisieren + kleine Bausteine) und Sprint 2 (Analyze-Flow + Collection-Browser) umsetzen. Sprint 3 folgt später.

**Architecture:** Keine Breaking Changes. Neue Endpoints in `server/app.py`, neue UI-Blöcke in `server/dashboard.js`, neue Advisor-Logik in `advisor/`.

---

## Sprint 1 — Stabilisieren + kleine Bausteine

### T1: innerHTML-Refactor

**Objective:** History-/Meta-/Ranks-Render-Blöcke in `dashboard.js` von `innerHTML` auf sichere DOM-APIs (`createElement`/`textContent`) umstellen.

**Files:**
- Modify: `server/dashboard.js` (ca. Zeile 550+, History-/Meta-/Ranks-Renderer)

**Verification:** `python3 -m pytest server/tests/test_app.py::test_dashboard_script_is_loaded_from_asset -v` → PASS

---

### T2: Chat-Panel-Regressionstest

**Objective:** End-to-End-Test, der das Chat-Panel mit echten `decks.json`/`collection.json` durchklickt.

**Files:**
- Create: `server/tests/test_chat_e2e.py`

**Verification:** `python3 -m pytest server/tests/test_chat_e2e.py -v` → PASS

---

### T3: Deck-Namens-Suchfilter

**Objective:** Textfeld über der Deck-Liste in `dashboard.js`, filtert client-seitig nach Deckname. Wirkt nur auf eigene Decks (Precon-Toggle bleibt erhalten).

**Files:**
- Modify: `server/dashboard.js` (neues Suchfeld + Filter-Logik)
- Modify: `server/app.py` (Template: Suchfeld-HTML einfügen)

**Verification:** Textfeld eingeben → Deck-Liste filtert live. Precon-Toggle + Suchfilter kombinierbar.

---

### T4: POST /api/advisor/build als Stub

**Objective:** Request/Response-Schema exakt wie in `docs/deck-advisor-roadmap.md` (Zeilen 117–153). Zunächst ohne echte LLM-Logik — nur damit das Builder-Formular dagegen entwickelt werden kann.

**Files:**
- Modify: `server/app.py` (neue Route `POST /api/advisor/build`)
- Create: `server/tests/test_advisor_build.py`

**Verification:** `curl -X POST http://127.0.0.1:8000/api/advisor/build -H 'Content-Type: application/json' -d '{"format":"Standard","colors":["R"],"archetype":"aggro","maxRares":8}'` → 200 + JSON

---

### T5: New-Deck-Builder-Formular

**Objective:** UI-Formular im Dashboard: Format-Dropdown, Farbwahl, Archetyp, maxRares, optional Budget/owned-first. Ruft `POST /api/advisor/build` auf.

**Files:**
- Modify: `server/dashboard.js` (Builder-Formular + Submit-Logik)
- Modify: `server/app.py` (Template: Builder-HTML einfügen)

**Verification:** Formular ausfüllen → Submit → Response wird angezeigt.

---

### T6: /api/collection/enriched Endpoint

**Objective:** Neuer Endpoint, der grpIds aus `collection.json` mit der lokalen SQLite-Card-DB joint. Pro Karte: Name, Rarity, Mana Cost, CMC, Type Line, Colors, Image URI.

**Files:**
- Create: `server/collection_enricher.py` (SQLite-Join + JSON-Serialisierung)
- Modify: `server/app.py` (neue Route `GET /api/collection/enriched`)
- Create: `server/tests/test_collection_enricher.py`

**Verification:** `curl http://127.0.0.1:8000/api/collection/enriched` → 200 + JSON mit `cards[]` (Name, Rarity, CMC, etc.)

---

## Sprint 2 — Analyze-Flow + Collection-Browser

### T7: POST /api/advisor/analyze mit echter Logik

**Objective:** Nutzt Prompt-/Parse-Logik aus `advisor/llm_advisor.py`. Analysiert ein Deck gegen die Collection und gibt Craft-Prioritäten, Missing Cards, Cuts zurück.

**Files:**
- Modify: `server/app.py` (neue Route `POST /api/advisor/analyze`)
- Modify: `advisor/llm_advisor.py` (ggf. Extraktion der Prompt-Logik)
- Create: `server/tests/test_advisor_analyze.py`

**Verification:** `curl -X POST http://127.0.0.1:8000/api/advisor/analyze -H 'Content-Type: application/json' -d '{"deckId":"..."}'` → 200 + strukturierte Analyse

---

### T8: Advisor Result View

**Objective:** Strukturierte Blöcke im Frontend: Summary, Core Cards, Missing Cards, Craft Priorities, Cuts, Mana Curve, Risk Assessment.

**Files:**
- Modify: `server/dashboard.js` (Result-View-Renderer)
- Modify: `server/app.py` (Template: Result-View-HTML)

**Verification:** Nach Analyze-Response → strukturierte Ansicht mit allen Blöcken.

---

### T9: Collection/Karten-Browser mit Suche

**Objective:** Neue Ansicht im Dashboard, durchsucht `/api/collection/enriched` client-seitig nach Name, Text, Typ, Farbe, Rarity, CMC.

**Files:**
- Modify: `server/dashboard.js` (Collection-Browser-View + Filter)
- Modify: `server/app.py` (Template: Browser-HTML)

**Verification:** Filter nach Name → Liste filtert. Filter nach Farbe + Rarity → kombiniert.

---

### T10: Analyze/Improve/Export-Schnellaktionen

**Objective:** Buttons im Deck-Detail-Panel: Analyze (→ T7), Improve (LLM-Optimierung), Export (Arena-Text).

**Files:**
- Modify: `server/dashboard.js` (Schnellaktionen + Handler)
- Modify: `server/app.py` (Template: Action-Buttons)

**Verification:** Klick auf "Export" → Arena-kompatibler Text. Klick auf "Analyze" → Result View.

---

## Dependencies

```
T1 (innerHTML) — keine
T2 (Chat-Test) — keine
T3 (Suchfilter) — keine
T4 (Build-Stub) — keine
T5 (Builder-UI) — T4 (braucht den Stub)
T6 (Enriched) — keine
T7 (Analyze) — T4 (Prompt-Struktur)
T8 (Result View) — T7 (braucht Analyze-Response)
T9 (Collection-Browser) — T6 (braucht enriched-Endpoint)
T10 (Schnellaktionen) — T7 + T8 (braucht Analyze + Result View)
```

## Erfolgskriterien

- [ ] `test_dashboard_script_is_loaded_from_asset` → PASS
- [ ] Chat-Panel funktioniert mit echten Daten
- [ ] Deck-Suchfilter filtert client-seitig, nur eigene Decks
- [ ] `POST /api/advisor/build` → 200 + JSON
- [ ] Builder-Formular sichtbar + submitbar
- [ ] `GET /api/collection/enriched` → 200 + angereicherte Karten
- [ ] `POST /api/advisor/analyze` → 200 + strukturierte Analyse
- [ ] Advisor Result View zeigt alle Blöcke
- [ ] Collection-Browser filtert nach Name/Farbe/Rarity/CMC
- [ ] Export-Button erzeugt Arena-Text
- [ ] Alle Tests: `python3 -m pytest server/tests/ -v`
