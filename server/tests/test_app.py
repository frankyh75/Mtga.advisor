from __future__ import annotations

from pathlib import Path
from io import BytesIO
import sys
import json
import base64
import threading
import time
import urllib.request
import urllib.error
from email.message import Message

sys.path.append(str(Path(__file__).resolve().parents[2]))

from advisor.llm_config import LLMConfig  # noqa: E402
from server.app import (  # noqa: E402
    _dashboard_js,
    _render_index,
    _fetch_scryfall_image_url,
    _SCRYFALL_IMAGE_CACHE,
    DEFAULT_HOST,
    DEFAULT_PORT,
    MtgaAdvisorServer,
    MtgaAdvisorHandler,
    configure_basic_auth,
    _check_auth,
    _AUTH_ENABLED,
    _get_lan_ip,
    _generate_qr_ascii,
    _print_server_banner,
    _health_response,
)


def test_render_index_includes_collection_deck_and_advisor_summary() -> None:
    collection = {
        "source": "memory-scan",
        "cards": {"100": 2, "200": 1},
        "diagnostics": {"completeness": {"cards": "complete"}, "warnings": []},
    }
    run_report = {
        "diagnostics": {"completeness": {"cards": "complete"}, "warnings": []},
    }
    deck = {
        "name": "Test Deck",
        "format": "standard",
        "mainboard": [{"count": 4}],
        "sideboard": [{"count": 1}],
        "diagnostics": {"warnings": []},
    }
    advisor_result = {
        "summary": {
            "completionScore": 60.0,
            "missingCards": 2,
            "hardCraftAdviceAllowed": False,
        },
        "recommendations": [
            {
                "name": "Lightning Strike",
                "needed": 2,
                "owned": 2,
                "rarity": "common",
                "reasons": ["missing-copies", "mainboard"],
            }
        ],
        "warnings": ["wildcards-unknown"],
    }

    decks = {
        "schema": "decks.v1",
        "decks": [
            {
                "name": "Control",
                "deckId": "1",
                "deckKey": "1",
                "format": "standard",
                "colors": ["U"],
                "cardCount": 60,
                "isPrecon": False,
            }
        ],
    }

    llm_config = LLMConfig()
    html = _render_index(collection, run_report, deck, advisor_result, decks, llm_config)

    assert "MTGA Advisor" in html
    assert "Unique IDs" in html
    assert "Control" in html
    assert "wildcards-unknown" in html
    assert "decks.json" in html
    assert "Precons ausblenden" in html
    assert "data-deck-key='1'" in html
    assert '<script src="/dashboard.js" defer></script>' in html
    assert "const decksData" not in html
    assert "document.getElementById('config-toggle')" not in html


def test_render_index_handles_missing_decks_and_advisor() -> None:
    html = _render_index(None, None, None, None, None, LLMConfig())

    assert "Decks" in html
    assert "decks.json nicht gefunden" in html
    assert "advisor-result.json nicht gefunden" in html


def test_dashboard_script_is_loaded_from_asset() -> None:
    js = _dashboard_js()

    assert "document.addEventListener(\"DOMContentLoaded\"" in js
    assert "fetch(\"/api/chat\"" in js
    assert "innerHTML" not in js


def test_dashboard_js_has_intersection_observer() -> None:
    """Card thumbnails are lazy-loaded via IntersectionObserver."""
    js = _dashboard_js()

    assert "IntersectionObserver" in js
    assert "card-thumb" in js
    assert "/api/card-image/" in js
    assert "card-thumb-placeholder" in js


def test_render_index_has_csp_with_scryfall_img_src() -> None:
    """CSP must allow Scryfall CDN images."""
    from server.app import _html_response

    class MockHandler:
        def __init__(self):
            self.headers = {}
            self.body = None

        def send_response(self, status):
            self.status = status

        def send_header(self, key, value):
            self.headers[key] = value

        def end_headers(self):
            pass

        def wfile_write(self, data):
            self.body = data

    # Patch wfile to capture body
    class MockWFile:
        def __init__(self):
            self.data = b""
        def write(self, data):
            self.data += data

    handler = MockHandler()
    handler.wfile = MockWFile()
    _html_response(handler, "<html>test</html>", status=200)

    csp = handler.headers.get("Content-Security-Policy", "")
    assert "cards.scryfall.io" in csp
    assert "img-src" in csp


