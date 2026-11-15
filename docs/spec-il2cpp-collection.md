# Spec: Familie 3 — Collection über IL2CPP-Feldpfad lesen

**Status:** Spezifikation, freigegeben 2026-11-15
**Ziel:** Die Karten-Sammlung (grpId → Menge) direkt aus dem laufenden MTGA-Prozess lesen —
metadata-getrieben über die Klassen-/Feldinformationen von IL2CPP, ohne Anker-Raterei und ohne
Katalog-Abdeckung als Erfolgsbedingung.

---

## 1. Warum Familie 3

Drei Lösungsfamilien wurden geprüft:

1. **Log-Parser** — liefert nur Währungen (`InventoryInfo`), DeckSummaries/Decks und Match-Daten.
   Die Owned-Liste wird seit Jahren nicht mehr geloggt. Einstimmig über sechs aktiv gepflegte
   Repos bestätigt. **Endgültig negativ.**
2. **Anker-/Stride-Scan** (unser aktueller `scan`-Pfad) — braucht einen Katalog, dessen Abdeckung
   für `known_ratio >= 0.5` reicht. Unser `arena_id_lookup.json` deckt 5,3 % ab ⇒ jeder Block
   fällt durch den Filter. Nur als Fallback brauchbar, und dann mit `Raw_CardDatabase_*.mtga`.
3. **IL2CPP-Feldpfad** — die Collection ist ein Objekt am Ende einer Pointer-Kette:
   `WrapperController.Instance → InventoryManager → InventoryServiceWrapper → Cards (<Cards>k__BackingField)
   → CardsAndQuantity.entries` (eine `Dictionary<int,int>`-Eintragstabelle).
   Stride und Feld-Offsets kommen zur Laufzeit aus der Metadaten-Klasse `Entry`, nicht aus Konstanten.
   **Das ist der Primärweg.** Unabhängig von Katalogqualität, ~10k Karten in einer Handvoll Reads.

## 2. Ist-Zustand im Repo (wiederverwenden, nicht neu bauen)

`scanner/il2cpp_nav.py` (2.155 Zeilen) kann bereits:

| Baustein | Status |
|---|---|
| `Il2CppReader`, `MemoryReader`-Protocol, `_read_exact`/`_read_ptr`/`_read_u32`/`_read_i32` | vorhanden |
| `find_class_by_name` (TypeInfoTable) | vorhanden, auf macOS unzuverlässig (Klassen liegen im Heap) |
| `get_class_fields` → `FieldInfo(name, type_name, offset, is_static)` | vorhanden, **ohne** Vererbungskette und **ohne** `type_code` |
| `read_dictionary_uint_ptr(dict, entry_stride=24, value_offset=0x10)` | vorhanden, **Stride hart kodiert** — für `Dictionary<int,int>` falsch |
| `discover_wrapper_controller_via_backref()` → `(instance, class)` | vorhanden, validiert über `PlayerRankServiceWrapper` |
| `_bulk_read(mem, base, size, chunk_size=4 MiB)` | vorhanden |
| `_enum_writable_private_regions*`, `_find_metadata_regions_via_proc` | vorhanden |
| Deck-Pfad: `navigate_to_all_decks`, `read_deck`, `scan_decks_il2cpp` | vorhanden und **funktioniert** |
| Collection-Pfad | **fehlt** |

`IL2CPP_OFFSETS["class_static_fields"] = 0xA8` ist **falsch**. Bei IL2CPP v31 liegt `static_fields`
bei **0xB8**; `0xA8` ist `implementedInterfaces`. Ein Read dort liefert einen plausiblen Pointer und
danach stumm Müll — exakt unser bisheriges Fehlerbild (plausible Zahlen, kein Treffer). Wird in
Aufgabe T1 korrigiert, auch wenn der Collection-Pfad ohne statische Felder auskommt.

## 3. Algorithmus

Referenz-Implementierung (nur lesen, **nicht kopieren** — GPL-3):
`mtgatool/mtga-reader`, `src/queries_il2cpp.rs:318-394` (Pfad) und
`src/il2cpp/macos_runtime.rs:986-1092` (`array_info`, `entry_layout`, `dict_pairs_bulk`).

### 3.1 Pfad

```
wrapper_instance                                   # discover_wrapper_controller_via_backref()
  + find_field_deep(wrapper_class, "<InventoryManager>k__BackingField")
  → inventory_manager
      + find_field_deep(class_of(inventory_manager), "InventoryServiceWrapper")
        ODER "_inventoryServiceWrapper"            # ältere Builds
  → service_wrapper
      + find_field_deep(class_of(service_wrapper), "<Cards>k__BackingField")
  → cards_obj                                      # Klasse CardsAndQuantity
      + find_field_deep(class_of(cards_obj), "entries") oder "_entries"
  → entries_array
```

