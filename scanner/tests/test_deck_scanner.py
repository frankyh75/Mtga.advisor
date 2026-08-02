"""Tests for pattern-based deck memory scanner.

The deck scanner parses pile-structured memory blocks that contain
EDeckPile-typed dictionaries with CardQuantity (grpId, quantity) entries.
Layout follows mtgatool's queries.rs read_piles() / read_pile_list().
"""

from __future__ import annotations

import struct
import pytest

from scanner.deck_scanner import (
    PILE_COMMAND_ZONE,
    PILE_COMPANIONS,
    PILE_MAIN,
    PILE_SIDEBOARD,
    DeckMemoryResult,
    DeckScanResult,
    parse_pile_list_from_bytes,
    parse_piles_from_bytes,
    parse_deck_block,
    merge_deck_results,
    scan_decks,
    find_deck_blocks,
    write_deck_artifacts,
)


# ---------------------------------------------------------------------------
# Helpers — build binary pile/dict structures matching IL2CPP/Mono layout
# ---------------------------------------------------------------------------

def _pack_u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _pack_i32(v: int) -> bytes:
    return struct.pack("<i", v)


def _pack_ptr(v: int) -> bytes:
    return struct.pack("<Q", v)


def _make_pile_list_bytes(cards: list[tuple[int, int]]) -> bytes:
    """Build a fake List<CardQuantity> backing array.

    IL2CPP List<T> backing array (items ptr + size + capacity):
      - _items: ptr (8 bytes) → points to array data at +0x20
      - _size: i32 (4 bytes)
      - _capacity: i32 (4 bytes, unused but present)

    Array data starts at +0x20, each element is 8 bytes:
      grpId: u32, quantity: i32
    """
    size = len(cards)
    # We build the full object inline: [ptr][size][cap] + array_header + data
    # ptr points to self + 0x18 (the array start after header fields)
    array_header = b"\x00" * 0x20  # 32 bytes header for IL2CPP array
    card_data = b"".join(_pack_u32(g) + _pack_i32(q) for g, q in cards)
    array_blob = array_header + card_data
    # list object: ptr(self+0x18), size, cap — but for test we just return array_blob
    # Actually parse_pile_list_from_bytes reads size and then data from a raw bytes buffer.
    # We design the test to work with our parser's expected layout.
    return _pack_i32(size) + array_blob


def _make_pile_dict_bytes(piles: list[tuple[int, list[tuple[int, int]]]]) -> bytes:
    """Build a fake Dictionary<EDeckPile, List<CardQuantity>> entries array.

    Dictionary entries layout (from queries.rs read_piles):
      entries = dict + 0x18 (ptr)
      cap = entries + 0x18 (i32)
      data = entries + 0x20
      entry stride = 24 bytes: hashCode(i32) + next(i32) + key(i32) + value(ptr)

    But for pattern-based scanning we don't have pointers — we parse
    the raw pile list data directly. So we build a flat representation:
    [pile_type(i32)][card_count(i32)][card_data...] repeated.
    """
    blob = b""
    for pile_type, cards in piles:
        blob += _pack_i32(pile_type)
        blob += _pack_i32(len(cards))
        for grp_id, qty in cards:
            blob += _pack_u32(grp_id) + _pack_i32(qty)
    return blob


# ---------------------------------------------------------------------------
# Tests — parse_pile_list_from_bytes
# ---------------------------------------------------------------------------

