"""Tests für advisor/analyze.py — Deck-Analyse gegen Collection + Wildcards."""

from __future__ import annotations

import json
from pathlib import Path

import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from advisor.analyze import analyze_deck, load_deck_cards  # noqa: E402


def _make_collection(cards: dict[str, int]) -> dict:
    """Erstelle ein collection.v1-kompatibles Dict."""
    return {
        "schema": "collection.v1",
        "source": "memory-scan",
        "cards": cards,
        "wildcards": {},
        "diagnostics": {
            "completeness": {"cards": "complete", "wildcards": "unknown"},
        },
    }


def _make_wildcards(c=0, u=0, r=0, m=0) -> dict:
    """Erstelle ein wildcards.v1-kompatibles Dict."""
    return {
        "schema": "wildcards.v1",
        "source": "local-logs",
        "wildcards": {"commons": c, "uncommons": u, "rares": r, "mythics": m},
        "currency": {"gold": 0, "gems": 0},
        "vaultProgress": 0,
    }


def _make_deck(
    deck_id: str = "test-1",
    name: str = "TestDeck",
    fmt: str = "Standard",
    main_deck: list[dict] | None = None,
    sideboard: list[dict] | None = None,
) -> dict:
    """Erstelle ein deck-cards.v1-kompatibles Deck-Dict."""
    if main_deck is None:
        main_deck = [{"cardId": 100, "quantity": 4}, {"cardId": 200, "quantity": 2}]
    if sideboard is None:
        sideboard = [{"cardId": 300, "quantity": 1}]
    return {
        "deckId": deck_id,
        "name": name,
        "format": fmt,
        "mainDeck": main_deck,
        "sideboard": sideboard,
        "commandZone": [],
        "companions": [],
    }


# ---------------------------------------------------------------------------
# analyze_deck
# ---------------------------------------------------------------------------

def test_analyze_deck_all_owned() -> None:
    """Alle Karten vorhanden → 100% completion, keine fehlenden."""
    collection = _make_collection({"100": 4, "200": 2, "300": 1})
    wildcards = _make_wildcards(c=10, u=5, r=3, m=1)
    deck = _make_deck()

    result = analyze_deck(deck=deck, collection=collection, wildcards=wildcards)

    assert result["schema"] == "advisor-analyze.v1"
    assert result["summary"]["completionScore"] == 100.0
    assert result["summary"]["missingCards"] == 0
    assert result["summary"]["missingUnique"] == 0
    assert result["missingCards"] == []
    assert len(result["ownedCards"]) == 3
    assert result["wildcardNeed"]["needed"] == {}
    assert result["craftPriority"] == []


def test_analyze_deck_partial_missing() -> None:
    """Einige Karten fehlen → completion < 100%, missingCards > 0."""
    collection = _make_collection({"100": 2, "200": 2})  # 100: need 4, own 2 → missing 2
    wildcards = _make_wildcards(c=10, u=5, r=3, m=1)
    deck = _make_deck()

    result = analyze_deck(
        deck=deck,
        collection=collection,
        wildcards=wildcards,
        card_db={100: {"name": "Lightning", "rarity": "common"}, 300: {"name": "RareCard", "rarity": "rare"}},
    )

    # 100: missing 2 (common), 300: missing 1 (rare)
    assert result["summary"]["missingCards"] == 3
    assert result["summary"]["completionScore"] < 100.0
    # 2 unique missing cards
    assert result["summary"]["missingUnique"] == 2

    # Wildcard-Bedarf
    needed = result["wildcardNeed"]["needed"]
    assert needed.get("common") == 2  # 100: missing 2 commons
    assert needed.get("rare") == 1  # 300: missing 1 rare

    # Craft-Priorität: Rare vor Common
    craft = result["craftPriority"]
    assert craft[0]["rarity"] == "rare"
    assert craft[1]["rarity"] == "common"


def test_analyze_deck_no_card_db_unknown_rarity() -> None:
    """Ohne card_db ist rarity 'unknown'."""
    collection = _make_collection({})
    wildcards = _make_wildcards()
    deck = _make_deck(main_deck=[{"cardId": 999, "quantity": 1}], sideboard=[])

    result = analyze_deck(deck=deck, collection=collection, wildcards=wildcards)

    assert result["summary"]["missingCards"] == 1
    assert result["missingCards"][0]["rarity"] == "unknown"
    # Warning about unknown rarity
    assert any("Karten-DB nicht geladen" in w for w in result["warnings"])


