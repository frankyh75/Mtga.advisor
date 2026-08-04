"""End-to-End Regressionstest für das Chat-Panel.

Startet einen echten HTTP-Server mit realen decks.json/collection.json
Fixtures und testet den vollständigen Chat-Flow:
  - Dashboard laden (GET /)
  - Decks abrufen (GET /api/decks)
  - Einzelnes Deck abrufen (GET /api/deck/<id>)
  - Deck-Karten abrufen (GET /api/deck-cards?deck_id=...)
  - Chat-Anfrage senden (POST /api/chat) mit gemocktem LLM
  - Fehlerfälle: fehlende Frage, unbekanntes Deck, fehlende decks.json
  - LLM-Fehlerbehandlung (HTTP-Fehler, Timeout)
  - Basic-Auth-Interaktion mit Chat-Endpunkt

Alle LLM-Aufrufe werden gemockt — es wird kein echter LLM-Endpoint kontaktiert.
"""

from __future__ import annotations

import base64
import json
import threading
import time
import tempfile
import urllib.request
import urllib.error
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Any

import pytest

import sys
sys.path.append(str(Path(__file__).resolve().parents[2]))

from server.app import (  # noqa: E402
    MtgaAdvisorServer,
    MtgaAdvisorHandler,
    configure_basic_auth,
    _build_chat_prompt,
    _call_llm_chat,
    _SCRYFALL_IMAGE_CACHE,
)


# ---------------------------------------------------------------------------
# Fixture data — realistic structures matching real out/ artifacts
# ---------------------------------------------------------------------------

REAL_COLLECTION = {
    "schema": "collection.v1",
    "source": "memory-scan",
    "cards": {
        "67362": 4,
        "67992": 4,
        "69551": 4,
        "69914": 4,
        "70057": 4,
        "70144": 4,
        "70145": 4,
        "71031": 4,
        "71219": 4,
        "71233": 4,
        "71337": 4,
        "71378": 4,
        "71408": 4,
        "71428": 4,
        "71609": 4,
        "71725": 4,
        "71914": 4,
        "71936": 4,
        "71985": 4,
        "72036": 4,
        "72180": 4,
        "72267": 4,
        "72331": 4,
        "72421": 4,
        "72593": 4,
        "72634": 4,
        "72680": 4,
        "72717": 4,
        "72814": 4,
        "72856": 4,
    },
    "wildcards": {
        "wcCommon": 12,
        "wcUncommon": 8,
        "wcRare": 2,
        "wcMythic": 1,
    },
    "diagnostics": {
        "completeness": {
            "cards": "complete",
            "source": "complete",
            "wildcards": "complete",
        },
        "warnings": [],
        "evidence": ["memory-scan"],
    },
}

