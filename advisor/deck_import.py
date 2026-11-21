from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DECK_SCHEMA = "arena-deck.v1"
DECK_LINE_RE = re.compile(
    r"^(?P<count>\d+)\s+(?P<name>.+?)(?:\s+\((?P<set>[^)]+)\)\s+(?P<collector_number>\d+))?$"
)


def import_arena_deck(
    text: str,
    *,
    card_db: dict[int, dict[str, Any]],
    deck_format: str,
    name: str | None = None,
    collection_ids: set[int] | None = None,
) -> dict[str, Any]:
    """Parse Arena text export into arena_deck.json payload.

    When ``collection_ids`` is provided, reprint resolution (same card name
    across multiple sets) prefers the grpId that actually exists in the
    player's collection. This ensures the imported deck uses IDs that match
    the collection, so downstream analysis correctly reports owned cards.
    If no candidate matches the collection, the alphabetically-last set is
    used as fallback (preserving the original behaviour).

    Zeilen, deren Karte in der Karten-DB fehlt, aber über ``set`` +
    ``collectorNumber`` identifizierbar sind (Arena-Export liefert immer
    ``<count> <name> (<SET>) <CN>``), werden NICHT verworfen: sie landen mit
    ``arenaId: null`` und ``reason: "unresolved-arena-id"`` im Board und
    zählen über ``diagnostics.unresolvedCards`` in der Summe mit. Der
    Kartenname dient dabei als Konsistenzprüfung für den Nutzer — die Karte
    wird über Set+CollectorNumber identifiziert, nicht über den Namen.
    """
    mainboard: list[dict[str, Any]] = []
    sideboard: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    section = "mainboard"
    deck_name = name or "Imported Deck"
    # Keep the full card database and fail conservatively on same-name ambiguity.
    name_to_cards = _build_name_index(card_db)
    # Summe der Counts aller geparsten Zeilen — gegen sie wird am Ende
    # verifiziert, dass keine Karte stillschweigend verschwindet.
    parsed_card_count = 0
    identity_index: dict[tuple[str, str], set[str]] = {}
    for meta in card_db.values():
        key = (str(meta.get("set", "")).upper(), str(meta.get("collector_number", "")))
        if all(key) and meta.get("name"):
            identity_index.setdefault(key, set()).add(_normalize_name(str(meta["name"])))
    # Karten, die über Set+CN aufgenommen wurden, aber keine Arena-ID haben.
    unresolved_arena_count = 0
    unknown_sets: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower = line.lower()
        if lower == "deck":
            section = "mainboard"
            continue
        if lower == "sideboard":
            section = "sideboard"
            continue
        if lower.startswith("deck name:"):
            deck_name = line.split(":", 1)[1].strip() or deck_name
            continue

        parsed = _parse_deck_line(line)
        if parsed is None:
            unresolved.append({"line": line, "reason": "unparseable-line"})
            continue
        count, card_name, set_code, collector_number = parsed
        parsed_card_count += count
        candidates = name_to_cards.get(_normalize_name(card_name), [])
        identity_names = identity_index.get((set_code or "", collector_number or ""), set())
        if not candidates and identity_names and _normalize_name(card_name) not in identity_names:
            entry = {"arenaId": None, "name": card_name, "set": set_code,
                     "collectorNumber": collector_number, "rarity": "unknown",
                     "count": count, "reason": "identity-mismatch"}
            if set_code:
                unknown_sets.add(set_code)
            (sideboard if section == "sideboard" else mainboard).append(entry)
            continue
        if not candidates:
            if set_code and collector_number:
                # Karte ist über Set + CollectorNumber eindeutig identifiziert
                # (der Name dient als Konsistenzprüfung), aber nicht in der
                # Karten-DB — z.B. TRK/FRA: Scryfall liefert keine arena_id.
                # Statt die Zeile stillschweigend fallen zu lassen, wird sie
                # ohne Arena-ID aufgenommen und diagonalisiert.
                entry = {
                    "arenaId": None,
                    "name": card_name,
                    "set": set_code,
                    "collectorNumber": collector_number,
                    "rarity": "unknown",
                    "count": count,
                    "reason": "unresolved-arena-id",
                }
                unresolved_arena_count += count
                unknown_sets.add(set_code)
                if section == "sideboard":
                    sideboard.append(entry)
                else:
                    mainboard.append(entry)
            else:
                unresolved.append(
                    {
                        "line": line,
                        "name": card_name,
                        "count": count,
                        "zone": "sideboard" if section == "sideboard" else "mainDeck",
                        "reason": "unknown-card-name",
                    }
                )
            continue
        if len(candidates) > 1:
            # Same card name across multiple sets (reprints).
            # Prefer a grpId that exists in the player's collection so the
            # imported deck IDs match the collection IDs used downstream.
            # If no candidate is in the collection (or no collection given),
            # fall back to the alphabetically-last set (original behaviour).
            chosen = _resolve_reprint(candidates, collection_ids)
            entry = dict(chosen)
            entry["count"] = count
            if section == "sideboard":
                sideboard.append(entry)
            else:
                mainboard.append(entry)
            continue

        entry = dict(candidates[0])
        entry["count"] = count
        if section == "sideboard":
            sideboard.append(entry)
        else:
            mainboard.append(entry)

    # Konsistenz-Assertion: Jede geparste Zeile muss entweder in einem Board
    # gelandet sein (aufgelöst ODER unresolved-arena-id) oder explizit in der
    # unresolved-Diagnose aufgeführt sein. Keine Karte darf stillschweigend
    # verschwinden — sonst ist totalCards downstream zu klein gerechnet.
    tracked = sum(int(e["count"]) for e in mainboard) + sum(int(e["count"]) for e in sideboard)
    dropped = sum(int(u.get("count", 0)) for u in unresolved)
    assert tracked + dropped == parsed_card_count, (
        f"Deck-Import verlor Karten: geparst {parsed_card_count} Karten, "
        f"übernommen {tracked}, verworfen {dropped} (unresolved-Diagnose) — "
        "Differenz verschwundene Karten"
    )

    payload = {
        "schema": DECK_SCHEMA,
        "deckId": _deck_id(deck_name, deck_format, mainboard, sideboard),
        "name": deck_name,
        "format": deck_format,
        "importedAt": _iso_now(),
        "mainboard": mainboard,
        "sideboard": sideboard,
        "diagnostics": {
            "unresolved": unresolved,
            "ambiguous": ambiguous,
            "unresolvedCards": parsed_card_count - sum(int(e["count"]) for e in mainboard + sideboard if e.get("arenaId") is not None),
            "unresolvedArenaIdCards": unresolved_arena_count,
            "unknownSets": sorted(unknown_sets),
            "warnings": _warnings(unresolved, ambiguous, unresolved_arena_count),
        },
    }
    return payload


