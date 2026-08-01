# Roadmap (v4 — Stand 2026-08-01)

Merged: Roadmap v3 + hermes-ornith-plan. Beide Dokumente sind jetzt eins.

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
- CLI: `python3 -m cli.main run`
- Menubar-App exportiert Decks mit

## ✅ Phase 2: LLM Advisor — Done
- **Hybrider Ansatz:**
  - Collection + Decks: deterministisch (Phase 1/1.2)
  - Advisor: LLM-gestützt (lokal, kein externer Service)
- **LLM Advisor (`advisor llm`):**
  - Wildcard-Crafting-Prioritäten
  - Deck-Optimierung: Mana-Kurve, Synergien, Sideboard
  - Meta-Relevanz
  - Konfigurierbar: Config-Datei, Env-Vars, CLI-Args
- **Dashboard:**
  - Decks-Tabelle mit Select-Buttons
  - Interaktiver Chat: Deck wählen → Frage stellen → LLM antwortet
  - LLM-Config-UI: Endpoint, Modell, Temperature, Max Tokens editierbar
  - API: `/api/chat` (POST), `/api/config` (GET/POST)

## ✅ Phase 2.1: LLM Konfiguration — Done
- `advisor/llm_config.py`: LLMConfig-Dataclass
- Config-Ladereihenfolge: Defaults → Config-Datei → Env → CLI
- Config-Datei: `~/.config/mtga-advisor/config.json` oder `mtga-advisor.json`
- Umgebungsvariablen: `MTGA_LLM_ENDPOINT`, `MTGA_LLM_TEMPERATURE`, etc.
- CLI: `--endpoint`, `--model`, `--temperature`, `--max-tokens`, `--config`
- Befehl: `advisor init-config` erzeugt Default-Config
- Beispiel-Config: `mtga-advisor.example.json` (Tailscale-IP 100.95.116.78)

---

## 🟡 Phase 3: Deck-Summaries & Namensauflösung — In Arbeit (Kanban)

Kanban-Tasks auf Board `mtga-advisor`, assignee: `perseus-ornith`.

### T1: Deck-Summaries stabilisieren — Running
- StartHook-Parsing weiter absichern
- Exportdiagnostik für fehlende Decks ergänzen
- GUI und CLI auf `decks.json` als Summary-Quelle vereinheitlichen
- **Ergebnis:** Alle bekannten Decks sichtbar, fehlende Logdaten markiert

### T2: Englische Kartennamen standardisieren — Running
- Lokale MTGA-DB als Primärquelle für Namen verwenden
- `Localizations_enUS` und Legacy-Fallbacks validieren
- Bei Mehrdeutigkeiten konservativ abbrechen statt raten
- Testfälle mit aktuellen MTGA-Schema-Varianten ergänzen
- **Ergebnis:** `arena_deck.json` mit stabilen englischen Namen, Diagnostics bei Unbekannt

### T3: Vollständigen Deck-Export definieren — Todo (wartet auf T1+T2)
- Quelle für vollständige Decklisten festlegen
- Exportformat dokumentieren
- Zielartefakt: pro Deck eine Datei oder Containerformat
- Import/Export-Pfade nicht vermischen
- **Ergebnis:** Echte Decklisten (nicht nur Summaries), reproduzierbar, diff-freundlich

### T4: Ornith über Tailscale anschließen — Ready
- Endpoint-Konfiguration: `mtga-advisor.json`, Env, CLI ✅
- Host auf `0.0.0.0` gebunden ✅
- LaunchAgent repariert (`LimitLoadToSessionType` hinzugefügt) ✅
- **Offen:** macOS Firewall freigeben für Port 8081 auf Tailscale-Interface
- **Offen:** `curl`-Validierung vom MacBook aus
- **Ergebnis:** `advisor llm` läuft gegen ornith über Tailscale