REAL_DECKS = {
    "schema": "decks.v1",
    "decks": [
        {
            "deckId": "deck-001",
            "deckKey": "deck-001",
            "name": "Mono-Red Aggro",
            "format": "standard",
            "colors": ["R"],
            "cardCount": 60,
            "isPrecon": False,
            "formatLegalities": {"standard": True, "historic": False},
            "cards": {
                "mainboard": [
                    {"cardId": 67362, "name": "Goblin Chainwhirler", "count": 4},
                    {"cardId": 67992, "name": "Shock", "count": 4},
                    {"cardId": 69551, "name": "Lightning Strike", "count": 4},
                    {"cardId": 69914, "name": "Viashino Pyromancer", "count": 4},
                    {"cardId": 70057, "name": "Ghitu Lavarunner", "count": 4},
                    {"cardId": 70144, "name": "Fanatical Firebrand", "count": 4},
                    {"cardId": 70145, "name": "Runaway Steam-Kin", "count": 4},
                    {"cardId": 71031, "name": "Goblin Instigator", "count": 4},
                    {"cardId": 71219, "name": "Risk Factor", "count": 4},
                    {"cardId": 71233, "name": "Experimental Frenzy", "count": 4},
                ],
                "sideboard": [
                    {"cardId": 71337, "name": "Fireblade Artist", "count": 2},
                    {"cardId": 71378, "name": "Lava Coil", "count": 3},
                ],
            },
        },
        {
            "deckId": "deck-002",
            "deckKey": "deck-002",
            "name": "Blue-White Control",
            "format": "standard",
            "colors": ["U", "W"],
            "cardCount": 60,
            "isPrecon": False,
            "formatLegalities": {"standard": True, "historic": True},
            "cards": {
                "mainboard": [
                    {"cardId": 71408, "name": "Teferi, Hero of Dominaria", "count": 4},
                    {"cardId": 71428, "name": "Settle the Wreckage", "count": 4},
                    {"cardId": 71609, "name": "Chemister's Insight", "count": 4},
                    {"cardId": 71725, "name": "Sinister Sabotage", "count": 4},
                ],
                "sideboard": [
                    {"cardId": 71914, "name": "Lyra Dawnbringer", "count": 2},
                ],
            },
        },
        {
            "deckId": "deck-003",
            "deckKey": "deck-003",
            "name": "Precon Starter Deck",
            "format": "standard",
            "colors": ["G"],
            "cardCount": 60,
            "isPrecon": True,
            "formatLegalities": {"standard": True},
            "cards": {
                "mainboard": [
                    {"cardId": 71936, "name": "Llanowar Elves", "count": 4},
                    {"cardId": 71985, "name": "Timber Gorge", "count": 4},
                ],
                "sideboard": [],
            },
        },
    ],
}

REAL_RUN_REPORT = {
    "diagnostics": {
        "completeness": {
            "cards": "complete",
            "source": "complete",
            "wildcards": "unknown",
        },
        "evidence": ["memory-scan"],
        "warnings": [],
    },
}

