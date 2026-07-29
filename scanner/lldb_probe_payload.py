"""LLDB-side memory probe for MTGA anchors.

This module is imported by LLDB's embedded Python interpreter. It reads a JSON
config path from MTGA_LLDB_PROBE_CONFIG and prints one machine-readable result
line prefixed with MTGA_LLDB_PROBE_RESULT=.
"""

from __future__ import annotations

import json
import os
import struct
import sys

import lldb

RESULT_PREFIX = "MTGA_LLDB_PROBE_RESULT="


def _find_all(data: bytes, needle: bytes) -> list[int]:
    positions: list[int] = []
    offset = 0
    while True:
        pos = data.find(needle, offset)
        if pos == -1:
            return positions
        positions.append(pos)
        offset = pos + 1


def _find_in_region_native(
    target: lldb.SBTarget,
    process: lldb.SBProcess,
    start: int,
    size: int,
    pattern: bytes,
    max_matches: int,
) -> list[int]:
    """Uses LLDB's native memory finder without copying whole regions to Python."""
    found: list[int] = []
    cursor = start
    end = start + size
    error = lldb.SBError()

    while cursor < end and len(found) < max_matches:
        address = target.ResolveLoadAddress(cursor)
        if not address.IsValid():
            break
        search_range = lldb.SBAddressRange(address, end - cursor)
        match = int(process.FindInMemory(pattern, search_range, 1, error))
        if not error.Success():
            error.Clear()
            break
        if match < cursor or match >= end or match == 0xFFFFFFFFFFFFFFFF:
            break
        found.append(match)
        cursor = match + 1

    return found


def run() -> None:
    config_path = os.environ["MTGA_LLDB_PROBE_CONFIG"]
    with open(config_path, "r", encoding="utf-8") as handle:
        config = json.load(handle)

    target = lldb.debugger.GetSelectedTarget()
    process = target.GetProcess()
    error = lldb.SBError()
    regions = process.GetMemoryRegions()

    card_ids = [int(card_id) for card_id in config["card_ids"]]
    patterns = {card_id: struct.pack("<I", card_id) for card_id in card_ids}
    matches = {str(card_id): [] for card_id in card_ids}

    max_matches_per_id = int(config.get("max_matches_per_id", 256))
    max_regions = config.get("max_regions")
    max_region_bytes = int(config.get("max_region_bytes", 1024 * 1024 * 1024))
    chunk_size = int(config.get("chunk_size", 4 * 1024 * 1024))
    overlap = max(len(pattern) for pattern in patterns.values()) - 1
    step = max(1, chunk_size - overlap)
    mode = config.get("mode", "native-find")

    scanned_regions = 0
    scanned_bytes = 0
    read_failures = 0

    for index in range(regions.GetSize()):
        if max_regions is not None and scanned_regions >= int(max_regions):
            break

        region = lldb.SBMemoryRegionInfo()
        regions.GetMemoryRegionAtIndex(index, region)
        start = int(region.GetRegionBase())
        end = int(region.GetRegionEnd())
        size = max(0, end - start)

        if not region.IsMapped() or not region.IsReadable() or size <= 0:
            continue
        if size > max_region_bytes:
            continue

        scanned_regions += 1
        if mode == "native-find":
            for card_id, pattern in patterns.items():
                bucket = matches[str(card_id)]
                if len(bucket) >= max_matches_per_id:
                    continue
                remaining = max_matches_per_id - len(bucket)
                bucket.extend(_find_in_region_native(target, process, start, size, pattern, remaining))
            continue

        offset = 0
        while offset < size:
            current_size = min(chunk_size, size - offset)
            data = process.ReadMemory(start + offset, current_size, error)
            if not error.Success() or not data:
                read_failures += 1
                error.Clear()
                break

            scanned_bytes += len(data)
            for card_id, pattern in patterns.items():
                bucket = matches[str(card_id)]
                if len(bucket) >= max_matches_per_id:
                    continue
                for pos in _find_all(data, pattern):
                    bucket.append(start + offset + pos)
                    if len(bucket) >= max_matches_per_id:
                        break

            if offset + current_size >= size:
                break
            offset += step

    result = {
        "regions_total": regions.GetSize(),
        "regions_scanned": scanned_regions,
        "bytes_scanned": scanned_bytes,
        "read_failures": read_failures,
        "matches": {
            card_id: {
                "count": len(addresses),
                "sample": [hex(address) for address in addresses[:10]],
            }
            for card_id, addresses in matches.items()
        },
    }
    print(RESULT_PREFIX + json.dumps(result, sort_keys=True))
    sys.stdout.flush()
