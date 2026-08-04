"""Tests für scanner.helper_client — Helper-Daemon-Kommunikation.

Testet das JSON-Protokoll über UNIX-Socket mit einem Mock-Server.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scanner.helper_client import (  # noqa: E402
    DEFAULT_SOCK_PATH,
    HelperConnectionError,
    HelperError,
    HelperProtocolError,
    HelperStatus,
    _send_request,
    get_status,
    helper_scan_collection,
    helper_scan_collection_detailed,
    is_helper_available,
    ping,
    request_list_regions,
    request_read_memory,
    request_scan,
)


# --- Mock Helper Server ---

class MockHelperServer:
    """Mock-Server, der auf einem UNIX-Socket lauscht und JSON-Requests beantwortet."""

    def __init__(self, sock_path: str, responses: dict[str, dict[str, Any]] | None = None):
        self.sock_path = sock_path
        self.responses = responses or {}
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.received_requests: list[dict[str, Any]] = []

    def start(self):
        os.makedirs(os.path.dirname(self.sock_path), exist_ok=True)
        if os.path.exists(self.sock_path):
            os.unlink(self.sock_path)

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.settimeout(5)
        self._server_sock.bind(self.sock_path)
        self._server_sock.listen(5)
        self._running = True

        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self):
        while self._running:
            try:
                assert self._server_sock is not None
                conn, _ = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(5)
                try:
                    data = conn.recv(65536)
                    if not data:
                        continue
                    request = json.loads(data.decode("utf-8").strip())
                    self.received_requests.append(request)
                    action = request.get("action", "unknown")
                    resp = self.responses.get(action, {"error": f"unknown action: {action}"})
                    conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                except Exception:
                    pass

    def stop(self):
        self._running = False
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        if self._thread:
            self._thread.join(timeout=3)
        if os.path.exists(self.sock_path):
            try:
                os.unlink(self.sock_path)
            except OSError:
                pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()


@pytest.fixture
def mock_sock_path(tmp_path):
    """Liefert einen temporären Socket-Pfad (kurz genug für AF_UNIX)."""
    # macOS hat ein ~104-Byte-Limit für UNIX-Socket-Pfade.
    # pytest tmp_path ist oft zu lang → verwende /tmp mit eindeutigem Suffix.
    import os
    import uuid
    short_id = uuid.uuid4().hex[:8]
    return f"/tmp/mtga-test-{short_id}.sock"


# --- Tests ---

def test_ping_returns_true_when_helper_responds(mock_sock_path):
    with MockHelperServer(mock_sock_path, {"ping": {"status": "ok"}}):
        assert ping(mock_sock_path) is True


def test_ping_returns_false_when_socket_missing(tmp_path):
    missing_path = str(tmp_path / "nonexistent.sock")
    assert ping(missing_path) is False


def test_ping_returns_false_when_helper_not_running(mock_sock_path):
    # Socket-Datei existiert, aber kein Server lauscht
    Path(mock_sock_path).touch()
    assert ping(mock_sock_path) is False


def test_is_helper_available_true(mock_sock_path):
    with MockHelperServer(mock_sock_path, {"ping": {"status": "ok"}}):
        assert is_helper_available(mock_sock_path) is True


def test_is_helper_available_false_when_no_socket(tmp_path):
    assert is_helper_available(str(tmp_path / "no.sock")) is False


def test_get_status_running(mock_sock_path):
    resp_data = {"status": "ok", "pid": 12345, "version": "1.0.0"}
    with MockHelperServer(mock_sock_path, {"status": resp_data}):
        status = get_status(mock_sock_path)
        assert status.running is True
        assert status.pid == 12345
        assert status.version == "1.0.0"
        assert status.socket_path == mock_sock_path


def test_get_status_not_running_when_socket_missing(tmp_path):
    status = get_status(str(tmp_path / "no.sock"))
    assert status.running is False


def test_request_scan_success(mock_sock_path):
    scan_response = {
        "status": "ok",
        "cards": {"100": 4, "200": 1},
        "anchor_matches": {"100": 3},
    }
    with MockHelperServer(mock_sock_path, {"scan": scan_response}):
        result = request_scan(mock_sock_path, process_names=["MTGA"])
        assert result["cards"] == {"100": 4, "200": 1}
        assert result["status"] == "ok"


def test_request_scan_raises_helper_error(mock_sock_path):
    scan_response = {"error": "MTGA process not found"}
    with MockHelperServer(mock_sock_path, {"scan": scan_response}):
        with pytest.raises(HelperError, match="MTGA process not found"):
            request_scan(mock_sock_path)


def test_request_scan_raises_protocol_error_when_no_cards(mock_sock_path):
    scan_response = {"status": "ok"}
    with MockHelperServer(mock_sock_path, {"scan": scan_response}):
        with pytest.raises(HelperProtocolError, match="cards"):
            request_scan(mock_sock_path)


def test_send_request_raises_connection_error_when_socket_missing(tmp_path):
    with pytest.raises(HelperConnectionError):
        _send_request({"action": "ping"}, str(tmp_path / "no.sock"))


def test_helper_scan_collection_returns_dict(mock_sock_path, monkeypatch):
    """helper_scan_collection konvertiert JSON-String-Keys zu int."""
    scan_response = {
        "status": "ok",
        "cards": {"100": 4, "200": 1, "300": 3},
    }
    with MockHelperServer(mock_sock_path, {"ping": {"status": "ok"}, "scan": scan_response}):
        result = helper_scan_collection(mock_sock_path, print_fn=lambda *a: None)
        assert result is not None
        assert result == {100: 4, 200: 1, 300: 3}


def test_helper_scan_collection_returns_none_when_no_helper(tmp_path):
    result = helper_scan_collection(
        str(tmp_path / "no.sock"),
        print_fn=lambda *a: None,
    )
    assert result is None


def test_helper_scan_collection_returns_none_on_error(mock_sock_path):
    with MockHelperServer(mock_sock_path, {"ping": {"status": "ok"}, "scan": {"error": "scan failed"}}):
        result = helper_scan_collection(mock_sock_path, print_fn=lambda *a: None)
        assert result is None


def test_helper_scan_collection_returns_none_on_empty_cards(mock_sock_path):
    with MockHelperServer(mock_sock_path, {"ping": {"status": "ok"}, "scan": {"status": "ok", "cards": {}}}):
        result = helper_scan_collection(mock_sock_path, print_fn=lambda *a: None)
        assert result is None


def test_helper_scan_collection_detailed_returns_result(mock_sock_path, monkeypatch):
    """helper_scan_collection_detailed baut ein MemoryScanResult mit Validierung."""
    scan_response = {
        "status": "ok",
        "cards": {"100": 4, "200": 1},
        "anchor_matches": {"100": 3, "200": 2},
    }
    # Mock card_database und validate_collection
    import scanner.helper_client as hc_module

    monkeypatch.setattr(
        "scanner.card_database.load_card_database",
        lambda: {100: {"name": "Lightning Bolt"}, 200: {"name": "Giant Growth"}},
    )
    monkeypatch.setattr(
        "scanner.memory_scanner.validate_collection",
        lambda cards, db=None, anchors=None: {
            "valid": True,
            "errors": [],
            "warnings": [],
            "cardsCount": len(cards),
            "totalCards": sum(cards.values()),
            "unknownCardIdsCount": 0,
            "unknownCardIdsTruncated": False,
        },
    )

    with MockHelperServer(mock_sock_path, {"ping": {"status": "ok"}, "scan": scan_response}):
        result = helper_scan_collection_detailed(
            mock_sock_path,
            anchors=[(100, 4, "Lightning Bolt")],
            print_fn=lambda *a: None,
        )
        assert result is not None
        assert result.collection == {100: 4, 200: 1}
        assert result.validation["valid"] is True
        assert result.anchor_matches == {100: 3, 200: 2}


def test_helper_scan_collection_detailed_returns_none_when_no_helper(tmp_path):
    result = helper_scan_collection_detailed(
        str(tmp_path / "no.sock"),
        print_fn=lambda *a: None,
    )
    assert result is None


def test_send_request_sends_correct_json(mock_sock_path):
    """Verifiziert, dass der Request korrekt als JSON gesendet wird."""
    with MockHelperServer(mock_sock_path, {"ping": {"status": "ok"}}) as server:
        ping(mock_sock_path)
        assert len(server.received_requests) == 1
        assert server.received_requests[0]["action"] == "ping"


# --- Tests für Low-Level Primitives (neues Protokoll) ---


def test_request_list_regions_success(mock_sock_path):
    """list_regions liefert eine Liste von Region-Dicts."""
    regions_response = {
        "status": "ok",
        "regions": [
            {"address": 4294967296, "size": 1048576},
            {"address": 4296015872, "size": 2097152},
        ],
    }
    with MockHelperServer(mock_sock_path, {"list_regions": regions_response}):
        result = request_list_regions(mock_sock_path)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["address"] == 4294967296
        assert result[0]["size"] == 1048576
        assert result[1]["address"] == 4296015872


def test_request_list_regions_raises_helper_error(mock_sock_path):
    with MockHelperServer(mock_sock_path, {"list_regions": {"error": "MTGA not found"}}):
        with pytest.raises(HelperError, match="MTGA not found"):
            request_list_regions(mock_sock_path)


def test_request_list_regions_raises_protocol_error_when_no_regions(mock_sock_path):
    with MockHelperServer(mock_sock_path, {"list_regions": {"status": "ok"}}):
        with pytest.raises(HelperProtocolError, match="regions"):
            request_list_regions(mock_sock_path)


def test_request_read_memory_success(mock_sock_path):
    """read_memory liefert dekodierte Bytes."""
    import base64
    raw_data = b"\x00\x01\x02\x03\x04"
    b64_data = base64.b64encode(raw_data).decode("utf-8")
    read_response = {
        "status": "ok",
        "data": b64_data,
        "bytes_read": 5,
    }
    with MockHelperServer(mock_sock_path, {"read_memory": read_response}):
        result = request_read_memory(mock_sock_path, address=4294967296, size=5)
        assert isinstance(result, bytes)
        assert result == raw_data
        assert len(result) == 5


def test_request_read_memory_raises_helper_error(mock_sock_path):
    with MockHelperServer(mock_sock_path, {"read_memory": {"error": "Invalid address"}}):
        with pytest.raises(HelperError, match="Invalid address"):
            request_read_memory(mock_sock_path, address=0, size=64)


def test_request_read_memory_raises_protocol_error_when_no_data(mock_sock_path):
    with MockHelperServer(mock_sock_path, {"read_memory": {"status": "ok"}}):
        with pytest.raises(HelperProtocolError, match="data"):
            request_read_memory(mock_sock_path, address=4294967296, size=64)


def test_request_read_memory_raises_protocol_error_on_invalid_address():
    with pytest.raises(HelperProtocolError, match="Ungültige Adresse"):
        request_read_memory("/tmp/no.sock", address=-1, size=64)


def test_request_read_memory_raises_protocol_error_on_invalid_size():
    with pytest.raises(HelperProtocolError, match="Ungültige Größe"):
        request_read_memory("/tmp/no.sock", address=4294967296, size=0)