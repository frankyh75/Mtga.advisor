from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence

from parser.export import export_collection
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "collection":
        return _run_collection(args)
    if args.command == "serve":
        return _run_serve(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
