"""Tests for IL2CPP navigation scanner.

Tests the IL2CPP memory navigation utilities that find and read deck data
by navigating the IL2CPP type system: TypeInfoTable → class resolution →
field navigation → Dictionary entries → Deck/Pile structures.

The scanner mirrors what mtgatool's Rust napi/mod.rs does on macOS:
  PAPA instance → DecksManager → _deckDataProvider → _allDecks → Client_Deck
"""

from __future__ import annotations

import struct
import pytest

from scanner.il2cpp_nav import (
    IL2CPP_OFFSETS,
    Il2CppReader,
    MockMemory,
    _discover_data_segment_base_from_image,
    _find_game_assembly_base,
    _find_string_in_metadata_regions,
    _find_class_by_field_backref_in_regions,
    _find_class_by_name_in_arena,
    _find_instance_by_klass_in_regions,
    _validate_class_struct,
    find_class_by_name,
    find_papa_instance,
    get_class_fields,
    read_il2cpp_string,
    read_dictionary_uint_ptr,
    read_list_of_structs,
    navigate_to_all_decks,
    scan_decks_il2cpp,
    DeckNavResult,
)


# ---------------------------------------------------------------------------
# Helpers — build mock IL2CPP memory layouts
# ---------------------------------------------------------------------------

def _pack_ptr(v: int) -> bytes:
    return struct.pack("<Q", v)


def _pack_u32(v: int) -> bytes:
    return struct.pack("<I", v)


def _pack_i32(v: int) -> bytes:
    return struct.pack("<i", v)


def _make_il2cpp_string(text: str) -> bytes:
    """Build a fake IL2CPP string in memory.

    IL2CPP string layout:
      +0x00: class_ptr (8 bytes, not checked by reader)
      +0x10: length (i32, char count)
      +0x14: chars (UTF-16LE)

    Returns raw bytes — caller places bytes at desired address in mock.
    """
    chars = text.encode("utf-16-le")
    raw = b"\x00" * 0x10  # header up to length
    raw += struct.pack("<i", len(text))
    raw += chars
    pad = (32 - len(raw) % 32) % 32
    raw += b"\x00" * pad  # pad to 32-byte boundary
    return raw


def _make_field_info(name_ptr: int, offset: int, type_ptr: int = 0x200000) -> bytes:
    """Build a 32-byte FieldInfo structure.

    FieldInfo layout (IL2CPP, 32 bytes):
      +0x00: name_ptr (8 bytes)
      +0x08: type_ptr (8 bytes)
      +0x10: parent (8 bytes)
      +0x18: offset (u32) + token (u32)
    """
    return _pack_ptr(name_ptr) + _pack_ptr(type_ptr) + _pack_ptr(0) + _pack_u32(offset) + _pack_u32(0)


def _make_class(name_ptr: int, fields_ptr: int, field_count: int = 5) -> bytes:
    """Build a minimal IL2CPP class structure.

    Only the offsets we read are populated; rest is zero.
    """
    raw = bytearray(0x200)  # 512 bytes, enough for all offsets
    # class_name at +0x10
    raw[0x10:0x18] = _pack_ptr(name_ptr)
    # class_namespace at +0x18
    raw[0x18:0x20] = _pack_ptr(0)
    # class_fields at +0x80
    raw[0x80:0x88] = _pack_ptr(fields_ptr)
    # class_field_count at +0x124
    raw[0x124:0x128] = _pack_u32(field_count)
    # class_static_fields at +0xA8
    raw[0xA8:0xB0] = _pack_ptr(0)
    return bytes(raw)


def _make_type_info_table(classes: list[tuple[int, int]]) -> bytes:
    """Build a TypeInfoTable: array of class pointers.

    Args:
        classes: list of (index, class_addr) pairs
    Returns:
        Raw bytes for the table region
    """
    max_idx = max(idx for idx, _ in classes) + 1
    table = bytearray(max_idx * 8)
    for idx, addr in classes:
        table[idx * 8: idx * 8 + 8] = _pack_ptr(addr)
    return bytes(table)


def _make_segment_64(name: str, vmaddr: int, vmsize: int) -> bytes:
    """Build a minimal LC_SEGMENT_64 load command."""
    segname = name.encode("ascii")[:16].ljust(16, b"\x00")
    body = segname + struct.pack("<QQQQIIII", vmaddr, vmsize, 0, 0, 7, 5, 0, 0)
    return struct.pack("<II", 0x19, 72) + body


def _make_macho_header(segments: list[bytes]) -> bytes:
    """Build a minimal 64-bit Mach-O header with the given load commands."""
    sizeofcmds = sum(len(seg) for seg in segments)
    header = struct.pack(
        "<IiiIIII",
        0xFEEDFACF,
        0x01000007,
        3,
        6,
        len(segments),
        sizeofcmds,
        0,
    ) + struct.pack("<I", 0)
    return header + b"".join(segments)


# ---------------------------------------------------------------------------
# MockMemory — in-memory byte store for testing
# ---------------------------------------------------------------------------

class TestMockMemory:
    """Tests for the MockMemory helper itself."""

    def test_write_and_read_bytes(self):
        mem = MockMemory()
        mem.write(0x100000, b"hello world")
        assert mem.read_bytes(0x100000, 11) == b"hello world"

    def test_read_ptr(self):
        mem = MockMemory()
        mem.write(0x200000, _pack_ptr(0xDEADBEEF))
        assert mem.read_ptr(0x200000) == 0xDEADBEEF

    def test_read_u32(self):
        mem = MockMemory()
        mem.write(0x300000, _pack_u32(12345))
        assert mem.read_u32(0x300000) == 12345

    def test_read_i32(self):
        mem = MockMemory()
        mem.write(0x400000, _pack_i32(-42))
        assert mem.read_i32(0x400000) == -42

    def test_read_bytes_uninitialized_returns_zeros(self):
        mem = MockMemory()
        data = mem.read_bytes(0x500000, 8)
        assert data == b"\x00" * 8

    def test_read_string_ascii(self):
        mem = MockMemory()
        mem.write(0x600000, b"ClassName\x00extra")
        assert mem.read_string(0x600000) == "ClassName"


