# Roadmap (v3 — Stand 2026-07-31)

**Wichtige Änderung:** Phase 2 wird hybrid — Collection-Export bleibt deterministisch,
Advisor wird LLM-gestützt (lokal: ornith:35b / qwen3.5:9b).

---

## ✅ Phase 0: Foundations — Done
- Repo-Struktur, Docs, Data-Schemas

## ✅ Phase 1: Collection Export — Done
- Memory-Scan (LLDB) + Log-Parsing + Merge läuft auf MacBook
- 7.873 Karten, 16.277 total, valid ✅
- Menubar-App (rumps) für manuellen Sync

## ✅ Phase 1.1: Card Metadata — Done
- 181 unbekannte IDs via `Raw_CardDatabase_*.mtga` aufgelöst

## ✅ Phase 1.2: Deck Export — Done
- StartHook aus Logs parsen → `decks.json` mit Namen, IDs, Formaten
- CLI: `mtga-export decks`
- Menubar-App exportiert Decks mit

## 🟢 Phase 2: LLM Advisor — Neu (heute deployed)
- **Hybrider Ansatz:**
  - Collection + Decks: deterministisch (Phase 1/1.2)
  - Advisor: LLM-gestützt (lokal, kein externer Service)
- **LLM Advisor (`advisor llm`):**
  - Wildcard-Crafting-Prioritäten: "Was zuerst craften?"
  - Deck-Optimierung: Mana-Kurve, Synergien, Sideboard
  - Meta-Relevanz: Welche Karten werden aktuell gespielt
  - Nutzt ornith:35b (Port 8081) oder fallback qwen3.5:9b (Port 8080)
- **Dashboard erweitert:**
  - Decks-Tabelle (aus decks.json)
  - LLM-Advisor-Ergebnisse (Crafting Priorities, Optimizations, Meta Notes)
  - API-Endpoints: `/api/decks`, `/api/advisor-result`
- **CLI:** `python -m cli.main advisor llm`

## ⏸️ Phase 3: Meta Signals — Pausiert
- Kann warten — LLM deckt Meta-Relevanz bereits ab

## 🔮 Strategische Gabelung (frühestens Phase 4)

Zwei Wege für die Zukunft:

### Weg A: LLDB/Logs weiterentwickeln (aktuell)
- Unser Python-Stack (LLDB + Log-Parsing + Merge)
- Vorteil: Läuft jetzt, kein Rust nötig
- Nachteil: Zwei getrennte Pfade (Memory + Logs), kein IL2CPP

### Weg B: mtga-reader einbinden (optional)
- Rust-Binary via Subprozess aus Python anrufen
- Liefert Collection + Decks + Inventory + Ranks + Account aus einem Guss
- macOS/IL2CPP-Reader müssten implementiert werden (Doku existiert)
- Vorteil: Ein System, alles aus Memory, schneller
- Nachteil: Rust-Build-Pipeline, sudo nötig

**Entscheidung:** Weg A bleibt erstmal. Weg B ist eine Option, wenn wir an IL2CPP-Grenzen stossen.

## Phase 4: Service Layer + MCP
- Remote-API für Multi-Client
- MCP als optionales Interface
- Offline-Modus bleibt first-class

## Phase 5: LLM Enhancements
- Prompt-Tuning für bessere Meta-Analyse
- Optional: RAG über aktuelle Meta-Daten (MTGGoldfish, Untapped)
- Collection-History: "Was hat sich seit letztem Sync geändert?"
