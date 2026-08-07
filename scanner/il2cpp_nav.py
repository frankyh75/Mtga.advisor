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

import ctypes
import ctypes.util
import logging
import os
import struct
from functools import lru_cache
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from .pattern_scanner import _iterate_regions_with_error, _iterate_writable_private_regions

logger = logging.getLogger(__name__)

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

# Mach-O / dyld constants for macOS IL2CPP images.
MH_MAGIC_64 = 0xFEEDFACF
LC_SEGMENT_64 = 0x19
MACHO_HEADER_64_SIZE = 32
MACHO_HEADER_SCAN_SIZE = 0x8000
GAME_ASSEMBLY_HINTS = ("GameAssembly", "libGameAssembly")
DATA_SEGMENT_NAMES = ("__DATA_CONST", "__DATA", "__DATA_DIRTY")


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
            # `all(b == 0 for b in chunk)` iterates byte-by-byte in pure
            # Python — over a million iterations per 1 MB chunk. Comparing
            # against a same-length zero-filled bytes object instead uses
            # CPython's native memcmp, which is orders of magnitude faster
            # for the common case where a chunk is entirely unmapped/empty.
            if not chunk or chunk == bytes(len(chunk)):
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
# Backref-based class & instance discovery (metadata-driven)
#
# The TypeInfoTable/data_segment_base approach above assumes IL2CPP class
# metadata lives inside GameAssembly.dylib's own __DATA segment at a fixed
# offset. On macOS that assumption does not hold: Il2CppClass structs and
# their FieldInfo arrays are heap-allocated at runtime, in a separate arena
# outside GameAssembly's Mach-O segments entirely (verified live, see
# docs/deck-scan-problem-report.md). This section finds classes by the
# opposite direction — starting from a known field/class *name string* in
# global-metadata.dat's string pool and following pointers back to the
# structure that references it — instead of assuming any fixed offset.
# ---------------------------------------------------------------------------



def _bulk_read(mem: MemoryReader, base: int, size: int, chunk_size: int = 0x400000) -> bytes:
    """Read a large region in bounded chunks (single huge reads can fail)."""
    buf = bytearray()
    offset = 0
    while offset < size:
        n = min(chunk_size, size - offset)
        buf.extend(mem.read_bytes(base + offset, n))
        offset += n
    return bytes(buf)


def _find_metadata_regions(pm: Any) -> list[tuple[int, int]]:
    """Locate global-metadata.dat's mapped region(s) in the live process.

    Polymorph: unterstützt Pymem-Objekte (mit .task und mach_vm_region_recurse)
    UND Helper-basierte Adapter, die `iterate_readable_regions()` direkt anbieten
    oder bei denen die Region-Enumeration nur beschreibbare Regionen liefert.

    Wenn der Reader keine lesbaren Regionen liefern kann (z.B. Helper liefert
    nur beschreibbare), wird die global-metadata-Region über die MTGA-PID und
    proc_regionfilename durch einen Adressraum-Scan gefunden.

    WICHTIG: Bei Helper-basierten Adaptern wird IMMER der proc-basierte Pfad C
    verwendet, da der Helper nur writable private regions listet (nicht die
    read-only mapped-file Region, in der global-metadata.dat liegt) und
    proc_regionfilename die korrekte mapped-file Basisadresse findet.
    """
    pid = _process_pid(pm)

    # Pfad A: Reader hat die Region-Methoden selbst (z.B. PersistentRemoteMemoryAdapter).
    # Aber: Helper-basierte Adapter liefern nur writable private regions,
    # nicht die read-only mapped-file Region. Daher überspringen wir Pfad A
    # für Helper-Adapter und gehen direkt zu Pfad C (proc-basiert).
    is_helper_adapter = hasattr(pm, "iterate_writable_private_regions") and not hasattr(pm, "task")

    if not is_helper_adapter and hasattr(pm, "iterate_readable_regions"):
        regions, _ = pm.iterate_readable_regions()
        result = []
        for addr, size in regions:
            fname = _region_filename(pid or 0, addr)
            if fname and "global-metadata" in fname:
                result.append((addr, size))
        if result:
            return result

    # Pfad B: Legacy Pymem-Objekt mit .task → mach_vm_region_recurse.
    if hasattr(pm, "task") or not hasattr(pm, "iterate_readable_regions"):
        try:
            regions, _ = _iterate_regions_with_error(pm)
            result = []
            for addr, size in regions:
                fname = _region_filename(pid or 0, addr)
                if fname and "global-metadata" in fname:
                    result.append((addr, size))
            if result:
                return result
        except Exception:
            pass

    # Pfad C: Fallback — scanne den Adressraum via proc_regionfilename.
    # Dies funktioniert ohne mach_vm_region_recurse, braucht nur die PID.
    # Für Helper-Adapter ist dies der Hauptpfad.
    if pid and pid > 0:
        return _find_metadata_regions_via_proc(pid)

    return []


