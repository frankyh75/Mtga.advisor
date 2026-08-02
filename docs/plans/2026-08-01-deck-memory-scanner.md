# Deck Memory Scanner — Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a pymem-osx-based memory scanner that extracts full card lists (grpId + quantity per pile) from MTGA's `_allDecks` structure in IL2CPP memory — matching what mtgatool's `queries.rs` does for Mono.

**Architecture:** Extend existing `scanner/` module with a new `deck_scanner.py` that uses the same pattern-scanning infrastructure as `memory_scanner.py` but targets deck-specific memory structures. Output format: `InternalDeck`-compatible JSON (mtgatool's schema) per deck.

**Tech Stack:** Python, pymem-osx, ctypes (Mach VM), struct, existing `scanner/` module (pattern_scanner, card_database, macos_paths)

**Reference:** mtgatool `queries.rs` — `decks_from()` function, `InternalDeck` type, `EDeckPile` enum (Main=1, Sideboard=2, CommandZone=3, Companions=4)

---

## Task 1: Research — Memory Layout Analysis

**Objective:** Determine the actual memory layout of `_allDecks` in IL2CPP by analyzing mtgatool's code and existing scanner patterns.

**Files:**
- Read: `mtga-reader/src/queries.rs` (lines 1-200, deck-related queries)
- Read: `mtgatool-desktop/src/utils/mtga/deck.ts` (InternalDeck schema)
- Read: `mtgatool-desktop/src/types/deck.ts` (TypeScript types)
- Read: `scanner/memory_scanner.py` (existing collection scanner pattern)
- Read: `scanner/pattern_scanner.py` (existing pattern scanning infrastructure)
- Create: `docs/deck-memory-layout.md`

**Step 1: Analyze mtgatool's deck memory path**

From `queries.rs`, the Mono path is:
```
WrapperController._instance
  → DecksManager._deckDataProvider
    → _allDecks (Dictionary<uint, Deck>)
      → Deck._contents
        → Piles (Dictionary<EDeckPile, List<CardQuantity>>)
          → CardQuantity: { grpId: uint, quantity: int }
```

For IL2CPP (macOS), the structure is the same but accessed via:
- Static field offsets from `Il2CppClass`
- `s_TypeInfoTable` for class resolution
- `read_static_field()` for singleton access

**Step 2: Document the approach**

Write `docs/deck-memory-layout.md` with:
- Memory path diagram
- Data structure sizes (IL2CPP object layout)
- Two strategies:
  - **Strategy A (Pattern-based):** Like collection scanner — scan for known deck card grpIds, parse surrounding blocks for pile-structured data
  - **Strategy B (IL2CPP navigation):** Find `DecksManager` class via type info, navigate static fields — more robust but needs runtime offsets

**Step 3: Commit**

```bash
git add docs/deck-memory-layout.md
git commit -m "[docs] Deck memory layout analysis — IL2CPP structure, two strategies"
```

---

## Task 2: Strategy A — Pattern-Based Deck Scanner

**Objective:** Implement a pattern-based memory scanner that finds deck card data by scanning for known grpIds and parsing pile-structured blocks.

**Files:**
- Create: `scanner/deck_scanner.py`
- Modify: `scanner/__init__.py` (export new module)

**Step 1: Write failing test**

```python
# scanner/tests/test_deck_scanner.py
def test_parse_pile_block():
    """Test parsing a memory block that contains pile-structured data."""
    # Simulate: Main pile header + 3 cards + Sideboard header + 1 card
    data = _make_pile_block([
        (1, [(12345, 4), (67890, 3), (11111, 2)]),  # Main pile
        (2, [(22222, 1)]),  # Sideboard
    ])
    result = parse_pile_block(data, 0)
    assert result is not None
    assert len(result.piles) == 2
    assert result.piles[1] == {12345: 4, 67890: 3, 11111: 2}
    assert result.piles[2] == {22222: 1}
```

**Step 2: Run test to verify failure**

Run: `python3 -m pytest scanner/tests/test_deck_scanner.py -v`
Expected: FAIL — "module not found"

**Step 3: Write minimal implementation**

```python
"""Deck Memory Scanner — extracts full card lists from MTGA's _allDecks structure."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

# Pile types matching EDeckPile enum
PILE_MAIN = 1
PILE_SIDEBOARD = 2
PILE_COMMAND_ZONE = 3
PILE_COMPANIONS = 4

@dataclass
class DeckMemoryResult:
    """A single deck found in memory with its piles."""
    name: str
    deck_id: str
    piles: dict[int, dict[int, int]]  # pile_type -> {grpId: quantity}
    last_updated: str

@dataclass
class DeckScanResult:
    """Result of a full deck memory scan."""
    decks: list[DeckMemoryResult]
    warnings: list[str]

def parse_pile_block(data: bytes, offset: int) -> DeckMemoryResult | None:
    """Parse a memory block that contains pile-structured deck data.
    
    The expected structure is:
    - Pile header: pile_type (4 bytes), card_count (4 bytes)
    - Card entries: grpId (4 bytes), quantity (4 bytes) × card_count
    - Next pile header or end marker
    
    Returns DeckMemoryResult or None if parsing fails.
    """
    # ... implementation
```

**Step 4: Run test to verify pass**

Run: `python3 -m pytest scanner/tests/test_deck_scanner.py -v`
Expected: PASS

**Step 5: Implement full deck scanner**

Key functions:
- `scan_decks(pm: Pymem, anchors: list[tuple[int, str]]) -> DeckScanResult` — Main entry point
- `find_deck_blocks(pm: Pymem, anchor_grp_ids: list[int]) -> list[bytes]` — Find candidate memory regions
- `parse_deck_block(data: bytes) -> DeckMemoryResult | None` — Parse a single deck from a block
- `merge_deck_results(candidates: list[DeckMemoryResult]) -> list[DeckMemoryResult]` — Deduplicate

**Step 6: Commit**

```bash
git add scanner/deck_scanner.py scanner/__init__.py scanner/tests/test_deck_scanner.py
git commit -m "[feat] Pattern-based deck memory scanner — parse pile-structured blocks"
```

---

## Task 3: Strategy B — IL2CPP Navigation Scanner

**Objective:** Implement a more robust scanner that navigates IL2CPP static fields to find `DecksManager._deckDataProvider._allDecks` directly.

**Files:**
- Modify: `scanner/deck_scanner.py` (add IL2CPP navigation)
- Create: `scanner/il2cpp_nav.py` (IL2CPP structure navigation utilities)

**Step 1: Write failing test**

```python
def test_find_decks_manager_class():
    """Test finding the DecksManager class in IL2CPP type info table."""
    # Mock: type info table with DecksManager entry
    mock_table = _make_mock_type_table(["WrapperController", "DecksManager", "Deck"])
    result = find_class_by_name(mock_table, "DecksManager")
    assert result is not None
    assert result.name == "DecksManager"
```

**Step 2: Run test to verify failure**

Run: `python3 -m pytest scanner/tests/test_il2cpp_nav.py -v`
Expected: FAIL

**Step 3: Write minimal implementation**

```python
def find_class_by_name(pm: Pymem, type_info_table: int, name: str) -> int | None:
    """Find an Il2CppClass by name in the type info table.
    
    Scans the s_TypeInfoTable for a class matching the given name.
    Returns the class pointer or None.
    """
    for i in range(50000):
        class_ptr = pm.read_ulonglong(type_info_table + i * 8)
        if class_ptr == 0:
            continue
        # Read class name from offset
        name_ptr = pm.read_ulonglong(class_ptr + OFFSET_CLASS_NAME)
        if name_ptr == 0:
            continue
        class_name = _read_ascii_string(pm, name_ptr)
        if class_name == name:
            return class_ptr
    return None
```

**Step 4: Run test to verify pass**

Run: `python3 -m pytest scanner/tests/test_il2cpp_nav.py -v`
Expected: PASS

**Step 5: Implement full IL2CPP deck navigation**

Key functions:
- `find_game_assembly_base(pid: int) -> int` — Find GameAssembly.dylib in process
- `find_data_segment(pid: int) -> int` — Find __DATA segment (globals)
- `find_type_info_table(pm: Pymem, data_segment: int) -> int` — Find s_TypeInfoTable
- `navigate_to_all_decks(pm: Pymem) -> int` — Navigate WrapperController → DecksManager → _allDecks
- `read_dictionary_entries(pm: Pymem, dict_ptr: int) -> list[tuple[str, int]]` — Read IL2CPP Dictionary entries
- `read_deck_from_ptr(pm: Pymem, deck_ptr: int) -> DeckMemoryResult` — Read a single Deck object

**Step 6: Commit**

```bash
git add scanner/il2cpp_nav.py scanner/deck_scanner.py
git commit -m "[feat] IL2CPP navigation scanner — find DecksManager via type info table"
```

---

## Task 4: Integration — CLI + Dashboard + LLM Advisor

**Objective:** Wire the deck scanner into the CLI, dashboard, and LLM advisor so full deck lists are available for analysis.

**Files:**
- Modify: `cli/main.py` (add `deck scan` subcommand)
- Modify: `server/app.py` (show deck cards in dashboard)
- Modify: `advisor/llm_advisor.py` (include deck cards in prompt)
- Modify: `advisor/completion.py` (use deck cards for completion advice)

**Step 1: Add CLI subcommand**

```python
# cli/main.py — new subcommand
deck_scan = subparsers.add_parser("deck-scan", help="Scannt Decks aus MTGA-Speicher (pymem-osx).")
deck_scan.add_argument("--output", type=Path, default=Path("out"))
deck_scan.add_argument("--deck-dir", type=Path, default=Path("out/decks"))
```

**Step 2: Update dashboard**

In `server/app.py`, when a deck is selected, show its cards from the memory scan result:
```python
# In deck detail view
deck_cards = deck_memory.get(deck_id, {}).get("piles", {})
mainboard = deck_cards.get(1, {})  # PILE_MAIN
sideboard = deck_cards.get(2, {})  # PILE_SIDEBOARD
```

**Step 3: Update LLM advisor prompt**

In `advisor/llm_advisor.py`, include deck cards in the prompt:
```python
for deck in deck_list[:15]:
    lines.append(f"\n### {name} ({fmt})")
    cards = deck.get("cards", {})
    if cards:
        mainboard = cards.get("mainboard", {})
        sideboard = cards.get("sideboard", {})
        lines.append(f"  - Maindeck: {len(mainboard)} unique cards")
        for grp_id, qty in sorted(mainboard.items(), key=lambda x: -x[1])[:20]:
            card_name = card_db.get(int(grp_id), {}).get("name", f"ID:{grp_id}")
            lines.append(f"    - {qty}x {card_name}")
```

**Step 4: Commit**

```bash
git add cli/main.py server/app.py advisor/llm_advisor.py
git commit -m "[feat] Integrate deck scanner into CLI, dashboard, LLM advisor"
```

---

## Task 5: Testing & Validation on MacBook

**Objective:** Test the deck scanner on the MacBook with real MTGA data and validate results.

**Files:**
- Modify: `scanner/tests/test_deck_scanner.py` (add integration tests)
- Create: `docs/deck-scanner-validation.md`

**Step 1: Write integration test**

```python
def test_deck_scan_integration():
    """Integration test: scan decks from a real MTGA process.
    
    Requires: MTGA running, pymem-osx installed, sudo.
    Skip if not available.
    """
    pytest.importorskip("pymem")
    if not _is_mtga_running():
        pytest.skip("MTGA not running")
    
    result = scan_decks()
    assert result is not None
    assert len(result.decks) > 0
    for deck in result.decks:
        assert deck.name
        assert PILE_MAIN in deck.piles
        assert len(deck.piles[PILE_MAIN]) > 0  # At least 60 cards
```

**Step 2: Run on MacBook**

```bash
sudo python3 -m cli.main deck-scan
```

Expected: List of decks with card counts per pile.

**Step 3: Validate against known decks**

Compare scanned decks with actual MTGA decks:
- Check card counts match
- Check sideboard cards are correct
- Check format matches

**Step 4: Document results**

Write `docs/deck-scanner-validation.md` with:
- Test results
- Known limitations
- Performance metrics

**Step 5: Commit**

```bash
git add scanner/tests/test_deck_scanner.py docs/deck-scanner-validation.md
git commit -m "[test] Deck scanner validation — integration tests + results"
```

---

## Success Criteria

- [ ] `python3 -m cli.main deck-scan` returns full card lists per deck
- [ ] Each deck has Mainboard (60+ cards), Sideboard (0-15), Command Zone (0-1)
- [ ] Card names resolved via card_database.py (grpId → name)
- [ ] Dashboard shows deck cards when a deck is selected
- [ ] LLM advisor includes deck cards in its analysis prompt
- [ ] All tests pass: `python3 -m unittest discover -s scanner/tests -v`
- [ ] Works with sudo on macOS (pymem-osx)