# ---------------------------------------------------------------------------
# IL2CPP string reading
# ---------------------------------------------------------------------------

class TestReadIl2CppString:
    """Tests for reading IL2CPP UTF-16 strings."""

    def test_simple_string(self):
        mem = MockMemory()
        str_bytes = _make_il2cpp_string("Hello")
        mem.write(0x100000, str_bytes)
        result = read_il2cpp_string(mem, 0x100000)
        assert result == "Hello"

    def test_empty_string(self):
        mem = MockMemory()
        str_bytes = _make_il2cpp_string("")
        mem.write(0x200000, str_bytes)
        result = read_il2cpp_string(mem, 0x200000)
        assert result == ""

    def test_unicode_string(self):
        mem = MockMemory()
        text = "Käfer Über"
        str_bytes = _make_il2cpp_string(text)
        mem.write(0x300000, str_bytes)
        result = read_il2cpp_string(mem, 0x300000)
        assert result == text

    def test_null_pointer_returns_empty(self):
        mem = MockMemory()
        result = read_il2cpp_string(mem, 0)
        assert result == ""

    def test_long_deck_name(self):
        mem = MockMemory()
        text = "My Awesome Standard Deck 2026"
        str_bytes = _make_il2cpp_string(text)
        mem.write(0x400000, str_bytes)
        result = read_il2cpp_string(mem, 0x400000)
        assert result == text


# ---------------------------------------------------------------------------
# Class finding via TypeInfoTable
# ---------------------------------------------------------------------------

class TestFindClassByName:
    """Tests for finding IL2CPP classes by name via the TypeInfoTable."""

    def test_find_existing_class(self):
        mem = MockMemory()
        # Set up: name string → class → type info table
        # Addresses must be > MIN_VALID_PTR (0x100000)
        name_addr = 0x100000
        mem.write(name_addr, b"PAPA\x00")
        class_addr = 0x200000
        class_bytes = _make_class(name_ptr=name_addr, fields_ptr=0)
        mem.write(class_addr, class_bytes)
        table_bytes = _make_type_info_table([(0, name_addr), (1, class_addr)])
        table_addr = 0x500000
        mem.write(table_addr, table_bytes)

        result = find_class_by_name(mem, table_addr, "PAPA")
        assert result == class_addr

    def test_find_nonexistent_class_returns_none(self):
        mem = MockMemory()
        name_addr = 0x100000
        mem.write(name_addr, b"SomeOther\x00")
        class_addr = 0x200000
        class_bytes = _make_class(name_ptr=name_addr, fields_ptr=0)
        mem.write(class_addr, class_bytes)
        table_bytes = _make_type_info_table([(0, class_addr)])
        table_addr = 0x500000
        mem.write(table_addr, table_bytes)

        result = find_class_by_name(mem, table_addr, "PAPA")
        assert result is None

    def test_find_class_among_many(self):
        mem = MockMemory()
        names = ["Object", "String", "PAPA", "Deck", "List"]
        classes = []
        for i, name in enumerate(names):
            name_addr = 0x100000 + i * 0x10000
            mem.write(name_addr, name.encode("ascii") + b"\x00")
            class_addr = 0x200000 + i * 0x200
            mem.write(class_addr, _make_class(name_ptr=name_addr, fields_ptr=0))
            classes.append((i, class_addr))

        table_addr = 0x800000
        mem.write(table_addr, _make_type_info_table(classes))

        result = find_class_by_name(mem, table_addr, "PAPA")
        assert result == 0x200000 + 2 * 0x200  # Third class (index 2)

    def test_skips_null_entries(self):
        mem = MockMemory()
        name_addr = 0x100000
        mem.write(name_addr, b"Target\x00")
        class_addr = 0x200000
        mem.write(class_addr, _make_class(name_ptr=name_addr, fields_ptr=0))
        # Table with null at index 0, class at index 1
        table_bytes = _pack_ptr(0) + _pack_ptr(class_addr)
        table_addr = 0x500000
        mem.write(table_addr, table_bytes)

        result = find_class_by_name(mem, table_addr, "Target")
        assert result == class_addr


# ---------------------------------------------------------------------------
# Field resolution
# ---------------------------------------------------------------------------

class TestGetClassFields:
    """Tests for reading IL2CPP class field metadata."""

    def test_read_single_field(self):
        mem = MockMemory()
        # Field name
        name_addr = 0x100000
        mem.write(name_addr, b"DecksManager\x00")
        # FieldInfo at fields_ptr
        fields_ptr = 0x200000
        field_bytes = _make_field_info(name_ptr=name_addr, offset=0x20)
        mem.write(fields_ptr, field_bytes)
        # Class with fields pointer
        class_addr = 0x300000
        mem.write(class_addr, _make_class(name_ptr=0, fields_ptr=fields_ptr, field_count=1))

        fields = get_class_fields(mem, class_addr)
        assert len(fields) >= 1
        assert any(f.name == "DecksManager" for f in fields)

    def test_read_multiple_fields(self):
        mem = MockMemory()
        field_names = ["DecksManager", "InventoryManager", "PlayerData"]
        fields_ptr = 0x200000
        for i, name in enumerate(field_names):
            name_addr = 0x100000 + i * 0x10000
            mem.write(name_addr, name.encode("ascii") + b"\x00")
            field_bytes = _make_field_info(name_ptr=name_addr, offset=0x10 + i * 8)
            mem.write(fields_ptr + i * 32, field_bytes)

        class_addr = 0x300000
        mem.write(class_addr, _make_class(name_ptr=0, fields_ptr=fields_ptr, field_count=3))

        fields = get_class_fields(mem, class_addr)
        field_names_result = [f.name for f in fields]
        assert "DecksManager" in field_names_result
        assert "InventoryManager" in field_names_result
        assert "PlayerData" in field_names_result

    def test_zero_fields_ptr_returns_empty(self):
        mem = MockMemory()
        class_addr = 0x300000
        mem.write(class_addr, _make_class(name_ptr=0, fields_ptr=0, field_count=0))
        fields = get_class_fields(mem, class_addr)
        assert fields == []

    def test_field_offset_correct(self):
        mem = MockMemory()
        name_addr = 0x100000
        mem.write(name_addr, b"_allDecks\x00")
        fields_ptr = 0x200000
        field_bytes = _make_field_info(name_ptr=name_addr, offset=0x38)
        mem.write(fields_ptr, field_bytes)
        class_addr = 0x300000
        mem.write(class_addr, _make_class(name_ptr=0, fields_ptr=fields_ptr, field_count=1))

        fields = get_class_fields(mem, class_addr)
        target = next(f for f in fields if f.name == "_allDecks")
        assert target.offset == 0x38


