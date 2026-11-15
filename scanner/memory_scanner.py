"""Memory-Scanner: Findet die Kartensammlung im MTGA-Prozess-Speicher.

Basiert auf NthPhantom10/MTGA-collection-exporter (v2.0).
Portiert von Windows pymem → macOS pymem-osx.
"""

from __future__ import annotations

import struct
import sys
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from pymem import Pymem
except ImportError:  # pragma: no cover - fallback for environments without pymem
    class Pymem:  # type: ignore[no-redef]
        pass

from .card_database import load_card_database
from .macos_paths import (
    get_default_cache_dir,
    get_macos_mtga_process_name,
    get_macos_mtga_process_names,
)
from .pattern_scanner import MemoryBackend, PymemBackend, scan_process_memory, scan_process_memory_with_stats
from .pattern_scanner import ScanStats, scan_process_memory_many_with_stats

# ---------------------------------------------------------------------------
# Scan-Konfiguration (aus Community-Tool v3.4 übernommen)
# ---------------------------------------------------------------------------
SCAN_RANGE_MB = 8
READ_CHUNK_SIZE = 256 * 1024
MIN_READ_SIZE = 64 * 1024
MIN_ARENA_ID = 1000
MAX_ARENA_ID = 900_000
MIN_QTY = 1
MAX_QTY = 400
MIN_BLOCK_SIZE = 50
MAX_GAP = 64
STRIDES_WORDS = (2, 3, 4)

# Schreibschutz-Konstanten für write_collection_artifacts
MAX_COLLECTION_ENTRIES = 100_000  # Ceiling-Guard: echte Collection ~8k, >100k = Müll
MIN_KNOWN_RATIO = 0.30            # Quality-Guard: <30% bekannte IDs = nicht real

# ---------------------------------------------------------------------------
# Vorgegebene Anker (Standard-Set)
# ---------------------------------------------------------------------------
# Ohne Anker kann der Scanner die echte Collection nicht von Speichermüll
# unterscheiden. Statt bei jedem Lauf 5 Karten abzufragen, ist hier ein
# bewährtes Set hinterlegt: Karten, deren Besitz im Scan vom 2026-08-02 mit
# 5/5 exakten Ankern bestätigt wurde (valid=true).
#
# Vorrang hat immer ~/.mtga_advisor/last_anchors.json: wer andere Karten
# will, lässt sich die Datei einmal interaktiv schreiben. Dieses Set ist nur
# der Fallback, damit der Scan NIE nach Ankern fragt.
DEFAULT_ANCHORS: tuple[tuple[int, int, str], ...] = (
    # Live ground-truth anchors from the 2026-11-15 collection/deck validation.
    (105645, 1, "TVA"),
    (105771, 1, "TVA Bureaucrat"),
    (105653, 1, "Virtual Assistant"),
    (104977, 3, "We Say Thee Nay!"),
    (104945, 1, "Echo, Perceptive Prodigy"),
)


@dataclass(frozen=True)
class MemoryScanResult:
    collection: dict[int, int]
    anchors: list[tuple[int, int, str]]
    anchor_matches: dict[int, int]
    validation: dict[str, Any]
    scan_stats: ScanStats | None = None
    read_coverage: dict[str, Any] = field(default_factory=dict)


def _anchor_file() -> Path:
    return get_default_cache_dir() / "last_anchors.json"