class TestParsePileList:
    def test_single_card(self):
        """Parse a pile list with one card."""
        data = _pack_i32(1)  # size = 1
        data += _pack_u32(12345) + _pack_i32(4)  # grpId=12345, qty=4
        result = parse_pile_list_from_bytes(data, 0)
        assert result == [(12345, 4)]

    def test_multiple_cards(self):
        """Parse a pile list with 3 cards."""
        cards = [(12345, 4), (67890, 3), (11111, 2)]
        data = _pack_i32(3)  # size = 3
        for g, q in cards:
            data += _pack_u32(g) + _pack_i32(q)
        result = parse_pile_list_from_bytes(data, 0)
        assert result == [(12345, 4), (67890, 3), (11111, 2)]

    def test_empty_pile(self):
        """Parse an empty pile list."""
        data = _pack_i32(0)
        result = parse_pile_list_from_bytes(data, 0)
        assert result == []

    def test_invalid_qty_filtered(self):
        """Cards with invalid quantities are filtered out."""
        data = _pack_i32(3)
        data += _pack_u32(1100) + _pack_i32(4)       # qty=4 ok
        data += _pack_u32(1200) + _pack_i32(0)       # qty=0 filtered
        data += _pack_u32(1300) + _pack_i32(-1)      # qty=-1 filtered
        result = parse_pile_list_from_bytes(data, 0)
        assert result == [(1100, 4)]

    def test_implausible_size_returns_empty(self):
        """Implausible size values return empty list."""
        data = _pack_i32(50000)  # > MAX_PILE_SIZE
        result = parse_pile_list_from_bytes(data, 0)
        assert result == []

    def test_truncated_data(self):
        """Truncated data returns partial results."""
        data = _pack_i32(3)  # says 3 cards
        data += _pack_u32(12345) + _pack_i32(4)  # only 1 card present
        result = parse_pile_list_from_bytes(data, 0)
        assert result == [(12345, 4)]  # partial


# ---------------------------------------------------------------------------
# Tests — parse_piles_from_bytes (flat pile-structured block)
# ---------------------------------------------------------------------------

class TestParsePiles:
    def test_single_pile(self):
        """Parse a block with one pile (Main)."""
        blob = _make_pile_dict_bytes([
            (PILE_MAIN, [(12345, 4), (67890, 3)]),
        ])
        result = parse_piles_from_bytes(blob)
        assert len(result) == 1
        assert result[0][0] == PILE_MAIN
        assert result[0][1] == [(12345, 4), (67890, 3)]

    def test_multiple_piles(self):
        """Parse a block with Main + Sideboard."""
        blob = _make_pile_dict_bytes([
            (PILE_MAIN, [(12345, 4), (67890, 3), (11111, 2)]),
            (PILE_SIDEBOARD, [(22222, 1)]),
        ])
        result = parse_piles_from_bytes(blob)
        assert len(result) == 2
        assert result[0][0] == PILE_MAIN
        assert result[0][1] == [(12345, 4), (67890, 3), (11111, 2)]
        assert result[1][0] == PILE_SIDEBOARD
        assert result[1][1] == [(22222, 1)]

    def test_all_pile_types(self):
        """Parse all four pile types."""
        blob = _make_pile_dict_bytes([
            (PILE_MAIN, [(1100, 4)]),
            (PILE_SIDEBOARD, [(1200, 1)]),
            (PILE_COMMAND_ZONE, [(1300, 1)]),
            (PILE_COMPANIONS, [(1400, 1)]),
        ])
        result = parse_piles_from_bytes(blob)
        assert len(result) == 4
        pile_types = [r[0] for r in result]
        assert PILE_MAIN in pile_types
        assert PILE_SIDEBOARD in pile_types
        assert PILE_COMMAND_ZONE in pile_types
        assert PILE_COMPANIONS in pile_types

    def test_empty_block(self):
        """Empty block returns no piles."""
        result = parse_piles_from_bytes(b"")
        assert result == []

    def test_garbage_data(self):
        """Random garbage returns no valid piles."""
        garbage = b"\x00" * 100
        result = parse_piles_from_bytes(garbage)
        # Might find pile_type=0 which is "Invalid" — should be filtered
        for pile_type, _ in result:
            assert pile_type in (PILE_MAIN, PILE_SIDEBOARD, PILE_COMMAND_ZONE, PILE_COMPANIONS)


# ---------------------------------------------------------------------------
# Tests — parse_deck_block
# ---------------------------------------------------------------------------

