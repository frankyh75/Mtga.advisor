"""Synthetic metadata tests for the IL2CPP collection reader."""
from __future__ import annotations

import struct
import pytest

from scanner.collection_il2cpp import read_collection
from scanner.il2cpp_nav import IL2CPP_OFFSETS, MockMemory, class_static_storage


def ptr(value: int) -> bytes:
    return struct.pack("<Q", value)


def i32(value: int) -> bytes:
    return struct.pack("<i", value)


def u32(value: int) -> bytes:
    return struct.pack("<I", value)


class Graph:
    def __init__(self, stride: int, type_code: int = 8, missing: str = "") -> None:
        self.mem = MockMemory()
        self.next = 0x100000
        self.stride = stride
        self.type_code = type_code
        self.missing = missing
        self.wrapper = 0x300000
        self.wrapper_class = 0x330000
        self.wrapper_base = 0x310000
        self.manager = 0x400000
        self.manager_class = 0x410000
        self.service = 0x500000
        self.service_class = 0x510000
        self.service_base = 0x520000
        self.cards = 0x600000
        self.cards_class = 0x610000
        self.dictionary = 0x700000
        self.array = 0x800000
        self.array_class = 0x810000
        self.entry_class = 0x820000
        self.build()

    def text(self, value: str) -> int:
        address = self.next
        self.next += 0x100
        self.mem.write(address, value.encode() + b"\0")
        return address

    def klass(self, address: int, fields: list[tuple[str, int, int]], parent: int = 0, instance_size: int = 0x30) -> None:
        raw = bytearray(0x140)
        field_base = self.next
        self.next += max(0x100, 0x20 * len(fields))
        raw[0x48:0x50] = ptr(parent)
        raw[0x80:0x88] = ptr(field_base)
        raw[0xf8:0xfc] = u32(instance_size)
        raw[0x124:0x128] = u32(len(fields))
        self.mem.write(address, raw)
        for index, (name, offset, code) in enumerate(fields):
            type_addr = self.next
            self.next += 0x20
            self.mem.write(type_addr + 8, u32(code << 16))
            info = ptr(self.text(name)) + ptr(type_addr) + ptr(address) + i32(offset) + u32(0)
            self.mem.write(field_base + index * 0x20, info)

    def build(self) -> None:
        self.klass(self.wrapper_base, [] if self.missing == "manager" else [("<InventoryManager>k__BackingField", 0x28, 0)])
        self.klass(self.wrapper_class, [], self.wrapper_base)
        self.klass(self.manager_class, [] if self.missing == "service" else [("InventoryServiceWrapper", 0x30, 0)])
        self.klass(self.service_base, [] if self.missing == "cards" else [("<Cards>k__BackingField", 0x38, 0)])
        self.klass(self.service_class, [], self.service_base)
        self.klass(self.cards_class, [] if self.missing == "entries" else [("entries", 0x18, 0)])
        self.klass(self.entry_class, [("next", 0x10, 8), ("hashCode", 0x14, 8), ("key", 0x18, self.type_code), ("value", 0x1c, self.type_code)], instance_size=self.stride + 0x10)
        self.klass(self.array_class, [], instance_size=0x30)
        self.mem.write(self.array_class + 0x40, ptr(self.entry_class))
        # Object class headers.
        self.mem.write(self.wrapper, ptr(self.wrapper_class))
        self.mem.write(self.wrapper + 0x28, ptr(self.manager))
        self.mem.write(self.manager, ptr(self.manager_class))
        self.mem.write(self.manager + 0x30, ptr(self.service))
        self.mem.write(self.service, ptr(self.service_class))
        self.mem.write(self.service + 0x38, ptr(self.cards))
        self.mem.write(self.cards, ptr(self.cards_class))
        self.mem.write(self.cards + 0x18, ptr(self.dictionary))
        self.mem.write(self.dictionary + 0x18, ptr(self.array))
        self.mem.write(self.dictionary + 0x20, i32(3))
        self.mem.write(self.array, ptr(self.array_class))
        self.mem.write(self.array + 0x18, i32(4))
        for slot, (hash_code, key, quantity) in enumerate(((11, 1001, 2), (-1, 0, 0), (12, 900000, 5), (13, 1002, 8))):
            raw = bytearray(self.stride)
            struct.pack_into("<i", raw, 4, hash_code)
            struct.pack_into("<I", raw, 8, key)
            struct.pack_into("<I", raw, 12, quantity)
            self.mem.write(self.array + 0x20 + slot * self.stride, raw)


@pytest.mark.parametrize("stride", [16, 24, 32])
@pytest.mark.parametrize("code", [8, 9])
def test_collection_decodes_metadata_stride_and_unsigned_values(stride: int, code: int) -> None:
    graph = Graph(stride, code)
    result = read_collection(graph.mem, wrapper_instance=graph.wrapper, wrapper_class=graph.wrapper_class)
    assert result.cards == {1001: 2, 900000: 5}
    assert result.unique_cards == 2
    assert result.total_copies == 7
    assert result.playset_copies == 6
    assert not result.warnings
    assert any("stride=" + str(stride) in item for item in result.path)
    assert any("freie Slots=1" in item for item in result.path)


def test_missing_field_returns_explained_empty_result() -> None:
    graph = Graph(16, missing="cards")
    result = read_collection(graph.mem, wrapper_instance=graph.wrapper, wrapper_class=graph.wrapper_class)
    assert result.cards == {}
    assert result.warnings
    assert "<Cards>k__BackingField" in result.warnings[0]


def test_static_storage_uses_v31_offset_b8_not_a8() -> None:
    mem = MockMemory()
    klass = 0x300000
    mem.write(klass + 0xa8, ptr(0x111111))
    mem.write(klass + 0xb8, ptr(0x222222))
    assert IL2CPP_OFFSETS["class_static_fields"] == 0xb8
    assert class_static_storage(mem, klass) == 0x222222
