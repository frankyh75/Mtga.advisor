from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from advisor.completion import build_completion_advice, load_json, write_advisor_result
from advisor.deck_import import import_arena_deck, write_deck
from advisor.llm_advisor import run_llm_advisor
from advisor.llm_config import LLMConfig, load_config, write_default_config
from parser.decks import export_decks, export_container, show_deck, list_decks
from parser.start_hook import DeckSummary
from parser.export import export_collection
from parser.log_paths import (
    MissingLogsError,
    PathConfig,
    detect_platform,
    discover_logs,
)
from scanner.card_database import load_card_database
from scanner.memory_scanner import scan_collection_detailed as scan_memory_collection_detailed
from scanner.memory_scanner import validate_collection
from scanner.memory_scanner import write_collection_artifacts
from scanner.deck_scanner import scan_decks, write_deck_artifacts, DeckScanResult
from scanner.il2cpp_nav import (
    PymemMemoryAdapter,
    Il2CppScanResult,
    scan_decks_il2cpp,
)
from scanner.macos_paths import get_macos_mtga_process_names
from scanner.pattern_scanner import scan_process_memory_many_with_stats
from server.app import DEFAULT_HOST, DEFAULT_PORT, run_server


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mtga-export",
        description="Exportiert die MTGA-Sammlung aus lokalen Logs und zeigt die Artefakte an.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    collection = subparsers.add_parser("collection", help="Exportiert die Sammlung aus MTGA-Logs.")
    collection.add_argument(
        "--log",
        dest="logs",
        action="append",
        type=Path,
        help="Pfad zu einer Logdatei (mehrfach angeben möglich). Wenn nicht gesetzt, werden Logs automatisch gesucht.",
    )
    collection.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Ausgabeverzeichnis (Standard: ./out).",
    )
    collection.add_argument(
        "--platform",
        choices=["windows", "macos", "unknown"],
        help="Plattform überschreiben (Standard: automatische Erkennung).",
    )
    collection.add_argument(
        "--windows-local-low",
        type=Path,
        help="Override für Windows LocalLow MTGA Pfad.",
    )
    collection.add_argument(
        "--windows-steam-userdata",
        type=Path,
        help="Override für Windows Steam userdata Pfad.",
    )
    collection.add_argument(
        "--macos-logs",
        type=Path,
        help="Override für macOS Log-Pfad.",
    )
    collection.add_argument(
        "--macos-steam-userdata",
        type=Path,
        help="Override für macOS Steam userdata Pfad.",
    )
    collection.add_argument(
        "--custom-log-dir",
        action="append",
        type=Path,
        default=[],
        help="Zusätzliche Log-Verzeichnisse, die geprüft werden sollen.",
    )

    decks = subparsers.add_parser("decks", help="Exportiert Decks aus MTGA-Logs (StartHook-Events).")
    decks.add_argument(
        "--log",
        dest="logs",
        action="append",
        type=Path,
        help="Pfad zu einer Logdatei (mehrfach angeben möglich). Wenn nicht gesetzt, werden Logs automatisch gesucht.",
    )
    decks.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Ausgabeverzeichnis (Standard: ./out).",
    )
    decks.add_argument(
        "--platform",
        choices=["windows", "macos", "unknown"],
        help="Plattform überschreiben (Standard: automatische Erkennung).",
    )
    decks.add_argument(
        "--macos-logs",
        type=Path,
        help="Override für macOS Log-Pfad.",
    )
    decks.add_argument(
        "--macos-steam-userdata",
        type=Path,
        help="Override für macOS Steam userdata Pfad.",
    )
    decks.add_argument(
        "--custom-log-dir",
        action="append",
        type=Path,
        default=[],
        help="Zusätzliche Log-Verzeichnisse, die geprüft werden sollen.",
    )

    # Container-Export für Decks
    deck_container = decks.add_subparsers(dest="deck_command", required=True)
    container_export = deck_container.add_parser(
        "container",
        help="Exportiere Decks als Container mit individuellem Format.",
    )
    container_export.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Ausgabeverzeichnis für den Container.",
    )
    container_export.add_argument(
        "--log",
        dest="logs",
        action="append",
        type=Path,
        help="Optionale Logdateien, falls kein decks.json existiert.",
    )
    container_export.add_argument(
        "--refresh",
        action="store_true",
        help="Überschreibe existierende Deck-Dateien.",
    )

    deck_list = deck_container.add_parser(
        "list",
        help="Liste alle Decks aus dem Container auf.",
    )
    deck_list.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Container-Verzeichnis.",
    )

    deck_show = deck_container.add_parser(
        "show",
        help="Zeige Details eines einzelnen Decks.",
    )
    deck_show.add_argument(
        "--deck-id",
        type=str,
        required=True,
        help="Die Deck-ID des anzuzeigenden Decks.",
    )
    deck_show.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Container-Verzeichnis.",
    )

    scan = subparsers.add_parser("scan", help="Scannt die macOS-Collection aus dem MTGA-Prozessspeicher.")
    scan.add_argument(
        "--output",
        type=Path,
        default=Path("out-memory"),
        help="Ausgabeverzeichnis (Standard: ./out-memory).",
    )
    scan.add_argument("--debug", action="store_true", help="Gibt Scan-Statistiken pro Anker aus.")

    deck_scan = subparsers.add_parser(
        "deck-scan",
        help="Scannt Decks aus dem MTGA-Prozessspeicher (Pattern + IL2CPP).",
    )
    deck_scan.add_argument(
        "--output",
        type=Path,
        default=Path("out-decks"),
        help="Ausgabeverzeichnis für Deck-Artefakte (Standard: ./out-decks).",
    )
    deck_scan.add_argument(
        "--method",
        choices=["pattern", "il2cpp", "auto"],
        default="auto",
        help="Scan-Methode: pattern (Anker-basiert), il2cpp (Navigation), auto (beide, IL2CPP bevorzugt).",
    )
    deck_scan.add_argument(
        "--anchors",
        nargs="*",
        type=int,
        help="Anker-GrpIDs für Pattern-Scan (mind. 1). Bei il2cpp nicht nötig.",
    )
    deck_scan.add_argument(
        "--card-db",
        action="store_true",
        help="Lade Karten-DB für Namensauflösung (grpId → Kartenname).",
    )
    deck_scan.add_argument("--debug", action="store_true", help="Gibt Scan-Statistiken aus.")

    run = subparsers.add_parser("run", help="Kanonischer Phase-1-Export: macOS Memory-Scan, sonst Log-Export.")
    run.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Ausgabeverzeichnis (Standard: ./out).",
    )
    run.add_argument(
        "--platform",
        choices=["windows", "macos", "unknown"],
        help="Plattform überschreiben (Standard: automatische Erkennung).",
    )
    run.add_argument("--debug", action="store_true", help="Gibt Scan-Statistiken aus, wenn Memory-Scan verwendet wird.")
    run.add_argument(
        "--log",
        dest="logs",
        action="append",
        type=Path,
        help="Pfad zu einer Logdatei für nicht-macOS oder expliziten Log-Export.",
    )
    run.add_argument("--windows-local-low", type=Path, help="Override für Windows LocalLow MTGA Pfad.")
    run.add_argument("--windows-steam-userdata", type=Path, help="Override für Windows Steam userdata Pfad.")
    run.add_argument("--macos-logs", type=Path, help="Override für macOS Log-Pfad.")
    run.add_argument("--macos-steam-userdata", type=Path, help="Override für macOS Steam userdata Pfad.")
    run.add_argument(
        "--custom-log-dir",
        action="append",
        type=Path,
        default=[],
        help="Zusätzliche Log-Verzeichnisse, die geprüft werden sollen.",
    )

    validate = subparsers.add_parser("validate", help="Validiert ein bestehendes collection.json Artefakt.")
    validate.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Verzeichnis mit collection.json (Standard: ./out).",
    )
    validate.add_argument(
        "--refresh-card-db",
        action="store_true",
        help="Ignoriert den Karten-DB-Cache und lädt lokale/Scryfall-Daten neu.",
    )
    validate.add_argument(
        "--no-report",
        action="store_true",
        help="Schreibt keinen validation-report.json.",
    )

    deck = subparsers.add_parser("deck", help="Decklisten importieren und normalisieren.")
    deck_subparsers = deck.add_subparsers(dest="deck_command", required=True)
    deck_import = deck_subparsers.add_parser("import", help="Importiert eine Arena-Textdeckliste.")
    deck_import.add_argument("--file", type=Path, required=True, help="Pfad zur Arena-Textdeckliste.")
    deck_import.add_argument("--format", required=True, help="Zielformat, z. B. standard oder brawl.")
    deck_import.add_argument("--name", help="Deckname überschreiben.")
    deck_import.add_argument("--output", type=Path, default=Path("out"), help="Ausgabeverzeichnis.")

    advisor = subparsers.add_parser("advisor", help="Regelbasierte Advisor-Funktionen.")
    advisor_subparsers = advisor.add_subparsers(dest="advisor_command", required=True)
    advisor_complete = advisor_subparsers.add_parser("complete", help="Berechnet Deck-Completion (regelbasiert).")
    advisor_complete.add_argument("--collection", type=Path, required=True, help="Pfad zu collection.json.")
    advisor_complete.add_argument("--deck", type=Path, required=True, help="Pfad zu arena_deck.json.")
    advisor_complete.add_argument("--output", type=Path, default=Path("out"), help="Ausgabeverzeichnis.")

    advisor_llm = advisor_subparsers.add_parser("llm", help="LLM-gestützte Wildcard- und Deck-Optimierung.")
    advisor_llm.add_argument("--collection", type=Path, default=Path("out/collection.json"), help="Pfad zu collection.json (Default: out/collection.json).")
    advisor_llm.add_argument("--decks", type=Path, default=Path("out/decks.json"), help="Pfad zu decks.json (Default: out/decks.json).")
    advisor_llm.add_argument("--output", type=Path, default=Path("out"), help="Ausgabeverzeichnis (Default: out).")
    advisor_llm.add_argument("--endpoint", help="LLM-Endpoint überschreiben (z.B. http://127.0.0.1:8081/v1/chat/completions).")
    advisor_llm.add_argument("--model", help="Modellname (für Logging/Anzeige).")
    advisor_llm.add_argument("--temperature", type=float, help="Sampling-Temperatur (0.0 - 1.0).")
    advisor_llm.add_argument("--max-tokens", type=int, help="Maximale Token-Anzahl.")
    advisor_llm.add_argument("--config", type=Path, help="Pfad zu einer Config-Datei (JSON/YAML).")

    advisor_config = advisor_subparsers.add_parser("init-config", help="Erzeugt eine Default-Config-Datei.")
    advisor_config.add_argument("--output", type=Path, default=None, help="Zielpfad (Default: ~/.config/mtga-advisor/config.json).")

    serve = subparsers.add_parser("serve", help="Startet einen lokalen Server für die Artefakte.")
    serve.add_argument("--host", default=DEFAULT_HOST, help="Host (Standard: 127.0.0.1).")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help="Port (Standard: 8000).")
    serve.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Verzeichnis mit collection.json/run-report.json (Standard: ./out).",
    )

    return parser


