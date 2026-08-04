"""T8: Advisor Result View — structured analysis rendering tests.

Tests three layers:
1. Backend: _parse_structured_analysis() correctly extracts structured fields
   from JSON LLM responses (with and without code fences).
2. Backend: POST /api/advisor/analyze includes a "structured" field when the
   LLM returns JSON, and omits it when the LLM returns plain text.
3. Frontend: dashboard.js contains the renderAdvisorResult function and all
   block renderers. HTML/CSS contains the advisor-result classes.

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
    _parse_structured_analysis,
    _dashboard_js,
    _render_index,
)


# ---------------------------------------------------------------------------
# Helpers (mirrored from test_quick_actions.py)
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
                    ],
                    "sideboard": [
                        {"cardId": 100004, "name": "Duress", "count": 3},
                    ],
                },
            },
        ]
    }


def _make_test_collection() -> dict:
    return {
        "source": "test",
        "cards": {"100001": 4, "100002": 4, "100003": 20},
        "wildcards": {"wcCommon": 10, "wcUncommon": 5, "wcRare": 3, "wcMythic": 1},
    }


# ---------------------------------------------------------------------------
# A structured JSON LLM response for testing
# ---------------------------------------------------------------------------

STRUCTURED_LLM_RESPONSE = json.dumps({
    "summary": {
        "topPriority": "Craft 2x Sheoldred for midrange matchups",
        "confidence": "high",
        "notes": "Deck is solid but lacks card advantage engines.",
    },
    "coreCards": [
        {"name": "Lightning Strike", "count": 4, "role": "Removal"},
        {"name": "Shock", "count": 4, "role": "Early removal"},
        {"name": "Mountain", "count": 20, "role": "Land base"},
    ],
    "missingCards": [
        {"name": "Sheoldred, the Apocalypse", "count": 2, "rarity": "mythic", "reason": "Card advantage"},
        {"name": "Fable of the Mirror-Breaker", "count": 3, "rarity": "rare", "reason": "Value engine"},
    ],
    "craftPriorities": [
        {
            "reason": "Top priority: card advantage",
            "cards": [
                {"name": "Sheoldred, the Apocalypse", "count": 2, "rarity": "mythic", "forDecks": ["Test Mono Red"]},
            ],
        },
    ],
    "cuts": [
        {"name": "Shock", "count": 1, "reason": "Too narrow for current meta"},
    ],
    "manaCurve": {
        "cmc0": 0, "cmc1": 8, "cmc2": 4, "cmc3": 0, "cmc4": 0, "cmc5": 0, "cmc6plus": 0,
    },
    "riskAssessment": {
        "lands": "Good — 20 lands is solid for aggro",
        "curve": "Warning — heavy on 1-drops, no 3+ drops",
        "synergy": "Good — burn package synergizes well",
        "sideboard": "Low — only 3 sideboard cards",
    },
})


# ---------------------------------------------------------------------------
# Unit tests: _parse_structured_analysis
# ---------------------------------------------------------------------------

class TestParseStructuredAnalysis:
    """Unit tests for _parse_structured_analysis()."""

    def test_parses_full_json_response(self) -> None:
        """A complete JSON LLM response is parsed into all structured fields."""
        result = _parse_structured_analysis(STRUCTURED_LLM_RESPONSE)
        assert "summary" in result
        assert result["summary"]["topPriority"] == "Craft 2x Sheoldred for midrange matchups"
        assert result["summary"]["confidence"] == "high"
        assert result["summary"]["notes"].startswith("Deck is solid")

        assert "coreCards" in result
        assert len(result["coreCards"]) == 3
        assert result["coreCards"][0]["name"] == "Lightning Strike"
        assert result["coreCards"][0]["count"] == 4
        assert result["coreCards"][0]["role"] == "Removal"

        assert "missingCards" in result
        assert len(result["missingCards"]) == 2
        assert result["missingCards"][0]["name"] == "Sheoldred, the Apocalypse"
        assert result["missingCards"][0]["rarity"] == "mythic"

        assert "craftPriorities" in result
        assert len(result["craftPriorities"]) == 1
        assert result["craftPriorities"][0]["reason"] == "Top priority: card advantage"
        assert result["craftPriorities"][0]["cards"][0]["forDecks"] == ["Test Mono Red"]

        assert "cuts" in result
        assert result["cuts"][0]["name"] == "Shock"
        assert result["cuts"][0]["count"] == 1

        assert "manaCurve" in result
        assert result["manaCurve"]["cmc1"] == 8
        assert result["manaCurve"]["cmc6plus"] == 0

        assert "riskAssessment" in result
        assert result["riskAssessment"]["lands"] == "Good — 20 lands is solid for aggro"
        assert result["riskAssessment"]["curve"].startswith("Warning")

    def test_parses_json_with_code_fence(self) -> None:
        """JSON wrapped in ```json ... ``` fences is parsed correctly."""
        fenced = "```json\n" + STRUCTURED_LLM_RESPONSE + "\n```"
        result = _parse_structured_analysis(fenced)
        assert "summary" in result
        assert result["summary"]["topPriority"].startswith("Craft 2x")

    def test_parses_json_with_plain_code_fence(self) -> None:
        """JSON wrapped in plain ``` ... ``` fences is parsed correctly."""
        fenced = "```\n" + STRUCTURED_LLM_RESPONSE + "\n```"
        result = _parse_structured_analysis(fenced)
        assert "summary" in result

    def test_parses_json_with_surrounding_text(self) -> None:
        """JSON embedded in surrounding prose is extracted via brace matching."""
        embedded = "Here is my analysis:\n" + STRUCTURED_LLM_RESPONSE + "\nHope this helps!"
        result = _parse_structured_analysis(embedded)
        assert "summary" in result

    def test_plain_text_returns_empty(self) -> None:
        """Plain text (no JSON) returns an empty dict."""
        result = _parse_structured_analysis("This deck needs more removal. Try adding Sheoldred.")
        assert result == {}

    def test_empty_string_returns_empty(self) -> None:
        result = _parse_structured_analysis("")
        assert result == {}

    def test_partial_json_returns_partial_fields(self) -> None:
        """JSON with only some fields returns only those fields."""
        partial = json.dumps({
            "summary": {"topPriority": "Add more lands"},
            "manaCurve": {"cmc0": 0, "cmc1": 4, "cmc2": 6},
        })
        result = _parse_structured_analysis(partial)
        assert "summary" in result
        assert "manaCurve" in result
        assert "coreCards" not in result
        assert "missingCards" not in result

    def test_malformed_json_returns_empty(self) -> None:
        """Malformed JSON returns empty dict, not an exception."""
        result = _parse_structured_analysis("{broken json,,,}")
        assert result == {}

    def test_json_array_returns_empty(self) -> None:
        """A JSON array (not object) returns empty dict."""
        result = _parse_structured_analysis('["not", "an", "object"]')
        assert result == {}

    def test_cmc6plus_fallback_from_cmc6_plus_key(self) -> None:
        """manaCurve with 'cmc6+' key is mapped to 'cmc6plus'."""
        data = json.dumps({"manaCurve": {"cmc0": 0, "cmc1": 2, "cmc6+": 3}})
        result = _parse_structured_analysis(data)
        assert result["manaCurve"]["cmc6plus"] == 3


# ---------------------------------------------------------------------------
# Integration tests: POST /api/advisor/analyze with structured response
# ---------------------------------------------------------------------------

class TestAnalyzeEndpointStructured:
    """POST /api/advisor/analyze returns structured fields when LLM gives JSON."""

    @patch("server.app._call_llm_chat")
    def test_analyze_returns_structured_when_llm_returns_json(self, mock_llm: MagicMock) -> None:
        """Analyze response includes 'structured' field when LLM returns JSON."""
        mock_llm.return_value = {"response": STRUCTURED_LLM_RESPONSE, "model": "test-model"}
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
                assert data["schema"] == "advisor-analyze.v1"
                assert "analysis" in data  # raw text always present
                assert "structured" in data
                assert data["structured"]["summary"]["topPriority"].startswith("Craft")
                assert len(data["structured"]["coreCards"]) == 3
                assert len(data["structured"]["missingCards"]) == 2
                assert len(data["structured"]["craftPriorities"]) == 1
                assert len(data["structured"]["cuts"]) == 1
                assert data["structured"]["manaCurve"]["cmc1"] == 8
                assert "riskAssessment" in data["structured"]
            finally:
                server.shutdown()

    @patch("server.app._call_llm_chat")
    def test_analyze_omits_structured_when_llm_returns_plain_text(self, mock_llm: MagicMock) -> None:
        """Analyze response has no 'structured' field when LLM returns plain text."""
        mock_llm.return_value = {"response": "This deck needs more removal.", "model": "test"}
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
                assert "analysis" in data
                assert "structured" not in data
            finally:
                server.shutdown()

    @patch("server.app._call_llm_chat")
    def test_analyze_structured_with_code_fenced_json(self, mock_llm: MagicMock) -> None:
        """Analyze parses structured data from code-fenced JSON LLM response."""
        fenced = "```json\n" + STRUCTURED_LLM_RESPONSE + "\n```"
        mock_llm.return_value = {"response": fenced, "model": "test"}
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
                assert data["structured"]["summary"]["confidence"] == "high"
            finally:
                server.shutdown()


# ---------------------------------------------------------------------------
# Frontend tests: dashboard.js contains renderAdvisorResult and block renderers
# ---------------------------------------------------------------------------

class TestFrontendRenderAdvisorResult:
    """dashboard.js and HTML contain the T8 advisor result view code."""

    def test_dashboard_js_contains_render_advisor_result(self) -> None:
        """dashboard.js must define the renderAdvisorResult function."""
        js = _dashboard_js()
        assert "renderAdvisorResult" in js
        assert "function renderAdvisorResult" in js

    def test_dashboard_js_contains_all_block_renderers(self) -> None:
        """dashboard.js must render all 7 structured blocks."""
        js = _dashboard_js()
        # Summary
        assert "advisor-result-summary" in js
        assert "top-priority" in js
        assert "confidence-badge" in js
        # Core Cards
        assert "Core Cards" in js
        # Missing Cards
        assert "Missing Cards" in js
        # Craft Priorities
        assert "Craft Priorities" in js
        assert "advisor-craft-group" in js
        # Cuts
        assert "Cuts" in js
        assert "advisor-cuts-list" in js
        # Mana Curve
        assert "Mana Curve" in js
        assert "advisor-mana-curve" in js
        assert "advisor-mana-curve-bar" in js
        # Risk Assessment
        assert "Risk Assessment" in js
        assert "advisor-risk-grid" in js
        assert "advisor-risk-item" in js

    def test_dashboard_js_uses_render_advisor_result_in_analyze_handler(self) -> None:
        """The analyze button handler must call renderAdvisorResult."""
        js = _dashboard_js()
        assert "renderAdvisorResult(data, deckAnalyzeResult)" in js

    def test_dashboard_js_has_fallback_for_plain_text(self) -> None:
        """renderAdvisorResult must have a <pre> fallback for non-structured responses."""
        js = _dashboard_js()
        # The fallback path should create a pre element
        assert "Fallback: raw text" in js or "raw text" in js.lower()

    def test_dashboard_js_has_raw_text_block_alongside_structured(self) -> None:
        """When structured data is present, raw text is still rendered in a block."""
        js = _dashboard_js()
        assert "Raw Analysis Text" in js

    def test_html_contains_advisor_result_css(self) -> None:
        """The HTML template must include CSS classes for advisor result blocks."""
        from server.app import _render_index
        from advisor.llm_config import LLMConfig
        collection = {"cards": {}, "source": "test", "diagnostics": {"warnings": []}}
        decks = {"decks": []}
        config = LLMConfig()
        html = _render_index(collection, None, None, None, decks, config)
        assert "advisor-result" in html
        assert "advisor-result-block" in html
        assert "advisor-mana-curve" in html
        assert "advisor-risk-grid" in html
        assert "advisor-craft-group" in html

    def test_html_contains_analyze_result_container(self) -> None:
        """The HTML must have the deck-analyze-result container div."""
        from server.app import _render_index
        from advisor.llm_config import LLMConfig
        collection = {"cards": {}, "source": "test", "diagnostics": {"warnings": []}}
        decks = {"decks": []}
        config = LLMConfig()
        html = _render_index(collection, None, None, None, decks, config)
        assert 'id="deck-analyze-result"' in html

    def test_dashboard_js_has_rarity_css_classes(self) -> None:
        """dashboard.js must apply rarity CSS classes to card elements."""
        js = _dashboard_js()
        assert "card-rarity" in js
        assert "mythic" in js
        assert "rare" in js