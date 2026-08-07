from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Sequence

from advisor.completion import build_completion_advice, load_json, write_advisor_result
from advisor.deck_export import export_deck_to_arena_text, load_deck_by_id, DeckNotFoundError, DeckExportError
from advisor.deck_import import import_arena_deck, write_deck
from advisor.analyze import analyze_deck, arena_deck_to_deck_cards, load_deck_cards
from advisor.llm_advisor import run_llm_advisor
from advisor.llm_config import LLMConfig, load_config, write_default_config
from parser.decks import export_decks, export_container, show_deck, list_decks
from parser.deck_cards import export_deck_cards
from parser.wildcards import export_wildcards
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
from scanner.history import (
    save_snapshot as save_history_snapshot,
    list_snapshots as list_history_snapshots,
    diff_snapshots as diff_history_snapshots,
    format_diff_summary as format_history_diff,
    format_snapshot_list as format_history_list,
)
from scanner.il2cpp_nav import (
    PymemMemoryAdapter,
    Il2CppScanResult,
    scan_decks_il2cpp,
)
from scanner.rank_scanner import (
    scan_ranks_and_account,
    format_rank_table,
    format_rank_summary,
    format_account_table,
    format_account_summary,
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
        "--windows-local-low",
        type=Path,
        help="Override für Windows LocalLow MTGA Pfad.",
    )
    decks.add_argument(
        "--windows-steam-userdata",
        type=Path,
        help="Override für Windows Steam userdata Pfad.",
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
    deck_container = decks.add_subparsers(dest="deck_command")
    deck_container.required = False
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

    wildcards = subparsers.add_parser(
        "wildcards",
        help="Exportiert Wildcards (StartHook-InventoryInfo) aus MTGA-Logs.",
    )
    wildcards.add_argument(
        "--log",
        dest="logs",
        action="append",
        type=Path,
        help="Pfad zu einer Logdatei (mehrfach angeben möglich). Wenn nicht gesetzt, werden Logs automatisch gesucht.",
    )
    wildcards.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Ausgabeverzeichnis (Standard: ./out).",
    )
    wildcards.add_argument(
        "--platform",
        choices=["windows", "macos", "unknown"],
        help="Plattform überschreiben (Standard: automatische Erkennung).",
    )
    wildcards.add_argument(
        "--windows-local-low",
        type=Path,
        help="Override für Windows LocalLow MTGA Pfad.",
    )
    wildcards.add_argument(
        "--windows-steam-userdata",
        type=Path,
        help="Override für Windows Steam userdata Pfad.",
    )
    wildcards.add_argument(
        "--macos-logs",
        type=Path,
        help="Override für macOS Log-Pfad.",
    )
    wildcards.add_argument(
        "--macos-steam-userdata",
        type=Path,
        help="Override für macOS Steam userdata Pfad.",
    )
    wildcards.add_argument(
        "--custom-log-dir",
        action="append",
        type=Path,
        default=[],
        help="Zusätzliche Log-Verzeichnisse, die geprüft werden sollen.",
    )

    deck_cards = subparsers.add_parser(
        "deck-cards",
        help="Exportiert Deck-Kartenlisten (DeckUpsertDeckV3) aus MTGA-Logs.",
    )
    deck_cards.add_argument(
        "--log",
        dest="logs",
        action="append",
        type=Path,
        help="Pfad zu einer Logdatei (mehrfach angeben möglich). Wenn nicht gesetzt, werden Logs automatisch gesucht.",
    )
    deck_cards.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Ausgabeverzeichnis (Standard: ./out).",
    )
    deck_cards.add_argument(
        "--platform",
        choices=["windows", "macos", "unknown"],
        help="Plattform überschreiben (Standard: automatische Erkennung).",
    )
    deck_cards.add_argument(
        "--windows-local-low",
        type=Path,
        help="Override für Windows LocalLow MTGA Pfad.",
    )
    deck_cards.add_argument(
        "--windows-steam-userdata",
        type=Path,
        help="Override für Windows Steam userdata Pfad.",
    )
    deck_cards.add_argument(
        "--macos-logs",
        type=Path,
        help="Override für macOS Log-Pfad.",
    )
    deck_cards.add_argument(
        "--macos-steam-userdata",
        type=Path,
        help="Override für macOS Steam userdata Pfad.",
    )
    deck_cards.add_argument(
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
    scan.add_argument(
        "--all",
        action="store_true",
        help="Kombinierter Scan: Collection + Decks + Ranks in einem Durchlauf (über eine Helper-Verbindung).",
    )
    scan.add_argument(
        "--no-collection",
        action="store_true",
        help="Mit --all: Collection-Scan überspringen.",
    )
    scan.add_argument(
        "--no-decks",
        action="store_true",
        help="Mit --all: Deck-Scan überspringen.",
    )
    scan.add_argument(
        "--no-ranks",
        action="store_true",
        help="Mit --all: Rank-Scan überspringen.",
    )
    scan.add_argument(
        "--no-account",
        action="store_true",
        help="Mit --all: Account-Info im Rank-Scan überspringen.",
    )

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

    ranks = subparsers.add_parser(
        "ranks",
        help="Scannt Spieler-Rang und Account-Info aus dem MTGA-Prozessspeicher (IL2CPP).",
    )
    ranks.add_argument(
        "--json",
        action="store_true",
        help="Ausgabe als JSON statt Text-Tabelle.",
    )
    ranks.add_argument(
        "--no-account",
        action="store_true",
        help="Nur Ränge scannen, Account-Info überspringen.",
    )
    ranks.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional: JSON-Ergebnis in Datei schreiben.",
    )

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
    deck_subparsers = deck.add_subparsers(dest="deck_command")
    deck_import = deck_subparsers.add_parser("import", help="Importiert eine Arena-Textdeckliste.")
    deck_import.add_argument("--file", type=Path, required=True, help="Pfad zur Arena-Textdeckliste.")
    deck_import.add_argument("--format", required=True, help="Zielformat, z. B. standard oder brawl.")
    deck_import.add_argument("--name", help="Deckname überschreiben.")
    deck_import.add_argument("--output", type=Path, default=Path("out"), help="Ausgabeverzeichnis.")

    deck_export = deck_subparsers.add_parser("export", help="Exportiert ein Deck als Arena-kompatiblen Text.")
    deck_export.add_argument("deck_id", type=str, help="Die Deck-ID des zu exportierenden Decks.")
    deck_export.add_argument(
        "--search-dir",
        dest="search_dirs",
        action="append",
        type=Path,
        help="Verzeichnis, in dem nach Decks gesucht wird (mehrfach möglich). Default: out, out-decks.",
    )
    deck_export.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Ausgabedatei (Default: stdout).",
    )

    advisor = subparsers.add_parser("advisor", help="Regelbasierte Advisor-Funktionen.")
    advisor_subparsers = advisor.add_subparsers(dest="advisor_command", required=True)
    advisor_complete = advisor_subparsers.add_parser("complete", help="Berechnet Deck-Completion (regelbasiert).")
    advisor_complete.add_argument("--collection", type=Path, required=True, help="Pfad zu collection.json.")
    advisor_complete.add_argument("--deck", type=Path, required=True, help="Pfad zu arena_deck.json.")
    advisor_complete.add_argument("--output", type=Path, default=Path("out"), help="Ausgabeverzeichnis.")

    advisor_analyze = advisor_subparsers.add_parser(
        "analyze",
        help="Analysiert ein Deck gegen Collection + Wildcards (owned/missing, Craft-Priorität).",
    )
    advisor_analyze.add_argument(
        "--deck-text",
        type=Path,
        default=None,
        help="Pfad zu einer Arena-Textdeckliste. Wird direkt gegen Collection + Wildcards analysiert.",
    )
    advisor_analyze.add_argument(
        "--deck-text-stdin",
        action="store_true",
        help="Lese die Arena-Textdeckliste von stdin (Alternative zu --deck-text).",
    )
    advisor_analyze.add_argument(
        "--format",
        type=str,
        default="standard",
        help="Deck-Format für den Import (standard, historic, brawl, ...). Default: standard.",
    )
    advisor_analyze.add_argument(
        "--deck",
        type=str,
        help="Deck-ID des zu analysierenden Decks (aus deck-cards.json).",
    )
    advisor_analyze.add_argument(
        "--name",
        type=str,
        help="Deck-Name des zu analysierenden Decks (Alternative zu --deck).",
    )
    advisor_analyze.add_argument(
        "--collection",
        type=Path,
        default=Path("out/collection.json"),
        help="Pfad zu collection.json (Default: out/collection.json).",
    )
    advisor_analyze.add_argument(
        "--wildcards",
        type=Path,
        default=Path("out/wildcards.json"),
        help="Pfad zu wildcards.json (Default: out/wildcards.json).",
    )
    advisor_analyze.add_argument(
        "--deck-cards",
        dest="deck_cards_path",
        type=Path,
        default=Path("out/deck-cards.json"),
        help="Pfad zu deck-cards.json (Default: out/deck-cards.json).",
    )
    advisor_analyze.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Ausgabeverzeichnis (Default: out).",
    )
    advisor_analyze.add_argument(
        "--json",
        action="store_true",
        help="Ausgabe als JSON statt Text-Zusammenfassung.",
    )

    advisor_llm = advisor_subparsers.add_parser("llm", help="LLM-gestützte Wildcard- und Deck-Optimierung.")
    advisor_llm.add_argument("--collection", type=Path, default=Path("out/collection.json"), help="Pfad zu collection.json (Default: out/collection.json).")
    advisor_llm.add_argument("--decks", type=Path, default=Path("out/decks.json"), help="Pfad zu decks.json (Default: out/decks.json).")
    advisor_llm.add_argument("--output", type=Path, default=Path("out"), help="Ausgabeverzeichnis (Default: out).")
    advisor_llm.add_argument("--endpoint", help="LLM-Endpoint überschreiben (z.B. http://127.0.0.1:8081/v1/chat/completions).")
    advisor_llm.add_argument("--model", help="Modellname (für Logging/Anzeige).")
    advisor_llm.add_argument("--temperature", type=float, help="Sampling-Temperatur (0.0 - 1.0).")
    advisor_llm.add_argument("--max-tokens", type=int, help="Maximale Token-Anzahl.")
    advisor_llm.add_argument("--config", type=Path, help="Pfad zu einer Config-Datei (JSON/YAML).")
    advisor_llm.add_argument(
        "--meta-format",
        default="standard",
        help="Format für Meta-Daten (standard, modern, etc.). Set to empty to disable. Default: standard.",
    )
    advisor_llm.add_argument(
        "--no-meta",
        action="store_true",
        help="Meta-Daten nicht in den LLM-Prompt einfügen.",
    )

    advisor_config = advisor_subparsers.add_parser("init-config", help="Erzeugt eine Default-Config-Datei.")
    advisor_config.add_argument("--output", type=Path, default=None, help="Zielpfad (Default: ~/.config/mtga-advisor/config.json).")

    advisor_meta = advisor_subparsers.add_parser("meta", help="Zeigt aktuelle Meta-Daten von MTGGoldfish.")
    advisor_meta.add_argument(
        "--format",
        default="standard",
        help="Format (standard, modern, pioneer, historic, explorer, timeless, alchemy, pauper, legacy, vintage, brawl, commander). Default: standard.",
    )
    advisor_meta.add_argument(
        "--full",
        action="store_true",
        help="Scrape die /full Seite (alle Decks, nicht nur Top-12).",
    )
    advisor_meta.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignoriere den Cache und lade neu von MTGGoldfish.",
    )
    advisor_meta.add_argument(
        "--json",
        action="store_true",
        help="Ausgabe als JSON statt Text-Tabelle.",
    )
    advisor_meta.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional: JSON-Ergebnis in Datei schreiben.",
    )
    advisor_meta.add_argument(
        "--max-decks",
        type=int,
        default=20,
        help="Maximale Anzahl Decks in der Ausgabe (Default: 20).",
    )

    serve = subparsers.add_parser("serve", help="Startet einen lokalen Server für die Artefakte.")
    serve.add_argument("--host", default=DEFAULT_HOST, help=f"Host (Standard: {DEFAULT_HOST} — vom Handy erreichbar).")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (Standard: {DEFAULT_PORT}).")
    serve.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Verzeichnis mit collection.json/run-report.json (Standard: ./out).",
    )
    serve.add_argument(
        "--auth",
        default=None,
        help="Basic-Auth Credentials als user:password (oder via MTGA_ADVISOR_AUTH env var).",
    )
    serve.add_argument(
        "--no-qr",
        action="store_true",
        help="QR-Code im Terminal unterdrücken.",
    )

    # --- history subcommand ---
    history = subparsers.add_parser(
        "history",
        help="Collection-History: Snapshots speichern und Diffs anzeigen.",
    )
    history_subparsers = history.add_subparsers(dest="history_command", required=True)

    history_save = history_subparsers.add_parser(
        "save",
        help="Speichert einen Snapshot der aktuellen Collection/Decks.",
    )
    history_save.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Verzeichnis mit collection.json/decks.json (Standard: ./out).",
    )

    history_list = history_subparsers.add_parser(
        "list",
        help="Listet alle gespeicherten Snapshots auf.",
    )
    history_list.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Verzeichnis mit history/ Unterverzeichnis (Standard: ./out).",
    )

    history_diff = history_subparsers.add_parser(
        "diff",
        help="Zeigt Änderungen zwischen zwei Snapshots (Default: letzter vs. vorletzter).",
    )
    history_diff.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Verzeichnis mit history/ Unterverzeichnis (Standard: ./out).",
    )
    history_diff.add_argument(
        "--older",
        help="Timestamp des älteren Snapshots (Default: vorletzter).",
    )
    history_diff.add_argument(
        "--newer",
        help="Timestamp des neueren Snapshots (Default: letzter).",
    )
    history_diff.add_argument(
        "--json",
        action="store_true",
        help="Gibt das Diff als JSON aus (statt formatiertem Text).",
    )

    # --- watch subcommand ---
    watch = subparsers.add_parser(
        "watch",
        help="Watch-Mode: regelmäßiger Scan, wenn MTGA läuft.",
    )
    watch.add_argument(
        "--interval",
        type=int,
        default=300,
        help="Scan-Intervall in Sekunden (Default: 300 = 5 Min). Minimum: 30.",
    )
    watch.add_argument(
        "--output",
        type=Path,
        default=Path("out"),
        help="Ausgabeverzeichnis für Scan-Artefakte (Standard: ./out).",
    )
    watch.add_argument(
        "--debug",
        action="store_true",
        help="Debug-Output aktivieren.",
    )
    watch.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="Optional: schreibt den Watch-Status in diese JSON-Datei (für Monitoring).",
    )
    watch.add_argument(
        "--single-shot",
        action="store_true",
        help="Führt genau einen Scan aus und beendet (nur wenn MTGA läuft). Für Tests/CI.",
    )

    return parser


