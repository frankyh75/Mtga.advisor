from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
import re
from typing import Iterable

from .pipeline import ParsedEvent, chunk, dispatch, extract_json, ingest


@dataclass(frozen=True)
class ExportPaths:
    collection: Path
    run_report: Path
    raw_samples_dir: Path


def export_collection(paths: Iterable[Path], output_dir: Path) -> ExportPaths:
    sorted_paths = sorted({Path(path) for path in paths})
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_samples_dir = output_dir / "raw-samples"
    raw_samples_dir.mkdir(parents=True, exist_ok=True)

    started_at = _iso_now()
    events = list(extract_json(chunk(ingest(sorted_paths))))
    report = dispatch(events)

    _write_raw_samples(raw_samples_dir, events)
    collection_path = output_dir / "collection.json"
    run_report_path = output_dir / "run-report.json"

    collection_payload = _build_collection_payload(report)
    _write_json(collection_path, collection_payload)

    finished_at = _iso_now()
    run_report_payload = _build_run_report_payload(
        report,
        sorted_paths,
        collection_path,
        raw_samples_dir,
        started_at=started_at,
        finished_at=finished_at,
    )
    _write_json(run_report_path, run_report_payload)

    return ExportPaths(
        collection=collection_path,
        run_report=run_report_path,
        raw_samples_dir=raw_samples_dir,
    )


def _build_collection_payload(report) -> dict:
    cards = report.cards or {}
    wildcards = report.wildcards or {}

    return {
        "schema": "collection.v1",
        "source": "local-logs",
        "cards": {str(card_id): count for card_id, count in sorted(cards.items())},
        "wildcards": {key: value for key, value in sorted(wildcards.items())},
        "diagnostics": {
            "completeness": report.completeness,
            "warnings": list(report.warnings),
            "evidence": list(report.evidence),
        },
    }


def _build_run_report_payload(
    report,
    paths: list[Path],
    collection_path: Path,
    raw_samples_dir: Path,
    *,
    started_at: str,
    finished_at: str,
) -> dict:
    cards_count = len(report.cards or {})
    return {
        "schema": "run-report.v1",
        "runId": f"run-{started_at}",
        "startedAt": started_at,
        "finishedAt": finished_at,
        "source": "local-logs",
        "logs": [path.as_posix() for path in paths],
        "outputs": {
            "collection": collection_path.as_posix(),
            "rawSamples": raw_samples_dir.as_posix(),
        },
        "summary": {
            "cardsCount": cards_count,
            "wildcardsIncluded": report.wildcards is not None,
        },
        "diagnostics": {
            "completeness": report.completeness,
            "warnings": list(report.warnings),
            "evidence": list(report.evidence),
        },
    }


def _write_raw_samples(raw_samples_dir: Path, events: list[ParsedEvent]) -> None:
    for index, event in enumerate(events, start=1):
        if event.data is None:
            continue
        sample_name = f"{index:04d}-{_slug(event.event)}.json"
        payload = {
            "event": event.event,
            "path": event.path.as_posix(),
            "data": event.data,
        }
        _write_json(raw_samples_dir / sample_name, payload)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(_format_json(payload), encoding="utf-8")


def _format_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "event"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