# ---------------------------------------------------------------------------
# PAPA instance finding (heap scan)
# ---------------------------------------------------------------------------

class TestFindPapaInstance:
    """Tests for finding the PAPA singleton instance on the heap."""

    def test_find_papa_at_expected_offset(self):
        mem = MockMemory()
        papa_class = 0x300000

        # Build a fake PAPA instance at a heap address
        papa_addr = 0x15A001000
        # +0x00: class_ptr = papa_class
        # +0x10: some other ptr (not papa_class, > 0x100000)
        # +224 (0xE0): InventoryManager pointer
        # The InventoryManager's class should contain "InventoryManager"
        inv_mgr_addr = 0x15B000000
        inv_class_addr = 0x400000
        # Set up inv class name
        inv_name_addr = 0x500000
        mem.write(inv_name_addr, b"InventoryManager\x00")
        mem.write(inv_class_addr, _make_class(name_ptr=inv_name_addr, fields_ptr=0))

        instance = bytearray(0x200)
        instance[0:8] = _pack_ptr(papa_class)
        instance[16:24] = _pack_ptr(0x200000)  # val_at_16, not papa_class, > 0x100000
        instance[224:232] = _pack_ptr(inv_mgr_addr)
        mem.write(papa_addr, bytes(instance))

        # Set up inv_mgr: first 8 bytes = class_ptr = inv_class_addr
        mem.write(inv_mgr_addr, _pack_ptr(inv_class_addr))

        result = find_papa_instance(mem, papa_class)
        assert result == papa_addr

    def test_no_papa_instance_returns_none(self):
        mem = MockMemory()
        papa_class = 0x300000
        # No instance with papa_class pointer in the heap regions
        result = find_papa_instance(mem, papa_class)
        assert result is None

    def test_rejects_wrong_class_at_offset_16(self):
        """If the value at +16 equals papa_class, it's not the right instance."""
        mem = MockMemory()
        papa_class = 0x300000
        papa_addr = 0x15A001000

        instance = bytearray(0x200)
        instance[0:8] = _pack_ptr(papa_class)
        instance[16:24] = _pack_ptr(papa_class)  # Same as papa_class → rejected
        instance[224:232] = _pack_ptr(0x15B000000)
        mem.write(papa_addr, bytes(instance))

        result = find_papa_instance(mem, papa_class)
        assert result is None


# ---------------------------------------------------------------------------
# Dictionary<uint, Client_Deck*> reading
# ---------------------------------------------------------------------------