def test_render_deck_cards_section_has_thumb_column() -> None:
    """Deck card table should have a thumbnail column with data-card-id."""
    from server.app import _render_deck_cards_section

    deck = {
        "cards": {
            "mainboard": [
                {"cardId": 12345, "name": "Lightning Strike", "count": 4},
                {"cardId": 67890, "name": "Shock", "count": 2},
            ],
        },
    }
    html = _render_deck_cards_section(deck)

    assert "card-thumb-cell" in html
    assert "card-thumb-placeholder" in html
    assert "data-card-id='12345'" in html
    assert "data-card-id='67890'" in html
    assert "Lightning Strike" in html
    assert "Shock" in html


def test_fetch_scryfall_image_url_caches_results(monkeypatch) -> None:
    """_fetch_scryfall_image_url should cache results in-memory."""
    # Clear cache for this test
    _SCRYFALL_IMAGE_CACHE.clear()

    call_count = 0

    class FakeResponse:
        def __init__(self, data):
            self._data = json.dumps(data).encode("utf-8")
            self._pos = 0

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=None):
        nonlocal call_count
        call_count += 1
        return FakeResponse({
            "image_uris": {
                "normal": "https://cards.scryfall.io/normal/front/abc/123.jpg",
            }
        })

    monkeypatch.setattr("server.app.urllib.request.urlopen", fake_urlopen)

    url1 = _fetch_scryfall_image_url(99999)
    assert url1 == "https://cards.scryfall.io/normal/front/abc/123.jpg"
    assert call_count == 1

    # Second call should use cache, not API
    url2 = _fetch_scryfall_image_url(99999)
    assert url2 == url1
    assert call_count == 1  # no additional API call

    _SCRYFALL_IMAGE_CACHE.clear()


def test_fetch_scryfall_image_url_handles_404(monkeypatch) -> None:
    """_fetch_scryfall_image_url should return None on 404 and cache it."""
    _SCRYFALL_IMAGE_CACHE.clear()

    call_count = 0

    def fake_urlopen(req, timeout=None):
        nonlocal call_count
        call_count += 1
        from email.message import Message
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", Message(), BytesIO(b"not found")
        )

    monkeypatch.setattr("server.app.urllib.request.urlopen", fake_urlopen)

    url1 = _fetch_scryfall_image_url(88888)
    assert url1 is None
    assert call_count == 1

    # Second call should use cached None
    url2 = _fetch_scryfall_image_url(88888)
    assert url2 is None
    assert call_count == 1

    _SCRYFALL_IMAGE_CACHE.clear()


def test_fetch_scryfall_image_url_handles_double_faced(monkeypatch) -> None:
    """Double-faced cards should use card_faces[0].image_uris."""
    _SCRYFALL_IMAGE_CACHE.clear()

    class FakeResponse:
        def __init__(self, data):
            self._data = json.dumps(data).encode("utf-8")

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=None):
        return FakeResponse({
            "card_faces": [
                {
                    "image_uris": {
                        "normal": "https://cards.scryfall.io/normal/front/df/face1.jpg",
                    }
                }
            ]
        })

    monkeypatch.setattr("server.app.urllib.request.urlopen", fake_urlopen)

    url = _fetch_scryfall_image_url(77777)
    assert url == "https://cards.scryfall.io/normal/front/df/face1.jpg"

    _SCRYFALL_IMAGE_CACHE.clear()


# ---------------------------------------------------------------------------
# T7: Sync-Server tests
# ---------------------------------------------------------------------------

def test_default_host_is_0_0_0_0() -> None:
    """DEFAULT_HOST must be 0.0.0.0 for LAN/Tailscale access."""
    assert DEFAULT_HOST == "0.0.0.0"


def test_default_port_is_8000() -> None:
    assert DEFAULT_PORT == 8000


# --- Basic-Auth tests ---

def test_configure_basic_auth_enables_auth() -> None:
    """configure_basic_auth with valid user:pass enables auth."""
    configure_basic_auth("admin:secret")
    from server.app import _AUTH_ENABLED, _AUTH_USER, _AUTH_PASS
    assert _AUTH_ENABLED is True
    assert _AUTH_USER == "admin"
    assert _AUTH_PASS == "secret"
    # Cleanup
    configure_basic_auth(None)