def write_deck(deck: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "arena_deck.json"
    path.write_text(json.dumps(deck, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _build_name_index(card_db: dict[int, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for arena_id, meta in card_db.items():
        name = meta.get("name")
        if not name:
            continue
        key = _normalize_name(str(name))
        entry = {
            "arenaId": arena_id,
            "name": str(name),
            "set": meta.get("set", ""),
            "collectorNumber": meta.get("collector_number", ""),
            "rarity": meta.get("rarity", "unknown"),
        }
        index.setdefault(key, []).append(entry)

    for entries in index.values():
        entries.sort(key=lambda entry: (str(entry.get("set", "")), int(entry["arenaId"])))
    return index


def _parse_deck_line(line: str) -> tuple[int, str, str | None, str | None] | None:
    """Parse eine Arena-Zeile ``<count> <name> [(<SET>) <CN>]``.

    Returns:
        ``(count, name, set_code, collector_number)`` — ``set_code`` und
        ``collector_number`` sind ``None``, wenn die Zeile keinen
        Arena-Suffix ``(SET) <CollectorNumber>`` hat.
    """
    match = DECK_LINE_RE.match(line)
    if not match:
        return None
    set_code = match.group("set")
    collector_number = match.group("collector_number")
    return (
        int(match.group("count")),
        match.group("name").strip(),
        set_code.strip().upper() if set_code else None,
        collector_number,
    )


def _resolve_reprint(
    candidates: list[dict[str, Any]],
    collection_ids: set[int] | None,
) -> dict[str, Any]:
    """Pick the best candidate for a reprinted card name.

    If ``collection_ids`` is provided and one or more candidates have an
    ``arenaId`` that exists in the collection, the first matching candidate
    (sorted by set then arenaId) is returned — guaranteeing the imported
    deck uses IDs that align with the player's collection.

    If no candidate matches the collection (or no collection is given), the
    alphabetically-last candidate is returned (original behaviour: newest
    set as a heuristic).
    """
    if collection_ids:
        for cand in candidates:
            if int(cand["arenaId"]) in collection_ids:
                return cand
    return candidates[-1]


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


def _deck_id(
    name: str,
    deck_format: str,
    mainboard: list[dict[str, Any]],
    sideboard: list[dict[str, Any]],
) -> str:
    basis = json.dumps(
        {
            "name": name,
            "format": deck_format,
            "mainboard": mainboard,
            "sideboard": sideboard,
        },
        sort_keys=True,
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def _warnings(
    unresolved: list[dict[str, Any]],
    ambiguous: list[dict[str, Any]],
    unresolved_arena_cards: int = 0,
) -> list[str]:
    warnings: list[str] = []
    if unresolved:
        warnings.append("unresolved-deck-lines")
    if ambiguous:
        warnings.append("ambiguous-card-names")
    if unresolved_arena_cards:
        warnings.append("arena-id-unbekannt")
    return warnings


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