def _find_metadata_regions_via_proc(pid: int) -> list[tuple[int, int]]:
    """Finde global-metadata.dat-Regionen durch Adressraum-Scan mit proc_regionfilename.

    Diese Funktion ist ein Fallback für Helper-basierte Reader, die nur
    beschreibbare Regionen auflisten können. Sie scannt den Adressraum
    in groben Schritten und verfeinert dann die Grenzen.

    WICHTIG: proc_regionfilename meldet denselben Dateinamen für:
    (a) die echte read-only mapped-file Region (wo IL2CPP-String-Pointer
        hinzeigen) und
    (b) COW-Kopien in MALLOC_SMALL-Regionen (writable private, wo
        modifizierte Seiten landen).
    Die Il2CppClass-String-Pointer zeigen auf die ORIGINAL mapped-file
    Region, nicht auf die COW-Kopien. Daher müssen wir die größte
    zusammenhängende Region finden, die die Datei-Größe abdeckt.
    """
    try:
        lib = _load_proc_lib()
    except OSError:
        return []
    if not hasattr(lib, "proc_regionfilename"):
        return []

    func = lib.proc_regionfilename
    func.argtypes = [ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint32]
    func.restype = ctypes.c_int
    buf = ctypes.create_string_buffer(1024)

    # Grober Scan: 16MB-Schritte im typischen MTGA-Adressbereich
    coarse_step = 0x1000000  # 16 MB
    coarse_hits: list[int] = []
    addr = 0x100000000  # Typische macOS-Startadresse
    end = 0x200000000
    while addr < end:
        rc = func(
            ctypes.c_int(pid), ctypes.c_uint64(addr),
            ctypes.cast(buf, ctypes.c_void_p), ctypes.c_uint32(len(buf)),
        )
        if rc > 0:
            name = buf.value.decode("utf-8", errors="ignore")
            if "global-metadata" in name:
                coarse_hits.append(addr)
                # NICHT break — sammle alle Treffer, um die größte
                # zusammenhängende Region zu finden (mapped-file vs COW)
        addr += coarse_step

    if not coarse_hits:
        return []

    # Verfeinere JEDEN Treffer und bestimme die Ausdehnung
    refined: list[tuple[int, int]] = []
    for hit in coarse_hits:
        # Verfeinere: finde den exakten Start (rückwärts in 4K-Schritten)
        start = hit
        while start > 0x100000000:
            rc = func(
                ctypes.c_int(pid), ctypes.c_uint64(start - 0x4000),
                ctypes.cast(buf, ctypes.c_void_p), ctypes.c_uint32(len(buf)),
            )
            if rc > 0:
                name = buf.value.decode("utf-8", errors="ignore")
                if "global-metadata" not in name:
                    break
            start -= 0x4000

        # Verfeinere: finde das exakte Ende (vorwärts in 4K-Schritten)
        end_addr = hit
        while end_addr < 0x200000000:
            rc = func(
                ctypes.c_int(pid), ctypes.c_uint64(end_addr + 0x4000),
                ctypes.cast(buf, ctypes.c_void_p), ctypes.c_uint32(len(buf)),
            )
            if rc > 0:
                name = buf.value.decode("utf-8", errors="ignore")
                if "global-metadata" not in name:
                    break
            end_addr += 0x4000

        refined.append((start, end_addr - start))

    # Dedupliziere überlappende Regionen und wähle die größte
    # (die echte mapped-file Region ist typischerweise die größte,
    # ~29 MB, während COW-Kopien kleiner sind)
    refined.sort(key=lambda r: r[1], reverse=True)
    # Merge überlappende Regionen
    merged: list[tuple[int, int]] = []
    for start, size in refined:
        end = start + size
        # Prüfe ob diese Region mit einer bereits gefundenen überlappt
        overlapped = False
        for i, (ms, me) in enumerate(merged):
            if start <= me and end >= ms:
                # Merge
                merged[i] = (min(ms, start), max(me, end))
                overlapped = True
                break
        if not overlapped:
            merged.append((start, end))
    # Konvertiere zurück zu (addr, size) und sortiere nach Größe (größte zuerst)
    merged.sort(key=lambda r: r[1] - r[0], reverse=True)
    return [(s, e - s) for s, e in merged]


def _find_string_in_metadata_regions(
    mem: MemoryReader,
    metadata_regions: list[tuple[int, int]],
    name: str,
) -> int | None:
    """Find the runtime address of a NUL-delimited string within the given
    global-metadata.dat region(s), without relying on a hardcoded file
    offset (which shifts between game builds/patches).

    Polymorph: Wenn der Helper die read-only metadata-Region nicht lesen kann
    (liefert Nullen), wird die String-Suche auf die Datei
    global-metadata.dat ausgewichen und die Memory-Adresse berechnet.
    """
    needle = b"\x00" + name.encode("ascii") + b"\x00"
    for addr, size in metadata_regions:
        buf = _bulk_read(mem, addr, size)
        idx = buf.find(needle)
        if idx != -1:
            return addr + idx + 1  # skip the leading NUL

    # Fallback: Der Helper kann die read-only metadata-Region nicht lesen
    # (liefert Nullen). Lese die Datei direkt und berechne die Memory-Adresse.
    return _find_string_in_metadata_file(metadata_regions, name)


def _find_string_in_metadata_file(
    metadata_regions: list[tuple[int, int]],
    name: str,
) -> int | None:
    """Fallback: Suche einen String in der global-metadata.dat-Datei und
    berechne die Memory-Adresse (file_offset + region_base).

    Der Helper-Daemon kann keine read-only Regionen lesen. Da die
    global-metadata.dat-Datei aber vom Anwender lesbar ist, kann der
    String-Pool direkt aus der Datei gelesen werden. Die Memory-Adresse
    ergibt sich aus dem Datei-Offset plus der Region-Basisadresse.

    WICHTIG: Die Region-Basisadresse muss die der ORIGINAL mapped-file
    Region sein (read-only), nicht die der COW-Kopien (writable private).
    _find_metadata_regions_via_proc sortiert Regionen nach Größe (größte
    zuerst), daher ist metadata_regions[0] die größte = die mapped-file Region.
    """
    needle = b"\x00" + name.encode("ascii") + b"\x00"

    # Versuche die Datei direkt zu lesen
    file_path = "/Users/Shared/Epic Games/MagicTheGathering/MTGA.app/Contents/Resources/Data/il2cpp_data/Metadata/global-metadata.dat"
    try:
        with open(file_path, "rb") as f:
            data = f.read()
    except (OSError, FileNotFoundError):
        return None

    idx = data.find(needle)
    if idx == -1:
        return None

    # Berechne Memory-Adresse: file_offset + region_base
    # metadata_regions ist nach Größe sortiert (größte zuerst) —
    # die größte Region ist die echte mapped-file Region.
    if metadata_regions:
        region_base = metadata_regions[0][0]
        # Stelle sicher, dass der String-Offset innerhalb der Region liegt
        string_offset = idx + 1  # skip leading NUL
        if string_offset < metadata_regions[0][1]:
            return region_base + string_offset

    return None


