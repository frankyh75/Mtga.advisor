from __future__ import annotations

import argparse
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Sequence

from parser.export import export_collection
from parser.pipeline import parse_collection
from parser.log_paths import (
    MissingLogsError,
    PathConfig,
    detect_platform,
    discover_logs,
)
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

    decks = subparsers.add_parser("decks", help="Zeigt eine kurze Deckvorschau aus MTGA-Logs.")
    decks.add_argument(
        "--log",
        dest="logs",
        action="append",
        type=Path,
        help="Pfad zu einer Logdatei (mehrfach angeben m??glich). Wenn nicht gesetzt, werden Logs automatisch gesucht.",
    )
    decks.add_argument(
        "--platform",
        choices=["windows", "macos", "unknown"],
        help="Plattform ??berschreiben (Standard: automatische Erkennung).",
    )
    decks.add_argument(
        "--windows-local-low",
        type=Path,
        help="Override f??r Windows LocalLow MTGA Pfad.",
    )
    decks.add_argument(
        "--windows-steam-userdata",
        type=Path,
        help="Override f??r Windows Steam userdata Pfad.",
    )
    decks.add_argument(
        "--macos-logs",
        type=Path,
        help="Override f??r macOS Log-Pfad.",
    )
    decks.add_argument(
        "--macos-steam-userdata",
        type=Path,
        help="Override f??r macOS Steam userdata Pfad.",
    )
    decks.add_argument(
        "--custom-log-dir",
        action="append",
        type=Path,
        default=[],
        help="Zus??tzliche Log-Verzeichnisse, die gepr??ft werden sollen.",
    )

    carddb = subparsers.add_parser("carddb", help="Importiert lokale CardDB Dateien.")
    carddb_sub = carddb.add_subparsers(dest="carddb_command", required=True)
    carddb_import = carddb_sub.add_parser("import", help="Importiert eine Scryfall Bulk JSON Datei.")
    carddb_import.add_argument("--input", type=Path, required=True, help="Pfad zur Bulk JSON Datei.")
    carddb_import.add_argument(
        "--source",
        choices=["oracle", "default"],
        required=True,
        help="Quelle der Bulk Datei.",
    )
    carddb_import.add_argument(
        "--db",
        type=Path,
        default=Path("data/carddb/carddb.sqlite"),
        help="Pfad zur SQLite Datenbank.",
    )
    carddb_import.add_argument(
        "--language",
        type=str,
        default="en",
        help="Sprache der Bulk Datei (Standard: en).",
    )
    carddb_import.add_argument(
        "--bulk-version",
        type=str,
        help="Optional: Bulk-Version/Datum (z. B. 2026-01-23).",
    )
    carddb_info = carddb_sub.add_parser("info", help="Zeigt kurze CardDB Statistiken.")
    carddb_info.add_argument(
        "--db",
        type=Path,
        default=Path("data/carddb/carddb.sqlite"),
        help="Pfad zur SQLite Datenbank.",
    )

    analysis_cmd = subparsers.add_parser(
        "deck-analysis", help="Erzeugt deck-analysis.json Dateien aus Logs + CardDB."
    )
    analysis_cmd.add_argument(
        "--log",
        dest="logs",
        action="append",
        type=Path,
        help="Pfad zu einer Logdatei (mehrfach angeben m??glich). Wenn nicht gesetzt, werden Logs automatisch gesucht.",
    )
    analysis_cmd.add_argument(
        "--platform",
        choices=["windows", "macos", "unknown"],
        help="Plattform ??berschreiben (Standard: automatische Erkennung).",
    )
    analysis_cmd.add_argument(
        "--windows-local-low",
        type=Path,
        help="Override f??r Windows LocalLow MTGA Pfad.",
    )
    analysis_cmd.add_argument(
        "--windows-steam-userdata",
        type=Path,
        help="Override f??r Windows Steam userdata Pfad.",
    )
    analysis_cmd.add_argument(
        "--macos-logs",
        type=Path,
        help="Override f??r macOS Log-Pfad.",
    )
    analysis_cmd.add_argument(
        "--macos-steam-userdata",
        type=Path,
        help="Override f??r macOS Steam userdata Pfad.",
    )
    analysis_cmd.add_argument(
        "--custom-log-dir",
        action="append",
        type=Path,
        default=[],
        help="Zus??tzliche Log-Verzeichnisse, die gepr??ft werden sollen.",
    )
    analysis_cmd.add_argument(
        "--db",
        type=Path,
        default=Path("data/carddb/carddb.sqlite"),
        help="Pfad zur SQLite CardDB.",
    )
    analysis_cmd.add_argument(
        "--mapping-csv",
        type=Path,
        default=Path("data/mtga_to_scryfall.csv"),
        help="Optional: Mapping CSV (cardId,scryfall_id,oracle_id).",
    )
    analysis_cmd.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Ausgabeverzeichnis (Standard: ./out).",
    )
    analysis_cmd.add_argument(
        "--deck-id",
        type=str,
        help="Optional: nur ein spezifisches Deck analysieren (DeckId).",
    )
    analysis_cmd.add_argument(
        "--include-saved",
        action="store_true",
        help="Saved Decks zusaetzlich zu Last-Played analysieren.",
    )

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


