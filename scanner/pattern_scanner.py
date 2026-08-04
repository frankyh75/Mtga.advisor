"""Pattern-Scanner für macOS-Prozess-Speicher.

pymem-osx hat kein pattern_scan_all(). Diese Implementierung
nutzt mach_vm_region() via ctypes, um Speicherregionen zu
iterieren und nach Byte-Patterns zu durchsuchen.

Optimierung: Nur private, readable, non-shared Regionen scannen.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass
from typing import Any

from typing import Any, Protocol


class MemoryBackend(Protocol):
    """Protocol für austauschbare Memory-Backends.

    Drei Methoden, die jedes Backend implementieren muss:
    - read_bytes: liest Speicher an einer Adresse
    - iterate_writable_private_regions: listet beschreibbare, private Regionen (IL2CPP-Navigation)
    - iterate_readable_regions: listet alle lesbaren Regionen (Pattern-Scanning)
    """

    def read_bytes(self, addr: int, size: int) -> bytes | None: ...

    def iterate_writable_private_regions(self) -> list[tuple[int, int]]: ...

    def iterate_readable_regions(self) -> tuple[list[tuple[int, int]], int | None]: ...


class PymemBackend:
    """Pymem-basiertes Backend — nutzt task_for_pid() lokal (braucht sudo)."""

    def __init__(self, pm: Pymem) -> None:
        self._pm = pm
        self._lib: Any | None = None

    def _ensure_lib(self) -> Any:
        if self._lib is None:
            self._lib = _load_mach_lib()
            _setup_mach_functions(self._lib)
        return self._lib

    def _task_port(self) -> int:
        task = getattr(self._pm, "task")
        return int(getattr(task, "value", task))

    def read_bytes(self, addr: int, size: int) -> bytes | None:
        lib = self._ensure_lib()
        buffer = ctypes.create_string_buffer(size)
        out_size = ctypes.c_uint64(0)
        result = lib.mach_vm_read_overwrite(
            ctypes.c_uint64(self._task_port()),
            ctypes.c_uint64(addr),
            ctypes.c_uint64(size),
            ctypes.cast(buffer, ctypes.c_void_p),
            ctypes.byref(out_size),
        )
        if result != KERN_SUCCESS or out_size.value <= 0:
            return None
        return buffer.raw[: out_size.value]

    def iterate_writable_private_regions(self) -> list[tuple[int, int]]:
        lib = self._ensure_lib()
        regions: list[tuple[int, int]] = []
        address = ctypes.c_uint64(0)
        size = ctypes.c_uint64(0)
        info = vm_region_submap_info_data_64_t()
        depth = ctypes.c_uint32(0)

        while True:
            count = ctypes.c_uint32(
                ctypes.sizeof(vm_region_submap_info_data_64_t) // ctypes.sizeof(ctypes.c_uint32)
            )
            result = lib.mach_vm_region_recurse(
                ctypes.c_uint64(self._task_port()),
                ctypes.byref(address),
                ctypes.byref(size),
                ctypes.byref(depth),
                ctypes.byref(info),
                ctypes.byref(count),
            )
            if result != KERN_SUCCESS:
                break

            addr = address.value
            sz = size.value

            if info.is_submap:
                depth = ctypes.c_uint32(depth.value + 1)
                continue

            prot = info.protection
            writable = bool(prot & VM_PROT_READ) and bool(prot & VM_PROT_WRITE)
            if writable and info.share_mode not in _SHARED_SHARE_MODES:
                if 0 < sz < 1024 * 1024 * 1024:
                    regions.append((addr, sz))

            address = ctypes.c_uint64(addr + sz)

        return regions


    def iterate_readable_regions(self) -> tuple[list[tuple[int, int]], int | None]:
        """Iteriert über alle lesbaren Speicherregionen (für Pattern-Scanning)."""
        lib = self._ensure_lib()
        regions: list[tuple[int, int]] = []
        address = ctypes.c_uint64(0)
        size = ctypes.c_uint64(0)
        info = vm_region_submap_info_data_64_t()
        depth = ctypes.c_uint32(0)
        first_error: int | None = None

        while True:
            count = ctypes.c_uint32(
                ctypes.sizeof(vm_region_submap_info_data_64_t) // ctypes.sizeof(ctypes.c_uint32)
            )
            result = lib.mach_vm_region_recurse(
                ctypes.c_uint64(self._task_port()),
                ctypes.byref(address),
                ctypes.byref(size),
                ctypes.byref(depth),
                ctypes.byref(info),
                ctypes.byref(count),
            )

            if result != KERN_SUCCESS:
                first_error = int(result)
                break

            addr = address.value
            sz = size.value

            if info.is_submap:
                depth = ctypes.c_uint32(depth.value + 1)
                continue

            if info.protection & VM_PROT_READ:
                if sz > 0 and sz < 1024 * 1024 * 1024:
                    regions.append((addr, sz))

            address = ctypes.c_uint64(addr + sz)

        return regions, first_error


try:
    from pymem import Pymem
except ImportError:  # pragma: no cover - fallback for environments without pymem
    class Pymem:  # type: ignore[no-redef]
        pass

# Mach-O VM Flags
VM_PROT_READ = 1
VM_PROT_WRITE = 2
VM_PROT_EXECUTE = 4

# Region subtypes
SMALL_REGION = 1
LARGE_REGION = 2
REGION_SCAN_CHUNK_SIZE = 4 * 1024 * 1024
KERN_SUCCESS = 0


@dataclass(frozen=True)
class ScanStats:
    regions: int
    bytes_scanned: int
    read_failures: int
    matches: int
    region_error: int | None = None


@dataclass(frozen=True)
class ScanResult:
    addresses: list[int]
    stats: ScanStats


@dataclass(frozen=True)
class MultiScanResult:
    addresses: dict[int, list[int]]
    stats: ScanStats


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


class vm_region_submap_info_data_64_t(ctypes.Structure):
    _fields_ = [
        ("protection", ctypes.c_uint32),
        ("max_protection", ctypes.c_uint32),
        ("inheritance", ctypes.c_uint32),
        ("offset", ctypes.c_uint64),
        ("user_tag", ctypes.c_uint32),
        ("pages_resident", ctypes.c_uint32),
        ("pages_shared_now_private", ctypes.c_uint32),
        ("pages_swapped_out", ctypes.c_uint32),
        ("pages_dirtied", ctypes.c_uint32),
        ("ref_count", ctypes.c_uint32),
        ("shadow_depth", ctypes.c_uint16),
        ("external_pager", ctypes.c_uint8),
        ("share_mode", ctypes.c_uint8),
        ("is_submap", ctypes.c_uint32),
        ("behavior", ctypes.c_uint32),
        ("object_id", ctypes.c_uint32),
        ("user_wired_count", ctypes.c_uint16),
        ("flags", ctypes.c_uint16),
        ("pages_reusable", ctypes.c_uint32),
        ("object_id_full", ctypes.c_uint64),
    ]


def _task_port(pm: Pymem) -> int:
    """Legacy-Wrapper: Baut PymemBackend und delegiert."""
    return PymemBackend(pm)._task_port()


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
        ctypes.c_uint64,  # task
        ctypes.POINTER(ctypes.c_uint64),  # address (in/out)
        ctypes.POINTER(ctypes.c_uint64),  # size (out)
        ctypes.c_uint32,  # flavor
        ctypes.POINTER(vm_region_basic_info_data_t),  # info (out)
        ctypes.POINTER(ctypes.c_uint32),  # count (in/out)
        ctypes.POINTER(ctypes.c_uint32),  # object_name (out)
    ]
    lib.mach_vm_region.restype = ctypes.c_uint32

    # mach_vm_region_recurse(task, address, size, nesting_depth, info, count)
    lib.mach_vm_region_recurse.argtypes = [
        ctypes.c_uint64,  # task
        ctypes.POINTER(ctypes.c_uint64),  # address (in/out)
        ctypes.POINTER(ctypes.c_uint64),  # size (out)
        ctypes.POINTER(ctypes.c_uint32),  # nesting_depth (in/out)
        ctypes.POINTER(vm_region_submap_info_data_64_t),  # info (out)
        ctypes.POINTER(ctypes.c_uint32),  # count (in/out)
    ]
    lib.mach_vm_region_recurse.restype = ctypes.c_uint32

    # mach_vm_read_overwrite(task, address, size, data, out_size)
    lib.mach_vm_read_overwrite.argtypes = [
        ctypes.c_uint64,  # task
        ctypes.c_uint64,  # address
        ctypes.c_uint64,  # size
        ctypes.c_void_p,  # data
        ctypes.POINTER(ctypes.c_uint64),  # out_size
    ]
    lib.mach_vm_read_overwrite.restype = ctypes.c_uint32

    # mach_task_self()
    lib.mach_task_self.argtypes = []
    lib.mach_task_self.restype = ctypes.c_uint64


def _iterate_regions_with_error(pm: Pymem) -> tuple[list[tuple[int, int]], int | None]:
    """Legacy-Wrapper: Baut PymemBackend und delegiert."""
    return PymemBackend(pm).iterate_readable_regions()


def _iterate_regions(pm: Pymem) -> list[tuple[int, int]]:
    """Legacy-Wrapper: Baut PymemBackend und delegiert."""
    regions, _ = PymemBackend(pm).iterate_readable_regions()
    return regions


# vm_region_submap_info_data_64_t.share_mode values that indicate a mapping
# shared across processes (dyld shared cache, shared libraries) — these never
# hold GC/managed-heap objects and can be skipped when hunting for live
# IL2CPP instances.
SM_SHARED = 4
SM_TRUESHARED = 5
SM_SHARED_ALIASED = 7
_SHARED_SHARE_MODES = frozenset({SM_SHARED, SM_TRUESHARED, SM_SHARED_ALIASED})


def _iterate_writable_private_regions(pm: Pymem) -> list[tuple[int, int]]:
    """Legacy-Wrapper: Baut PymemBackend und delegiert."""
    return PymemBackend(pm).iterate_writable_private_regions()


def _read_bytes_silent(pm: Pymem, addr: int, size: int) -> bytes | None:
    """Legacy-Wrapper: Baut PymemBackend und delegiert."""
    return PymemBackend(pm).read_bytes(addr, size)


def _scan_region(backend: MemoryBackend, addr: int, size: int, needle: bytes) -> tuple[list[int], int, int]:
    """Scannt eine Speicherregion chunkweise nach einem Byte-Pattern."""
    found: list[int] = []
    bytes_scanned = 0
    read_failures = 0
    if size <= 0:
        return found, bytes_scanned, read_failures

    overlap = max(0, len(needle) - 1)
    step = REGION_SCAN_CHUNK_SIZE if overlap == 0 else max(1, REGION_SCAN_CHUNK_SIZE - overlap)
    seen: set[int] = set()
    offset = 0
    while offset < size:
        chunk_size = min(REGION_SCAN_CHUNK_SIZE, size - offset)
        data = backend.read_bytes(addr + offset, chunk_size)
        if data is None:
            read_failures += 1
            break
        bytes_scanned += len(data)

        search_from = 0
        while True:
            pos = data.find(needle, search_from)
            if pos == -1:
                break
            absolute = addr + offset + pos
            if absolute not in seen:
                seen.add(absolute)
                found.append(absolute)
            search_from = pos + 1

        if offset + chunk_size >= size:
            break
        offset += step

    return found, bytes_scanned, read_failures


def _scan_region_many(
    backend: MemoryBackend,
    addr: int,
    size: int,
    needles: dict[int, bytes],
) -> tuple[dict[int, list[int]], int, int]:
    """Scannt eine Region einmal und sucht darin mehrere Byte-Patterns."""
    found: dict[int, list[int]] = {key: [] for key in needles}
    bytes_scanned = 0
    read_failures = 0
    if size <= 0 or not needles:
        return found, bytes_scanned, read_failures

    max_needle_len = max(len(needle) for needle in needles.values())
    overlap = max(0, max_needle_len - 1)
    step = REGION_SCAN_CHUNK_SIZE if overlap == 0 else max(1, REGION_SCAN_CHUNK_SIZE - overlap)
    seen: dict[int, set[int]] = {key: set() for key in needles}
    offset = 0
    while offset < size:
        chunk_size = min(REGION_SCAN_CHUNK_SIZE, size - offset)
        data = backend.read_bytes(addr + offset, chunk_size)
        if data is None:
            read_failures += 1
            break
        bytes_scanned += len(data)

        for key, needle in needles.items():
            if not needle:
                continue
            search_from = 0
            while True:
                pos = data.find(needle, search_from)
                if pos == -1:
                    break
                absolute = addr + offset + pos
                if absolute not in seen[key]:
                    seen[key].add(absolute)
                    found[key].append(absolute)
                search_from = pos + 1

        if offset + chunk_size >= size:
            break
        offset += step

    return found, bytes_scanned, read_failures


def scan_process_memory_with_stats(backend: MemoryBackend, needle: bytes) -> ScanResult:
    """Scannt den gesamten Prozess-Speicher nach einem Byte-Pattern.

    Args:
        backend: MemoryBackend-Instanz (attached an MTGA-Prozess)
        needle: Zu suchendes Byte-Pattern (z.B. struct.pack('<I', card_id))

    Returns:
        Liste der virtuellen Adressen, an denen das Pattern gefunden wurde.
    """
    if not needle:
        return ScanResult([], ScanStats(regions=0, bytes_scanned=0, read_failures=0, matches=0))

    regions, region_error = backend.iterate_readable_regions()
    found: list[int] = []
    bytes_scanned = 0
    read_failures = 0

    for addr, size in regions:
        region_found, region_bytes, region_failures = _scan_region(backend, addr, size, needle)
        found.extend(region_found)
        bytes_scanned += region_bytes
        read_failures += region_failures

    return ScanResult(
        found,
        ScanStats(
            regions=len(regions),
            bytes_scanned=bytes_scanned,
            read_failures=read_failures,
            matches=len(found),
            region_error=region_error,
        ),
    )


def scan_process_memory(backend: MemoryBackend, needle: bytes) -> list[int]:
    """Scannt den gesamten Prozess-Speicher nach einem Byte-Pattern."""
    return scan_process_memory_with_stats(backend, needle).addresses


def scan_process_memory_many_with_stats(backend: MemoryBackend, needles: dict[int, bytes]) -> MultiScanResult:
    """Scannt den gesamten Prozess-Speicher in einem Durchlauf nach mehreren Patterns."""
    active_needles = {key: needle for key, needle in needles.items() if needle}
    empty_addresses = {key: [] for key in needles}
    if not active_needles:
        return MultiScanResult(
            empty_addresses,
            ScanStats(regions=0, bytes_scanned=0, read_failures=0, matches=0),
        )

    regions, region_error = backend.iterate_readable_regions()
    found: dict[int, list[int]] = {key: [] for key in needles}
    bytes_scanned = 0
    read_failures = 0

    for addr, size in regions:
        region_found, region_bytes, region_failures = _scan_region_many(backend, addr, size, active_needles)
        for key, addresses in region_found.items():
            found[key].extend(addresses)
        bytes_scanned += region_bytes
        read_failures += region_failures

    matches = sum(len(addresses) for addresses in found.values())
    return MultiScanResult(
        found,
        ScanStats(
            regions=len(regions),
            bytes_scanned=bytes_scanned,
            read_failures=read_failures,
            matches=matches,
            region_error=region_error,
        ),
    )


def scan_process_memory_many(backend: MemoryBackend, needles: dict[int, bytes]) -> dict[int, list[int]]:
    """Scannt den gesamten Prozess-Speicher nach mehreren Byte-Patterns."""
    return scan_process_memory_many_with_stats(backend, needles).addresses