def _path_config_from_args(args: argparse.Namespace) -> PathConfig:
    return PathConfig(
        windows_local_low=args.windows_local_low,
        windows_steam_userdata=args.windows_steam_userdata,
        macos_logs=args.macos_logs,
        macos_steam_userdata=args.macos_steam_userdata,
        custom_logs=args.custom_log_dir or [],
    )


def _error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


def _run_collection(args: argparse.Namespace) -> int:
    if args.logs:
        log_paths = [Path(path) for path in args.logs]
        missing = [path for path in log_paths if not path.exists()]
        if missing:
            missing_str = ", ".join(path.as_posix() for path in missing)
            _error(f"Logdatei(en) nicht gefunden: {missing_str}")
            return 1
    else:
        platform = args.platform or detect_platform()
        config = _path_config_from_args(args)
        try:
            discovery = discover_logs(platform, config)
        except MissingLogsError as exc:
            _error(str(exc))
            return 1
        log_paths = discovery.found

    export_paths = export_collection(log_paths, args.output)
    print(f"Collection exportiert: {export_paths.collection}")
    print(f"Run-Report: {export_paths.run_report}")
    print(f"Raw Samples: {export_paths.raw_samples_dir}")
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    run_server(host=args.host, port=args.port, output_dir=args.output)
    return 0