def _resolve_logs(args: argparse.Namespace) -> tuple[list[Path] | None, str | None]:
    if args.logs:
        log_paths = [Path(path) for path in args.logs]
        missing = [path for path in log_paths if not path.exists()]
        if missing:
            missing_str = ", ".join(path.as_posix() for path in missing)
            return None, f"Logdatei(en) nicht gefunden: {missing_str}"
        return log_paths, None
    platform = args.platform or detect_platform()
    config = _path_config_from_args(args)
    try:
        discovery = discover_logs(platform, config)
    except MissingLogsError as exc:
        return None, str(exc)
    return discovery.found, None


def _run_collection(args: argparse.Namespace) -> int:
    resolved, error = _resolve_logs(args)
    if error:
        _error(error)
        return 1
    log_paths = resolved or []

    export_paths = export_collection(log_paths, args.output)
    print(f"Collection exportiert: {export_paths.collection}")
    print(f"Run-Report: {export_paths.run_report}")
    print(f"Raw Samples: {export_paths.raw_samples_dir}")
    return 0


def _run_serve(args: argparse.Namespace) -> int:
    run_server(host=args.host, port=args.port, output_dir=args.output)
    return 0


def _run_decks(args: argparse.Namespace) -> int:
    resolved, error = _resolve_logs(args)
    if error:
        _error(error)
        return 1
    log_paths = resolved or []
    report = parse_collection(log_paths)
    decks = report.decks or []
    print(f"Decks gefunden: {len(decks)}")
    for index, deck in enumerate(decks, start=1):
        source = deck.get("source", "unknown")
        name = deck.get("name", "<unbenannt>")
        fmt = deck.get("format", "unknown")
        deck_id = deck.get("id", "")
        main_total = _sum_deck_cards(deck.get("mainDeck", []))
        side_total = _sum_deck_cards(deck.get("sideboard", []))
        command_total = _sum_deck_cards(deck.get("commandZone", []))
        companions_total = _sum_deck_cards(deck.get("companions", []))
        extras = []
        if command_total:
            extras.append(f"command={command_total}")
        if companions_total:
            extras.append(f"companions={companions_total}")
        extra_str = f" ({', '.join(extras)})" if extras else ""
        print(
            f"{index}. [{source}] {name} ({fmt}) id={deck_id} main={main_total} side={side_total}{extra_str}"
        )
    return 0


def _run_carddb(args: argparse.Namespace) -> int:
    from carddb import import_bulk_json, open_db, read_meta

    if args.carddb_command == "import":
        if not args.input.exists():
            _error(f"Bulk Datei nicht gefunden: {args.input.as_posix()}")
            return 1
        conn = open_db(args.db)
        fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        meta = {"bulk_source": "local", "language": args.language, "fetched_at": fetched_at}
        if args.bulk_version:
            meta["bulk_version"] = args.bulk_version
        result = import_bulk_json(
            conn,
            args.input,
            source=args.source,
            meta=meta,
        )
        print(
            f"CardDB importiert: {result.cards_inserted} Karten ({result.source}) -> {args.db.as_posix()}"
        )
        return 0
    if args.carddb_command == "info":
        conn = open_db(args.db)
        meta = read_meta(conn)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM cards_oracle")
        oracle_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM cards_printings")
        printing_count = cursor.fetchone()[0]
        print(f"CardDB: oracle={oracle_count} printings={printing_count}")
        if meta:
            for key in sorted(meta.keys()):
                print(f"{key}: {meta[key]}")
        return 0
    _error("Unbekannter carddb Befehl.")
    return 1


def _run_deck_analysis(args: argparse.Namespace) -> int:
    from analysis.export import export_deck_analysis
    from carddb import load_mapping, open_db

    resolved, error = _resolve_logs(args)
    if error:
        _error(error)
        return 1
    if not args.db.exists():
        _error(f"CardDB nicht gefunden: {args.db.as_posix()}")
        return 1
    log_paths = resolved or []
    report = parse_collection(log_paths)
    if args.include_saved:
        decks = [
            deck
            for deck in (report.decks or [])
            if deck.get("source") in ("last_played", "saved")
        ]
    else:
        decks = [deck for deck in (report.decks or []) if deck.get("source") == "last_played"]
    if args.deck_id:
        decks = [deck for deck in decks if deck.get("id") == args.deck_id]
    if not decks:
        _error("Keine Last-Played Decks gefunden.")
        return 1
    conn = open_db(args.db)
    mapping = load_mapping(args.mapping_csv)
    export_paths = export_deck_analysis(decks, conn=conn, output_dir=args.output, mapping=mapping)
    print(f"Deck-Analyse Summary: {export_paths.summary}")
    print(f"Deck-Analyse Dateien: {export_paths.deck_dir}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "collection":
        return _run_collection(args)
    if args.command == "decks":
        return _run_decks(args)
    if args.command == "carddb":
        return _run_carddb(args)
    if args.command == "deck-analysis":
        return _run_deck_analysis(args)
    if args.command == "serve":
        return _run_serve(args)
    parser.print_help()
    return 1


def _sum_deck_cards(entries: list[dict]) -> int:
    total = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        quantity = entry.get("quantity")
        if isinstance(quantity, int):
            total += quantity
    return total


if __name__ == "__main__":
    sys.exit(main())