def _read_window_with_coverage(
    backend: MemoryBackend, block_start: int, scan_bytes: int
) -> tuple[bytearray, list[tuple[int, int]]]:
    """Liest ein Anker-Fenster fehlertolerant und liefert seine Abdeckung.

    ``mach_vm_read_overwrite`` ist atomar: ein ungültiges Byte lässt den
    ganzen Read fehlschlagen. Deshalb werden große Chunks bei einem Fehler
    bis auf ``MIN_READ_SIZE`` geteilt. Nicht gelesene Bereiche werden nur im
    Puffer repräsentiert; sie werden später nie an den Block-Parser gegeben.
    """
    buffer = bytearray(scan_bytes)
    ranges: list[tuple[int, int]] = []

    def read_piece(offset: int, size: int) -> None:
        try:
            data = backend.read_bytes(block_start + offset, size)
        except Exception:
            data = None
        if data:
            read_size = min(len(data), size)
            buffer[offset:offset + read_size] = data[:read_size]
            ranges.append((offset, offset + read_size))
            if read_size < size:
                read_piece(offset + read_size, size - read_size)
            return
        if size > MIN_READ_SIZE:
            left_size = max(MIN_READ_SIZE, size // 2)
            left_size = min(left_size, size - MIN_READ_SIZE)
            read_piece(offset, left_size)
            read_piece(offset + left_size, size - left_size)

    for offset in range(0, scan_bytes, READ_CHUNK_SIZE):
        read_piece(offset, min(READ_CHUNK_SIZE, scan_bytes - offset))

    merged: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return buffer, merged


def _find_blocks_with_dupes_and_coverage(
    backend: MemoryBackend, addr: int
) -> tuple[list[tuple[dict[int, int], int]], dict[str, Any]]:
    """Wie :func:`find_blocks_with_dupes`, zusätzlich mit Read-Coverage."""
    scan_bytes = SCAN_RANGE_MB * 1024 * 1024
    block_start = max(0, addr - scan_bytes // 2)
    data, ranges = _read_window_with_coverage(backend, block_start, scan_bytes)
    bytes_read = sum(end - start for start, end in ranges)
    coverage = {
        "address": addr,
        "windowStart": block_start,
        "bytesRead": bytes_read,
        "windowBytes": scan_bytes,
        "complete": bytes_read == scan_bytes,
    }
    if not ranges:
        return [], coverage

    results: list[tuple[dict[int, int], int]] = []
    for start, end in ranges:
        # Ein Kandidat darf nie über eine ungelesene Lücke hinweg entstehen.
        # Bei einer Lücke am Rand wird der potenziell abgeschnittene erste bzw.
        # letzte Block verworfen; abgeschlossene innere Blöcke bleiben erhalten.
        segment = bytes(data[start:end])
        for stride_w in STRIDES_WORDS:
            for off_w in range(stride_w):
                adjusted_off = (off_w - (start // 4)) % stride_w
                candidates = _extract_blocks(segment, stride_w, adjusted_off)
                # Ein Block an einer ungelesenen Grenze kann abgeschnitten sein.
                # Nur Kandidaten behalten, die auch nach dem Ausschneiden einer
                # kompletten max-gap-Zone noch extrahiert werden: dann liegt der
                # gesamte Kandidat sicher innerhalb des gelesenen Intervalls.
                edge_margin = (MAX_GAP + 1) * stride_w * 4
                safe_start = edge_margin if start > 0 else 0
                safe_end = len(segment) - edge_margin if end < scan_bytes else len(segment)
                if safe_end <= safe_start:
                    candidates = []
                elif safe_start or safe_end != len(segment):
                    safe_off = (off_w - ((start + safe_start) // 4)) % stride_w
                    safe_candidates = _extract_blocks(
                        segment[safe_start:safe_end], stride_w, safe_off
                    )
                    safe_keys = {(tuple(sorted(block.items())), dupes) for block, dupes in safe_candidates}
                    candidates = [
                        candidate for candidate in candidates
                        if (tuple(sorted(candidate[0].items())), candidate[1]) in safe_keys
                    ]
                results.extend(candidates)
    return results, coverage


def find_blocks_with_dupes(
    backend: MemoryBackend, addr: int
) -> list[tuple[dict[int, int], int]]:
    """Liest Speicher um eine Adresse und extrahiert (block, duplicates)-Paare.

    Analog zum Community-Tool _find_blocks + _scan_region + _extract:
    - Liest SCAN_RANGE_MB um die Fundstelle
    - Versucht mehrere Strides (2, 3, 4 Wörter) und Offsets
    - Trackt doppelte arena_ids innerhalb eines Blocks (→ dirty memory)
    - max_gap / min_block_size Schwellen beenden Blöcke

    Args:
        backend: MemoryBackend-Instanz
        addr: Fundstelle einer Anker-Karte

    Returns:
        Liste von (block_dict, duplicates_count)-Tupeln
    """
    results, _coverage = _find_blocks_with_dupes_and_coverage(backend, addr)
    return results


def find_blocks(backend: MemoryBackend, addr: int) -> list[dict[int, int]]:
    """Backward-kompatibler Wrapper: liefert nur Block-Dicts (ohne duplicates).

    Behält die ursprüngliche Signatur für bestehende Tests und CLI.
    Die Dedup-Logik (Schutz vor verfälschtem max()) bleibt erhalten.

    Args:
        backend: MemoryBackend-Instanz
        addr: Fundstelle einer Anker-Karte

    Returns:
        Liste von Karten-Dictionaries (grpId → quantity)
    """
    pairs = find_blocks_with_dupes(backend, addr)
    return [block for block, _dupes in pairs]


def _extract_blocks(
    data: bytes, stride_w: int, off_w: int
) -> list[tuple[dict[int, int], int]]:
    """Extrahiert (grpId, qty)-Paare aus rohen Speicherbytes.

    Technik 2 (Duplicate-Tracking): Wenn eine arena_id mehrfach im selben
    Block vorkommt, wird sie NICHT überschrieben (erste Vorkommen gewinnt)
    und `duplicates` wird inkrementiert. Blöcke mit vielen Duplikaten
    deuten auf dirty memory (falscher Speicherbereich).

    Technik 2 (max_gap + min_block_size): Wenn mehr als MAX_GAP aufeinander-
    folgende (k,v)-Paare nicht dem Filter entsprechen, wird der Block
    abgeschlossen. Nur Blöcke mit >= MIN_BLOCK_SIZE Einträgen werden behalten.

    Args:
        data: Rohe Speicherbytes
        stride_w: Stride in Wörtern (2, 3, oder 4)
        off_w: Word-Offset für diese Iteration

    Returns:
        Liste von (block_dict, duplicates_count)-Tupeln
    """
    n_ints = len(data) // 4
    if n_ints < 2:
        return []

    try:
        ints = struct.unpack_from(f"<{n_ints}I", data)
    except struct.error:
        return []

    blocks: list[tuple[dict[int, int], int]] = []
    current: dict[int, int] = {}
    duplicates = 0
    misses = 0
    i = off_w

    while i + 1 < n_ints:
        k, v = ints[i], ints[i + 1]

        if MIN_ARENA_ID <= k < MAX_ARENA_ID and MIN_QTY <= v <= MAX_QTY:
            if k in current:
                duplicates += 1
            else:
                current[k] = v
            misses = 0
        else:
            misses += 1
            if misses > MAX_GAP:
                if len(current) >= MIN_BLOCK_SIZE:
                    blocks.append((current, duplicates))
                current = {}
                duplicates = 0
                misses = 0

        i += stride_w

    if len(current) >= MIN_BLOCK_SIZE:
        blocks.append((current, duplicates))

    return blocks


def write_collection_artifacts(
    collection: dict[int, int],
    output_dir: Path,
    *,
    scan_result: MemoryScanResult | None = None,
    known_ids: set[int] | None = None,
    method: str | None = None,
    path: Sequence[str] | None = None,
    extra_warnings: Sequence[str] | None = None,
) -> tuple[Path, Path]:
    """Schreibt Memory-Scan-Ergebnisse im bestehenden Artefaktformat.

    Robuster Schreibschutz (konservativ — lieber alt behalten als verlieren):

    - **Grundregel (Teil 1):** Ein Scan mit ``valid: false`` überschreibt
      **niemals** eine bestehende Collection (egal ob die bestehende valid
      ist oder nicht).  Ein invalider Scan ist nie besser als der aktuelle
      Zustand.  Ausnahme: kein bestehendes Artefakt → dann schreiben
      (besser als nichts, aber mit Warnung).

    - **Ceiling-Guard (Teil 2):** Ein Block mit > ``MAX_COLLECTION_ENTRIES``
      (> 100.000) Einträgen ist mit Sicherheit Speichermüll (Katalog-Daten,
      Deck-Definitionen etc.) — die echte Collection hat ~8.000 unique Karten.
      Wenn eine bestehende Collection existiert → nicht überschreiben.

    - **Known-Ratio-Guard (Teil 3):** Wenn der neue Block eine
      known_ratio < ``MIN_KNOWN_RATIO`` (< 30%) hat und eine bestehende
      Collection existiert → nicht überschreiben.  Ein Block mit 4.8%
      bekannten IDs ist kein echter Collection-Block.

    - **Mengen-Sanity-Check:** Wenn der neue Scan ``valid: true`` liefert,
      aber WENIGER unique Karten hat als die bestehende Collection → nicht
      überschreiben.  Karten können nicht weniger werden.

    - **JSON-Backup:** Vor dem Überschreiben einer bestehenden Collection wird
      diese als ``collection.backup.json`` gesichert.

    Ein legitimer erster Scan (keine bestehende Datei) schreibt immer.  Ein
    valider neuer Scan mit >= bestehender Kartenanzahl, der alle Guards
    passiert, überschreibt (echte Aktualisierung).

    Args:
        collection: Die gescannte Collection als {grpId: quantity}.
        output_dir: Ausgabe-Verzeichnis für collection.json + run-report.json.
        scan_result: Das vollständige MemoryScanResult (für Validation + Stats).
        known_ids: Set aller bekannten arenaIds aus der Karten-DB.
            Wenn None, wird der known_ratio-Guard übersprungen (backward-kompatibel).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    collection_path = output_dir / "collection.json"
    run_report_path = output_dir / "run-report.json"

    started_at = _iso_now()
    validation = scan_result.validation if scan_result is not None else validate_collection(collection)
    warnings = list(validation.get("warnings", []))
    if extra_warnings:
        warnings.extend(str(warning) for warning in extra_warnings)
    artifact_metadata = {
        key: value for key, value in {
            "method": method,
            "path": list(path) if path is not None else None,
            "warnings": warnings if method is not None else None,
        }.items() if value is not None
    }

    new_valid = validation.get("valid", False)
    new_count = len(collection)

    # --- Bestehende Collection einmal lesen (für alle Schreibschutz-Checks) ---
    existing = _read_existing_collection(collection_path) if collection_path.exists() else None
    existing_valid = False
    existing_count = 0
    if existing is not None:
        existing_valid = (
            existing.get("diagnostics", {})
            .get("validation", {})
            .get("valid", False)
        )
        existing_count = len(existing.get("cards", {}))

    # --- Known-Ratio berechnen (für Quality-Guard) ---
    new_known_ratio: float | None = None
    if known_ids is not None and new_count > 0:
        known_in_block = sum(1 for cid in collection if cid in known_ids)
        new_known_ratio = known_in_block / new_count

    # ===================================================================
    # SCHREIBSCHUTZ-LOGIK — konservativ, mehre unabhängige Guards
    # ===================================================================
    # Grundregel: ein invalider Scan überschreibt NIE eine bestehende Collection.
    # Ceiling-Guard: >100k Einträge = Müll.
    # Known-Ratio-Guard: <30% bekannte IDs = nicht die echte Collection.
    # Mengen-Sanity: weniger Karten als bestehend → nicht überschreiben.
    #
    # Jeder Guard greift nur wenn eine bestehende Collection existiert
    # (erster Scan schreibt immer, auch wenn invalid — besser als nichts).
    # ===================================================================
    write_protect_reason: str | None = None

    if existing is not None:
        # --- Teil 1 (Grundregel): invalider Scan überschreibt NIE ---
        if not new_valid:
            write_protect_reason = (
                f"existing collection ({existing_count} cards, valid={existing_valid}) "
                f"NOT overwritten by invalid scan ({new_count} cards, valid={new_valid}) "
                f"— an invalid scan is never better than the current state"
            )
        # --- Teil 2 (Ceiling-Guard): absurd großer Block ---
        elif new_count > MAX_COLLECTION_ENTRIES:
            write_protect_reason = (
                f"existing collection ({existing_count} cards) NOT overwritten by "
                f"absurdly large block ({new_count} entries > {MAX_COLLECTION_ENTRIES}) "
                f"— not a real collection (memory junk / catalog data)"
            )
        # --- Teil 3 (Known-Ratio-Guard): sehr niedrige known_ratio ---
        elif new_known_ratio is not None and new_known_ratio < MIN_KNOWN_RATIO:
            write_protect_reason = (
                f"existing collection ({existing_count} cards) NOT overwritten by "
                f"low-quality block ({new_count} entries, known_ratio={new_known_ratio:.1%} "
                f"< {MIN_KNOWN_RATIO:.0%}) — not a real collection block"
            )
        # --- Mengen-Sanity-Check: Karten können nicht weniger werden ---
        elif new_count < existing_count:
            write_protect_reason = (
                f"existing collection ({existing_count} cards) NOT overwritten "
                f"by new scan with fewer unique cards ({new_count} cards, valid={new_valid}) "
                f"— cards cannot decrease"
            )

    if write_protect_reason is not None:
        warnings.append(f"write-protected: {write_protect_reason}")
        run_report_payload = {
            "schema": "run-report.v1",
            "runId": f"run-{started_at}",
            "startedAt": started_at,
            "finishedAt": _iso_now(),
            "source": "memory-scan",
            "logs": [
                f"WARNING: {write_protect_reason}. "
                f"Existing collection ({existing_count} cards) "
                f"was kept — not overwritten."
            ],
            "outputs": {
                "collection": collection_path.as_posix(),
                "rawSamples": None,
            },
            "summary": {
                "cardsCount": new_count,
                "totalCards": sum(collection.values()),
                "wildcardsIncluded": False,
                "valid": new_valid,
                "writeProtected": True,
                "existingCardsCount": existing_count,
                "knownRatio": round(new_known_ratio, 4) if new_known_ratio is not None else None,
            },
            "diagnostics": {
                "completeness": {
                    "cards": "incomplete",
                    "wildcards": "unknown",
                    "source": "incomplete",
                },
                "warnings": warnings,
                "evidence": ["memory-scan"],
                "validation": validation,
                "readCoverage": scan_result.read_coverage if scan_result is not None else {},
            },
        }
        scan_payload: dict[str, Any] | None = None
        if scan_result is not None and scan_result.scan_stats is not None:
            stats = scan_result.scan_stats
            scan_payload = {
                "regions": stats.regions,
                "bytesScanned": stats.bytes_scanned,
                "readFailures": stats.read_failures,
                "matches": stats.matches,
                "regionStop": stats.region_error,
                "anchorMatches": {str(card_id): count for card_id, count in sorted(scan_result.anchor_matches.items())},
                "anchors": [
                    {"arenaId": card_id, "name": name, "expectedQuantity": quantity}
                    for card_id, quantity, name in scan_result.anchors
                ],
            }
        if scan_payload is not None:
            run_report_payload["scan"] = scan_payload
        run_report_payload.update(artifact_metadata)
        _write_json(run_report_path, run_report_payload)
        return collection_path, run_report_path

    # --- JSON-Backup der alten Collection vor dem Überschreiben ---
    if collection_path.exists():
        backup_path = output_dir / "collection.backup.json"
        try:
            backup_path.write_bytes(collection_path.read_bytes())
        except OSError:
            pass  # non-fatal — Backup-Fehler soll den Scan nicht blockieren

    # --- Erster Scan mit invalidem Ergebnis → Warnung (besser als nichts) ---
    if existing is None and not new_valid:
        warnings.append(
            "first scan is invalid but written — no existing collection to protect"
        )

    collection_payload = {
        "schema": "collection.v1",
        "source": "memory-scan",
        "cards": {str(card_id): count for card_id, count in sorted(collection.items())},
        "wildcards": {},
        "diagnostics": {
            "completeness": {
                "cards": "complete",
                "wildcards": "unknown",
                "source": "complete",
            },
            "warnings": warnings,
            "evidence": ["memory-scan"],
            "validation": validation,
            "readCoverage": scan_result.read_coverage if scan_result is not None else {},
        },
    }
    scan_payload: dict[str, Any] | None = None
    if scan_result is not None and scan_result.scan_stats is not None:
        stats = scan_result.scan_stats
        scan_payload = {
            "regions": stats.regions,
            "bytesScanned": stats.bytes_scanned,
            "readFailures": stats.read_failures,
            "matches": stats.matches,
            "regionStop": stats.region_error,
            "anchorMatches": {str(card_id): count for card_id, count in sorted(scan_result.anchor_matches.items())},
            "anchors": [
                {"arenaId": card_id, "name": name, "expectedQuantity": quantity}
                for card_id, quantity, name in scan_result.anchors
            ],
        }
    run_report_payload = {
        "schema": "run-report.v1",
        "runId": f"run-{started_at}",
        "startedAt": started_at,
        "finishedAt": _iso_now(),
        "source": "memory-scan",
        "logs": [],
        "outputs": {
            "collection": collection_path.as_posix(),
            "rawSamples": None,
        },
        "summary": {
            "cardsCount": len(collection),
            "totalCards": sum(collection.values()),
            "wildcardsIncluded": False,
            "valid": validation.get("valid", False),
        },
        "diagnostics": collection_payload["diagnostics"],
    }
    if scan_payload is not None:
        run_report_payload["scan"] = scan_payload
    collection_payload.update(artifact_metadata)
    run_report_payload.update(artifact_metadata)

    _write_json(collection_path, collection_payload)
    _write_json(run_report_path, run_report_payload)
    return collection_path, run_report_path


def _read_existing_collection(path: Path) -> dict[str, Any] | None:
    """Lese eine bestehende collection.json sicher (gibt None bei Fehler)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_collection(
    collection: dict[int, int],
    *,
    db: dict[int, dict[str, Any]] | None = None,
    anchors: Sequence[tuple[int, int, str]] | None = None,
) -> dict[str, Any]:
    """Validiert den Memory-Scan-Export defensiv für Reports und CLI."""
    errors: list[str] = []
    warnings: list[str] = []

    invalid_quantities = {
        str(card_id): quantity
        for card_id, quantity in collection.items()
        if not isinstance(card_id, int) or not isinstance(quantity, int) or quantity < 1 or quantity > 400
    }
    if invalid_quantities:
        errors.append("invalid-quantities")

    unknown_ids: list[int] = []
    if db is not None:
        unknown_ids = sorted(card_id for card_id in collection if card_id not in db)
        if unknown_ids:
            warnings.append("unknown-card-ids")

    anchor_results: list[dict[str, Any]] = []
    if anchors:
        for card_id, expected, name in anchors:
            actual = collection.get(card_id)
            ok = actual == expected
            if actual is None:
                # Anker-Karte komplett fehlend → error (Block ist nicht die Collection)
                errors.append("anchor-missing")
            elif not ok:
                # Anker-Karte vorhanden, aber Menge abweicht → warning
                # (Block ist wahrscheinlich die echte Collection, Mengen-Abweichung
                # kann durch Parser-Ungenauigkeit oder View-Status entstehen)
                warnings.append("anchor-qty-mismatch")
            anchor_results.append(
                {
                    "arenaId": card_id,
                    "name": name,
                    "expected": expected,
                    "actual": actual,
                    "ok": ok,
                }
            )

    if not collection:
        errors.append("empty-collection")

    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "cardsCount": len(collection),
        "totalCards": sum(collection.values()),
        "invalidQuantities": invalid_quantities,
        "unknownCardIdsCount": len(unknown_ids),
        "unknownCardIds": [str(card_id) for card_id in unknown_ids[:50]],
        "unknownCardIdsWithCounts": {
            str(card_id): collection[card_id]
            for card_id in unknown_ids
        },
        "unknownCardIdsTruncated": len(unknown_ids) > 50,
        "anchors": anchor_results,
    }


def _normalize_anchor(entry: Any) -> tuple[int, int, str] | None:
    """Bringt einen Anker-Eintrag in die Form ``(grpId, quantity, name)``.

    Nötig, weil die Anker-Datei JSON ist: Tupel kommen dort als Listen
    zurück, und ältere/abgewandelte Dateien können dicts enthalten
    (``{"arenaId": ...}``). Ohne Normalisierung wäre der Rückgabetyp
    gelogen und nachgelagerter Code müsste beide Formen kennen.

    Returns:
        Normalisierter Anker oder None, wenn der Eintrag unbrauchbar ist.
    """
    try:
        if isinstance(entry, dict):
            grp_id = entry.get("arenaId", entry.get("grpId", entry.get("cardID")))
            qty = entry.get("expected", entry.get("quantity", 1))
            name = entry.get("name", "")
        elif isinstance(entry, (list, tuple)) and len(entry) >= 3:
            grp_id, qty, name = entry[0], entry[1], entry[2]
        else:
            return None
        if grp_id is None:
            return None
        return (int(grp_id), int(qty), str(name))
    except (TypeError, ValueError):
        return None


def get_user_anchors(
    name_to_id: dict[str, int],
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
    non_interactive: bool = False,
    warnings: list[str] | None = None,
) -> list[tuple[int, int, str]]:
    """Interaktive Anker-Eingabe mit Auto-Save.

    Der User gibt 5 Karten + Mengen ein, die er sicher besitzt.
    Diese dienen als Suchanker im Speicher.

    Args:
        name_to_id: Mapping von Kartenname (lower) → grpId
        non_interactive: Wenn True, werden gespeicherte Anker automatisch
            akzeptiert (kein stdin). Für Watch-Modus / Hintergrund-Scans.
        warnings: Optionale Liste für Hinweise, die in das Scan-Ergebnis
            übernommen werden.

    Returns:
        Liste von (grpId, quantity, name)-Tupeln
    """
    import difflib
    import json

    # Ein nicht-interaktiver Aufruf darf niemals versuchen, stdin zu lesen.
    # Das gilt auch ohne --non-interactive, etwa für CI, LaunchAgents oder
    # pytest mit aktivem Capture. Der Scan verwendet dann wie beim expliziten
    # nicht-interaktiven Modus gespeicherte bzw. Standard-Anker.
    stdin_is_tty = sys.stdin.isatty()
    can_prompt = not non_interactive and stdin_is_tty
    if not stdin_is_tty and warnings is not None:
        warnings.append("anchor input skipped: no tty")

    # Gespeicherte Anker laden
    anchor_file = _anchor_file()

    if anchor_file.exists():
        try:
            with anchor_file.open("r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved and isinstance(saved, list):
                # JSON kennt keine Tupel — normalisieren, damit der
                # Rückgabetyp (int, int, str) überall stimmt.
                normalized = [a for a in (_normalize_anchor(e) for e in saved) if a]
                if normalized:
                    saved = normalized
                    print_fn("\n📌 [Gespeicherte Anker gefunden]")
                    for i, (_, qty, name) in enumerate(saved, 1):
                        print_fn(f"   {i}. {name} (x{qty})")
                    if not can_prompt:
                        print_fn("   (nicht-interaktiv: gespeicherte Anker verwendet)")
                        return saved
                    choice = input_fn("   Diese verwenden? [Y/n]: ").strip().lower()
                    if choice not in ("n", "no"):
                        return saved
        except Exception:
            pass

    # Nicht-interaktiv und keine (brauchbare) Datei: Standard-Anker nehmen.
    # Wichtig für Skripte/Launcher — der Scan darf NIE nach Karten fragen.
    if not can_prompt:
        print_fn("\n📌 [Standard-Anker] (nicht-interaktiv, keine gespeicherten Anker)")
        for i, (_, qty, name) in enumerate(DEFAULT_ANCHORS, 1):
            print_fn(f"   {i}. {name} (x{qty})")
        anchors = list(DEFAULT_ANCHORS)
        # Einmal ablegen, damit beim nächsten Lauf sichtbar ist, womit
        # gescannt wurde. Best effort: scheitert das (z.B. root-eigener
        # Ordner), läuft der Scan trotzdem weiter.
        try:
            anchor_file.parent.mkdir(parents=True, exist_ok=True)
            with anchor_file.open("w", encoding="utf-8") as f:
                json.dump(anchors, f, indent=2)
            print_fn(f"   → gespeichert in {anchor_file}")
        except Exception as exc:
            print_fn(f"   (Anker nicht speicherbar: {exc})")
        return anchors

    print_fn("\n🔧 [Setup] Gib 5 Karten ein, die du sicher besitzt (Rares/Mythics am besten).")

    anchors: list[tuple[int, int, str]] = []
    while len(anchors) < 5:
        print_fn(f"\nKarte #{len(anchors) + 1} (Enter = fertig):")
        name_input = input_fn("  Name: ").strip()

        if not name_input:
            if anchors:
                break
            print_fn("  ⚠ Bitte eine Karte eingeben.")
            continue

        search = name_input.lower()
        cid = name_to_id.get(search)

        if not cid:
            matches = difflib.get_close_matches(search, name_to_id.keys(), n=5, cutoff=0.5)
            if not matches:
                print_fn("  ❌ Nicht gefunden. Prüfe die Schreibweise.")
                continue

            if len(matches) == 1:
                final_name = matches[0]
                print_fn(f"  → {final_name.title()}")
            else:
                print_fn("  Meintest du?")
                for i, m in enumerate(matches, 1):
                    print_fn(f"    {i}. {m.title()}")
                sel = input_fn("  Auswahl #: ")
                if not sel.isdigit() or not (1 <= int(sel) <= len(matches)):
                    continue
                final_name = matches[int(sel) - 1]

            cid = name_to_id[final_name]
            name_input = final_name.title()

        try:
            qty = int(input_fn(f"  Menge von '{name_input}': "))
            if qty < 1:
                raise ValueError
            anchors.append((cid, qty, name_input))
        except ValueError:
            print_fn("  ❌ Ungültige Menge.")
            continue

    # Anker speichern
    if anchors:
        try:
            import json

            with anchor_file.open("w", encoding="utf-8") as f:
                json.dump(anchors, f, indent=2)
        except Exception:
            pass

    return anchors


def _select_best_block(
    candidates: Sequence[dict[int, int]] | Sequence[tuple[dict[int, int], int]],
    anchors: Sequence[tuple[int, int, str]],
    *,
    known_ids: set[int] | None = None,
    print_fn: Callable[..., None] = print,
) -> dict[int, int]:
    """Wählt den besten Kandidaten-Block aus der Liste von Speicherblöcken.

    Technik 3 (gewichteter Score, aus Community-Tool v3.4 übernommen):

    score = known_ratio * 0.35
          + anchors_exact_ratio * 0.35
          + anchors_id_ratio * 0.10
          + size_score * 0.10
          + (1 - dup_ratio) * 0.10

    - **known_ratio**: Antel bekannter Karten-IDs im Block (aus Karten-DB).
      Ein Block mit vielen unbekannten IDs ist wahrscheinlich kein
      Collection-Block.  *Benötigt known_ids-Parameter.*
    - **anchors_exact**: Anker mit exakter Menge.  Höchste Gewichtung.
    - **anchors_id**: Anker-grpIds überhaupt vorhanden.
    - **size_score**: min(len/5000, 1.0) — größere Blöcke sind wahrscheinlicher
      die echte Collection.
    - **(1 - dup_ratio)**: Weniger Duplikate = sauberer Speicherbereich.

    Sortierung: (score, anchors_exact, known, -dup_ratio, size) absteigend.
    Duplikate sind damit nur noch Tiebreak — sie dürfen die Score-Ordnung
    nicht überstimmen (siehe Regressionstest
    ``test_select_best_block_anchor_block_with_one_dupe_beats_clean_junk``).

    **Backward-Kompatibilität**: Ohne ``known_ids`` fällt known_ratio auf
    1.0 (alle IDs als "bekannt" angenommen) — die alte anchor-presence-Logik
    bleibt damit funktionsfähig.  Kandidaten können als reine dicts
    (alte API) oder (dict, dupes)-Tupel (neue API) übergeben werden.

    Args:
        candidates: Deduplizierte Blöcke als dicts oder (dict, dupes)-Tupel.
        anchors: Liste von (grpId, expected_qty, name)-Tupeln.
        known_ids: Set aller bekannten arenaIds aus der Karten-DB.
        print_fn: Ausgabe-Funktion für Diagnose-Meldungen.

    Returns:
        Der ausgewählte Block als {grpId: qty} Dictionary.
    """
    if not candidates:
        return {}

    # Normalisiere Kandidaten: trenne Block-Dict und Duplikate-Zähler
    normalized: list[tuple[dict[int, int], int]] = []
    for c in candidates:
        if isinstance(c, tuple) and len(c) == 2 and isinstance(c[0], dict):
            normalized.append((c[0], c[1]))
        elif isinstance(c, dict):
            normalized.append((c, 0))
        else:
            continue

    if not normalized:
        return {}

    if not anchors:
        # Keine Anker → Fallback auf größten Block (altes Verhalten)
        return max((blk for blk, _ in normalized), key=len)

    anchor_pairs = {(aid, qty) for aid, qty, _ in anchors}
    anchor_ids = {aid for aid, _, _ in anchors}
    n_anchors = max(1, len(anchors))

    # Wenn known_ids nicht gegeben: alle IDs als bekannt annehmen (ratio=1.0)
    _known = known_ids if known_ids is not None else None

    scored: list[tuple[dict[int, int], float, int, int, int, int]] = []
    # (block, score, anchors_exact, known_count, dupes, size)

    for blk, dupes in normalized:
        if not blk:
            continue

        blk_len = len(blk)

        # known_ratio: Anteil bekannter Karten-IDs
        if _known is not None:
            known_count = sum(1 for k in blk if k in _known)
            known_ratio = known_count / blk_len
        else:
            known_count = blk_len  # alle als bekannt annehmen
            known_ratio = 1.0

        # anchors_exact: Anker mit exakter Menge
        anchors_exact = sum(1 for k, v in blk.items() if (k, v) in anchor_pairs)
        # anchors_id: Anker-grpIds überhaupt vorhanden
        anchors_id = sum(1 for k in blk if k in anchor_ids)

        size_score = min(blk_len / 5000, 1.0)
        dup_ratio = dupes / max(1, blk_len + dupes)

        score = (
            known_ratio * 0.35
            + (anchors_exact / n_anchors) * 0.35
            + (anchors_id / n_anchors) * 0.10
            + size_score * 0.10
            + (1.0 - dup_ratio) * 0.10
        )

        scored.append((blk, score, anchors_exact, known_count, dupes, blk_len))

    # Sortierung: score → Anker-Treffer → bekannte IDs → Duplikat-Anteil → Größe.
    #
    # WICHTIG: ``dupes == 0`` darf NICHT das dominierende Kriterium sein.  Als
    # Boolean an erster Stelle der Sortierung schlägt es jeden Score: ein
    # riesiger Fremd-Block ohne Duplikate gewinnt dann gegen den echten
    # Collection-Block, sobald dieser ein einziges Duplikat enthält — obwohl
    # er alle Anker trägt.  Genau das führte am 26.09.2026 zu einem Export mit
    # 6944 Einträgen und nur 1/10 Ankern (Validierung: ``anchor-missing``).
    # Duplikate fließen bereits gewichtet über ``(1 - dup_ratio)`` in den Score
    # ein; hier dienen sie nur noch als Tiebreak.
    scored.sort(
        key=lambda x: (x[1], x[2], x[3], -(x[4] / max(1, x[5] + x[4])), x[5]),
        reverse=True,
    )

    best = scored[0]
    best_blk, best_score, best_exact, best_known, best_dupes, best_size = best
    total_anchors = len(anchor_ids)

    if best_dupes == 0 and best_exact == total_anchors:
        print_fn(
            f"   📋 Block ausgewählt: {best_size} Einträge, "
            f"score {best_score:.3f}, {best_exact}/{total_anchors} Anker korrekt"
        )
    elif best_exact > 0 or best_known > 0:
        print_fn(
            f"   📋 Block ausgewählt: {best_size} Einträge, "
            f"score {best_score:.3f}, "
            f"{best_exact}/{total_anchors} Anker exakt, "
            f"known={best_known}, dupes={best_dupes}"
        )
    else:
        # Kein Block enthält Anker — Fallback auf größten Block
        biggest = max((blk for blk, _ in normalized), key=len)
        print_fn(
            f"   ⚠ Kein Block enthält Anker-Karten. "
            f"Fallback auf größten Block ({len(biggest)} Einträge)"
        )
        return biggest

    return best_blk


def _validate_block(
    block: dict[int, int],
    duplicates: int = 0,
    known_ids: set[int] | None = None,
) -> bool:
    """Validiert einen extrahierten Block gegen Schwellen (Technik 4).

    Aus Community-Tool v3.4 _validate übernommen:
    - len(block) >= 10 und <= 100_000
    - known_ratio >= 0.30 (bekannte IDs / len)
    - total (Summe der Mengen) <= 500_000
    - duplicates <= max(25, int(len * 0.05))

    Args:
        block: Extrahierter Block als {grpId: qty}
        duplicates: Anzahl doppelter arena_ids im Block
        known_ids: Set aller bekannten arenaIds (aus Karten-DB)

    Returns:
        True wenn der Block die Schwellen erfüllt (wahrscheinlich echte Collection)
    """
    if not block:
        return False
    if len(block) < 10:
        return False
    if len(block) > 100_000:
        return False

    # known_ratio: Anteil bekannter Karten-IDs
    if known_ids is not None:
        known = sum(1 for k in block if k in known_ids)
        ratio = known / len(block)
        if ratio < 0.30:
            return False

    total = sum(block.values())
    if total > 500_000:
        return False

    if duplicates > max(25, int(len(block) * 0.05)):
        return False

    return True


def _attach_process(
    process_names: Sequence[str],
    *,
    print_fn: Callable[..., None] = print,
) -> Pymem | None:
    """Attach an MTGA process using the first name that works."""
    last_error: Exception | None = None
    for process_name in process_names:
        print_fn(f"🔗 Verbinde zu {process_name}...")
        try:
            pm = Pymem(process_name)
            print_fn(f"✅ Verbunden (PID: {pm.pid})")
            return pm
        except Exception as exc:
            last_error = exc
            print_fn(f"⚠ Prozess '{process_name}' nicht verfügbar.")
            continue

    if last_error is not None:
        print_fn("❌ MTGA läuft nicht. Starte das Spiel und öffne die 'Decks'-Ansicht.")
        print_fn(f"   (Fehler: {last_error})")
        print_fn("   Hinweis: pymem-osx benötigt sudo. Starte mit: sudo python3 ...")
    return None


def scan_collection_detailed(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
    db_loader: Callable[[], dict[int, dict[str, Any]]] | None = None,
    process_names: Sequence[str] | None = None,
    memory_scanner: Callable[[MemoryBackend, bytes], list[int]] | None = None,
    block_parser: Callable[[MemoryBackend, int], list[Any]] | None = None,
    debug: bool = False,
    use_helper: bool | None = None,
    sock_path: str | None = None,
    backend: MemoryBackend | None = None,
    non_interactive: bool = False,
) -> MemoryScanResult | None:
    """Hauptfunktion: Scannt den MTGA-Speicher nach der Collection.

    Wenn *use_helper* True ist (oder None und der Helper-Daemon läuft),
    wird der Scan über den Sudo-Helper-Daemon ausgeführt — sudo-frei.
    Andernfalls wird der direkte pymem-osx-Weg verwendet (erfordert sudo).

    Args:
        use_helper: True → Helper erzwingen; False → direkter Scan;
                     None → auto-detect (Helper wenn verfügbar).
        sock_path: Pfad zum Helper-UNIX-Socket (sonst Default).
        backend:   Wenn gesetzt, wird dieses Backend verwendet statt ein
                   neues zu erstellen.  Erlaubt es, eine persistente
                   Helper-Verbindung für mehrere Scans wiederzuverwenden.

    Returns:
        MemoryScanResult oder None bei Fehler.
    """
    # --- Backend bestimmen ---
    from .helper_client import is_helper_available, HelperBackend
    from .helper_client import DEFAULT_SOCK_PATH as _default_sock

    if sock_path is None:
        sock_path = _default_sock

    sudo_hint = "   - Der Helper-Daemon läuft (sudo-frei)"

    if backend is not None:
        # Externes Backend (z.B. PersistentHelperBackend für kombinierten Scan)
        pass
    else:
        if use_helper is None:
            use_helper = is_helper_available(sock_path)

        if use_helper:
            print_fn("🔄 Scan über Helper-Daemon (sudo-frei)...")
            backend = HelperBackend(sock_path)
            sudo_hint = "   - Der Helper-Daemon läuft (sudo-frei)"
        else:
            candidate_names = tuple(process_names) if process_names is not None else get_macos_mtga_process_names()
            if not candidate_names:
                candidate_names = (get_macos_mtga_process_name(),)
            pm = _attach_process(candidate_names, print_fn=print_fn)
            if pm is None:
                return None
            backend = PymemBackend(pm)
            sudo_hint = "   - Das Script mit sudo läuft"

    assert backend is not None  # durch einen der obigen Zweige gesetzt

    # --- Gemeinsamer Code ---
    if db_loader is None:
        db_loader = load_card_database
    if memory_scanner is None:
        memory_scanner = scan_process_memory
    if block_parser is None:
        block_parser = find_blocks_with_dupes

    db = db_loader()
    if not db:
        print_fn("❌ Karten-DB konnte nicht geladen werden.")
        return None

    # known_ids für known_ratio im gewichteten Score und _validate_block
    known_ids: set[int] = set(db.keys())

    name_to_id = {v["name"].lower(): k for k, v in db.items()}
    anchor_warnings: list[str] = []
    anchors = get_user_anchors(
        name_to_id,
        input_fn=input_fn,
        print_fn=print_fn,
        non_interactive=non_interactive,
        warnings=anchor_warnings,
    )
    if not anchors:
        print_fn("❌ Keine Anker-Karten angegeben.")
        return None

    # --- Technik 1: (arena_id, quantity)-Paar-Scan ---
    # Statt nur nach der arena_id (4 bytes) zu suchen, suchen wir nach
    # dem (arena_id, quantity)-Paar (8 bytes, struct.pack("<II", aid, qty)).
    # Das reduziert False-Positives massiv, da die Wahrscheinlichkeit dass
    # ein zufälliges 8-Byte-Pattern im Heap matcht extrem gering ist.
    print_fn("\n🔍 Scanne Speicher nach (arena_id, quantity)-Paaren...")
    matches: list[int] = []
    match_anchor_ids: dict[int, set[int]] = {}
    total = len(anchors)
    anchor_matches: dict[int, int] = {}
    aggregate_stats: ScanStats | None = None

    if memory_scanner is scan_process_memory:
        # Paar-Pattern: struct.pack("<II", arena_id, quantity)
        needles = {aid: struct.pack("<II", aid, qty) for aid, qty, _ in anchors}
        multi_result = scan_process_memory_many_with_stats(backend, needles)
        aggregate_stats = multi_result.stats
        for i, (aid, _aqty, aname) in enumerate(anchors, 1):
            display = (aname[:15] + "..") if len(aname) > 15 else aname
            found = multi_result.addresses.get(aid, [])
            anchor_matches[aid] = len(found)
            print_fn(f"   [{i}/{total}] {display}: {len(found)} Fundstellen")
            matches.extend(found)
            for address in found:
                match_anchor_ids.setdefault(address, set()).add(aid)
        if debug:
            stats = multi_result.stats
            print_fn(
                "   Debug: "
                f"{stats.regions} Regionen, "
                f"{stats.bytes_scanned / 1024 / 1024:.1f} MB gelesen, "
                f"{stats.read_failures} Lesefehler, "
                f"{stats.matches} Treffer gesamt, "
                f"Region-Stop={stats.region_error}"
            )
    else:
        for i, (aid, aqty, aname) in enumerate(anchors, 1):
            display = (aname[:15] + "..") if len(aname) > 15 else aname
            print_fn(f"   [{i}/{total}] Suche {display} (x{aqty})...")
            # Technik 1: Paar-Pattern statt nur arena_id
            needle = struct.pack("<II", aid, aqty)
            found = memory_scanner(backend, needle)
            anchor_matches[aid] = len(found)
            print_fn(f"     → {len(found)} Fundstellen")
            matches.extend(found)
            for address in found:
                match_anchor_ids.setdefault(address, set()).add(aid)

    if not matches:
        print_fn("❌ Keine Anker-Karten im Speicher gefunden.")
        print_fn("   Stelle sicher, dass:")
        print_fn("   - MTGA läuft und du in der 'Decks'-Ansicht bist")
        print_fn("   - Die Karten existieren und die Mengen stimmen")
        print_fn(sudo_hint)
        return None

    print_fn("\n📦 Parse Speicherblöcke...")
    # Normalisiere block_parser-Ergebnisse: akzeptiert sowohl list[dict]
    # (alte API, find_blocks) als auch list[tuple[dict, int]] (neue API,
    # find_blocks_with_dupes).  Dupes-Count wird für Score + Validation
    # gebraucht.
    raw_candidates: list[tuple[dict[int, int], int]] = []
    read_coverage: dict[str, Any] = {
        "windows_total": 0,
        "windows_complete": 0,
        "windows_partial": 0,
        "windows_unreadable": 0,
        "bytes_unreadable": 0,
        "anchors": [],
    }
    coverage_warnings: list[str] = []
    for m in matches:
        if block_parser is find_blocks_with_dupes:
            result, window_coverage = _find_blocks_with_dupes_and_coverage(backend, m)
            bytes_read = window_coverage["bytesRead"]
            window_bytes = window_coverage["windowBytes"]
            read_coverage["windows_total"] += 1
            read_coverage["bytes_unreadable"] += window_bytes - bytes_read
            anchor_ids = sorted(match_anchor_ids.get(m, set()))
            read_coverage["anchors"].append({**window_coverage, "arenaIds": anchor_ids})
            if bytes_read == 0:
                read_coverage["windows_unreadable"] += 1
                warning_name = "anchor-window-unreadable"
            elif bytes_read < window_bytes:
                read_coverage["windows_partial"] += 1
                warning_name = "anchor-window-partial"
            else:
                read_coverage["windows_complete"] += 1
                warning_name = None
            if warning_name is not None:
                for aid in anchor_ids:
                    coverage_warnings.append(
                        f"{warning_name}: address={m:#x} arenaId={aid} read={bytes_read}/{window_bytes}"
                    )
        else:
            result = block_parser(backend, m)
        for item in result:
            if isinstance(item, tuple) and len(item) == 2 and isinstance(item[0], dict):
                blk_dict: dict[int, int] = item[0]
                blk_dupes: int = item[1] if isinstance(item[1], int) else 0
                raw_candidates.append((blk_dict, blk_dupes))
            elif isinstance(item, dict):
                raw_candidates.append((item, 0))

    if not raw_candidates:
        print_fn("❌ Keine validen Datenblöcke gefunden.")
        if read_coverage["windows_total"]:
            validation = validate_collection({}, db=db, anchors=anchors)
            validation["warnings"] = sorted(
                set(validation["warnings"] + coverage_warnings + anchor_warnings)
            )
            return MemoryScanResult(
                collection={}, anchors=list(anchors), anchor_matches=anchor_matches,
                validation=validation, scan_stats=aggregate_stats, read_coverage=read_coverage,
            )
        return None

    # Dedup: Mehrere Anker im selben Collection-Array parsen denselben
    # Speicherbereich mehrfach → identische Blöcke in candidates.
    # Identische Karten-Mengen entfernen, damit die Blockauswahl nicht durch
    # Duplikate verfälscht wird und redundante Arbeit entfällt.
    # Dupes-Count des ersten Vorkommens wird behalten.
    unique_candidates: list[tuple[dict[int, int], int]] = []
    seen: set[tuple[tuple[int, int], ...]] = set()
    for block, dupes in raw_candidates:
        key = tuple(sorted(block.items()))
        if key not in seen:
            seen.add(key)
            unique_candidates.append((block, dupes))

    # Technik 3: gewichteter Score mit known_ids + dupes
    collection = _select_best_block(
        unique_candidates,
        anchors,
        known_ids=known_ids,
        print_fn=print_fn,
    )

    # Anker-Mengen erzwingen (user-provided Werte sind vertrauenswürdig)
    for aid, qty, _name in anchors:
        if aid in collection:
            collection[aid] = qty

    # Technik 4: _validate_block mit known_ratio/duplicates/total Schwellen
    best_dupes = 0
    for blk, dupes in unique_candidates:
        if blk is collection or blk == collection:
            best_dupes = dupes
            break

    block_valid = _validate_block(collection, best_dupes, known_ids)
    if not block_valid and len(collection) >= 10:
        print_fn("   ⚠ Block-Validierung fehlgeschlagen (known_ratio/dupes/total)")

    validation = validate_collection(collection, db=db, anchors=anchors)
    validation["warnings"] = sorted(
        set(validation["warnings"] + coverage_warnings + anchor_warnings)
    )
    if validation["valid"]:
        print_fn(f"\n✅ {len(collection)} unique Einträge gefunden!")
    else:
        print_fn(f"\n⚠ {len(collection)} unique Einträge gefunden, Validierung mit Fehlern: {', '.join(validation['errors'])}")

    return MemoryScanResult(
        collection=collection,
        anchors=list(anchors),
        anchor_matches=anchor_matches,
        validation=validation,
        scan_stats=aggregate_stats,
        read_coverage=read_coverage,
    )


def scan_collection(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
    db_loader: Callable[[], dict[int, dict[str, Any]]] | None = None,
    process_names: Sequence[str] | None = None,
    memory_scanner: Callable[[MemoryBackend, bytes], list[int]] | None = None,
    block_parser: Callable[[MemoryBackend, int], list[Any]] | None = None,
    debug: bool = False,
    use_helper: bool | None = None,
    sock_path: str | None = None,
) -> dict[int, int] | None:
    """Kompatibler Wrapper: liefert nur {grpId: quantity}.

    Siehe scan_collection_detailed() für use_helper- und sock_path-Parameter.
    """
    result = scan_collection_detailed(
        input_fn=input_fn,
        print_fn=print_fn,
        db_loader=db_loader,
        process_names=process_names,
        memory_scanner=memory_scanner,
        block_parser=block_parser,
        debug=debug,
        use_helper=use_helper,
        sock_path=sock_path,
    )
    if result is None:
        return None

    return result.collection


def main() -> int:
    """CLI-Einstiegspunkt für den Scanner."""
    import argparse

    parser = argparse.ArgumentParser(description="Scannt die MTGA-Collection aus dem Prozessspeicher.")
    parser.add_argument("--debug", action="store_true", help="Gibt Scan-Statistiken pro Anker aus.")
    parser.add_argument(
        "--output",
        type=Path,
        help="Schreibt collection.json und run-report.json in dieses Verzeichnis.",
    )
    parser.add_argument(
        "--helper",
        choices=["auto", "force", "off"],
        default="auto",
        help="Helper-Daemon nutzen: auto (default), force (erzwingen), off (direkter Scan).",
    )
    parser.add_argument(
        "--sock",
        type=str,
        default=None,
        help="Pfad zum Helper-UNIX-Socket (default: /var/run/mtga-helper.sock).",
    )
    args = parser.parse_args()

    use_helper: bool | None
    if args.helper == "force":
        use_helper = True
    elif args.helper == "off":
        use_helper = False
    else:
        use_helper = None  # auto

    result = scan_collection_detailed(debug=args.debug, use_helper=use_helper, sock_path=args.sock)
    if result is None:
        return 1

    # Ausgabe
    print(f"\n📊 Collection: {sum(result.collection.values())} Karten, {len(result.collection)} unique IDs")  # noqa: E501
    if args.output:
        # known_ids für Quality-Guard laden
        try:
            _db = load_card_database()
            _known_ids = set(_db.keys()) if _db else None
        except Exception:
            _known_ids = None
        collection_path, run_report_path = write_collection_artifacts(
            result.collection, args.output, scan_result=result, known_ids=_known_ids,
        )
        print(f"Collection exportiert: {collection_path}")
        print(f"Run-Report: {run_report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
