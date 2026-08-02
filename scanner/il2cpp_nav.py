"""IL2CPP Navigation Scanner for MTGA deck data.

Navigates the IL2CPP runtime type system to find and read deck data:
  TypeInfoTable → PAPA class → PAPA instance → DecksManager
  → _deckDataProvider → _allDecks (Dictionary<uint, Client_Deck*>)
  → Client_Deck → _contents → Piles (Dictionary<EDeckPile, List<{grpId, qty}>>)

This mirrors the Rust implementation in mtgatool's napi/mod.rs (macOS backend),
but operates from Python using the same mach_vm_read_overwrite primitives via
the existing pattern_scanner._read_bytes_silent infrastructure.

All offsets are verified against the Unity 2021.x IL2CPP metadata v29 layout
used by MTGA, as documented in offsets.rs and napi/mod.rs::offsets.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# IL2CPP structure offsets (Unity 2021.x, MTGA-verified)
# ---------------------------------------------------------------------------

IL2CPP_OFFSETS: dict[str, int] = {
    # Il2CppClass
    "class_image": 0x0,
    "class_name": 0x10,
    "class_namespace": 0x18,
    "class_parent": 0x48,
    "class_fields": 0x80,
    "class_field_count": 0x124,
    "class_static_fields": 0xA8,
    "class_methods": 0x88,
    "class_instance_size": 0xF8,
    "class_flags": 0xFC,
    "class_type_definition": 0x68,
    "class_generic_class": 0x50,

    # Il2CppFieldInfo (32 bytes each)
    "field_info_size": 32,
    "field_name": 0x0,
    "field_type": 0x8,
    "field_parent": 0x10,
    "field_offset": 0x18,

    # Global pointer offsets (from second __DATA segment base)
    "type_info_table_offset": 0x24360,

    # Il2CppString
    "string_length": 0x10,
    "string_chars": 0x14,

    # Il2CppArray
    "array_length": 0x18,
    "array_elements": 0x20,

    # Dictionary<TKey, TValue> (Mono/IL2CPP internal layout)
    "dict_entries": 0x18,
    "dict_count": 0x20,

    # List<T> (Mono/IL2CPP internal layout)
    "list_items": 0x10,
    "list_size": 0x18,
}

# Navigation field-name chains — the exact C# field names in MTGA's IL2CPP
PAPA_TO_DECKS_PATH = ["DecksManager", "_deckDataProvider", "_allDecks"]

# Deck structure field names
DECK_SUMMARY_FIELD = "_summary"
DECK_CONTENTS_FIELD = "_contents"
CONTENTS_PILES_FIELD = "Piles"
SUMMARY_NAME_FIELD = "Name"

# Pile types (EDeckPile enum values in MTGA)
PILE_MAIN = 1
PILE_SIDEBOARD = 2
PILE_COMMANDZONE = 3
PILE_COMPANIONS = 4
VALID_PILE_TYPES = frozenset({PILE_MAIN, PILE_SIDEBOARD, PILE_COMMANDZONE, PILE_COMPANIONS})

# Card grpId/qty validation
MIN_GRP_ID = 1000
MAX_GRP_ID = 500000
MIN_QTY = 1
MAX_QTY = 400

# Heap regions for PAPA instance scan (matching Rust find_papa_instance)
PAPA_HEAP_REGIONS = [
    (0x15A000000, 0x15B000000),
    (0x158000000, 0x16A000000),
    (0x145000000, 0x150000000),
]
PAPA_SCAN_STEP = 0x100000  # 1 MB chunks
PAPA_INVMGR_OFFSET = 224  # +0xE0: InventoryManager pointer

# Limits
MAX_CLASSES_TO_SCAN = 50000
MAX_DICT_ENTRIES = 5000
MAX_LIST_ELEMENTS = 1000
MIN_VALID_PTR = 0x100000
MAX_VALID_PTR = 0x400000000


# ---------------------------------------------------------------------------
# Memory reader protocol — works with both MockMemory (tests) and Pymem (live)
# ---------------------------------------------------------------------------

class MemoryReader(Protocol):
    """Protocol for memory reading backends."""

    def read_bytes(self, addr: int, size: int) -> bytes: ...


# ---------------------------------------------------------------------------
# Mock memory — for testing without a live MTGA process
# ---------------------------------------------------------------------------

class MockMemory:
    """In-memory byte store for testing. Implements MemoryReader protocol.

    Uses a list of (start, bytearray) regions for efficient bulk reads.
    Writes grow or update existing regions; reads span regions with zero-fill.
    """

    def __init__(self) -> None:
        self._regions: list[tuple[int, bytearray]] = []

    def write(self, addr: int, data: bytes) -> None:
        """Write bytes at the given address."""
        if not data:
            return
        # Try to merge with an existing adjacent/overlapping region
        for i, (start, buf) in enumerate(self._regions):
            end = start + len(buf)
            if addr <= end and addr + len(data) >= start:
                # Overlap or adjacency — extend/merge
                new_start = min(start, addr)
                new_end = max(end, addr + len(data))
                new_buf = bytearray(new_end - new_start)
                # Copy old data
                off = start - new_start
                new_buf[off:off + len(buf)] = buf
                # Copy new data
                off2 = addr - new_start
                new_buf[off2:off2 + len(data)] = data
                self._regions[i] = (new_start, new_buf)
                return
        # New region
        self._regions.append((addr, bytearray(data)))

    def read_bytes(self, addr: int, size: int) -> bytes:
        """Read bytes from the given address. Uninitialized bytes are zero."""
        result = bytearray(size)
        for start, buf in self._regions:
            end = start + len(buf)
            # Calculate overlap
            r_start = max(addr, start)
            r_end = min(addr + size, end)
            if r_start < r_end:
                src_off = r_start - start
                dst_off = r_start - addr
                length = r_end - r_start
                result[dst_off:dst_off + length] = buf[src_off:src_off + length]
        return bytes(result)

    def read_ptr(self, addr: int) -> int:
        """Read a 64-bit pointer (little-endian)."""
        raw = self.read_bytes(addr, 8)
        return struct.unpack_from("<Q", raw)[0]

    def read_u32(self, addr: int) -> int:
        raw = self.read_bytes(addr, 4)
        return struct.unpack_from("<I", raw)[0]

    def read_i32(self, addr: int) -> int:
        raw = self.read_bytes(addr, 4)
        return struct.unpack_from("<i", raw)[0]

    def read_string(self, addr: int) -> str:
        """Read a null-terminated ASCII string (for IL2CPP class/field names)."""
        if addr == 0:
            return ""
        raw = self.read_bytes(addr, 256)
        end = raw.find(b"\x00")
        if end == -1:
            end = len(raw)
        return raw[:end].decode("ascii", errors="replace")


# ---------------------------------------------------------------------------
# Il2CppReader — wraps a MemoryReader with IL2CPP-aware read methods
# ---------------------------------------------------------------------------

class Il2CppReader:
    """Wraps a MemoryReader with IL2CPP-aware reading methods.

    Works with both MockMemory (tests) and Pymem (live process, via
    pattern_scanner._read_bytes_silent).
    """

    def __init__(self, mem: MemoryReader) -> None:
        self.mem = mem

    def read_bytes(self, addr: int, size: int) -> bytes:
        return self.mem.read_bytes(addr, size)

    def read_ptr(self, addr: int) -> int:
        raw = self.read_bytes(addr, 8)
        return struct.unpack_from("<Q", raw)[0]

    def read_u32(self, addr: int) -> int:
        raw = self.read_bytes(addr, 4)
        return struct.unpack_from("<I", raw)[0]

    def read_i32(self, addr: int) -> int:
        raw = self.read_bytes(addr, 4)
        return struct.unpack_from("<i", raw)[0]

    def read_string_ascii(self, addr: int) -> str:
        """Read a null-terminated ASCII string (IL2CPP class/field names)."""
        if addr == 0:
            return ""
        raw = self.read_bytes(addr, 256)
        end = raw.find(b"\x00")
        if end == -1:
            end = len(raw)
        return raw[:end].decode("ascii", errors="replace")


# ---------------------------------------------------------------------------
# IL2CPP string reading (UTF-16LE with length prefix)
# ---------------------------------------------------------------------------

def read_il2cpp_string(mem: MemoryReader, addr: int) -> str:
    """Read an IL2CPP/Mono C# string.

    Layout:
      +0x00: class_ptr (8 bytes, not checked)
      +0x10: length (i32, character count, not byte count)
      +0x14: chars (UTF-16LE encoded, `length` characters)

    Args:
        mem: Memory reader.
        addr: Address of the string object.

    Returns:
        The decoded string, or "" if addr is 0 or invalid.
    """
    if addr == 0:
        return ""

    # Read length field at +0x10
    length_raw = mem.read_bytes(addr + IL2CPP_OFFSETS["string_length"], 4)
    char_count = struct.unpack_from("<i", length_raw)[0]
    if char_count <= 0 or char_count > 10000:
        return ""

    # Read UTF-16LE characters at +0x14
    char_bytes = mem.read_bytes(addr + IL2CPP_OFFSETS["string_chars"], char_count * 2)
    try:
        return char_bytes.decode("utf-16-le", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# Class finding via TypeInfoTable
# ---------------------------------------------------------------------------

@dataclass
class FieldInfo:
    """IL2CPP field metadata."""
    name: str
    type_name: str
    offset: int
    is_static: bool = False


def find_class_by_name(
    mem: MemoryReader,
    type_info_table: int,
    name: str,
    *,
    max_classes: int = MAX_CLASSES_TO_SCAN,
) -> int | None:
    """Find an IL2CPP class by its name via the TypeInfoTable.

    The TypeInfoTable is an array of Il2CppClass* pointers. We iterate through
    it, reading each class's name and comparing.

    Args:
        mem: Memory reader.
        type_info_table: Address of the TypeInfoTable (Il2CppClass**).
        name: Class name to search for (e.g. "PAPA").
        max_classes: Maximum number of entries to scan.

    Returns:
        Address of the Il2CppClass structure, or None if not found.
    """
    for i in range(max_classes):
        class_ptr = _read_ptr(mem, type_info_table + i * 8)
        if class_ptr == 0:
            continue
        if class_ptr < MIN_VALID_PTR:
            continue

        name_ptr = _read_ptr(mem, class_ptr + IL2CPP_OFFSETS["class_name"])
        if name_ptr == 0 or name_ptr < MIN_VALID_PTR:
            continue

        class_name = _read_string_ascii(mem, name_ptr)
        if class_name == name:
            return class_ptr

    return None


def get_class_fields(mem: MemoryReader, class_addr: int) -> list[FieldInfo]:
    """Read IL2CPP class field metadata.

    Iterates through the FieldInfo array of a class, reading each field's
    name, type, and offset.

    Args:
        mem: Memory reader.
        class_addr: Address of the Il2CppClass structure.

    Returns:
        List of FieldInfo objects. Empty if class has no fields or fields_ptr
        is invalid.
    """
    if class_addr == 0 or class_addr < MIN_VALID_PTR:
        return []

    fields_ptr = _read_ptr(mem, class_addr + IL2CPP_OFFSETS["class_fields"])
    if fields_ptr == 0 or fields_ptr < MIN_VALID_PTR:
        return []

    field_count_raw = mem.read_bytes(class_addr + IL2CPP_OFFSETS["class_field_count"], 4)
    field_count = struct.unpack_from("<I", field_count_raw)[0]
    if field_count == 0 or field_count > 200:
        field_count = 50  # fallback to scanning up to 50

    fields: list[FieldInfo] = []
    field_info_size = IL2CPP_OFFSETS["field_info_size"]

    for i in range(field_count):
        field_addr = fields_ptr + i * field_info_size
        name_ptr = _read_ptr(mem, field_addr + IL2CPP_OFFSETS["field_name"])
        if name_ptr == 0 or name_ptr < MIN_VALID_PTR:
            break  # end of field list

        field_name = _read_string_ascii(mem, name_ptr)
        if not field_name:
            break

        offset_raw = mem.read_bytes(field_addr + IL2CPP_OFFSETS["field_offset"], 4)
        field_offset = struct.unpack_from("<I", offset_raw)[0]

        # Read type info for static detection
        type_ptr = _read_ptr(mem, field_addr + IL2CPP_OFFSETS["field_type"])
        is_static = False
        type_name = "Unknown"
        if type_ptr > MIN_VALID_PTR:
            type_attrs_raw = mem.read_bytes(type_ptr + IL2CPP_OFFSETS["field_type"] + 8, 4)
            # TYPE_ATTRS is at type_ptr + 0x08
            type_attrs_raw = mem.read_bytes(type_ptr + 0x08, 4)
            type_attrs = struct.unpack_from("<I", type_attrs_raw)[0]
            is_static = (type_attrs & 0x10) != 0

            type_data = _read_ptr(mem, type_ptr)
            if type_data > MIN_VALID_PTR:
                tn = _read_string_ascii_at(mem, type_data)
                if tn:
                    type_name = tn

        fields.append(FieldInfo(
            name=field_name,
            type_name=type_name,
            offset=field_offset,
            is_static=is_static,
        ))

    return fields


def _read_string_ascii_at(mem: MemoryReader, addr: int) -> str:
    """Read a null-terminated ASCII string at addr (for type class names)."""
    if addr == 0 or addr < MIN_VALID_PTR:
        return ""
    raw = mem.read_bytes(addr + IL2CPP_OFFSETS["class_name"], 256)
    # For class names, the name pointer is at class+0x10
    name_ptr = _read_ptr(mem, addr + IL2CPP_OFFSETS["class_name"])
    if name_ptr > MIN_VALID_PTR:
        return _read_string_ascii(mem, name_ptr)
    return ""


# ---------------------------------------------------------------------------
# PAPA instance finding (heap scan)
# ---------------------------------------------------------------------------

def find_papa_instance(mem: MemoryReader, papa_class: int) -> int | None:
    """Find the PAPA singleton instance by scanning heap regions.

    Mirrors the Rust find_papa_instance: scans specific heap address ranges
    for objects whose first 8 bytes (class pointer) match papa_class. Then
    validates by checking that:
      - val_at_16 (obj+16) is NOT papa_class and is > MIN_VALID_PTR
      - inv_mgr (obj+224) is a valid pointer
      - The class name at inv_mgr's class contains "InventoryManager"

    Args:
        mem: Memory reader.
        papa_class: Address of the PAPA Il2CppClass.

    Returns:
        Address of the PAPA instance, or None if not found.
    """
    for region_start, region_end in PAPA_HEAP_REGIONS:
        for chunk_start in range(region_start, region_end, PAPA_SCAN_STEP):
            chunk = mem.read_bytes(chunk_start, PAPA_SCAN_STEP)
            if not chunk or all(b == 0 for b in chunk):
                continue

            # Scan for papa_class pointer at 8-byte alignment
            for i in range(0, len(chunk) - 8, 8):
                ptr = struct.unpack_from("<Q", chunk, i)[0]
                if ptr != papa_class:
                    continue

                obj_addr = chunk_start + i

                # Check val_at_16: must not be papa_class, must be valid
                val_at_16 = _read_ptr(mem, obj_addr + 16)
                if val_at_16 == papa_class or val_at_16 <= MIN_VALID_PTR:
                    continue

                # Check InventoryManager at +224
                inv_mgr = _read_ptr(mem, obj_addr + PAPA_INVMGR_OFFSET)
                if inv_mgr <= MIN_VALID_PTR or inv_mgr >= MAX_VALID_PTR:
                    continue

                # Verify inv_mgr's class contains "InventoryManager"
                inv_class = _read_ptr(mem, inv_mgr)
                if inv_class <= MIN_VALID_PTR:
                    continue

                inv_name_ptr = _read_ptr(mem, inv_class + IL2CPP_OFFSETS["class_name"])
                if inv_name_ptr <= MIN_VALID_PTR:
                    continue

                inv_name = _read_string_ascii(mem, inv_name_ptr)
                if "InventoryManager" in inv_name:
                    return obj_addr

    return None


# ---------------------------------------------------------------------------
# Dictionary<uint, ptr> reading
# ---------------------------------------------------------------------------

def read_dictionary_uint_ptr(
    mem: MemoryReader,
    dict_addr: int,
    *,
    entry_stride: int = 24,
    value_offset: int = 0x10,
) -> list[tuple[int, int]]:
    """Read entries from an IL2CPP Dictionary<uint, T*> where T is a pointer.

    Dictionary internal layout:
      +0x18: _entries (Il2CppArray*)
      +0x20: _count (i32, active entry count)

    Entry layout (Entry struct, stride varies by Mono/IL2CPP version):
      +0x00: hashCode (i32, >= 0 means active slot)
      +0x04: next (i32, -1 for end of chain)
      +0x08: key (u32)
      +0x10: value (ptr, 8 bytes)  [at value_offset within entry]

    Args:
        mem: Memory reader.
        dict_addr: Address of the Dictionary object.
        entry_stride: Byte size of each Entry struct (24 or 32).
        value_offset: Offset of the value pointer within each entry.

    Returns:
        List of (key, value_ptr) tuples for active entries.
    """
    if dict_addr == 0 or dict_addr < MIN_VALID_PTR:
        return []

    entries_array = _read_ptr(mem, dict_addr + IL2CPP_OFFSETS["dict_entries"])
    if entries_array == 0 or entries_array < MIN_VALID_PTR:
        return []

    count = _read_i32(mem, dict_addr + IL2CPP_OFFSETS["dict_count"])
    if count <= 0:
        return []

    count = min(count, MAX_DICT_ENTRIES)

    # Array: +0x18 = length, +0x20 = data start
    array_len = _read_i32(mem, entries_array + IL2CPP_OFFSETS["array_length"])
    capacity = max(array_len, count)

    results: list[tuple[int, int]] = []
    data_start = entries_array + IL2CPP_OFFSETS["array_elements"]

    for i in range(capacity):
        entry_addr = data_start + i * entry_stride
        hash_code = _read_i32(mem, entry_addr)
        if hash_code < 0:
            continue  # free slot

        key = _read_u32(mem, entry_addr + 0x08)
        if key == 0:
            continue

        value_ptr = _read_ptr(mem, entry_addr + value_offset)
        if value_ptr < MIN_VALID_PTR:
            continue

        results.append((key, value_ptr))

        if len(results) >= count:
            break

    return results


# ---------------------------------------------------------------------------
# List<T> of structs reading
# ---------------------------------------------------------------------------

def read_list_of_structs(
    mem: MemoryReader,
    list_addr: int,
    *,
    element_size: int = 8,
) -> list[tuple[int, int]]:
    """Read a List<{grpId, qty}> from IL2CPP memory.

    List internal layout:
      +0x10: _items (Il2CppArray*)
      +0x18: _size (i32, actual count)

    Array layout:
      +0x18: length (capacity)
      +0x20: data start

    Each element: grpId (u32) + qty (i32) = 8 bytes.

    Args:
        mem: Memory reader.
        list_addr: Address of the List<T> object.
        element_size: Size of each element in bytes (default 8 for grpId+qty).

    Returns:
        List of (grpId, quantity) tuples, filtered by MIN/MAX_GRP_ID and
        MIN/MAX_QTY.
    """
    if list_addr == 0 or list_addr < MIN_VALID_PTR:
        return []

    items_array = _read_ptr(mem, list_addr + IL2CPP_OFFSETS["list_items"])
    if items_array == 0 or items_array < MIN_VALID_PTR:
        return []

    size = _read_i32(mem, list_addr + IL2CPP_OFFSETS["list_size"])
    if size <= 0:
        return []

    size = min(size, MAX_LIST_ELEMENTS)

    data_start = items_array + IL2CPP_OFFSETS["array_elements"]
    results: list[tuple[int, int]] = []

    for i in range(size):
        elem_addr = data_start + i * element_size
        grp_id = _read_u32(mem, elem_addr)
        qty = _read_i32(mem, elem_addr + 4)

        if MIN_GRP_ID <= grp_id <= MAX_GRP_ID and MIN_QTY <= qty <= MAX_QTY:
            results.append((grp_id, qty))

    return results


# ---------------------------------------------------------------------------
# Navigation: PAPA → _allDecks
# ---------------------------------------------------------------------------

@dataclass
class DeckNavResult:
    """Result of navigating from PAPA to the _allDecks dictionary."""
    dict_addr: int
    entries: list[tuple[int, int]]  # (deck_id, deck_ptr)


def navigate_to_all_decks(
    mem: MemoryReader,
    papa_instance: int,
    papa_class: int,
    *,
    path: list[str] | None = None,
) -> DeckNavResult | None:
    """Navigate from a PAPA instance to the _allDecks dictionary.

    Follows the field chain: PAPA → DecksManager → _deckDataProvider → _allDecks

    At each step:
      1. Read the current object's class pointer
      2. Look up the target field by name in the class's field list
      3. Read the pointer at instance + field.offset
      4. Move to that pointer

    The final step reads the Dictionary<uint, Client_Deck*> at _allDecks.

    Args:
        mem: Memory reader.
        papa_instance: Address of the PAPA singleton instance.
        papa_class: Address of the PAPA Il2CppClass (for field lookup).
        path: Field name chain to follow (default: PAPA_TO_DECKS_PATH).

    Returns:
        DeckNavResult with dict address and entries, or None if navigation
        fails at any step.
    """
    if path is None:
        path = PAPA_TO_DECKS_PATH

    current_addr = papa_instance
    current_class = _read_ptr(mem, current_addr)
    if current_class == 0 or current_class < MIN_VALID_PTR:
        return None

    # First step uses papa_class (already known), subsequent steps read from instance
    for i, field_name in enumerate(path):
        if i == 0:
            class_addr = papa_class
        else:
            class_addr = current_class

        if class_addr == 0 or class_addr < MIN_VALID_PTR:
            return None

        fields = get_class_fields(mem, class_addr)
        target_field = next((f for f in fields if f.name == field_name), None)
        if target_field is None:
            return None

        field_ptr = _read_ptr(mem, current_addr + target_field.offset)
        if field_ptr == 0 or field_ptr < MIN_VALID_PTR:
            return None

        current_addr = field_ptr
        current_class = _read_ptr(mem, current_addr)

    # current_addr is now the _allDecks dictionary
    entries = read_dictionary_uint_ptr(mem, current_addr)
    return DeckNavResult(dict_addr=current_addr, entries=entries)


# ---------------------------------------------------------------------------
# Deck reading: Client_Deck → piles
# ---------------------------------------------------------------------------

@dataclass
class Il2CppDeckResult:
    """A single deck read via IL2CPP navigation."""
    deck_id: int = 0
    name: str = ""
    piles: dict[int, dict[int, int]] = field(default_factory=dict)
    raw_address: int = 0


def read_deck(
    mem: MemoryReader,
    deck_addr: int,
    deck_class: int | None = None,
) -> Il2CppDeckResult | None:
    """Read a Client_Deck from memory.

    Client_Deck structure:
      +field_offset(_summary): DeckSummary* (contains Name string)
      +field_offset(_contents): Client_DeckContents* (contains Piles dict)

    Piles is a Dictionary<EDeckPile, List<{grpId, qty}>*>:
      - Key: EDeckPile enum (1=Main, 2=Sideboard, 3=CommandZone, 4=Companions)
      - Value: List<{grpId, qty}>*

    Args:
        mem: Memory reader.
        deck_addr: Address of the Client_Deck instance.
        deck_class: Optional pre-read class pointer for the deck.

    Returns:
        Il2CppDeckResult with name and piles, or None if deck is invalid.
    """
    if deck_addr == 0 or deck_addr < MIN_VALID_PTR:
        return None

    if deck_class is None:
        deck_class = _read_ptr(mem, deck_addr)
    if deck_class == 0 or deck_class < MIN_VALID_PTR:
        return None

    fields = get_class_fields(mem, deck_class)

    summary_field = next((f for f in fields if f.name == DECK_SUMMARY_FIELD), None)
    contents_field = next((f for f in fields if f.name == DECK_CONTENTS_FIELD), None)

    result = Il2CppDeckResult(raw_address=deck_addr)

    # Read summary (name)
    if summary_field is not None:
        summary_ptr = _read_ptr(mem, deck_addr + summary_field.offset)
        if summary_ptr > MIN_VALID_PTR:
            name = _read_deck_name(mem, summary_ptr)
            if name:
                result.name = name

    # Read contents (piles)
    if contents_field is not None:
        contents_ptr = _read_ptr(mem, deck_addr + contents_field.offset)
        if contents_ptr > MIN_VALID_PTR:
            piles = _read_deck_piles(mem, contents_ptr)
            if piles:
                result.piles = piles

    return result


def _read_deck_name(mem: MemoryReader, summary_addr: int) -> str:
    """Read a deck name from a DeckSummary object.

    Tries the "Name" field first. If the summary object is actually an
    IL2CPP string (some deck variants store the string directly), reads
    it as a string.
    """
    if summary_addr == 0 or summary_addr < MIN_VALID_PTR:
        return ""

    # Try reading as IL2CPP string directly (some layouts)
    name = read_il2cpp_string(mem, summary_addr)
    if name:
        return name

    # Otherwise, navigate to the Name field
    summary_class = _read_ptr(mem, summary_addr)
    if summary_class < MIN_VALID_PTR:
        return ""

    fields = get_class_fields(mem, summary_class)
    name_field = next((f for f in fields if f.name == SUMMARY_NAME_FIELD), None)

    if name_field is not None:
        name_ptr = _read_ptr(mem, summary_addr + name_field.offset)
        if name_ptr > MIN_VALID_PTR:
            return read_il2cpp_string(mem, name_ptr)

    return ""


def _read_deck_piles(mem: MemoryReader, contents_addr: int) -> dict[int, dict[int, int]]:
    """Read pile data from a Client_DeckContents object.

    Navigates: contents → Piles field → Dictionary<EDeckPile, List<...>*>
    """
    if contents_addr == 0 or contents_addr < MIN_VALID_PTR:
        return {}

    contents_class = _read_ptr(mem, contents_addr)
    if contents_class < MIN_VALID_PTR:
        return {}

    fields = get_class_fields(mem, contents_class)
    piles_field = next((f for f in fields if f.name == CONTENTS_PILES_FIELD), None)

    if piles_field is None:
        return {}

    piles_dict_addr = _read_ptr(mem, contents_addr + piles_field.offset)
    if piles_dict_addr < MIN_VALID_PTR:
        return {}

    # Read Dictionary<int, List*>
    pile_entries = read_dictionary_uint_ptr(mem, piles_dict_addr)

    result: dict[int, dict[int, int]] = {}
    for pile_type, list_ptr in pile_entries:
        if pile_type not in VALID_PILE_TYPES:
            continue

        cards = read_list_of_structs(mem, list_ptr)
        if cards:
            result.setdefault(pile_type, {})
            for grp_id, qty in cards:
                result[pile_type][grp_id] = qty

    return result


# ---------------------------------------------------------------------------
# Full scan: scan_decks_il2cpp
# ---------------------------------------------------------------------------

@dataclass
class Il2CppScanResult:
    """Result of a full IL2CPP deck scan."""
    decks: list[Il2CppDeckResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    papa_instance: int = 0
    all_decks_addr: int = 0


def scan_decks_il2cpp(
    reader: Il2CppReader | MemoryReader,
    *,
    papa_instance: int = 0,
    papa_class: int = 0,
    type_info_table: int = 0,
    data_segment_base: int = 0,
) -> Il2CppScanResult | None:
    """Scan for MTGA decks via IL2CPP memory navigation.

    This is the main entry point for the IL2CPP deck scanner. It either uses
    pre-discovered addresses (papa_instance, papa_class) or discovers them
    from the TypeInfoTable.

    Args:
        reader: Il2CppReader or MemoryReader instance.
        papa_instance: Pre-discovered PAPA instance address (0 = discover).
        papa_class: Pre-discovered PAPA class address (0 = discover).
        type_info_table: Pre-discovered TypeInfoTable address (0 = discover).
        data_segment_base: GameAssembly __DATA segment base (for discovery).

    Returns:
        Il2CppScanResult with found decks, or None on critical failure.
    """
    # Normalize reader
    if isinstance(reader, Il2CppReader):
        mem = reader.mem
    else:
        mem = reader

    warnings: list[str] = []

    # Discover PAPA class if not provided
    if papa_class == 0:
        if type_info_table == 0:
            if data_segment_base == 0:
                warnings.append("no data_segment_base provided for class discovery")
                return Il2CppScanResult(warnings=warnings)
            type_info_table = _read_ptr(mem, data_segment_base + IL2CPP_OFFSETS["type_info_table_offset"])
            if type_info_table == 0:
                warnings.append("TypeInfoTable not found at data_segment_base")
                return Il2CppScanResult(warnings=warnings)

        papa_class = find_class_by_name(mem, type_info_table, "PAPA")
        if papa_class is None:
            warnings.append("PAPA class not found in TypeInfoTable")
            return Il2CppScanResult(warnings=warnings)

    # Discover PAPA instance if not provided
    if papa_instance == 0:
        found = find_papa_instance(mem, papa_class)
        if found is None:
            warnings.append("PAPA instance not found in heap scan")
            return Il2CppScanResult(warnings=warnings)
        papa_instance = found

    # Navigate to _allDecks
    nav_result = navigate_to_all_decks(mem, papa_instance, papa_class)
    if nav_result is None:
        warnings.append("navigation from PAPA to _allDecks failed")
        return Il2CppScanResult(warnings=warnings, papa_instance=papa_instance)

    if not nav_result.entries:
        warnings.append("_allDecks dictionary is empty or unreadable")
        return Il2CppScanResult(
            warnings=warnings,
            papa_instance=papa_instance,
            all_decks_addr=nav_result.dict_addr,
        )

    # Read each deck
    decks: list[Il2CppDeckResult] = []
    for deck_id, deck_ptr in nav_result.entries:
        deck = read_deck(mem, deck_ptr)
        if deck is not None:
            deck.deck_id = deck_id
            decks.append(deck)

    if not decks:
        warnings.append("no decks could be parsed from _allDecks entries")

    return Il2CppScanResult(
        decks=decks,
        warnings=warnings,
        papa_instance=papa_instance,
        all_decks_addr=nav_result.dict_addr,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_ptr(mem: MemoryReader, addr: int) -> int:
    """Read a 64-bit pointer from memory."""
    raw = mem.read_bytes(addr, 8)
    return struct.unpack_from("<Q", raw)[0]


def _read_u32(mem: MemoryReader, addr: int) -> int:
    """Read a 32-bit unsigned integer from memory."""
    raw = mem.read_bytes(addr, 4)
    return struct.unpack_from("<I", raw)[0]


def _read_i32(mem: MemoryReader, addr: int) -> int:
    """Read a 32-bit signed integer from memory."""
    raw = mem.read_bytes(addr, 4)
    return struct.unpack_from("<i", raw)[0]


def _read_string_ascii(mem: MemoryReader, addr: int) -> str:
    """Read a null-terminated ASCII string from memory."""
    if addr == 0 or addr < MIN_VALID_PTR:
        return ""
    raw = mem.read_bytes(addr, 256)
    end = raw.find(b"\x00")
    if end == -1:
        end = len(raw)
    return raw[:end].decode("ascii", errors="replace")