"""Deck-Export aus MTGA-Logs.

Extrahiert DeckSummaries aus StartHook-Events in Player.log-Dateien
und schreibt sie als decks.json parallel zu collection.json.

Zusätzlich: Container-Export für strukturierte Deck-Verwaltung
mit Verlinkung zu manuellen Decklisten.

Referenz: mtgatool-desktop InStartHook.ts
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json
import re
from typing import Iterable, Optional, Any

from .pipeline import ParsedEvent
from .start_hook import DeckSummary, parse_start_hook


PRECON_MARKERS = (
    "loc/decks/precon",
    "precon",
    "starter deck",
    "starterdeck",
    "beginner deck",
    "beginner",
    "intro deck",
    "introductory",
    "npe",
    "spotlight",
    "storydeck",
    "story deck",
)


@dataclass(frozen=True)
class DeckExportPaths:
    """Pfade der Deck-Export-Artefakte."""

    decks: Path
    run_report: Path


@dataclass(frozen=True)
class ContainerPaths:
    """Pfade des Deck-Container-Exports."""

    index: Path
    deck_dir: Path


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
    # Effizienter zeilenweiser Pfad: liest nur DeckSummaries-Zeilen,
    # nicht alle Zeilen in den Speicher. Der alte Pipeline-Weg
    # (ingest→chunk→extract_json→_extract_decks) war zu langsam auf
    # großen Logs (25 MB → Timeout).
    decks, warnings = _extract_decks_from_raw_json_lines(sorted_paths)
    decks = _dedupe_decks(decks)

    # Ergänze Struktur-Warnings (früher in _extract_decks generiert)
    for deck in decks:
        if deck.deck_id is None:
            warnings.append(
                f"Deck '{deck.name}' hat keine DeckId — kann nicht eindeutig identifiziert werden."
            )
    name_counts: dict[str, int] = {}
    for deck in decks:
        name_counts[deck.name] = name_counts.get(deck.name, 0) + 1
    for name, count in name_counts.items():
        if count > 1:
            warnings.append(
                f"Deck-Name '{name}' tritt {count}-mal auf (verschiedene IDs). "
                "Möglicherweise unterschiedliche Deck-Varianten."
            )

    decks_path = output_dir / "decks.json"
    run_report_path = output_dir / "run-report-decks.json"

    _write_json(decks_path, _build_decks_payload(decks, sorted_paths))

    finished_at = _iso_now()
    _write_json(
        run_report_path,
        _build_run_report(decks, sorted_paths, decks_path, started_at, finished_at, warnings),
    )

    return DeckExportPaths(decks=decks_path, run_report=run_report_path)


def export_container(deck_dir: Path, decks: list[DeckSummary], refresh: bool = False) -> ContainerPaths:
    """Exportiere Decks als Container mit individuellem Format für jedes Deck.

    Args:
        deck_dir: Ausgabeverzeichnis für den Container.
        decks: Liste der zu exportierenden Decks.
        refresh: Ob vorhandene Deck-Dateien überschrieben werden sollen.

    Returns:
        ContainerPaths mit Pfaden zu index.json und dem Verzeichnis.
    """
    deck_dir.mkdir(parents=True, exist_ok=True)

    decks_by_id: dict[str, DeckSummary] = {
        (d.deck_id or d.name): d for d in decks
    }

    index_entries: list[dict[str, any]] = []
    for key in [d.deck_id or d.name for d in decks]:
        deck = decks_by_id.get(key)
        if not deck:
            continue
        deck_id = deck.deck_id or key
        entry = {
            "deckId": deck_id,
            "name": deck.name,
            "hasFullList": False,
            "fullListPath": f"{_slug(deck_id)}.arena.json",
            "summaryPath": f"{_slug(deck_id)}.json",
            "textListPath": f"{_slug(deck_id)}.txt",
        }
        index_entries.append(entry)

        summary_path = deck_dir / f"{_slug(deck_id)}.json"
        if not summary_path.exists() or refresh:
            _write_json(summary_path, _build_deck_summary_payload(deck))

        index_entries[-1]["hasFullList"] = False

    index_payload = {
        "schema": "decks-container.v1",
        "generatedAt": _iso_now(),
        "deckCount": len(index_entries),
        "decks": index_entries,
    }

    index_path = deck_dir / "index.json"
    _write_json(index_path, index_payload)

    return ContainerPaths(index=index_path, deck_dir=deck_dir)


def show_deck(deck_id: str, deck_dir: Path) -> dict[str, any]:
    """Zeige Details eines einzelnen Decks aus dem Container.

    Args:
        deck_id: Die Deck-ID des anzuzeigenden Decks.
        deck_dir: Container-Verzeichnis.

    Returns:
        Deck-Detail-Payload oder None wenn nicht gefunden.
    """
    slug = _slug(deck_id)
    summary_path = deck_dir / f"{slug}.json"
    if not summary_path.exists():
        return None
    return json.loads(summary_path.read_text(encoding="utf-8"))


def list_decks(deck_dir: Path) -> dict[str, any]:
    """Liste alle Decks aus dem Container.

    Args:
        deck_dir: Container-Verzeichnis.

    Returns:
        Index-Payload des Containers.
    """
    index_path = deck_dir / "index.json"
    if not index_path.exists():
        return {"schema": "error", "message": "Kein Container gefunden"}
    return json.loads(index_path.read_text(encoding="utf-8"))


def _extract_decks(events: list[ParsedEvent]) -> tuple[list[DeckSummary], list[str]]:
    """Extrahiere DeckSummaries aus den Deck-Events in den Logs.

    Sammelt Decks aus StartHook-Events und aus CourseDeckSummary-Strukturen.
    Neuere Events überschreiben ältere (letzter Login gewinnt).

    Returns:
        Tuple von (decks, warnings).
    """
    seen: dict[str, DeckSummary] = {}  # deck_id -> deck (letzter gewinnt)
    order: list[str] = []  # Reihenfolge der IDs
    warnings: list[str] = []

    for event in events:
        if not isinstance(event.data, dict):
            continue

        if event.event == "StartHook" or "DeckSummaries" in event.data:
            parsed, hook_warnings = parse_start_hook(event.data)
            if parsed is not None:
                for deck in parsed.deck_summaries:
                    key = deck.deck_id or deck.name
                    if key not in seen:
                        order.append(key)
                    seen[key] = deck
            if hook_warnings:
                warnings.extend(hook_warnings)

        course_decks, course_warnings = _parse_course_deck_summaries(event.data)
        if course_decks:
            for deck in course_decks:
                key = deck.deck_id or deck.name
                if key not in seen:
                    order.append(key)
                seen[key] = deck
        if course_warnings:
            warnings.extend(course_warnings)

    # Flagge Decks die keine DeckId haben (werden als Key genutzt)
    for deck in seen.values():
        if deck.deck_id is None:
            warnings.append(
                f"Deck '{deck.name}' hat keine DeckId — kann nicht eindeutig identifiziert werden."
            )

    # Prüfe auf doppelte Namen (könnte auf Log-Wiederholungen hindeuten)
    name_counts: dict[str, int] = {}
    for key in order:
        deck = seen[key]
        name_counts[deck.name] = name_counts.get(deck.name, 0) + 1
    for name, count in name_counts.items():
        if count > 1:
            warnings.append(
                f"Deck-Name '{name}' tritt {count}-mal auf (verschiedene IDs). "
                "Möglicherweise unterschiedliche Deck-Varianten."
            )

    return [seen[key] for key in order], warnings


def _extract_decks_from_raw_json_lines(paths: list[Path]) -> tuple[list[DeckSummary], list[str]]:
    """Effizienter zeilenweiser Deck-Extraktionspfad.

    Liest die Logs zeilenweise (nicht alles in den Speicher), filtert nur
    Zeilen mit ``DeckSummaries`` oder ``CourseDeckSummary`` und extrahiert
    robust den JSON-Block daraus.

    Manche Log-Zeilen sind reines JSON (starten direkt mit ``{``), andere
    haben einen Präfix (z.B. Timestamp) mit JSON-Block danach. Diese Funktion
    findet den ersten ``{`` und scannt bis zur passenden schließenden ``}``,
    wobei Strings und Escapes korrekt behandelt werden.
    """
    decks: list[DeckSummary] = []
    warnings: list[str] = []

    for path in paths:
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    # Quick filter: nur Zeilen mit Deck-Kennzeichen verarbeiten
                    if '"DeckSummaries"' not in raw_line and '"CourseDeckSummary"' not in raw_line:
                        continue

                    payload = _extract_json_block(raw_line)
                    if payload is None:
                        continue

                    found, payload_warnings = _extract_decks_from_payload(payload)
                    if found:
                        decks.extend(found)
                    if payload_warnings:
                        warnings.extend(
                            f"{path.name}:{line_number}: {w}" for w in payload_warnings
                        )
        except OSError as exc:
            warnings.append(f"{path.name}: konnte nicht gelesen werden ({exc})")

    return decks, warnings


def _extract_json_block(line: str) -> dict[str, Any] | None:
    """Extrahiere den ersten vollständigen JSON-Objekt-Block aus einer Zeile.

    Findet das erste ``{`` und scannt bis zur passenden schließenden ``}``,
    wobei Strings (single/double quotes) und Escape-Sequenzen beachtet werden.

    Versuche-Reihenfolge:
    1. Brace-matching: extrahiere den JSON-Block ab erstem ``{``.
    2. Falls json.loads auf den Block fehlschlägt, versuche die ganze Zeile
       ab ``{`` (kann bei mehrzeiligen Konstrukten helfen).
    3. Als letzten Versuch: json.loads auf die ganze (getrimmte) Zeile.
    """
    start = line.find("{")
    if start == -1:
        return None

    json_str = _scan_json_object(line, start)
    if json_str is not None:
        try:
            data = json.loads(json_str)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass  # Fallback unten

    # Fallback 1: ganze Zeile ab erstem '{'
    tail = line[start:]
    try:
        data = json.loads(tail)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    # Fallback 2: ganze getrimmte Zeile (für den Fall, dass '{' nicht der Start ist)
    stripped = line.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return None


def _scan_json_object(text: str, start: int) -> str | None:
    """Scanne ab Position *start* bis zur passenden schließenden Klammer.

    Gibt den Substring inklusive der äußeren ``{}``  zurück, oder ``None``
    wenn kein vollständiges JSON-Objekt gefunden wurde.
    """
    depth = 0
    in_string = False
    string_char = ""
    i = start

    while i < len(text):
        ch = text[i]

        if in_string:
            if ch == "\\":
                # Escape: überspringe das nächste Zeichen
                i += 2
                continue
            if ch == string_char:
                in_string = False
            i += 1
            continue

        # Nicht in einem String
        if ch == '"' or ch == "'":
            in_string = True
            string_char = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

        i += 1

    return None  # keine schließende Klammer gefunden


def _extract_decks_from_payload(data: dict[str, Any]) -> tuple[list[DeckSummary], list[str]]:
    """Extrahiere DeckSummaries aus einem einzelnen JSON-Payload."""
    if not isinstance(data, dict):
        return [], []

    decks: list[DeckSummary] = []
    warnings: list[str] = []

    parsed, hook_warnings = parse_start_hook(data)
    if parsed is not None:
        decks.extend(parsed.deck_summaries)
    if hook_warnings:
        warnings.extend(hook_warnings)

    course_decks, course_warnings = _parse_course_deck_summaries(data)
    if course_decks:
        decks.extend(course_decks)
    if course_warnings:
        warnings.extend(course_warnings)

    return decks, warnings


def _parse_course_deck_summaries(data: dict[str, Any]) -> tuple[list[DeckSummary], list[str]]:
    """Extrahiere DeckSummaries aus CourseDeckSummary-Strukturen."""
    raw_courses = data.get("Courses")
    if not isinstance(raw_courses, list):
        return [], []

    decks: list[DeckSummary] = []
    warnings: list[str] = []

    for idx, raw_course in enumerate(raw_courses):
        if not isinstance(raw_course, dict):
            warnings.append(f"Courses[{idx}] ist kein Objekt, übersprungen.")
            continue

        summary = raw_course.get("CourseDeckSummary")
        if not isinstance(summary, dict):
            continue

        name = summary.get("Name", "")
        if not isinstance(name, str):
            name = str(name) if name is not None else ""
        if not name.strip():
            warnings.append(f"Courses[{idx}].CourseDeckSummary hat keinen Namen, übersprungen.")
            continue

        deck_id = summary.get("DeckId")
        if deck_id is not None:
            try:
                deck_id = str(deck_id)
            except (TypeError, ValueError):
                warnings.append(f"Courses[{idx}].CourseDeckSummary: DeckId konnte nicht konvertiert werden.")
                deck_id = None

        deck_tile_id = summary.get("DeckTileId")
        if deck_tile_id is not None:
            try:
                deck_tile_id = int(deck_tile_id)
            except (TypeError, ValueError):
                warnings.append(f"Courses[{idx}].CourseDeckSummary: DeckTileId konnte nicht konvertiert werden.")
                deck_tile_id = None

        attributes_raw = summary.get("Attributes", {})
        attributes: dict[str, str]
        if isinstance(attributes_raw, list):
            attributes = {}
            for attr in attributes_raw:
                if isinstance(attr, dict):
                    key = attr.get("name", attr.get("key"))
                    value = attr.get("value")
                    if key is not None and value is not None:
                        attributes[str(key)] = str(value)
        elif isinstance(attributes_raw, dict):
            attributes = {str(k): str(v) for k, v in attributes_raw.items()}
        else:
            attributes = {}

        format_legalities: dict[str, bool] = {}
        format_name = attributes.get("Format")
        if isinstance(format_name, str) and format_name:
            format_legalities[format_name] = True

        description = summary.get("Description")
        if description is not None and not isinstance(description, str):
            description = str(description)

        decks.append(
            DeckSummary(
                name=name,
                deck_id=deck_id,
                deck_tile_id=deck_tile_id,
                description=description,
                attributes=attributes,
                format_legalities=format_legalities,
                is_companion_valid=None,
                mana=None,
            )
        )

    return decks, warnings


def _dedupe_decks(decks: list[DeckSummary]) -> list[DeckSummary]:
    """Dedupere Decks nach DeckId oder Name, letzter Treffer gewinnt."""
    seen: dict[str, DeckSummary] = {}
    order: list[str] = []
    for deck in decks:
        key = deck.deck_id or deck.name
        if key not in seen:
            order.append(key)
        seen[key] = deck
    return [seen[key] for key in order]


def _build_deck_summary_payload(deck: DeckSummary) -> dict:
    """Baue das Payload für ein einzelnes Deck."""
    return {
        "schema": "deck-summary.v1",
        "name": deck.name,
        "deckKey": _deck_key(deck),
        "deckId": deck.deck_id,
        "deckTileId": deck.deck_tile_id,
        "description": deck.description,
        "attributes": deck.attributes,
        "formatLegalities": deck.format_legalities,
        "isCompanionValid": deck.is_companion_valid,
        "mana": deck.mana,
        "hasDeckId": deck.deck_id is not None,
        "isPrecon": _is_precon_deck(deck),
        "preconReason": _precon_reason(deck),
    }


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
                "deckKey": _deck_key(d),
                "deckId": d.deck_id,
                "deckTileId": d.deck_tile_id,
                "description": d.description,
                "attributes": d.attributes,
                "formatLegalities": d.format_legalities,
                "isCompanionValid": d.is_companion_valid,
                "mana": d.mana,
                "hasDeckId": d.deck_id is not None,
                "isPrecon": _is_precon_deck(d),
                "preconReason": _precon_reason(d),
            }
            for d in decks
        ],
        "diagnostics": {
            "deckCount": len(decks),
            "logs": [p.as_posix() for p in log_paths],
            "missingDeckIds": len([d for d in decks if d.deck_id is None]),
            "warnings": list(set(
                f"Deck '{d.name}' hat keine DeckId — kann nicht eindeutig identifiziert werden."
                for d in decks if d.deck_id is None
            )),
        },
    }


def _build_run_report(
    decks: list[DeckSummary],
    log_paths: list[Path],
    decks_path: Path,
    started_at: str,
    finished_at: str,
    parser_warnings: list[str],
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
            "warnings": parser_warnings,
            "evidence": ["start-hook-parser"],
        },
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _slug(value: str) -> str:
    """Konvertiere einen Wert in einen safe Dateinamen."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower() or "deck"
    return slug[:50]  # Begrenze Länge für Dateisystem-Kompatibilität


def _deck_key(deck: DeckSummary) -> str:
    """Baue einen stabilen Schlüssel für UI und Detail-Routing."""
    if deck.deck_id:
        return deck.deck_id
    if deck.deck_tile_id is not None:
        return f"tile-{deck.deck_tile_id}"
    return _slug(deck.name)


def _precon_reason(deck: DeckSummary) -> str | None:
    """Ermittle den ersten Precon-Hinweis, falls vorhanden.

    Ein echtes Precon hat einen Namen, der auf ein Precon-Template verweist
    (z.B. ``?=?Loc/Decks/Precon/...``). Die Description kann auf ein
    Precon-Template verweisen, auf dem ein EIGENES Deck basiert (z.B.
    ``Decks/Precon/Precon_EPP2021_BG_Desc``) — das ist dann KEIN Precon.
    Daher zählt nur der Name als Precon-Indikator, nicht die Description.
    """
    haystack = " ".join(
        [
            deck.name or "",
            " ".join(f"{k}:{v}" for k, v in deck.attributes.items()),
            " ".join(f"{k}:{v}" for k, v in deck.format_legalities.items()),
        ]
    ).lower()
    for marker in PRECON_MARKERS:
        if marker in haystack:
            return marker
    return None


def _is_precon_deck(deck: DeckSummary) -> bool:
    return _precon_reason(deck) is not None


def _iso_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