def test_configure_basic_auth_disables_on_none() -> None:
    """configure_basic_auth with None disables auth."""
    configure_basic_auth("admin:secret")
    configure_basic_auth(None)
    from server.app import _AUTH_ENABLED
    assert _AUTH_ENABLED is False


def test_configure_basic_auth_disables_on_empty() -> None:
    """configure_basic_auth with empty string disables auth."""
    configure_basic_auth("admin:secret")
    configure_basic_auth("")
    from server.app import _AUTH_ENABLED
    assert _AUTH_ENABLED is False


def test_configure_basic_auth_disables_on_no_colon() -> None:
    """configure_basic_auth without colon disables auth."""
    configure_basic_auth("justauser")
    from server.app import _AUTH_ENABLED
    assert _AUTH_ENABLED is False


def test_check_auth_disabled_returns_true() -> None:
    """When auth is disabled, _check_auth always returns True."""
    configure_basic_auth(None)

    class MockHandler:
        def __init__(self):
            self.headers = {}

    assert _check_auth(MockHandler()) is True


def test_check_auth_enabled_with_valid_credentials() -> None:
    """When auth is enabled, valid credentials pass."""
    configure_basic_auth("admin:secret")

    class MockHandler:
        def __init__(self, auth_header):
            self.headers = {"Authorization": auth_header}

    valid_b64 = base64.b64encode(b"admin:secret").decode("ascii")
    assert _check_auth(MockHandler(f"Basic {valid_b64}")) is True

    configure_basic_auth(None)


def test_check_auth_enabled_with_wrong_password() -> None:
    """When auth is enabled, wrong password fails."""
    configure_basic_auth("admin:secret")

    class MockHandler:
        def __init__(self, auth_header):
            self.headers = {"Authorization": auth_header}

    invalid_b64 = base64.b64encode(b"admin:wrong").decode("ascii")
    assert _check_auth(MockHandler(f"Basic {invalid_b64}")) is False

    configure_basic_auth(None)


def test_check_auth_enabled_with_no_header() -> None:
    """When auth is enabled, missing Authorization header fails."""
    configure_basic_auth("admin:secret")

    class MockHandler:
        def __init__(self):
            self.headers = {}

    assert _check_auth(MockHandler()) is False

    configure_basic_auth(None)


def test_check_auth_enabled_with_malformed_header() -> None:
    """When auth is enabled, malformed Authorization header fails."""
    configure_basic_auth("admin:secret")

    class MockHandler:
        def __init__(self, auth_header):
            self.headers = {"Authorization": auth_header}

    assert _check_auth(MockHandler("Bearer some-token")) is False
    assert _check_auth(MockHandler("Basic not-valid-base64!!!")) is False

    configure_basic_auth(None)


# --- /health endpoint tests ---

def test_health_response_returns_ok_status() -> None:
    """_health_response returns JSON with status=ok."""
    import tempfile
    from server.app import _json_response

    class MockHandler:
        def __init__(self):
            self.headers = {}
            self.status = None
            self.body = None

        def send_response(self, status):
            self.status = status

        def send_header(self, key, value):
            self.headers[key] = value

        def end_headers(self):
            pass

    class MockWFile:
        def __init__(self):
            self.data = b""

        def write(self, data):
            self.data += data

    handler = MockHandler()
    handler.wfile = MockWFile()

    with tempfile.TemporaryDirectory() as tmpdir:
        _health_response(handler, Path(tmpdir))

    assert handler.status == 200
    body = json.loads(handler.wfile.data.decode("utf-8"))
    assert body["status"] == "ok"
    assert "timestamp" in body
    assert "version" in body
    assert "auth_enabled" in body
    assert "files" in body
    assert "collection.json" in body["files"]


# --- QR-Code tests ---

def test_get_lan_ip_returns_non_loopback() -> None:
    """_get_lan_ip should return a non-loopback IP."""
    ip = _get_lan_ip()
    assert ip != "127.0.0.1"
    # Should be a valid IPv4 address
    parts = ip.split(".")
    assert len(parts) == 4
    for part in parts:
        assert 0 <= int(part) <= 255


def test_generate_qr_ascii_returns_string() -> None:
    """_generate_qr_ascii should return a non-empty string."""
    qr = _generate_qr_ascii("http://example.com:8000")
    assert isinstance(qr, str)
    assert len(qr) > 0