class TestReadDictionaryUintPtr:
    """Tests for reading IL2CPP Dictionary<uint, ptr> entries."""

    def test_read_single_entry(self):
        """Read a dictionary with one entry: key=42, value=0x200000."""
        mem = MockMemory()
        dict_addr = 0x100000
        entries_array = 0x200000
        mem.write(dict_addr + 0x18, _pack_ptr(entries_array))
        mem.write(dict_addr + 0x20, _pack_i32(1))
        mem.write(entries_array + 0x18, _pack_i32(1))  # array length

        # Entry at entries_array + 0x20, stride 24 bytes:
        entry_addr = entries_array + 0x20
        entry = _pack_i32(42) + _pack_i32(-1) + _pack_u32(42) + b"\x00" * 4 + _pack_ptr(0x200000)
        mem.write(entry_addr, entry)

        entries = read_dictionary_uint_ptr(mem, dict_addr, entry_stride=24)
        assert len(entries) == 1
        assert entries[0][0] == 42  # key
        assert entries[0][1] == 0x200000  # value ptr

    def test_read_multiple_entries(self):
        mem = MockMemory()
        dict_addr = 0x100000
        entries_array = 0x200000
        mem.write(dict_addr + 0x18, _pack_ptr(entries_array))
        mem.write(dict_addr + 0x20, _pack_i32(3))
        mem.write(entries_array + 0x18, _pack_i32(4))  # capacity

        # Three entries at stride 24
        for i, (key, val) in enumerate([(1, 0x100000), (2, 0x200000), (3, 0x300000)]):
            entry_addr = entries_array + 0x20 + i * 24
            entry = _pack_i32(key * 7) + _pack_i32(-1) + _pack_u32(key) + b"\x00" * 4 + _pack_ptr(val)
            mem.write(entry_addr, entry)

        entries = read_dictionary_uint_ptr(mem, dict_addr, entry_stride=24)
        assert len(entries) == 3
        assert entries[0] == (1, 0x100000)
        assert entries[1] == (2, 0x200000)
        assert entries[2] == (3, 0x300000)

    def test_skip_free_slots(self):
        """Entries with negative hashCode are free slots and should be skipped."""
        mem = MockMemory()
        dict_addr = 0x100000
        entries_array = 0x200000
        mem.write(dict_addr + 0x18, _pack_ptr(entries_array))
        mem.write(dict_addr + 0x20, _pack_i32(2))
        mem.write(entries_array + 0x18, _pack_i32(4))

        # Entry 0: free (hashCode < 0)
        entry0_addr = entries_array + 0x20
        entry0 = _pack_i32(-1) + _pack_i32(-1) + _pack_u32(0) + b"\x00" * 4 + _pack_ptr(0)
        mem.write(entry0_addr, entry0)

        # Entry 1: active
        entry1_addr = entries_array + 0x20 + 24
        entry1 = _pack_i32(99) + _pack_i32(-1) + _pack_u32(99) + b"\x00" * 4 + _pack_ptr(0x500000)
        mem.write(entry1_addr, entry1)

        entries = read_dictionary_uint_ptr(mem, dict_addr, entry_stride=24)
        assert len(entries) == 1
        assert entries[0] == (99, 0x500000)

    def test_empty_dictionary(self):
        mem = MockMemory()
        dict_addr = 0x100000
        mem.write(dict_addr + 0x18, _pack_ptr(0))
        mem.write(dict_addr + 0x20, _pack_i32(0))
        entries = read_dictionary_uint_ptr(mem, dict_addr, entry_stride=24)
        assert entries == []

    def test_entry_stride_32(self):
        """Test with 32-byte entry stride (Mono-like layout with padding)."""
        mem = MockMemory()
        dict_addr = 0x100000
        entries_array = 0x200000
        mem.write(dict_addr + 0x18, _pack_ptr(entries_array))
        mem.write(dict_addr + 0x20, _pack_i32(1))
        mem.write(entries_array + 0x18, _pack_i32(2))

        # Entry at stride 32, value at +0x18 (Mono layout)
        entry_addr = entries_array + 0x20
        entry = _pack_i32(7) + _pack_i32(-1) + _pack_u32(7) + b"\x00" * 4
        entry += b"\x00" * 8  # padding
        entry += _pack_ptr(0x300000)
        mem.write(entry_addr, entry)

        entries = read_dictionary_uint_ptr(mem, dict_addr, entry_stride=32, value_offset=0x18)
        assert len(entries) == 1
        assert entries[0] == (7, 0x300000)


# ---------------------------------------------------------------------------
# List<{grpId, qty}> reading
# ---------------------------------------------------------------------------

class TestReadListOfStructs:
    """Tests for reading IL2CPP List<T> of struct elements."""

    def test_read_three_cards(self):
        mem = MockMemory()
        list_addr = 0x100000
        items_array = 0x200000
        mem.write(list_addr + 0x10, _pack_ptr(items_array))
        mem.write(list_addr + 0x18, _pack_i32(3))
        mem.write(items_array + 0x18, _pack_i32(4))  # capacity

        # Three 8-byte elements: (grpId u32, qty i32)
        for i, (grp_id, qty) in enumerate([(12345, 4), (67890, 3), (11111, 2)]):
            elem_addr = items_array + 0x20 + i * 8
            mem.write(elem_addr, _pack_u32(grp_id) + _pack_i32(qty))

        result = read_list_of_structs(mem, list_addr, element_size=8)
        assert result == [(12345, 4), (67890, 3), (11111, 2)]

    def test_empty_list(self):
        mem = MockMemory()
        list_addr = 0x100000
        mem.write(list_addr + 0x10, _pack_ptr(0))
        mem.write(list_addr + 0x18, _pack_i32(0))
        result = read_list_of_structs(mem, list_addr, element_size=8)
        assert result == []

    def test_filters_invalid_grpids(self):
        mem = MockMemory()
        list_addr = 0x100000
        items_array = 0x200000
        mem.write(list_addr + 0x10, _pack_ptr(items_array))
        mem.write(list_addr + 0x18, _pack_i32(4))
        mem.write(items_array + 0x18, _pack_i32(4))

        # Mix of valid and invalid
        cards = [(12345, 4), (500, 1), (999999, 2), (67890, 3)]
        for i, (grp_id, qty) in enumerate(cards):
            elem_addr = items_array + 0x20 + i * 8
            mem.write(elem_addr, _pack_u32(grp_id) + _pack_i32(qty))

        result = read_list_of_structs(mem, list_addr, element_size=8)
        # 500 < MIN_GRP_ID(1000) and 999999 > MAX_GRP_ID(500000) filtered
        assert (12345, 4) in result
        assert (67890, 3) in result
        assert (500, 1) not in result
        assert (999999, 2) not in result


# ---------------------------------------------------------------------------
# Full navigation: PAPA → _allDecks
# ---------------------------------------------------------------------------