`find_field_deep` **muss** die Vererbungskette laufen (`class_parent`, max. 32 Schritte): sowohl
`InventoryServiceWrapper` als auch `<Cards>k__BackingField` sind auf Basisklassen deklariert
(konkret `AwsInventoryServiceWrapper`). Ohne Kette bricht der Pfad genau hier ab.

Jeder Schritt: Feld lesen → Pointer auf Plausibilität prüfen (`>= MIN_VALID_PTR`) → auf 8-Byte-Alignment
prüfen → erst dann weiter. Bei Bruch: **Abbruch mit Schrittnamen**, kein Weiterraten.

### 3.2 Objekt- und Klassen-Offsets (IL2CPP v31, macOS/arm64)

| Was | Offset | Anmerkung |
|---|---|---|
| Objekt-Header (Klassenzeiger) | `+0x00` | `class_of(obj) = read_ptr(obj)` |
| `Il2CppClass.name` | `+0x10` | `char*` |
| `Il2CppClass.element_class` | `+0x40` | **neu** — Elementtyp eines Arrays |
| `Il2CppClass.parent` | `+0x48` | Vererbungskette |
| `Il2CppClass.fields` | `+0x80` | `FieldInfo*` |
| `Il2CppClass.instance_size` | `+0xF8` | u32 |
| `Il2CppClass.static_fields` | `+0xB8` | **Korrektur** (vorher 0xA8) |
| `Il2CppClass.field_count` | `+0x124` | u32, nur Plausibilitätsgrenze |
| `FieldInfo.name` | `+0x00` | `char*` |
| `FieldInfo.type` | `+0x08` | `Il2CppType*` |
| `FieldInfo.parent` | `+0x10` | `Il2CppClass*` — **Abbruchkriterium** der Feldliste |
| `FieldInfo.offset` | `+0x18` | i32; `-1` = thread-static |
| `Il2CppType` Attrs/Code | `type_ptr + 0x08` | u32: `attrs = bits & 0xFFFF`, `type_code = (bits >> 16) & 0xFF` |
| `Il2CppArray.length` | `+0x18` | i32 |
| `Il2CppArray.data` | `+0x20` | erste Elementadresse |
| Objekt-Header-Größe | `0x10` | Feld-Offsets sind **mit** Header; in Arrays **abzüglich** |
| `FIELD_ATTR_STATIC` | `0x10` | |
| `FIELD_ATTR_LITERAL` | `0x40` | const ⇒ kein Speicher |

Feldliste lesen: `fields`-Zeiger, dann pro Eintrag `FieldInfo.parent == klasse` prüfen — sobald die
Bedingung bricht, ist die Liste dieser Klasse zu Ende. Die bisherige Variante (Abbruch nur bei
ungültigem Namen, `field_count`-Fallback 50) läuft in die Felder der Folgeklasse und produziert
Geisterfelder.

### 3.3 Eintrags-Layout (der entscheidende Teil)

```python
elem_class, stride, length, data = array_info(entries_array)
#   elem_class = class_element_class(class_of(entries_array))      # +0x40
#   stride     = array_element_size(class_of(entries_array), elem_class)
#              = instance_size(elem_class) - 0x10                   (Value-Type, unboxed)
#              Fallback: Feld element_size an 0xF8/0xFC/0x100/0x108/0x110 der Array-Klasse,
#              dann 8 Byte
#   length     = i32(entries_array + 0x18)
#   data       = entries_array + 0x20

layout = entry_layout(elem_class)
#   offset(name) = FieldInfo.offset(name) - 0x10     für hashCode / key / value
#   code(name)   = Il2CppType.type_code(name)        für key / value
```

Dekodierung (Bulk-Read, ein Syscall für den ganzen Block):

```
count = min(i32(cards_obj + 0x20), length)      # _count/_Count = belegte Obergrenze
blob  = read_bytes(data, count * stride)        # in 4-MiB-Stücken (MAX_READ_SIZE 16 MiB)
für i in 0..count:
    base = i * stride
    hash = i32(blob[base + layout.hash])
    wenn hash < 0: continue                     # freier Slot
    key   = decode(blob[base + layout.key],   layout.key_code)     # grpId
    value = decode(blob[base + layout.value], layout.value_code)   # Menge
    wenn key > 0: cards[key] = value
```

