from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
from typing import Iterable

from carddb.lookup import CardLookup

from .deck_analysis import DeckAnalysisResult, build_deck_analysis


@dataclass(frozen=True)
class DeckAnalysisPaths:
    summary: Path
    deck_dir: Path
    deck_files: list[Path]


def export_deck_analysis(
    decks: Iterable[dict],
    *,
    conn: sqlite3.Connection,
    output_dir: Path,
) -> DeckAnalysisPaths:
    output_dir.mkdir(parents=True, exist_ok=True)
    deck_dir = output_dir / "deck-analysis"
    deck_dir.mkdir(parents=True, exist_ok=True)

    lookup = CardLookup(conn)
    deck_files: list[Path] = []
    summary_entries: list[dict] = []

    for deck in decks:
        result = build_deck_analysis(deck, lookup)
        deck_id = deck.get("id", "unknown")
        file_path = deck_dir / f"{deck_id}.json"
        _write_json(file_path, result.payload)
        deck_files.append(file_path)
        summary_entries.append(
            {
                "deckId": deck_id,
                "name": deck.get("name"),
                "format": deck.get("format"),
                "source": deck.get("source"),
                "mappingCoverage": result.payload.get("mappingCoverage"),
            }
        )

    summary_path = output_dir / "deck-analysis-summary.json"
    _write_json(
        summary_path,
        {
            "schema": "deck-analysis-summary.v1",
            "decks": summary_entries,
        },
    )

    return DeckAnalysisPaths(
        summary=summary_path,
        deck_dir=deck_dir,
        deck_files=deck_files,
    )


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
