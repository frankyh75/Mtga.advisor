from __future__ import annotations

from advisor.completion import build_completion_advice
from advisor.deck_import import import_arena_deck


def test_import_arena_deck_resolves_mainboard_and_sideboard() -> None:
    card_db = {
        100: {"name": "Lightning Strike", "rarity": "common", "set": "DMU"},
        200: {"name": "Abrade", "rarity": "uncommon", "set": "VOW"},
    }
    deck = import_arena_deck(
        """
Deck
4 Lightning Strike

Sideboard
2 Abrade
""",
        card_db=card_db,
        deck_format="standard",
        name="Test Deck",
    )

    assert deck["schema"] == "arena-deck.v1"
    assert deck["name"] == "Test Deck"
    assert deck["format"] == "standard"
    assert deck["mainboard"] == [
        {
            "arenaId": 100,
            "collectorNumber": "",
            "count": 4,
            "name": "Lightning Strike",
            "rarity": "common",
            "set": "DMU",
        }
    ]
    assert deck["sideboard"][0]["arenaId"] == 200
    assert deck["diagnostics"]["warnings"] == []


def test_import_arena_deck_reports_unknown_and_ambiguous() -> None:
    """Same-name cards from different sets are resolved to newest reprint.
    Unknown card names and unparseable lines are reported as unresolved."""
    card_db = {
        100: {"name": "Opt", "set": "XLN"},
        101: {"name": "Opt", "set": "STA"},
    }
    deck = import_arena_deck(
        "Deck\n4 Opt\n2 Missing Card\nnot a line",
        card_db=card_db,
        deck_format="historic",
    )

    # Opt resolves to the alphabetically last set (XLN > STA)
    assert len(deck["mainboard"]) == 1
    assert deck["mainboard"][0]["name"] == "Opt"
    assert deck["mainboard"][0]["set"] == "XLN"
    assert deck["mainboard"][0]["count"] == 4
    assert len(deck["diagnostics"]["ambiguous"]) == 0
    assert len(deck["diagnostics"]["unresolved"]) == 2
    assert deck["diagnostics"]["warnings"] == ["unresolved-deck-lines"]


def test_build_completion_advice_groups_missing_cards_and_guards_wildcards() -> None:
    collection = {
        "schema": "collection.v1",
        "cards": {"100": 2, "200": 4},
        "wildcards": {},
        "diagnostics": {
            "completeness": {
                "cards": "complete",
                "wildcards": "unknown",
                "source": "complete",
            }
        },
    }
    deck = {
        "schema": "arena-deck.v1",
        "deckId": "abc",
        "name": "Test Deck",
        "format": "standard",
        "mainboard": [
            {"arenaId": 100, "name": "Lightning Strike", "count": 4},
            {"arenaId": 200, "name": "Abrade", "count": 4},
        ],
        "sideboard": [
            {"arenaId": 300, "name": "Sideboard Rare", "count": 1},
        ],
        "diagnostics": {"warnings": []},
    }
    card_db = {
        100: {"name": "Lightning Strike", "rarity": "common"},
        200: {"name": "Abrade", "rarity": "uncommon"},
        300: {"name": "Sideboard Rare", "rarity": "rare"},
    }

    result = build_completion_advice(collection=collection, deck=deck, card_db=card_db)

    assert result["schema"] == "advisor-result.v1"
    assert result["summary"]["completionScore"] == 70.6
    assert result["summary"]["missingCards"] == 3
    assert result["summary"]["hardCraftAdviceAllowed"] is False
    assert result["warnings"] == ["wildcards-unknown"]
    assert result["missingByRarity"] == {"rare": 1, "common": 2}
    assert result["recommendations"][0]["name"] == "Lightning Strike"
    assert result["recommendations"][0]["craftAdvice"] == "what-if"


def test_import_arena_deck_prefers_collection_id_for_reprints() -> None:
    """Reprints should resolve to the grpId that exists in the collection."""
    card_db = {
        100: {"name": "Opt", "set": "XLN"},
        101: {"name": "Opt", "set": "STA"},
        102: {"name": "Opt", "set": "DMR"},
    }
    # Player owns grpId 101 (STA) but not 100 (XLN) or 102 (DMR).
    # Without collection_ids the fallback picks 100 (XLN, alphabetically last).
    deck_no_col = import_arena_deck(
        "Deck\n4 Opt",
        card_db=card_db,
        deck_format="historic",
    )
    assert deck_no_col["mainboard"][0]["arenaId"] == 100

    # With collection_ids, the print in the collection (101) is chosen.
    deck_with_col = import_arena_deck(
        "Deck\n4 Opt",
        card_db=card_db,
        deck_format="historic",
        collection_ids={101, 200},
    )
    assert deck_with_col["mainboard"][0]["arenaId"] == 101


def test_import_arena_deck_collection_ids_fallback_when_no_match() -> None:
    """If no candidate matches the collection, fall back to newest-set heuristic."""
    card_db = {
        100: {"name": "Opt", "set": "XLN"},
        101: {"name": "Opt", "set": "STA"},
    }
    # Collection has neither 100 nor 101.
    deck = import_arena_deck(
        "Deck\n4 Opt",
        card_db=card_db,
        deck_format="historic",
        collection_ids={999},
    )
    assert deck["mainboard"][0]["arenaId"] == 100  # XLN > STA alphabetically