def _coalesced_region_containing(regions: list[tuple[int, int]], addr: int) -> tuple[int, int] | None:
    """Return the maximal byte-contiguous span of `regions` that contains addr.

    mach_vm_region_recurse can split one real heap allocation into several
    adjacent vm_region entries (e.g. differing internal wired/resident
    bookkeeping) even though it's a single contiguous arena. A class
    name-string backref search needs the whole arena, not just whichever
    single fragment happens to contain the address we started from — so
    merge every region that touches its neighbor into one span before
    searching.
    """
    ordered = sorted(regions, key=lambda r: r[0])
    start_idx = None
    for i, (r_addr, r_size) in enumerate(ordered):
        if r_addr <= addr < r_addr + r_size:
            start_idx = i
            break
    if start_idx is None:
        return None

    span_start, span_size = ordered[start_idx]
    span_end = span_start + span_size

    # Extend forward through touching regions.
    for r_addr, r_size in ordered[start_idx + 1:]:
        if r_addr != span_end:
            break
        span_end = r_addr + r_size

    # Extend backward through touching regions.
    for r_addr, r_size in reversed(ordered[:start_idx]):
        if r_addr + r_size != span_start:
            break
        span_start = r_addr

    return span_start, span_end - span_start


def _validate_class_struct(mem: MemoryReader, klass: int, expected_name: str | None = None) -> bool:
    """Sanity-check that `klass` looks like a real Il2CppClass structure."""
    if klass < MIN_VALID_PTR:
        return False
    name_ptr = _read_ptr(mem, klass + IL2CPP_OFFSETS["class_name"])
    if not (MIN_VALID_PTR < name_ptr < MAX_VALID_PTR):
        return False
    name = _read_string_ascii(mem, name_ptr)
    if not name:
        return False
    if expected_name is not None and name != expected_name:
        return False

    fields_ptr = _read_ptr(mem, klass + IL2CPP_OFFSETS["class_fields"])
    if not (MIN_VALID_PTR < fields_ptr < MAX_VALID_PTR):
        return False

    field_count_raw = mem.read_bytes(klass + IL2CPP_OFFSETS["class_field_count"], 4)
    field_count = struct.unpack_from("<I", field_count_raw)[0]
    return 0 < field_count < 200


def _find_class_by_field_backref_in_regions(
    mem: MemoryReader,
    metadata_regions: list[tuple[int, int]],
    fieldinfo_regions: list[tuple[int, int]],
    field_name: str,
    *,
    budget: int = 4_000_000_000,
) -> int | None:
    """Find the Il2CppClass that declares a field named `field_name`.

    FieldInfo layout (confirmed live against MTGA's IL2CPP build):
      +0x00: name (const char*)
      +0x08: type (Il2CppType*)
      +0x10: parent (Il2CppClass*)
      +0x18: offset (i32) + token (u32)

    We locate the field name's string address in global-metadata.dat, then
    search the FieldInfo heap arena for an 8-byte-aligned pointer equal to
    that address — the start of the matching FieldInfo entry — and read its
    `parent` field directly.

    `fieldinfo_regions` uses (start, end) pairs, unlike `metadata_regions`
    and most other region lists in this module, which use (addr, size).
    Regions are searched smallest-first, up to `budget` bytes total, mirroring
    _find_instance_by_klass_in_regions — the FieldInfo arena's absolute
    address shifts with ASLR on every process launch, so this must scan the
    live process's actual writable region map rather than a fixed guess.
    """
    name_addr = _find_string_in_metadata_regions(mem, metadata_regions, field_name)
    if name_addr is None:
        return None

    needle = struct.pack("<Q", name_addr)
    ordered = sorted(fieldinfo_regions, key=lambda r: r[1] - r[0])

    remaining = budget
    for region_start, region_end in ordered:
        if remaining <= 0:
            break
        region_size = region_end - region_start
        read_size = min(region_size, remaining)
        remaining -= read_size

        buf = _bulk_read(mem, region_start, read_size)
        idx = buf.find(needle)
        while idx != -1:
            entry_addr = region_start + idx
            if entry_addr % 8 == 0:
                parent = _read_ptr(mem, entry_addr + IL2CPP_OFFSETS["field_parent"])
                if _validate_class_struct(mem, parent):
                    return parent
            idx = buf.find(needle, idx + 1)

    return None


def _find_class_by_name_in_arena(
    mem: MemoryReader,
    metadata_regions: list[tuple[int, int]],
    arena_addr: int,
    arena_size: int,
    class_name: str,
) -> int | None:
    """Find an Il2CppClass by name within a known class-descriptor heap
    arena (Il2CppClass structs for a build are allocated together in one
    contiguous heap region, so once one class is found the rest can be
    located by searching that same region for their name-string address).
    """
    name_addr = _find_string_in_metadata_regions(mem, metadata_regions, class_name)
    if name_addr is None:
        return None

    needle = struct.pack("<Q", name_addr)
    buf = _bulk_read(mem, arena_addr, arena_size)
    idx = buf.find(needle)
    while idx != -1:
        hit_addr = arena_addr + idx
        if hit_addr % 8 == 0:
            candidate = hit_addr - IL2CPP_OFFSETS["class_name"]
            if _validate_class_struct(mem, candidate, expected_name=class_name):
                return candidate
        idx = buf.find(needle, idx + 1)

    return None


