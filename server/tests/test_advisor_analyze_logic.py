"""T7: POST /api/advisor/analyze — echte Analyse-Logik Tests.

Tests three layers:
1. Unit: _compute_missing_cards() correctly compares deck vs collection
2. Unit: _build_analysis_prompt() produces a structured-JSON-requesting prompt
3. Integration: POST /api/advisor/analyze uses the new prompt + returns
   computedMissingCards in the response, even when LLM fails.

All LLM calls are mocked — no real LLM endpoint is contacted.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from server.app import (  # noqa: E402
    MtgaAdvisorServer,
    MtgaAdvisorHandler,
    configure_basic_auth,
    _compute_missing_cards,
    _build_analysis_prompt,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_test_server(output_dir: Path, port: int = 0):
    server = MtgaAdvisorServer(("127.0.0.1", port), MtgaAdvisorHandler, output_dir)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    return server, actual_port, thread


def _post_json(port: int, path: str, body: dict) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=5)
        return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def _make_test_decks_json() -> dict:
    return {
        "decks": [
            {
                "deckId": "test-deck-001",
                "deckKey": "test-deck-001",
                "name": "Test Mono Red",
                "schema": "deck.v1",
                "cards": {
                    "mainboard": [
                        {"cardId": 100001, "name": "Lightning Strike", "count": 4},
                        {"cardId": 100002, "name": "Shock", "count": 4},
                        {"cardId": 100003, "name": "Mountain", "count": 20},
                        {"cardId": 100005, "name": "Goblin Guide", "count": 4},
                    ],
                    "sideboard": [
                        {"cardId": 100004, "name": "Duress", "count": 3},
                    ],
                },
            },
        ]
    }


def _make_test_collection() -> dict:
    # Player owns: 4x Lightning Strike, 4x Shock, 20x Mountain
    # Missing: 4x Goblin Guide (100005), 3x Duress (100004)
    return {
        "source": "test",
        "cards": {"100001": 4, "100002": 4, "100003": 20},
        "wildcards": {"wcCommon": 10, "wcUncommon": 5, "wcRare": 3, "wcMythic": 1},
    }


def _make_test_collection_plain_wc() -> dict:
    """Collection with plain wildcard keys (common/uncommon/rare/mythic)."""
    return {
        "source": "test",
        "cards": {"100001": 4, "100002": 4, "100003": 20},
        "wildcards": {"common": 10, "uncommon": 5, "rare": 3, "mythic": 1},
    }


# ---------------------------------------------------------------------------
# Unit tests: _compute_missing_cards
# ---------------------------------------------------------------------------

class TestComputeMissingCards:
    """Unit tests for _compute_missing_cards()."""

    def test_finds_missing_cards(self) -> None:
        """Cards in deck but not fully in collection are reported as missing."""
        deck = _make_test_decks_json()["decks"][0]
        collection = _make_test_collection()
        missing = _compute_missing_cards(deck, collection)

        # Goblin Guide (100005): needs 4, has 0, missing 4
        # Duress (100004): needs 3, has 0, missing 3
        assert len(missing) == 2

        names = {m["name"] for m in missing}
        assert "Goblin Guide" in names
        assert "Duress" in names

        goblin = next(m for m in missing if m["name"] == "Goblin Guide")
        assert goblin["needed"] == 4
        assert goblin["owned"] == 0
        assert goblin["missing"] == 4

        duress = next(m for m in missing if m["name"] == "Duress")
        assert duress["needed"] == 3
        assert duress["owned"] == 0
        assert duress["missing"] == 3

    def test_no_missing_when_collection_has_all(self) -> None:
        """When collection has all cards, missing list is empty."""
        deck = _make_test_decks_json()["decks"][0]
        collection = {
            "cards": {"100001": 4, "100002": 4, "100003": 20, "100004": 3, "100005": 4},
        }
        missing = _compute_missing_cards(deck, collection)
        assert missing == []

    def test_partial_ownership(self) -> None:
        """Cards partially owned report the correct missing count."""
        deck = _make_test_decks_json()["decks"][0]
        collection = {
            "cards": {"100001": 4, "100002": 4, "100003": 20, "100005": 1},
        }
        missing = _compute_missing_cards(deck, collection)
        # Missing: Goblin Guide (needs 4, has 1, missing 3) + Duress (needs 3, has 0, missing 3)
        assert len(missing) == 2
        goblin = next(m for m in missing if m["name"] == "Goblin Guide")
        assert goblin["owned"] == 1
        assert goblin["missing"] == 3

    def test_no_collection_returns_empty(self) -> None:
        """Without a collection, missing cards cannot be computed."""
        deck = _make_test_decks_json()["decks"][0]
        missing = _compute_missing_cards(deck, None)
        assert missing == []

    def test_empty_collection_returns_empty(self) -> None:
        """An empty collection dict returns empty list."""
        deck = _make_test_decks_json()["decks"][0]
        missing = _compute_missing_cards(deck, {})
        assert missing == []

    def test_empty_deck_returns_empty(self) -> None:
        """A deck with no cards returns empty list."""
        collection = _make_test_collection()
        missing = _compute_missing_cards({"name": "Empty"}, collection)
        assert missing == []

    def test_supports_grpid_dict_format(self) -> None:
        """Deck cards in grpId->qty dict format are handled."""
        deck = {
            "cards": {
                "mainboard": {"100001": 4, "100099": 2},
            }
        }
        collection = {"cards": {"100001": 4}}
        missing = _compute_missing_cards(deck, collection)
        assert len(missing) == 1
        assert missing[0]["cardId"] == "100099"
        assert missing[0]["missing"] == 2

    def test_supports_flat_list_format(self) -> None:
        """Deck cards in flat list format (mainDeck) are handled."""
        deck = {
            "mainDeck": [
                {"cardId": 100001, "name": "Bolt", "count": 4},
                {"cardId": 100099, "name": "Missing Card", "count": 2},
            ]
        }
        collection = {"cards": {"100001": 4}}
        missing = _compute_missing_cards(deck, collection)
        assert len(missing) == 1
        assert missing[0]["name"] == "Missing Card"
        assert missing[0]["missing"] == 2

    def test_supports_quantity_key(self) -> None:
        """Cards using 'quantity' instead of 'count' are handled."""
        deck = {
            "cards": {
                "mainboard": [
                    {"cardId": 100001, "name": "Bolt", "quantity": 4},
                    {"cardId": 100099, "name": "Missing", "quantity": 3},
                ]
            }
        }
        collection = {"cards": {"100001": 4}}
        missing = _compute_missing_cards(deck, collection)
        assert len(missing) == 1
        assert missing[0]["missing"] == 3


# ---------------------------------------------------------------------------
# Unit tests: _build_analysis_prompt
# ---------------------------------------------------------------------------

class TestBuildAnalysisPrompt:
    """Unit tests for _build_analysis_prompt()."""

    def test_prompt_contains_deck_info(self) -> None:
        """Prompt includes deck name and format."""
        deck = _make_test_decks_json()["decks"][0]
        collection = _make_test_collection()
        prompt = _build_analysis_prompt(deck, collection)

        assert "Test Mono Red" in prompt
        assert "Name:" in prompt
        assert "Format:" in prompt

    def test_prompt_contains_deck_cards(self) -> None:
        """Prompt includes the deck's card list."""
        deck = _make_test_decks_json()["decks"][0]
        collection = _make_test_collection()
        prompt = _build_analysis_prompt(deck, collection)

        assert "Lightning Strike" in prompt
        assert "Mountain" in prompt
        assert "Mainboard" in prompt

    def test_prompt_contains_collection_info(self) -> None:
        """Prompt includes collection summary with wildcards."""
        deck = _make_test_decks_json()["decks"][0]
        collection = _make_test_collection()
        prompt = _build_analysis_prompt(deck, collection)

        assert "Collection" in prompt
        assert "Wildcards" in prompt
        assert "Common: 10" in prompt
        assert "Mythic: 1" in prompt

    def test_prompt_supports_plain_wildcard_keys(self) -> None:
        """Prompt handles both wcXxx and plain wildcard key formats."""
        deck = _make_test_decks_json()["decks"][0]
        collection = _make_test_collection_plain_wc()
        prompt = _build_analysis_prompt(deck, collection)

        assert "Common: 10" in prompt
        assert "Rare: 3" in prompt
        assert "Mythic: 1" in prompt

    def test_prompt_includes_computed_missing_cards(self) -> None:
        """When computed_missing is provided, it appears in the prompt."""
        deck = _make_test_decks_json()["decks"][0]
        collection = _make_test_collection()
        missing = _compute_missing_cards(deck, collection)
        prompt = _build_analysis_prompt(deck, collection, missing)

        assert "Fehlende Karten (berechnet" in prompt
        assert "Goblin Guide" in prompt
        assert "Duress" in prompt
        # Check the format: braucht N, hat M, fehlt K
        assert "braucht 4" in prompt
        assert "fehlt 4" in prompt

    def test_prompt_shows_no_collection_message(self) -> None:
        """When collection is None, prompt notes it."""
        deck = _make_test_decks_json()["decks"][0]
        prompt = _build_analysis_prompt(deck, None, None)

        assert "nicht verfuegbar" in prompt

    def test_prompt_requests_json_schema(self) -> None:
        """Prompt asks for JSON output matching the structured schema."""
        deck = _make_test_decks_json()["decks"][0]
        collection = _make_test_collection()
        prompt = _build_analysis_prompt(deck, collection)

        assert "summary" in prompt
        assert "topPriority" in prompt
        assert "confidence" in prompt
        assert "coreCards" in prompt
        assert "missingCards" in prompt
        assert "craftPriorities" in prompt
        assert "cuts" in prompt
        assert "manaCurve" in prompt
        assert "riskAssessment" in prompt
        assert "cm c0" in prompt or "cmc0" in prompt

    def test_prompt_includes_analysis_instructions(self) -> None:
        """Prompt includes key analysis instructions."""
        deck = _make_test_decks_json()["decks"][0]
        collection = _make_test_collection()
        prompt = _build_analysis_prompt(deck, collection)

        assert "Priorisiere Rares und Mythics" in prompt
        assert "konkrete Kartennamen" in prompt
        assert "NUR mit JSON" in prompt

    def test_prompt_includes_sideboard(self) -> None:
        """Prompt includes sideboard cards."""
        deck = _make_test_decks_json()["decks"][0]
        collection = _make_test_collection()
        prompt = _build_analysis_prompt(deck, collection)

        assert "Sideboard" in prompt
        assert "Duress" in prompt


