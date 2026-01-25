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
    wildcards_baseline_present: bool
    pending_wildcard_deltas: dict[str, int] | None
    as_of: str | None
    snapshot_seen: bool
    cards_seen_in_decks: set[int]
    decks: list[dict]
    collection_completeness: str
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
    wildcards_baseline_present = False
    pending_wildcard_deltas: dict[str, int] | None = None
    snapshot_seen = False
    cards_snapshot_seen = False
    as_of: str | None = None
    deck_lists: dict[str, dict] = {}
    deck_meta: dict[str, dict] = {}
    deck_sources: dict[str, str] = {}
    evidence: list[str] = []
    deck_evidence: set[str] = set()
    warnings: list[str] = []
    inventory_wildcard_keys = {
        "WildCardCommons": "common",
        "WildCardUnCommons": "uncommon",
        "WildCardRares": "rare",
        "WildCardMythics": "mythic",
    }

    def _accumulate_wildcard_deltas(
        target: dict[str, int] | None,
        deltas: dict,
    ) -> dict[str, int]:
        if target is None:
            target = {}
        for key, value in deltas.items():
            if not isinstance(value, int):
                continue
            target[key] = target.get(key, 0) + value
        return target

    def _normalize_deck_cards(cards: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        for entry in cards:
            if not isinstance(entry, dict):
                continue
            card_id = entry.get("cardId")
            quantity = entry.get("quantity")
            if not isinstance(card_id, int) or not isinstance(quantity, int):
                continue
            normalized.append({"cardId": card_id, "quantity": quantity})
        return normalized

    def _parse_deck_list(deck_id: str, deck_data: dict) -> dict | None:
        if not isinstance(deck_data, dict):
            return None
        main = _normalize_deck_cards(deck_data.get("MainDeck", []) or [])
        sideboard = _normalize_deck_cards(deck_data.get("Sideboard", []) or [])
        command_zone = _normalize_deck_cards(deck_data.get("CommandZone", []) or [])
        companions = _normalize_deck_cards(deck_data.get("Companions", []) or [])
        reduced_sideboard = _normalize_deck_cards(deck_data.get("ReducedSideboard", []) or [])
        if not any([main, sideboard, command_zone, companions, reduced_sideboard]):
            return None
        payload = {
            "id": deck_id,
            "mainDeck": main,
            "sideboard": sideboard,
            "commandZone": command_zone,
            "companions": companions,
        }
        if reduced_sideboard:
            payload["reducedSideboard"] = reduced_sideboard
        return payload

    def _source_priority(source: str) -> int:
        priority = {"saved": 0, "last_played": 1, "event": 2}
        return priority.get(source, 99)

    def _set_deck_source(deck_id: str, source: str) -> None:
        existing = deck_sources.get(deck_id)
        if existing is None or _source_priority(source) < _source_priority(existing):
            deck_sources[deck_id] = source

    def _should_replace_deck(deck_id: str, source: str) -> bool:
        existing = deck_sources.get(deck_id)
        if existing is None:
            return True
        return _source_priority(source) < _source_priority(existing)

    def _extract_attribute(attributes: list[dict], name: str) -> str | None:
        for entry in attributes:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") != name:
                continue
            value = entry.get("value")
            if isinstance(value, str):
                if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
                    return value[1:-1]
                return value
        return None

    def _is_event_deck(summary: dict, internal_event_name: str | None) -> bool:
        name = summary.get("Name")
        description = summary.get("Description")
        for candidate in (name, description, internal_event_name):
            if not isinstance(candidate, str):
                continue
            if "Decks/Precon" in candidate or "Precon" in candidate:
                return True
        return False

    def _update_deck_meta(deck_id: str, meta: dict) -> None:
        if not meta:
            return
        deck_meta.setdefault(deck_id, {}).update(meta)

    def _extract_decks_from_data(data: object, event_name: str | None) -> None:
        if isinstance(data, dict):
            internal_event_name = data.get("InternalEventName")
            if not isinstance(internal_event_name, str):
                internal_event_name = None
            decks_value = data.get("Decks")
            if isinstance(decks_value, dict):
                for deck_id, deck_data in decks_value.items():
                    if not isinstance(deck_id, str):
                        continue
                    parsed = _parse_deck_list(deck_id, deck_data)
                    if parsed is not None:
                        source = "saved"
                        if _should_replace_deck(deck_id, source):
                            deck_lists[deck_id] = parsed
                        _set_deck_source(deck_id, source)
            course_deck = data.get("CourseDeck")
            course_summary = data.get("CourseDeckSummary")
            if isinstance(course_deck, dict) and isinstance(course_summary, dict):
                deck_id = course_summary.get("DeckId")
                if isinstance(deck_id, str):
                    parsed = _parse_deck_list(deck_id, course_deck)
                    if parsed is not None:
                        source = "event" if _is_event_deck(course_summary, internal_event_name) else "last_played"
                        if _should_replace_deck(deck_id, source):
                            deck_lists[deck_id] = parsed
                        _set_deck_source(deck_id, source)
                attributes = course_summary.get("Attributes")
                if isinstance(attributes, list):
                    meta: dict[str, str] = {}
                    name = course_summary.get("Name")
                    if isinstance(name, str):
                        meta["name"] = name
                    fmt = _extract_attribute(attributes, "Format")
                    if fmt:
                        meta["format"] = fmt
                    last_played = _extract_attribute(attributes, "LastPlayed")
                    if last_played:
                        meta["lastPlayed"] = last_played
                    if isinstance(deck_id, str):
                        _update_deck_meta(deck_id, meta)
            deck_id_value = data.get("DeckId")
            if isinstance(deck_id_value, str):
                meta: dict[str, str] = {}
                name = data.get("Name")
                fmt = data.get("Format")
                if isinstance(name, str):
                    meta["name"] = name
                if isinstance(fmt, str):
                    meta["format"] = fmt
                if meta:
                    _update_deck_meta(deck_id_value, meta)
            for value in data.values():
                _extract_decks_from_data(value, event_name)
        elif isinstance(data, list):
            for item in data:
                _extract_decks_from_data(item, event_name)

    for event in events:
        if event.data is None:
            continue
        deck_count_before = len(deck_lists)
        _extract_decks_from_data(event.data, event.event)
        if len(deck_lists) > deck_count_before:
            deck_evidence.add(event.event)
        if isinstance(event.data, dict) and "InventoryInfo" in event.data:
            inventory_info = event.data.get("InventoryInfo")
            if isinstance(inventory_info, dict):
                snapshot_seen = True
                evidence.append("InventoryInfo")
                snapshot_wildcards = {}
                for source_key, target_key in inventory_wildcard_keys.items():
                    value = inventory_info.get(source_key)
                    if isinstance(value, int):
                        snapshot_wildcards[target_key] = value
                if snapshot_wildcards:
                    wildcards = snapshot_wildcards
                    wildcards_baseline_present = True
                    if pending_wildcard_deltas:
                        wildcards = _accumulate_wildcard_deltas(wildcards, pending_wildcard_deltas)
                        pending_wildcard_deltas = None
                as_of = event.timestamp or as_of
            continue
        if event.event == "PlayerInventory.GetPlayerCardsV3":
            snapshot_seen = True
            cards_snapshot_seen = True
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
                wildcards_baseline_present = True
                if pending_wildcard_deltas:
                    wildcards = _accumulate_wildcard_deltas(wildcards, pending_wildcard_deltas)
                    pending_wildcard_deltas = None
            else:
                wildcards = None
                wildcards_baseline_present = False
            as_of = event.data.get("asOf") or event.timestamp
            continue

        if event.event == "Inventory.Updated":
            if not snapshot_seen:
                warnings.append("delta-before-snapshot")
                delta = event.data.get("delta", {})
                delta_wildcards = delta.get("wildcards")
                if isinstance(delta_wildcards, dict):
                    pending_wildcard_deltas = _accumulate_wildcard_deltas(
                        pending_wildcard_deltas,
                        delta_wildcards,
                    )
                continue
            evidence.append(event.event)
            delta = event.data.get("delta", {})
            if cards_snapshot_seen:
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
                if wildcards_baseline_present and wildcards is not None:
                    wildcards = _accumulate_wildcard_deltas(wildcards, delta_wildcards)
                else:
                    pending_wildcard_deltas = _accumulate_wildcard_deltas(
                        pending_wildcard_deltas,
                        delta_wildcards,
                    )
            as_of = event.timestamp or as_of

    decks = _finalize_decks(deck_lists, deck_meta, deck_sources)
    fallback_decks = _select_fallback_decks(decks)
    use_deck_fallback = not cards_snapshot_seen and bool(fallback_decks)
    cards_seen_in_decks = _collect_deck_card_ids(fallback_decks) if use_deck_fallback else set()

    if use_deck_fallback:
        if "decklist-fallback" not in warnings:
            warnings.append("decklist-fallback")
        for event_name in sorted(deck_evidence):
            if event_name not in evidence:
                evidence.append(event_name)

    if not snapshot_seen and "missing-snapshot" not in warnings:
        warnings.append("missing-snapshot")

    if cards_snapshot_seen and cards is not None:
        cards_completeness = "complete"
    elif use_deck_fallback:
        cards_completeness = "partial"
    else:
        cards_completeness = "unknown"
    wildcards_completeness = "complete" if wildcards_baseline_present else "unknown"
    if cards_completeness == "complete" and wildcards_completeness == "complete":
        source_completeness = "complete"
    elif cards_completeness == "partial":
        source_completeness = "partial"
    else:
        source_completeness = "unknown"
    completeness = {
        "cards": cards_completeness,
        "wildcards": wildcards_completeness,
        "source": source_completeness,
    }
    collection_completeness = source_completeness
    return CollectionReport(
        cards=cards,
        wildcards=wildcards,
        wildcards_baseline_present=wildcards_baseline_present,
        pending_wildcard_deltas=pending_wildcard_deltas,
        as_of=as_of,
        snapshot_seen=snapshot_seen,
        cards_seen_in_decks=cards_seen_in_decks,
        decks=decks,
        collection_completeness=collection_completeness,
        completeness=completeness,
        evidence=evidence,
        warnings=warnings,
    )

def _finalize_decks(
    deck_lists: dict[str, dict],
    deck_meta: dict[str, dict],
    deck_sources: dict[str, str],
) -> list[dict]:
    decks: list[dict] = []
    for deck_id, deck in deck_lists.items():
        payload = dict(deck)
        meta = deck_meta.get(deck_id)
        if meta:
            payload.update(meta)
        source = deck_sources.get(deck_id)
        if source:
            payload["source"] = source
        payload = _apply_zone_rules(payload)
        decks.append(payload)
    decks.sort(key=_deck_sort_key)
    return decks


def _deck_sort_key(entry: dict) -> tuple:
    source = entry.get("source")
    priority = {"saved": 0, "last_played": 1, "event": 2}
    return (priority.get(source, 99), entry.get("name", ""), entry.get("id", ""))


def _select_fallback_decks(decks: list[dict]) -> list[dict]:
    if not decks:
        return []
    sources = {deck.get("source") for deck in decks}
    for preferred in ("saved", "last_played", "event"):
        if preferred in sources:
            return [deck for deck in decks if deck.get("source") == preferred]
    return decks


def _apply_zone_rules(deck: dict) -> dict:
    fmt = deck.get("format")
    if isinstance(fmt, str) and ("Brawl" not in fmt and "Commander" not in fmt):
        deck = dict(deck)
        deck["commandZone"] = []
        deck["companions"] = []
    return deck


def _collect_deck_card_ids(decks: list[dict]) -> set[int]:
    card_ids: set[int] = set()
    for deck in decks:
        for key in ("mainDeck", "sideboard", "commandZone", "companions", "reducedSideboard"):
            entries = deck.get(key, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                card_id = entry.get("cardId")
                if isinstance(card_id, int):
                    card_ids.add(card_id)
    return card_ids


def _has_detailed_logs(lines: Iterable[LogLine]) -> bool:
    return any("DETAILED LOGS: ENABLED" in line.text for line in lines)


def parse_collection(paths: Iterable[Path]) -> CollectionReport:
    lines = ingest(paths)
    detailed_logs = _has_detailed_logs(lines)
    report = dispatch(extract_json(chunk(lines)))
    if not detailed_logs and "detailed-logs-missing" not in report.warnings:
        report.warnings.append("detailed-logs-missing")
    return report