REAL_ADVISOR_RESULT = {
    "summary": {
        "topPriority": "Craft Lightning Strike",
        "completionScore": 75.0,
        "missingCards": 2,
        "hardCraftAdviceAllowed": False,
    },
    "recommendations": [
        {
            "name": "Lightning Strike",
            "needed": 4,
            "owned": 2,
            "rarity": "common",
            "reasons": ["missing-copies", "mainboard"],
        },
    ],
    "warnings": ["wildcards-unknown"],
    "craftingPriorities": [
        {
            "reason": "Missing core cards for Mono-Red Aggro",
            "cards": [
                {"name": "Lightning Strike", "count": 2, "rarity": "common", "forDecks": ["Mono-Red Aggro"]},
            ],
        },
    ],
    "deckOptimizations": [
        {
            "deckName": "Mono-Red Aggro",
            "format": "standard",
            "issues": ["Too few lands"],
            "suggestions": ["Add 2 more mountains"],
            "craftingNeeded": "0 wildcards",
        },
    ],
    "metaNotes": ["Aggro decks dominate the current meta."],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_fixtures(output_dir: Path) -> None:
    """Write all fixture JSON files into output_dir."""
    (output_dir / "collection.json").write_text(
        json.dumps(REAL_COLLECTION, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "decks.json").write_text(
        json.dumps(REAL_DECKS, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "run-report.json").write_text(
        json.dumps(REAL_RUN_REPORT, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "advisor-result.json").write_text(
        json.dumps(REAL_ADVISOR_RESULT, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "arena_deck.json").write_text(
        json.dumps(REAL_DECKS["decks"][0], ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _start_server(output_dir: Path, port: int = 0, *, reset_auth: bool = True) -> tuple[MtgaAdvisorServer, int, threading.Thread]:
    """Start a test server on a random port. Returns (server, port, thread).

    If reset_auth is True (default), auth is disabled before starting.
    Set reset_auth=False for auth tests that configure auth themselves.
    """
    if reset_auth:
        configure_basic_auth(None)
    server = MtgaAdvisorServer(("127.0.0.1", port), MtgaAdvisorHandler, output_dir)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.3)
    return server, actual_port, thread


def _stop_server(server: MtgaAdvisorServer) -> None:
    server.shutdown()
    server.server_close()


def _get(url: str, headers: dict[str, str] | None = None, timeout: int = 5) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(url: str, body: dict[str, Any], headers: dict[str, str] | None = None, timeout: int = 5) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8")
    all_headers = {"Content-Type": "application/json"}
    if headers:
        all_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=all_headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ---------------------------------------------------------------------------
# Mock LLM responses
# ---------------------------------------------------------------------------

def _mock_llm_ok(content: str = "Das Deck ist solide. Füge mehr Burn-Spells hinzu.") -> dict[str, Any]:
    """Return a successful LLM result dict (as _call_llm_chat would)."""
    return {"response": content, "model": "test-model"}


def _mock_llm_error(error: str) -> dict[str, Any]:
    """Return an error LLM result dict."""
    return {"error": error}


def _patch_llm_ok(monkeypatch, content: str = "Antwort.", capture: list | None = None) -> None:
    """Patch _call_llm_chat to return a successful response.

    If capture is provided, the prompt string is appended to it.
    """
    def fake_call(config, prompt):
        if capture is not None:
            capture.append(prompt)
        return _mock_llm_ok(content)

    monkeypatch.setattr("server.app._call_llm_chat", fake_call)


def _patch_llm_error(monkeypatch, error: str, capture: list | None = None) -> None:
    """Patch _call_llm_chat to return an error response."""
    def fake_call(config, prompt):
        if capture is not None:
            capture.append(prompt)
        return _mock_llm_error(error)

    monkeypatch.setattr("server.app._call_llm_chat", fake_call)


# ---------------------------------------------------------------------------
# E2E Tests: Full chat panel flow
# ---------------------------------------------------------------------------

class TestChatPanelE2E:
    """End-to-end tests for the chat panel with a live server."""

    def test_dashboard_loads_with_real_data(self, tmp_path: Path) -> None:
        """GET / returns the dashboard HTML with decks table and chat panel."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _get(f"http://127.0.0.1:{port}/")
            assert status == 200
            html = body.decode("utf-8")
            # Chat panel elements
            assert "chat-panel" in html
            assert "chat-messages" in html
            assert "chat-input" in html
            assert "chat-send" in html
            # Decks rendered
            assert "Mono-Red Aggro" in html
            assert "Blue-White Control" in html
            assert "Precon Starter Deck" in html
            # Dashboard JS loaded
            assert "/dashboard.js" in html
            # Decks JSON embedded
            assert "decks-json" in html
        finally:
            _stop_server(server)

    def test_api_decks_returns_full_json(self, tmp_path: Path) -> None:
        """GET /api/decks returns the complete decks.json with all 3 decks."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _get(f"http://127.0.0.1:{port}/api/decks")
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert data["schema"] == "decks.v1"
            assert len(data["decks"]) == 3
            assert data["decks"][0]["name"] == "Mono-Red Aggro"
            assert data["decks"][1]["name"] == "Blue-White Control"
            assert data["decks"][2]["isPrecon"] is True
        finally:
            _stop_server(server)

    def test_api_collection_returns_full_json(self, tmp_path: Path) -> None:
        """GET /api/collection returns the complete collection.json."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _get(f"http://127.0.0.1:{port}/api/collection")
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "cards" in data
            assert "wildcards" in data
            assert data["schema"] == "collection.v1"
            assert data["cards"]["67362"] == 4
            assert data["wildcards"]["wcCommon"] == 12
        finally:
            _stop_server(server)

    def test_api_deck_by_id_returns_deck(self, tmp_path: Path) -> None:
        """GET /api/deck/deck-001 returns the first deck with cards."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _get(f"http://127.0.0.1:{port}/api/deck/deck-001")
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert data["name"] == "Mono-Red Aggro"
            assert "cards" in data
            assert "mainboard" in data["cards"]
        finally:
            _stop_server(server)

    def test_api_deck_cards_returns_extracted_cards(self, tmp_path: Path) -> None:
        """GET /api/deck-cards?deck_id=deck-001 returns normalized card piles."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _get(f"http://127.0.0.1:{port}/api/deck-cards?deck_id=deck-001")
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "mainboard" in data
            assert "sideboard" in data
            assert len(data["mainboard"]) == 10
            assert data["mainboard"][0]["name"] == "Goblin Chainwhirler"
            assert data["mainboard"][0]["count"] == 4
            assert len(data["sideboard"]) == 2
            assert data["totalCards"] > 0
        finally:
            _stop_server(server)

    def test_chat_post_with_mocked_llm_returns_response(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat with mocked LLM returns a successful chat response."""
        _write_fixtures(tmp_path)

        _patch_llm_ok(monkeypatch, content="Goblin Chainwhirler ist stark im Aggro-Matchup.")

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Was hält ihr von Goblin Chainwhirler?"},
            )
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "response" in data
            assert "Goblin Chainwhirler" in data["response"]
            assert data.get("model") is not None
            assert "error" not in data
        finally:
            _stop_server(server)

    def test_chat_post_includes_collection_context(self, tmp_path: Path, monkeypatch) -> None:
        """The chat prompt should include collection data (wildcards, card counts)."""
        _write_fixtures(tmp_path)

        captured: list[str] = []
        _patch_llm_ok(monkeypatch, content="Ok.", capture=captured)

        server, port, thread = _start_server(tmp_path)
        try:
            _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Welche Karten fehlen mir?"},
            )
            assert len(captured) == 1
            prompt = captured[0]
            # Deck info in prompt
            assert "Mono-Red Aggro" in prompt
            assert "Goblin Chainwhirler" in prompt
            assert "Shock" in prompt
            assert "Sideboard" in prompt
            # Collection info in prompt
            assert "Collection" in prompt
            assert "Wildcards" in prompt
            assert "Common" in prompt
            # Question
            assert "Welche Karten fehlen mir?" in prompt
            assert "Deutsch" in prompt
        finally:
            _stop_server(server)

    def test_chat_post_for_second_deck(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat for deck-002 (Blue-White Control) works correctly."""
        _write_fixtures(tmp_path)

        _patch_llm_ok(monkeypatch, content="Teferi ist der Schlüssel.")

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-002", "question": "Wie spiele ich Control?"},
            )
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "response" in data
            assert "Teferi" in data["response"]
        finally:
            _stop_server(server)

    def test_chat_post_for_precon_deck(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat for a precon deck (deck-003) works correctly."""
        _write_fixtures(tmp_path)

        _patch_llm_ok(monkeypatch, content="Precon-Decks sind limitiert.")

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-003", "question": "Was kann ich verbessern?"},
            )
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "response" in data
        finally:
            _stop_server(server)

    def test_chat_post_by_deck_name(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat using deck name as deck_id also resolves correctly."""
        _write_fixtures(tmp_path)

        _patch_llm_ok(monkeypatch, content="Gute Wahl.")

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "Mono-Red Aggro", "question": "Beste Strategie?"},
            )
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "response" in data
        finally:
            _stop_server(server)

    def test_chat_post_by_slug_name(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat using slugified deck name as deck_id also resolves."""
        _write_fixtures(tmp_path)

        _patch_llm_ok(monkeypatch, content="Aggro.")

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "mono-red-aggro", "question": "Tipp?"},
            )
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "response" in data
        finally:
            _stop_server(server)


# ---------------------------------------------------------------------------
# E2E Error cases
# ---------------------------------------------------------------------------

class TestChatPanelErrors:
    """Error handling in the chat panel flow."""

    def test_chat_post_empty_question_returns_400(self, tmp_path: Path) -> None:
        """POST /api/chat with empty question returns 400."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": ""},
            )
            assert status == 400
            data = json.loads(body.decode("utf-8"))
            assert "error" in data
        finally:
            _stop_server(server)

    def test_chat_post_whitespace_question_returns_400(self, tmp_path: Path) -> None:
        """POST /api/chat with whitespace-only question returns 400."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "   "},
            )
            assert status == 400
        finally:
            _stop_server(server)

    def test_chat_post_missing_question_key_returns_400(self, tmp_path: Path) -> None:
        """POST /api/chat without question key returns 400."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001"},
            )
            assert status == 400
        finally:
            _stop_server(server)

    def test_chat_post_unknown_deck_returns_404(self, tmp_path: Path) -> None:
        """POST /api/chat with unknown deck_id returns 404."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "nonexistent-deck", "question": "Was?"},
            )
            assert status == 404
            data = json.loads(body.decode("utf-8"))
            assert "error" in data
            assert "nicht gefunden" in data.get("error", "")
        finally:
            _stop_server(server)

    def test_chat_post_missing_decks_json_returns_404(self, tmp_path: Path) -> None:
        """POST /api/chat when decks.json is missing returns 404."""
        # Write only collection, no decks.json
        (tmp_path / "collection.json").write_text(
            json.dumps(REAL_COLLECTION), encoding="utf-8"
        )
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Was?"},
            )
            assert status == 404
            data = json.loads(body.decode("utf-8"))
            assert "decks.json" in data.get("error", "")
        finally:
            _stop_server(server)

    def test_chat_post_invalid_json_returns_400(self, tmp_path: Path) -> None:
        """POST /api/chat with invalid JSON body returns 400."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/chat",
                data=b"not valid json{{{",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as e:
                assert e.code == 400
                data = json.loads(e.read().decode("utf-8"))
                assert "invalid" in data.get("error", "").lower() or "error" in data
            else:
                pytest.fail("Should have raised HTTPError 400")
        finally:
            _stop_server(server)


# ---------------------------------------------------------------------------
# E2E LLM error handling
# ---------------------------------------------------------------------------

class TestChatPanelLLMErrors:
    """LLM error handling in the chat flow."""

    def test_chat_post_llm_http_error_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat when LLM returns HTTP 500 returns error in response."""
        _write_fixtures(tmp_path)

        _patch_llm_error(monkeypatch, "HTTP 500: Internal Server Error")

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Was?"},
            )
            # The endpoint returns 200 with an error dict, not an HTTP error
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "error" in data
            assert "HTTP 500" in data["error"]
        finally:
            _stop_server(server)

    def test_chat_post_llm_url_error_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat when LLM is unreachable returns error."""
        _write_fixtures(tmp_path)

        _patch_llm_error(monkeypatch, "LLM nicht erreichbar: Connection refused")

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Was?"},
            )
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "error" in data
            assert "nicht erreichbar" in data["error"]
        finally:
            _stop_server(server)

    def test_chat_post_llm_empty_response_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat when LLM returns empty content returns error."""
        _write_fixtures(tmp_path)

        _patch_llm_error(monkeypatch, "LLM gab leere Antwort")

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Was?"},
            )
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "error" in data
            assert "leere" in data["error"].lower()
        finally:
            _stop_server(server)

    def test_chat_post_llm_no_choices_returns_error(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat when LLM returns no choices returns error."""
        _write_fixtures(tmp_path)

        _patch_llm_error(monkeypatch, "LLM gab keine Choices zurück")

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Was?"},
            )
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "error" in data
            assert "Choices" in data["error"]
        finally:
            _stop_server(server)


# ---------------------------------------------------------------------------
# E2E: Full chat panel workflow (multi-step session)
# ---------------------------------------------------------------------------

class TestChatPanelWorkflow:
    """Multi-step workflows that exercise the full chat panel lifecycle."""

    def test_full_workflow_load_dashboard_select_deck_chat(self, tmp_path: Path, monkeypatch) -> None:
        """Simulates a user: load dashboard → fetch decks → select deck → chat."""
        _write_fixtures(tmp_path)

        _patch_llm_ok(monkeypatch, content="Dein Deck hat 10 Mainboard-Karten. Empfehle mehr Lands.")

        server, port, thread = _start_server(tmp_path)
        try:
            base = f"http://127.0.0.1:{port}"

            # Step 1: Load dashboard
            status, body = _get(f"{base}/")
            assert status == 200
            assert "chat-panel" in body.decode("utf-8")

            # Step 2: Fetch decks JSON (as dashboard.js would)
            status, body = _get(f"{base}/api/decks")
            assert status == 200
            decks_data = json.loads(body.decode("utf-8"))
            assert len(decks_data["decks"]) == 3

            # Step 3: Select first deck — fetch its details
            deck_id = decks_data["decks"][0]["deckId"]
            status, body = _get(f"{base}/api/deck/{deck_id}")
            assert status == 200
            deck_data = json.loads(body.decode("utf-8"))
            assert deck_data["name"] == "Mono-Red Aggro"

            # Step 4: Fetch deck cards (for chat panel display)
            status, body = _get(f"{base}/api/deck-cards?deck_id={deck_id}")
            assert status == 200
            cards_data = json.loads(body.decode("utf-8"))
            assert len(cards_data["mainboard"]) == 10

            # Step 5: Send chat question
            status, body = _post(
                f"{base}/api/chat",
                {"deck_id": deck_id, "question": "Was soll ich craften?"},
            )
            assert status == 200
            chat_data = json.loads(body.decode("utf-8"))
            assert "response" in chat_data
            assert "Lands" in chat_data["response"] or "Karten" in chat_data["response"]

            # Step 6: Ask a follow-up question
            status, body = _post(
                f"{base}/api/chat",
                {"deck_id": deck_id, "question": "Welche Sideboard-Karten?"},
            )
            assert status == 200
            chat2 = json.loads(body.decode("utf-8"))
            assert "response" in chat2
        finally:
            _stop_server(server)

    def test_workflow_chat_with_all_decks(self, tmp_path: Path, monkeypatch) -> None:
        """Chat with each deck in sequence — all should succeed."""
        _write_fixtures(tmp_path)

        _patch_llm_ok(monkeypatch, content="Antwort.")

        server, port, thread = _start_server(tmp_path)
        try:
            base = f"http://127.0.0.1:{port}"

            # Get decks
            _, body = _get(f"{base}/api/decks")
            decks_data = json.loads(body.decode("utf-8"))

            # Chat with each deck
            for deck in decks_data["decks"]:
                status, resp_body = _post(
                    f"{base}/api/chat",
                    {"deck_id": deck["deckId"], "question": "Analyse?"},
                )
                assert status == 200
                data = json.loads(resp_body.decode("utf-8"))
                assert "response" in data
                assert "error" not in data
        finally:
            _stop_server(server)

    def test_workflow_collection_context_in_prompt(self, tmp_path: Path, monkeypatch) -> None:
        """Verify that the chat prompt contains real collection data from collection.json."""
        _write_fixtures(tmp_path)

        captured: list[str] = []
        _patch_llm_ok(monkeypatch, content="Ok.", capture=captured)

        server, port, thread = _start_server(tmp_path)
        try:
            _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Habe ich genug Wildcards?"},
            )
            assert len(captured) == 1
            prompt = captured[0]
            # Collection has 30 unique cards, 120 total
            assert "30 unique" in prompt
            # Wildcards from collection
            assert "12" in prompt  # wcCommon value
            assert "Rare" in prompt or "rare" in prompt
        finally:
            _stop_server(server)

    def test_workflow_chat_without_collection(self, tmp_path: Path, monkeypatch) -> None:
        """Chat should still work when collection.json is missing."""
        # Write decks only, no collection
        (tmp_path / "decks.json").write_text(
            json.dumps(REAL_DECKS), encoding="utf-8"
        )

        captured: list[str] = []
        _patch_llm_ok(monkeypatch, content="Ok.", capture=captured)

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Analyse?"},
            )
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "response" in data
            # Prompt should still have deck info but no collection section
            assert len(captured) == 1
            prompt = captured[0]
            assert "Mono-Red Aggro" in prompt
            assert "Goblin Chainwhirler" in prompt
        finally:
            _stop_server(server)


# ---------------------------------------------------------------------------
# E2E: Chat with config overrides
# ---------------------------------------------------------------------------

class TestChatPanelConfigOverrides:
    """Test that CLI config overrides from the chat request are applied."""

    def test_chat_post_with_config_overrides(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat with endpoint/model/temperature overrides uses them."""
        _write_fixtures(tmp_path)

        captured_configs: list = []

        def fake_call(config, prompt):
            captured_configs.append(config)
            return _mock_llm_ok("Ok.")

        monkeypatch.setattr("server.app._call_llm_chat", fake_call)

        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {
                    "deck_id": "deck-001",
                    "question": "Was?",
                    "temperature": 0.5,
                    "max_tokens": 256,
                },
            )
            assert status == 200
            assert len(captured_configs) == 1
            config = captured_configs[0]
            assert config.temperature == 0.5
            assert config.max_tokens == 256
        finally:
            _stop_server(server)