def _run_decks(args: argparse.Namespace) -> int:
    # Container-Subcommands (list, show, container)
    if hasattr(args, 'deck_command') and args.deck_command:
        if args.deck_command == 'list':
            return _run_deck_list(args)
        elif args.deck_command == 'show':
            return _run_deck_show(args)
        elif args.deck_command == 'container':
            return _run_deck_container(args)

    # Standard deck export (existing behavior)
    if args.logs:
        log_paths = [Path(path) for path in args.logs]
        missing = [path for path in args.logs if not path.exists()]
        if missing:
            missing_str = ", ".join(path.as_posix() for path in missing)
            _error(f"Logdatei(en) nicht gefunden: {missing_str}")
            return 1
    else:
        platform = args.platform or detect_platform()
        config = _path_config_from_args(args)
        try:
            discovery = discover_logs(platform, config)
        except MissingLogsError as exc:
            _error(str(exc))
            return 1
        log_paths = discovery.found

    export_paths = export_decks(log_paths, args.output)
    print(f"Decks exportiert: {export_paths.decks}")
    print(f"Run-Report: {export_paths.run_report}")
    return 0


def _run_deck_container(args: argparse.Namespace) -> int:
    """Container-Export: Schreibe Decks als Container-Verzeichnis."""
    deck_dir = args.output
    decks_path = deck_dir.parent / "decks.json"

    if not decks_path.exists():
        log_paths = [Path(p) for p in (args.logs or [])]
        if log_paths:
            export_paths = export_decks(log_paths, deck_dir.parent)
            print(f"Decks aus Logs exportiert: {export_paths.decks}")
            decks_path = export_paths.decks
            decks_data = json.loads(decks_path.read_text(encoding="utf-8"))
        else:
            _error("Keine decks.json gefunden und keine Logs angegeben.")
            return 1
    else:
        decks_data = json.loads(decks_path.read_text(encoding="utf-8"))

    # Extrahiere DeckSummaries
    decks_list = [
        DeckSummary(
            name=d["name"],
            deck_id=d.get("deckId"),
            deck_tile_id=d.get("deckTileId"),
            description=d.get("description"),
            attributes=d.get("attributes", {}),
            format_legalities=d.get("formatLegalities", {}),
            is_companion_valid=d.get("isCompanionValid"),
            mana=d.get("mana"),
        )
        for d in decks_data.get("decks", [])
    ]

    paths = export_container(deck_dir, decks_list, refresh=args.refresh)
    print(f"Container exportiert: {paths.index}")
    print(f"Deck-Verzeichnis: {paths.deck_dir}")
    return 0


