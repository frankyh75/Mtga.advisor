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
    HelperBackend,
    HelperConnectionError,
    HelperError,
    HelperProtocolError,
    HelperStatus,
    RemoteMemoryAdapter,
    _send_request,
    get_status,
    is_helper_available,
    ping,
    request_list_regions,
    request_read_memory,
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


def test_send_request_raises_connection_error_when_socket_missing(tmp_path):
    with pytest.raises(HelperConnectionError):
        _send_request({"action": "ping"}, str(tmp_path / "no.sock"))


def test_send_request_sends_correct_json(mock_sock_path):
    """Verifiziert, dass der Request korrekt als JSON gesendet wird."""
    with MockHelperServer(mock_sock_path, {"ping": {"status": "ok"}}) as server:
        ping(mock_sock_path)
        assert len(server.received_requests) == 1
        assert server.received_requests[0]["action"] == "ping"


# --- Tests für HelperBackend ---


def test_helper_backend_read_bytes_success(mock_sock_path):
    import base64
    raw = b"\xde\xad\xbe\xef"
    b64 = base64.b64encode(raw).decode("utf-8")
    with MockHelperServer(mock_sock_path, {"read_memory": {"status": "ok", "data": b64, "bytes_read": 4}}):
        backend = HelperBackend(mock_sock_path)
        result = backend.read_bytes(0x1000, 4)
        assert result == raw


def test_helper_backend_read_bytes_returns_none_on_error(mock_sock_path):
    with MockHelperServer(mock_sock_path, {"read_memory": {"error": "failed"}}):
        backend = HelperBackend(mock_sock_path)
        result = backend.read_bytes(0x1000, 4)
        assert result is None


def test_helper_backend_iterate_readable_regions(mock_sock_path):
    regions_response = {
        "status": "ok",
        "regions": [
            {"address": 4294967296, "size": 1048576},
            {"address": 4296015872, "size": 2097152},
        ],
    }
    with MockHelperServer(mock_sock_path, {"list_regions": regions_response}):
        backend = HelperBackend(mock_sock_path)
        regions, err = backend.iterate_readable_regions()
        assert err is None
        assert len(regions) == 2
        assert regions[0] == (4294967296, 1048576)
        assert regions[1] == (4296015872, 2097152)


# --- Tests für RemoteMemoryAdapter ---


def test_remote_memory_adapter_read_bytes(mock_sock_path):
    import base64
    raw = b"\x00\x01\x02\x03"
    b64 = base64.b64encode(raw).decode("utf-8")
    with MockHelperServer(mock_sock_path, {"read_memory": {"status": "ok", "data": b64, "bytes_read": 4}}):
        adapter = RemoteMemoryAdapter(mock_sock_path)
        result = adapter.read_bytes(0x1000, 4)
        assert result == raw


def test_remote_memory_adapter_read_bytes_fallback_on_error(mock_sock_path):
    with MockHelperServer(mock_sock_path, {"read_memory": {"error": "failed"}}):
        adapter = RemoteMemoryAdapter(mock_sock_path)
        result = adapter.read_bytes(0x1000, 4)
        assert result == b"\x00" * 4


def test_remote_memory_adapter_read_ptr(mock_sock_path):
    import base64
    import struct
    raw = struct.pack("<Q", 0xDEADBEEF)
    b64 = base64.b64encode(raw).decode("utf-8")
    with MockHelperServer(mock_sock_path, {"read_memory": {"status": "ok", "data": b64, "bytes_read": 8}}):
        adapter = RemoteMemoryAdapter(mock_sock_path)
        assert adapter.read_ptr(0x1000) == 0xDEADBEEF


def test_remote_memory_adapter_read_u32(mock_sock_path):
    import base64
    import struct
    raw = struct.pack("<I", 42)
    b64 = base64.b64encode(raw).decode("utf-8")
    with MockHelperServer(mock_sock_path, {"read_memory": {"status": "ok", "data": b64, "bytes_read": 4}}):
        adapter = RemoteMemoryAdapter(mock_sock_path)
        assert adapter.read_u32(0x1000) == 42


def test_remote_memory_adapter_read_string(mock_sock_path):
    import base64
    raw = b"Hello\x00world"
    b64 = base64.b64encode(raw).decode("utf-8")
    with MockHelperServer(mock_sock_path, {"read_memory": {"status": "ok", "data": b64, "bytes_read": 11}}):
        adapter = RemoteMemoryAdapter(mock_sock_path)
        assert adapter.read_string(0x1000) == "Hello"


def test_remote_memory_adapter_read_string_zero_addr():
    adapter = RemoteMemoryAdapter("/tmp/no.sock")
    assert adapter.read_string(0) == ""


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