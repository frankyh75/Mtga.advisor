# mtgatool-Repos — Analyse & Vergleich mit Mtga.advisor

**Datum:** 2026-08-01
**Autor:** Jarvis
**Typ:** recherche
**Status:** active
**Tags:** #mtga #mtgatool #deck-parsing #memory-reading #analyse

## Übersicht

Zwei Repos lokal unter `/Users/agent/workspace/mtgatool-forks/`:

| Repo | Sprache | Zweck | Status |
|------|---------|-------|--------|
| `mtga-reader` | Rust | Memory-Reader (NAPI + HTTP) | macOS/IL2CPP unvollständig |
| `mtgatool-desktop` | TypeScript/Electron | Desktop-App (Menubar, Sync, UI) | Aktiv entwickelt |

---

## 1. mtga-reader (Rust) — Memory-Reader

### Architektur

```
src/
├── backend/
│   ├── detection.rs    → Mono vs IL2CPP erkennen
│   ├── traits.rs       → RuntimeBackend + MemoryReader Traits
│   └── mod.rs
├── mono/               → Mono-Backend (Windows, Linux)
│   ├── reader.rs       → MonoReader (vollständig)
│   └── ...
├── il2cpp/             → IL2CPP-Backend (macOS, iOS)
│   ├── reader.rs       → Il2CppBackend (Stubs für macOS!)
│   ├── macos_memory.rs → Mach-API Memory-Reader (funktioniert)
│   ├── offsets.rs      → Unity-Versions-Offsets
│   └── metadata.rs     → global-metadata.dat Parser
├── queries.rs          → **High-Level Queries (das Wichtigste)**
├── napi/mod.rs         → Node.js-Bindings
└── api.rs              → HTTP-Server
```

### Memory-Navigationspfade (aus `queries.rs`)

**Decks:**
```
WrapperController.Instance
  → DecksManager
    → _deckDataProvider
      → _allDecks (Dictionary<Guid, Deck>)
        → _summary (DeckSummary)
          → Name, DeckId, DeckTileId, Description, Attributes
        → _contents
          → Piles (Dictionary<EDeckPile, List<{grpId, qty}>>)
```

**Collection:**
```
WrapperController.Instance
  → InventoryManager
    → InventoryServiceWrapper
      → Cards (Dictionary<uint, int>)  // grpId → quantity
```

**Inventory (Wildcards, Gems, Gold):**
```
WrapperController.Instance
  → InventoryManager
    → InventoryServiceWrapper
      → m_inventory
        → gems, gold, wcCommon, wcUncommon, wcRare, wcMythic, vaultProgress
```

**Ranks:**
```
WrapperController.Instance
  → PlayerRankServiceWrapper
    → _combinedRankInfo
      → constructedClass, limitedClass, level, step, wins, losses
```

### Pile-Typen (EDeckPile)

| Wert | Name | Beschreibung |
|------|------|-------------|
| 0 | Invalid | — |
| 1 | Main | Hauptdeck (60+ Karten) |
| 2 | Sideboard | Sideboard (max 15) |
| 3 | CommandZone | Commander / Oathbreaker |
| 4 | Companions | Companion (z.B. Lurrus) |

### macOS-Status

**Funktioniert:**
- `macos_memory.rs` — `task_for_pid` + `mach_vm_read_overwrite` (braucht sudo/entitlements)
- `find_game_assembly_base()` — findet `GameAssembly.dylib` via `vmmap`
- `find_data_segment()` — findet `__DATA`-Segment via `vmmap`

**Unvollständig/Stubs:**
- `detect_runtime_macos()` — **harter Codepfad**: gibt immer `RuntimeType::Il2Cpp` zurück (TODO-Kommentar)
- `Il2CppBackend::find_class()` — lineares Scannen der TypeInfoTable (langsam, kein Caching)
- `read_static_field()` — vereinfacht, nur primitive Typen
- Keine `queries.rs`-Äquivalente für IL2CPP (die High-Level-Funktionen sind Mono-only)
- `find_data_segment()` für Windows/Linux: `"not implemented"`

**Fazit macOS:** Der Reader **kann** Speicher lesen, aber die IL2CPP-Struktur-Navigation ist nicht fertig. Die `queries.rs`-Funktionen (`decks_from`, `collection_from`, etc.) sind **nur für Mono** implementiert.

---

## 2. mtgatool-desktop (TypeScript/Electron) — Desktop-App

### StartHook-Parsing (`InStartHook.ts`)

```typescript
interface StartHook {
  InventoryInfo: InventoryInfo;     // → Wird extrahiert
  DeckSummaries: DeckSummary[];     // → Wird IGNORIERT!
  Formats: FormatData[];
  CardMetadataInfo: { ... };
}
```

**Wichtig:** mtgatool **ignoriert** die `DeckSummaries` im StartHook komplett! Es extrahiert nur `InventoryInfo` (Gems, Gold, Wildcards) und schickt den Rest per `postChannelMessage` weiter. Die Decks selbst werden später aus dem **Memory** gelesen (via `queries.rs`).

### Deck-Klasse (`deck.ts`)

```typescript
class Deck {
  mainboard: CardsList;     // Karten mit {id, quantity}
  sideboard: CardsList;
  commandZoneGRPIds: number[];
  companionGRPId: number | null;
  name: string;
  id: string;
  lastUpdated: string;
  tile: number;
  format: string;
}
```