# ---------------------------------------------------------------------------
# E2E: Auth interaction with chat
# ---------------------------------------------------------------------------

class TestChatPanelAuth:
    """Test Basic-Auth interaction with the chat endpoint."""

    def test_chat_blocked_by_auth(self, tmp_path: Path) -> None:
        """POST /api/chat returns 401 when auth is enabled and no credentials provided."""
        _write_fixtures(tmp_path)
        configure_basic_auth("admin:secret")
        server, port, thread = _start_server(tmp_path, reset_auth=False)
        try:
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Was?"},
            )
            assert status == 401
        finally:
            _stop_server(server)
            configure_basic_auth(None)

    def test_chat_with_valid_auth(self, tmp_path: Path, monkeypatch) -> None:
        """POST /api/chat with valid auth credentials succeeds."""
        _write_fixtures(tmp_path)

        _patch_llm_ok(monkeypatch, content="Ok.")

        configure_basic_auth("admin:secret")
        server, port, thread = _start_server(tmp_path, reset_auth=False)
        try:
            valid_b64 = base64.b64encode(b"admin:secret").decode("ascii")
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Was?"},
                headers={"Authorization": f"Basic {valid_b64}"},
            )
            assert status == 200
            data = json.loads(body.decode("utf-8"))
            assert "response" in data
        finally:
            _stop_server(server)
            configure_basic_auth(None)

    def test_chat_with_wrong_auth(self, tmp_path: Path) -> None:
        """POST /api/chat with wrong auth credentials returns 401."""
        _write_fixtures(tmp_path)
        configure_basic_auth("admin:secret")
        server, port, thread = _start_server(tmp_path, reset_auth=False)
        try:
            wrong_b64 = base64.b64encode(b"admin:wrong").decode("ascii")
            status, body = _post(
                f"http://127.0.0.1:{port}/api/chat",
                {"deck_id": "deck-001", "question": "Was?"},
                headers={"Authorization": f"Basic {wrong_b64}"},
            )
            assert status == 401
        finally:
            _stop_server(server)
            configure_basic_auth(None)