# ---------------------------------------------------------------------------
# Integration tests: POST /api/advisor/analyze with computed missing cards
# ---------------------------------------------------------------------------

class TestAnalyzeEndpointComputedMissing:
    """POST /api/advisor/analyze returns computedMissingCards."""

    @patch("server.app._call_llm_chat")
    def test_analyze_returns_computed_missing_cards(self, mock_llm: MagicMock) -> None:
        """Response includes computedMissingCards field."""
        mock_llm.return_value = {"response": "Plain text analysis", "model": "test"}
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            (Path(tmpdir) / "collection.json").write_text(
                json.dumps(_make_test_collection()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/analyze", {
                    "deckId": "test-deck-001",
                })
                assert status == 200
                assert "computedMissingCards" in data
                missing = data["computedMissingCards"]
                assert len(missing) == 2
                names = {m["name"] for m in missing}
                assert "Goblin Guide" in names
                assert "Duress" in names
            finally:
                server.shutdown()

    @patch("server.app._call_llm_chat")
    def test_analyze_returns_computed_missing_even_on_llm_error(self, mock_llm: MagicMock) -> None:
        """Even when LLM fails, computedMissingCards is returned."""
        mock_llm.return_value = {"error": "LLM nicht erreichbar"}
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            (Path(tmpdir) / "collection.json").write_text(
                json.dumps(_make_test_collection()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/analyze", {
                    "deckId": "test-deck-001",
                })
                assert status == 200
                assert "error" in data
                assert "computedMissingCards" in data
                assert len(data["computedMissingCards"]) == 2
                assert data["deckName"] == "Test Mono Red"
            finally:
                server.shutdown()

    @patch("server.app._call_llm_chat")
    def test_analyze_uses_dedicated_prompt_not_chat_prompt(self, mock_llm: MagicMock) -> None:
        """The LLM is called with _build_analysis_prompt, not _build_chat_prompt."""
        mock_llm.return_value = {"response": "{}", "model": "test"}
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            (Path(tmpdir) / "collection.json").write_text(
                json.dumps(_make_test_collection()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                _post_json(port, "/api/advisor/analyze", {
                    "deckId": "test-deck-001",
                })
                # Check the prompt passed to _call_llm_chat
                call_args = mock_llm.call_args
                prompt = call_args[0][1]  # second positional arg
                # The analysis prompt includes "Fehlende Karten" section
                assert "Fehlende Karten" in prompt
                assert "riskAssessment" in prompt
                assert "craftPriorities" in prompt
                # It should NOT be the generic chat prompt (which has "Frage")
                assert "## Frage" not in prompt
            finally:
                server.shutdown()

    @patch("server.app._call_llm_chat")
    def test_analyze_computed_missing_empty_when_collection_complete(self, mock_llm: MagicMock) -> None:
        """When all deck cards are in collection, computedMissingCards is empty."""
        mock_llm.return_value = {"response": "All good", "model": "test"}
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            # Collection has all cards
            full_collection = {
                "source": "test",
                "cards": {"100001": 4, "100002": 4, "100003": 20, "100004": 3, "100005": 4},
                "wildcards": {"wcCommon": 10, "wcUncommon": 5, "wcRare": 3, "wcMythic": 1},
            }
            (Path(tmpdir) / "collection.json").write_text(
                json.dumps(full_collection), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/analyze", {
                    "deckId": "test-deck-001",
                })
                assert status == 200
                assert data["computedMissingCards"] == []
            finally:
                server.shutdown()

    @patch("server.app._call_llm_chat")
    def test_analyze_structured_and_computed_coexist(self, mock_llm: MagicMock) -> None:
        """Both 'structured' (from LLM) and 'computedMissingCards' are present."""
        structured_response = json.dumps({
            "summary": {"topPriority": "Craft Goblins", "confidence": "high", "notes": "..."},
            "craftPriorities": [
                {"reason": "Aggro", "cards": [{"name": "Goblin Guide", "count": 4, "rarity": "rare", "forDecks": ["Test Mono Red"]}]}
            ],
        })
        mock_llm.return_value = {"response": structured_response, "model": "test"}
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            (Path(tmpdir) / "collection.json").write_text(
                json.dumps(_make_test_collection()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/analyze", {
                    "deckId": "test-deck-001",
                })
                assert status == 200
                assert "structured" in data
                assert "computedMissingCards" in data
                assert data["structured"]["summary"]["topPriority"] == "Craft Goblins"
                assert len(data["computedMissingCards"]) == 2
            finally:
                server.shutdown()