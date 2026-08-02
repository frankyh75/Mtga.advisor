"""Deck Memory Scanner — pattern-based extraction of full card lists from MTGA's
_allDecks structure in IL2CPP memory.

This module mirrors what mtgatool's queries.rs ``decks_from()`` does for Mono,
but uses the same pattern-scanning infrastructure as ``memory_scanner.py`` to
find deck card data by scanning for known grpIds and parsing pile-structured
blocks.

Memory layout (from queries.rs):
  WrapperController._instance
    → DecksManager._deckDataProvider
      → _allDecks (Dictionary<uint, Deck>)
        → Deck._contents
          → Piles (Dictionary<EDeckPile, List<CardQuantity>>)
            → CardQuantity: { grpId: uint, quantity: int }

EDeckPile enum:
  0 = Invalid, 1 = Main, 2 = Sideboard, 3 = CommandZone, 4 = Companions

For pattern-based scanning, we search for known card grpIds in memory, then
parse the surrounding bytes for pile-structured blocks: sequences of
(pile_type, card_count, [(grpId, quantity)]*) that represent a deck's contents.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from pymem import Pymem
except ImportError:  # pragma: no cover
    class Pymem:  # type: ignore[no-redef]
        pass

from . import pattern_scanner as _ps
from .pattern_scanner import ScanStats

# ---------------------------------------------------------------------------
# Constants — EDeckPile enum values (Wizards.Mtga.Decks)
# ---------------------------------------------------------------------------

PILE_INVALID = 0
PILE_MAIN = 1
PILE_SIDEBOARD = 2
PILE_COMMAND_ZONE = 3
PILE_COMPANIONS = 4

PILE_NAMES: dict[int, str] = {
    PILE_INVALID: "Invalid",
    PILE_MAIN: "Main",
    PILE_SIDEBOARD: "Sideboard",
    PILE_COMMAND_ZONE: "CommandZone",
    PILE_COMPANIONS: "Companions",
}

# Pile type → output key
PILE_KEYS: dict[int, str] = {
    PILE_MAIN: "mainboard",
    PILE_SIDEBOARD: "sideboard",
    PILE_COMMAND_ZONE: "commandZone",
    PILE_COMPANIONS: "companions",
}

# Valid pile types for filtering
VALID_PILE_TYPES = frozenset({PILE_MAIN, PILE_SIDEBOARD, PILE_COMMAND_ZONE, PILE_COMPANIONS})

# Plausibility limits (matching queries.rs guards)
MAX_PILE_SIZE = 5000
MAX_CARDS_PER_PILE = 1000
MIN_GRP_ID = 1000
MAX_GRP_ID = 500000
MIN_QTY = 1
MAX_QTY = 400

# Scanning parameters
BLOCK_READ_SIZE = 4 * 1024 * 1024  # 4 MB around each anchor
BLOCK_READ_BACK = 1 * 1024 * 1024  # 1 MB before anchor
MIN_PILE_CARDS = 1
MAX_PILES_PER_BLOCK = 10


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class DeckMemoryResult:
    """A single deck found in memory with its piles."""
    name: str = ""
    deck_id: str = ""
    piles: dict[int, dict[int, int]] = field(default_factory=dict)
    # pile_type → {grpId: quantity}
    last_updated: str = ""
    raw_address: int = 0  # memory address where this deck block was found

    def card_count(self, pile_type: int) -> int:
        """Total card count for a specific pile type."""
        return sum(self.piles.get(pile_type, {}).values())

    def unique_card_count(self, pile_type: int) -> int:
        """Number of unique cards in a pile."""
        return len(self.piles.get(pile_type, {}))

    def to_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dict matching InternalDeck schema."""
        cards: dict[str, dict[str, int]] = {}
        for pile_type, key in PILE_KEYS.items():
            pile = self.piles.get(pile_type, {})
            if pile:
                cards[key] = {str(grp_id): qty for grp_id, qty in sorted(pile.items())}
        return {
            "name": self.name,
            "deckId": self.deck_id,
            "cards": cards,
            "lastUpdated": self.last_updated,
        }


