"""Tests für memory_scanner.py mit HelperBackend-Mocking.

Statt echter Socket-Kommunikation wird HelperBackend gemockt,
sodass scan_collection_detailed() mit use_helper=True getestet wird.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scanner import helper_client, memory_scanner, pattern_scanner


def test_scan_collection_detailed_with_helper_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scan_collection_detailed() mit use_helper=True und gemocktem HelperBackend."""
    anchor_file = tmp_path / "last_anchors.json"
    monkeypatch.setattr(memory_scanner, "_anchor_file", lambda: anchor_file)
    monkeypatch.setattr(
        memory_scanner,
        "load_card_database",
        lambda: {
            114001: {"name": "Card A"},
            114002: {"name": "Card B"},
        },
    )
    monkeypatch.setattr(
        memory_scanner,
        "get_user_anchors",
        lambda name_to_id, **kwargs: [(114001, 4, "Card A"), (114002, 2, "Card B")],
    )

    # HelperBackend durch FakeBackend ersetzen
    monkeypatch.setattr(
        helper_client,
        "is_helper_available",
        lambda sock_path: True,
    )
    monkeypatch.setattr(
        helper_client,
        "HelperBackend",
        lambda sock_path: FakeBackend(),
    )

    # memory_scanner und block_parser mocken (damit find_blocks nicht auf echte Daten angewiesen ist)
    found_addresses = iter([[0x2000], [0x3000]])

    def fake_memory_scanner(backend: object, needle: bytes) -> list[int]:
        return next(found_addresses)

    def fake_block_parser(backend: object, addr: int) -> list[dict[int, int]]:
        if addr == 0x2000:
            return [{114001: 4, 114002: 2}, {100010: 1}]
        return [{200001: 1, 200002: 2}]

    result = memory_scanner.scan_collection_detailed(
        use_helper=True,
        memory_scanner=fake_memory_scanner,
        block_parser=fake_block_parser,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result is not None
    assert len(result.collection) > 0
    assert result.validation["valid"] is True


def test_scan_collection_detailed_helper_fallback_to_direct(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Wenn Helper nicht verfügbar, fällt use_helper=None auf direkten Scan zurück."""
    anchor_file = tmp_path / "last_anchors.json"
    monkeypatch.setattr(memory_scanner, "_anchor_file", lambda: anchor_file)
    monkeypatch.setattr(
        memory_scanner,
        "load_card_database",
        lambda: {
            114001: {"name": "Card A"},
            114002: {"name": "Card B"},
        },
    )
    monkeypatch.setattr(
        memory_scanner,
        "get_user_anchors",
        lambda name_to_id, **kwargs: [(114001, 4, "Card A"), (114002, 2, "Card B")],
    )

    # Helper nicht verfügbar
    monkeypatch.setattr(
        helper_client,
        "is_helper_available",
        lambda sock_path: False,
    )

    # Direkten Scan mocken (Pymem)
    class FakePymem:
        def __init__(self, process_name: str) -> None:
            self.pid = 4242
            self.task = 7

    monkeypatch.setattr(memory_scanner, "Pymem", FakePymem)
    monkeypatch.setattr(
        memory_scanner,
        "_attach_process",
        lambda process_names, **kwargs: FakePymem("MTGA"),
    )

    # memory_scanner und block_parser mocken
    found_addresses = iter([[0x2000], [0x3000]])

    def fake_memory_scanner(backend: object, needle: bytes) -> list[int]:
        return next(found_addresses)

    def fake_block_parser(backend: object, addr: int) -> list[dict[int, int]]:
        if addr == 0x2000:
            return [{114001: 4, 114002: 2}, {100010: 1}]
        return [{200001: 1, 200002: 2}]

    result = memory_scanner.scan_collection_detailed(
        use_helper=None,
        memory_scanner=fake_memory_scanner,
        block_parser=fake_block_parser,
        print_fn=lambda *args, **kwargs: None,
    )

    # Sollte trotzdem funktionieren (via PymemBackend)
    assert result is not None


def test_scan_collection_detailed_helper_returns_none_on_empty_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Helper-Pfad: leere DB → None."""
    monkeypatch.setattr(
        helper_client,
        "is_helper_available",
        lambda sock_path: True,
    )
    monkeypatch.setattr(
        memory_scanner,
        "load_card_database",
        lambda: {},
    )

    result = memory_scanner.scan_collection_detailed(
        use_helper=True,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result is None


def test_scan_collection_detailed_helper_returns_none_on_no_anchors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Helper-Pfad: keine Anker → None."""
    monkeypatch.setattr(
        helper_client,
        "is_helper_available",
        lambda sock_path: True,
    )
    monkeypatch.setattr(
        memory_scanner,
        "load_card_database",
        lambda: {114001: {"name": "Card A"}},
    )
    monkeypatch.setattr(
        memory_scanner,
        "get_user_anchors",
        lambda name_to_id, **kwargs: [],
    )

    result = memory_scanner.scan_collection_detailed(
        use_helper=True,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result is None


def test_scan_collection_helper_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """scan_collection() (Wrapper) funktioniert mit use_helper=True."""
    anchor_file = tmp_path / "last_anchors.json"
    monkeypatch.setattr(memory_scanner, "_anchor_file", lambda: anchor_file)
    monkeypatch.setattr(
        memory_scanner,
        "load_card_database",
        lambda: {
            114001: {"name": "Card A"},
            114002: {"name": "Card B"},
        },
    )
    monkeypatch.setattr(
        memory_scanner,
        "get_user_anchors",
        lambda name_to_id, **kwargs: [(114001, 4, "Card A")],
    )

    class FakeHelperBackend:
        def read_bytes(self, addr: int, size: int) -> bytes | None:
            return b"\x00" * size

        def iterate_writable_private_regions(self) -> list[tuple[int, int]]:
            return [(0x1000, 4096)]

        def iterate_readable_regions(self) -> tuple[list[tuple[int, int]], int | None]:
            return ([(0x1000, 4096)], None)

    monkeypatch.setattr(
        helper_client,
        "is_helper_available",
        lambda sock_path: True,
    )
    monkeypatch.setattr(
        helper_client,
        "HelperBackend",
        lambda sock_path: FakeHelperBackend(),
    )

    collection = memory_scanner.scan_collection(
        use_helper=True,
        print_fn=lambda *args, **kwargs: None,
    )

    # Keine Anker gefunden → None (weil FakeBackend keine Anker-Daten liefert)
    # Das ist ok — der Test prüft, dass der Wrapper ohne Exception durchläuft
    assert collection is None or isinstance(collection, dict)


def test_scan_collection_detailed_dedups_duplicate_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mehrere Anker im selben Array → block_parser liefert identische Blöcke.

    scan_collection_detailed() muss Duplikate in candidates entfernen, bevor
    max() den größten Block wählt. Sonst kann ein duplizierter Block fälschlich
    als 'größer' erscheinen oder redundante Arbeit entstehen.
    """
    anchor_file = tmp_path / "last_anchors.json"
    monkeypatch.setattr(memory_scanner, "_anchor_file", lambda: anchor_file)
    monkeypatch.setattr(
        memory_scanner,
        "load_card_database",
        lambda: {
            114001: {"name": "Card A"},
            114002: {"name": "Card B"},
        },
    )
    monkeypatch.setattr(
        memory_scanner,
        "get_user_anchors",
        lambda name_to_id, **kwargs: [(114001, 4, "Card A"), (114002, 2, "Card B")],
    )
    monkeypatch.setattr(
        helper_client,
        "is_helper_available",
        lambda sock_path: True,
    )
    monkeypatch.setattr(
        helper_client,
        "HelperBackend",
        lambda sock_path: FakeBackend(),
    )

    # Zwei Anker-Fundstellen, die DENSELBEN Speicherbereich parsen →
    # block_parser liefert für beide identische Blöcke (Duplikate).
    found_addresses = iter([[0x2000], [0x2000]])

    def fake_memory_scanner(backend: object, needle: bytes) -> list[int]:
        return next(found_addresses)

    def fake_block_parser(backend: object, addr: int) -> list[dict[int, int]]:
        # Identischer Block für beide Anker (Duplikat)
        return [{114001: 4, 114002: 2, 100010: 1}]

    result = memory_scanner.scan_collection_detailed(
        use_helper=True,
        memory_scanner=fake_memory_scanner,
        block_parser=fake_block_parser,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result is not None
    # Dedup: Collection enthält die Karten genau einmal, keine Duplikat-Artefakte
    assert result.collection == {114001: 4, 114002: 2, 100010: 1}
    assert result.validation["valid"] is True


def test_scan_collection_detailed_helper_with_custom_sock_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Benutzerdefinierter sock_path wird an HelperBackend weitergegeben."""
    captured_sock: list[str] = []

    monkeypatch.setattr(
        helper_client,
        "is_helper_available",
        lambda sock_path: True,
    )
    monkeypatch.setattr(
        helper_client,
        "HelperBackend",
        lambda sock_path: captured_sock.append(sock_path) or FakeBackend(),  # type: ignore[return-value]
    )
    monkeypatch.setattr(
        memory_scanner,
        "load_card_database",
        lambda: {114001: {"name": "Card A"}},
    )
    monkeypatch.setattr(
        memory_scanner,
        "get_user_anchors",
        lambda name_to_id, **kwargs: [],
    )

    memory_scanner.scan_collection_detailed(
        use_helper=True,
        sock_path="/tmp/custom-test.sock",
        print_fn=lambda *args, **kwargs: None,
    )

    assert captured_sock == ["/tmp/custom-test.sock"]


def test_scan_collection_detailed_helper_auto_detect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """use_helper=None erkennt Helper-Verfügbarkeit automatisch."""
    monkeypatch.setattr(
        helper_client,
        "is_helper_available",
        lambda sock_path: True,
    )
    monkeypatch.setattr(
        memory_scanner,
        "load_card_database",
        lambda: {},
    )

    # Sollte Helper-Pfad nehmen (is_helper_available=True)
    result = memory_scanner.scan_collection_detailed(
        use_helper=None,
        print_fn=lambda *args, **kwargs: None,
    )

    assert result is None  # wegen leerer DB, aber kein Fehler im Helper-Dispatch


class FakeBackend:
    """Dummy-Backend für sock_path-Tests."""

    def read_bytes(self, addr: int, size: int) -> bytes | None:
        return b"\x00" * size

    def iterate_writable_private_regions(self) -> list[tuple[int, int]]:
        return [(0x1000, 4096)]

    def iterate_readable_regions(self) -> tuple[list[tuple[int, int]], int | None]:
        return ([(0x1000, 4096)], None)