class TestNavigateToAllDecks:
    """Tests for navigating from PAPA instance to the _allDecks dictionary."""

    def test_successful_navigation(self):
        """Navigate PAPA → DecksManager → _deckDataProvider → _allDecks."""
        mem = MockMemory()

        # Set up class names
        papa_name_addr = 0x100000
        mem.write(papa_name_addr, b"PAPA\x00")
        dm_name_addr = 0x100100
        mem.write(dm_name_addr, b"DecksManager\x00")
        ddp_name_addr = 0x100200
        mem.write(ddp_name_addr, b"DeckDataProvider\x00")

        # Set up field names
        dm_field_name = 0x110000
        mem.write(dm_field_name, b"DecksManager\x00")
        ddp_field_name = 0x110100
        mem.write(ddp_field_name, b"_deckDataProvider\x00")
        decks_field_name = 0x110200
        mem.write(decks_field_name, b"_allDecks\x00")

        # Set up classes with fields
        # PAPA class
        papa_class = 0x200000
        papa_fields_ptr = 0x210000
        mem.write(papa_class, _make_class(name_ptr=papa_name_addr, fields_ptr=papa_fields_ptr))
        # PAPA has a field "DecksManager" at offset 0x20
        mem.write(papa_fields_ptr, _make_field_info(name_ptr=dm_field_name, offset=0x20))

        # DecksManager class
        dm_class = 0x300000
        dm_fields_ptr = 0x310000
        mem.write(dm_class, _make_class(name_ptr=dm_name_addr, fields_ptr=dm_fields_ptr))
        mem.write(dm_fields_ptr, _make_field_info(name_ptr=ddp_field_name, offset=0x10))

        # DeckDataProvider class
        ddp_class = 0x400000
        ddp_fields_ptr = 0x410000
        mem.write(ddp_class, _make_class(name_ptr=ddp_name_addr, fields_ptr=ddp_fields_ptr))
        mem.write(ddp_fields_ptr, _make_field_info(name_ptr=decks_field_name, offset=0x18))

        # PAPA instance
        papa_instance = 0x500000
        mem.write(papa_instance, _pack_ptr(papa_class))
        # DecksManager instance at PAPA + 0x20
        dm_instance = 0x600000
        mem.write(papa_instance + 0x20, _pack_ptr(dm_instance))
        mem.write(dm_instance, _pack_ptr(dm_class))
        # DeckDataProvider at DM + 0x10
        ddp_instance = 0x700000
        mem.write(dm_instance + 0x10, _pack_ptr(ddp_instance))
        mem.write(ddp_instance, _pack_ptr(ddp_class))
        # _allDecks dictionary at DDP + 0x18
        all_decks_dict = 0x800000
        mem.write(ddp_instance + 0x18, _pack_ptr(all_decks_dict))
        # Set up dictionary: entries ptr + count
        entries_array = 0x810000
        mem.write(all_decks_dict + 0x18, _pack_ptr(entries_array))
        mem.write(all_decks_dict + 0x20, _pack_i32(1))
        mem.write(entries_array + 0x18, _pack_i32(1))
        # One entry: key=1, value=0x900000 (deck ptr)
        entry_addr = entries_array + 0x20
        mem.write(entry_addr, _pack_i32(7) + _pack_i32(-1) + _pack_u32(1) + b"\x00" * 4 + _pack_ptr(0x900000))

        result = navigate_to_all_decks(mem, papa_instance, papa_class)
        assert result is not None
        assert result.dict_addr == all_decks_dict
        assert len(result.entries) == 1
        assert result.entries[0] == (1, 0x900000)

    def test_navigation_with_zero_pointer_returns_none(self):
        """If a field pointer is zero, navigation fails gracefully."""
        mem = MockMemory()
        papa_class = 0x200000
        papa_instance = 0x500000
        mem.write(papa_instance, _pack_ptr(papa_class))
        # DecksManager field at +0x20 is zero
        mem.write(papa_instance + 0x20, _pack_ptr(0))

        result = navigate_to_all_decks(mem, papa_instance, papa_class)
        assert result is None


# ---------------------------------------------------------------------------
# Full scan_decks_il2cpp with mock memory
# ---------------------------------------------------------------------------

