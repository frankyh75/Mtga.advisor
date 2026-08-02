# T5: Testing & Validation Report

## Übersicht

T5 validiert die in T1-T4 entwickelte Deck-Memory-Scanner-Integration auf dem MacBook.
Alle 178 Tests bestanden (0 failures). 2 pre-existing failures wurden behoben.
10 neue E2E-Validation-Tests hinzugefügt.

---

## Test-Ergebnisse

### Full Test Suite: 178 passed, 0 failed (24.88s)

| Modul | Tests | Status |
|-------|-------|--------|
| advisor/tests/test_advisor.py | 3 | ✓ |
| advisor/tests/test_deck_integration.py | 13 | ✓ |
| advisor/tests/test_e2e_validation.py | 10 (neu) | ✓ |
| cli/tests/test_cli.py | 9 | ✓ |
| parser/tests/test_*.py | 9 | ✓ |
| scanner/tests/test_scanner.py | 8 | ✓ |
| scanner/tests/test_card_database_edge_cases.py | 13 | ✓ (2 fixed) |
| scanner/tests/test_deck_scanner.py | 23 | ✓ |
| scanner/tests/test_il2cpp_nav.py | 8 | ✓ |
| server/tests/test_app.py | 3 | ✓ |
| test_cli_verification.py | 1 | ✓ (repariert) |
| **Total** | **178** | **✅** |

### Security-Tests (hermes_verify.py): 22 passed, 0 failed

| Kategorie | Tests | Status |
|-----------|-------|--------|
| Path-Traversal-Safety (server/app.py) | 5 | ✓ |
| deck_id Sanitization (cli/main.py) | 5 | ✓ |
| Prompt Truncation (advisor/llm_advisor.py) | 12 | ✓ |

### Container Verification Script: ✓ alle bestanden

---

## Bug Fixes

### 1. Pre-existing: Same-name cards across sets (deck_import.py)

**Problem:** `import_arena_deck` markierte same-name cards aus verschiedenen Sets
als ambiguous und übersprang sie. Das Resultat: leeres Mainboard für Karten wie
"Opt" die in XLN und STA existieren.

**Fix:** Same-name cards werden jetzt als reprint erkannt und automatisch
zum alphabetisch letzten Set resolved. Der ambiguity-Eintrag entfällt.

**Dateien geändert:**
- `advisor/deck_import.py` — Logik für `len(candidates) > 1` geändert
- `advisor/tests/test_advisor.py` — Test angepasst an neues Verhalten
- `scanner/tests/test_card_database_edge_cases.py` — Tests jetzt grün

### 2. test_cli_verification.py: Container-Flow repariert

**Problem:** Der Test erzeugte `decks.json` aber rief `decks list/show` ohne
vorherigen `decks container` auf. Die CLI sucht nach `index.json` im
Container-Verzeichnis, nicht nach `decks.json`.

**Fix:** Test-Flow umgestellt: erst `decks container` erstellen, dann
`decks list` und `decks show` aus dem Container. Verwendet jetzt `assert`
statt `return False/True` (pytest-konform).

### 3. hermes_verify.py: Sanitize-Assertion korrigiert

**Problem:** Die erwartete Ausgabe für `sanitize("../../../etc/passwd")` war
falsch (`"__________.____passwd"` statt `"_________etc_passwd"`).

**Fix:** Erwartung an die tatsächliche (korrekte) Ausgabe angepasst.

---

## E2E Validation Tests (neu: advisor/tests/test_e2e_validation.py)

10 neue End-to-End Tests validieren den kompletten Flow:

### 1. Memory Scan → Advisor → LLM Prompt

**test_e2e_memory_scan_to_advisor:**
- Simuliert deck.v1 (IL2CPP-Scanner Output)
- Normalisierung via `_normalize_deck_cards`
- `build_completion_advice` mit Collection + Deck + card_db
- `_build_prompt` generiert LLM-Prompt mit Kartennamen
- Validiert: 10 missing cards, 4 unique, alle Kartennamen im Prompt

### 2. Server API Endpoints

**test_e2e_decks_container_to_server:**
- Erzeugt `decks-container.json` und individuelle `deck-{id}.json` Files
- Startet Server auf localhost
- GET `/api/decks-container` → 2 Decks
- GET `/api/deck/e2e-001` → Red Aggro mit Karten
- GET `/api/deck/e2e-002` → Blue Control

### 3. Realistic Arena Import