# ---------------------------------------------------------------------------
# E2E: Dashboard.js integration
# ---------------------------------------------------------------------------

class TestChatPanelDashboardJS:
    """Verify dashboard.js (the chat panel frontend) is served correctly."""

    def test_dashboard_js_served(self, tmp_path: Path) -> None:
        """GET /dashboard.js returns the JS with chat panel logic."""
        _write_fixtures(tmp_path)
        server, port, thread = _start_server(tmp_path)
        try:
            status, body = _get(f"http://127.0.0.1:{port}/dashboard.js")
            assert status == 200
            js = body.decode("utf-8")
            assert "fetch" in js
            assert "/api/chat" in js
            assert "chat-panel" in js or "chatPanel" in js
        finally:
            _stop_server(server)


# ---------------------------------------------------------------------------
# Unit-level: _build_chat_prompt with real data
# ---------------------------------------------------------------------------

class TestBuildChatPrompt:
    """Unit tests for _build_chat_prompt with real deck/collection data."""

    def test_prompt_contains_deck_name_and_format(self) -> None:
        deck = REAL_DECKS["decks"][0]
        prompt = _build_chat_prompt(deck, REAL_COLLECTION, "Was fehlt?")
        assert "Mono-Red Aggro" in prompt
        # _build_chat_prompt uses deck["attributes"]["Format"] which may be absent
        assert "Format:" in prompt

    def test_prompt_contains_all_mainboard_cards(self) -> None:
        deck = REAL_DECKS["decks"][0]
        prompt = _build_chat_prompt(deck, REAL_COLLECTION, "Analyse?")
        for card in deck["cards"]["mainboard"]:
            assert card["name"] in prompt

    def test_prompt_contains_sideboard_section(self) -> None:
        deck = REAL_DECKS["decks"][0]
        prompt = _build_chat_prompt(deck, REAL_COLLECTION, "Sideboard?")
        assert "Sideboard" in prompt
        for card in deck["cards"]["sideboard"]:
            assert card["name"] in prompt

    def test_prompt_contains_wildcards(self) -> None:
        deck = REAL_DECKS["decks"][0]
        prompt = _build_chat_prompt(deck, REAL_COLLECTION, "Wildcards?")
        assert "Wildcards" in prompt
        assert "Common" in prompt
        assert "Mythic" in prompt

    def test_prompt_without_collection(self) -> None:
        deck = REAL_DECKS["decks"][0]
        prompt = _build_chat_prompt(deck, None, "Was?")
        assert "Mono-Red Aggro" in prompt
        assert "Goblin Chainwhirler" in prompt
        # Should not crash, just no "## Collection" section header
        assert "## Collection" not in prompt

    def test_prompt_contains_question(self) -> None:
        deck = REAL_DECKS["decks"][0]
        prompt = _build_chat_prompt(deck, REAL_COLLECTION, "Soll ich Shock spielen?")
        assert "Soll ich Shock spielen?" in prompt

    def test_prompt_instructs_german(self) -> None:
        deck = REAL_DECKS["decks"][0]
        prompt = _build_chat_prompt(deck, REAL_COLLECTION, "Was?")
        assert "Deutsch" in prompt

    def test_prompt_for_control_deck(self) -> None:
        deck = REAL_DECKS["decks"][1]
        prompt = _build_chat_prompt(deck, REAL_COLLECTION, "Control-Strategie?")
        assert "Blue-White Control" in prompt
        assert "Teferi" in prompt

    def test_prompt_for_precon_deck(self) -> None:
        deck = REAL_DECKS["decks"][2]
        prompt = _build_chat_prompt(deck, REAL_COLLECTION, "Verbessern?")
        assert "Precon Starter Deck" in prompt
        assert "Llanowar Elves" in prompt


