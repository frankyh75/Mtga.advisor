from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from advisor.completion import build_completion_advice, load_json, write_advisor_result
from advisor.deck_import import import_arena_deck, write_deck
from parser.decks import export_decks
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

    scan = subparsers.add_parser("scan", help="Scannt die macOS-Collection aus dem MTGA-Prozessspeicher.")
    scan.add_argument(
        "--output",
        type=Path,
        default=Path("out-memory"),
        help="Ausgabeverzeichnis (Standard: ./out-memory).",
    )
    scan.add_argument("--debug", action="store_true", help="Gibt Scan-Statistiken pro Anker aus.")

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
    advisor_complete = advisor_subparsers.add_parser("complete", help="Berechnet Deck-Completion.")
    advisor_complete.add_argument("--collection", type=Path, required=True, help="Pfad zu collection.json.")
    advisor_complete.add_argument("--deck", type=Path, required=True, help="Pfad zu arena_deck.json.")
    advisor_complete.add_argument("--output", type=Path, default=Path("out"), help="Ausgabeverzeichnis.")

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

    export_paths = export_decks(log_paths, args.output)
    print(f"Decks exportiert: {export_paths.decks}")
    print(f"Run-Report: {export_paths.run_report}")
    return 0


def _run_scan(args: argparse.Namespace) -> int:
    result = scan_memory_collection_detailed(debug=args.debug)
    if result is None:
        return 1
    collection_path, run_report_path = write_collection_artifacts(result.collection, args.output, scan_result=result)
    print(f"Collection exportiert: {collection_path}")
    print(f"Run-Report: {run_report_path}")
    return 0


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
