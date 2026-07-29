"""Memory-Scanner: Findet die Kartensammlung im MTGA-Prozess-Speicher.

Basiert auf NthPhantom10/MTGA-collection-exporter (v2.0).
Portiert von Windows pymem → macOS pymem-osx.
"""

from __future__ import annotations

import struct
import sys
import json
from dataclasses import dataclass
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
from .pattern_scanner import _read_bytes_silent, scan_process_memory, scan_process_memory_with_stats
from .pattern_scanner import ScanStats, scan_process_memory_many_with_stats


@dataclass(frozen=True)
class MemoryScanResult:
    collection: dict[int, int]
    anchors: list[tuple[int, int, str]]
    anchor_matches: dict[int, int]
    validation: dict[str, Any]
    scan_stats: ScanStats | None = None


def _anchor_file() -> Path:
    return get_default_cache_dir() / "last_anchors.json"


def find_blocks(pm: Pymem, addr: int) -> list[dict[int, int]]:
    """Liest Speicher um eine Adresse und sucht nach (k,v)-Paaren.

    Die Collection-Daten liegen als Array von (grpId, quantity)-Paaren
    im Heap. Diese Funktion liest 4MB um die Fundstelle und parst
    alle gültigen Paare.

    Args:
        pm: Pymem-Instanz
        addr: Fundstelle einer Anker-Karte

    Returns:
        Liste von Karten-Dictionaries (grpId → quantity)
    """
    try:
        block_start = max(0, addr - 1024 * 1024)
        data = _read_bytes_silent(pm, block_start, 4 * 1024 * 1024)
        if data is None:
            return []
        ints = struct.unpack(f"<{len(data) // 4}I", data)

        blocks: list[dict[int, int]] = []
        for offset in (0, 1):
            current: dict[int, int] = {}
            misses = 0
            for i in range(offset, len(ints) - 1, 2):
                k, v = ints[i], ints[i + 1]
                if 1000 <= k < 500000 and 1 <= v <= 400:
                    current[k] = v
                    misses = 0
                else:
                    misses += 1
                    if misses > 50:
                        if len(current) > 50:
                            blocks.append(current)
                        current = {}
                        misses = 0
            if len(current) > 50:
                blocks.append(current)

        return blocks
    except Exception:
        return []


def write_collection_artifacts(
    collection: dict[int, int],
    output_dir: Path,
    *,
    scan_result: MemoryScanResult | None = None,
) -> tuple[Path, Path]:
    """Schreibt Memory-Scan-Ergebnisse im bestehenden Artefaktformat."""
    output_dir.mkdir(parents=True, exist_ok=True)
    collection_path = output_dir / "collection.json"
    run_report_path = output_dir / "run-report.json"

    started_at = _iso_now()
    validation = scan_result.validation if scan_result is not None else validate_collection(collection)
    warnings = list(validation.get("warnings", []))
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

    _write_json(collection_path, collection_payload)
    _write_json(run_report_path, run_report_payload)
    return collection_path, run_report_path


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
            if not ok:
                errors.append("anchor-mismatch")
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
        "unknownCardIdsTruncated": len(unknown_ids) > 50,
        "anchors": anchor_results,
    }


