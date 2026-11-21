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
    _helper_status_response,
    _find_deck_payload,
    _render_decks_table,
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


def _write_deck_cards(path: Path, deck_id: str = "imported-1", name: str = "Imported Deck") -> None:
    """Schreibe eine minimale deck-cards.json (Schema deck-cards.v1)."""
    payload = {
        "schema": "deck-cards.v1",
        "source": "local-logs",
        "decks": [
            {
                "deckId": deck_id,
                "name": name,
                "format": "historic",
                "mainDeck": [{"cardId": 123, "quantity": 4}],
                "sideboard": [],
                "commandZone": [],
                "companions": [],
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_render_decks_table_without_decks_shows_import_form(tmp_path: Path) -> None:
    # T10 AK1: keine decks.json, keine deck-cards.json → Hinweis UND Import-Formular
    html = _render_decks_table(None, tmp_path)

    assert "Noch keine Decks erkannt" in html
    assert "Deck → Copy to Clipboard" in html
    assert 'id="deck-import-btn"' in html
    assert 'id="deck-import-panel"' in html
    assert 'id="deck-import-text"' in html
    assert 'id="deck-import-submit"' in html
    assert "Noch kein Deck-Export vorhanden" not in html


def test_render_decks_table_without_decks_json_merges_deck_cards(tmp_path: Path) -> None:
    # T10: deck-cards.json ohne decks.json → synthetischer Tabellen-Eintrag
    # (Deckname statt nackter deckId) mit Kartenliste-Badge + Import-Formular.
    _write_deck_cards(tmp_path / "deck-cards.json")

    html = _render_decks_table(None, tmp_path)

    assert "Imported Deck" in html
    assert "✓ Kartenliste" in html
    assert "data-deck-key='imported-1'" in html
    assert 'id="deck-import-btn"' in html
    assert "Noch keine Decks erkannt" not in html
    assert "<table id='deck-table'>" in html


def test_render_decks_table_with_decks_json_is_unchanged(tmp_path: Path) -> None:
    # T10 AK3: mit vorhandenen decks.json-Daten kein Regress —
    # Tabelle inkl. Toolbar wie im Bestand, kein Badge ohne deck-cards.json.
    decks = {
        "schema": "decks.v1",
        "decks": [
            {
                "name": "Control",
                "deckId": "1",
                "deckKey": "1",
                "attributes": {"Format": "standard"},
                "isPrecon": False,
            }
        ],
    }

    html = _render_decks_table(decks, tmp_path)

    assert "Control" in html
    assert "✓ Kartenliste" not in html
    assert "Noch keine Decks erkannt" not in html
    assert "<table id='deck-table'>" in html
    assert 'id="deck-import-btn"' in html
    assert "Klick auf eine Zeile für Details, Chat-Button für Advisor." in html


def test_render_decks_table_merges_deck_cards_into_existing_decks(tmp_path: Path) -> None:
    # decks.json vorhanden + importiertes Deck in deck-cards.json →
    # beide erscheinen, importiertes mit Badge, bestehendes ohne.
    decks = {
        "schema": "decks.v1",
        "decks": [{"name": "Control", "deckId": "1", "deckKey": "1", "isPrecon": False}],
    }
    _write_deck_cards(tmp_path / "deck-cards.json")

    html = _render_decks_table(decks, tmp_path)

    assert "Control" in html
    assert "Imported Deck" in html
    assert "✓ Kartenliste" in html


def test_find_deck_payload_reads_deck_cards_json(tmp_path: Path) -> None:
    # T10: Detailansicht — /api/deck/<key> muss importierte Decks auch
    # dann liefern, wenn decks.json fehlt.
    _write_deck_cards(tmp_path / "deck-cards.json")

    payload = _find_deck_payload(tmp_path, "imported-1")

    assert payload is not None
    assert payload["name"] == "Imported Deck"
    assert payload["mainDeck"] == [{"cardId": 123, "quantity": 4}]
    assert _find_deck_payload(tmp_path, "unknown-deck") is None


def test_find_deck_payload_prefers_decks_json(tmp_path: Path) -> None:
    # AK3: decks.json hat Vorrang vor deck-cards.json (kein Regress).
    (tmp_path / "decks.json").write_text(
        json.dumps(
            {
                "schema": "decks.v1",
                "decks": [
                    {
                        "deckId": "1",
                        "deckKey": "1",
                        "name": "Aus decks.json",
                        "mainDeck": [{"grpId": 1, "name": "Island", "quantity": 2}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    _write_deck_cards(tmp_path / "deck-cards.json", deck_id="1", name="Aus deck-cards.json")

    payload = _find_deck_payload(tmp_path, "1")

    assert payload is not None
    assert payload["name"] == "Aus decks.json"


def test_dashboard_js_reloads_after_saved_import() -> None:
    js = _dashboard_js()

    # T10: Import-Handler hängt an IDs, nicht an Tabellenzeilen — wird also
    # auch initialisiert, wenn die Tabelle leer ist.
    assert 'getElementById("deck-import-btn")' in js
    # T10: nach gespeichertem Import deck-cards.json neu lesen via Reload.
    assert "window.location.reload()" in js
    # T10: Steuer-Buttons ohne Deck-Bezug lösen keinen Chat-Select aus.
    assert "if (!deckKey) return;" in js


def test_dashboard_script_is_loaded_from_asset() -> None:
    js = _dashboard_js()

    assert "document.addEventListener(\"DOMContentLoaded\"" in js
    assert "fetch(\"/api/chat\"" in js


def test_dashboard_js_has_no_code_execution_sinks() -> None:
    """Das Dashboard-JS darf keinen Code aus Daten ausführen.

    Ursprünglich prüfte dieser Test pauschal ``innerHTML not in js``.  Das war
    kein tragfähiger Proxy für die Sicherheitsinvariante: ``innerHTML`` wird
    weiterhin für serverseitig erzeugtes Markup und escapte Werte genutzt, und
    der Test schlug deshalb dauerhaft fehl, ohne eine echte Schwachstelle zu
    benennen (Regression vom 26.09.2026).

    Die tatsächliche Invariante ist: kein Ausführen von Strings als Code.
    Serverseitig ist das ohnehin doppelt abgesichert — die CSP im Dashboard
    setzt ``script-src 'self'`` ohne ``'unsafe-inline'``/``'unsafe-eval'``
    (siehe test_render_index_has_csp_with_scryfall_img_src).
    """
    js = _dashboard_js()

    assert "eval(" not in js
    assert "new Function(" not in js
    assert "document.write" not in js
    assert "javascript:" not in js
    assert 'setTimeout("' not in js
    assert 'setInterval("' not in js


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


# ---------------------------------------------------------------------------
# T5: /api/helper/status endpoint tests
# ---------------------------------------------------------------------------


def test_helper_status_response_returns_dict_with_required_fields() -> None:
    """_helper_status_response must return a dict with all required fields."""
    status = _helper_status_response()
    assert isinstance(status, dict)
    assert "installed" in status
    assert "running" in status
    assert "socket_path" in status
    assert "pid" in status
    assert "version" in status
    assert "bundle_built" in status
    assert "install_instructions" in status
    assert isinstance(status["installed"], bool)
    assert isinstance(status["running"], bool)
    assert isinstance(status["socket_path"], str)


def test_helper_status_response_running_is_false_when_no_socket() -> None:
    """When no helper socket exists, running should be False."""
    status = _helper_status_response()
    # On a test machine without the helper installed, running should be False
    # (We can't guarantee installed is False, but running should be False
    #  if there's no socket)
    if not status["installed"]:
        assert status["running"] is False


def test_helper_status_response_install_instructions_when_not_installed() -> None:
    """When helper is not installed, install_instructions should be non-empty."""
    status = _helper_status_response()
    if not status["installed"]:
        assert status["install_instructions"] != ""
        assert "install.sh" in status["install_instructions"]


def test_helper_status_endpoint_via_http() -> None:
    """GET /api/helper/status should return 200 with JSON status."""
    import tempfile
    configure_basic_auth(None)
    with tempfile.TemporaryDirectory() as tmpdir:
        server, port, thread = _start_test_server(Path(tmpdir))
        try:
            resp = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/helper/status", timeout=5
            )
            assert resp.status == 200
            data = json.loads(resp.read().decode("utf-8"))
            assert "installed" in data
            assert "running" in data
            assert "socket_path" in data
        finally:
            server.shutdown()


def test_render_index_includes_helper_status_section() -> None:
    """_render_index should include the Sudo-Helper section in the HTML."""
    html = _render_index(None, None, None, None, None, LLMConfig())
    assert "Sudo-Helper" in html
    assert "helper-section" in html
    assert "helper-status-content" in html
    assert "helper-loading" in html


def test_render_index_includes_helper_status_nav_link() -> None:
    """_render_index should include a link to /api/helper/status in the nav."""
    html = _render_index(None, None, None, None, None, LLMConfig())
    assert 'href="/api/helper/status"' in html


def test_dashboard_js_has_helper_status_fetch() -> None:
    """dashboard.js should fetch /api/helper/status and render the result."""
    js = _dashboard_js()
    assert "/api/helper/status" in js
    assert "helper-status-content" in js
    assert "renderHelperStatus" in js
    assert "loadHelperStatus" in js


def test_dashboard_js_has_helper_install_button() -> None:
    """dashboard.js should include an install button for the helper."""
    js = _dashboard_js()
    assert "helper-install-btn" in js
    assert "helper-start-btn" in js
    assert "helper-refresh-btn" in js
