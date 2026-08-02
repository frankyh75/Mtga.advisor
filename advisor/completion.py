from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ADVISOR_SCHEMA = "advisor-result.v1"
RARITY_ORDER = ("mythic", "rare", "uncommon", "common", "unknown")


def build_completion_advice(
    *,
    collection: dict[str, Any],
    deck: dict[str, Any],
    card_db: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Build deterministic completion advice for one deck.

    Supports two deck formats:
    - arena-deck.v1: deck["mainboard"] = [{"arenaId": 123, "count": 4, ...}, ...]
    - deck.v1 / decks-container.v1: deck["cards"]["mainboard"] = [{"cardId": 123, "count": 4, ...}, ...]
      or deck["cards"]["mainboard"] = {"123": 4, ...}
    """
    collection_cards = {int(card_id): int(count) for card_id, count in collection.get("cards", {}).items()}
    completeness = collection.get("diagnostics", {}).get("completeness", {})
    wildcards_complete = completeness.get("wildcards") == "complete"
    warnings: list[str] = []
    if not wildcards_complete:
        warnings.append("wildcards-unknown")

    mainboard, sideboard = _normalize_deck_cards(deck)
    requirements = _aggregate_requirements(mainboard, sideboard)
    recommendations: list[dict[str, Any]] = []
    owned_weight = 0
    required_weight = 0
    missing_cards = 0

    for arena_id, requirement in sorted(requirements.items()):
        required = requirement["required"]
        owned = min(collection_cards.get(arena_id, 0), required)
        weight = 2 if requirement["mainboard"] else 1
        owned_weight += owned * weight
        required_weight += required * weight
        needed = max(0, required - owned)
        if needed <= 0:
            continue
        missing_cards += needed
        meta = card_db.get(arena_id, {})
        recommendations.append(
            {
                "type": "missing-card",
                "arenaId": arena_id,
                "name": requirement["name"],
                "needed": needed,
                "owned": collection_cards.get(arena_id, 0),
                "required": required,
                "rarity": str(meta.get("rarity") or requirement.get("rarity") or "unknown").lower(),
                "zones": requirement["zones"],
                "reasons": _reasons(requirement),
                "craftAdvice": "what-if" if not wildcards_complete else "allowed",
            }
        )

    recommendations.sort(key=_recommendation_sort_key)
    completion_score = 100.0 if required_weight == 0 else round((owned_weight / required_weight) * 100, 1)
    hard_craft_allowed = wildcards_complete

    return {
        "schema": ADVISOR_SCHEMA,
        "generatedAt": _iso_now(),
        "deck": {
            "deckId": deck.get("deckId"),
            "name": deck.get("name", "Imported Deck"),
            "format": deck.get("format", "unknown"),
        },
        "summary": {
            "completionScore": completion_score,
            "confidence": _confidence(completeness, deck),
            "missingCards": missing_cards,
            "missingUniqueCards": len(recommendations),
            "hardCraftAdviceAllowed": hard_craft_allowed,
        },
        "recommendations": recommendations,
        "missingByRarity": _missing_by_rarity(recommendations),
        "warnings": warnings + list(deck.get("diagnostics", {}).get("warnings", [])),
        "evidence": ["collection.v1", "arena-deck.v1", "local-mtga-card-db"],
    }


def write_advisor_result(result: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "advisor-result.json"
    path.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_deck_cards(deck: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize deck card data from different formats.

    Returns (mainboard, sideboard) as lists of dicts with keys:
    arenaId, count, name, rarity.
    """
    # Format 1: arena-deck.v1 (deck["mainboard"] = [...])
    if "mainboard" in deck or "sideboard" in deck:
        mainboard = list(deck.get("mainboard", []))
        sideboard = list(deck.get("sideboard", []))
        return mainboard, sideboard

    # Format 2: deck.v1 / decks-container.v1 (deck["cards"]["mainboard"] = ...)
    cards = deck.get("cards", {})
    if not cards:
        return [], []

    mainboard: list[dict[str, Any]] = []
    sideboard: list[dict[str, Any]] = []

    for pile_key, target in (("mainboard", mainboard), ("sideboard", sideboard)):
        pile = cards.get(pile_key)
        if not pile:
            continue
        if isinstance(pile, list):
            # Named card list format
            for card in pile:
                arena_id = int(card.get("cardId") or card.get("arenaId") or 0)
                if arena_id <= 0:
                    continue
                target.append({
                    "arenaId": arena_id,
                    "count": int(card.get("count", 1)),
                    "name": card.get("name", f"#{arena_id}"),
                    "rarity": card.get("rarity", "unknown"),
                })
        elif isinstance(pile, dict):
            # grpId→qty format (no names, no rarity)
            for grp_id_str, qty in pile.items():
                arena_id = int(grp_id_str)
                target.append({
                    "arenaId": arena_id,
                    "count": int(qty),
                    "name": f"#{arena_id}",
                    "rarity": "unknown",
                })

    return mainboard, sideboard


def _aggregate_requirements(
    mainboard: list[dict[str, Any]],
    sideboard: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    requirements: dict[int, dict[str, Any]] = {}
    for zone, entries in (("mainboard", mainboard), ("sideboard", sideboard)):
        for entry in entries:
            arena_id = int(entry["arenaId"])
            target = requirements.setdefault(
                arena_id,
                {
                    "name": entry.get("name", f"#{arena_id}"),
                    "rarity": entry.get("rarity", "unknown"),
                    "required": 0,
                    "mainboard": False,
                    "sideboard": False,
                    "zones": [],
                },
            )
            target["required"] += int(entry.get("count", 0))
            target[zone] = True
            if zone not in target["zones"]:
                target["zones"].append(zone)
    return requirements


def _reasons(requirement: dict[str, Any]) -> list[str]:
    reasons = ["missing-copies"]
    if requirement["mainboard"]:
        reasons.append("mainboard")
    if requirement["sideboard"]:
        reasons.append("sideboard")
    return reasons


def _recommendation_sort_key(rec: dict[str, Any]) -> tuple[int, int, str]:
    try:
        rarity_rank = RARITY_ORDER.index(rec["rarity"])
    except ValueError:
        rarity_rank = RARITY_ORDER.index("unknown")
    zone_rank = 0 if "mainboard" in rec["zones"] else 1
    return (zone_rank, rarity_rank, rec["name"])


def _missing_by_rarity(recommendations: list[dict[str, Any]]) -> dict[str, int]:
    grouped = {rarity: 0 for rarity in RARITY_ORDER}
    for rec in recommendations:
        rarity = rec.get("rarity", "unknown")
        if rarity not in grouped:
            rarity = "unknown"
        grouped[rarity] += int(rec.get("needed", 0))
    return {rarity: count for rarity, count in grouped.items() if count}


def _confidence(completeness: dict[str, str], deck: dict[str, Any]) -> str:
    if deck.get("diagnostics", {}).get("warnings"):
        return "medium"
    if completeness.get("cards") == "complete":
        return "high"
    if completeness.get("cards") == "partial":
        return "medium"
    return "low"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