def test_analyze_deck_wildcard_sufficiency() -> None:
    """Wildcard-Sufficiency wird korrekt berechnet."""
    collection = _make_collection({})  # Nichts owned
    wildcards = _make_wildcards(c=5, u=0, r=2, m=0)
    deck = _make_deck(
        main_deck=[{"cardId": 1, "quantity": 3}],  # 3 commons fehlen
        sideboard=[],
    )

    result = analyze_deck(
        deck=deck,
        collection=collection,
        wildcards=wildcards,
        card_db={1: {"name": "C", "rarity": "common"}},
    )

    suff = result["wildcardNeed"]["sufficiency"]
    assert suff["common"]["needed"] == 3
    assert suff["common"]["owned"] == 5
    assert suff["common"]["sufficient"] is True
    assert suff["common"]["shortfall"] == 0


def test_analyze_deck_wildcard_insufficient() -> None:
    """Wildcard-Bestand nicht ausreichend → Warning."""
    collection = _make_collection({})
    wildcards = _make_wildcards(c=1, u=0, r=0, m=0)
    deck = _make_deck(
        main_deck=[{"cardId": 1, "quantity": 5}],
        sideboard=[],
    )

    result = analyze_deck(
        deck=deck,
        collection=collection,
        wildcards=wildcards,
        card_db={1: {"name": "C", "rarity": "common"}},
    )

    suff = result["wildcardNeed"]["sufficiency"]
    assert suff["common"]["needed"] == 5
    assert suff["common"]["owned"] == 1
    assert suff["common"]["sufficient"] is False
    assert suff["common"]["shortfall"] == 4
    assert any("nicht ausreichend" in w for w in result["warnings"])


def test_analyze_deck_empty_collection_warning() -> None:
    """Leere Collection → Warning."""
    collection = {"schema": "collection.v1", "cards": {}, "wildcards": {}}
    wildcards = _make_wildcards()
    deck = _make_deck()

    result = analyze_deck(deck=deck, collection=collection, wildcards=wildcards)

    assert any("Collection ist leer" in w for w in result["warnings"])


def test_analyze_deck_empty_wildcards_warning() -> None:
    """Fehlende Wildcard-Daten → Warning."""
    collection = _make_collection({"100": 4})
    wildcards = {"wildcards": {}}
    deck = _make_deck(main_deck=[{"cardId": 100, "quantity": 4}])

    result = analyze_deck(deck=deck, collection=collection, wildcards=wildcards)

    assert any("Wildcard-Daten fehlen" in w for w in result["warnings"])


def test_analyze_deck_craft_priority_order() -> None:
    """Craft-Priorität ist nach Mythic → Rare → Uncommon → Common sortiert."""
    collection = _make_collection({})
    wildcards = _make_wildcards()
    deck = _make_deck(
        main_deck=[
            {"cardId": 1, "quantity": 1},  # common
            {"cardId": 2, "quantity": 1},  # rare
            {"cardId": 3, "quantity": 1},  # mythic
            {"cardId": 4, "quantity": 1},  # uncommon
        ],
        sideboard=[],
    )
    card_db = {
        1: {"name": "C", "rarity": "common"},
        2: {"name": "R", "rarity": "rare"},
        3: {"name": "M", "rarity": "mythic"},
        4: {"name": "U", "rarity": "uncommon"},
    }

    result = analyze_deck(deck=deck, collection=collection, wildcards=wildcards, card_db=card_db)

    craft = result["craftPriority"]
    rarities = [g["rarity"] for g in craft]
    assert rarities == ["mythic", "rare", "uncommon", "common"]


def test_analyze_deck_sideboard_cards_included() -> None:
    """Sideboard-Karten werden in die Analyse einbezogen."""
    collection = _make_collection({"100": 4})
    wildcards = _make_wildcards()
    deck = _make_deck(
        main_deck=[{"cardId": 100, "quantity": 4}],
        sideboard=[{"cardId": 200, "quantity": 2}],
    )

    result = analyze_deck(deck=deck, collection=collection, wildcards=wildcards)

    # 100: owned, 200: missing 2
    assert result["summary"]["missingCards"] == 2
    assert result["summary"]["missingUnique"] == 1
    missing_card = result["missingCards"][0]
    assert missing_card["cardId"] == 200
    assert missing_card["zone"] == "sideboard"


# ---------------------------------------------------------------------------
# load_deck_cards
# ---------------------------------------------------------------------------

def test_load_deck_cards_by_id(tmp_path: Path) -> None:
    """load_deck_cards findet ein Deck per deckId."""
    data = {
        "schema": "deck-cards.v1",
        "decks": [
            {"deckId": "abc", "name": "A", "format": "Standard", "mainDeck": [], "sideboard": [], "commandZone": [], "companions": []},
            {"deckId": "def", "name": "D", "format": "Historic", "mainDeck": [], "sideboard": [], "commandZone": [], "companions": []},
        ],
    }
    path = tmp_path / "deck-cards.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    deck = load_deck_cards(path, deck_id="def")
    assert deck["deckId"] == "def"
    assert deck["name"] == "D"