### T5: Hermes-Workflow bauen — Todo (wartet auf T1+T3+T4)
- Reihenfolge: Scan → Deck-Export → `advisor complete` → optional `advisor llm` → Dashboard
- Statusanzeige: Collection? Decks? Advisor? LLM erreichbar?
- Nur lesende GUI, keine Doppelberechnung
- **Ergebnis:** Verständlicher Arbeitsfluss statt verstreuter Einzelschritte

**Dependency-Graph:**
```
T1 (Summaries) ──┐
                  ├─→ T3 (Deck-Export) ──┐
T2 (Namen) ──────┘                       ├─→ T5 (Workflow)
T4 (Tailscale) ──────────────────────────┘
```

---

## ⏸️ Phase 4: Meta Signals — Pausiert
- LLM deckt Meta-Relevanz bereits in Phase 2 ab
- Kann wieder aktiviert werden wenn RAG-Architektur steht (Phase 6)

## 🔮 Strategische Gabelung (frühestens Phase 5)

### Weg A: LLDB/Logs weiterentwickeln (aktuell)
- Python-Stack (LLDB + Log-Parsing + Merge)
- Vorteil: Läuft jetzt, kein Rust nötig
- Nachteil: Zwei getrennte Pfade (Memory + Logs), kein IL2CPP

### Weg B: mtga-reader einbinden (optional)
- Rust-Binary via Subprozess aus Python anrufen
- Liefert Collection + Decks + Inventory + Ranks + Account aus einem Guss
- macOS/IL2CPP-Reader müssten implementiert werden
- Vorteil: Ein System, alles aus Memory, schneller
- Nachteil: Rust-Build-Pipeline, sudo nötig

**Entscheidung:** Weg A bleibt erstmal. Weg B ist eine Option bei IL2CPP-Grenzen.

---

## Phase 5: Service Layer + MCP
- Remote-API für Multi-Client
- MCP als optionales Interface (Hermes nativ)
- Offline-Modus bleibt first-class

## Phase 6: LLM Enhancements
- Prompt-Tuning: System-Prompt für freie Chat-Antworten (aktuell JSON-only)
- RAG über aktuelle Meta-Daten (MTGGoldfish, Untapped)
- Collection-History: "Was hat sich seit letztem Sync geändert?"
- Multi-Model-Support: ornith, Codex, Cloud-API parallel wählbar

---

## Offene Punkte (aus hermes-ornith-plan)

- [ ] **Hermes-Integration klären:** Dashboard ist eigenständig (http.server). MCP-Interface für Hermes-native Steuerung?
- [ ] **Decklisten-Quelle:** Logs, Export aus MTGA, manueller Import, oder kombiniert?
- [ ] **ornith Rolle:** Nur Advisor-Text oder auch Decklisten-Zusammenfassung?
- [ ] **pymem-osx:** Auf MacBook installieren (statt Windows-pymem)
- [ ] **Firewall:** macOS Firewall Port 8081 für Tailscale freigeben

## Erfolgskriterien

- [x] `decks.json` ist verfügbar und in der UI sichtbar
- [ ] Mindestens ein vollständiges Deck mit englischen Namen sauber exportiert
- [ ] `advisor llm` erreicht `ornith` über Tailscale
- [x] Dashboard zeigt Collection, Decks und Advisor-Status
- [ ] Fehler sind als Diagnostics sichtbar und nicht still versteckt
- [x] LLM ist konfigurierbar (Config-Datei, Env, CLI, GUI)
- [x] Interaktiver Chat: Deck wählen → Frage stellen → LLM antwortet

## Nächste Implementierungsreihenfolge

1. **Firewall freigeben** (du machst auf Mac Studio)
2. **pymem-osx installieren** auf MacBook (du machst)
3. **T1-T5 Kanban-Tasks** fertigstellen (perseus-ornith läuft)
4. **Prompt-Tuning:** System-Prompt für Chat freie Antworten erlauben (nicht nur JSON)
5. **Doku:** README und Roadmap referenzieren