**test_e2e_realistic_arena_import:**
- Importiert ein realistisches Arena Deck (Red Aggro, 10 unique mainboard, 3 sideboard)
- Feeds in completion advisor → missing cards detected

### 4. Pattern Scanner (synthetic memory)

**test_e2e_pattern_scanner_synthetic:**
- Konstruiert synthetische pile-list bytes
- `parse_pile_list_from_bytes` und `parse_piles_from_bytes` ohne Crash

### 5. IL2CPP Scanner (mock memory)

**test_e2e_il2cpp_mock_memory:**
- `MockMemory` + `Il2CppReader` Konstruktion
- `scan_decks_il2cpp` auf leerem Mock → graceful handling

### 6. write_deck_artifacts Round-Trip

**test_e2e_write_deck_artifacts:**
- 2 synthetische Decks → `write_deck_artifacts`
- Validiert: `decks-container.json` (schema, 2 decks)
- Validiert: `deck-e2e-art-1.json` (schema, name, cards)

### 7. merge_deck_results Dedup

**test_e2e_merge_deck_results_dedup:**
- 3 Deck-Ergebnisse (2 mit gleicher deckId) → merge
- Validiert: 2 unique Decks nach dedup

### 8-10. CLI deck-scan Subcommand

**test_cli_deck_scan_help:** --help zeigt alle Optionen
**test_cli_deck_scan_invalid_method:** invalid → argparse error (exit 2)
**test_cli_deck_scan_no_mtga_graceful:** kein MTGA → graceful error (exit 1)

---

## sudo-Test auf MacBook

MTGA läuft nicht auf dem MacBook (kein Spiel installiert). Der sudo-Test
konnte nicht gegen einen echten MTGA-Prozess durchgeführt werden.

**Was getestet wurde:**
- `deck-scan --method auto` ohne MTGA → graceful failure (exit 1)
- Fehlermeldung verweist auf `sudo python3 ...` Hinweis
- CLI versucht 3 Prozessnamen: MTGA, MTGALauncher, MTGArena

**Was nicht getestet werden konnte:**
- Echte Memory-Reads gegen MTGA-Prozess
- IL2CPP-Navigation gegen echte TypeInfoTable
- Pattern-Scan gegen echte Anker-GrpIDs im Speicher

**Empfehlung:** sudo-Test gegen echtes MTGA ist ein separater manueller
Validierungsschritt, der nicht automatisiert werden kann.

---

## Validierung gegen echte Decks

Da kein MTGA installiert ist, wurden synthetische Decks verwendet:

1. **Red Aggro (synthetisch, IL2CPP-Format):**
   - 5 Mainboard-Karten (Lightning Bolt, Swiftspear, Goblin Guide, Lava Spike, Rift Bolt)
   - 2 Sideboard-Karten (Smash to Smithereens, Searing Blood)
   - Completion Score berechnet, LLM-Prompt enthält alle Namen

2. **Blue Control (synthetisch, Pattern-Format):**
   - 2 Mainboard-Karten (Counterspell, Opt)
   - Server liefert korrekt via API

3. **Realistic Arena Export:**
   - 10 unique Mainboard, 3 Sideboard
   - Import → Completion Advisor → 4+ missing cards detected

---

## Geänderte Dateien

| Datei | Änderung |
|-------|----------|
| `advisor/deck_import.py` | Same-name reprint resolution (statt ambiguous skip) |
| `advisor/tests/test_advisor.py` | Test angepasst an reprint-resolution |
| `advisor/tests/test_e2e_validation.py` | Neu: 10 E2E-Validation-Tests |
| `test_cli_verification.py` | Repariert: Container-Flow + assert statt return |
| `hermes_verify.py` | Sanitize-Assertion korrigiert |

---

## Fazit

Alle T4-Integrationen sind funktional validiert:
- CLI `deck-scan` Subcommand: --help, error handling, graceful failure ✓
- Server `/api/decks-container` und `/api/deck/{id}` Endpoints ✓
- Completion Advisor mit deck.v1 Format ✓
- LLM Advisor mit Karten im Prompt ✓
- Pattern Scanner mit synthetischen Daten ✓
- IL2CPP Scanner mit Mock Memory ✓
- write_deck_artifacts Round-Trip ✓
- Path-Traversal-Sicherheit ✓
- Prompt-Truncation (max 60 cards) ✓

**178/178 Tests bestanden. Ready für manuelle sudo-Validierung gegen echtes MTGA.**