**Export-Formate:**
- `getExportTxt()` → `"4 Lightning Strike\r\n2 Shock\r\n\r\n3 Duress\r\n"`
- `getExportArena()` → Gleiches Format, mit MED-Set-Handling
- `getSave()` → `InternalDeck`-Objekt (JSON)

### Typen (`types/deck.ts`)

```typescript
interface InternalDeck {
  id: string;
  name: string;
  mainDeck: CardObject[];       // [{id, quantity}]
  sideboard: CardObject[];
  commandZoneGRPIds?: number[];
  companionGRPId?: number;
  lastUpdated: string;
  deckTileId: number;
  format: string;
  type: "InternalDeck";
}
```

### Card-Database

mtgatool hat eine **lokale SQLite/JSON-Datenbank** (aus Scryfall), die `grpId → Name` auflöst. Wird in `database.card(grpId)` verwendet. Enthält auch Set-Informationen, Reprints, etc.

---

## 3. Vergleich: mtgatool vs. Mtga.advisor

| Aspekt | mtgatool | Mtga.advisor |
|--------|----------|--------------|
| **Deck-Quelle** | Memory (Rust) | StartHook-Logs (Python) |
| **Karten-IDs** | `grpId` (uint) | `DeckTileId` (int) |
| **Karten-Namen** | Lokale DB (Scryfall) | Scryfall-API / SQLite |
| **Deck-Inhalt** | ✅ Vollständig (Main+Side+Command) | ❌ Nur Metadaten (Name, Format) |
| **Collection** | Memory (Dictionary) | StartHook / LLDB |
| **Wildcards** | Memory (m_inventory) | StartHook (InventoryInfo) |
| **Plattform** | Windows (Mono) + macOS (IL2CPP Stubs) | macOS (Logs) |
| **Sprache** | Rust + TypeScript | Python |
| **Geschwindigkeit** | Millisekunden | Sekunden (Log-Parsing) |
| **Sudo nötig** | Ja (task_for_pid) | Nein (Logs) |

### Was mtgatool besser kann

1. **Vollständige Deck-Karten** — Memory liefert `grpId + quantity` pro Pile, StartHook nur Metadaten
2. **Collection in Echtzeit** — Memory-Scan ist schneller als Log-Parsing
3. **Ranks + Account** — Memory hat Zugriff auf Rang-Informationen
4. **Lokale Card-DB** — Keine API-Abhängigkeit, schneller

### Was Mtga.advisor besser kann

1. **Kein Sudo nötig** — Log-Parsing funktioniert ohne root
2. **Einfacherer Code** — Python statt Rust+TypeScript
3. **LLM-Integration** — ornith:35b für Deck-Beratung
4. **Dashboard** — Interaktive GUI mit Deck-Auswahl + Chat
5. **Kein MTGA-Prozess nötig** — Logs reichen, MTGA muss nicht laufen

---

## 4. Learnings für Mtga.advisor

### Was wir übernehmen können

1. **Pile-Struktur** — Main=1, Sideboard=2, CommandZone=3, Companions=4 (für späteren Memory-Reader)
2. **Export-Format** — `"4 Lightning Strike\n\n3 Duress\n"` ist Arena-kompatibel
3. **Lokale Card-DB** — Statt Scryfall-API jedes Mal: SQLite/JSON mit `grpId → Name` (T2 hat das teilweise)
4. **InternalDeck-Schema** — Als Zielformat für vollständige Deck-Exporte

### Was wir nicht brauchen

1. **Memory-Reader** — StartHook reicht für Metadaten, manueller Import für vollständige Decks
2. **Rust-Backend** — Zu komplex für unseren Anwendungsfall
3. **Electron-UI** — Unser Dashboard (Python + HTML) ist schlanker

### Offene Lücke

Der **größte Unterschied**: mtgatool liefert **Kartenlisten pro Deck** (grpId + quantity), wir liefern nur **Metadaten** (Name, Format, Mana). Für den LLM-Advisor brauchen wir aber die Karten. 

**Lösungswege:**
1. **Manueller Import** — Spieler kopiert Deck aus Arena → `deck import` (bereits implementiert)
2. **Memory-Reader für macOS** — pymem-osx (bereits geplant, sudo nötig)
3. **StartHook erweitern** — MTGA sendet `DeckSummaries` ohne Karten, aber evtl. gibt's andere Events mit Karten

---

## 5. Code-Referenzen

| Datei | Inhalt |
|-------|--------|
| `mtga-reader/src/queries.rs` | Memory-Navigationspfade (Deck, Collection, Inventory, Ranks) |
| `mtga-reader/src/il2cpp/macos_memory.rs` | macOS Mach-API Memory-Reader |
| `mtga-reader/src/il2cpp/reader.rs` | IL2CPP-Backend (unvollständig) |
| `mtga-reader/src/backend/detection.rs` | Mono vs IL2CPP Detection |
| `mtgatool-desktop/src/background/onLabel/InStartHook.ts` | StartHook-Parser (ignoriert Decks!) |
| `mtgatool-desktop/src/utils/mtga/deck.ts` | Deck-Klasse mit Export |
| `mtgatool-desktop/src/types/deck.ts` | TypeScript-Typen (InternalDeck, CardObject) |