def _path_config_from_args(args: argparse.Namespace) -> PathConfig:
    return PathConfig(
        windows_local_low=getattr(args, "windows_local_low", None),
        windows_steam_userdata=getattr(args, "windows_steam_userdata", None),
        macos_logs=getattr(args, "macos_logs", None),
        macos_steam_userdata=getattr(args, "macos_steam_userdata", None),
        custom_logs=getattr(args, "custom_log_dir", []) or [],
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
    run_server(
        host=args.host,
        port=args.port,
        output_dir=args.output,
        auth=getattr(args, "auth", None),
        show_qr=not getattr(args, "no_qr", False),
    )
    return 0


def _run_history(args: argparse.Namespace) -> int:
    """Collection-History Subcommand: save, list, diff."""
    if args.history_command == "save":
        return _run_history_save(args)
    if args.history_command == "list":
        return _run_history_list(args)
    if args.history_command == "diff":
        return _run_history_diff(args)
    return 1


def _run_history_save(args: argparse.Namespace) -> int:
    """Speichert einen Snapshot der aktuellen Collection/Decks."""
    output_dir = args.output
    collection_path = output_dir / "collection.json"
    decks_path = output_dir / "decks.json"

    if not collection_path.exists() and not decks_path.exists():
        _error(f"Weder collection.json noch decks.json gefunden in {output_dir}")
        return 1

    history_dir = save_history_snapshot(output_dir=output_dir)
    snapshots = list_history_snapshots(output_dir=output_dir)
    print(f"Snapshot gespeichert: {history_dir}")
    print(f"  Snapshots insgesamt: {len(snapshots)}")
    latest = snapshots[-1] if snapshots else None
    if latest:
        print(f"  Timestamp: {latest['timestamp']}")
        files = latest.get("files", {})
        if files:
            print(f"  Dateien: {', '.join(files.keys())}")
        if "collectionStats" in latest:
            cs = latest["collectionStats"]
            print(f"  Collection: {cs['uniqueCards']} unique, {cs['totalCards']} total")
        if "deckStats" in latest:
            ds = latest["deckStats"]
            print(f"  Decks: {ds['deckCount']}")
    return 0


def _run_history_list(args: argparse.Namespace) -> int:
    """Listet alle gespeicherten Snapshots auf."""
    snapshots = list_history_snapshots(output_dir=args.output)
    print(format_history_list(snapshots))
    return 0


def _run_history_diff(args: argparse.Namespace) -> int:
    """Zeigt Änderungen zwischen zwei Snapshots."""
    diff = diff_history_snapshots(
        output_dir=args.output,
        older=args.older,
        newer=args.newer,
    )
    if args.json:
        print(json.dumps(diff, ensure_ascii=False, indent=2))
    else:
        print(format_history_diff(diff))
    return 0 if "error" not in diff else 1


def _run_decks(args: argparse.Namespace) -> int:
    # Container-Subcommands (list, show, container)
    if hasattr(args, 'deck_command') and args.deck_command:
        if args.deck_command == 'list':
            return _run_deck_list(args)
        elif args.deck_command == 'show':
            return _run_deck_show(args)
        elif args.deck_command == 'container':
            return _run_deck_container(args)

    if getattr(args, "deck_command", None) is None:
        # Standardverhalten: Deck-Summaries aus Logs exportieren.
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


def _is_helper_available() -> bool:
    """Prüft, ob der Sudo-Helper-Daemon läuft und erreichbar ist."""
    from scanner.helper_client import is_helper_available as _check
    return _check()


def _run_wildcards(args: argparse.Namespace) -> int:
    """Exportiert Wildcards aus StartHook-InventoryInfo-Events."""
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

    export_paths = export_wildcards(log_paths, args.output)
    print(f"Wildcards exportiert: {export_paths.wildcards}")
    print(f"Run-Report: {export_paths.run_report}")

    # Kurze Zusammenfassung
    import json as _json
    data = _json.loads(export_paths.wildcards.read_text(encoding="utf-8"))
    wc = data.get("wildcards", {})
    currency = data.get("currency", {})
    print(
        f"  Commons: {wc.get('commons')}, Uncommons: {wc.get('uncommons')}, "
        f"Rares: {wc.get('rares')}, Mythics: {wc.get('mythics')}"
    )
    print(f"  Gold: {currency.get('gold')}, Gems: {currency.get('gems')}")
    return 0


def _run_deck_cards(args: argparse.Namespace) -> int:
    """Exportiert Deck-Kartenlisten aus DeckUpsertDeckV3-Events."""
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

    export_paths = export_deck_cards(log_paths, args.output)
    print(f"Deck-Kartenlisten exportiert: {export_paths.deck_cards}")
    print(f"Run-Report: {export_paths.run_report}")

    # Kurze Zusammenfassung
    import json as _json
    data = _json.loads(export_paths.deck_cards.read_text(encoding="utf-8"))
    decks = data.get("decks", [])
    print(f"  Decks mit Kartenlisten: {len(decks)}")
    for deck in decks:
        main_count = sum(c.get("quantity", 0) for c in deck.get("mainDeck", []))
        sb_count = sum(c.get("quantity", 0) for c in deck.get("sideboard", []))
        print(f"  - {deck.get('name', '?')} ({deck.get('deckId', '?')[:8]}...): Main {main_count}, SB {sb_count}")
    return 0


def _run_scan_via_helper(args: argparse.Namespace) -> int:
    """Führt den Memory-Scan über den Helper-Daemon aus (sudo-frei)."""
    from scanner.memory_scanner import scan_collection_detailed

    result = scan_collection_detailed(debug=args.debug, use_helper=True)
    if result is None:
        return 1
    collection_path, run_report_path = write_collection_artifacts(
        result.collection, args.output, scan_result=result
    )
    print(f"Collection exportiert: {collection_path}")
    print(f"Run-Report: {run_report_path}")
    return 0


def _run_scan_all(args: argparse.Namespace) -> int:
    """Kombinierter Scan: Collection + Decks + Ranks in einem Durchlauf.

    Wenn der Helper-Daemon verfügbar ist, wird eine einzige persistente
    Socket-Verbindung für alle drei Scans verwendet (sudo-frei).
    """
    from scanner.combined_scanner import scan_all

    result = scan_all(
        debug=args.debug,
        scan_collection=not args.no_collection,
        scan_decks=not args.no_decks,
        scan_ranks=not args.no_ranks,
        read_account=not args.no_account,
        print_fn=print,
    )

    if result.errors and not result.collection and not result.decks and not result.ranks:
        print(f"❌ Kombinierter Scan fehlgeschlagen: {'; '.join(result.errors)}")
        return 1

    # Collection-Artefakte schreiben
    if result.collection is not None:
        collection_path, run_report_path = write_collection_artifacts(
            result.collection.collection, args.output, scan_result=result.collection
        )
        print(f"\nCollection exportiert: {collection_path}")
        print(f"Run-Report: {run_report_path}")
    elif not args.no_collection:
        print("⚠ Collection-Scan: keine Daten")

    # Deck-Artefakte schreiben
    if result.decks:
        import json as _json
        decks_path = args.output / "decks.json"
        args.output.mkdir(parents=True, exist_ok=True)
        decks_path.write_text(
            _json.dumps(result.decks, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Decks exportiert: {decks_path} ({len(result.decks)} Decks)")
    elif not args.no_decks:
        print("⚠ Deck-Scan: keine Decks gefunden")

    # Rank-Artefakte schreiben
    if result.ranks or result.account:
        import json as _json
        rank_data = {
            "ranks": result.ranks,
            "account": result.account,
            "warnings": result.rank_warnings,
        }
        rank_path = args.output / "ranks.json"
        args.output.mkdir(parents=True, exist_ok=True)
        rank_path.write_text(
            _json.dumps(rank_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Ränge exportiert: {rank_path}")
    elif not args.no_ranks:
        print("⚠ Rank-Scan: keine Ränge gefunden")

    if result.errors:
        print(f"\nWarnungen: {'; '.join(result.errors)}")

    return 0


def _run_scan(args: argparse.Namespace) -> int:
    # Kombinierter Scan (Collection + Decks + Ranks in einem Durchlauf)
    if getattr(args, "all", False):
        return _run_scan_all(args)

    # Auto-Detect: Helper → direkt (sudo-frei), sonst Fallback mit Warnung
    if _is_helper_available():
        return _run_scan_via_helper(args)

    # Fallback: direkter Scan (benötigt sudo)
    print(
        "⚠ Sudo-Helper nicht verfügbar — direkter Memory-Scan erfordert sudo.\n"
        "  Starte mit: sudo python3 -m cli.main scan ...\n"
        "  Oder installiere den Helper: sudo ./helper/install.sh\n"
        "  Siehe: docs/helper-installation.md",
        file=sys.stderr,
    )
    result = scan_memory_collection_detailed(debug=args.debug)
    if result is None:
        return 1
    collection_path, run_report_path = write_collection_artifacts(
        result.collection, args.output, scan_result=result
    )
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

    if args.debug:
        log_dir = args.output
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "deck-scan-debug.log"
        file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s.%(msecs)03d %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        )
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(logging.Formatter("  %(message)s"))
        scanner_logger = logging.getLogger("scanner")
        scanner_logger.setLevel(logging.DEBUG)
        scanner_logger.addHandler(file_handler)
        scanner_logger.addHandler(stream_handler)
        scanner_logger.propagate = False
        scanner_logger.debug("=== deck-scan run started (method=%s) ===", method)
        print(f"🪵 Debug-Log: {log_path}")

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
        il2cpp_result = scan_decks_il2cpp(adapter, debug=args.debug)
        if il2cpp_result and il2cpp_result.decks:
            print(f"✅ IL2CPP: {len(il2cpp_result.decks)} Decks gefunden")
        elif il2cpp_result:
            print(f"⚠ IL2CPP: keine Decks — {', '.join(il2cpp_result.warnings) or 'unbekannt'}")
        else:
            print("⚠ IL2CPP: Scan fehlgeschlagen")

    # --- Pattern-based Scan ---
    # In "auto" mode, only skip the fallback when IL2CPP was clean.
    # If IL2CPP produced warnings, let pattern-scan try to recover the
    # missing or partially parsed decks.
    il2cpp_clean_success = bool(il2cpp_result and il2cpp_result.decks and not il2cpp_result.warnings)
    pattern_result: DeckScanResult | None = None
    if method == "pattern" or (method == "auto" and not il2cpp_clean_success):
        anchor_ids = args.anchors or []
        if not anchor_ids and method == "pattern":
            _error("--anchors erforderlich für Pattern-Scan (oder --method auto verwenden)")
            return 1
        if not anchor_ids and method == "auto":
            # Derive a bounded anchor set from IL2CPP results when the
            # fallback is actually needed. This preserves the old recovery
            # path without paying for a pattern scan on clean successes.
            if il2cpp_result and il2cpp_result.decks:
                anchor_ids = list({
                    grp_id
                    for deck in il2cpp_result.decks
                    for pile in deck.piles.values()
                    for grp_id in pile
                })[:20]
            if not anchor_ids:
                # Fallback: previously persisted anchors from a collection scan.
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
                "cards": _piles_to_card_dict(deck.piles, card_db),
                "cardsById": _piles_to_cards_by_id(deck.piles),
            })
            deck_piles_by_id[deck_id] = deck.piles

    if pattern_result and pattern_result.decks:
        warnings_all.extend(pattern_result.warnings)
        for pattern_index, deck in enumerate(pattern_result.decks):
            # deck.raw_address is a buffer-relative offset from the pattern
            # parser (often 0 for several distinct decks), not a unique
            # absolute address — include the loop index so distinct decks
            # don't collide onto the same fallback deckId and get dropped
            # as "duplicates" below.
            deck_id = deck.deck_id or f"pattern-{pattern_index}-{deck.raw_address:#x}"
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
        # Sanitize deck_id for filesystem safety
        safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(deck_id))
        deck_path = decks_dir / f"deck-{safe_id}.json"
        payload = {
            "schema": "deck.v1",
            "deckId": deck_id,
            "name": deck_entry["name"],
            "source": deck_entry["source"],
            "cards": deck_entry["cards"],
            "cardsById": deck_entry["cardsById"],
        }
        _write_json_artifact_safe(deck_path, payload, all_decks, print_fn=print)

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
    _write_json_artifact_safe(container_path, container_payload, all_decks, print_fn=print)

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
    _write_json_artifact_safe(decks_json_path, decks_json_payload, all_decks, print_fn=print)

    print(f"\n📊 {len(all_decks)} Decks gescannt")
    print(f"Container: {container_path}")
    print(f"decks.json: {decks_json_path}")
    print(f"Deck-Verzeichnis: {decks_dir}")
    return 0


def _iso_now_cli() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json_artifact_safe(
    path: Path,
    payload: dict[str, Any],
    all_decks: list[dict[str, Any]],
    *,
    print_fn: Callable[..., None] = print,
) -> None:
    """Write a JSON artifact with a small plausibility guard.

    If a scan looks suspicious and the target file already exists, keep a
    backup of the previous file before writing the new result.
    """
    min_cards_per_deck = 40
    plausible = bool(all_decks) and any(
        len(d.get("cards", {}).get("Main", [])) >= min_cards_per_deck
        for d in all_decks
    )

    if path.exists() and not plausible:
        bak_path = path.with_suffix(path.suffix + ".bak")
        import shutil
        shutil.copy2(str(path), str(bak_path))
        print_fn(
            f"⚠ Neues Ergebnis scheint unplausibel (keine ausreichende Deckgröße).\n"
            f"  Alte Datei gesichert nach: {bak_path}\n"
            f"  Neues Ergebnis wird trotzdem geschrieben — prüfe es manuell."
        )

    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


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


def _run_ranks(args: argparse.Namespace) -> int:
    """Scannt Spieler-Rang und Account-Info aus dem MTGA-Prozessspeicher.

    Nutzt IL2CPP-Navigation: WrapperController → PlayerRankServiceWrapper → _combinedRankInfo
    und WrapperController → AccountClient → AccountInformation.
    """
    from scanner.memory_scanner import _attach_process

    print("🔍 Rang-Scan (IL2CPP-Navigation)")

    # An MTGA-Prozess attachen
    candidate_names = get_macos_mtga_process_names()
    pm = _attach_process(candidate_names)
    if pm is None:
        print("❌ MTGA-Prozess nicht gefunden")
        return 1

    adapter = PymemMemoryAdapter(pm)
    read_account = not args.no_account

    print(f"🧭 Navigiere: WrapperController → PlayerRankServiceWrapper → _combinedRankInfo")
    if read_account:
        print(f"🧭 Navigiere: WrapperController → AccountClient → AccountInformation")

    result = scan_ranks_and_account(adapter, read_account=read_account)

    if result.warnings:
        for w in result.warnings:
            print(f"⚠ {w}")

    if args.json:
        import json as _json
        output = _json.dumps(result.to_dict(), indent=2, ensure_ascii=False)
        print(output)
        if args.output:
            args.output.write_text(output, encoding="utf-8")
            print(f"📄 JSON geschrieben: {args.output}")
    else:
        # Text-Ausgabe
        print()
        print("=" * 50)
        print("RÄNGE")
        print("=" * 50)
        print(format_rank_table(result.ranks))
        print(format_rank_summary(result.ranks))
        print()

        if read_account:
            print("=" * 50)
            print("ACCOUNT")
            print("=" * 50)
            print(format_account_table(result.account))
            print()
            print(format_account_summary(result.account))
            print()

        if args.output:
            import json as _json
            args.output.write_text(
                _json.dumps(result.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"📄 JSON geschrieben: {args.output}")

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
    if args.deck_command == "export":
        return _run_deck_export(args)
    return 1


def _run_deck_export(args: argparse.Namespace) -> int:
    """Exportiere ein Deck als Arena-kompatiblen Text."""
    search_dirs = args.search_dirs if args.search_dirs else None
    try:
        deck = load_deck_by_id(args.deck_id, search_dirs=search_dirs)
    except DeckNotFoundError as exc:
        _error(str(exc))
        return 1
    try:
        arena_text = export_deck_to_arena_text(deck)
    except DeckExportError as exc:
        _error(str(exc))
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(arena_text, encoding="utf-8")
        print(f"Arena-Export: {args.output}")
    else:
        sys.stdout.write(arena_text)
    return 0


def _run_advisor_meta(args: argparse.Namespace) -> int:
    """Zeigt aktuelle Meta-Daten von MTGGoldfish an."""
    from advisor.meta import fetch_meta, format_meta_table, save_meta_to_file

    fmt = args.format
    print(f"📡 Fetch Meta-Daten: {fmt} (MTGGoldfish)")

    try:
        meta = fetch_meta(
            format_name=fmt,
            use_cache=not args.no_cache,
            full=args.full,
        )
    except ValueError as exc:
        _error(str(exc))
        return 1
    except RuntimeError as exc:
        _error(str(exc))
        return 1

    if meta.warnings:
        for w in meta.warnings:
            print(f"  WARN: {w}")

    if args.json:
        import json as _json
        output = _json.dumps(meta.to_dict(), ensure_ascii=False, indent=2)
        print(output)
        if args.output:
            save_meta_to_file(meta, args.output)
            print(f"\n📄 JSON geschrieben: {args.output}")
    else:
        print(format_meta_table(meta, max_decks=args.max_decks))
        if args.output:
            save_meta_to_file(meta, args.output)
            print(f"\n📄 JSON geschrieben: {args.output}")

    return 0


def _run_advisor_analyze(args: argparse.Namespace) -> int:
    """Analysiert ein Deck gegen Collection + Wildcards.

    Zwei Quellen werden unterstützt:
      1. Arena-Textdeckliste via ``--deck-text <datei>`` oder ``--deck-text-stdin``
         → ``import_arena_deck`` → ``arena_deck_to_deck_cards`` → ``analyze_deck``.
      2. Gespeichertes Deck via ``--deck <deckId>`` / ``--name`` aus deck-cards.json.
    """
    has_text = bool(args.deck_text) or bool(getattr(args, "deck_text_stdin", False))
    if not has_text and not args.deck and not args.name:
        _error(
            "Entweder --deck-text <datei> / --deck-text-stdin ODER "
            "--deck <deckId> / --name <deckName> erforderlich."
        )
        return 1
    if not args.collection.exists():
        _error(f"collection.json nicht gefunden: {args.collection}")
        return 1

    # Karten-DB optional laden (für Namens-/Seltenheits-Auflösung, Arena-Import braucht sie).
    card_db = None
    try:
        card_db = load_card_database()
    except Exception:
        pass  # Karten-DB ist optional (im Arena-Import-Flow werden unbekannte Karten übersprungen)

    if has_text:
        # --- Arena-Textdeckliste → Import → Konvertierung → Analyse ---
        if args.deck_text and not args.deck_text.exists():
            _error(f"Arena-Textdeckliste nicht gefunden: {args.deck_text}")
            return 1
        if args.deck_text:
            deck_text = args.deck_text.read_text(encoding="utf-8")
        else:
            deck_text = sys.stdin.read()
        try:
            arena_deck = import_arena_deck(
                deck_text,
                card_db=card_db or {},
                deck_format=args.format,
                name=args.name,
            )
        except Exception as exc:
            _error(f"Fehler beim Import der Arena-Textdeckliste: {exc}")
            return 1
        deck = arena_deck_to_deck_cards(arena_deck)
    else:
        # --- Deck aus deck-cards.json laden ---
        if not args.deck_cards_path.exists():
            _error(f"deck-cards.json nicht gefunden: {args.deck_cards_path}")
            return 1
        try:
            deck = load_deck_cards(args.deck_cards_path, deck_id=args.deck, name=args.name)
        except ValueError as exc:
            _error(str(exc))
            return 1

    collection_data = json.loads(args.collection.read_text(encoding="utf-8"))
    wildcards_data: dict[str, Any] = {}
    if args.wildcards.exists():
        wildcards_data = json.loads(args.wildcards.read_text(encoding="utf-8"))
    else:
        print(f"⚠ Wildcards nicht gefunden: {args.wildcards} — Analyse ohne Wildcard-Bestand.")

    result = analyze_deck(
        deck=deck,
        collection=collection_data,
        wildcards=wildcards_data,
        card_db=card_db,
    )

    # Ergebnis in Datei schreiben
    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "advisor-analyze.json"
    result_path.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n📄 Result geschrieben: {result_path}")
        return 0

    # Text-Ausgabe
    deck_info = result["deck"]
    summary = result["summary"]
    print(f"{'=' * 50}")
    print(f"DECK-ANALYSE: {deck_info['name']} ({deck_info['format']})")
    print(f"  DeckId: {deck_info['deckId']}")
    print(f"{'=' * 50}")
    print(f"\n📊 Completion: {summary['completionScore']}%")
    print(f"  Total: {summary['totalCards']} Karten")
    print(f"  Owned: {summary['ownedCards']} Karten")
    print(f"  Missing: {summary['missingCards']} Karten ({summary['missingUnique']} unique)")

    # Wildcard-Bedarf
    wc = result.get("wildcardNeed", {})
    wc_needed = wc.get("needed", {})
    wc_sufficiency = wc.get("sufficiency", {})
    if wc_needed:
        print(f"\n🎴 Wildcard-Bedarf:")
        for rarity, count in wc_needed.items():
            suff = wc_sufficiency.get(rarity, {})
            status = "✅" if suff.get("sufficient") else "❌"
            print(f"  {status} {rarity}: benötigt {count}, vorhanden {suff.get('owned', 0)}, Fehlt {suff.get('shortfall', 0)}")
    else:
        print(f"\n🎴 Keine Wildcards benötigt — alle Karten vorhanden!")

    # Craft-Priorität
    craft = result.get("craftPriority", [])
    if craft:
        print(f"\n🔨 Craft-Priorität:")
        for group in craft:
            rarity = group["rarity"]
            total = group["totalMissing"]
            cards = group["cards"]
            print(f"  {rarity.upper()} ({total} Karten, {len(cards)} unique):")
            for card in cards[:10]:
                print(f"    - {card['name']} (×{card['missing']}, {card['zone']})")
            if len(cards) > 10:
                print(f"    ... und {len(cards) - 10} weitere")

    # Owned/missing Detail
    missing = result.get("missingCards", [])
    owned = result.get("ownedCards", [])
    print(f"\n📋 Fehlende Karten ({len(missing)}):")
    if missing:
        for card in missing[:15]:
            print(f"  - {card['name']} ({card['rarity']}): {card['missing']} fehlt, {card['owned']} vorhanden")
        if len(missing) > 15:
            print(f"  ... und {len(missing) - 15} weitere")
    else:
        print("  Keine — alle Karten vorhanden!")

    if result.get("warnings"):
        print(f"\n⚠ Warnings: {', '.join(result['warnings'])}")

    print(f"\n📄 Result geschrieben: {result_path}")
    return 0


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
    if args.advisor_command == "analyze":
        return _run_advisor_analyze(args)
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
            meta_format="" if args.no_meta else args.meta_format,
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

    if args.advisor_command == "meta":
        return _run_advisor_meta(args)

    return 1


def _run_watch(args: argparse.Namespace) -> int:
    """Watch-Mode: regelmäßiger Scan, wenn MTGA läuft."""
    from scanner.watch import WatchConfig, watch_loop

    config = WatchConfig(
        interval=args.interval,
        output_dir=args.output,
        debug=args.debug,
        single_shot=args.single_shot,
        state_file=args.state_file,
    )
    state = watch_loop(config)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "collection":
        return _run_collection(args)
    if args.command == "decks":
        return _run_decks(args)
    if args.command == "wildcards":
        return _run_wildcards(args)
    if args.command == "deck-cards":
        return _run_deck_cards(args)
    if args.command == "scan":
        return _run_scan(args)
    if args.command == "deck-scan":
        return _run_deck_scan(args)
    if args.command == "ranks":
        return _run_ranks(args)
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
    if args.command == "history":
        return _run_history(args)
    if args.command == "watch":
        return _run_watch(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
