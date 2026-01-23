from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from carddb.lookup import CardLookup


@dataclass(frozen=True)
class DeckAnalysisResult:
    payload: dict
    coverage: float


def build_deck_analysis(deck: dict, lookup: CardLookup) -> DeckAnalysisResult:
    deck_id = deck.get("id", "")
    name = deck.get("name")
    fmt = deck.get("format")
    source = deck.get("source")
    analyzed_at = _iso_now()

    main_entries = _collect_entries(deck.get("mainDeck", []) or [])
    side_entries = _collect_entries(deck.get("sideboard", []) or [])
    command_entries = _collect_entries(deck.get("commandZone", []) or [])
    companion_entries = _collect_entries(deck.get("companions", []) or [])
    entries = _merge_entries([main_entries, side_entries, command_entries, companion_entries])
    unique_ids = {entry["cardId"] for entry in entries}
    main_ids = {entry["cardId"] for entry in main_entries}
    total_unique = len(unique_ids)
    mapped_ids: set[int] = set()
    unknown_cards: list[int] = []
    cards: list[dict] = []
    analysis_cards: list[dict] = []

    for entry in entries:
        card_id = entry["cardId"]
        quantity = entry["quantity"]
        card = lookup.lookup_arena_id(card_id)
        if card is None:
            if card_id not in unknown_cards:
                unknown_cards.append(card_id)
            continue
        mapped_ids.add(card_id)
        entry_payload = _build_card_payload(card_id, quantity, card)
        cards.append(entry_payload)
        if card_id in main_ids:
            analysis_cards.append(entry_payload)

    coverage = len(mapped_ids) / total_unique if total_unique else 0.0
    stats = _compute_stats(analysis_cards)
    synergies = _build_synergies(stats)
    recommendations = _build_recommendations(stats)
    payload = {
        "schema": "deck-analysis.v1",
        "deckId": deck_id,
        "name": name,
        "format": fmt,
        "source": source,
        "analyzedAt": analyzed_at,
        "mappingCoverage": round(coverage, 4),
        "unknownCards": unknown_cards,
        "cards": cards,
        "synergies": synergies,
        "recommendations": recommendations,
        "diagnostics": {"coverageWarning": coverage < 0.9},
    }
    return DeckAnalysisResult(payload=payload, coverage=coverage)


