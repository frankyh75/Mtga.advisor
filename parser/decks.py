"""Deck-Export aus MTGA-Logs.

Extrahiert DeckSummaries aus StartHook-Events in Player.log-Dateien
und schreibt sie als decks.json parallel zu collection.json.

Referenz: mtgatool-desktop InStartHook.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json
import re
from typing import Iterable

from .pipeline import ParsedEvent, chunk, extract_json, ingest
from .start_hook import DeckSummary, parse_start_hook


@dataclass(frozen=True)
class DeckExportPaths:
    """Pfade der Deck-Export-Artefakte."""

    decks: Path
    run_report: Path


def export_decks(paths: Iterable[Path], output_dir: Path) -> DeckExportPaths:
    """Extrahiere Decks aus MTGA-Logs und schreibe Artefakte.

    Args:
        paths: Zu parsende Log-Dateien.
        output_dir: Ausgabeverzeichnis.

    Returns:
        DeckExportPaths mit Pfaden zu decks.json und run-report.
    """
    sorted_paths = sorted({Path(path) for path in paths})
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = _iso_now()
    events = list(extract_json(chunk(ingest(sorted_paths))))
    decks = _extract_decks(events)

    decks_path = output_dir / "decks.json"
    run_report_path = output_dir / "run-report-decks.json"

    _write_json(decks_path, _build_decks_payload(decks, sorted_paths))

    finished_at = _iso_now()
    _write_json(
        run_report_path,
        _build_run_report(decks, sorted_paths, decks_path, started_at, finished_at),
    )

    return DeckExportPaths(decks=decks_path, run_report=run_report_path)


def _extract_decks(events: list[ParsedEvent]) -> list[DeckSummary]:
    """Extrahiere DeckSummaries aus allen StartHook-Events.

    Sammelt Decks aus allen StartHook-Events in den Logs.
    Neuere Events überschreiben ältere (letzter Login gewinnt).
    """
    seen: dict[str, DeckSummary] = {}  # deck_id -> deck (letzter gewinnt)
    order: list[str] = []  # Reihenfolge der IDs

    for event in events:
        if event.event != "StartHook":
            continue
        if not isinstance(event.data, dict):
            continue

        parsed = parse_start_hook(event.data)
        if parsed is None:
            continue

        for deck in parsed.deck_summaries:
            key = deck.deck_id or deck.name
            if key not in seen:
                order.append(key)
            seen[key] = deck

    return [seen[key] for key in order]


def _build_decks_payload(
    decks: list[DeckSummary],
    log_paths: list[Path],
) -> dict:
    """Baue das decks.json Payload."""
    return {
        "schema": "decks.v1",
        "source": "local-logs",
        "decks": [
            {
                "name": d.name,
                "deckId": d.deck_id,
                "deckTileId": d.deck_tile_id,
                "description": d.description,
                "attributes": d.attributes,
                "formatLegalities": d.format_legalities,
                "isCompanionValid": d.is_companion_valid,
                "mana": d.mana,
            }
            for d in decks
        ],
        "diagnostics": {
            "deckCount": len(decks),
            "logs": [p.as_posix() for p in log_paths],
        },
    }


def _build_run_report(
    decks: list[DeckSummary],
    log_paths: list[Path],
    decks_path: Path,
    started_at: str,
    finished_at: str,
) -> dict:
    """Baue den run-report für Deck-Export."""
    return {
        "schema": "run-report-decks.v1",
        "runId": f"run-decks-{started_at}",
        "startedAt": started_at,
        "finishedAt": finished_at,
        "source": "local-logs",
        "logs": [p.as_posix() for p in log_paths],
        "outputs": {
            "decks": decks_path.as_posix(),
        },
        "summary": {
            "deckCount": len(decks),
        },
        "diagnostics": {
            "warnings": [],
            "evidence": ["start-hook-parser"],
        },
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