def test_generate_qr_ascii_contains_url_content() -> None:
    """QR code should encode the URL — if qrcode is installed, it produces ASCII art."""
    qr = _generate_qr_ascii("http://example.com:8000")
    # If qrcode package is available, the output contains ASCII block chars
    # If not, it contains the URL as fallback
    assert "http://example.com:8000" in qr or any(c in qr for c in ["█", "▀", "▄", "#", "  "])


def test_print_server_banner_outputs_info(capsys) -> None:
    """_print_server_banner should print URL and auth status."""
    _print_server_banner("0.0.0.0", 8000, "192.168.1.100", False, show_qr=False)
    captured = capsys.readouterr()
    assert "MTGA Advisor Dashboard" in captured.out
    assert "8000" in captured.out
    assert "disabled" in captured.out


def test_print_server_banner_with_auth(capsys) -> None:
    """_print_server_banner should show auth enabled."""
    _print_server_banner("0.0.0.0", 8000, "192.168.1.100", True, show_qr=False)
    captured = capsys.readouterr()
    assert "enabled" in captured.out


def test_print_server_banner_with_qr(capsys) -> None:
    """_print_server_banner with show_qr=True should print QR code or fallback."""
    _print_server_banner("0.0.0.0", 8000, "192.168.1.100", False, show_qr=True)
    captured = capsys.readouterr()
    assert "MTGA Advisor Dashboard" in captured.out
    # QR code or fallback message should be present
    assert "Scan" in captured.out or "qrcode" in captured.out


# --- Live server integration tests ---

def _start_test_server(output_dir: Path, port: int = 0) -> tuple[MtgaAdvisorServer, int, threading.Thread]:
    """Start a test server on a random port. Returns (server, actual_port, thread)."""
    server = MtgaAdvisorServer(("127.0.0.1", port), MtgaAdvisorHandler, output_dir)
    actual_port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    return server, actual_port, thread


def test_health_endpoint_via_http() -> None:
    """GET /health should return 200 with JSON health status."""
    import tempfile
    configure_basic_auth(None)
    with tempfile.TemporaryDirectory() as tmpdir:
        server, port, thread = _start_test_server(Path(tmpdir))
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ok"
            assert "version" in data
            assert "files" in data
        finally:
            server.shutdown()


def test_auth_blocks_unauthenticated_request() -> None:
    """When auth is enabled, unauthenticated requests get 401."""
    import tempfile
    configure_basic_auth("admin:secret")
    with tempfile.TemporaryDirectory() as tmpdir:
        server, port, thread = _start_test_server(Path(tmpdir))
        try:
            try:
                resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/collection", timeout=5)
                assert False, "Should have raised 401"
            except urllib.error.HTTPError as e:
                assert e.code == 401
                assert "WWW-Authenticate" in e.headers
                assert "Basic" in e.headers["WWW-Authenticate"]
        finally:
            server.shutdown()
    configure_basic_auth(None)


def test_auth_allows_health_without_credentials() -> None:
    """When auth is enabled, /health is still accessible without credentials."""
    import tempfile
    configure_basic_auth("admin:secret")
    with tempfile.TemporaryDirectory() as tmpdir:
        server, port, thread = _start_test_server(Path(tmpdir))
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert data["status"] == "ok"
            assert data["auth_enabled"] is True
        finally:
            server.shutdown()
    configure_basic_auth(None)


def test_auth_allows_authenticated_request() -> None:
    """When auth is enabled, authenticated requests succeed."""
    import tempfile
    configure_basic_auth("admin:secret")
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a collection.json so the endpoint returns 200 not 404
        (Path(tmpdir) / "collection.json").write_text('{"cards": {}}', encoding="utf-8")
        server, port, thread = _start_test_server(Path(tmpdir))
        try:
            # Build request with auth header
            valid_b64 = base64.b64encode(b"admin:secret").decode("ascii")
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/collection",
                headers={"Authorization": f"Basic {valid_b64}"},
            )
            resp = urllib.request.urlopen(req, timeout=5)
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "cards" in data
        finally:
            server.shutdown()
    configure_basic_auth(None)


def test_no_auth_allows_all_requests() -> None:
    """When auth is disabled, all requests succeed (no 401)."""
    import tempfile
    configure_basic_auth(None)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "collection.json").write_text('{"cards": {}}', encoding="utf-8")
        server, port, thread = _start_test_server(Path(tmpdir))
        try:
            resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/api/collection", timeout=5)
            assert resp.status == 200
        finally:
            server.shutdown()