class TestScanDecksIl2cpp:
    """Integration test for the full IL2CPP deck scan pipeline."""

    def test_scan_with_mock_memory(self):
        """End-to-end scan with a mock IL2CPP memory layout containing 2 decks."""
        mem = MockMemory()

        # === Build the full object graph ===
        # Class names
        names = {
            "PAPA": 0x100000,
            "DecksManager": 0x100100,
            "DeckDataProvider": 0x100200,
            "Client_Deck": 0x100300,
            "Client_DeckContents": 0x100400,
        }
        for name, addr in names.items():
            mem.write(addr, name.encode("ascii") + b"\x00")

        # Field names
        field_names = {
            "DecksManager": 0x110000,
            "_deckDataProvider": 0x110100,
            "_allDecks": 0x110200,
            "_summary": 0x110300,
            "_contents": 0x110400,
            "Name": 0x110500,
            "Piles": 0x110600,
        }
        for name, addr in field_names.items():
            mem.write(addr, name.encode("ascii") + b"\x00")

        # Classes with fields
        # PAPA class with DecksManager field at offset 0x20
        papa_class = 0x200000
        papa_fields = 0x210000
        mem.write(papa_class, _make_class(name_ptr=names["PAPA"], fields_ptr=papa_fields))
        mem.write(papa_fields, _make_field_info(name_ptr=field_names["DecksManager"], offset=0x20))

        # DecksManager class with _deckDataProvider at offset 0x10
        dm_class = 0x300000
        dm_fields = 0x310000
        mem.write(dm_class, _make_class(name_ptr=names["DecksManager"], fields_ptr=dm_fields))
        mem.write(dm_fields, _make_field_info(name_ptr=field_names["_deckDataProvider"], offset=0x10))

        # DeckDataProvider class with _allDecks at offset 0x18
        ddp_class = 0x400000
        ddp_fields = 0x410000
        mem.write(ddp_class, _make_class(name_ptr=names["DeckDataProvider"], fields_ptr=ddp_fields))
        mem.write(ddp_fields, _make_field_info(name_ptr=field_names["_allDecks"], offset=0x18))

        # Client_Deck class with _summary at +0x10, _contents at +0x18
        deck_class = 0x500000
        deck_fields = 0x510000
        mem.write(deck_class, _make_class(name_ptr=names["Client_Deck"], fields_ptr=deck_fields))
        mem.write(deck_fields, _make_field_info(name_ptr=field_names["_summary"], offset=0x10))
        mem.write(deck_fields + 32, _make_field_info(name_ptr=field_names["_contents"], offset=0x18))

        # Client_DeckContents class with Piles at +0x10
        contents_class = 0x600000
        contents_fields = 0x610000
        mem.write(contents_class, _make_class(name_ptr=names["Client_DeckContents"], fields_ptr=contents_fields))
        mem.write(contents_fields, _make_field_info(name_ptr=field_names["Piles"], offset=0x10))

        # PAPA instance
        papa_instance = 0x700000
        mem.write(papa_instance, _pack_ptr(papa_class))
        dm_instance = 0x710000
        mem.write(papa_instance + 0x20, _pack_ptr(dm_instance))
        mem.write(dm_instance, _pack_ptr(dm_class))
        ddp_instance = 0x720000
        mem.write(dm_instance + 0x10, _pack_ptr(ddp_instance))
        mem.write(ddp_instance, _pack_ptr(ddp_class))

        # _allDecks dictionary
        all_decks = 0x730000
        mem.write(ddp_instance + 0x18, _pack_ptr(all_decks))
        entries_array = 0x740000
        mem.write(all_decks + 0x18, _pack_ptr(entries_array))
        mem.write(all_decks + 0x20, _pack_i32(2))
        mem.write(entries_array + 0x18, _pack_i32(4))

        # Two deck entries (stride 24, value at +0x10)
        deck1_addr = 0x800000
        deck2_addr = 0x810000
        for i, (key, deck_addr) in enumerate([(1, deck1_addr), (2, deck2_addr)]):
            entry_addr = entries_array + 0x20 + i * 24
            mem.write(entry_addr, _pack_i32(key * 7) + _pack_i32(-1) + _pack_u32(key) + b"\x00" * 4 + _pack_ptr(deck_addr))

        # Deck 1: name + contents with Main pile
        # _summary at deck+0x10, _contents at deck+0x18
        summary1 = 0x820000
        contents1 = 0x830000
        mem.write(deck1_addr, _pack_ptr(deck_class))
        mem.write(deck1_addr + 0x10, _pack_ptr(summary1))
        mem.write(deck1_addr + 0x18, _pack_ptr(contents1))
        mem.write(contents1, _pack_ptr(contents_class))

        # Summary name (IL2CPP string)
        name1_bytes = _make_il2cpp_string("Aggro Red")
        mem.write(summary1, name1_bytes)

        # Piles dict (EDeckPile → List<{grpId, qty}>)
        piles_dict = 0x840000
        mem.write(contents1 + 0x10, _pack_ptr(piles_dict))
        piles_entries = 0x850000
        mem.write(piles_dict + 0x18, _pack_ptr(piles_entries))
        mem.write(piles_dict + 0x20, _pack_i32(1))
        mem.write(piles_entries + 0x18, _pack_i32(2))

        # Pile entry: key=1 (Main), value=List ptr, stride 24, value at +0x10
        pile_list = 0x860000
        entry_addr = piles_entries + 0x20
        mem.write(entry_addr, _pack_i32(7) + _pack_i32(-1) + _pack_i32(1) + b"\x00" * 4 + _pack_ptr(pile_list))

        # List<{grpId, qty}>: _items at +0x10, _size at +0x18
        items_array = 0x870000
        mem.write(pile_list + 0x10, _pack_ptr(items_array))
        mem.write(pile_list + 0x18, _pack_i32(3))
        mem.write(items_array + 0x18, _pack_i32(4))
        # Three cards
        for i, (grp_id, qty) in enumerate([(12345, 4), (67890, 3), (11111, 2)]):
            mem.write(items_array + 0x20 + i * 8, _pack_u32(grp_id) + _pack_i32(qty))

        # Deck 2: simpler, just name
        summary2 = 0x880000
        mem.write(deck2_addr, _pack_ptr(deck_class))
        mem.write(deck2_addr + 0x10, _pack_ptr(summary2))
        mem.write(deck2_addr + 0x18, _pack_ptr(0))  # no contents
        name2_bytes = _make_il2cpp_string("Control Blue")
        mem.write(summary2, name2_bytes)

        # Run scan
        reader = Il2CppReader(mem)
        result = scan_decks_il2cpp(reader, papa_instance=papa_instance, papa_class=papa_class)

        assert result is not None
        assert len(result.decks) >= 1
        # At least one deck should have the name "Aggro Red"
        named_decks = [d for d in result.decks if d.name]
        assert any("Aggro" in d.name for d in named_decks)
        # The first deck should have mainboard cards
        deck1 = next(d for d in result.decks if "Aggro" in d.name)
        assert 1 in deck1.piles  # PILE_MAIN
        main = deck1.piles[1]
        assert main.get(12345) == 4
        assert main.get(67890) == 3
        assert main.get(11111) == 2


class TestDataSegmentDiscovery:
    """Tests for automatically finding the IL2CPP data segment base."""

    class _FakeSection:
        def __init__(self, name: str, address: int) -> None:
            self._name = name
            self._address = address

        def get_name(self) -> str:
            return self._name

        def get_address(self) -> int:
            return self._address

    class _FakeModule:
        def __init__(self, name: str, address: int, sections: list["TestDataSegmentDiscovery._FakeSection"]) -> None:
            self._name = name
            self._address = address
            self._sections = sections

        def get_name(self) -> str:
            return self._name

        def get_address(self) -> int:
            return self._address

        def get_sections(self) -> list["TestDataSegmentDiscovery._FakeSection"]:
            return self._sections

    class _FakePm:
        def __init__(self, modules: list["TestDataSegmentDiscovery._FakeModule"]) -> None:
            self._modules = modules

        def get_modules(self, extended: bool = False):
            return self._modules

    def test_discover_data_segment_base_from_macho_header(self):
        mem = MockMemory()
        image_base = 0x100000000
        data_base = image_base + 0x200000
        type_info_table_ptr = data_base + IL2CPP_OFFSETS["type_info_table_offset"]
        type_info_table = 0x220000

        header = _make_macho_header([
            _make_segment_64("__TEXT", image_base, 0x100000),
            _make_segment_64("__DATA_CONST", data_base, 0x80000),
            _make_segment_64("__LINKEDIT", image_base + 0x300000, 0x20000),
        ])
        mem.write(image_base, header)

        papa_name = 0x200000
        papa_class = 0x210000
        mem.write(papa_name, b"PAPA\x00")
        mem.write(papa_class, _make_class(name_ptr=papa_name, fields_ptr=0, field_count=0))
        mem.write(type_info_table, _pack_ptr(papa_class))
        mem.write(type_info_table_ptr, _pack_ptr(type_info_table))

        result = _discover_data_segment_base_from_image(mem, image_base)

        assert result == data_base

    def test_find_game_assembly_base_prefers_data_section(self):
        pm = self._FakePm([
            self._FakeModule(
                "/Applications/MTGA.app/Contents/MacOS/GameAssembly.dylib",
                0x100000000,
                [
                    self._FakeSection("__TEXT", 0x100000000),
                    self._FakeSection("__DATA_CONST", 0x100200000),
                ],
            )
        ])

        assert _find_game_assembly_base(pm) == 0x100200000


