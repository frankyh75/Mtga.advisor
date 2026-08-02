"""Tests for deck-card integration: server endpoints, completion with deck.v1, LLM prompt with cards."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from advisor.completion import build_completion_advice, _normalize_deck_cards  # noqa: E402
from advisor.llm_advisor import _build_prompt, _format_deck_cards_for_prompt  # noqa: E402


# ---------------------------------------------------------------------------
# Completion: deck.v1 format (from memory scan / decks-container)
# ---------------------------------------------------------------------------

def test_normalize_deck_cards_from_deck_v1_named_list() -> None:
    """deck.v1 format with named card lists should be normalized correctly."""
    deck = {
        "schema": "deck.v1",
        "deckId": "test-1",
        "name": "Test Deck",
        "cards": {
            "mainboard": [
                {"cardId": 100, "name": "Lightning Bolt", "count": 4},
                {"cardId": 200, "name": "Counterspell", "count": 2},
            ],
            "sideboard": [
                {"cardId": 300, "name": "Negate", "count": 2},
            ],
        },
    }
    mainboard, sideboard = _normalize_deck_cards(deck)
    assert len(mainboard) == 2
    assert mainboard[0]["arenaId"] == 100
    assert mainboard[0]["count"] == 4
    assert mainboard[0]["name"] == "Lightning Bolt"
    assert len(sideboard) == 1
    assert sideboard[0]["arenaId"] == 300


def test_normalize_deck_cards_from_decks_container_grpId_dict() -> None:
    """decks-container.v1 format with grpId→qty dicts should be normalized."""
    deck = {
        "deckId": "test-2",
        "name": "Scan Deck",
        "cards": {
            "mainboard": {"100": 4, "200": 2},
            "sideboard": {"300": 1},
        },
    }
    mainboard, sideboard = _normalize_deck_cards(deck)
    assert len(mainboard) == 2
    assert mainboard[0]["arenaId"] == 100
    assert mainboard[0]["count"] == 4
    assert len(sideboard) == 1
    assert sideboard[0]["arenaId"] == 300


def test_normalize_deck_cards_arena_deck_v1_passthrough() -> None:
    """arena-deck.v1 format should pass through unchanged."""
    deck = {
        "schema": "arena-deck.v1",
        "mainboard": [{"arenaId": 100, "count": 4, "name": "Test"}],
        "sideboard": [],
    }
    mainboard, sideboard = _normalize_deck_cards(deck)
    assert len(mainboard) == 1
    assert mainboard[0]["arenaId"] == 100


def test_completion_advice_with_deck_v1_format() -> None:
    """build_completion_advice should work with deck.v1 card format."""
    collection = {
        "schema": "collection.v1",
        "cards": {"100": 2, "200": 4},
        "diagnostics": {
            "completeness": {"cards": "complete", "wildcards": "complete"},
        },
    }
    deck = {
        "schema": "deck.v1",
        "deckId": "scan-1",
        "name": "Scanned Deck",
        "cards": {
            "mainboard": [
                {"cardId": 100, "name": "Lightning Bolt", "count": 4},
                {"cardId": 200, "name": "Counterspell", "count": 2},
            ],
            "sideboard": [
                {"cardId": 300, "name": "Negate", "count": 2},
            ],
        },
    }
    card_db = {
        100: {"name": "Lightning Bolt", "rarity": "common"},
        200: {"name": "Counterspell", "rarity": "uncommon"},
        300: {"name": "Negate", "rarity": "uncommon"},
    }
    result = build_completion_advice(collection=collection, deck=deck, card_db=card_db)
    assert result["schema"] == "advisor-result.v1"
    assert result["deck"]["name"] == "Scanned Deck"
    # Missing: 2x Lightning Bolt (100), 2x Negate (300)
    assert result["summary"]["missingCards"] == 4
    assert result["summary"]["missingUniqueCards"] == 2


# ---------------------------------------------------------------------------
# LLM Advisor: _format_deck_cards_for_prompt
# ---------------------------------------------------------------------------

def test_format_deck_cards_named_list() -> None:
    cards = {
        "mainboard": [
            {"cardId": 100, "name": "Lightning Bolt", "count": 4},
            {"cardId": 200, "name": "Counterspell", "count": 2},
        ],
        "sideboard": [
            {"cardId": 300, "name": "Negate", "count": 2},
        ],
    }
    lines = _format_deck_cards_for_prompt(cards)
    assert len(lines) == 2
    assert "Mainboard" in lines[0]
    assert "4x Lightning Bolt" in lines[0]
    assert "2x Counterspell" in lines[0]
    assert "Sideboard" in lines[1]
    assert "2x Negate" in lines[1]


def test_format_deck_cards_grpId_dict() -> None:
    cards = {
        "mainboard": {"100": 4, "200": 2},
    }
    lines = _format_deck_cards_for_prompt(cards)
    assert len(lines) == 1
    assert "4x ID:100" in lines[0]
    assert "2x ID:200" in lines[0]


def test_format_deck_cards_truncation_named_list() -> None:
    """When a pile exceeds max_cards_per_pile, entries should be truncated."""
    cards = {
        "mainboard": [
            {"cardId": i, "name": f"Card{i}", "count": 1}
            for i in range(100)
        ],
    }
    lines = _format_deck_cards_for_prompt(cards, max_cards_per_pile=10)
    assert len(lines) == 1
    assert "Card0" in lines[0]
    assert "Card9" in lines[0]
    assert "Card10" not in lines[0]
    assert "+90 more" in lines[0]
    assert "100 unique" in lines[0]


def test_format_deck_cards_truncation_grpId_dict() -> None:
    """When a grpId dict pile exceeds max_cards_per_pile, entries should be truncated."""
    cards = {
        "mainboard": {str(i): 1 for i in range(100)},
    }
    lines = _format_deck_cards_for_prompt(cards, max_cards_per_pile=10)
    assert len(lines) == 1
    assert "+90 more" in lines[0]
    assert "100 unique" in lines[0]


def test_format_deck_cards_no_truncation_under_limit() -> None:
    """When pile size is under max_cards_per_pile, no truncation suffix."""
    cards = {
        "mainboard": [
            {"cardId": 100, "name": "Bolt", "count": 4},
        ],
    }
    lines = _format_deck_cards_for_prompt(cards, max_cards_per_pile=60)
    assert len(lines) == 1
    assert "+" not in lines[0]
    assert "1 unique" in lines[0]


def test_build_prompt_includes_deck_cards() -> None:
    """_build_prompt should include deck card names when available."""
    collection = {
        "cards": {"100": 2, "200": 4},
        "wildcards": {"wcCommon": 10, "wcUncommon": 5, "wcRare": 3, "wcMythic": 1},
        "diagnostics": {"completeness": {"cards": "complete"}},
    }
    decks = {
        "schema": "decks.v1",
        "decks": [
            {
                "name": "Test Deck",
                "deckId": "1",
                "attributes": {"Format": "standard"},
                "cards": {
                    "mainboard": [
                        {"cardId": 100, "name": "Lightning Bolt", "count": 4},
                    ],
                    "sideboard": [
                        {"cardId": 300, "name": "Negate", "count": 2},
                    ],
                },
            },
        ],
    }
    prompt = _build_prompt(collection, decks)
    assert "Lightning Bolt" in prompt
    assert "Negate" in prompt
    assert "Mainboard" in prompt
    assert "Sideboard" in prompt


# ---------------------------------------------------------------------------
# Server: /api/decks-container and /api/deck/{deckId} endpoints
# ---------------------------------------------------------------------------

def test_api_decks_container_endpoint(tmp_path: Path) -> None:
    """Server should serve decks-container.json via /api/decks-container."""
    from server.app import MtgaAdvisorHandler, MtgaAdvisorServer
    from http.server import HTTPServer

    container_data = {
        "schema": "decks-container.v1",
        "decks": [
            {"deckId": "1", "name": "Test", "cards": {"mainboard": {"100": 4}}},
        ],
    }
    (tmp_path / "decks-container.json").write_text(
        json.dumps(container_data), encoding="utf-8"
    )

    server = MtgaAdvisorServer(("127.0.0.1", 0), MtgaAdvisorHandler, tmp_path)
    port = server.server_address[1]
    server.timeout = 1

    import urllib.request
    import threading
    try:
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/decks-container", timeout=2)
        data = json.loads(resp.read().decode("utf-8"))
        assert data["schema"] == "decks-container.v1"
        assert len(data["decks"]) == 1
    finally:
        server.server_close()


def test_api_deck_individual_endpoint(tmp_path: Path) -> None:
    """Server should serve individual deck files via /api/deck/{deckId}."""
    from server.app import MtgaAdvisorHandler, MtgaAdvisorServer
    from http.server import HTTPServer

    decks_dir = tmp_path / "decks"
    decks_dir.mkdir()
    deck_data = {
        "schema": "deck.v1",
        "deckId": "test-1",
        "name": "Test Deck",
        "cards": {
            "mainboard": [{"cardId": 100, "name": "Bolt", "count": 4}],
        },
    }
    (decks_dir / "deck-test-1.json").write_text(
        json.dumps(deck_data), encoding="utf-8"
    )

    server = MtgaAdvisorServer(("127.0.0.1", 0), MtgaAdvisorHandler, tmp_path)
    port = server.server_address[1]
    server.timeout = 1

    import urllib.request
    import threading
    try:
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/deck/test-1", timeout=2)
        data = json.loads(resp.read().decode("utf-8"))
        assert data["schema"] == "deck.v1"
        assert data["name"] == "Test Deck"
        assert data["cards"]["mainboard"][0]["name"] == "Bolt"
    finally:
        server.server_close()


def test_api_deck_not_found(tmp_path: Path) -> None:
    """Server should return 404 for non-existent deck."""
    from server.app import MtgaAdvisorHandler, MtgaAdvisorServer
    import urllib.error
    import urllib.request

    server = MtgaAdvisorServer(("127.0.0.1", 0), MtgaAdvisorHandler, tmp_path)
    port = server.server_address[1]
    server.timeout = 1

    import threading
    try:
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/deck/nonexistent", timeout=2)
            assert False, "Should have raised HTTPError"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        server.server_close()


def test_api_deck_path_traversal_blocked(tmp_path: Path) -> None:
    """Server should reject path traversal attempts in /api/deck/{deckId}."""
    from server.app import MtgaAdvisorHandler, MtgaAdvisorServer
    import urllib.error
    import urllib.request

    # Create a secret file outside decks/ that should not be accessible
    secret_path = tmp_path / "secret.json"
    secret_path.write_text('{"secret": "should-not-leak"}', encoding="utf-8")

    decks_dir = tmp_path / "decks"
    decks_dir.mkdir()

    server = MtgaAdvisorServer(("127.0.0.1", 0), MtgaAdvisorHandler, tmp_path)
    port = server.server_address[1]
    server.timeout = 1

    import threading
    traversal_attempts = [
        "/api/deck/..%2f..%2fsecret",
        "/api/deck/../../secret",
        "/api/deck/..%2F..%2Fsecret",
    ]

    try:
        for attempt in traversal_attempts:
            thread = threading.Thread(target=server.handle_request, daemon=True)
            thread.start()

            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{attempt}", timeout=2)
                assert False, f"Should have raised HTTPError for {attempt}"
            except urllib.error.HTTPError as exc:
                # Should be 400 (bad request) not 200 (data leaked)
                assert exc.code in (400, 404), f"Got {exc.code} for {attempt}"
            thread.join(timeout=2)
    finally:
        server.server_close()


def test_api_deck_special_chars_blocked(tmp_path: Path) -> None:
    """Server should reject deck IDs with special characters."""
    from server.app import MtgaAdvisorHandler, MtgaAdvisorServer
    import urllib.error
    import urllib.request

    server = MtgaAdvisorServer(("127.0.0.1", 0), MtgaAdvisorHandler, tmp_path)
    port = server.server_address[1]
    server.timeout = 1

    import threading
    try:
        thread = threading.Thread(target=server.handle_request, daemon=True)
        thread.start()

        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/deck/..;DROP", timeout=2)
            assert False, "Should have raised HTTPError"
        except urllib.error.HTTPError as exc:
            assert exc.code in (400, 404)
    finally:
        server.server_close()