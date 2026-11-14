"""Regression tests for partial reads around collection anchors."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from scanner import memory_scanner


WINDOW_BYTES = 1024 * 1024
ADDR = 0x100000
WINDOW_START = ADDR - WINDOW_BYTES // 2


def _block_bytes() -> bytes:
    pairs = [(10_000 + index, 1) for index in range(60)]
    pairs.extend([(999, 0)] * 70)
    return b"".join(struct.pack("<II", *pair) for pair in pairs)


class HoleBackend:
    def __init__(self, memory: bytes, holes: list[tuple[int, int]]) -> None:
        self.memory = memory
        self.holes = holes
        self.calls: list[tuple[int, int]] = []

    def read_bytes(self, addr: int, size: int) -> bytes | None:
        self.calls.append((addr, size))
        end = addr + size
        if any(addr < hole_end and hole_start < end for hole_start, hole_end in self.holes):
            return None
        offset = addr - WINDOW_START
        return self.memory[offset:offset + size]


@pytest.fixture(autouse=True)
def small_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(memory_scanner, "SCAN_RANGE_MB", 1)
    monkeypatch.setattr(memory_scanner, "READ_CHUNK_SIZE", 64 * 1024)
    monkeypatch.setattr(memory_scanner, "MIN_READ_SIZE", 16 * 1024)
    monkeypatch.setattr(memory_scanner, "DEFAULT_ANCHORS", ((10_000, 1, "Anchor"),))
    monkeypatch.setattr(
        memory_scanner, "get_user_anchors", lambda _name_to_id, **_kwargs: [(10_000, 1, "Anchor")]
    )


def _memory_at(offset: int) -> bytes:
    memory = bytearray(WINDOW_BYTES)
    block = _block_bytes()
    memory[offset:offset + len(block)] = block
    return bytes(memory)


def _scan(backend: HoleBackend):
    anchors = [(10_000, 1, "Anchor")]
    return memory_scanner.scan_collection_detailed(
        backend=backend,
        db_loader=lambda: {10_000 + i: {"name": f"Card {i}"} for i in range(60)},
        memory_scanner=lambda _backend, _needle: [ADDR],
        print_fn=lambda *args, **kwargs: None,
        input_fn=lambda _prompt: "",
        non_interactive=True,
    )


def test_hole_outside_block_keeps_block_and_reports_partial() -> None:
    backend = HoleBackend(_memory_at(64 * 1024), [(WINDOW_START + 512 * 1024, WINDOW_START + 528 * 1024)])

    result = _scan(backend)

    assert result is not None
    assert len(result.collection) == 60
    coverage = result.read_coverage
    assert coverage["windows_partial"] == 1
    assert coverage["windows_unreadable"] == 0
    assert coverage["anchors"][0]["bytesRead"] < WINDOW_BYTES
    assert any(w.startswith("anchor-window-partial: address=0x100000 arenaId=10000") for w in result.validation["warnings"])


def test_hole_inside_block_discards_candidate() -> None:
    block_offset = 64 * 1024
    backend = HoleBackend(
        _memory_at(block_offset),
        [(WINDOW_START + block_offset + 400, WINDOW_START + block_offset + 400 + 16 * 1024)],
    )

    result = _scan(backend)

    assert result is not None
    assert result.collection == {}
    assert result.read_coverage["windows_partial"] == 1
    assert any(w.startswith("anchor-window-partial: address=0x100000") for w in result.validation["warnings"])


def test_completely_unreadable_window_is_reported_without_crash() -> None:
    backend = HoleBackend(_memory_at(64 * 1024), [(WINDOW_START, WINDOW_START + WINDOW_BYTES)])

    result = _scan(backend)

    assert result is not None
    assert result.collection == {}
    assert result.read_coverage["windows_unreadable"] == 1
    assert result.read_coverage["bytes_unreadable"] == WINDOW_BYTES
    assert any(w.startswith("anchor-window-unreadable:") for w in result.validation["warnings"])


def test_failed_chunk_does_not_skip_later_chunks() -> None:
    backend = HoleBackend(_memory_at(64 * 1024), [(WINDOW_START + 512 * 1024, WINDOW_START + 528 * 1024)])

    _buffer, ranges = memory_scanner._read_window_with_coverage(backend, WINDOW_START, WINDOW_BYTES)

    assert any(start == 0 and end >= 512 * 1024 for start, end in ranges)
    assert any(end == WINDOW_BYTES for _start, end in ranges)
    assert any(addr >= WINDOW_START + 576 * 1024 for addr, _size in backend.calls)


def test_intact_window_matches_legacy_extraction_and_writes_coverage(tmp_path: Path) -> None:
    backend = HoleBackend(_memory_at(64 * 1024), [])

    result = _scan(backend)
    assert result is not None
    legacy = []
    data = _memory_at(64 * 1024)
    for stride in memory_scanner.STRIDES_WORDS:
        for offset in range(stride):
            legacy.extend(memory_scanner._extract_blocks(data, stride, offset))
    assert memory_scanner.find_blocks_with_dupes(backend, ADDR) == legacy
    assert result.read_coverage["windows_complete"] == 1

    collection_path, _report_path = memory_scanner.write_collection_artifacts(
        result.collection, tmp_path, scan_result=result
    )
    payload = json.loads(collection_path.read_text(encoding="utf-8"))
    assert payload["diagnostics"]["readCoverage"]["windows_complete"] == 1