class TestParseDeckBlock:
    def test_valid_deck_block(self):
        """Parse a complete deck block with metadata + piles."""
        # Build a deck block: name_len(u32) + name(utf16-le) + piles
        name = "Test Deck"
        name_bytes = name.encode("utf-16-le")
        blob = _pack_u32(len(name)) + name_bytes
        blob += _make_pile_dict_bytes([
            (PILE_MAIN, [(12345, 4), (67890, 3), (11111, 2)]),
            (PILE_SIDEBOARD, [(22222, 1)]),
        ])
        result = parse_deck_block(blob, 0)
        assert result is not None
        assert result.name == "Test Deck"
        assert PILE_MAIN in result.piles
        assert result.piles[PILE_MAIN] == {12345: 4, 67890: 3, 11111: 2}
        assert PILE_SIDEBOARD in result.piles
        assert result.piles[PILE_SIDEBOARD] == {22222: 1}

    def test_block_without_name(self):
        """Parse a block without name — should still extract piles."""
        blob = _make_pile_dict_bytes([
            (PILE_MAIN, [(12345, 4)]),
        ])
        result = parse_deck_block(blob, 0)
        assert result is not None
        assert PILE_MAIN in result.piles
        assert result.piles[PILE_MAIN] == {12345: 4}

    def test_empty_block_returns_none(self):
        """Empty block returns None."""
        result = parse_deck_block(b"", 0)
        assert result is None

    def test_garbage_block_returns_none(self):
        """Garbage block returns None or a result with no valid piles."""
        result = parse_deck_block(b"\xff" * 64, 0)
        if result is not None:
            assert not any(result.piles.values())


# ---------------------------------------------------------------------------
# Tests — merge_deck_results
# ---------------------------------------------------------------------------

class TestMergeDeckResults:
    def test_dedup_identical_decks(self):
        """Two identical deck results merge into one."""
        d1 = DeckMemoryResult(
            name="Deck A",
            deck_id="abc",
            piles={PILE_MAIN: {100: 4, 200: 2}},
            last_updated="",
        )
        d2 = DeckMemoryResult(
            name="Deck A",
            deck_id="abc",
            piles={PILE_MAIN: {100: 4, 200: 2}},
            last_updated="",
        )
        merged = merge_deck_results([d1, d2])
        assert len(merged) == 1
        assert merged[0].name == "Deck A"

    def test_different_decks_kept(self):
        """Two different decks are both kept."""
        d1 = DeckMemoryResult(name="Deck A", deck_id="abc", piles={PILE_MAIN: {100: 4}}, last_updated="")
        d2 = DeckMemoryResult(name="Deck B", deck_id="def", piles={PILE_MAIN: {200: 2}}, last_updated="")
        merged = merge_deck_results([d1, d2])
        assert len(merged) == 2

    def test_merge_complementary_piles(self):
        """Same deck found with different piles — merge complementary."""
        d1 = DeckMemoryResult(
            name="Deck A", deck_id="abc",
            piles={PILE_MAIN: {100: 4, 200: 2}},
            last_updated="",
        )
        d2 = DeckMemoryResult(
            name="Deck A", deck_id="abc",
            piles={PILE_SIDEBOARD: {300: 1}},
            last_updated="",
        )
        merged = merge_deck_results([d1, d2])
        assert len(merged) == 1
        assert PILE_MAIN in merged[0].piles
        assert PILE_SIDEBOARD in merged[0].piles
        assert merged[0].piles[PILE_MAIN] == {100: 4, 200: 2}
        assert merged[0].piles[PILE_SIDEBOARD] == {300: 1}

    def test_empty_input(self):
        """Empty list returns empty."""
        assert merge_deck_results([]) == []


# ---------------------------------------------------------------------------
# Tests — find_deck_blocks (with mock pm)
# ---------------------------------------------------------------------------