def _run_deck_list(args: argparse.Namespace) -> int:
    """Liste alle Decks aus dem Container auf."""
    index_data = list_decks(args.output)
    if "schema" not in index_data:
        print("Kein Container gefunden.")
        return 1
    print(f"Container: {args.output}/index.json")
    print(f"Decks: {index_data.get('deckCount', 0)}")
    for deck in index_data.get("decks", []):
        name = deck.get("name", "?")
        deck_id = deck.get("deckId", "?")
        has_full = "✓" if deck.get("hasFullList") else " "
        print(f"  [{has_full}] {name} ({deck_id})")
    return 0


def _run_deck_show(args: argparse.Namespace) -> int:
    """Zeige Details eines einzelnen Decks."""
    deck_data = show_deck(args.deck_id, args.output)
    if deck_data is None:
        print(f"Deck '{args.deck_id}' nicht gefunden.")
        return 1
    print(json.dumps(deck_data, indent=2, ensure_ascii=False))
    return 0


def _run_scan(args: argparse.Namespace) -> int:
    result = scan_memory_collection_detailed(debug=args.debug)
    if result is None:
        return 1
    collection_path, run_report_path = write_collection_artifacts(result.collection, args.output, scan_result=result)
    print(f"Collection exportiert: {collection_path}")
    print(f"Run-Report: {run_report_path}")
    return 0