def _find_instance_by_klass_in_regions(
    mem: MemoryReader,
    regions: list[tuple[int, int]],
    klass: int,
    *,
    validate: Callable[[int], bool] | None = None,
    budget: int = 4_000_000_000,
) -> int | None:
    """Find a live object instance whose header klass-pointer equals `klass`.

    IL2CPP object headers start with the class pointer at offset 0. Live
    instances are scattered across the general managed heap (not confined
    to any small fixed range), so this scans the given regions — smallest
    first, up to `budget` bytes total — for an 8-byte-aligned occurrence of
    the class pointer value. `validate` should reject coincidental byte
    matches, e.g. by checking a known field resolves to a plausible object.
    """
    needle = struct.pack("<Q", klass)
    ordered = sorted(regions, key=lambda r: r[1])

    remaining = budget
    for addr, size in ordered:
        if remaining <= 0:
            break
        read_size = min(size, remaining)
        buf = _bulk_read(mem, addr, read_size)
        remaining -= read_size

        idx = buf.find(needle)
        while idx != -1:
            hit_addr = addr + idx
            if hit_addr % 8 == 0 and (validate is None or validate(hit_addr)):
                return hit_addr
            idx = buf.find(needle, idx + 1)

    return None


def _has_region_enumeration(pm: Any) -> bool:
    """Polymorpher Guard: prüft, ob der Reader Regionen aufzählen kann.

    Traditionell wurde nur `hasattr(pm, "task")` geprüft (Pymem mit Live-Task-Port).
    Helper-basierte Adapter (PersistentRemoteMemoryAdapter) haben kein .task,
    bieten aber `iterate_readable_regions()` / `iterate_writable_private_regions()`.
    """
    if hasattr(pm, "task"):
        return True
    if hasattr(pm, "iterate_readable_regions") and hasattr(pm, "iterate_writable_private_regions"):
        return True
    return False


