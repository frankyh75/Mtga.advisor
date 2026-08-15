"""Unit-Tests für die 4 Community-Tool-Techniken in memory_scanner.py.

Technik 1: (arena_id, quantity)-Paar-Scan
Technik 2: Duplicate-Tracking in _extract_blocks
Technik 3: Gewichteter Score in _select_best_block
Technik 4: _validate_block mit known_ratio/duplicates/total Schwellen
"""

from __future__ import annotations

import struct

from scanner import memory_scanner


# ---------------------------------------------------------------------------
# Technik 1: (arena_id, quantity)-Paar-Scan
# ---------------------------------------------------------------------------


def test_pair_scan_pattern_is_8_bytes_not_4() -> None:
    """Der Paar-Scan erzeugt ein 8-Byte-Pattern (struct.pack('<II', aid, qty)),
    nicht nur 4 Bytes (arena_id allein)."""
    pattern = struct.pack("<II", 83896, 3)
    assert len(pattern) == 8
    # Die ersten 4 Bytes sind die arena_id
    assert struct.unpack("<I", pattern[:4])[0] == 83896
    # Die letzten 4 Bytes sind die quantity
    assert struct.unpack("<I", pattern[4:])[0] == 3


# ---------------------------------------------------------------------------
# Technik 2: Duplicate-Tracking in _extract_blocks
# ---------------------------------------------------------------------------


def _make_collection_bytes(pairs: list[tuple[int, int]], gap: int = 0) -> bytes:
    """Erzeugt rohe Speicherbytes aus (arena_id, qty)-Paaren."""
    data = b""
    for aid, qty in pairs:
        data += struct.pack("<II", aid, qty)
        if gap > 0:
            data += b"\x00\x00\x00\x00" * gap  # gap ints
    return data


def test_extract_blocks_tracks_duplicates() -> None:
    """Wenn eine arena_id mehrfach im Block vorkommt, wird duplicates gezählt."""
    # Block mit doppelten IDs am Anfang, dann genug unique IDs für min_block_size
    pairs = [
        (1000, 1), (2000, 2), (1000, 3),  # 2 duplicates (1000 doppelt)
        (3000, 1), (2000, 1),  # 1 more duplicate (2000 doppelt) → 3 total
    ]
    # Fülle auf >50 unique IDs auf, damit der Block die min_block_size Schwelle erreicht
    pairs.extend([(5000 + i, 1) for i in range(55)])
    data = _make_collection_bytes(pairs)
    blocks = memory_scanner._extract_blocks(data, stride_w=2, off_w=0)
    assert len(blocks) >= 1
    block, dupes = blocks[0]
    # 1000, 2000, 3000 + 55 unique = 58 unique IDs
    assert len(block) == 58
    assert dupes == 2  # 1000 doppelt, 2000 doppelt → 2 duplicates


def test_extract_blocks_no_duplicates_clean_block() -> None:
    """Ein sauberer Block ohne doppelte IDs hat duplicates=0."""
    pairs = [(1000 + i, 1) for i in range(60)]
    data = _make_collection_bytes(pairs)
    blocks = memory_scanner._extract_blocks(data, stride_w=2, off_w=0)
    assert len(blocks) >= 1
    block, dupes = blocks[0]
    assert len(block) == 60
    assert dupes == 0


def test_extract_blocks_max_gap_splits_blocks() -> None:
    """Wenn mehr als MAX_GAP aufeinanderfolgende Paare nicht matchen,
    wird der Block abgeschlossen."""
    # Erster Block: 60 gültige Paare
    pairs1 = [(1000 + i, 1) for i in range(60)]
    # Gap: 70 ungültige Paare (Werte außerhalb des Filters)
    gap = [(999, 0)] * 70  # k<MIN_ARENA_ID oder v<MIN_QTY → ungültig
    # Zweiter Block: 55 gültige Paare
    pairs2 = [(5000 + i, 1) for i in range(55)]

    all_pairs = pairs1 + gap + pairs2
    data = _make_collection_bytes(all_pairs)
    blocks = memory_scanner._extract_blocks(data, stride_w=2, off_w=0)
    # Sollte mindestens 2 Blöcke finden (gap > MAX_GAP=64)
    assert len(blocks) >= 2