def _attach_mtga():
    """Attach to the running MTGA process. Returns Pymem instance or None."""
    from scanner.memory_scanner import _attach_process
    return _attach_process(get_macos_mtga_process_names())


def _run_deck_scan(args: argparse.Namespace) -> int:
    """Scannt Decks aus dem MTGA-Prozessspeicher.

    Unterstützt zwei Methoden:
    - il2cpp: Navigation über PAPA → DecksManager → _allDecks (keine Anker nötig)
    - pattern: Anker-basierter Scan für bekannte grpIds (wie Collection-Scanner)
    - auto: Versucht IL2CPP zuerst, fällt auf Pattern zurück
    """
    from scanner.memory_scanner import _attach_process

    method = args.method
    print(f"🔍 Deck-Scan (Methode: {method})")

    # An MTGA-Prozess attachen
    candidate_names = get_macos_mtga_process_names()
    pm = _attach_process(candidate_names)
    if pm is None:
        return 1

    # Karten-DB optional laden
    card_db = None
    if args.card_db:
        print("📖 Lade Karten-DB...")
        card_db = load_card_database()

    # --- IL2CPP Navigation Scan ---
    il2cpp_result: Il2CppScanResult | None = None
    if method in ("il2cpp", "auto"):
        print("🧭 IL2CPP-Navigation: Suche PAPA → DecksManager → _allDecks...")
        adapter = PymemMemoryAdapter(pm)
        il2cpp_result = scan_decks_il2cpp(adapter)
        if il2cpp_result and il2cpp_result.decks:
            print(f"✅ IL2CPP: {len(il2cpp_result.decks)} Decks gefunden")
        elif il2cpp_result:
            print(f"⚠ IL2CPP: keine Decks — {', '.join(il2cpp_result.warnings) or 'unbekannt'}")
        else:
            print("⚠ IL2CPP: Scan fehlgeschlagen")

    # --- Pattern-based Scan ---
    pattern_result: DeckScanResult | None = None
    if method in ("pattern", "auto"):
        anchor_ids = args.anchors or []
        if not anchor_ids and method == "pattern":
            _error("--anchors erforderlich für Pattern-Scan (oder --method auto verwenden)")
            return 1
        if not anchor_ids and method == "auto":
            # Versuche Anker aus Collection-Scan zu holen
            if il2cpp_result and il2cpp_result.decks:
                # Nutze grpIds aus IL2CPP-Decks als Anker
                anchor_ids = list({
                    grp_id
                    for deck in il2cpp_result.decks
                    for pile in deck.piles.values()
                    for grp_id in pile
                })[:20]
            if not anchor_ids:
                # Versuche gespeciherte Anker oder Collection
                from scanner.memory_scanner import _anchor_file
                anchor_file = _anchor_file()
                if anchor_file.exists():
                    import json as _json
                    try:
                        saved = _json.loads(anchor_file.read_text(encoding="utf-8"))
                        anchor_ids = [a[0] for a in saved if isinstance(a, (list, tuple)) and len(a) >= 1]
                    except (OSError, ValueError):
                        pass
            if not anchor_ids:
                if il2cpp_result and il2cpp_result.decks:
                    # IL2CPP already succeeded, no need for pattern fallback
                    pass
                else:
                    _error("Keine Anker für Pattern-Scan verfügbar. Verwende --anchors oder führe vorher 'scan' aus.")
                    return 1

        if anchor_ids:
            print(f"🔎 Pattern-Scan mit {len(anchor_ids)} Anker-GrpIDs...")
            pattern_result = scan_decks(pm, anchor_ids)
            if pattern_result.decks:
                print(f"✅ Pattern: {len(pattern_result.decks)} Decks gefunden")
            else:
                print(f"⚠ Pattern: keine Decks — {', '.join(pattern_result.warnings) or 'unbekannt'}")

    # --- Merge results ---
    all_decks: list[dict[str, Any]] = []
    deck_piles_by_id: dict[str, dict[int, dict[int, int]]] = {}
    warnings_all: list[str] = []

    if il2cpp_result and il2cpp_result.decks:
        warnings_all.extend(il2cpp_result.warnings)
        for deck in il2cpp_result.decks:
            deck_id = str(deck.deck_id) if deck.deck_id else f"il2cpp-{deck.raw_address:#x}"
            all_decks.append({
                "deckId": deck_id,
                "name": deck.name or "Unknown Deck",
                "source": "il2cpp",
                "cards": _piles_to_card_dict(deck.piles),
                "cardsById": _piles_to_cards_by_id(deck.piles),
            })
            deck_piles_by_id[deck_id] = deck.piles

    if pattern_result and pattern_result.decks:
        warnings_all.extend(pattern_result.warnings)
        for deck in pattern_result.decks:
            deck_id = deck.deck_id or f"pattern-{deck.raw_address:#x}"
            # Skip if already found by IL2CPP (dedup by deck_id)
            if any(d["deckId"] == deck_id for d in all_decks):
                continue
            all_decks.append({
                "deckId": deck_id,
                "name": deck.name or "Unknown Deck",
                "source": "pattern",
                "cards": _piles_to_card_dict(deck.piles, card_db),
                "cardsById": _piles_to_cards_by_id(deck.piles),
            })
            deck_piles_by_id[deck_id] = deck.piles

    if not all_decks:
        _error("Keine Decks gefunden.")
        if warnings_all:
            print(f"  Warnungen: {'; '.join(warnings_all)}", file=sys.stderr)
        return 1

    # --- Write artifacts ---
    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    decks_dir = output_dir / "decks"
    decks_dir.mkdir(exist_ok=True)

    # Individual deck files (deck.v1 schema)
    for deck_entry in all_decks:
        deck_id = deck_entry["deckId"]
        deck_path = decks_dir / f"deck-{deck_id}.json"
        payload = {
            "schema": "deck.v1",
            "deckId": deck_id,
            "name": deck_entry["name"],
            "source": deck_entry["source"],
            "cards": deck_entry["cards"],
            "cardsById": deck_entry["cardsById"],
        }
        deck_path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

    # Container file (decks-container.v1)
    container_path = output_dir / "decks-container.json"
    container_payload = {
        "schema": "decks-container.v1",
        "exportedAt": _iso_now_cli(),
        "decks": [
            {
                "deckId": d["deckId"],
                "name": d["name"],
                "source": d["source"],
                "cards": d["cards"],
            }
            for d in all_decks
        ],
        "warnings": warnings_all,
    }
    container_path.write_text(
        json.dumps(container_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    # Also write decks.json (compatible with dashboard/advisor which expect this format)
    decks_json_path = output_dir / "decks.json"
    decks_json_payload = {
        "schema": "decks.v1",
        "exportedAt": _iso_now_cli(),
        "decks": [
            {
                "deckId": d["deckId"],
                "name": d["name"],
                "source": d["source"],
                "cards": d["cards"],
                "cardsById": d["cardsById"],
            }
            for d in all_decks
        ],
        "warnings": warnings_all,
    }
    decks_json_path.write_text(
        json.dumps(decks_json_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    print(f"\n📊 {len(all_decks)} Decks gescannt")
    print(f"Container: {container_path}")
    print(f"decks.json: {decks_json_path}")
    print(f"Deck-Verzeichnis: {decks_dir}")
    return 0


def _iso_now_cli() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _piles_to_card_dict(
    piles: dict[int, dict[int, int]],
    card_db: dict[int, dict[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Convert piles (pile_type → {grpId: qty}) to named card lists by pile key."""
    from scanner.deck_scanner import PILE_KEYS
    result: dict[str, list[dict[str, Any]]] = {}
    for pile_type, key in PILE_KEYS.items():
        pile = piles.get(pile_type, {})
        if not pile:
            continue
        cards = []
        for grp_id, qty in sorted(pile.items()):
            name = f"ID:{grp_id}"
            if card_db and grp_id in card_db:
                name = card_db[grp_id].get("name", name)
            cards.append({"cardId": grp_id, "name": name, "count": qty})
        result[key] = cards
    return result


def _piles_to_cards_by_id(piles: dict[int, dict[int, int]]) -> dict[str, dict[str, int]]:
    """Convert piles to {pileKey: {grpId_str: qty}} format."""
    from scanner.deck_scanner import PILE_KEYS
    result: dict[str, dict[str, int]] = {}
    for pile_type, key in PILE_KEYS.items():
        pile = piles.get(pile_type, {})
        if not pile:
            continue
        result[key] = {str(grp_id): qty for grp_id, qty in sorted(pile.items())}
    return result


def _run_run(args: argparse.Namespace) -> int:
    platform = args.platform or detect_platform()
    if platform == "macos" and not args.logs:
        return _run_scan(args)
    return _run_collection(args)


def _run_validate(args: argparse.Namespace) -> int:
    collection_path = args.output / "collection.json"
    if not collection_path.exists():
        _error(f"collection.json nicht gefunden: {collection_path}")
        return 1

    try:
        import json

        payload = json.loads(collection_path.read_text(encoding="utf-8"))
        cards = {int(card_id): int(quantity) for card_id, quantity in payload.get("cards", {}).items()}
    except (OSError, ValueError, TypeError) as exc:
        _error(f"collection.json konnte nicht gelesen werden: {exc}")
        return 1

    validation = validate_collection(cards, db=load_card_database(refresh_cache=args.refresh_card_db))
    print(f"Valid: {validation['valid']}")
    print(f"Cards: {validation['cardsCount']} unique, {validation['totalCards']} total")
    if validation["errors"]:
        print(f"Errors: {', '.join(validation['errors'])}")
    if validation["warnings"]:
        print(f"Warnings: {', '.join(validation['warnings'])}")
    if validation["unknownCardIdsCount"]:
        suffix = " (truncated)" if validation["unknownCardIdsTruncated"] else ""
        print(f"Unknown card IDs: {validation['unknownCardIdsCount']}{suffix}")
    if not args.no_report:
        import json

        report_path = args.output / "validation-report.json"
        try:
            report_path.write_text(
                json.dumps(
                    {
                        "schema": "validation-report.v1",
                        "collection": collection_path.as_posix(),
                        "validation": validation,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            print(f"Validation report: {report_path}")
        except OSError as exc:
            print(f"Validation report konnte nicht geschrieben werden: {exc}")
    return 0 if validation["valid"] else 1


def _run_deck(args: argparse.Namespace) -> int:
    if args.deck_command == "import":
        if not args.file.exists():
            _error(f"Deckdatei nicht gefunden: {args.file}")
            return 1
        deck = import_arena_deck(
            args.file.read_text(encoding="utf-8"),
            card_db=load_card_database(),
            deck_format=args.format,
            name=args.name,
        )
        path = write_deck(deck, args.output)
        print(f"Deck importiert: {path}")
        warnings = deck.get("diagnostics", {}).get("warnings", [])
        if warnings:
            print(f"Warnings: {', '.join(warnings)}")
        return 0 if not warnings else 1
    return 1


def _run_advisor(args: argparse.Namespace) -> int:
    if args.advisor_command == "complete":
        if not args.collection.exists():
            _error(f"collection.json nicht gefunden: {args.collection}")
            return 1
        if not args.deck.exists():
            _error(f"arena_deck.json nicht gefunden: {args.deck}")
            return 1
        result = build_completion_advice(
            collection=load_json(args.collection),
            deck=load_json(args.deck),
            card_db=load_card_database(),
        )
        path = write_advisor_result(result, args.output)
        summary = result["summary"]
        print(f"Advisor Result: {path}")
        print(
            "Completion: "
            f"{summary['completionScore']}% "
            f"({summary['missingCards']} missing cards, "
            f"{summary['missingUniqueCards']} unique)"
        )
        if result["warnings"]:
            print(f"Warnings: {', '.join(result['warnings'])}")
        return 0
    if args.advisor_command == "llm":
        if not args.collection.exists():
            _error(f"collection.json nicht gefunden: {args.collection}")
            return 1

        # CLI-Overrides für LLM-Konfiguration sammeln
        cli_overrides = {}
        if args.endpoint:
            cli_overrides["endpoint"] = args.endpoint
        if args.model:
            cli_overrides["model_name"] = args.model
        if args.temperature is not None:
            cli_overrides["temperature"] = args.temperature
        if args.max_tokens is not None:
            cli_overrides["max_tokens"] = args.max_tokens

        # Config laden (Datei + Env + CLI)
        llm_config = load_config(config_path=args.config, cli_overrides=cli_overrides)

        print(f"LLM-Advisor wird ausgeführt...")
        print(f"  Endpoint: {llm_config.endpoint}")
        print(f"  Model: {llm_config.model_name}")
        print(f"  Temperature: {llm_config.temperature}")
        print(f"  Max Tokens: {llm_config.max_tokens}")
        print(f"  Collection: {args.collection}")
        if args.decks and args.decks.exists():
            print(f"  Decks: {args.decks}")
        else:
            print("  Decks: keine gefunden (nur Collection-Analyse)")

        result = run_llm_advisor(
            collection_path=args.collection,
            decks_path=args.decks if args.decks and args.decks.exists() else None,
            output_dir=args.output,
            llm_config=llm_config,
        )
        if result.warnings:
            for w in result.warnings:
                print(f"  WARN: {w}")
        if result.summary:
            s = result.summary
            print(f"\nTop Priority: {s.get('topPriority', '?')}")
            print(f"Analysed: {s.get('analysedDecks', 0)} Decks")
        if result.craftingPriorities:
            print(f"\nCrafting Priorities ({len(result.craftingPriorities)}):")
            for p in result.craftingPriorities[:5]:
                cards = p.get("cards", [])
                card_names = ", ".join(c.get("name", "?") for c in cards[:3])
                if len(cards) > 3:
                    card_names += f" ... (+{len(cards)-3})"
                print(f"  - {p.get('reason', '?')}: {card_names}")
        if result.deckOptimizations:
            print(f"\nDeck Optimizations ({len(result.deckOptimizations)}):")
            for d in result.deckOptimizations[:3]:
                print(f"  - {d.get('deckName', '?')}: {', '.join(d.get('issues', [])[:2])}")
        if result.metaNotes:
            print(f"\nMeta Notes ({len(result.metaNotes)}):")
            for n in result.metaNotes[:3]:
                print(f"  - {n}")
        print(f"\nResult written to: {args.output / 'advisor-result.json'}")
        return 0

    if args.advisor_command == "init-config":
        path = write_default_config(args.output)
        print(f"Default-Config geschrieben: {path}")
        print(f"")
        print(f"Bearbeite die Datei und passe endpoint, temperature etc. an.")
        print(f"Danach: python -m cli.main advisor llm")
        return 0
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "collection":
        return _run_collection(args)
    if args.command == "decks":
        return _run_decks(args)
    if args.command == "scan":
        return _run_scan(args)
    if args.command == "deck-scan":
        return _run_deck_scan(args)
    if args.command == "run":
        return _run_run(args)
    if args.command == "validate":
        return _run_validate(args)
    if args.command == "deck":
        return _run_deck(args)
    if args.command == "advisor":
        return _run_advisor(args)
    if args.command == "serve":
        return _run_serve(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