def _enum_writable_private_regions_via_vmmap(pid: int) -> list[tuple[int, int]]:
    """Finde alle writable private Regionen via vmmap-Output-Parsing.

    Der Helper's list_regions (C: get_writable_regions mit mach_vm_region_recurse)
    verpasst Regionen, weil die Submap-Traversal-Logik nicht vollständig ist
    (depth++ ohne depth-- beim Verlassen einer Submap). vmmap nutzt die selbe
    Mach-API, aber mit korrekter Traversal-Logik.

    Parse den vmmap-Output nach Zeilen mit 'rw-/rwx SM=PRV' und extrahiere
    Start- und End-Adresse. Regionen < 4K werden gefiltert (Guard Pages etc.).
    """
    import re
    import subprocess

    try:
        result = subprocess.run(
            ["vmmap", "--interleaved", str(pid)],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []

    if result.returncode != 0:
        return []

    regions: list[tuple[int, int]] = []
    # vmmap-Zeilen haben das Format (macOS 15.6, report format 2.4):
    # TYPE  START-END  [ VSIZE  RSDNT  DIRTY  SWAP] PRT/MAX SHRMOD ...
    # z.B.:
    # VM_ALLOCATE  104c40000-104d40000  [ 1024K  400K  400K  384K] rw-/rwx SM=PRV
    # Der alte Regex erwartete nur einen Wert in den Klammern (\[\s*[\d.]+[KMGT]?\]),
    # aber vmmap gibt VIER Werte aus → Regex matchte nie → 0 Regionen.
    # Fix: \[.*?\] matcht beliebig viele Werte in den Klammern.
    pattern = re.compile(
        r"^\S+\s+([0-9a-f]+)-([0-9a-f]+)\s+\[.*?\]\s+rw-/(?:rwx|rw-)\s+SM=PRV"
    )
    for line in result.stdout.splitlines():
        m = pattern.match(line.strip())
        if m:
            start = int(m.group(1), 16)
            end = int(m.group(2), 16)
            size = end - start
            if size >= 0x4000:  # Mindestens 16K (macOS page size)
                regions.append((start, size))

    return regions


def _enum_writable_private_regions(pm: Any) -> list[tuple[int, int]]:
    """Polymorpher Wrapper für beschreibbare private Regionen.

    Bei Helper-basierten Adaptern kann der Helper's list_regions Regionen
    verpassen (Submap-Traversal-Bug in mach_vm_region_recurse). Daher wird
    zusätzlich ein vmmap-basierter Scan durchgeführt, der ALLE writable
    private Regionen findet. Die Vereinigung aus Helper-Regionen und
    vmmap-Regionen wird zurückgegeben.
    """
    helper_regions: list[tuple[int, int]] = []
    if hasattr(pm, "iterate_writable_private_regions"):
        try:
            helper_regions = pm.iterate_writable_private_regions()
        except Exception:
            helper_regions = []

    # Fallback/Ergänzung: vmmap-basierter Scan für Helper-Adapter
    # (der Helper verpasst Regionen aufgrund eines Submap-Traversal-Bugs)
    pid = _process_pid(pm)
    if pid and pid > 0:
        vmmap_regions = _enum_writable_private_regions_via_vmmap(pid)
        if vmmap_regions:
            # Vereinigung: Helper-Regionen + vmmap-Regionen (dedup)
            all_regions = list(helper_regions)
            for v_addr, v_size in vmmap_regions:
                # Prüfe ob diese Region schon in helper_regions enthalten ist
                found = False
                for h_addr, h_size in helper_regions:
                    if h_addr <= v_addr < h_addr + h_size:
                        found = True
                        break
                if not found:
                    all_regions.append((v_addr, v_size))
            return all_regions

    if helper_regions:
        return helper_regions
    return _iterate_writable_private_regions(pm)


def _enum_readable_regions(pm: Any) -> tuple[list[tuple[int, int]], int | None]:
    """Polymorpher Wrapper für lesbare Regionen."""
    if hasattr(pm, "iterate_readable_regions"):
        return pm.iterate_readable_regions()
    return _iterate_regions_with_error(pm)


def discover_decks_manager_via_backref(
    reader: Il2CppReader | MemoryReader | Any,
    *,
    debug: bool = False,
) -> tuple[int, int] | None:
    """Locate a live DecksManager instance without data_segment_base/TypeInfoTable.

    Empirically verified end-to-end against a live MTGA process (see
    docs/deck-scan-problem-report.md): Il2CppClass structs are heap-allocated
    outside GameAssembly.dylib's own segments, so the TypeInfoTable-based
    discovery above cannot find them reliably on macOS. Instead:

      1. Find DeckDataProvider's Il2CppClass via a FieldInfo backref to the
         "_allDecks" field name (FieldInfo arrays live somewhere in the
         process's writable private heap — enumerated fresh each run, since
         their absolute address shifts with ASLR).
      2. Find DecksManager's Il2CppClass in the *same* class-descriptor heap
         arena as DeckDataProvider (classes for a build are allocated together).
      3. Read DecksManager's real "_deckDataProvider" field offset from its
         own FieldInfo array (no hardcoded offset).
      4. Scan writable heap memory for a live object whose header klass
         pointer equals DecksManager's class, validated by checking that its
         _deckDataProvider field points to an object of the confirmed
         DeckDataProvider class.

    Returns:
        (decks_manager_instance, decks_manager_class), or None if any step
        fails.
    """
    def _log(msg: str) -> None:
        if debug:
            logger.debug("[backref] %s", msg)

    mem = _coerce_safe_reader(reader)
    if mem is None:
        _log("FAIL: reader could not be coerced to a MemoryReader")
        return None
    pm = getattr(reader, "pm", reader)
    if not _has_region_enumeration(pm):
        # Not a live process handle (e.g. a bare MockMemory in tests) —
        # region enumeration needs a real task port or adapter methods.
        _log("FAIL: no region enumeration available on reader (no pm.task and no adapter methods)")
        return None

    metadata_regions = _find_metadata_regions(pm)
    if not metadata_regions:
        _log("FAIL: no global-metadata.dat regions found in process memory")
        return None
    _log(f"OK: {len(metadata_regions)} metadata region(s) found")

    # FieldInfo arrays and live instances both live somewhere in the
    # process's writable private heap, but their absolute address shifts
    # with ASLR on every launch — enumerate the real region map once
    # instead of guessing fixed ranges, and reuse it for both searches.
    writable_regions = _enum_writable_private_regions(pm)
    fieldinfo_regions = [(addr, addr + size) for addr, size in writable_regions]
    _log(
        f"OK: {len(writable_regions)} writable private region(s), "
        f"{sum(size for _, size in writable_regions):,} bytes total"
    )

    ddp_class = _find_class_by_field_backref_in_regions(
        mem, metadata_regions, fieldinfo_regions, "_allDecks",
    )
    if ddp_class is None:
        _log("FAIL: no FieldInfo backref to \"_allDecks\" found in writable regions "
             "(DeckDataProvider class not located)")
        return None
    _log(f"OK: DeckDataProvider class @ {ddp_class:#x}")

    readable_regions, _ = _enum_readable_regions(pm)
    arena = _coalesced_region_containing(readable_regions, ddp_class)
    if arena is None:
        _log("FAIL: no readable region contains the DeckDataProvider class address")
        return None
    _log(f"OK: class-descriptor arena @ {arena[0]:#x} (+{arena[1]:#x}, coalesced from adjacent regions)")

    dm_class = _find_class_by_name_in_arena(mem, metadata_regions, arena[0], arena[1], "DecksManager")
    if dm_class is None:
        _log("FAIL: \"DecksManager\" class not found in the same heap arena as DeckDataProvider")
        return None
    _log(f"OK: DecksManager class @ {dm_class:#x}")

    ddp_field = next(
        (f for f in get_class_fields(mem, dm_class) if f.name == "_deckDataProvider"),
        None,
    )
    if ddp_field is None:
        _log("FAIL: DecksManager class has no \"_deckDataProvider\" field in its FieldInfo array")
        return None
    _log(f"OK: _deckDataProvider field offset = {ddp_field.offset:#x}")

    def _validate(candidate: int) -> bool:
        ddp_ptr = _read_ptr(mem, candidate + ddp_field.offset)
        if not (MIN_VALID_PTR < ddp_ptr < MAX_VALID_PTR):
            return False
        return _read_ptr(mem, ddp_ptr) == ddp_class

    dm_instance = _find_instance_by_klass_in_regions(mem, writable_regions, dm_class, validate=_validate)
    if dm_instance is None:
        _log("FAIL: no live instance in writable memory whose class pointer matches "
             "DecksManager (and validates via _deckDataProvider)")
        return None
    _log(f"OK: DecksManager instance @ {dm_instance:#x}")

    return dm_instance, dm_class


def discover_wrapper_controller_via_backref(
    reader: Il2CppReader | MemoryReader | Any,
    *,
    debug: bool = False,
) -> tuple[int, int] | None:
    """Locate a live WrapperController instance without data_segment_base/TypeInfoTable.

    Same strategy as discover_decks_manager_via_backref: Il2CppClass structs
    are heap-allocated outside GameAssembly.dylib's own segments, so the
    TypeInfoTable-based discovery cannot find them reliably on macOS. Instead:

      1. Find WrapperController's Il2CppClass via a FieldInfo backref to the
         "<PlayerRankServiceWrapper>k__BackingField" field name (FieldInfo
         arrays live in the process's writable private heap, enumerated fresh
         each run since their absolute address shifts with ASLR).
      2. Find PlayerRankServiceWrapper's Il2CppClass via a FieldInfo backref
         to the "_combinedRankInfo" field name (the field it declares).
      3. Read WrapperController's real "<PlayerRankServiceWrapper>k__BackingField"
         offset from its own FieldInfo array (no hardcoded offset).
      4. Scan writable heap memory for a live object whose header klass pointer
         equals WrapperController's class, validated by checking that its rank
         wrapper field points at an object of the confirmed
         PlayerRankServiceWrapper class.

    Returns:
        (wrapper_instance, wrapper_class), or None if any step fails.
    """
    def _log(msg: str) -> None:
        if debug:
            logger.debug("[backref-rank] %s", msg)

    mem = _coerce_safe_reader(reader)
    if mem is None:
        _log("FAIL: reader could not be coerced to a MemoryReader")
        return None
    pm = getattr(reader, "pm", reader)
    if not _has_region_enumeration(pm):
        # Not a live process handle (e.g. a bare MockMemory in tests) —
        # region enumeration needs a real task port or adapter methods.
        _log("FAIL: no region enumeration available on reader (no pm.task and no adapter methods)")
        return None

    metadata_regions = _find_metadata_regions(pm)
    if not metadata_regions:
        _log("FAIL: no global-metadata.dat regions found in process memory")
        return None
    _log(f"OK: {len(metadata_regions)} metadata region(s) found")

    writable_regions = _enum_writable_private_regions(pm)
    fieldinfo_regions = [(addr, addr + size) for addr, size in writable_regions]
    _log(
        f"OK: {len(writable_regions)} writable private region(s), "
        f"{sum(size for _, size in writable_regions):,} bytes total"
    )

    # WrapperController class — parent of the rank-wrapper backing field.
    wc_class = _find_class_by_field_backref_in_regions(
        mem, metadata_regions, fieldinfo_regions,
        "<PlayerRankServiceWrapper>k__BackingField",
    )
    if wc_class is None:
        _log("FAIL: no FieldInfo backref to \"<PlayerRankServiceWrapper>k__BackingField\" "
             "found in writable regions (WrapperController class not located)")
        return None
    _log(f"OK: WrapperController class @ {wc_class:#x}")

    # PlayerRankServiceWrapper class — parent of the _combinedRankInfo field.
    prsw_class = _find_class_by_field_backref_in_regions(
        mem, metadata_regions, fieldinfo_regions, "_combinedRankInfo",
    )
    if prsw_class is None:
        _log("FAIL: no FieldInfo backref to \"_combinedRankInfo\" found in writable regions "
             "(PlayerRankServiceWrapper class not located)")
        return None
    _log(f"OK: PlayerRankServiceWrapper class @ {prsw_class:#x}")

    wc_rank_field = next(
        (f for f in get_class_fields(mem, wc_class)
         if f.name == "<PlayerRankServiceWrapper>k__BackingField"),
        None,
    )
    if wc_rank_field is None:
        _log("FAIL: WrapperController class has no \"<PlayerRankServiceWrapper>k__BackingField\" "
             "field in its FieldInfo array")
        return None
    _log(f"OK: rank-wrapper field offset = {wc_rank_field.offset:#x}")

    def _validate(candidate: int) -> bool:
        prsw_ptr = _read_ptr(mem, candidate + wc_rank_field.offset)
        if not (MIN_VALID_PTR < prsw_ptr < MAX_VALID_PTR):
            return False
        return _read_ptr(mem, prsw_ptr) == prsw_class

    wc_instance = _find_instance_by_klass_in_regions(mem, writable_regions, wc_class, validate=_validate)
    if wc_instance is None:
        _log("FAIL: no live instance in writable memory whose class pointer matches "
             "WrapperController (and validates via PlayerRankServiceWrapper)")
        return None
    _log(f"OK: WrapperController instance @ {wc_instance:#x}")

    return wc_instance, wc_class


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
    *,
    debug: bool = False,
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
        debug: If set, print every field name on Client_Deck and DeckSummary
            once (diagnostic aid for finding an as-yet-unread field, e.g.
            deck format — no format field is read today).

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

    if debug:
        logger.debug("[read_deck] Client_Deck fields: %s", [f.name for f in fields])

    summary_field = next((f for f in fields if f.name == DECK_SUMMARY_FIELD), None)
    contents_field = next((f for f in fields if f.name == DECK_CONTENTS_FIELD), None)

    result = Il2CppDeckResult(raw_address=deck_addr)

    # Read summary (name)
    if summary_field is not None:
        summary_ptr = _read_ptr(mem, deck_addr + summary_field.offset)
        if summary_ptr > MIN_VALID_PTR:
            if debug:
                summary_class = _read_ptr(mem, summary_ptr)
                if summary_class > MIN_VALID_PTR:
                    summary_fields = get_class_fields(mem, summary_class)
                    logger.debug(
                        "[read_deck] DeckSummary fields: %s",
                        [f.name for f in summary_fields],
                    )
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
    debug: bool = False,
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

    # Fast path: locate a live DecksManager instance directly via metadata
    # string backrefs (see discover_decks_manager_via_backref), bypassing
    # data_segment_base/TypeInfoTable discovery entirely. Only attempted
    # when the caller hasn't pre-supplied explicit PAPA addresses.
    if papa_instance == 0 and papa_class == 0:
        via_backref = discover_decks_manager_via_backref(reader, debug=debug)
        if via_backref is not None:
            dm_instance, dm_class = via_backref
            nav_result = navigate_to_all_decks(
                mem, dm_instance, dm_class, path=["_deckDataProvider", "_allDecks"],
            )
            if nav_result is not None and nav_result.entries:
                decks: list[Il2CppDeckResult] = []
                for idx, (deck_id, deck_ptr) in enumerate(nav_result.entries):
                    # debug=idx==0 alone is fragile: if the very first dict
                    # entry happens to be an invalid/orphaned deck, read_deck
                    # returns None before ever reaching its field-dump prints,
                    # and every later (successful) deck gets no debug output
                    # at all. Give the first few entries a chance instead.
                    deck = read_deck(mem, deck_ptr, debug=debug and idx < 3)
                    if deck is not None:
                        deck.deck_id = deck_id
                        decks.append(deck)
                if decks:
                    return Il2CppScanResult(
                        decks=decks,
                        warnings=warnings,
                        papa_instance=dm_instance,
                        all_decks_addr=nav_result.dict_addr,
                    )
                warnings.append("backref discovery found _allDecks but no decks could be parsed")
            else:
                warnings.append("backref discovery found DecksManager but _allDecks navigation failed")

    # Fallback: legacy TypeInfoTable / PAPA heap-scan discovery path.
    # Discover PAPA class if not provided
    if papa_class == 0:
        if type_info_table == 0:
            if data_segment_base == 0:
                data_segment_base = discover_data_segment_base(mem)
            if data_segment_base == 0:
                warnings.append("no data_segment_base could be discovered for class discovery")
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


# ---------------------------------------------------------------------------
# Pymem adapter — wraps a live Pymem process for IL2CPP navigation
# ---------------------------------------------------------------------------

class PymemMemoryAdapter:
    """Adapter that wraps a Pymem instance to implement the MemoryReader protocol.

    Uses pattern_scanner._read_bytes_silent (mach_vm_read_overwrite) for reads,
    returning empty bytes on failure instead of raising.
    """

    def __init__(self, pm: Any) -> None:
        self.pm = pm

    @property
    def pid(self) -> int:
        pid = _process_pid(self.pm)
        return pid or 0

    def read_bytes(self, addr: int, size: int) -> bytes:
        from . import pattern_scanner as _ps
        raw = _ps._read_bytes_silent(self.pm, addr, size)
        if raw is None:
            return b"\x00" * size
        if len(raw) < size:
            raw = raw + b"\x00" * (size - len(raw))
        return raw

    def read_ptr(self, addr: int) -> int:
        raw = self.read_bytes(addr, 8)
        return struct.unpack_from("<Q", raw)[0]

    def read_u32(self, addr: int) -> int:
        raw = self.read_bytes(addr, 4)
        return struct.unpack_from("<I", raw)[0]

    def read_i32(self, addr: int) -> int:
        raw = self.read_bytes(addr, 4)
        return struct.unpack_from("<i", raw)[0]

    def read_string(self, addr: int) -> str:
        if addr == 0:
            return ""
        raw = self.read_bytes(addr, 256)
        end = raw.find(b"\x00")
        if end == -1:
            end = len(raw)
        return raw[:end].decode("ascii", errors="replace")


@dataclass(frozen=True)
class _MachOSegmentInfo:
    name: str
    vmaddr: int
    vmsize: int


def _process_pid(pm: Any) -> int | None:
    """Best-effort extraction of a PID from a Pymem-like object."""
    for attr in ("pid", "process_id"):
        value = getattr(pm, attr, None)
        if isinstance(value, int) and value > 0:
            return value
        if hasattr(value, "value"):
            try:
                pid = int(value.value)
            except (TypeError, ValueError):
                pid = 0
            if pid > 0:
                return pid

    process = getattr(pm, "process", None)
    if process is not None:
        for attr in ("pid", "process_id"):
            value = getattr(process, attr, None)
            if isinstance(value, int) and value > 0:
                return value
            if hasattr(value, "value"):
                try:
                    pid = int(value.value)
                except (TypeError, ValueError):
                    pid = 0
                if pid > 0:
                    return pid

    return None


@lru_cache(maxsize=1)
def _load_proc_lib() -> Any:
    """Load libproc once for proc_regionfilename lookups."""
    lib_name = ctypes.util.find_library("proc") or "libproc.dylib"
    return ctypes.cdll.LoadLibrary(lib_name)


def _region_filename(pid: int, addr: int) -> str:
    """Return the filesystem path for a mapped region, if available."""
    try:
        lib = _load_proc_lib()
    except OSError:
        return ""

    if not hasattr(lib, "proc_regionfilename"):
        return ""

    func = lib.proc_regionfilename
    func.argtypes = [ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_uint32]
    func.restype = ctypes.c_int

    buf = ctypes.create_string_buffer(1024)
    rc = func(ctypes.c_int(pid), ctypes.c_uint64(addr), ctypes.cast(buf, ctypes.c_void_p), ctypes.c_uint32(len(buf)))
    if rc <= 0:
        return ""

    raw = buf.value.decode("utf-8", errors="ignore")
    return raw


def _read_macho_segments(mem: MemoryReader, image_base: int) -> list[_MachOSegmentInfo]:
    """Parse LC_SEGMENT_64 entries from a Mach-O image header."""
    header = mem.read_bytes(image_base, MACHO_HEADER_SCAN_SIZE)
    if len(header) < MACHO_HEADER_64_SIZE:
        return []

    magic = struct.unpack_from("<I", header, 0)[0]
    if magic != MH_MAGIC_64:
        return []

    ncmds = struct.unpack_from("<I", header, 16)[0]
    offset = MACHO_HEADER_64_SIZE
    segments: list[_MachOSegmentInfo] = []

    for _ in range(ncmds):
        if offset + 8 > len(header):
            break
        cmd, cmdsize = struct.unpack_from("<II", header, offset)
        if cmdsize < 8 or offset + cmdsize > len(header):
            break
        if cmd == LC_SEGMENT_64 and cmdsize >= 72:
            segname_raw = header[offset + 8: offset + 24]
            segname = segname_raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore")
            vmaddr = struct.unpack_from("<Q", header, offset + 24)[0]
            vmsize = struct.unpack_from("<Q", header, offset + 32)[0]
            segments.append(_MachOSegmentInfo(segname, vmaddr, vmsize))
        offset += cmdsize

    return segments


def _looks_like_type_info_table(mem: MemoryReader, data_segment_base: int) -> bool:
    """Check whether a candidate data segment exposes a valid TypeInfoTable."""
    type_info_table = _read_ptr(mem, data_segment_base + IL2CPP_OFFSETS["type_info_table_offset"])
    if type_info_table <= MIN_VALID_PTR:
        return False

    for i in range(256):
        class_ptr = _read_ptr(mem, type_info_table + i * 8)
        if class_ptr == 0 or class_ptr < MIN_VALID_PTR:
            continue

        name_ptr = _read_ptr(mem, class_ptr + IL2CPP_OFFSETS["class_name"])
        if name_ptr == 0 or name_ptr < MIN_VALID_PTR:
            continue

        class_name = _read_string_ascii(mem, name_ptr)
        if class_name in {"WrapperController", "PAPA"}:
            return True
    return False


def _discover_data_segment_base_from_image(mem: MemoryReader, image_base: int) -> int:
    """Derive the runtime base of the IL2CPP data segment from Mach-O metadata."""
    segments = _read_macho_segments(mem, image_base)
    if not segments:
        return 0

    text_seg = next((seg for seg in segments if seg.name in {"__TEXT", "__TEXT_EXEC"}), None)
    if text_seg is None:
        return 0

    slide = image_base - text_seg.vmaddr
    candidates: list[int] = []

    for seg in segments:
        if seg.name == "__TEXT" or seg.name == "__TEXT_EXEC":
            continue
        if seg.name in DATA_SEGMENT_NAMES:
            runtime_base = seg.vmaddr + slide
            if runtime_base > MIN_VALID_PTR:
                candidates.append(runtime_base)

    for candidate in candidates:
        if _looks_like_type_info_table(mem, candidate):
            return candidate

    return 0


def _discover_data_segment_base_from_regions(mem: MemoryReader, pm: Any) -> int:
    """Probe readable regions and inspect any Mach-O images that start there."""
    regions, _ = _iterate_regions_with_error(pm)
    for addr, _size in regions:
        if _read_u32(mem, addr) != MH_MAGIC_64:
            continue
        data_base = _discover_data_segment_base_from_image(mem, addr)
        if data_base != 0:
            return data_base
    return 0


def _find_game_assembly_base(pm: Any) -> int:
    """Locate the runtime base address of GameAssembly.dylib."""
    try:
        modules = pm.get_modules(extended=True)
    except Exception:
        try:
            modules = pm.get_modules()
        except Exception:
            modules = []

    for module in modules:
        name = ""
        address = 0
        sections: list[Any] = []
        try:
            name = module.get_name()
        except Exception:
            name = getattr(module, "name", "")
        try:
            address = int(module.get_address())
        except Exception:
            address = int(getattr(module, "address", 0) or 0)
        try:
            sections = list(module.get_sections())
        except Exception:
            sections = list(getattr(module, "sections", []) or [])

        if address <= 0:
            continue

        base_name = os.path.basename(name)
        if any(hint in base_name for hint in GAME_ASSEMBLY_HINTS):
            for section in sections:
                try:
                    section_name = section.get_name()
                    section_addr = int(section.get_address())
                except Exception:
                    section_name = getattr(section, "name", "")
                    section_addr = int(getattr(section, "address", 0) or 0)
                if section_name in DATA_SEGMENT_NAMES and section_addr > 0:
                    return section_addr
            return address

    pid = _process_pid(pm)
    if pid is None:
        return 0

    regions, _ = _iterate_regions_with_error(pm)
    matches: list[int] = []
    for addr, _size in regions:
        filename = _region_filename(pid, addr)
        if not filename:
            continue
        base_name = os.path.basename(filename)
        if any(hint in base_name for hint in GAME_ASSEMBLY_HINTS):
            matches.append(addr)

    if not matches:
        return 0
    return min(matches)


def _coerce_safe_reader(reader: Il2CppReader | MemoryReader | Any) -> MemoryReader | None:
    """Return a reader that tolerates failed reads instead of raising.

    Live ``pymem`` objects expose ``read_bytes`` directly, but that method
    raises on short reads. The adapter path uses ``mach_vm_read_overwrite``
    and returns zero-filled buffers instead, which is safe for probing.
    """
    if isinstance(reader, Il2CppReader):
        return reader.mem

    if hasattr(reader, "pm") and hasattr(reader, "read_bytes"):
        return reader

    if hasattr(reader, "task") and hasattr(reader, "read_bytes"):
        return PymemMemoryAdapter(reader)

    if hasattr(reader, "read_bytes"):
        return reader

    return None


def discover_data_segment_base(reader: Il2CppReader | MemoryReader | Any) -> int:
    """Discover the IL2CPP data segment base for a live MTGA process.

    This is a macOS-specific helper that walks the loaded GameAssembly image,
    parses its Mach-O load commands, and validates the candidate data segment
    by checking whether the known TypeInfoTable offset resolves to a usable
    class table.
    """
    mem = _coerce_safe_reader(reader)
    if mem is None:
        return 0

    pm = getattr(reader, "pm", reader)
    if not hasattr(pm, "task"):
        # Not a live process handle (e.g. a bare MockMemory in tests) —
        # region/module enumeration needs a real task port, so there is
        # nothing to discover.
        return 0

    data_base = _discover_data_segment_base_from_regions(mem, pm)
    if data_base != 0:
        return data_base

    image_base = _find_game_assembly_base(pm)
    if image_base == 0:
        return 0

    return _discover_data_segment_base_from_image(mem, image_base)