def test_extract_blocks_min_block_size_filters_small() -> None:
    """Blöcke mit < MIN_BLOCK_SIZE Einträgen werden verworfen."""
    # Nur 30 gültige Paare → unter MIN_BLOCK_SIZE=50
    pairs = [(1000 + i, 1) for i in range(30)]
    # Gefolgt von genug ungültigen, um den Block zu beenden
    gap = [(999, 0)] * 70
    data = _make_collection_bytes(pairs + gap)
    blocks = memory_scanner._extract_blocks(data, stride_w=2, off_w=0)
    # Kein Block sollte die min_block_size Schwelle erreichen
    assert len(blocks) == 0


def test_extract_blocks_duplicate_keeps_first_quantity() -> None:
    """Bei doppelten arena_ids behält der Block die ERSTE gefundene Menge."""
    pairs = [(1000, 5), (2000, 3), (1000, 9)]  # 1000 doppelt, erste=5, zweite=9
    data = _make_collection_bytes(pairs + [(3000 + i, 1) for i in range(50)])
    blocks = memory_scanner._extract_blocks(data, stride_w=2, off_w=0)
    assert len(blocks) >= 1
    block, dupes = blocks[0]
    assert block[1000] == 5  # erste Menge gewinnt
    assert dupes == 1


def test_find_blocks_with_dupes_returns_tuples() -> None:
    """find_blocks_with_dupes liefert (block_dict, dupes)-Tupel."""
    pairs = [(1000 + i, 1) for i in range(60)]
    data = _make_collection_bytes(pairs)

    class FakeBackend:
        def read_bytes(self, addr: int, size: int) -> bytes | None:
            # Genug Daten, padding vor und nach den Paaren
            pad = b"\x00" * 1024
            return pad + data + pad

    result = memory_scanner.find_blocks_with_dupes(FakeBackend(), 2048)
    assert len(result) >= 1
    for item in result:
        assert isinstance(item, tuple)
        assert len(item) == 2
        assert isinstance(item[0], dict)
        assert isinstance(item[1], int)


def test_find_blocks_backward_compat_returns_only_dicts() -> None:
    """find_blocks (Wrapper) liefert nur Block-Dicts, keine Tupel."""
    pairs = [(1000 + i, 1) for i in range(60)]
    data = _make_collection_bytes(pairs)

    class FakeBackend:
        def read_bytes(self, addr: int, size: int) -> bytes | None:
            pad = b"\x00" * 1024
            return pad + data + pad

    result = memory_scanner.find_blocks(FakeBackend(), 2048)
    assert len(result) >= 1
    for item in result:
        assert isinstance(item, dict)  # nur dict, kein tuple


# ---------------------------------------------------------------------------
# Technik 3: Gewichteter Score in _select_best_block
# ---------------------------------------------------------------------------


def test_select_best_block_weighted_score_known_ids() -> None:
    """Block mit hoher known_ratio schlägt Block mit niedriger known_ratio,
    selbst wenn letzterer mehr Anker exakt hat."""
    # Block A: alle IDs bekannt, 3/5 Anker exakt, 3/5 present
    known_ids = {100, 200, 300, 600, 700, 800, 900, 1000}
    block_a = {100: 3, 200: 4, 300: 4, 600: 1, 700: 1, 800: 1, 900: 1, 1000: 1}
    # Block B: 5/5 Anker present, 4/5 exakt, aber 90% IDs unbekannt
    block_b = {100: 3, 200: 4, 300: 4, 400: 4, 500: 99}
    # Fülle block_b mit vielen unbekannten IDs auf (known_ratio sinkt)
    block_b.update({90000 + i: 1 for i in range(50)})  # 90000-90049 nicht in known_ids
    anchors = [(100, 3, "A"), (200, 4, "B"), (300, 4, "C"), (400, 4, "D"), (500, 1, "E")]

    # Score A: known=1.0, exact=3/5=0.6, present=3/5=0.6
    #   = 1.0*0.35 + 0.6*0.35 + 0.6*0.10 + (8/5000)*0.10 + 1.0*0.10
    #   = 0.35 + 0.21 + 0.06 + 0.00016 + 0.10 = 0.720
    # Score B: known=5/55=0.091, exact=4/5=0.8, present=5/5=1.0
    #   = 0.091*0.35 + 0.8*0.35 + 1.0*0.10 + (55/5000)*0.10 + 1.0*0.10
    #   = 0.032 + 0.28 + 0.10 + 0.0011 + 0.10 = 0.513
    # → block_a gewinnt (0.720 > 0.513)
    result = memory_scanner._select_best_block(
        [block_a, block_b],
        anchors,
        known_ids=known_ids,
        print_fn=lambda *a, **k: None,
    )
    assert result == block_a


