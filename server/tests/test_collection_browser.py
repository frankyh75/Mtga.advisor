"""Tests für T6 (/api/collection/enriched Endpoint) und T9 (Collection-Browser).

Testet:
  - /api/collection/enriched Endpoint mit echtem HTTP-Server
  - Enriched Collection-Datenstruktur (Karten mit Metadaten)
  - Fallback-Verhalten bei fehlender collection.json
  - Collection-Browser HTML-Rendering im Dashboard
  - dashboard.js enthält Collection-Browser JS-Logik
  - card_database.py enriched functions

Alle Scryfall-Aufrufe werden gemockt — es wird kein echter API-Endpoint kontaktiert.
"""

from __future__ import annotations

import json
import threading
import time
import tempfile
import urllib.request
import urllib.error
from io import BytesIO
from email.message import Message
from pathlib import Path
from typing import Any

import pytest

import sys
sys.path.append(str(Path(__file__).resolve().parents[2]))

from server.app import (  # noqa: E402
    MtgaAdvisorServer,
    MtgaAdvisorHandler,
    configure_basic_auth,
    _build_enriched_collection,
    _render_index,
    _dashboard_js,
    _ENRICHED_CARD_DB,
)
import server.app as app_module
from advisor.llm_config import LLMConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_ENRICHED_DB = {
    100458: {
        "name": "Action News Crew",
        "set": "TMT",
        "collector_number": "1",
        "rarity": "common",
        "cmc": 2,
        "type_line": "Creature — Human Reporter",
        "colors": ["W"],
        "oracle_text": "When this creature enters the battlefield, investigate.",
        "image_uri": "https://cards.scryfall.io/normal/front/test1.jpg",
    },
    100460: {
        "name": "April O'Neil, Kunoichi Trainee",
        "set": "TMT",
        "collector_number": "3",
        "rarity": "rare",
        "cmc": 3,
        "type_line": "Legendary Creature — Human Ninja",
        "colors": ["U", "B"],
        "oracle_text": "Ninjutsu {U}{B}. Whenever April deals combat damage, draw a card.",
        "image_uri": "https://cards.scryfall.io/normal/front/test2.jpg",
    },
    100462: {
        "name": "East Wind Avatar",
        "set": "TMT",
        "collector_number": "5",
        "rarity": "mythic",
        "cmc": 5,
        "type_line": "Creature — Avatar",
        "colors": ["G"],
        "oracle_text": "Trample. When this enters, creatures you control get +2/+2.",
        "image_uri": "https://cards.scryfall.io/normal/front/test3.jpg",
    },
}

MOCK_COLLECTION = {
    "source": "memory-scan",
    "cards": {
        "100458": 3,
        "100460": 2,
        "100462": 2,
    },
    "wildcards": {"common": 10, "uncommon": 5, "rare": 3, "mythic": 1},
    "diagnostics": {"completeness": {"cards": "complete"}, "warnings": []},
}


def _make_handler_request(method: str, path: str, output_dir: Path) -> urllib.request.Request:
    """Build a request for the test server."""
    return urllib.request.Request(f"http://127.0.0.1:0{path}", method=method)


def _read_response(resp) -> dict[str, Any]:
    """Read and parse a JSON response."""
    data = resp.read().decode("utf-8")
    return json.loads(data)


# ---------------------------------------------------------------------------
# Unit tests: _build_enriched_collection
# ---------------------------------------------------------------------------