# ---------------------------------------------------------------------------
# IL2CPP offsets constants
# ---------------------------------------------------------------------------

class TestIl2CppOffsets:
    """Verify IL2CPP offset constants match the Rust implementation."""

    def test_class_name_offset(self):
        assert IL2CPP_OFFSETS["class_name"] == 0x10

    def test_class_fields_offset(self):
        assert IL2CPP_OFFSETS["class_fields"] == 0x80

    def test_class_static_fields_offset(self):
        assert IL2CPP_OFFSETS["class_static_fields"] == 0xA8

    def test_field_info_size(self):
        assert IL2CPP_OFFSETS["field_info_size"] == 32

    def test_field_offset_in_fieldinfo(self):
        assert IL2CPP_OFFSETS["field_offset"] == 0x18

    def test_type_info_table_offset(self):
        assert IL2CPP_OFFSETS["type_info_table_offset"] == 0x24360

    def test_string_length_offset(self):
        assert IL2CPP_OFFSETS["string_length"] == 0x10

    def test_string_chars_offset(self):
        assert IL2CPP_OFFSETS["string_chars"] == 0x14

    def test_array_length_offset(self):
        assert IL2CPP_OFFSETS["array_length"] == 0x18

    def test_array_elements_offset(self):
        assert IL2CPP_OFFSETS["array_elements"] == 0x20

    def test_dict_entries_offset(self):
        assert IL2CPP_OFFSETS["dict_entries"] == 0x18

    def test_dict_count_offset(self):
        assert IL2CPP_OFFSETS["dict_count"] == 0x20

    def test_list_items_offset(self):
        assert IL2CPP_OFFSETS["list_items"] == 0x10

    def test_list_size_offset(self):
        assert IL2CPP_OFFSETS["list_size"] == 0x18


# ---------------------------------------------------------------------------
# Backref-based class & instance discovery (metadata-driven)
# ---------------------------------------------------------------------------

class TestFindStringInMetadataRegions:
    """Tests for locating a NUL-delimited string in global-metadata.dat."""

    def test_finds_string_between_nuls(self):
        mem = MockMemory()
        region_addr = 0x900000
        mem.write(region_addr, b"\x00garbage\x00_allDecks\x00more\x00")
        result = _find_string_in_metadata_regions(mem, [(region_addr, 64)], "_allDecks")
        assert result == region_addr + len(b"\x00garbage\x00")

    def test_returns_none_when_missing(self):
        mem = MockMemory()
        region_addr = 0x900000
        mem.write(region_addr, b"\x00SomethingElse\x00")
        result = _find_string_in_metadata_regions(mem, [(region_addr, 32)], "_allDecks")
        assert result is None

    def test_searches_multiple_regions_in_order(self):
        mem = MockMemory()
        region1 = 0x900000
        region2 = 0xA00000
        mem.write(region1, b"\x00NoMatchHere\x00")
        mem.write(region2, b"\x00DecksManager\x00")
        result = _find_string_in_metadata_regions(mem, [(region1, 32), (region2, 32)], "DecksManager")
        assert result == region2 + 1


class TestValidateClassStruct:
    """Tests for the Il2CppClass sanity-check used by backref discovery."""

    def test_accepts_well_formed_class(self):
        mem = MockMemory()
        name_addr = 0x110000
        mem.write(name_addr, b"DecksManager\x00")
        class_addr = 0x200000
        mem.write(class_addr, _make_class(name_ptr=name_addr, fields_ptr=0x300000, field_count=6))
        assert _validate_class_struct(mem, class_addr) is True
        assert _validate_class_struct(mem, class_addr, expected_name="DecksManager") is True

    def test_rejects_wrong_expected_name(self):
        mem = MockMemory()
        name_addr = 0x110000
        mem.write(name_addr, b"DecksManager\x00")
        class_addr = 0x200000
        mem.write(class_addr, _make_class(name_ptr=name_addr, fields_ptr=0x300000, field_count=6))
        assert _validate_class_struct(mem, class_addr, expected_name="PAPA") is False

    def test_rejects_implausible_field_count(self):
        mem = MockMemory()
        name_addr = 0x110000
        mem.write(name_addr, b"Bogus\x00")
        class_addr = 0x200000
        mem.write(class_addr, _make_class(name_ptr=name_addr, fields_ptr=0x300000, field_count=99999))
        assert _validate_class_struct(mem, class_addr) is False

    def test_rejects_missing_fields_ptr(self):
        mem = MockMemory()
        name_addr = 0x110000
        mem.write(name_addr, b"Bogus\x00")
        class_addr = 0x200000
        mem.write(class_addr, _make_class(name_ptr=name_addr, fields_ptr=0, field_count=3))
        assert _validate_class_struct(mem, class_addr) is False


