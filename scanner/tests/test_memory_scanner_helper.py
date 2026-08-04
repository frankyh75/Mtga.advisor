"""Tests für memory_scanner-Integration des helper_client.

Testet, dass scan_collection_detailed() und scan_collection() korrekt
zwischen Helper-Pfad (sudo-frei) und direktem Scan (pymem-osx) umschalten,
basierend auf dem use_helper-Parameter und Auto-Detect.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from scanner.memory_scanner import (  # noqa: E402
    MemoryScanResult,
    scan_collection,
    scan_collection_detailed,
)


# --- Fixtures ---

def _make_scan_result() -> MemoryScanResult:
    return MemoryScanResult(
        collection={100: 4, 200: 1},
        anchors=[],
        anchor_matches={},
        validation={
            "valid": True,
            "errors": [],
            "warnings": [],
            "cardsCount": 2,
            "totalCards": 5,
        },
    )


# --- use_helper=True → delegiert an helper_client ---

def test_scan_collection_detailed_use_helper_true_delegates_to_helper(monkeypatch):
    """use_helper=True → helper_scan_collection_detailed wird aufgerufen."""
    calls: list[dict[str, Any]] = []

    def mock_helper_scan(sock_path, *, process_names=None, debug=False, print_fn=print):
        calls.append({"sock_path": sock_path, "process_names": process_names, "debug": debug})
        return _make_scan_result()

    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        mock_helper_scan,
    )

    # is_helper_available sollte bei use_helper=True gar nicht aufgerufen werden
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: False)

    result = scan_collection_detailed(
        use_helper=True,
        sock_path="/tmp/test.sock",
        print_fn=lambda *a: None,
    )
    assert result is not None
    assert result.collection == {100: 4, 200: 1}
    assert len(calls) == 1
    assert calls[0]["sock_path"] == "/tmp/test.sock"


def test_scan_collection_detailed_use_helper_true_passes_process_names(monkeypatch):
    """process_names werden an helper_scan_collection_detailed durchgereicht."""
    received_names: list = []

    def mock_helper_scan(sock_path, *, process_names=None, debug=False, print_fn=print):
        received_names.append(process_names)
        return _make_scan_result()

    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        mock_helper_scan,
    )
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: False)

    scan_collection_detailed(
        use_helper=True,
        process_names=["MTGA", "MTGA.exe"],
        print_fn=lambda *a: None,
    )
    assert received_names == [["MTGA", "MTGA.exe"]]


def test_scan_collection_detailed_use_helper_true_returns_none_on_helper_failure(monkeypatch):
    """Wenn helper_scan_collection_detailed None liefert, wird None zurückgegeben."""
    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        lambda sock_path, *, process_names=None, debug=False, print_fn=print: None,
    )
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: False)

    result = scan_collection_detailed(
        use_helper=True,
        print_fn=lambda *a: None,
    )
    assert result is None


# --- use_helper=False → direkter Scan, Helper wird nicht kontaktiert ---

def test_scan_collection_detailed_use_helper_false_skips_helper(monkeypatch):
    """use_helper=False → helper_scan_collection_detailed wird NICHT aufgerufen."""
    helper_called: list[bool] = []

    def mock_helper_scan(sock_path, *, process_names=None, debug=False, print_fn=print):
        helper_called.append(True)
        return _make_scan_result()

    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        mock_helper_scan,
    )
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: True)

    # Direkten Scan mocken — _attach_process gibt None → scan_collection_detailed gibt None
    monkeypatch.setattr(
        "scanner.memory_scanner._attach_process",
        lambda names, print_fn=print: None,
    )
    monkeypatch.setattr(
        "scanner.memory_scanner.load_card_database",
        lambda: {100: {"name": "Test"}, 200: {"name": "Test2"}},
    )

    result = scan_collection_detailed(
        use_helper=False,
        print_fn=lambda *a: None,
    )
    # None weil _attach_process None liefert
    assert result is None
    assert helper_called == []


# --- use_helper=None → auto-detect ---

def test_scan_collection_detailed_auto_detect_uses_helper_when_available(monkeypatch):
    """use_helper=None + Helper verfügbar → Helper wird genutzt."""
    helper_called: list[bool] = []

    def mock_helper_scan(sock_path, *, process_names=None, debug=False, print_fn=print):
        helper_called.append(True)
        return _make_scan_result()

    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        mock_helper_scan,
    )
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: True)

    result = scan_collection_detailed(
        use_helper=None,
        print_fn=lambda *a: None,
    )
    assert result is not None
    assert result.collection == {100: 4, 200: 1}
    assert helper_called == [True]


def test_scan_collection_detailed_auto_detect_falls_back_when_helper_unavailable(monkeypatch):
    """use_helper=None + Helper nicht verfügbar → direkter Scan."""
    helper_called: list[bool] = []

    def mock_helper_scan(sock_path, *, process_names=None, debug=False, print_fn=print):
        helper_called.append(True)
        return _make_scan_result()

    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        mock_helper_scan,
    )
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: False)

    # Direkten Scan mocken
    monkeypatch.setattr(
        "scanner.memory_scanner._attach_process",
        lambda names, print_fn=print: None,
    )
    monkeypatch.setattr(
        "scanner.memory_scanner.load_card_database",
        lambda: {100: {"name": "Test"}, 200: {"name": "Test2"}},
    )

    result = scan_collection_detailed(
        use_helper=None,
        print_fn=lambda *a: None,
    )
    assert result is None  # _attach_process gibt None
    assert helper_called == []


# --- scan_collection Wrapper ---

def test_scan_collection_use_helper_true_returns_collection(monkeypatch):
    """scan_collection(use_helper=True) liefert dict[int,int]."""
    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        lambda sock_path, *, process_names=None, debug=False, print_fn=print: _make_scan_result(),
    )
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: False)

    result = scan_collection(
        use_helper=True,
        print_fn=lambda *a: None,
    )
    assert result is not None
    assert result == {100: 4, 200: 1}


def test_scan_collection_use_helper_true_returns_none_on_failure(monkeypatch):
    """scan_collection(use_helper=True) liefert None bei Helper-Fehler."""
    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        lambda sock_path, *, process_names=None, debug=False, print_fn=print: None,
    )
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: False)

    result = scan_collection(
        use_helper=True,
        print_fn=lambda *a: None,
    )
    assert result is None


# --- Default-Sock-Path ---

def test_scan_collection_detailed_uses_default_sock_path(monkeypatch):
    """Wenn sock_path=None, wird DEFAULT_SOCK_PATH verwendet."""
    received_sock: list[str] = []

    def mock_helper_scan(sock_path, *, process_names=None, debug=False, print_fn=print):
        received_sock.append(sock_path)
        return _make_scan_result()

    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        mock_helper_scan,
    )
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: True)

    scan_collection_detailed(
        use_helper=True,
        sock_path=None,
        print_fn=lambda *a: None,
    )
    from scanner.helper_client import DEFAULT_SOCK_PATH
    assert received_sock == [DEFAULT_SOCK_PATH]


# --- debug-Flag wird durchgereicht ---

def test_scan_collection_detailed_debug_passed_to_helper(monkeypatch):
    """debug=True wird an helper_scan_collection_detailed weitergegeben."""
    received_debug: list[bool] = []

    def mock_helper_scan(sock_path, *, process_names=None, debug=False, print_fn=print):
        received_debug.append(debug)
        return _make_scan_result()

    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        mock_helper_scan,
    )
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: True)

    scan_collection_detailed(
        use_helper=True,
        debug=True,
        print_fn=lambda *a: None,
    )
    assert received_debug == [True]


# --- print_fn wird durchgereicht ---

def test_scan_collection_detailed_print_fn_passed_to_helper(monkeypatch):
    """print_fn wird an helper_scan_collection_detailed weitergegeben."""
    received_prints: list = []

    def custom_print(*args, **kwargs):
        received_prints.append(args)

    def mock_helper_scan(sock_path, *, process_names=None, debug=False, print_fn=print):
        print_fn("test message")
        return _make_scan_result()

    monkeypatch.setattr(
        "scanner.helper_client.helper_scan_collection_detailed",
        mock_helper_scan,
    )
    monkeypatch.setattr("scanner.helper_client.is_helper_available", lambda sock: True)

    scan_collection_detailed(
        use_helper=True,
        print_fn=custom_print,
    )
    # scan_collection_detailed gibt selbst eine "🔄"-Meldung aus,
    # dann ruft es den Helper auf, der print_fn für "test message" nutzt.
    assert ("test message",) in received_prints