class TestBuildEnrichedCollection:
    """Unit tests for the _build_enriched_collection function."""

    def test_build_with_enriched_db(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that enriched DB data is joined correctly with collection."""
        # Write collection.json
        (tmp_path / "collection.json").write_text(json.dumps(MOCK_COLLECTION), encoding="utf-8")

        # Mock the enriched DB
        monkeypatch.setattr(app_module, "_ENRICHED_CARD_DB", MOCK_ENRICHED_DB)

        result = _build_enriched_collection(tmp_path)

        assert "error" not in result
        assert result["total_unique"] == 3
        assert result["total_copies"] == 7  # 3+2+2
        assert result["enriched_count"] == 3
        assert result["source"] == "scryfall"

        # Check cards are sorted by name
        names = [c["name"] for c in result["cards"]]
        assert names == sorted(names)

        # Check a specific card
        card = next(c for c in result["cards"] if c["grp_id"] == 100458)
        assert card["name"] == "Action News Crew"
        assert card["count"] == 3
        assert card["rarity"] == "common"
        assert card["cmc"] == 2
        assert card["type_line"] == "Creature — Human Reporter"
        assert card["colors"] == ["W"]
        assert card["image_uri"] is not None

    def test_build_missing_collection(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that missing collection.json returns an error."""
        monkeypatch.setattr(app_module, "_ENRICHED_CARD_DB", MOCK_ENRICHED_DB)
        result = _build_enriched_collection(tmp_path)
        assert "error" in result
        assert result["error"] == "not_found"

    def test_build_with_unknown_grp_id(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that unknown grp_ids get fallback card info."""
        collection = {
            "cards": {"100458": 1, "999999": 2},
            "diagnostics": {"completeness": {}, "warnings": []},
        }
        (tmp_path / "collection.json").write_text(json.dumps(collection), encoding="utf-8")
        monkeypatch.setattr(app_module, "_ENRICHED_CARD_DB", MOCK_ENRICHED_DB)

        result = _build_enriched_collection(tmp_path)

        assert result["total_unique"] == 2
        unknown_card = next(c for c in result["cards"] if c["grp_id"] == 999999)
        assert "999999" in unknown_card["name"]
        assert unknown_card["rarity"] == "unknown"
        assert unknown_card["image_uri"] is None

    def test_build_empty_collection(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test with empty collection cards."""
        collection = {"cards": {}, "diagnostics": {"completeness": {}, "warnings": []}}
        (tmp_path / "collection.json").write_text(json.dumps(collection), encoding="utf-8")
        monkeypatch.setattr(app_module, "_ENRICHED_CARD_DB", MOCK_ENRICHED_DB)

        result = _build_enriched_collection(tmp_path)

        assert result["total_unique"] == 0
        assert result["total_copies"] == 0
        assert result["cards"] == []

    def test_build_no_enriched_db_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test fallback to basic lookup when enriched DB is empty."""
        collection = {"cards": {"100458": 1}, "diagnostics": {"completeness": {}, "warnings": []}}
        (tmp_path / "collection.json").write_text(json.dumps(collection), encoding="utf-8")

        # Mock empty enriched DB
        monkeypatch.setattr(app_module, "_ENRICHED_CARD_DB", {})

        # Mock basic DB
        basic_db = {100458: {"name": "Action News Crew", "set": "TMT", "collector_number": "1"}}
        import scanner.card_database as card_db_module
        monkeypatch.setattr(card_db_module, "load_card_database", lambda **kw: basic_db)

        result = _build_enriched_collection(tmp_path)

        assert result["enriched_count"] == 0
        assert result["source"] == "basic"
        card = result["cards"][0]
        assert card["name"] == "Action News Crew"
        assert card["rarity"] == "unknown"

    def test_build_invalid_grp_id_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that invalid grp_ids are skipped gracefully."""
        collection = {"cards": {"100458": 1, "invalid": 2}, "diagnostics": {"completeness": {}, "warnings": []}}
        (tmp_path / "collection.json").write_text(json.dumps(collection), encoding="utf-8")
        monkeypatch.setattr(app_module, "_ENRICHED_CARD_DB", MOCK_ENRICHED_DB)

        result = _build_enriched_collection(tmp_path)

        # Only the valid grp_id should be included
        assert result["total_unique"] == 1
        assert result["cards"][0]["grp_id"] == 100458


# ---------------------------------------------------------------------------
# E2E tests: HTTP server endpoint
# ---------------------------------------------------------------------------

class TestEnrichedCollectionEndpoint:
    """E2E tests for GET /api/collection/enriched via real HTTP server."""

    @pytest.fixture
    def server_with_collection(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Start a test server with a mock collection and enriched DB."""
        (tmp_path / "collection.json").write_text(json.dumps(MOCK_COLLECTION), encoding="utf-8")
        # Need run-report.json for dashboard rendering
        (tmp_path / "run-report.json").write_text(
            json.dumps({"diagnostics": {"completeness": {}, "warnings": []}}), encoding="utf-8"
        )
        # Need decks.json for dashboard rendering
        (tmp_path / "decks.json").write_text(
            json.dumps({"schema": "decks.v1", "decks": []}), encoding="utf-8"
        )

        # Mock the enriched DB BEFORE server starts
        monkeypatch.setattr(app_module, "_ENRICHED_CARD_DB", MOCK_ENRICHED_DB)

        configure_basic_auth(None)
        server = MtgaAdvisorServer(("127.0.0.1", 0), MtgaAdvisorHandler, tmp_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.3)

        actual_port = server.server_address[1]

        yield actual_port

        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    def test_endpoint_returns_enriched_data(self, server_with_collection: int) -> None:
        """Test that /api/collection/enriched returns enriched card data."""
        url = f"http://127.0.0.1:{server_with_collection}/api/collection/enriched"
        with urllib.request.urlopen(url, timeout=5) as resp:
            assert resp.status == 200
            data = _read_response(resp)

        assert "error" not in data
        assert data["total_unique"] == 3
        assert data["total_copies"] == 7
        assert data["enriched_count"] == 3
        assert data["source"] == "scryfall"
        assert len(data["cards"]) == 3

    def test_endpoint_card_has_all_fields(self, server_with_collection: int) -> None:
        """Test that each card in the response has all expected fields."""
        url = f"http://127.0.0.1:{server_with_collection}/api/collection/enriched"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = _read_response(resp)

        card = data["cards"][0]
        expected_fields = {
            "grp_id", "count", "name", "set", "collector_number",
            "rarity", "cmc", "type_line", "colors", "oracle_text", "image_uri",
        }
        assert expected_fields.issubset(card.keys())

    def test_endpoint_404_when_no_collection(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that endpoint returns 404 when collection.json is missing."""
        monkeypatch.setattr(app_module, "_ENRICHED_CARD_DB", MOCK_ENRICHED_DB)
        configure_basic_auth(None)
        server = MtgaAdvisorServer(("127.0.0.1", 0), MtgaAdvisorHandler, tmp_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.3)
        port = server.server_address[1]

        try:
            url = f"http://127.0.0.1:{port}/api/collection/enriched"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = _read_response(resp)
                assert "error" in data
                assert data["error"] == "not_found"
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


# ---------------------------------------------------------------------------
# Dashboard rendering tests (T9)
# ---------------------------------------------------------------------------

class TestCollectionBrowserRendering:
    """Tests that the Collection Browser HTML and JS are rendered in the dashboard."""

    def test_html_contains_collection_browser_section(self) -> None:
        """Test that _render_index includes the collection-browser-section."""
        collection = {
            "source": "test", "cards": {"100": 2},
            "diagnostics": {"completeness": {}, "warnings": []},
        }
        run_report = {"diagnostics": {"completeness": {}, "warnings": []}}
        deck = None
        advisor_result = None
        decks = {"schema": "decks.v1", "decks": []}
        llm_config = LLMConfig()

        html_output = _render_index(collection, run_report, deck, advisor_result, decks, llm_config)

        assert 'id="collection-browser-section"' in html_output
        assert 'id="cb-search"' in html_output
        assert 'id="cb-type"' in html_output
        assert 'id="cb-color"' in html_output
        assert 'id="cb-rarity"' in html_output
        assert 'id="cb-cmc"' in html_output
        assert 'id="cb-reset"' in html_output
        assert 'id="collection-browser-results"' in html_output

    def test_html_contains_css_styles(self) -> None:
        """Test that CSS styles for the collection browser are present."""
        collection = {
            "source": "test", "cards": {},
            "diagnostics": {"completeness": {}, "warnings": []},
        }
        run_report = {"diagnostics": {"completeness": {}, "warnings": []}}
        decks = {"schema": "decks.v1", "decks": []}
        llm_config = LLMConfig()

        html_output = _render_index(collection, run_report, None, None, decks, llm_config)

        assert ".collection-browser-controls" in html_output
        assert ".cb-card" in html_output
        assert ".cb-mana-W" in html_output
        assert ".cb-rarity-mythic" in html_output

    def test_dashboard_js_contains_browser_logic(self) -> None:
        """Test that dashboard.js contains the collection browser JavaScript."""
        js = _dashboard_js()

        assert "cbLoadData" in js
        assert "cbApplyFilter" in js
        assert "cbRenderResults" in js
        assert "cbResetFilters" in js
        assert "/api/collection/enriched" in js

    def test_dashboard_js_filter_logic(self) -> None:
        """Test that the JS filter logic handles all filter types."""
        js = _dashboard_js()

        # Name/text search
        assert "cbSearch" in js
        # Type filter
        assert "cbType" in js
        # Color filter
        assert "cbColor" in js
        # Rarity filter
        assert "cbRarity" in js
        # CMC filter
        assert "cbCmc" in js
        # Event listeners
        assert "addEventListener" in js
        assert "input" in js  # for search and cmc
        assert "change" in js  # for selects

    def test_dashboard_js_uses_safe_rendering(self) -> None:
        """Test that the JS uses safe DOM methods (createElement/textContent) not innerHTML."""
        js = _dashboard_js()

        # The collection browser section should use el() helper
        # which uses createElement/textContent, not innerHTML
        # Check that the cb section doesn't use innerHTML
        # Find the collection browser section in JS
        cb_start = js.find("Collection Browser")
        if cb_start == -1:
            cb_start = js.find("cbLoadData")
        cb_section = js[cb_start:] if cb_start >= 0 else ""

        # Should use el() helper for DOM construction
        assert "el(" in cb_section
        assert "appendChild" in cb_section
        # Should NOT use innerHTML in the collection browser section
        assert "innerHTML" not in cb_section


# ---------------------------------------------------------------------------
# Card database enriched functions tests
# ---------------------------------------------------------------------------

class TestEnrichedCardDatabase:
    """Tests for the enriched card database functions in card_database.py."""

    def test_add_enriched_scryfall_card(self) -> None:
        """Test that _add_enriched_scryfall_card extracts all fields correctly."""
        from scanner.card_database import _add_enriched_scryfall_card

        lookup: dict[int, dict[str, Any]] = {}
        scryfall_card = {
            "arena_id": 12345,
            "name": "Test Card",
            "set": "tst",
            "collector_number": "42",
            "rarity": "rare",
            "cmc": 3,
            "type_line": "Creature — Test",
            "colors": ["R", "G"],
            "oracle_text": "When this enters, do a thing.",
            "image_uris": {"normal": "https://example.com/test.jpg"},
        }

        _add_enriched_scryfall_card(lookup, scryfall_card)

        assert 12345 in lookup
        entry = lookup[12345]
        assert entry["name"] == "Test Card"
        assert entry["set"] == "TST"  # should be uppercased
        assert entry["rarity"] == "rare"
        assert entry["cmc"] == 3
        assert entry["colors"] == ["R", "G"]
        assert entry["image_uri"] == "https://example.com/test.jpg"

    def test_add_enriched_scryfall_card_double_faced(self) -> None:
        """Test that double-faced cards extract image and colors from faces."""
        from scanner.card_database import _add_enriched_scryfall_card

        lookup: dict[int, dict[str, Any]] = {}
        scryfall_card = {
            "arena_id": 67890,
            "name": "Double Face Card",
            "set": "tst",
            "collector_number": "99",
            "rarity": "mythic",
            "cmc": 4,
            "type_line": "",
            "colors": [],  # empty for DFCs
            "card_faces": [
                {
                    "name": "Front",
                    "colors": ["W"],
                    "image_uris": {"normal": "https://example.com/front.jpg"},
                },
                {
                    "name": "Back",
                    "colors": ["B"],
                    "image_uris": {"normal": "https://example.com/back.jpg"},
                },
            ],
        }

        _add_enriched_scryfall_card(lookup, scryfall_card)

        entry = lookup[67890]
        assert entry["image_uri"] == "https://example.com/front.jpg"
        assert set(entry["colors"]) == {"W", "B"}

    def test_add_enriched_scryfall_card_no_arena_id(self) -> None:
        """Test that cards without arena_id are skipped."""
        from scanner.card_database import _add_enriched_scryfall_card

        lookup: dict[int, dict[str, Any]] = {}
        card = {"name": "No Arena ID", "rarity": "common"}
        _add_enriched_scryfall_card(lookup, card)
        assert len(lookup) == 0

    def test_enriched_cache_roundtrip(self, tmp_path: Path) -> None:
        """Test that enriched cache write/read roundtrips correctly."""
        from scanner.card_database import _write_enriched_cache, _read_enriched_cache

        lookup = {
            123: {"name": "Test", "set": "TST", "rarity": "common", "cmc": 1, "colors": ["W"]},
        }
        cache_file = tmp_path / "enriched_cache.json"

        _write_enriched_cache(cache_file, lookup)
        result = _read_enriched_cache(cache_file)

        assert result is not None
        assert 123 in result
        assert result[123]["name"] == "Test"
        assert result[123]["rarity"] == "common"

    def test_enriched_cache_returns_none_on_missing(self, tmp_path: Path) -> None:
        """Test that _read_enriched_cache returns None for missing file."""
        from scanner.card_database import _read_enriched_cache

        result = _read_enriched_cache(tmp_path / "nonexistent.json")
        assert result is None

    def test_enriched_cache_returns_none_on_wrong_schema(self, tmp_path: Path) -> None:
        """Test that _read_enriched_cache returns None for wrong schema."""
        from scanner.card_database import _read_enriched_cache, ENRICHED_CACHE_SCHEMA

        wrong = {"schema": "old-schema", "cards": {}}
        cache_file = tmp_path / "wrong_cache.json"
        cache_file.write_text(json.dumps(wrong), encoding="utf-8")

        result = _read_enriched_cache(cache_file)
        assert result is None

    def test_rarity_map(self) -> None:
        """Test that the Scryfall rarity map covers all expected values."""
        from scanner.card_database import _SCRYFALL_RARITY_MAP

        assert _SCRYFALL_RARITY_MAP["common"] == "common"
        assert _SCRYFALL_RARITY_MAP["uncommon"] == "uncommon"
        assert _SCRYFALL_RARITY_MAP["rare"] == "rare"
        assert _SCRYFALL_RARITY_MAP["mythic"] == "mythic"
        assert _SCRYFALL_RARITY_MAP["bonus"] == "special"