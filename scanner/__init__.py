"""MTGA Collection & Deck Scanner — macOS Memory-Scanning via pymem-osx."""

from .deck_scanner import (
    DeckMemoryResult,
    DeckScanResult,
    PILE_COMMAND_ZONE,
    PILE_COMPANIONS,
    PILE_MAIN,
    PILE_SIDEBOARD,
    find_deck_blocks,
    merge_deck_results,
    parse_deck_block,
    parse_pile_list_from_bytes,
    parse_piles_from_bytes,
    scan_decks,
    write_deck_artifacts,
)

__all__ = [
    "DeckMemoryResult",
    "DeckScanResult",
    "PILE_COMMAND_ZONE",
    "PILE_COMPANIONS",
    "PILE_MAIN",
    "PILE_SIDEBOARD",
    "find_deck_blocks",
    "merge_deck_results",
    "parse_deck_block",
    "parse_pile_list_from_bytes",
    "parse_piles_from_bytes",
    "scan_decks",
    "write_deck_artifacts",
]