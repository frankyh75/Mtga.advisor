# Roadmap (v2 — Stand 2026-07-30)

**Neue Erkenntnisse aus mtgatool-Repos:**
- `mtga-reader` (Rust) liest Mono/IL2CPP-Speicher direkt — Collection + Decks + Inventory + Ranks + Account in einem Rutsch
- `StartHook` im Log enthält `DeckSummaries[]` — Decks ohne Memory-Scan
- Navigationspfade für alle Datenstrukturen sind dokumentiert (`queries.rs`)

---

## ✅ Phase 0: Foundations — Done
- Repo-Struktur, Docs, Data-Schemas

## ✅ Phase 1: Collection Export — Done
- Memory-Scan (LLDB) + Log-Parsing + Merge läuft auf MacBook
- 7.873 Karten, 16.277 total, valid ✅
- Menubar-App (rumps) für manuellen Sync

## 🟡 Phase 1.1: Card Metadata — Fast done
- 181 unbekannte IDs → `Raw_CardDatabase_*.mtga` als Lösung eingebaut
- Nächster Sync sollte sie auflösen

## 🟢 Phase 1.2: Deck Export — Neu
- **StartHook aus Logs parsen** → `DeckSummaries[]` mit Namen, IDs, Formaten
- Kein neuer Memory-Scan nötig
- Output: `decks.json` (parallel zu `collection.json`)
- Grundlage für Advisor (Phase 2)

## 🟡 Phase 2: Rule-Based Advisor — In Arbeit
- CLI-MVP existiert
- **Jetzt mit echten Deck-Daten** aus Phase 1.2
- Wildcard-Optimierung: "Was craften für Deck X?"
- Dashboard als GUI-Surface

## ⏸️ Phase 3: Meta Signals — Pausiert
- Kein externer Service nötig
- Kann warten bis Advisor mit echten Daten läuft

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

**Entscheidung:** Weg A bleibt erstmal. Weg B ist eine Option, wenn wir an IL2CPP-Grenzen stossen oder Decks aus Memory brauchen (StartHook reicht fürs erste).

## Phase 4: Service Layer + MCP
- Remote-API für Multi-Client
- MCP als optionales Interface
- Offline-Modus bleibt first-class

## Phase 5: LLM Enhancements
- LLM-gestützte Erklärungen (opt-in)
- Regel-basierte Outputs bleiben autoritativ
