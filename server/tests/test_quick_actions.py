"""T10: Quick-Action Schnellaktionen Tests — Export, Analyze, Improve.

Testet die drei POST-Endpoints:
  - POST /api/advisor/export  → Arena-Text Export
  - POST /api/advisor/analyze → LLM-Analyse (gemockt)
  - POST /api/advisor/improve → LLM-Verbesserung (gemockt)

Sowie UI-Verifikation: Action-Buttons im HTML, JS-Handler in dashboard.js.

Alle LLM-Aufrufe werden gemockt — es wird kein echter LLM-Endpoint kontaktiert.
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
    _dashboard_js,
)
from advisor.llm_config import LLMConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_test_server(output_dir: Path, port: int = 0):
    """Start a test server on a random port. Returns (server, actual_port, thread)."""
    server = MtgaAdvisorServer(("127.0.0.1", port), MtgaAdvisorHandler, output_dir)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    return server, actual_port, thread


def _post_json(port: int, path: str, body: dict) -> tuple[int, dict]:
    """POST JSON to the test server, return (status_code, response_json)."""
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
    """Create a test decks.json with a deck.v1 deck and an arena-deck.v1 deck."""
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
            {
                "deckId": "test-deck-002",
                "deckKey": "test-deck-002",
                "name": "Test Azorius Control",
                "schema": "arena-deck.v1",
                "mainboard": [
                    {"arenaId": 200001, "name": "Absorb", "count": 3},
                    {"arenaId": 200002, "name": "Island", "count": 10},
                    {"arenaId": 200003, "name": "Plains", "count": 10},
                ],
                "sideboard": [
                    {"arenaId": 200004, "name": "Dovin's Veto", "count": 2},
                ],
            },
        ]
    }


def _make_test_collection() -> dict:
    """Create a minimal test collection.json."""
    return {
        "source": "test",
        "cards": {"100001": 4, "100002": 4, "100003": 20},
        "wildcards": {"wcCommon": 10, "wcUncommon": 5, "wcRare": 3, "wcMythic": 1},
    }


# ---------------------------------------------------------------------------
# Export Endpoint Tests
# ---------------------------------------------------------------------------

class TestExportEndpoint:
    """POST /api/advisor/export tests."""

    def test_export_deck_v1_returns_arena_text(self) -> None:
        """Export of a deck.v1 deck returns Arena-format text."""
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
                status, data = _post_json(port, "/api/advisor/export", {
                    "deckId": "test-deck-001",
                })
                assert status == 200
                assert data["schema"] == "advisor-export.v1"
                assert data["format"] == "arena"
                assert "Lightning Strike" in data["arenaText"]
                assert "4 Lightning Strike" in data["arenaText"]
                assert "4 Shock" in data["arenaText"]
                assert "20 Mountain" in data["arenaText"]
                # Sideboard separated by blank line
                assert "\n\n" in data["arenaText"]
                assert "3 Duress" in data["arenaText"]
                assert data["lineCount"] > 0
            finally:
                server.shutdown()

    def test_export_arena_v1_deck(self) -> None:
        """Export of an arena-deck.v1 deck returns Arena-format text."""
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/export", {
                    "deckId": "test-deck-002",
                })
                assert status == 200
                assert data["schema"] == "advisor-export.v1"
                assert "3 Absorb" in data["arenaText"]
                assert "10 Island" in data["arenaText"]
                assert "2 Dovin's Veto" in data["arenaText"]
            finally:
                server.shutdown()

    def test_export_with_deck_key(self) -> None:
        """Export works with deckKey instead of deckId."""
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/export", {
                    "deckKey": "test-deck-001",
                })
                assert status == 200
                assert "Lightning Strike" in data["arenaText"]
            finally:
                server.shutdown()

    def test_export_missing_deck_id_returns_400(self) -> None:
        """Export without deckId or deckKey returns 400."""
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/export", {})
                assert status == 400
                assert data["error"] == "missing_field"
            finally:
                server.shutdown()

    def test_export_unknown_deck_returns_404(self) -> None:
        """Export of a non-existent deck returns 404."""
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/export", {
                    "deckId": "nonexistent-deck",
                })
                assert status == 404
                assert data["error"] == "not_found"
            finally:
                server.shutdown()

    def test_export_invalid_json_returns_400(self) -> None:
        """Export with invalid JSON body returns 400."""
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/advisor/export",
                    data=b"not-json{",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                try:
                    urllib.request.urlopen(req, timeout=5)
                except urllib.error.HTTPError as e:
                    assert e.code == 400
                    data = json.loads(e.read().decode("utf-8"))
                    assert data["error"] == "invalid_json"
            finally:
                server.shutdown()


# ---------------------------------------------------------------------------
# Analyze Endpoint Tests (LLM mocked)
# ---------------------------------------------------------------------------

class TestAnalyzeEndpoint:
    """POST /api/advisor/analyze tests with mocked LLM."""

    @patch("server.app._call_llm_chat")
    def test_analyze_returns_analysis(self, mock_llm: MagicMock) -> None:
        """Analyze returns LLM analysis text."""
        mock_llm.return_value = {"response": "This deck needs more removal.", "model": "test-model"}
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
                assert data["deckName"] == "Test Mono Red"
                assert "removal" in data["analysis"]
                assert data["model"] == "test-model"
            finally:
                server.shutdown()

    @patch("server.app._call_llm_chat")
    def test_analyze_with_custom_question(self, mock_llm: MagicMock) -> None:
        """Analyze uses dedicated analysis prompt (T7) with manaCurve schema."""
        mock_llm.return_value = {"response": "Custom analysis", "model": "test"}
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
                    "question": "What is the mana curve?",
                })
                assert status == 200
                # T7: the dedicated analysis prompt requests a manaCurve block
                assert mock_llm.called
                call_args = mock_llm.call_args
                prompt = call_args[0][1]  # second positional arg is the prompt
                assert "manaCurve" in prompt
            finally:
                server.shutdown()

    def test_analyze_missing_deck_id_returns_400(self) -> None:
        """Analyze without deckId returns 400."""
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/analyze", {})
                assert status == 400
                assert data["error"] == "missing_field"
            finally:
                server.shutdown()

    def test_analyze_unknown_deck_returns_404(self) -> None:
        """Analyze of a non-existent deck returns 404."""
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/analyze", {
                    "deckId": "nonexistent",
                })
                assert status == 404
            finally:
                server.shutdown()

    @patch("server.app._call_llm_chat")
    def test_analyze_llm_error_returns_response_with_error(self, mock_llm: MagicMock) -> None:
        """Analyze returns 200 with error field when LLM fails."""
        mock_llm.return_value = {"error": "LLM nicht erreichbar: timeout"}
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
            finally:
                server.shutdown()


# ---------------------------------------------------------------------------
# Improve Endpoint Tests (LLM mocked)
# ---------------------------------------------------------------------------

class TestImproveEndpoint:
    """POST /api/advisor/improve tests with mocked LLM."""

    @patch("server.app._call_llm_chat")
    def test_improve_returns_suggestions(self, mock_llm: MagicMock) -> None:
        """Improve returns LLM improvement suggestions."""
        mock_llm.return_value = {"response": "Replace Shock with Play with Fire.", "model": "test-model"}
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
                status, data = _post_json(port, "/api/advisor/improve", {
                    "deckId": "test-deck-001",
                })
                assert status == 200
                assert data["schema"] == "advisor-improve.v1"
                assert data["deckName"] == "Test Mono Red"
                assert "Play with Fire" in data["improvements"]
                assert data["model"] == "test-model"
            finally:
                server.shutdown()

    @patch("server.app._call_llm_chat")
    def test_improve_with_custom_question(self, mock_llm: MagicMock) -> None:
        """Improve passes custom question to LLM."""
        mock_llm.return_value = {"response": "Sideboard needs more counterspells.", "model": "test"}
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
                status, data = _post_json(port, "/api/advisor/improve", {
                    "deckId": "test-deck-001",
                    "question": "Improve the sideboard against control.",
                })
                assert status == 200
                assert mock_llm.called
                call_args = mock_llm.call_args
                prompt = call_args[0][1]
                assert "sideboard" in prompt.lower()
                assert "control" in prompt.lower()
            finally:
                server.shutdown()

    def test_improve_missing_deck_id_returns_400(self) -> None:
        """Improve without deckId returns 400."""
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/improve", {})
                assert status == 400
                assert data["error"] == "missing_field"
            finally:
                server.shutdown()

    def test_improve_unknown_deck_returns_404(self) -> None:
        """Improve of a non-existent deck returns 404."""
        configure_basic_auth(None)
        with tempfile.TemporaryDirectory() as tmpdir:
            (Path(tmpdir) / "decks.json").write_text(
                json.dumps(_make_test_decks_json()), encoding="utf-8"
            )
            server, port, thread = _start_test_server(Path(tmpdir))
            try:
                status, data = _post_json(port, "/api/advisor/improve", {
                    "deckId": "nonexistent",
                })
                assert status == 404
            finally:
                server.shutdown()

    @patch("server.app._call_llm_chat")
    def test_improve_llm_error_returns_response_with_error(self, mock_llm: MagicMock) -> None:
        """Improve returns 200 with error field when LLM fails."""
        mock_llm.return_value = {"error": "HTTP 500: internal error"}
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
                status, data = _post_json(port, "/api/advisor/improve", {
                    "deckId": "test-deck-001",
                })
                assert status == 200
                assert "error" in data
            finally:
                server.shutdown()


# ---------------------------------------------------------------------------
# UI Verifikation: HTML + dashboard.js
# ---------------------------------------------------------------------------

class TestQuickActionsUI:
    """Verify that the HTML and dashboard.js contain the quick-action buttons."""

    def test_html_contains_action_buttons(self) -> None:
        """The rendered HTML must contain the three action buttons."""
        from server.app import _render_index
        collection = {"cards": {}, "source": "test", "diagnostics": {"warnings": []}}
        decks = {"decks": []}
        config = LLMConfig()
        html_output = _render_index(collection, None, None, None, decks, config)
        assert 'id="btn-export-arena"' in html_output
        assert 'id="btn-analyze"' in html_output
        assert 'id="btn-improve"' in html_output
        assert 'class="deck-detail-actions"' in html_output
        assert 'id="deck-action-status"' in html_output

    def test_html_contains_result_containers(self) -> None:
        """The HTML must contain result containers for analyze/improve."""
        from server.app import _render_index
        collection = {"cards": {}, "source": "test", "diagnostics": {"warnings": []}}
        decks = {"decks": []}
        config = LLMConfig()
        html_output = _render_index(collection, None, None, None, decks, config)
        assert 'id="deck-analyze-result"' in html_output
        assert 'id="deck-improve-result"' in html_output

    def test_dashboard_js_contains_export_handler(self) -> None:
        """dashboard.js must contain the export button handler."""
        js = _dashboard_js()
        assert "btn-export-arena" in js
        assert "/api/advisor/export" in js
        assert "Blob" in js  # Download via Blob
        assert "URL.createObjectURL" in js

    def test_dashboard_js_contains_analyze_handler(self) -> None:
        """dashboard.js must contain the analyze button handler."""
        js = _dashboard_js()
        assert "btn-analyze" in js
        assert "/api/advisor/analyze" in js
        assert "deck-analyze-result" in js

    def test_dashboard_js_contains_improve_handler(self) -> None:
        """dashboard.js must contain the improve button handler."""
        js = _dashboard_js()
        assert "btn-improve" in js
        assert "/api/advisor/improve" in js
        assert "deck-improve-result" in js

    def test_dashboard_js_contains_action_status_helper(self) -> None:
        """dashboard.js must contain the action status helper and disable logic."""
        js = _dashboard_js()
        assert "setActionStatus" in js
        assert "disableActions" in js
        assert "currentDeckPayload" in js

    def test_dashboard_js_no_innerhtml_in_action_handlers(self) -> None:
        """Action handlers must not use innerHTML (XSS hardening)."""
        js = _dashboard_js()
        # Check the action handler section doesn't use innerHTML
        assert "innerHTML" not in js

    def test_css_contains_action_button_styles(self) -> None:
        """The CSS must contain styling for action buttons."""
        from server.app import _render_index
        collection = {"cards": {}, "source": "test", "diagnostics": {"warnings": []}}
        decks = {"decks": []}
        config = LLMConfig()
        html_output = _render_index(collection, None, None, None, decks, config)
        assert ".action-btn" in html_output
        assert ".action-btn.export" in html_output
        assert ".action-btn.analyze" in html_output
        assert ".action-btn.improve" in html_output
        assert ".deck-detail-actions" in html_output


# ---------------------------------------------------------------------------
# Full E2E: HTML → JS → Server round-trip
# ---------------------------------------------------------------------------

class TestQuickActionsE2E:
    """Full end-to-end tests: HTML has buttons → JS sends POST → server responds."""

    def test_e2e_export_via_http(self) -> None:
        """Full E2E: HTML has buttons → server returns Arena text."""
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
                # 1. Verify HTML contains buttons
                resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
                html_content = resp.read().decode("utf-8")
                assert 'id="btn-export-arena"' in html_content
                assert 'id="btn-analyze"' in html_content
                assert 'id="btn-improve"' in html_content

                # 2. Verify JS contains handlers
                resp_js = urllib.request.urlopen(f"http://127.0.0.1:{port}/dashboard.js", timeout=5)
                js_content = resp_js.read().decode("utf-8")
                assert "/api/advisor/export" in js_content
                assert "/api/advisor/analyze" in js_content
                assert "/api/advisor/improve" in js_content

                # 3. POST to export endpoint
                status, data = _post_json(port, "/api/advisor/export", {
                    "deckId": "test-deck-001",
                })
                assert status == 200
                assert data["schema"] == "advisor-export.v1"
                assert "Lightning Strike" in data["arenaText"]
            finally:
                server.shutdown()