`decode` nach `type_code`: `I4`/`VALUETYPE` → i32, `U4` → u32, `I8`/`I` → i64, `U8`/`U` → u64,
`I2`/`U2`/`CHAR` → 16-bit, `I1`/`U1`/`BOOLEAN` → 8-bit, `R4`/`R8` → float, sonst i32.
Für `Dictionary<int,int>` ist mit `stride = 16`, `hash@0`, `key@4`, `value@8` zu rechnen; bei älteren
Layouts mit 24/32 Byte Stride entsprechend der Metadaten. **Nichts davon hart kodieren.**

### 3.4 Diagnose-Ausgabe (Pflicht bei Fehlschlag)

Der Pfad muss im Fehlerfall seine Stationen melden, damit ein Fehlschlag von „keine Collection"
unterscheidbar ist:

```
wrapper_instance = 0x...
  <InventoryManager>k__BackingField  @ +0x1B8 → 0x...
  InventoryServiceWrapper            @ +0x2C0 → 0x...
  <Cards>k__BackingField             @ +0x0F8 → 0x...
  entries                            @ +0x18  → 0x... (Array, len=..., stride=16)
  count=..., gelesen=..., freie Slots=...
```

## 4. Ergebnis-Datenmodell

`CollectionIl2CppResult` (Dataclass, `scanner/collection_il2cpp.py`):

| Feld | Typ | Bedeutung |
|---|---|---|
| `cards` | `dict[int, int]` | grpId → Menge |
| `unique_cards` | int | Anzahl Schlüssel |
| `total_copies` | int | Summe der Mengen |
| `playset_copies` | int | Summe `min(qty, 4)` |
| `path` | `list[str]` | Diagnose-Trail aus 3.4 |
| `warnings` | `list[str]` | z. B. „count=0", „Feld nicht gefunden: …" |
| `elapsed_ms` | int | Laufzeit |

**Kein stilles Null-Ergebnis.** Findet die Navigation nicht statt, ist `cards` leer **und**
`warnings` erklärt, an welcher Station es brach — dieselbe Regel, die `scan_decks_il2cpp` mit
`NavigationReadError` schon durchsetzt. Ein leeres Dict ohne Warnung ist ein Bug.

## 5. Validierung (fail-closed)

Vor dem Schreiben von Artefakten:

1. **Anker-Pflicht:** Alle übergebenen Anker (grpId, Menge) müssen in `cards` stehen und
   `cards[grp] >= anker_menge` erfüllen. Fehlt einer ⇒ kein Erfolg, Warnung mit Namen/ID.
   Die 29-Zeilen-Deckliste ist der Standard-Anker (`TVA = 1`, `We Say Thee Nay! = 3`).
