"""StartHook-Parser für MTGA-Logs.

Der StartHook-Event wird beim Login von MTGA ausgelöst und enthält:
- DeckSummaries[] — alle Decks des Spielers
- InventoryInfo — Gems, Gold, Wildcards, Vault
- Formats — verfügbare Formate
- CardMetadataInfo — NonCraftable/NonCollectible Card-Listen

Referenz: mtgatool-desktop src/background/onLabel/InStartHook.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeckSummary:
    """Ein einzelnes Deck aus dem StartHook-Event."""

    name: str
    deck_id: str | None
    deck_tile_id: int | None
    description: str | None
    attributes: dict[str, str]
    format_legalities: dict[str, bool]
    is_companion_valid: bool | None
    mana: str | None


@dataclass(frozen=True)
class StartHookData:
    """Geparste Daten aus einem StartHook-Event."""

    deck_summaries: list[DeckSummary]
    inventory: dict[str, Any] | None
    formats: list[dict[str, Any]] | None
    card_metadata: dict[str, Any] | None
    deck_limit: int | None


def parse_start_hook(data: dict[str, Any]) -> StartHookData | None:
    """Extrahiere DeckSummaries und andere Daten aus einem StartHook-Event.

    Args:
        data: Das JSON-Payload des StartHook-Events.

    Returns:
        StartHookData mit geparsten Decks, oder None wenn keine Decks enthalten.
    """
    raw_decks = data.get("DeckSummaries", [])
    if not isinstance(raw_decks, list) or not raw_decks:
        return None

    decks: list[DeckSummary] = []
    for raw in raw_decks:
        if not isinstance(raw, dict):
            continue
        name = raw.get("Name", "")
        if not name:
            continue

        deck_id = raw.get("DeckId")
        if deck_id is not None:
            deck_id = str(deck_id)

        attributes_raw = raw.get("Attributes", {})
        if isinstance(attributes_raw, list):
            attributes = {}
            for attr in attributes_raw:
                if isinstance(attr, dict) and "key" in attr and "value" in attr:
                    attributes[str(attr["key"])] = str(attr["value"])
        elif isinstance(attributes_raw, dict):
            attributes = {str(k): str(v) for k, v in attributes_raw.items()}
        else:
            attributes = {}

        format_legalities_raw = raw.get("FormatLegalities", {})
        if isinstance(format_legalities_raw, dict):
            format_legalities = {
                str(k): bool(v) for k, v in format_legalities_raw.items()
            }
        else:
            format_legalities = {}

        decks.append(
            DeckSummary(
                name=name,
                deck_id=deck_id,
                deck_tile_id=raw.get("DeckTileId"),
                description=raw.get("Description"),
                attributes=attributes,
                format_legalities=format_legalities,
                is_companion_valid=raw.get("IsCompanionValid"),
                mana=raw.get("Mana"),
            )
        )

    if not decks:
        return None

    inventory = data.get("InventoryInfo")
    if isinstance(inventory, dict):
        # Entferne unnötige Felder (wie in InStartHook.ts)
        inventory = {
            k: v
            for k, v in inventory.items()
            if k not in ("SeqId", "Changes", "CustomTokens", "Vouchers", "Cosmetics")
        }

    formats = data.get("Formats")
    if isinstance(formats, list):
        formats = [f for f in formats if isinstance(f, dict)]

    card_metadata = data.get("CardMetadataInfo")
    if isinstance(card_metadata, dict):
        card_metadata = {
            k: v
            for k, v in card_metadata.items()
            if k
            in (
                "NonCraftableCardList",
                "NonCollectibleCardList",
                "UnreleasedSets",
            )
        }

    return StartHookData(
        deck_summaries=decks,
        inventory=inventory,
        formats=formats,
        card_metadata=card_metadata,
        deck_limit=data.get("DeckLimit"),
    )