def test_select_best_block_dupes_penalty() -> None:
    """Ein Block mit vielen Duplikaten wird durch (1-dup_ratio) penalisiert."""
    known_ids = {100, 200, 300, 400, 500, 600, 700, 800}
    # Beide Blöcke haben dieselben Anker, aber einer hat Duplikate
    block_clean = {100: 3, 200: 4, 300: 4, 400: 4, 500: 1, 600: 1, 700: 1, 800: 1}
    block_dirty = {100: 3, 200: 4, 300: 4, 400: 4, 500: 1, 600: 1, 700: 1, 800: 1}
    anchors = [(100, 3, "A"), (200, 4, "B"), (300, 4, "C"), (400, 4, "D"), (500, 1, "E")]

    # block_dirty als (dict, dupes=10) → hoher dup_ratio
    result = memory_scanner._select_best_block(
        [block_clean, (block_dirty, 10)],
        anchors,
        known_ids=known_ids,
        print_fn=lambda *a, **k: None,
    )
    # clean hat dupes=0, dirty hat dupes=10 → clean gewinnt durch (dupes==0) Sortierschlüssel
    assert result == block_clean


def test_select_best_block_size_score_tiebreak() -> None:
    """Bei gleichem known_ratio und gleichen Ankern gewinnt der größere Block."""
    known_ids = set(range(100, 1100))
    # Beide Blöcke haben alle 5 Anker exakt und 100% known_ratio
    small = {100: 3, 200: 4, 300: 4, 400: 4, 500: 1}
    big = {100: 3, 200: 4, 300: 4, 400: 4, 500: 1}
    big.update({600 + i: 1 for i in range(500)})  # 505 entries total
    anchors = [(100, 3, "A"), (200, 4, "B"), (300, 4, "C"), (400, 4, "D"), (500, 1, "E")]

    result = memory_scanner._select_best_block(
        [small, big],
        anchors,
        known_ids=known_ids,
        print_fn=lambda *a, **k: None,
    )
    assert result == big


def test_select_best_block_accepts_tuple_candidates() -> None:
    """_select_best_block akzeptiert (dict, dupes)-Tupel als Kandidaten."""
    known_ids = {100, 200, 300, 400, 500}
    block = {100: 3, 200: 4, 300: 4, 400: 4, 500: 1}
    anchors = [(100, 3, "A"), (200, 4, "B"), (300, 4, "C"), (400, 4, "D"), (500, 1, "E")]

    result = memory_scanner._select_best_block(
        [(block, 0)],
        anchors,
        known_ids=known_ids,
        print_fn=lambda *a, **k: None,
    )
    assert result == block


def test_select_best_block_no_known_ids_backward_compat() -> None:
    """Ohne known_ids-Parameter fällt known_ratio auf 1.0 (alle bekannt).
    Alte Tests ohne known_ids müssen weiterhin funktionieren."""
    block = {100: 3, 200: 4, 600: 1}
    anchors = [(100, 3, "A"), (200, 4, "B")]

    result = memory_scanner._select_best_block(
        [block],
        anchors,
        print_fn=lambda *a, **k: None,
    )
    assert result == block


