# Codex CLI Prompt — Builder Mode (Full Context)

Du bist Codex CLI im Repo. Verwende **builder-mode** (siehe Skill in `$CODEX_HOME/skills/builder-mode`).
Ziel: Implementiere die Phase‑2/2.5 Arbeitspakete **gemäß der bestehenden Planung**. Kein ausführlicher Plan, sondern Umsetzung.

## Quelle der Planung (muss beachtet werden)
- `docs/deck-analysis-roadmap.md` (Roadmap + Example Output)
- `docs/ai-context.md` (Normativer Kontext, offline-first)
- `docs/data-schemas.md` (Schema-Formen)
- `AGENTS.md` (Repo‑Regeln, Tests, Scope)

## Zielzustand (Phase 2 + 2.5)
- Deckanalyse für **Last Played** Decks
- Ausgabe **Englisch** (card names + reasons)
- Offline‑first, keine verpflichtenden Netzwerk‑Calls im Kernpfad
- Mapping‑Coverage warnen bei < 90%

## Deliverables (implementieren)
1) **CardDB Ingestion (SQLite, EN)**
   - Scryfall Bulk (Oracle/Default, EN) in lokale SQLite importieren
   - `carddb_meta` mit `language='en'`, `fetched_at`, `bulk_version`
   - Indizes: `oracle_id`, `name`, `scryfall_id`, `arena_id` (falls vorhanden)

2) **Mapping-Layer**
   - `cardId` (MTGA) → `arena_id` → `scryfall_id` → `oracle_id`
   - Fallback: lokale Mapping‑Tabelle `mtga_to_scryfall.csv`
   - Coverage berechnen + `unknownCards` ausgeben

3) **Deck Analysis Export**
   - Neues Artefakt `deck-analysis.json`
   - Struktur wie im Beispiel in `docs/deck-analysis-roadmap.md`
   - `mappingCoverage`, `unknownCards`, `synergies[]`, `recommendations[]`
   - `diagnostics.coverageWarning = (mappingCoverage < 0.90)`

4) **Synergy Rules (10 Regeln)**
   - Deterministisch, mit EN‑Reasons
   - Regeln entsprechen Plan (curve, removal, draw, mana‑base, tribal, artifact, enchantment, interaction/threats, creature/spell balance, top‑heavy curve)

## Tagging Rules (deterministisch, EN)
- removal: “destroy target”, “exile target”, “deals X damage …”
- counterspell: “counter target spell”
- card_draw: “draw a card” / “draw two cards” / “draw X cards”
- ramp: “search your library for a land” / “add {…}”
- token_maker: “create a … token”
- tribal_payoff: “<Type> you control …”
- artifact_payoff: “artifact you control …”
- enchantment_payoff: “enchantment you control …”
- burn: “deals X damage”
- lifegain_payoff: “whenever you gain life”
- graveyard_synergy: “from your graveyard”, “mill”
- sweeper: “destroy all creatures”

## Constraints
- **Offline-first**: kein erzwungener Download in `parse_collection`/`export_collection`.
- CLI‑Commands dürfen optionalen Download anbieten, aber Core‑Flow bleibt offline.
- Tests ausführen, wenn sinnvoll.
- Diffs klein und fokussiert.

## Output (kurz & statusorientiert)
- Was geändert wurde, wo, warum
- Tests/Next steps