def test_load_deck_cards_by_name(tmp_path: Path) -> None:
    """load_deck_cards findet ein Deck per name."""
    data = {
        "schema": "deck-cards.v1",
        "decks": [
            {"deckId": "abc", "name": "Victimize", "format": "Historic", "mainDeck": [], "sideboard": [], "commandZone": [], "companions": []},
        ],
    }
    path = tmp_path / "deck-cards.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    deck = load_deck_cards(path, name="victimize")
    assert deck["deckId"] == "abc"


def test_load_deck_cards_partial_id_match(tmp_path: Path) -> None:
    """load_deck_cards unterstützt partial Match der deckId."""
    data = {
        "schema": "deck-cards.v1",
        "decks": [
            {"deckId": "ecc24960-f911-4da6-b3aa-2fac8c281458", "name": "Victimize", "format": "Historic", "mainDeck": [], "sideboard": [], "commandZone": [], "companions": []},
        ],
    }
    path = tmp_path / "deck-cards.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    deck = load_deck_cards(path, deck_id="ecc24960")
    assert deck["name"] == "Victimize"


def test_load_deck_cards_not_found(tmp_path: Path) -> None:
    """load_deck_cards raised ValueError bei nicht gefundenem Deck."""
    data = {"schema": "deck-cards.v1", "decks": []}
    path = tmp_path / "deck-cards.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(ValueError):
        load_deck_cards(path, deck_id="nonexistent")


# ---------------------------------------------------------------------------
# Print-übergreifendes owned-Matching (Reprints / alle Prints)
# ---------------------------------------------------------------------------

def test_analyze_deck_owned_across_prints() -> None:
    """owned wird über alle grpIds mit gleichem Kartennamen summiert (Reprints).

    Deck will Duress grpId 83792 (1x). Collection hat Duress unter drei
    grpIds (77508: 4, 78439: 4, 83792: 1) = 9 insgesamt. Die Analyse
    darf nicht nur 83792 zählen.
    """
    collection = _make_collection({"77508": 4, "78439": 4, "83792": 1})
    wildcards = _make_wildcards()
    deck = _make_deck(main_deck=[{"cardId": 83792, "quantity": 4}], sideboard=[])
    card_db = {
        77508: {"name": "Duress", "rarity": "common"},
        78439: {"name": "Duress", "rarity": "common"},
        83792: {"name": "Duress", "rarity": "common"},
    }

    result = analyze_deck(deck=deck, collection=collection, wildcards=wildcards, card_db=card_db)

    assert result["summary"]["completionScore"] == 100.0
    assert result["summary"]["missingCards"] == 0
    assert result["summary"]["missingUnique"] == 0
    # Die Deck-Karte soll 9 owned anzeigen (Summe aller Prints).
    owned_entry = result["ownedCards"][0]
    assert owned_entry["owned"] == 9


def test_analyze_deck_basic_land_any_grp_id() -> None:
    """Grundländer sind immer owned, auch wenn die grpId in der Collection
    eine andere ist als im Deck (MTGA Grundländer sind unbegrenzt)."""
    collection = _make_collection({"58445": 1})  # Swamp grpId 58445, nur 1
    wildcards = _make_wildcards()
    deck = _make_deck(main_deck=[{"cardId": 99999, "quantity": 23}], sideboard=[])
    # 99999 ist eine Swamp-grpId, die nicht in der Collection liegt.
    card_db = {99999: {"name": "Swamp", "rarity": "common"}, 58445: {"name": "Swamp", "rarity": "common"}}

    result = analyze_deck(deck=deck, collection=collection, wildcards=wildcards, card_db=card_db)

    assert result["summary"]["completionScore"] == 100.0
    assert result["summary"]["missingCards"] == 0
    # owned = needed (23), da Grundländer unbegrenzt.
    assert result["ownedCards"][0]["owned"] == 23


def test_analyze_deck_no_card_db_falls_back_to_exact_match() -> None:
    """Ohne Karten-DB bleibt das alte Verhalten: exakter grpId-Match."""
    collection = _make_collection({"77508": 4, "83792": 1})
    wildcards = _make_wildcards()
    deck = _make_deck(main_deck=[{"cardId": 83792, "quantity": 4}], sideboard=[])

    result = analyze_deck(deck=deck, collection=collection, wildcards=wildcards)

    # Ohne DB nur 83792 (1) → 3 fehlen.
    assert result["summary"]["missingCards"] == 3
    assert result["summary"]["completionScore"] < 100.0