# ---------------------------------------------------------------------------
# Unit-level: _call_llm_chat error handling
# ---------------------------------------------------------------------------

class TestCallLlmChat:
    """Unit tests for _call_llm_chat with mocked HTTP."""

    def test_call_llm_chat_success(self, monkeypatch) -> None:
        from advisor.llm_config import LLMConfig
        config = LLMConfig()

        class FakeResp:
            def __init__(self, data):
                self._data = data
            def read(self):
                return self._data
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        def fake_urlopen(req, timeout=None):
            return FakeResp(json.dumps({
                "choices": [{"message": {"content": "Test-Antwort."}}],
                "model": "test-model",
            }).encode("utf-8"))

        monkeypatch.setattr("server.app.urllib.request.urlopen", fake_urlopen)
        result = _call_llm_chat(config, "Test prompt")
        assert "response" in result
        assert result["response"] == "Test-Antwort."
        assert "model" in result

    def test_call_llm_chat_http_error(self, monkeypatch) -> None:
        from advisor.llm_config import LLMConfig
        config = LLMConfig()

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 503, "Service Unavailable",
                Message(), BytesIO(b"unavailable"),
            )

        monkeypatch.setattr("server.app.urllib.request.urlopen", fake_urlopen)
        result = _call_llm_chat(config, "Test")
        assert "error" in result
        assert "503" in result["error"]

    def test_call_llm_chat_url_error(self, monkeypatch) -> None:
        from advisor.llm_config import LLMConfig
        config = LLMConfig()

        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError("Connection refused")

        monkeypatch.setattr("server.app.urllib.request.urlopen", fake_urlopen)
        result = _call_llm_chat(config, "Test")
        assert "error" in result
        assert "nicht erreichbar" in result["error"]