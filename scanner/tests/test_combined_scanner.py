"""Tests für scanner.combined_scanner — kombinierter Scan über eine persistente Helper-Verbindung.

Testet:
- _PersistentConnection mit Mock-Server (Keep-Alive: mehrere Requests über eine Connection)
- PersistentHelperBackend / PersistentRemoteMemoryAdapter
- scan_all() mit use_helper=True (gemockt)
- scan_all() mit use_helper=False (gemockt, direkter Weg)
"""

from __future__ import annotations

import base64
import json
import os
import socket
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scanner.helper_client import (  # noqa: E402
    _PersistentConnection,
    PersistentHelperBackend,
    PersistentRemoteMemoryAdapter,
    open_helper_connection,
)
from scanner.combined_scanner import scan_all, CombinedScanResult  # noqa: E402


# --- Mock Helper Server (Keep-Alive fähig) ---

class MockHelperServer:
    """Mock-Server, der mehrere Requests pro Connection verarbeitet (Keep-Alive)."""

    def __init__(self, sock_path: str, responses: dict[str, dict[str, Any]] | None = None):
        self.sock_path = sock_path
        self.responses = responses or {}
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self.received_requests: list[dict[str, Any]] = []
        self.connection_count = 0

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
            self.connection_count += 1
            # Handle multiple requests on the same connection (Keep-Alive)
            threading.Thread(target=self._handle_client, args=(conn,), daemon=True).start()

    def _handle_client(self, conn: socket.socket):
        with conn:
            conn.settimeout(5)
            buf = b""
            while self._running:
                try:
                    data = conn.recv(65536)
                    if not data:
                        break
                    buf += data
                    # Process all complete (newline-terminated) requests
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            request = json.loads(line.decode("utf-8"))
                            self.received_requests.append(request)
                            action = request.get("action", "unknown")
                            resp = self.responses.get(action, {"error": f"unknown action: {action}"})
                            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
                        except Exception:
                            pass
                except socket.timeout:
                    break
                except OSError:
                    break

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
def mock_sock_path():
    """Liefert einen temporären Socket-Pfad (kurz genug für AF_UNIX)."""
    short_id = uuid.uuid4().hex[:8]
    return f"/tmp/mtga-test-{short_id}.sock"


# --- Tests für _PersistentConnection ---

def test_persistent_connection_multiple_requests(mock_sock_path):
    """Mehrere Requests über eine persistente Verbindung."""
    responses = {
        "ping": {"status": "ok"},
        "status": {"status": "ok", "pid": 12345, "version": "1.0.0"},
    }
    with MockHelperServer(mock_sock_path, responses) as server:
        conn = _PersistentConnection(mock_sock_path)

        # Erstes Request
        resp1 = conn.request({"action": "ping"})
        assert resp1["status"] == "ok"

        # Zweites Request über dieselbe Verbindung
        resp2 = conn.request({"action": "status"})
        assert resp2["status"] == "ok"
        assert resp2["pid"] == 12345

        conn.close()

        # Nur eine Verbindung sollte geöffnet worden sein
        assert server.connection_count == 1
        assert len(server.received_requests) == 2


def test_persistent_connection_reconnect_after_close(mock_sock_path):
    """Nach close() wird bei nächstem Request eine neue Verbindung geöffnet."""
    with MockHelperServer(mock_sock_path, {"ping": {"status": "ok"}}) as server:
        conn = _PersistentConnection(mock_sock_path)
        conn.request({"action": "ping"})
        conn.close()
        conn.request({"action": "ping"})
        conn.close()
        assert server.connection_count == 2


def test_open_helper_connection_ping(mock_sock_path):
    """open_helper_connection prüft mit Ping, dass die Verbindung funktioniert."""
    with MockHelperServer(mock_sock_path, {"ping": {"status": "ok"}}):
        conn = open_helper_connection(mock_sock_path)
        assert conn._sock is not None
        conn.close()


# --- Tests für PersistentHelperBackend ---

def test_persistent_helper_backend_read_bytes(mock_sock_path):
    raw = b"\xde\xad\xbe\xef"
    b64 = base64.b64encode(raw).decode("utf-8")
    responses = {
        "ping": {"status": "ok"},
        "read_memory": {"status": "ok", "data": b64, "bytes_read": 4},
    }
    with MockHelperServer(mock_sock_path, responses):
        conn = open_helper_connection(mock_sock_path)
        backend = PersistentHelperBackend(conn)
        result = backend.read_bytes(0x1000, 4)
        assert result == raw
        conn.close()


def test_persistent_helper_backend_iterate_regions(mock_sock_path):
    regions_resp = {
        "status": "ok",
        "regions": [
            {"address": 4294967296, "size": 1048576},
            {"address": 4296015872, "size": 2097152},
        ],
    }
    responses = {
        "ping": {"status": "ok"},
        "list_regions": regions_resp,
    }
    with MockHelperServer(mock_sock_path, responses):
        conn = open_helper_connection(mock_sock_path)
        backend = PersistentHelperBackend(conn)
        regions, err = backend.iterate_readable_regions()
        assert err is None
        assert len(regions) == 2
        assert regions[0] == (4294967296, 1048576)
        conn.close()