def get_user_anchors(
    name_to_id: dict[str, int],
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
) -> list[tuple[int, int, str]]:
    """Interaktive Anker-Eingabe mit Auto-Save.

    Der User gibt 5 Karten + Mengen ein, die er sicher besitzt.
    Diese dienen als Suchanker im Speicher.

    Args:
        name_to_id: Mapping von Kartenname (lower) → grpId

    Returns:
        Liste von (grpId, quantity, name)-Tupeln
    """
    import difflib
    import json

    # Gespeicherte Anker laden
    anchor_file = _anchor_file()

    if anchor_file.exists():
        try:
            with anchor_file.open("r", encoding="utf-8") as f:
                saved = json.load(f)
            if saved and isinstance(saved, list):
                print_fn("\n📌 [Gespeicherte Anker gefunden]")
                for i, (_, qty, name) in enumerate(saved, 1):
                    print_fn(f"   {i}. {name} (x{qty})")
                choice = input_fn("   Diese verwenden? [Y/n]: ").strip().lower()
                if choice not in ("n", "no"):
                    return saved
        except Exception:
            pass

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
    memory_scanner: Callable[[Pymem, bytes], list[int]] | None = None,
    block_parser: Callable[[Pymem, int], list[dict[int, int]]] | None = None,
    debug: bool = False,
) -> MemoryScanResult | None:
    """Hauptfunktion: Scannt den MTGA-Speicher nach der Collection.

    Returns:
        Dictionary {grpId: quantity} oder None bei Fehler.
    """
    if db_loader is None:
        db_loader = load_card_database
    if memory_scanner is None:
        memory_scanner = scan_process_memory
    if block_parser is None:
        block_parser = find_blocks

    # 1. Karten-DB laden
    db = db_loader()
    if not db:
        print_fn("❌ Karten-DB konnte nicht geladen werden.")
        return None

    # 2. Name → ID Mapping
    name_to_id = {v["name"].lower(): k for k, v in db.items()}

    # 3. An MTGA-Prozess attach-en
    candidate_names = tuple(process_names) if process_names is not None else get_macos_mtga_process_names()
    if not candidate_names:
        candidate_names = (get_macos_mtga_process_name(),)
    pm = _attach_process(candidate_names, print_fn=print_fn)
    if pm is None:
        return None

    # 4. Anker-Karten eingeben
    anchors = get_user_anchors(name_to_id, input_fn=input_fn, print_fn=print_fn)
    if not anchors:
        print_fn("❌ Keine Anker-Karten angegeben.")
        return None

    # 5. Memory-Scan
    print_fn("\n🔍 Scanne Speicher nach Collection-Daten...")
    matches: list[int] = []
    total = len(anchors)
    anchor_matches: dict[int, int] = {}
    aggregate_stats: ScanStats | None = None

    if memory_scanner is scan_process_memory:
        needles = {aid: struct.pack("<I", aid) for aid, _, _ in anchors}
        multi_result = scan_process_memory_many_with_stats(pm, needles)
        aggregate_stats = multi_result.stats
        for i, (aid, _aqty, aname) in enumerate(anchors, 1):
            display = (aname[:15] + "..") if len(aname) > 15 else aname
            found = multi_result.addresses.get(aid, [])
            anchor_matches[aid] = len(found)
            print_fn(f"   [{i}/{total}] {display}: {len(found)} Fundstellen")
            matches.extend(found)
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
        for i, (aid, _aqty, aname) in enumerate(anchors, 1):
            display = (aname[:15] + "..") if len(aname) > 15 else aname
            print_fn(f"   [{i}/{total}] Suche {display}...")

            needle = struct.pack("<I", aid)
            found = memory_scanner(pm, needle)
            anchor_matches[aid] = len(found)
            print_fn(f"     → {len(found)} Fundstellen")
            matches.extend(found)

    if not matches:
        print_fn("❌ Keine Anker-Karten im Speicher gefunden.")
        print_fn("   Stelle sicher, dass:")
        print_fn("   - MTGA läuft und du in der 'Decks'-Ansicht bist")
        print_fn("   - Die Karten existieren und die Mengen stimmen")
        print_fn("   - Das Script mit sudo läuft")
        return None

    # 6. Blöcke um Fundstellen parsen
    print_fn("\n📦 Parse Speicherblöcke...")
    candidates: list[dict[int, int]] = []
    for m in matches:
        candidates.extend(block_parser(pm, m))

    if not candidates:
        print_fn("❌ Keine validen Datenblöcke gefunden.")
        return None

    # 7. Besten Block auswählen (meiste Einträge)
    collection = max(candidates, key=len)
    validation = validate_collection(collection, db=db, anchors=anchors)
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
    )


def scan_collection(
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[..., None] = print,
    db_loader: Callable[[], dict[int, dict[str, Any]]] | None = None,
    process_names: Sequence[str] | None = None,
    memory_scanner: Callable[[Pymem, bytes], list[int]] | None = None,
    block_parser: Callable[[Pymem, int], list[dict[int, int]]] | None = None,
    debug: bool = False,
) -> dict[int, int] | None:
    """Kompatibler Wrapper: liefert nur {grpId: quantity}."""
    result = scan_collection_detailed(
        input_fn=input_fn,
        print_fn=print_fn,
        db_loader=db_loader,
        process_names=process_names,
        memory_scanner=memory_scanner,
        block_parser=block_parser,
        debug=debug,
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
    args = parser.parse_args()

    result = scan_collection_detailed(debug=args.debug)
    if result is None:
        return 1

    # Ausgabe
    print(f"\n📊 Collection: {sum(result.collection.values())} Karten, {len(result.collection)} unique IDs")
    if args.output:
        collection_path, run_report_path = write_collection_artifacts(result.collection, args.output, scan_result=result)
        print(f"Collection exportiert: {collection_path}")
        print(f"Run-Report: {run_report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