# ---------------------------------------------------------------------------
# Technik 4: _validate_block mit known_ratio/duplicates/total Schwellen
# ---------------------------------------------------------------------------


def test_validate_block_valid_clean_block() -> None:
    """Ein sauberer Block mit 100% known_ratio und keine Duplikate ist valid."""
    block = {1000 + i: 1 for i in range(60)}
    known_ids = set(block.keys())
    assert memory_scanner._validate_block(block, duplicates=0, known_ids=known_ids) is True


def test_validate_block_rejects_too_small() -> None:
    """Block mit < 10 Einträgen wird abgelehnt."""
    block = {1000: 1, 2000: 2, 3000: 1}
    known_ids = {1000, 2000, 3000}
    assert memory_scanner._validate_block(block, duplicates=0, known_ids=known_ids) is False


def test_validate_block_rejects_too_large() -> None:
    """Block mit > 100.000 Einträgen wird abgelehnt."""
    block = {1000 + i: 1 for i in range(100_001)}
    known_ids = set(block.keys())
    assert memory_scanner._validate_block(block, duplicates=0, known_ids=known_ids) is False


def test_validate_block_rejects_low_known_ratio() -> None:
    """Block mit known_ratio < 0.30 wird abgelehnt."""
    block = {1000 + i: 1 for i in range(100)}
    # Nur 10% der IDs sind bekannt
    known_ids = {1000, 1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009}
    assert memory_scanner._validate_block(block, duplicates=0, known_ids=known_ids) is False


def test_validate_block_accepts_threshold_known_ratio() -> None:
    """Block mit known_ratio >= 0.30 wird akzeptiert (boundary test)."""
    block = {1000 + i: 1 for i in range(100)}
    # Genau 30% bekannt → ratio = 0.30,刚好 >= 0.30
    known_ids = {1000 + i for i in range(30)}
    assert memory_scanner._validate_block(block, duplicates=0, known_ids=known_ids) is True


def test_validate_block_rejects_high_total() -> None:
    """Block mit total > 500.000 wird abgelehnt."""
    block = {1000 + i: 400 for i in range(2000)}  # total = 800.000
    known_ids = set(block.keys())
    assert memory_scanner._validate_block(block, duplicates=0, known_ids=known_ids) is False


def test_validate_block_rejects_high_duplicates() -> None:
    """Block mit duplicates > max(25, len*0.05) wird abgelehnt."""
    block = {1000 + i: 1 for i in range(100)}  # len=100, threshold=max(25, 5)=25
    known_ids = set(block.keys())
    assert memory_scanner._validate_block(block, duplicates=30, known_ids=known_ids) is False


def test_validate_block_accepts_duplicate_threshold() -> None:
    """Block mit duplicates <= max(25, len*0.05) wird akzeptiert (boundary)."""
    block = {1000 + i: 1 for i in range(100)}  # threshold = max(25, 5) = 25
    known_ids = set(block.keys())
    assert memory_scanner._validate_block(block, duplicates=25, known_ids=known_ids) is True


def test_validate_block_no_known_ids_skips_ratio_check() -> None:
    """Ohne known_ids wird die known_ratio-Prüfung übersprungen."""
    block = {1000 + i: 1 for i in range(60)}
    assert memory_scanner._validate_block(block, duplicates=0, known_ids=None) is True


def test_validate_block_empty_rejected() -> None:
    """Leerer Block wird abgelehnt."""
    assert memory_scanner._validate_block({}, duplicates=0, known_ids=None) is False


def test_validate_block_large_block_duplicate_threshold() -> None:
    """Bei großen Blöcken skaliert die duplicate-Schwelle mit len*0.05."""
    # len=1000, threshold = max(25, 50) = 50
    block = {1000 + i: 1 for i in range(1000)}
    known_ids = set(block.keys())
    # 50 Duplikate → genau an der Schwelle → valid
    assert memory_scanner._validate_block(block, duplicates=50, known_ids=known_ids) is True
    # 51 Duplikate → über der Schwelle → invalid
    assert memory_scanner._validate_block(block, duplicates=51, known_ids=known_ids) is False