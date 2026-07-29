"""Pattern-Scanner für macOS-Prozess-Speicher.

pymem-osx hat kein pattern_scan_all(). Diese Implementierung
nutzt mach_vm_region() via ctypes, um Speicherregionen zu
iterieren und nach Byte-Patterns zu durchsuchen.

Optimierung: Nur private, readable, non-shared Regionen scannen.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import struct
from typing import Any

from pymem import Pymem

# Mach-O VM Flags
VM_PROT_READ = 1
VM_PROT_WRITE = 2
VM_PROT_EXECUTE = 4

# Region subtypes
SMALL_REGION = 1
LARGE_REGION = 2

# mach_vm_region() result structure
class vm_region_basic_info_data_t(ctypes.Structure):
    _fields_ = [
        ("protection", ctypes.c_uint32),
        ("max_protection", ctypes.c_uint32),
        ("inheritance", ctypes.c_uint32),
        ("shared", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32),
        ("offset", ctypes.c_uint32),
        ("behavior", ctypes.c_uint32),
        ("user_wired_count", ctypes.c_uint16),
    ]


class vm_region_submap_info_data_t(ctypes.Structure):
    _fields_ = [
        ("protection", ctypes.c_uint32),
        ("max_protection", ctypes.c_uint32),
        ("inheritance", ctypes.c_uint32),
        ("offset", ctypes.c_uint32),
        ("user_tag", ctypes.c_uint32),
        ("ref_count", ctypes.c_uint32),
        ("shadow_depth", ctypes.c_uint16),
        ("external_pager", ctypes.c_uint8),
        ("share_mode", ctypes.c_uint8),
        ("is_submap", ctypes.c_uint32),
        ("behavior", ctypes.c_uint32),
        ("object_id", ctypes.c_uint32),
        ("user_wired_count", ctypes.c_uint16),
    ]


def _load_mach_lib() -> Any:
    """Lädt die Mach Bibliothek für VM-Operationen."""
    lib = ctypes.util.find_library("libSystem")
    if not lib:
        raise RuntimeError("libSystem nicht gefunden")
    return ctypes.cdll.LoadLibrary(lib)


def _setup_mach_functions(lib: Any) -> None:
    """Richtet die ctypes-Signaturen für Mach VM Funktionen ein."""
    # mach_vm_region(task, address, size, flavor, info, count, object_name)
    lib.mach_vm_region.argtypes = [
        ctypes.c_uint32,  # task
        ctypes.POINTER(ctypes.c_uint64),  # address (in/out)
        ctypes.POINTER(ctypes.c_uint64),  # size (out)
        ctypes.c_uint32,  # flavor
        ctypes.POINTER(vm_region_basic_info_data_t),  # info (out)
        ctypes.POINTER(ctypes.c_uint32),  # count (in/out)
        ctypes.POINTER(ctypes.c_uint64),  # object_name (out)
    ]
    lib.mach_vm_region.restype = ctypes.c_uint32

    # mach_task_self()
    lib.mach_task_self.argtypes = []
    lib.mach_task_self.restype = ctypes.c_uint32


def _iterate_regions(pm: Pymem) -> list[tuple[int, int]]:
    """Iteriert über alle Speicherregionen des Zielprozesses.

    Returns:
        Liste von (address, size)-Tupeln für lesbare, private Regionen.
    """
    lib = _load_mach_lib()
    _setup_mach_functions(lib)

    regions: list[tuple[int, int]] = []
    address = ctypes.c_uint64(0)
    size = ctypes.c_uint64(0)
    info = vm_region_basic_info_data_t()
    count = ctypes.c_uint32(ctypes.sizeof(vm_region_basic_info_data_t) // ctypes.sizeof(ctypes.c_uint32))
    object_name = ctypes.c_uint64(0)

    while True:
        result = lib.mach_vm_region(
            ctypes.c_uint32(pm.task),
            ctypes.byref(address),
            ctypes.byref(size),
            ctypes.c_uint32(1),  # BASIC_INFO_64
            ctypes.byref(info),
            ctypes.byref(count),
            ctypes.byref(object_name),
        )

        if result != 0:  # KERN_SUCCESS
            break

        addr = address.value
        sz = size.value

        # Nur lesbare, private, nicht-shared Regionen scannen
        if (info.protection & VM_PROT_READ) and not info.shared and not info.is_submap:
            if sz > 0 and sz < 1024 * 1024 * 1024:  # < 1GB
                regions.append((addr, sz))

        # Nächste Region
        address = ctypes.c_uint64(addr + sz)

    return regions


def scan_process_memory(pm: Pymem, needle: bytes) -> list[int]:
    """Scannt den gesamten Prozess-Speicher nach einem Byte-Pattern.

    Args:
        pm: Pymem-Instanz (attached an MTGA-Prozess)
        needle: Zu suchendes Byte-Pattern (z.B. struct.pack('<I', card_id))

    Returns:
        Liste der virtuellen Adressen, an denen das Pattern gefunden wurde.
    """
    if not needle:
        return []

    regions = _iterate_regions(pm)
    found: list[int] = []

    for addr, size in regions:
        try:
            data = pm.read_bytes(addr, min(size, 4 * 1024 * 1024))  # max 4MB pro Region
            offset = 0
            while True:
                pos = data.find(needle, offset)
                if pos == -1:
                    break
                found.append(addr + pos)
                offset = pos + 1
        except Exception:
            continue  # Region nicht lesbar → überspringen

    return found