@dataclass
class DeckScanResult:
    """Result of a full deck memory scan."""
    decks: list[DeckMemoryResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scan_stats: ScanStats | None = None


# ---------------------------------------------------------------------------
# Pile list parsing — reads List<CardQuantity> backing array data
# ---------------------------------------------------------------------------

def parse_pile_list_from_bytes(data: bytes, offset: int) -> list[tuple[int, int]]:
    """Parse a pile's card list from raw bytes.

    Expected layout at ``offset``:
      size: i32 (4 bytes) — number of card entries
      card entries: grpId(u32) + quantity(i32) × size

    This mirrors ``read_pile_list()`` in queries.rs, but operates on a flat
    byte buffer instead of reading from a live process.

    Args:
        data: Raw bytes containing the pile list.
        offset: Byte offset where the size field starts.

    Returns:
        List of (grpId, quantity) tuples. Invalid entries are filtered.
    """
    if offset + 4 > len(data):
        return []

    size = struct.unpack_from("<i", data, offset)[0]
    if size <= 0 or size > MAX_PILE_SIZE:
        return []

    cards: list[tuple[int, int]] = []
    entry_start = offset + 4
    for i in range(size):
        entry_off = entry_start + i * 8
        if entry_off + 8 > len(data):
            break  # truncated
        grp_id = struct.unpack_from("<I", data, entry_off)[0]
        qty = struct.unpack_from("<i", data, entry_off + 4)[0]
        if MIN_GRP_ID <= grp_id <= MAX_GRP_ID and MIN_QTY <= qty <= MAX_QTY:
            cards.append((grp_id, qty))
    return cards


# ---------------------------------------------------------------------------
# Pile dictionary parsing — reads pile-structured blocks
# ---------------------------------------------------------------------------

def parse_piles_from_bytes(data: bytes) -> list[tuple[int, list[tuple[int, int]]]]:
    """Parse pile-structured data from a flat byte buffer.

    Scans for pile headers: sequences of (pile_type: i32, card_count: i32)
    followed by card_count × (grpId: u32, quantity: i32) entries.

    Valid pile types: 1 (Main), 2 (Sideboard), 3 (CommandZone), 4 (Companions).

    Args:
        data: Raw bytes potentially containing pile-structured data.

    Returns:
        List of (pile_type, [(grpId, quantity), ...]) tuples.
    """
    if len(data) < 8:
        return []

    results: list[tuple[int, list[tuple[int, int]]]] = []
    offset = 0
    # We scan through the data looking for valid pile headers.
    # A valid pile header has:
    #   - pile_type in VALID_PILE_TYPES
    #   - card_count in plausible range
    #   - Following card entries with valid grpId and quantity
    while offset + 8 <= len(data):
        pile_type, card_count = struct.unpack_from("<ii", data, offset)
        if pile_type in VALID_PILE_TYPES and 0 < card_count <= MAX_PILE_SIZE:
            # Check if the following bytes look like valid card entries
            entry_start = offset + 8
            cards = _try_parse_cards(data, entry_start, card_count)
            if cards and len(cards) >= MIN_PILE_CARDS:
                results.append((pile_type, cards))
                offset = entry_start + len(cards) * 8
                continue
        offset += 1  # advance by 1 byte for thorough alignment scanning

    return results


def _try_parse_cards(
    data: bytes, offset: int, expected_count: int
) -> list[tuple[int, int]]:
    """Try to parse ``expected_count`` card entries starting at ``offset``.

    Returns the list of valid (grpId, quantity) pairs. Stops early if
    data is exhausted or an invalid entry is encountered (heuristic: if
    the first 3 entries are all invalid, this probably isn't a card list).
    """
    cards: list[tuple[int, int]] = []
    consecutive_invalid = 0

    for i in range(expected_count):
        entry_off = offset + i * 8
        if entry_off + 8 > len(data):
            break
        grp_id = struct.unpack_from("<I", data, entry_off)[0]
        qty = struct.unpack_from("<i", data, entry_off + 4)[0]
        if MIN_GRP_ID <= grp_id <= MAX_GRP_ID and MIN_QTY <= qty <= MAX_QTY:
            cards.append((grp_id, qty))
            consecutive_invalid = 0
        else:
            consecutive_invalid += 1
            if consecutive_invalid >= 3 and len(cards) == 0:
                return []  # not a card list
            if consecutive_invalid >= 5:
                break  # too many invalid entries, stop

    return cards


# ---------------------------------------------------------------------------
# Deck block parsing — extracts a full DeckMemoryResult from a memory block
# ---------------------------------------------------------------------------

def parse_deck_block(data: bytes, offset: int = 0) -> DeckMemoryResult | None:
    """Parse a memory block that contains pile-structured deck data.

    Scans the block for pile headers and extracts card lists per pile.
    Also attempts to extract a deck name if present (UTF-16LE string with
    length prefix, matching Unity/IL2CPP string layout).

    Args:
        data: Raw bytes of the memory block.
        offset: Starting offset within the data.

    Returns:
        DeckMemoryResult with parsed piles, or None if no valid piles found.
    """
    if len(data) < 8:
        return None

    # Try to find a name (UTF-16LE string with length prefix) near the start.
    name = _try_extract_name(data, offset)

    # Parse piles from the block
    piles = parse_piles_from_bytes(data)

    if not piles:
        return None

    # Convert to pile_type → {grpId: quantity}
    pile_dict: dict[int, dict[int, int]] = {}
    for pile_type, cards in piles:
        if pile_type not in pile_dict:
            pile_dict[pile_type] = {}
        for grp_id, qty in cards:
            pile_dict[pile_type][grp_id] = qty

    # Verify we have at least a Main pile with cards
    if PILE_MAIN not in pile_dict or not pile_dict[PILE_MAIN]:
        # Some decks might only have sideboard visible; accept if any pile has cards
        if not any(pile_dict.values()):
            return None

    return DeckMemoryResult(
        name=name,
        deck_id="",
        piles=pile_dict,
        last_updated="",
        raw_address=offset,
    )


def _try_extract_name(data: bytes, offset: int) -> str:
    """Attempt to extract a UTF-16LE string name from the start of a block.

    IL2CPP/Mono strings have layout:
      length: i32 (character count, not bytes)
      data: UTF-16LE encoded characters

    We look for a plausible string near the start of the block.
    """
    if offset + 4 > len(data):
        return ""

    # Try at offset 0 and a few nearby positions
    for try_offset in range(offset, min(offset + 64, len(data) - 4), 4):
        if try_offset + 4 > len(data):
            break
        char_count = struct.unpack_from("<i", data, try_offset)[0]
        # Deck names are typically 1-100 characters
        if 1 <= char_count <= 100:
            str_start = try_offset + 4
            str_end = str_start + char_count * 2
            if str_end <= len(data):
                try:
                    name = data[str_start:str_end].decode("utf-16-le", errors="strict")
                    # Filter out strings with control characters (likely not a name)
                    if all(c.isprintable() or c in " " for c in name) and name.strip():
                        return name
                except (UnicodeDecodeError, ValueError):
                    continue
    return ""


# ---------------------------------------------------------------------------
# Memory block finding — reads memory around anchor addresses
# ---------------------------------------------------------------------------

def find_deck_blocks(
    pm: Pymem,
    anchor_addresses: list[int],
    *,
    read_back: int = BLOCK_READ_BACK,
    read_size: int = BLOCK_READ_SIZE,
) -> list[bytes]:
    """Read memory blocks around anchor addresses.

    For each anchor address, reads a window of memory and returns the raw
    bytes. These blocks are then parsed for pile-structured data.

    Args:
        pm: Pymem instance attached to MTGA.
        anchor_addresses: List of virtual addresses where anchor grpIds were found.
        read_back: How many bytes to read before the anchor address.
        read_size: Total size of the read window.

    Returns:
        List of raw byte blocks (one per anchor address).
    """
    blocks: list[bytes] = []
    for addr in anchor_addresses:
        block_start = max(0, addr - read_back)
        data = _ps._read_bytes_silent(pm, block_start, read_size)
        if data is not None and len(data) > 0:
            blocks.append(data)
    return blocks


# ---------------------------------------------------------------------------
# Deck merging — deduplicate and merge complementary results
# ---------------------------------------------------------------------------

def merge_deck_results(
    candidates: list[DeckMemoryResult],
    *,
    name_similarity_threshold: float = 0.8,
) -> list[DeckMemoryResult]:
    """Merge and deduplicate deck scan results.

    Two DeckMemoryResults are considered the same deck if:
      - They have the same deck_id (non-empty), OR
      - They have the same name (non-empty) AND overlapping main pile cards

    When merging, complementary piles are combined (e.g., one result has
    Main, another has Sideboard → merged result has both).

    Args:
        candidates: List of DeckMemoryResult candidates from block parsing.
        name_similarity_threshold: Not currently used; reserved for fuzzy matching.

    Returns:
        Deduplicated and merged list of DeckMemoryResult.
    """
    if not candidates:
        return []

    merged: list[DeckMemoryResult] = []

    for candidate in candidates:
        match_idx = _find_matching_deck(merged, candidate)
        if match_idx is not None:
            _merge_into(merged[match_idx], candidate)
        else:
            merged.append(DeckMemoryResult(
                name=candidate.name,
                deck_id=candidate.deck_id,
                piles=dict(candidate.piles),
                last_updated=candidate.last_updated,
                raw_address=candidate.raw_address,
            ))

    return merged


def _find_matching_deck(
    decks: list[DeckMemoryResult],
    candidate: DeckMemoryResult,
) -> int | None:
    """Find the index of a deck in ``decks`` that matches ``candidate``."""
    for i, deck in enumerate(decks):
        # Match by deck_id
        if candidate.deck_id and deck.deck_id == candidate.deck_id:
            return i
        # Match by name + card overlap
        if candidate.name and deck.name == candidate.name:
            if _piles_overlap(deck.piles, candidate.piles):
                return i
        # Match by card overlap alone (same main pile cards)
        if not candidate.name and not deck.name:
            if _piles_overlap(deck.piles, candidate.piles):
                return i
    return None


def _piles_overlap(
    piles_a: dict[int, dict[int, int]],
    piles_b: dict[int, dict[int, int]],
) -> bool:
    """Check if two pile dicts share at least 50% of main pile cards."""
    main_a = piles_a.get(PILE_MAIN, {})
    main_b = piles_b.get(PILE_MAIN, {})
    if not main_a or not main_b:
        # If no main pile, check any pile
        for pile_type in VALID_PILE_TYPES:
            pa = piles_a.get(pile_type, {})
            pb = piles_b.get(pile_type, {})
            if pa and pb:
                overlap = len(set(pa.keys()) & set(pb.keys()))
                return overlap >= min(len(pa), len(pb)) * 0.5
        return False
    overlap = len(set(main_a.keys()) & set(main_b.keys()))
    return overlap >= min(len(main_a), len(main_b)) * 0.5


def _merge_into(target: DeckMemoryResult, source: DeckMemoryResult) -> None:
    """Merge source piles into target. Mutates target in-place."""
    for pile_type, cards in source.piles.items():
        if pile_type not in target.piles:
            target.piles[pile_type] = dict(cards)
        else:
            for grp_id, qty in cards.items():
                # Prefer the higher quantity (more complete scan)
                if grp_id not in target.piles[pile_type] or qty > target.piles[pile_type][grp_id]:
                    target.piles[pile_type][grp_id] = qty
    # Take the name if target doesn't have one
    if not target.name and source.name:
        target.name = source.name
    if not target.deck_id and source.deck_id:
        target.deck_id = source.deck_id


# ---------------------------------------------------------------------------
# Full deck scan — main entry point
# ---------------------------------------------------------------------------

def scan_decks(
    pm: Pymem,
    anchor_grp_ids: list[int],
    *,
    deck_names: dict[int, str] | None = None,
    read_back: int = BLOCK_READ_BACK,
    read_size: int = BLOCK_READ_SIZE,
    memory_scanner: Callable[..., Any] | None = None,
    block_finder: Callable[[Pymem, list[int]], list[bytes]] | None = None,
) -> DeckScanResult:
    """Scan MTGA process memory for deck data.

    Uses anchor card grpIds to find candidate memory regions, then parses
    those regions for pile-structured deck data.

    Args:
        pm: Pymem instance attached to MTGA.
        anchor_grp_ids: List of known card grpIds to search for as anchors.
        deck_names: Optional mapping of grpId → deck name for identification.
        read_back: Bytes to read before each anchor address.
        read_size: Total read window size per anchor.
        memory_scanner: Override for the memory scanning function (testing).
        block_finder: Override for the block finding function (testing).

    Returns:
        DeckScanResult with found decks and any warnings.
    """
    warnings: list[str] = []
    candidates: list[DeckMemoryResult] = []
    scan_stats: ScanStats | None = None

    if not anchor_grp_ids:
        return DeckScanResult(warnings=["no anchor grpIds provided"])

    # 1. Scan memory for anchor grpIds
    needles = {grp_id: struct.pack("<I", grp_id) for grp_id in anchor_grp_ids}

    if memory_scanner is not None:
        # Allow injection for testing
        scan_result = memory_scanner(pm, needles)
        if hasattr(scan_result, "addresses"):
            all_addresses: list[int] = []
            for grp_id in anchor_grp_ids:
                all_addresses.extend(scan_result.addresses.get(grp_id, []))
            if hasattr(scan_result, "stats"):
                scan_stats = scan_result.stats
        else:
            all_addresses = list(scan_result)
    else:
        scan_result = _ps.scan_process_memory_many_with_stats(pm, needles)
        scan_stats = scan_result.stats
        all_addresses: list[int] = []
        for grp_id in anchor_grp_ids:
            all_addresses.extend(scan_result.addresses.get(grp_id, []))

    if not all_addresses:
        return DeckScanResult(warnings=["no anchor matches found in memory"])

    # 2. Read memory blocks around anchor addresses
    if block_finder is not None:
        blocks = block_finder(pm, all_addresses)
    else:
        blocks = find_deck_blocks(pm, all_addresses, read_back=read_back, read_size=read_size)

    if not blocks:
        return DeckScanResult(warnings=["no readable memory blocks around anchors"])

    # 3. Parse each block for pile-structured deck data
    for block in blocks:
        result = parse_deck_block(block, 0)
        if result is not None and any(result.piles.values()):
            candidates.append(result)

    if not candidates:
        return DeckScanResult(warnings=["no valid deck blocks parsed"], scan_stats=scan_stats)

    # 4. Merge and deduplicate
    decks = merge_deck_results(candidates)

    if not decks:
        return DeckScanResult(warnings=["no decks after merge"], scan_stats=scan_stats)

    # 5. Post-process: assign names from deck_names if available
    if deck_names:
        for deck in decks:
            if not deck.name:
                # Try to find a name based on anchor cards
                for grp_id, name in deck_names.items():
                    if grp_id in deck.piles.get(PILE_MAIN, {}):
                        deck.name = name
                        break

    return DeckScanResult(decks=decks, warnings=warnings, scan_stats=scan_stats)


# ---------------------------------------------------------------------------
# Artifact writing — produces JSON output files
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_deck_artifacts(
    result: DeckScanResult,
    output_dir: Path,
    *,
    card_db: dict[int, dict[str, Any]] | None = None,
) -> tuple[Path, Path]:
    """Write deck scan results to JSON files.

    Produces:
      - ``decks-container.json``: All decks in container format (schema: decks-container.v1)
      - ``decks/`` directory: Individual deck files (schema: deck.v1)

    Args:
        result: DeckScanResult from scan_decks().
        output_dir: Directory to write output files.
        card_db: Optional card database for name resolution (grpId → name).

    Returns:
        Tuple of (decks_dir_path, container_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    decks_dir = output_dir / "decks"
    decks_dir.mkdir(exist_ok=True)

    def resolve_name(grp_id: int) -> str:
        if card_db and grp_id in card_db:
            return card_db[grp_id].get("name", f"ID:{grp_id}")
        return f"ID:{grp_id}"

    def cards_to_list(pile: dict[int, int]) -> list[dict[str, Any]]:
        return [
            {"cardId": grp_id, "name": resolve_name(grp_id), "count": qty}
            for grp_id, qty in sorted(pile.items())
        ]

    # Write individual deck files
    for deck in result.decks:
        deck_dict = deck.to_dict()
        deck_id = deck.deck_id or f"deck-{deck.raw_address:#x}"
        deck_path = decks_dir / f"deck-{deck_id}.json"
        payload = {
            "schema": "deck.v1",
            "deckId": deck_id,
            "name": deck.name or "Unknown Deck",
            "cards": {
                key: cards_to_list(deck.piles.get(pile_type, {}))
                for pile_type, key in PILE_KEYS.items()
                if deck.piles.get(pile_type)
            },
            "cardsById": {
                key: {str(g): q for g, q in sorted(deck.piles.get(pile_type, {}).items())}
                for pile_type, key in PILE_KEYS.items()
                if deck.piles.get(pile_type)
            },
            "lastUpdated": deck.last_updated,
            "exportedAt": _iso_now(),
        }
        deck_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    # Write container file
    container_decks: list[dict[str, Any]] = []
    for deck in result.decks:
        deck_id = deck.deck_id or f"deck-{deck.raw_address:#x}"
        cards: dict[str, dict[str, int]] = {}
        for pile_type, key in PILE_KEYS.items():
            pile = deck.piles.get(pile_type, {})
            if pile:
                cards[key] = {str(grp_id): qty for grp_id, qty in sorted(pile.items())}
        container_decks.append({
            "deckId": deck_id,
            "name": deck.name or "Unknown Deck",
            "cards": cards,
            "lastUpdated": deck.last_updated,
        })

    container_path = output_dir / "decks-container.json"
    container_payload = {
        "schema": "decks-container.v1",
        "exportedAt": _iso_now(),
        "decks": container_decks,
        "warnings": result.warnings,
    }
    container_path.write_text(
        json.dumps(container_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    return decks_dir, container_path