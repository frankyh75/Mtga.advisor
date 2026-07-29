"""Proof-of-concept MTGA memory probe via LLDB Developer Tools access.

This avoids sudo by letting LLDB perform the process attach. LLDB briefly stops
the target process while the probe runs.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

from .card_database import load_card_database
from .macos_paths import get_macos_mtga_process_name

RESULT_PREFIX = "MTGA_LLDB_PROBE_RESULT="


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sucht MTGA-Karten-IDs im Prozessspeicher via LLDB.")
    parser.add_argument("--pid", type=int, help="MTGA PID. Wenn nicht gesetzt, wird per pgrep gesucht.")
    parser.add_argument("--process", default=get_macos_mtga_process_name(), help="Prozessname fuer pgrep.")
    parser.add_argument("--card-id", action="append", type=int, default=[], help="Arena-ID/GrpId, mehrfach angebbar.")
    parser.add_argument("--card-name", action="append", default=[], help="Kartenname, mehrfach angebbar.")
    parser.add_argument("--max-regions", type=int, help="Maximale Anzahl lesbarer Regionen fuer schnelle Tests.")
    parser.add_argument("--max-region-mb", type=int, default=1024, help="Groessere Regionen werden uebersprungen.")
    parser.add_argument("--chunk-mb", type=int, default=4, help="ReadMemory Chunk-Groesse.")
    parser.add_argument("--max-matches", type=int, default=256, help="Maximale Treffer pro Karten-ID.")
    parser.add_argument(
        "--mode",
        choices=["native-find", "read-chunks"],
        default="native-find",
        help="LLDB-Suchstrategie.",
    )
    parser.add_argument("--show-lldb-output", action="store_true", help="Zeigt die komplette LLDB-Ausgabe.")
    return parser


def _resolve_pid(process_name: str) -> int:
    completed = subprocess.run(
        ["pgrep", "-x", process_name],
        check=False,
        text=True,
        capture_output=True,
    )
    pids = [int(line) for line in completed.stdout.splitlines() if line.strip().isdigit()]
    if not pids:
        raise RuntimeError(f"Prozess nicht gefunden: {process_name}")
    return pids[0]


def _resolve_card_ids(card_ids: Sequence[int], card_names: Sequence[str]) -> list[int]:
    resolved = list(card_ids)
    if not card_names:
        return resolved

    db = load_card_database()
    name_to_id = {meta["name"].lower(): card_id for card_id, meta in db.items() if "name" in meta}
    for name in card_names:
        card_id = name_to_id.get(name.lower())
        if card_id is None:
            raise RuntimeError(f"Karte nicht gefunden: {name}")
        resolved.append(card_id)
    return resolved


def _run_lldb(pid: int, card_ids: Sequence[int], args: argparse.Namespace) -> dict:
    payload_path = Path(__file__).with_name("lldb_probe_payload.py").resolve()
    config = {
        "card_ids": list(card_ids),
        "max_regions": args.max_regions,
        "max_region_bytes": args.max_region_mb * 1024 * 1024,
        "chunk_size": args.chunk_mb * 1024 * 1024,
        "max_matches_per_id": args.max_matches,
        "mode": args.mode,
    }

    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(config, handle)
        config_path = handle.name

    env = os.environ.copy()
    env["MTGA_LLDB_PROBE_CONFIG"] = config_path
    command = [
        "lldb",
        "-b",
        "-o",
        f"process attach --pid {pid}",
        "-o",
        f"command script import {payload_path}",
        "-o",
        "script lldb_probe_payload.run()",
        "-o",
        "process detach",
        "-o",
        "quit",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            env=env,
        )
    finally:
        Path(config_path).unlink(missing_ok=True)

    output = completed.stdout + completed.stderr
    if args.show_lldb_output:
        print(output)
    if completed.returncode != 0:
        raise RuntimeError(f"LLDB fehlgeschlagen ({completed.returncode})\n{output}")

    for line in output.splitlines():
        if line.startswith(RESULT_PREFIX):
            return json.loads(line[len(RESULT_PREFIX) :])
    raise RuntimeError(f"Keine Ergebniszeile von LLDB gefunden.\n{output}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        pid = args.pid if args.pid is not None else _resolve_pid(args.process)
        card_ids = _resolve_card_ids(args.card_id, args.card_name)
        if not card_ids:
            parser.error("Mindestens --card-id oder --card-name angeben.")
        result = _run_lldb(pid, card_ids, args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"PID: {pid}")
    print(f"Regionen: {result['regions_scanned']}/{result['regions_total']}")
    if args.mode == "read-chunks":
        print(f"Gelesen: {result['bytes_scanned'] / 1024 / 1024:.1f} MB")
    else:
        print("Gelesen: native LLDB-Suche")
    print(f"Lesefehler: {result['read_failures']}")
    for card_id, match in result["matches"].items():
        print(f"{card_id}: {match['count']} Treffer")
        for address in match["sample"]:
            print(f"  {address}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
