from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import json
import re
from typing import Iterable, Iterator


LOG_LINE_RE = re.compile(r"^\[(?P<timestamp>[^\]]+)\]\s+\[[^\]]+\]\s+(?P<content>.+)")


@dataclass(frozen=True)
class LogLine:
    path: Path
    line_number: int
    text: str


@dataclass(frozen=True)
class LogChunk:
    event: str
    timestamp: str | None
    path: Path
    payload_lines: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedEvent:
    event: str
    timestamp: str | None
    data: dict | list | None
    path: Path
    raw_payload: str


@dataclass
class CollectionReport:
    cards: dict[int, int] | None
    wildcards: dict[str, int] | None
    as_of: str | None
    snapshot_seen: bool
    completeness: dict[str, str]
    evidence: list[str]
    warnings: list[str]


def ingest(paths: Iterable[Path]) -> list[LogLine]:
    lines: list[LogLine] = []
    for path in paths:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line_number, line in enumerate(handle, start=1):
                lines.append(LogLine(path=path, line_number=line_number, text=line.rstrip("\n")))
    return lines


def chunk(lines: Iterable[LogLine]) -> Iterator[LogChunk]:
    current_chunk: LogChunk | None = None
    for line in lines:
        match = LOG_LINE_RE.match(line.text)
        if match:
            content = match.group("content")
            if content.lstrip().startswith(("{", "[")):
                if current_chunk is not None:
                    current_chunk.payload_lines.append(content)
                continue
            if current_chunk:
                yield current_chunk
            current_chunk = LogChunk(
                event=content.strip(),
                timestamp=match.group("timestamp"),
                path=line.path,
                payload_lines=[],
            )
            continue
        if current_chunk is not None:
            current_chunk.payload_lines.append(line.text)
    if current_chunk:
        yield current_chunk


def _normalize_json_payload(raw_payload: str) -> str:
    return re.sub(r":\s*\+(\d)", r": \1", raw_payload)


def extract_json(chunks: Iterable[LogChunk]) -> Iterator[ParsedEvent]:
    for chunk in chunks:
        raw_payload = "\n".join(chunk.payload_lines).strip()
        normalized_payload = _normalize_json_payload(raw_payload)
        data: dict | list | None = None
        if normalized_payload:
            try:
                data = json.loads(normalized_payload)
            except json.JSONDecodeError:
                data = None
        yield ParsedEvent(
            event=chunk.event,
            timestamp=chunk.timestamp,
            data=data,
            path=chunk.path,
            raw_payload=raw_payload,
        )


def dispatch(events: Iterable[ParsedEvent]) -> CollectionReport:
    cards: dict[int, int] | None = None
    wildcards: dict[str, int] | None = None
    snapshot_seen = False
    as_of: str | None = None
    evidence: list[str] = []
    warnings: list[str] = []

    for event in events:
        if event.data is None:
            continue
        if event.event == "PlayerInventory.GetPlayerCardsV3":
            snapshot_seen = True
            evidence.append(event.event)
            snapshot_cards = {
                int(entry["id"]): int(entry["quantity"])
                for entry in event.data.get("cards", [])
                if "id" in entry and "quantity" in entry
            }
            cards = snapshot_cards
            snapshot_wildcards = event.data.get("wildcards")
            if isinstance(snapshot_wildcards, dict):
                wildcards = {
                    key: int(value)
                    for key, value in snapshot_wildcards.items()
                    if isinstance(value, int)
                }
            else:
                wildcards = None
            as_of = event.data.get("asOf") or event.timestamp
            continue

        if event.event == "Inventory.Updated":
            if not snapshot_seen:
                warnings.append("delta-before-snapshot")
                continue
            evidence.append(event.event)
            delta = event.data.get("delta", {})
            if cards is None:
                cards = {}
            for entry in delta.get("cards", []) or []:
                if "id" not in entry or "quantity" not in entry:
                    continue
                card_id = int(entry["id"])
                quantity = int(entry["quantity"])
                cards[card_id] = cards.get(card_id, 0) + quantity
            delta_wildcards = delta.get("wildcards")
            if isinstance(delta_wildcards, dict):
                if wildcards is None:
                    wildcards = {}
                for key, value in delta_wildcards.items():
                    if not isinstance(value, int):
                        continue
                    wildcards[key] = wildcards.get(key, 0) + value
            as_of = event.timestamp or as_of

    if not snapshot_seen:
        completeness = {"cards": "unknown", "wildcards": "unknown", "source": "unknown"}
        return CollectionReport(
            cards=None,
            wildcards=None,
            as_of=as_of,
            snapshot_seen=False,
            completeness=completeness,
            evidence=evidence,
            warnings=warnings,
        )

    cards_completeness = "complete" if cards is not None else "unknown"
    wildcards_completeness = "complete" if wildcards is not None else "unknown"
    completeness = {
        "cards": cards_completeness,
        "wildcards": wildcards_completeness,
        "source": "complete" if cards_completeness == "complete" else "unknown",
    }
    return CollectionReport(
        cards=cards,
        wildcards=wildcards,
        as_of=as_of,
        snapshot_seen=True,
        completeness=completeness,
        evidence=evidence,
        warnings=warnings,
    )


def parse_collection(paths: Iterable[Path]) -> CollectionReport:
    return dispatch(extract_json(chunk(ingest(paths))))