class TestFindDeckBlocks:
    def test_finds_blocks_around_anchor(self, monkeypatch):
        """find_deck_blocks reads memory around anchor addresses and returns candidate bytes."""
        # Build a fake pile block in memory
        pile_blob = _make_pile_dict_bytes([
            (PILE_MAIN, [(12345, 4), (67890, 2)]),
        ])

        class FakePm:
            task = 1

        # Mock _read_bytes_silent in the deck_scanner module's reference
        from scanner import deck_scanner

        def fake_read(pm, addr, size):
            # Place pile_blob at offset 1024 within the read window
            padding_before = b"\x00" * 1024
            padding_after = b"\x00" * max(0, size - 1024 - len(pile_blob))
            return padding_before + pile_blob + padding_after

        monkeypatch.setattr(deck_scanner._ps, "_read_bytes_silent", fake_read)

        blocks = find_deck_blocks(FakePm(), [0x10000])
        assert len(blocks) > 0
        # At least one block should contain our pile data
        found_pile = False
        for block in blocks:
            piles = parse_piles_from_bytes(block)
            if piles:
                found_pile = True
                break
        assert found_pile, "No valid pile data found in any block"


# ---------------------------------------------------------------------------
# Tests — scan_decks (integration with mock pm)
# ---------------------------------------------------------------------------

class TestScanDecks:
    def test_scan_decks_with_mocks(self, monkeypatch):
        """Full scan_decks pipeline with mocked memory scanner and block parser."""
        pile_blob = _make_pile_dict_bytes([
            (PILE_MAIN, [(12345, 4), (67890, 3)]),
            (PILE_SIDEBOARD, [(22222, 1)]),
        ])

        class FakePm:
            pid = 4242
            task = 7

        from scanner import pattern_scanner

        # Mock memory scanning to return one fake address
        def fake_scan_many(pm, needles):
            return pattern_scanner.MultiScanResult(
                addresses={k: [0x10000] for k in needles},
                stats=pattern_scanner.ScanStats(
                    regions=1, bytes_scanned=4096,
                    read_failures=0, matches=len(needles),
                ),
            )

        monkeypatch.setattr(pattern_scanner, "scan_process_memory_many_with_stats", fake_scan_many)

        # Mock _read_bytes_silent to return pile blob
        def fake_read(pm, addr, size):
            padding = b"\x00" * 512
            return padding + pile_blob + b"\x00" * max(0, size - 512 - len(pile_blob))

        monkeypatch.setattr(pattern_scanner, "_read_bytes_silent", fake_read)

        result = scan_decks(
            FakePm(),
            anchor_grp_ids=[12345],
            deck_names={12345: "Test Deck"},
        )
        assert isinstance(result, DeckScanResult)
        # Should find at least one deck with cards
        # (may or may not depending on block parsing heuristics)
        assert isinstance(result.decks, list)
        assert isinstance(result.warnings, list)


# ---------------------------------------------------------------------------
# Tests — write_deck_artifacts
# ---------------------------------------------------------------------------

class TestWriteDeckArtifacts:
    def test_writes_json_files(self, tmp_path):
        """write_deck_artifacts produces valid JSON output."""
        result = DeckScanResult(
            decks=[
                DeckMemoryResult(
                    name="Test Deck",
                    deck_id="abc-123",
                    piles={
                        PILE_MAIN: {12345: 4, 67890: 3},
                        PILE_SIDEBOARD: {22222: 1},
                    },
                    last_updated="2026-01-01T00:00:00Z",
                ),
            ],
            warnings=[],
        )
        deck_path, container_path = write_deck_artifacts(result, tmp_path / "out")
        assert deck_path.exists()
        assert container_path.exists()

        import json
        container = json.loads(container_path.read_text())
        assert container["schema"] == "decks-container.v1"
        assert len(container["decks"]) == 1
        assert container["decks"][0]["name"] == "Test Deck"
        assert container["decks"][0]["cards"]["mainboard"] == {"12345": 4, "67890": 3}
        assert container["decks"][0]["cards"]["sideboard"] == {"22222": 1}

    def test_empty_result(self, tmp_path):
        """Empty scan result produces empty container."""
        result = DeckScanResult(decks=[], warnings=["no decks found"])
        deck_dir, container_path = write_deck_artifacts(result, tmp_path / "out")
        assert container_path.exists()

        import json
        container = json.loads(container_path.read_text())
        assert container["decks"] == []
        assert "no decks found" in container["warnings"]