# --- Tests für PersistentRemoteMemoryAdapter ---

def test_persistent_remote_memory_adapter_read_ptr(mock_sock_path):
    import struct
    raw = struct.pack("<Q", 0xDEADBEEF)
    b64 = base64.b64encode(raw).decode("utf-8")
    responses = {
        "ping": {"status": "ok"},
        "read_memory": {"status": "ok", "data": b64, "bytes_read": 8},
    }
    with MockHelperServer(mock_sock_path, responses):
        conn = open_helper_connection(mock_sock_path)
        adapter = PersistentRemoteMemoryAdapter(conn)
        assert adapter.read_ptr(0x1000) == 0xDEADBEEF
        conn.close()


def test_persistent_remote_memory_adapter_pid(mock_sock_path):
    responses = {
        "ping": {"status": "ok"},
        "status": {"status": "ok", "pid": 9999, "version": "1.0.0"},
    }
    with MockHelperServer(mock_sock_path, responses):
        conn = open_helper_connection(mock_sock_path)
        adapter = PersistentRemoteMemoryAdapter(conn)
        assert adapter.pid == 9999
        conn.close()


# --- Tests für scan_all() ---

def test_scan_all_helper_not_available(monkeypatch):
    """Wenn Helper nicht verfügbar und use_helper=False, wird direkter Weg versucht."""
    from scanner import combined_scanner

    # Direkten Scan mocken — _attach_process gibt None zurück
    monkeypatch.setattr(
        combined_scanner,
        "_scan_all_direct",
        lambda **kwargs: CombinedScanResult(errors=["mocked"]),
    )

    result = scan_all(use_helper=False, print_fn=lambda *a, **k: None)

    assert result.errors == ["mocked"]
    assert result.used_helper is False


def test_scan_all_helper_available_but_connection_fails(monkeypatch):
    """Helper ist verfügbar, aber Verbindung schlägt fehl."""
    from scanner import combined_scanner

    monkeypatch.setattr(
        combined_scanner,
        "is_helper_available",
        lambda sock_path: True,
    )
    monkeypatch.setattr(
        combined_scanner,
        "open_helper_connection",
        lambda *a, **k: (_ for _ in ()).throw(ConnectionError("mock")),
    )

    result = scan_all(use_helper=True, print_fn=lambda *a, **k: None)

    assert result.used_helper is True
    assert any("Helper-Verbindung" in e for e in result.errors)


def test_scan_all_via_helper_with_mocks(monkeypatch):
    """Kombinierter Scan über Helper mit gemockten Scanner-Funktionen."""
    from scanner import combined_scanner, helper_client

    # Fake persistent connection
    class FakeConn:
        def __init__(self):
            self._sock = object()  # nicht None → sieht verbunden aus

        def request(self, req):
            if req.get("action") == "ping":
                return {"status": "ok"}
            if req.get("action") == "status":
                return {"status": "ok", "pid": 12345, "version": "1.0.0"}
            return {"status": "ok"}

        def close(self):
            self._sock = None

    fake_conn = FakeConn()
    monkeypatch.setattr(combined_scanner, "is_helper_available", lambda sock_path: True)
    monkeypatch.setattr(combined_scanner, "open_helper_connection", lambda *a, **k: fake_conn)

    # scan_collection_detailed mocken
    from scanner.memory_scanner import MemoryScanResult

    def fake_scan_collection_detailed(**kwargs):
        return MemoryScanResult(
            collection={114001: 4},
            anchors=[(114001, 4, "Card A")],
            anchor_matches={114001: 1},
            validation={"valid": True},
        )

    import scanner.memory_scanner as memory_scanner_mod
    monkeypatch.setattr(
        memory_scanner_mod,
        "scan_collection_detailed",
        fake_scan_collection_detailed,
    )

    # scan_decks_il2cpp mocken
    from scanner.il2cpp_nav import Il2CppScanResult, Il2CppDeckResult

    def fake_scan_decks(reader, **kwargs):
        deck = Il2CppDeckResult(deck_id=1, name="Test Deck", piles={1: {114001: 4}})
        return Il2CppScanResult(decks=[deck])

    import scanner.il2cpp_nav as il2cpp_mod
    monkeypatch.setattr(il2cpp_mod, "scan_decks_il2cpp", fake_scan_decks)

    # scan_ranks_and_account mocken
    from scanner.rank_scanner import FullRankScanResult, RankScanResult, AccountInfo

    def fake_scan_ranks(reader, **kwargs):
        return FullRankScanResult(
            ranks=RankScanResult(player_id="test123"),
            account=AccountInfo(display_name="Tester"),
        )

    import scanner.rank_scanner as rank_mod
    monkeypatch.setattr(rank_mod, "scan_ranks_and_account", fake_scan_ranks)

    result = scan_all(use_helper=True, print_fn=lambda *a, **k: None)

    assert result.used_helper is True
    assert result.collection is not None
    assert len(result.decks) == 1
    assert result.decks[0]["name"] == "Test Deck"
    assert result.ranks is not None
    assert result.account is not None
    assert fake_conn._sock is None  # Verbindung wurde geschlossen