2. **Band:** `5.000 <= unique_cards <= 12.000`. Außerhalb ⇒ Fehlschlag (nicht „trotzdem speichern").
3. **Plausibilität:** `total_copies >= unique_cards`, `max(qty) <= 400`, `min(grpId) >= 1000`,
   `max(grpId) <= 900.000`.
4. **Kennzahlen schreiben:** `unique_cards`, `total_copies`, `playset_copies` (R8 bleibt verbindlich).
   `total_copies == playset_copies` ist ein Warnsignal, kein Beweis für Kappung.
5. Referenzwerte für die Plausibilitätsprüfung: Untapped.gg Stand 2026-11-14 = **7.803 unique**,
   Snapshot 2026-08-02 = **7.874 unique / 16.280 total**. Abweichung > 25 % ⇒ Warnung im Report.

## 6. Aufgaben

### T1 — Reader + Tests (kein CLI)

Neue/geänderte Dateien:

- `scanner/il2cpp_nav.py` erweitern:
  - `class_of`, `class_parent`, `class_element_class`, `class_instance_size`, `class_static_storage`
  - `class_fields_raw(klass)` — Feldliste mit `FieldInfo.parent`-Abbruch, `type_code`, `is_static`,
    `literal`-Skip; `FieldInfo` um `type_code: int = 0` und `is_thread_static: bool = False` erweitern
  - `find_field_deep(klass, name)` — Vererbungskette, max. 32
  - `read_ref_field(obj, name)`, `read_num_field(obj, name)`
  - `array_info(array)`, `array_element_size(arr_class, elem_class)`, `entry_layout(elem_class)`
  - `read_dict_int_int_bulk(dict_addr)` → `(dict[int, int], dict)` mit Layout im zweiten Rückgabewert
  - `IL2CPP_OFFSETS["class_static_fields"] = 0xB8` + `class_element_class = 0x40`
- `scanner/collection_il2cpp.py`: `CollectionIl2CppResult`, `read_collection(reader, wrapper_instance=0, wrapper_class=0, debug=False)`,
  `scan_collection_il2cpp(adapter, ...)` mit `NavigationReadError`-Fang, `find_collection_anchors`-freundliche
  Hilfsfunktion zum Vergleich gegen `DEFAULT_ANCHORS`.
- `scanner/tests/test_collection_il2cpp.py` gegen `MockMemory`:
  - synthetischer Objektgraph (Wrapper → InventoryManager → Wrapper-Klasse mit Vererbung → Cards → entries)
  - **Stride-Varianten** 16 / 24 / 32, freie Slots (`hash = -1`), `count < length`
  - `key`/`value` als `I4` und als `U4`
  - Feld auf Basisklasse deklariert (Vererbung) — ohne Kette muss der Test fehlschlagen
  - Regression: `static_fields = 0xA8` darf **nicht** als Storage verwendet werden
  - leere Collection ⇒ `cards == {}` **und** `warnings` nicht leer

Definition of Done T1: `PYTHONPATH=. /opt/homebrew/bin/pytest -q scanner/tests/test_collection_il2cpp.py scanner/tests/test_il2cpp_nav.py` grün, keine Regression in der Gesamtsuite (`pytest -q`), Deck-Scan unverändert lauffähig.

### T2 — CLI, Integration, Live-Validierung (hängt an T1)

- `cli/main.py`: `scan` bekommt `--method {anchor,il2cpp,auto}` (Default `auto`: erst IL2CPP, bei
  Fehlschlag Anker-Scan) und `--anchors` (nargs, optional). IL2CPP-Zweig nutzt denselben
  Attach-Pfad wie `_run_deck_scan` (`PersistentRemoteMemoryAdapter` / `_find_mtga_pid`).
- Artefakt-Schreiben über `memory_scanner.write_collection_artifacts`, damit Schema und
  `history/`-Format identisch bleiben; `method`/`path`/`warnings` zusätzlich ins JSON.
- `scanner/combined_scanner.py`: Collection-Zweig kann `il2cpp` nutzen (`scan_all(method=...)`).
- Live-Lauf gegen laufendes MTGA als uid 502 über den Helper-Socket:
  `python -m cli.main scan --method il2cpp --non-interactive --output out-memory`
  ⇒ `collection.json` mit Anker-Prüfung, Band-Prüfung, Kennzahlen.
- Report `docs/t5-il2cpp-collection-validation.md`: gefundene unique/total/playset, Anker-Status,
  Laufzeit, Vergleich zu Untapped (7.803) und Snapshot (7.874/16.280), offene Abweichungen.

Definition of Done T2: Live-Lauf produziert ein `collection.json`, in dem **alle** 29 Anker mit
mindestens ihrer Deckmenge stehen und `unique_cards` im Band liegt; Tests grün; Report committed.

## 7. Risiken & Leitplanken

| Risiko | Umgang |
|---|---|
| MTGA-Build weicht ab (Feldnamen, Offsets) | Diagnose-Trail (3.4) zeigt die Station; Fallback-Feldnamen `_inventoryServiceWrapper`, `_entries`, `_count`; `get_class_fields` dynamisch |
| `get_class_fields` läuft in Nachbar-Klasse | `FieldInfo.parent`-Abbruchkriterium ist Pflicht, nicht optional |
| Wert-Typ-Offsets um Header verschoben | `offset - 0x10` nur für Array-Elemente; für Instanzfelder roh verwenden |
| Bulk-Read zu groß | `min(count, MAX_DICT_ENTRIES)`, 4-MiB-Stücke, Helper-Limit 16 MiB |
| ASLR / Prozess-Neustart | Klassen und Instanzen pro Lauf neu auflösen, kein Caching über Prozesse hinweg |
| GPL-Kontamination | Referenz nur lesen; Algorithmus nachbauen, kein Code/Text aus `mtga-reader` übernehmen |
| Helper nicht installiert / falsche uid | Vor dem Lauf `helper_client.ping()`; Fehlermeldung statt Traceback |
| Client nicht im Collection-Screen | Der Feldpfad braucht offene Sammlung/Deckbuilder in der Regel nicht, da `InventoryManager` beim Login gefüllt wird — falls `Cards` leer ist: Nutzer bitten, die Sammlung zu öffnen, Lauf wiederholen |

## 8. Nicht Teil dieser Spec

- Familie 2 aufräumen (Katalog aus `Raw_CardDatabase_*.mtga`) — separater Task.
- Automatischer Helper-Selbstupdate / Autonomie-Loop.
- Log-Spiegelung per LaunchAgent.
- Änderungen an Ranking-, Deck- oder Advisor-Funktionen.
