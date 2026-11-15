"""Metadata-driven IL2CPP reader for the MTGA owned-card collection."""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from .il2cpp_nav import (
    MIN_VALID_PTR,
    NavigationReadError,
    class_of,
    discover_wrapper_controller_via_backref,
    read_dict_int_int_bulk,
    read_ref_field,
)


@dataclass
class CollectionIl2CppResult:
    cards: dict[int, int] = field(default_factory=dict)
    unique_cards: int = 0
    total_copies: int = 0
    playset_copies: int = 0
    path: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


def _finish(result: CollectionIl2CppResult, started: float) -> CollectionIl2CppResult:
    result.unique_cards = len(result.cards)
    result.total_copies = sum(result.cards.values())
    result.playset_copies = sum(min(quantity, 4) for quantity in result.cards.values())
    result.elapsed_ms = round((perf_counter() - started) * 1000)
    if not result.cards and not result.warnings:
        result.warnings.append("Collection leer: Navigation lieferte keine Karten")
    return result


def _pointer_step(reader: Any, result: CollectionIl2CppResult, source: int, field_name: str) -> int:
    found = read_ref_field(reader, source, field_name)
    if found is None:
        raise NavigationReadError(source, 0, field_name, "Feld nicht gefunden oder nicht lesbar")
    target, info = found
    result.path.append(f"  {field_name} @ +0x{info.offset:X}  0x{target:X}")
    if target < MIN_VALID_PTR or target % 8:
        raise NavigationReadError(source + info.offset, 8, field_name, f"ungueltiger Zielzeiger {target:#x}")
    return target


def read_collection(
    reader: Any, *, wrapper_instance: int = 0, wrapper_class: int = 0, debug: bool = False,
) -> CollectionIl2CppResult:
    """Follow WrapperController's inventory path and decode Cards.entries."""
    started = perf_counter()
    result = CollectionIl2CppResult()
    try:
        if not wrapper_instance or not wrapper_class:
            discovered = discover_wrapper_controller_via_backref(reader, debug=debug)
            if discovered is None:
                raise NavigationReadError(0, 0, "wrapper_instance", "WrapperController per Backref nicht gefunden")
            wrapper_instance, wrapper_class = discovered
        result.path.append(f"wrapper_instance = 0x{wrapper_instance:X}")
        if class_of(reader, wrapper_instance) != wrapper_class:
            raise NavigationReadError(wrapper_instance, 8, "wrapper_instance", "Klassenzeiger passt nicht")
        manager = _pointer_step(reader, result, wrapper_instance, "<InventoryManager>k__BackingField")
        service = None
        for name in ("InventoryServiceWrapper", "_inventoryServiceWrapper"):
            try:
                service = _pointer_step(reader, result, manager, name)
                break
            except NavigationReadError:
                continue
        if service is None:
            raise NavigationReadError(manager, 0, "InventoryServiceWrapper", "Feld nicht gefunden")
        cards_object = _pointer_step(reader, result, service, "<Cards>k__BackingField")
        entries = None
        for name in ("entries", "_entries"):
            try:
                entries = _pointer_step(reader, result, cards_object, name)
                break
            except NavigationReadError:
                continue
        if entries is None:
            raise NavigationReadError(cards_object, 0, "entries", "Feld nicht gefunden")
        cards, layout = read_dict_int_int_bulk(reader, entries)
        result.cards = cards
        result.path[-1] += f" (Array, len={layout['length']}, stride={layout['stride']})"
        result.path.append(f"  count={layout['count']}, gelesen={len(cards)}, freie Slots={layout['free_slots']}")
        if not cards:
            result.warnings.append("entries dekodiert, aber keine belegten Karten gefunden")
    except Exception as exc:  # Memory backends raise backend-specific read errors.
        result.warnings.append(f"Collection-Navigation fehlgeschlagen bei {getattr(exc, 'step', 'read')}: {exc}")
    return _finish(result, started)


def scan_collection_il2cpp(adapter: Any, *, debug: bool = False) -> CollectionIl2CppResult:
    """Best-effort adapter entry point: never expose a raw memory-read error."""
    return read_collection(adapter, debug=debug)
