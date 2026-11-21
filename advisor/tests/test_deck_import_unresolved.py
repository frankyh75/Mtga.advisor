"""Tests: Deck-Import verliert keine Karten ohne Arena-ID (TRK-Fall, T11).

Regression zu T11: Ein 60-Karten-Deck mit einer Zeile, deren Karte nicht in
der Karten-DB ist (Scryfall liefert für TRK keine ``arena_id``), muss als
60-Karten-Deck bewertet werden — die Karte wird über Set+CollectorNumber
aufgenommen (``reason: "unresolved-arena-id"``, ``arenaId: null``) und zählt
in ``totalCards`` mit, während ``completionScore`` nur auf der auflösbaren
Basis gerechnet und über ``scoreBasis`` ausgewiesen wird.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[2]))

from advisor.analyze import analyze_deck, arena_deck_to_deck_cards  # noqa: E402
from advisor.deck_import import _parse_deck_line, import_arena_deck  # noqa: E402


TRK_LINE = "4 U.S.S. Discovery, at Black Alert (TRK) 221"
TRK_NAME = "U.S.S. Discovery, at Black Alert"

# 14 Zeilen à 4 Karten = 56 auflösbare Karten; zusammen mit der TRK-Zeile
# ergibt das exakt das 60-Karten-Deck aus dem Live-Fall.
RESOLVED_NAMES = [f"Resolved Card {i}" for i in range(1, 15)]


def _card_db() -> dict[int, dict]:
    return {
        1000 + i: {
            "name": name,
            "set": "DMU",
            "collector_number": str(i),
            "rarity": "common",
        }
        for i, name in enumerate(RESOLVED_NAMES, start=1)
    }


def _deck_text() -> str:
    lines = ["Deck"]
    for i, name in enumerate(RESOLVED_NAMES, start=1):
        lines.append(f"4 {name} (DMU) {i}")
    lines.append(TRK_LINE)
    return "\n".join(lines)


def _collection() -> dict:
    return {
        "schema": "collection.v1",
        "source": "memory-scan",
        "cards": {str(1000 + i): 4 for i in range(1, 15)},
        "wildcards": {},
        "diagnostics": {"completeness": {"cards": "complete", "wildcards": "complete"}},
    }


def _wildcards() -> dict:
    return {
        "schema": "wildcards.v1",
        "source": "local-logs",
        "wildcards": {"commons": 10, "uncommons": 5, "rares": 3, "mythics": 1},
    }


def _analyze_60er_deck(collection: dict | None = None):
    imported = import_arena_deck(_deck_text(), card_db=_card_db(), deck_format="standard")
    deck = arena_deck_to_deck_cards(imported)
    return analyze_deck(
        deck=deck,
        collection=collection or _collection(),
        wildcards=_wildcards(),
        card_db=_card_db(),
    )


# ---------------------------------------------------------------------------
# _parse_deck_line: Set + CollectorNumber werden geparst (F1)
# ---------------------------------------------------------------------------


def test_parse_deck_line_extracts_set_and_collector_number() -> None:
    assert _parse_deck_line(TRK_LINE) == (4, TRK_NAME, "TRK", "221")
    assert _parse_deck_line("4 Opt") == (4, "Opt", None, None)
    assert _parse_deck_line("2 Abrade (FDN) 146") == (2, "Abrade", "FDN", "146")
    assert _parse_deck_line("not a card line") is None


def test_parse_deck_line_set_code_normalized_uppercase() -> None:
    assert _parse_deck_line("1 Some Card (trk) 5") == (1, "Some Card", "TRK", "5")


# ---------------------------------------------------------------------------
# Import: Karte ohne Arena-ID wird aufgenommen, nicht verworfen (F1)
# ---------------------------------------------------------------------------


def test_trk_line_lands_in_mainboard_without_arena_id() -> None:
    deck = import_arena_deck(_deck_text(), card_db=_card_db(), deck_format="standard")

    trk_entries = [e for e in deck["mainboard"] if e.get("arenaId") is None]
    assert len(trk_entries) == 1
    entry = trk_entries[0]
    assert entry["name"] == TRK_NAME
    assert entry["set"] == "TRK"
    assert entry["collectorNumber"] == "221"
    assert entry["count"] == 4
    assert entry["reason"] == "unresolved-arena-id"

    # Die Zeile ist KEIN unbekannter Kartenname mehr — sie ist im Board.
    assert deck["diagnostics"]["unresolved"] == []
    assert deck["diagnostics"]["unresolvedCards"] == 4
    assert deck["diagnostics"]["unknownSets"] == ["TRK"]
    assert "arena-id-unbekannt" in deck["diagnostics"]["warnings"]


def test_unknown_name_with_set_and_cn_is_not_unparseable() -> None:
    """Regression: Unbekannter Name + Set + CN darf nicht als unparseable oder
    unknown-card-name enden — die Karte wird ohne Arena-ID aufgenommen."""
    deck = import_arena_deck(
        "Deck\n4 Nobody Knows This Card (XYZ) 99\n",
        card_db={},
        deck_format="standard",
    )

    assert deck["diagnostics"]["unresolved"] == []
    assert len(deck["mainboard"]) == 1
    assert deck["mainboard"][0]["arenaId"] is None
    assert deck["mainboard"][0]["reason"] == "unresolved-arena-id"
    assert deck["diagnostics"]["warnings"] == ["arena-id-unbekannt"]


def test_unknown_name_without_set_and_cn_stays_unresolved() -> None:
    """Ohne Set+CN ist keine Identifikation möglich → wie bisher unknown-card-name."""
    deck = import_arena_deck("Deck\n2 Missing Card\n", card_db={}, deck_format="standard")

    assert len(deck["mainboard"]) == 0
    unresolved = deck["diagnostics"]["unresolved"]
    assert len(unresolved) == 1
    assert unresolved[0]["reason"] == "unknown-card-name"
    assert unresolved[0]["count"] == 2
    assert deck["diagnostics"]["unresolvedCards"] == 2
    assert deck["diagnostics"]["warnings"] == ["unresolved-deck-lines"]


def test_sideboard_unresolved_arena_id_tracked_separately() -> None:
    text = (
        "Deck\n"
        "4 Resolved Card 1 (DMU) 1\n"
        "\n"
        "Sideboard\n"
        f"{TRK_LINE}\n"
    )
    imported = import_arena_deck(text, card_db=_card_db(), deck_format="standard")

    assert len(imported["sideboard"]) == 1
    assert imported["sideboard"][0]["arenaId"] is None

    deck = arena_deck_to_deck_cards(imported)
    assert deck["unresolvedCards"][0]["zone"] == "sideboard"

    result = analyze_deck(
        deck=deck, collection=_collection(), wildcards=_wildcards(), card_db=_card_db()
    )
    assert result["summary"]["totalCards"] == 8
    assert result["summary"]["unresolvedCards"] == 4
    assert result["unresolvedCards"][0]["zone"] == "sideboard"


# ---------------------------------------------------------------------------
# Kette Import → Analyze: 60-Karten-Deck wird als 60-Karten-Deck bewertet (F3)
# ---------------------------------------------------------------------------


def test_60_card_deck_with_trk_line_analyzes_on_full_total() -> None:
    result = _analyze_60er_deck()
    summary = result["summary"]

    assert summary["totalCards"] == 60
    assert summary["ownedCards"] == 56
    assert summary["unresolvedCards"] == 4
    assert summary["missingCards"] == 0
    assert summary["unknownSets"] == ["TRK"]
    assert summary["scoreBasis"] == {"owned": 56, "resolved": 56, "total": 60}
    # Score nur auf auflösbare Basis (56/56) — nicht auf 60 gedrückt.
    assert summary["completionScore"] == 100.0
    assert "arena-id-unbekannt" in result["warnings"]
    assert result["unresolvedCards"][0]["name"] == TRK_NAME
    assert result["unresolvedCards"][0]["set"] == "TRK"
    assert result["unresolvedCards"][0]["zone"] == "mainDeck"


def test_parsed_card_sum_equals_total_cards_end_to_end() -> None:
    """Keine Karte darf stillschweigend verschwinden: Die Summe der Counts
    aller geparsten Zeilen muss am Ende in totalCards wieder auftauchen."""
    deck_text = _deck_text()
    parsed_total = 0
    for line in deck_text.splitlines():
        parsed = _parse_deck_line(line.strip())
        if parsed is not None:
            parsed_total += parsed[0]

    assert parsed_total == 60

    imported = import_arena_deck(deck_text, card_db=_card_db(), deck_format="standard")
    deck = arena_deck_to_deck_cards(imported)
    result = analyze_deck(
        deck=deck, collection=_collection(), wildcards=_wildcards(), card_db=_card_db()
    )

    assert result["summary"]["totalCards"] == parsed_total
    assert result["summary"]["scoreBasis"]["total"] == parsed_total


def test_completion_score_uses_resolved_basis_only() -> None:
    text = f"Deck\n4 Resolved Card 1 (DMU) 1\n{TRK_LINE}\n"
    imported = import_arena_deck(text, card_db=_card_db(), deck_format="standard")
    deck = arena_deck_to_deck_cards(imported)
    collection = _collection()
    collection["cards"]["1001"] = 2  # nur 2 von 4 Exemplaren owned

    result = analyze_deck(
        deck=deck, collection=collection, wildcards=_wildcards(), card_db=_card_db()
    )

    # Score = 2/4 auf der auflösbaren Basis — die 4 TRK-Karten drücken ihn nicht.
    assert result["summary"]["scoreBasis"] == {"owned": 2, "resolved": 4, "total": 8}
    assert result["summary"]["completionScore"] == 50.0


def test_only_unresolved_deck_scores_zero() -> None:
    text = f"Deck\n{TRK_LINE}\n"
    imported = import_arena_deck(text, card_db={}, deck_format="standard")
    deck = arena_deck_to_deck_cards(imported)

    result = analyze_deck(
        deck=deck, collection=_collection(), wildcards=_wildcards(), card_db=_card_db()
    )

    assert result["summary"]["totalCards"] == 4
    assert result["summary"]["unresolvedCards"] == 4
    assert result["summary"]["scoreBasis"] == {"owned": 0, "resolved": 0, "total": 4}
    assert result["summary"]["completionScore"] == 0.0


# ---------------------------------------------------------------------------
# Konvertierung / Persistenz
# ---------------------------------------------------------------------------


def test_convert_without_unresolved_has_no_unresolved_field() -> None:
    deck = arena_deck_to_deck_cards(
        {"mainboard": [{"arenaId": 1, "count": 4}], "sideboard": []}
    )
    assert "unresolvedCards" not in deck


def test_saved_deck_with_unresolved_field_keeps_full_total() -> None:
    """Ein gespeichertes Deck (deck-cards.v1) mit unresolvedCards-Feld behält
    die volle totalCards-Basis — spätere Analysen rechnen mit 60, nicht 56."""
    imported = import_arena_deck(_deck_text(), card_db=_card_db(), deck_format="standard")
    saved_deck = arena_deck_to_deck_cards(imported)
    assert "unresolvedCards" in saved_deck

    result = analyze_deck(
        deck=saved_deck,
        collection=_collection(),
        wildcards=_wildcards(),
        card_db=_card_db(),
    )
    assert result["summary"]["totalCards"] == 60


def test_analyze_deck_without_unresolved_field_unchanged() -> None:
    """Decks ohne unresolvedCards-Feld (Altbestand) verhalten sich wie bisher:
    totalCards == resolved Basis, kein Zähler, kein scoreBasis-Unterschied."""
    deck = {
        "deckId": "legacy",
        "name": "Legacy",
        "format": "standard",
        "mainDeck": [{"cardId": 1001, "quantity": 4}],
        "sideboard": [],
        "commandZone": [],
        "companions": [],
    }
    result = analyze_deck(
        deck=deck, collection=_collection(), wildcards=_wildcards(), card_db=_card_db()
    )
    summary = result["summary"]
    assert summary["totalCards"] == 4
    assert summary["unresolvedCards"] == 0
    assert summary["scoreBasis"] == {"owned": 4, "resolved": 4, "total": 4}
    assert summary["completionScore"] == 100.0
    assert result["unresolvedCards"] == []
    assert "arena-id-unbekannt" not in result["warnings"]