def _collect_entries(entries: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        card_id = entry.get("cardId")
        quantity = entry.get("quantity")
        if isinstance(card_id, int) and isinstance(quantity, int):
            normalized.append({"cardId": card_id, "quantity": quantity})
    return normalized


def _merge_entries(groups: list[list[dict]]) -> list[dict]:
    entries: list[dict] = []
    totals: dict[int, int] = {}
    for group in groups:
        for entry in group:
            card_id = entry["cardId"]
            totals[card_id] = totals.get(card_id, 0) + entry["quantity"]
    for card_id, quantity in totals.items():
        entries.append({"cardId": card_id, "quantity": quantity})
    return entries


def _extract_types(type_line: str | None) -> list[str]:
    if not type_line:
        return []
    primary = type_line.split("—", 1)[0].strip()
    if not primary:
        return []
    return [part for part in primary.split() if part]


def _build_card_payload(card_id: int, quantity: int, card: dict) -> dict:
    return {
        "cardId": card_id,
        "quantity": quantity,
        "oracleId": card.get("oracleId"),
        "scryfallId": card.get("scryfallId"),
        "name": card.get("name"),
        "manaCost": card.get("manaCost"),
        "typeLine": card.get("typeLine"),
        "types": _extract_types(card.get("typeLine")),
        "oracleText": card.get("oracleText"),
        "keywords": card.get("keywords", []),
        "colorIdentity": card.get("colorIdentity", []),
        "legalities": card.get("legalities", {}),
    }


def _compute_stats(cards: list[dict]) -> dict:
    stats = {
        "total_cards": 0,
        "lands": 0,
        "creatures": 0,
        "nonlands": 0,
        "avg_cmc": 0.0,
        "curve": {},
        "removal": 0,
        "counterspell": 0,
        "card_draw": 0,
        "ramp": 0,
        "tribal_payoff": 0,
        "artifact_payoff": 0,
        "enchantment_payoff": 0,
        "token_maker": 0,
        "lifegain_payoff": 0,
        "graveyard_synergy": 0,
        "sweeper": 0,
        "interaction": 0,
    }
    cmc_total = 0.0
    for card in cards:
        quantity = card.get("quantity", 0)
        if not isinstance(quantity, int) or quantity <= 0:
            continue
        stats["total_cards"] += quantity
        type_line = card.get("typeLine") or ""
        oracle_text = (card.get("oracleText") or "").lower()
        types = _extract_types(type_line)
        is_land = "Land" in types
        is_creature = "Creature" in types
        if is_land:
            stats["lands"] += quantity
        else:
            stats["nonlands"] += quantity
        if is_creature:
            stats["creatures"] += quantity
        cmc = _mana_cost_to_cmc(card.get("manaCost"))
        if cmc is not None:
            cmc_total += cmc * quantity
            bucket = str(int(cmc))
            stats["curve"][bucket] = stats["curve"].get(bucket, 0) + quantity
        tags = _tag_card(oracle_text)
        for tag in tags:
            if tag in stats:
                stats[tag] += quantity
        if "removal" in tags or "counterspell" in tags or "sweeper" in tags:
            stats["interaction"] += quantity
    if stats["nonlands"] > 0:
        stats["avg_cmc"] = round(cmc_total / stats["nonlands"], 2)
    return stats


def _tag_card(oracle_text: str) -> set[str]:
    tags: set[str] = set()
    if "destroy target" in oracle_text or "exile target" in oracle_text:
        tags.add("removal")
    if "deals" in oracle_text and "damage" in oracle_text:
        tags.add("removal")
        tags.add("burn")
    if "counter target spell" in oracle_text:
        tags.add("counterspell")
    if "draw a card" in oracle_text or "draw two cards" in oracle_text or "draw x cards" in oracle_text:
        tags.add("card_draw")
    if "search your library for a land" in oracle_text or "add {" in oracle_text:
        tags.add("ramp")
    if "create a" in oracle_text and "token" in oracle_text:
        tags.add("token_maker")
    if "you control" in oracle_text and "artifact" in oracle_text:
        tags.add("artifact_payoff")
    if "you control" in oracle_text and "enchantment" in oracle_text:
        tags.add("enchantment_payoff")
    if "you control" in oracle_text and "creature" in oracle_text:
        tags.add("tribal_payoff")
    if "whenever you gain life" in oracle_text:
        tags.add("lifegain_payoff")
    if "from your graveyard" in oracle_text or "mill" in oracle_text:
        tags.add("graveyard_synergy")
    if "destroy all creatures" in oracle_text:
        tags.add("sweeper")
    return tags


def _mana_cost_to_cmc(mana_cost: str | None) -> float | None:
    if not mana_cost:
        return None
    total = 0.0
    for chunk in mana_cost.replace("{", "").split("}"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if chunk.isdigit():
            total += float(chunk)
        elif chunk in ("X", "Y", "Z"):
            total += 0.0
        else:
            total += 1.0
    return total


def _build_synergies(stats: dict) -> list[dict]:
    synergies: list[dict] = []
    if stats["tribal_payoff"] >= 4:
        synergies.append(
            {
                "id": "tribal-payoff.v1",
                "score": _score(stats["tribal_payoff"], stats["total_cards"]),
                "reason": "Tribal payoffs appear frequently; the deck likely benefits from a focused creature type.",
            }
        )
    if stats["artifact_payoff"] >= 4:
        synergies.append(
            {
                "id": "artifact-payoff.v1",
                "score": _score(stats["artifact_payoff"], stats["total_cards"]),
                "reason": "Multiple artifact payoffs suggest an artifact-centered game plan.",
            }
        )
    if stats["enchantment_payoff"] >= 4:
        synergies.append(
            {
                "id": "enchantment-payoff.v1",
                "score": _score(stats["enchantment_payoff"], stats["total_cards"]),
                "reason": "Enchantment payoffs indicate a potential enchantment synergy core.",
            }
        )
    if stats["token_maker"] >= 6:
        synergies.append(
            {
                "id": "tokens.v1",
                "score": _score(stats["token_maker"], stats["total_cards"]),
                "reason": "Token makers are common; token synergies may be strong.",
            }
        )
    if stats["lifegain_payoff"] >= 3:
        synergies.append(
            {
                "id": "lifegain.v1",
                "score": _score(stats["lifegain_payoff"], stats["total_cards"]),
                "reason": "Lifegain payoffs appear in the list; lifegain synergies may be relevant.",
            }
        )
    if stats["graveyard_synergy"] >= 4:
        synergies.append(
            {
                "id": "graveyard.v1",
                "score": _score(stats["graveyard_synergy"], stats["total_cards"]),
                "reason": "Multiple graveyard signals detected; consider graveyard-centric lines.",
            }
        )
    if stats["sweeper"] >= 2:
        synergies.append(
            {
                "id": "sweeper.v1",
                "score": _score(stats["sweeper"], stats["total_cards"]),
                "reason": "Board wipes present; deck likely plays a control posture.",
            }
        )
    return synergies


def _build_recommendations(stats: dict) -> list[dict]:
    recs: list[dict] = []
    total = stats["total_cards"] or 1
    lands = stats["lands"]
    land_ratio = lands / total
    if land_ratio < 0.33:
        recs.append(
            {
                "id": "mana-base.v1",
                "type": "add",
                "reason": "Low land count for the deck size; consider adding 1-3 lands.",
                "confidence": "medium",
            }
        )
    if land_ratio > 0.45:
        recs.append(
            {
                "id": "mana-base.v1",
                "type": "cut",
                "reason": "High land count; consider trimming lands for more spells.",
                "confidence": "medium",
            }
        )
    if stats["avg_cmc"] > 3.8:
        recs.append(
            {
                "type": "add",
                "reason": "Curve looks top-heavy; add more low-cost cards to smooth early turns.",
                "confidence": "medium",
                "id": "curve-top-heavy.v1",
            }
        )
    low_drops = stats["curve"].get("1", 0) + stats["curve"].get("2", 0)
    if low_drops / total < 0.25:
        recs.append(
            {
                "type": "add",
                "reason": "Low density of 1-2 drops; consider adding early plays.",
                "confidence": "low",
                "id": "curve-low-drops.v1",
            }
        )
    interaction = stats["interaction"]
    creatures = stats["creatures"] or 1
    if interaction / creatures < 0.3:
        recs.append(
            {
                "id": "interaction-low.v1",
                "type": "add",
                "reason": "Interaction is low relative to threats; add removal or counterspells.",
                "confidence": "medium",
            }
        )
    if stats["removal"] / total < 0.08:
        recs.append(
            {
                "id": "removal-density.v1",
                "type": "add",
                "reason": "Removal density is low; add more targeted removal.",
                "confidence": "medium",
            }
        )
    if stats["card_draw"] / total < 0.06:
        recs.append(
            {
                "type": "add",
                "reason": "Card draw density is low; add draw effects for consistency.",
                "confidence": "low",
                "id": "card-draw-low.v1",
            }
        )
    if stats["ramp"] / total < 0.04:
        recs.append(
            {
                "id": "ramp-low.v1",
                "type": "add",
                "reason": "Ramp is scarce; consider adding mana acceleration.",
                "confidence": "low",
            }
        )
    nonlands = stats["nonlands"] or 1
    creature_ratio = creatures / nonlands
    if creature_ratio < 0.35:
        recs.append(
            {
                "id": "creature-spell-balance.v1",
                "type": "add",
                "reason": "Low creature count; add threats to close games.",
                "confidence": "low",
            }
        )
    if creature_ratio > 0.75:
        recs.append(
            {
                "id": "creature-spell-balance.v1",
                "type": "add",
                "reason": "Very high creature density; add more spells or interaction.",
                "confidence": "low",
            }
        )
    return recs


def _score(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(min(1.0, count / total), 2)


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