class TestFindClassByFieldBackref:
    """Tests for resolving a class via a FieldInfo.name backref."""

    def test_finds_parent_class_via_field_name(self):
        mem = MockMemory()

        # global-metadata.dat string pool (mocked)
        metadata_addr = 0x900000
        mem.write(metadata_addr, b"\x00garbage\x00_allDecks\x00")
        field_name_addr = metadata_addr + len(b"\x00garbage\x00")

        # Parent class ("DeckDataProvider") — must pass _validate_class_struct
        class_name_addr = 0xA00000
        mem.write(class_name_addr, b"DeckDataProvider\x00")
        class_addr = 0xB00000
        mem.write(class_addr, _make_class(name_ptr=class_name_addr, fields_ptr=0xB10000, field_count=5))

        # FieldInfo entry referencing the field name and the parent class
        fieldinfo_region_addr = 0xC00000
        fieldinfo_entry = fieldinfo_region_addr + 0x40  # 8-byte aligned offset within region
        mem.write(fieldinfo_entry, _make_field_info(name_ptr=field_name_addr, offset=0x28))
        # _make_field_info leaves parent=0; patch it in directly.
        mem.write(fieldinfo_entry + 0x10, _pack_ptr(class_addr))

        result = _find_class_by_field_backref_in_regions(
            mem,
            metadata_regions=[(metadata_addr, 64)],
            fieldinfo_regions=[(fieldinfo_region_addr, fieldinfo_region_addr + 0x1000)],
            field_name="_allDecks",
        )
        assert result == class_addr

    def test_returns_none_when_field_name_not_in_metadata(self):
        mem = MockMemory()
        metadata_addr = 0x900000
        mem.write(metadata_addr, b"\x00Unrelated\x00")
        result = _find_class_by_field_backref_in_regions(
            mem,
            metadata_regions=[(metadata_addr, 32)],
            fieldinfo_regions=[(0xC00000, 0xC01000)],
            field_name="_allDecks",
        )
        assert result is None

    def test_returns_none_when_no_fieldinfo_references_string(self):
        mem = MockMemory()
        metadata_addr = 0x900000
        mem.write(metadata_addr, b"\x00_allDecks\x00")
        result = _find_class_by_field_backref_in_regions(
            mem,
            metadata_regions=[(metadata_addr, 32)],
            fieldinfo_regions=[(0xC00000, 0xC01000)],
            field_name="_allDecks",
        )
        assert result is None


class TestFindClassByNameInArena:
    """Tests for finding a sibling class within a known class-heap arena."""

    def test_finds_class_by_name_string_xref(self):
        mem = MockMemory()

        metadata_addr = 0x900000
        mem.write(metadata_addr, b"\x00garbage\x00DecksManager\x00")
        name_addr = metadata_addr + len(b"\x00garbage\x00")

        arena_addr = 0xD00000
        arena_size = 0x100000
        class_addr = arena_addr + 0x2000
        # class_name field (+0x10) holds a pointer to name_addr
        mem.write(class_addr + IL2CPP_OFFSETS["class_name"], _pack_ptr(name_addr))
        mem.write(class_addr + IL2CPP_OFFSETS["class_fields"], _pack_ptr(0xE00000))
        mem.write(class_addr + IL2CPP_OFFSETS["class_field_count"], _pack_u32(6))

        result = _find_class_by_name_in_arena(
            mem, [(metadata_addr, 64)], arena_addr, arena_size, "DecksManager",
        )
        assert result == class_addr

    def test_returns_none_outside_arena_bounds(self):
        mem = MockMemory()
        metadata_addr = 0x900000
        mem.write(metadata_addr, b"\x00DecksManager\x00")
        name_addr = metadata_addr + 1

        # Class lives OUTSIDE the searched arena bounds.
        class_addr = 0xF00000
        mem.write(class_addr + IL2CPP_OFFSETS["class_name"], _pack_ptr(name_addr))
        mem.write(class_addr + IL2CPP_OFFSETS["class_fields"], _pack_ptr(0xE00000))
        mem.write(class_addr + IL2CPP_OFFSETS["class_field_count"], _pack_u32(6))

        result = _find_class_by_name_in_arena(
            mem, [(metadata_addr, 32)], 0xD00000, 0x1000, "DecksManager",
        )
        assert result is None


class TestFindInstanceByKlass:
    """Tests for the writable-heap wide instance scan."""

    def test_finds_instance_with_matching_klass_pointer(self):
        mem = MockMemory()
        klass = 0xAABBCC00
        region_addr = 0x1000000
        instance_addr = region_addr + 0x40
        mem.write(instance_addr, _pack_ptr(klass))

        result = _find_instance_by_klass_in_regions(mem, [(region_addr, 0x1000)], klass)
        assert result == instance_addr

    def test_validate_callback_rejects_false_positive(self):
        mem = MockMemory()
        klass = 0xAABBCC00
        region_addr = 0x1000000
        bad_addr = region_addr + 0x40
        good_addr = region_addr + 0x100
        mem.write(bad_addr, _pack_ptr(klass))
        mem.write(good_addr, _pack_ptr(klass))

        result = _find_instance_by_klass_in_regions(
            mem, [(region_addr, 0x1000)], klass,
            validate=lambda addr: addr == good_addr,
        )
        assert result == good_addr

    def test_returns_none_when_not_found(self):
        mem = MockMemory()
        result = _find_instance_by_klass_in_regions(mem, [(0x1000000, 0x1000)], 0xDEADBEEF)
        assert result is None

    def test_ignores_misaligned_matches(self):
        mem = MockMemory()
        klass = 0xAABBCC00
        region_addr = 0x1000000
        # Write the pointer at a non-8-byte-aligned offset within the region.
        mem.write(region_addr + 3, _pack_ptr(klass))

        result = _find_instance_by_klass_in_regions(mem, [(region_addr, 0x1000)], klass)
        assert result is None
