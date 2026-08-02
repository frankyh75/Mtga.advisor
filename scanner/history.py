"""
scanner/history.py — Collection-History: Snapshot-Manager + Diff-Engine

Jeder Sync speichert Timestamp + Snapshot der Collection und Decks.
Die Diff-Engine vergleicht zwei Snapshots und zeigt Änderungen
("Was ist neu seit letzter Woche?").

Usage:
    from scanner.history import save_snapshot, list_snapshots, diff_snapshots

    # Snapshot speichern (nach jedem Sync/Scan)
    save_snapshot(output_dir=Path("out"))

    # Alle Snapshots auflisten
    snapshots = list_snapshots(output_dir=Path("out"))

    # Diff zwischen letztem und vorletztem Snapshot
    diff = diff_snapshots(output_dir=Path("out"))

Format:
    out/history/
    ├── 2026-08-01T10-00-00Z_collection.json
    ├── 2026-08-01T10-00-00Z_decks.json
    ├── 2026-08-02T14-30-00Z_collection.json
    ├── 2026-08-02T14-30-00Z_decks.json
    └── index.json  (Metadaten)
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HISTORY_SUBDIR = "history"
INDEX_FILENAME = "index.json"

# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def _iso_now() -> str:
    """Return current UTC timestamp in ISO 8601 format with Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _timestamp_to_filename(timestamp: str) -> str:
    """Convert ISO timestamp to filename-safe string (colons → dashes)."""
    return timestamp.replace(":", "-")


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------

def _load_index(index_path: Path) -> dict[str, Any]:
    """Load or create the history index."""
    if index_path.exists():
        try:
            return json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {
        "schema": "history-index.v1",
        "snapshots": [],
        "snapshotCount": 0,
    }


def _save_index(index_path: Path, index: dict[str, Any]) -> None:
    """Write the history index to disk."""
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------
# Snapshot saving
# ---------------------------------------------------------------------------

def save_snapshot(output_dir: Path = Path("out")) -> Path:
    """Save current collection.json and decks.json as a timestamped snapshot.

    Creates ``<output_dir>/history/<timestamp>_collection.json`` and
    ``<output_dir>/history/<timestamp>_decks.json``.
    Updates ``<output_dir>/history/index.json`` with metadata.

    Returns the history directory path.
    """
    history_dir = output_dir / HISTORY_SUBDIR
    history_dir.mkdir(parents=True, exist_ok=True)

    timestamp = _iso_now()
    safe_ts = _timestamp_to_filename(timestamp)

    snapshot_files: dict[str, str] = {}

    # Copy collection.json
    collection_path = output_dir / "collection.json"
    if collection_path.exists():
        dest = history_dir / f"{safe_ts}_collection.json"
        shutil.copy2(collection_path, dest)
        snapshot_files["collection"] = dest.name

    # Copy decks.json
    decks_path = output_dir / "decks.json"
    if decks_path.exists():
        dest = history_dir / f"{safe_ts}_decks.json"
        shutil.copy2(decks_path, dest)
        snapshot_files["decks"] = dest.name

    # Copy decks-container.json if it exists
    container_path = output_dir / "decks-container.json"
    if container_path.exists():
        dest = history_dir / f"{safe_ts}_decks-container.json"
        shutil.copy2(container_path, dest)
        snapshot_files["decks_container"] = dest.name

    # Build index entry
    entry: dict[str, Any] = {
        "timestamp": timestamp,
        "files": snapshot_files,
    }

    # Add summary stats for quick overview
    if "collection" in snapshot_files:
        coll = _read_json(history_dir / snapshot_files["collection"])
        if coll:
            cards = coll.get("cards", {})
            if isinstance(cards, dict):
                total = sum(
                    int(v) for v in cards.values()
                    if isinstance(v, (int, str)) and str(v).isdigit()
                )
            else:
                total = 0
            entry["collectionStats"] = {
                "uniqueCards": len(cards) if isinstance(cards, dict) else 0,
                "totalCards": total,
            }

    if "decks" in snapshot_files:
        decks_data = _read_json(history_dir / snapshot_files["decks"])
        if decks_data:
            entry["deckStats"] = {
                "deckCount": len(decks_data.get("decks", [])),
            }

    # Update index
    index_path = history_dir / INDEX_FILENAME
    index = _load_index(index_path)
    index["snapshots"].append(entry)
    index["lastUpdated"] = timestamp
    index["snapshotCount"] = len(index["snapshots"])
    _save_index(index_path, index)

    return history_dir


# ---------------------------------------------------------------------------
# Snapshot listing
# ---------------------------------------------------------------------------

def list_snapshots(output_dir: Path = Path("out")) -> list[dict[str, Any]]:
    """List all saved snapshots from the index.

    Returns a list of snapshot entry dicts, each containing:
    - timestamp: ISO 8601 string
    - files: dict of file_type → filename
    - collectionStats: optional summary stats
    - deckStats: optional summary stats
    """
    index_path = output_dir / HISTORY_SUBDIR / INDEX_FILENAME
    index = _load_index(index_path)
    return list(index.get("snapshots", []))


# ---------------------------------------------------------------------------
# Diff engine
# ---------------------------------------------------------------------------

def diff_snapshots(
    output_dir: Path = Path("out"),
    older: str | None = None,
    newer: str | None = None,
) -> dict[str, Any]:
    """Compute diff between two snapshots.

    Args:
        output_dir: Base output directory containing history/ subdir.
        older: Timestamp of older snapshot (None = second-to-last).
        newer: Timestamp of newer snapshot (None = last).

    Returns dict with collectionDiff and decksDiff, each containing
    added/removed/increased/decreased entries and a summary.
    """
    history_dir = output_dir / HISTORY_SUBDIR
    snapshots = list_snapshots(output_dir)

    if len(snapshots) < 2:
        return {
            "error": "Weniger als 2 Snapshots vorhanden — kein Diff möglich.",
            "snapshotCount": len(snapshots),
        }

    # Determine which snapshots to compare
    if older is None:
        older_entry = snapshots[-2]
    else:
        older_entry = _find_snapshot(snapshots, older)
        if older_entry is None:
            return {"error": f"Snapshot '{older}' nicht gefunden."}

    if newer is None:
        newer_entry = snapshots[-1]
    else:
        newer_entry = _find_snapshot(snapshots, newer)
        if newer_entry is None:
            return {"error": f"Snapshot '{newer}' nicht gefunden."}

    result: dict[str, Any] = {
        "older": older_entry["timestamp"],
        "newer": newer_entry["timestamp"],
        "collectionDiff": None,
        "decksDiff": None,
    }

    # Collection diff
    older_coll = _load_snapshot_file(history_dir, older_entry, "collection")
    newer_coll = _load_snapshot_file(history_dir, newer_entry, "collection")

    if older_coll and newer_coll:
        result["collectionDiff"] = _diff_collections(older_coll, newer_coll)
    elif older_coll is None and newer_coll is not None:
        result["collectionDiff"] = {"error": "Älterer Snapshot hat keine Collection-Daten."}
    elif older_coll is not None and newer_coll is None:
        result["collectionDiff"] = {"error": "Neuerer Snapshot hat keine Collection-Daten."}

    # Decks diff
    older_decks = _load_snapshot_file(history_dir, older_entry, "decks")
    newer_decks = _load_snapshot_file(history_dir, newer_entry, "decks")

    if older_decks and newer_decks:
        result["decksDiff"] = _diff_decks(older_decks, newer_decks)

    return result


# ---------------------------------------------------------------------------
# Collection diff
# ---------------------------------------------------------------------------

def _diff_collections(
    older: dict[str, Any],
    newer: dict[str, Any],
) -> dict[str, Any]:
    """Diff two collection.json payloads.

    Compares the ``cards`` dict (grpId → quantity) and ``wildcards`` dict.
    """
    older_cards_raw = older.get("cards", {})
    newer_cards_raw = newer.get("cards", {})

    # Normalize to {str: int}
    older_cards = _normalize_card_dict(older_cards_raw)
    newer_cards = _normalize_card_dict(newer_cards_raw)

    all_ids = set(older_cards.keys()) | set(newer_cards.keys())

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    increased: list[dict[str, Any]] = []
    decreased: list[dict[str, Any]] = []
    unchanged = 0

    for card_id in sorted(all_ids, key=lambda x: int(x) if x.lstrip("-").isdigit() else 0):
        old_qty = older_cards.get(card_id, 0)
        new_qty = newer_cards.get(card_id, 0)

        if old_qty == 0 and new_qty > 0:
            added.append({"cardId": card_id, "count": new_qty})
        elif old_qty > 0 and new_qty == 0:
            removed.append({"cardId": card_id, "oldCount": old_qty})
        elif new_qty > old_qty:
            increased.append({
                "cardId": card_id,
                "oldCount": old_qty,
                "newCount": new_qty,
                "delta": new_qty - old_qty,
            })
        elif new_qty < old_qty:
            decreased.append({
                "cardId": card_id,
                "oldCount": old_qty,
                "newCount": new_qty,
                "delta": old_qty - new_qty,
            })
        else:
            unchanged += 1

    # Wildcards diff
    older_wc = older.get("wildcards", {})
    newer_wc = newer.get("wildcards", {})
    wc_diff: dict[str, Any] = {}
    if isinstance(older_wc, dict) and isinstance(newer_wc, dict):
        for key in set(list(older_wc.keys()) + list(newer_wc.keys())):
            old_val = _safe_int(older_wc.get(key, 0))
            new_val = _safe_int(newer_wc.get(key, 0))
            if old_val != new_val:
                wc_diff[key] = {"old": old_val, "new": new_val, "delta": new_val - old_val}

    # Summary
    total_added = sum(c["count"] for c in added) + sum(c["delta"] for c in increased)
    total_removed = sum(c["oldCount"] for c in removed) + sum(c["delta"] for c in decreased)

    return {
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "increased": len(increased),
            "decreased": len(decreased),
            "unchanged": unchanged,
            "totalAdded": total_added,
            "totalRemoved": total_removed,
            "netChange": total_added - total_removed,
        },
        "added": added,
        "removed": removed,
        "increased": increased,
        "decreased": decreased,
        "wildcardDiff": wc_diff if wc_diff else None,
    }


# ---------------------------------------------------------------------------
# Decks diff
# ---------------------------------------------------------------------------

def _diff_decks(
    older: dict[str, Any],
    newer: dict[str, Any],
) -> dict[str, Any]:
    """Diff two decks.json payloads.

    Detects added, removed, and modified decks (card changes).
    """
    older_decks = {}
    for d in older.get("decks", []):
        deck_id = str(d.get("deckId", d.get("name", "")))
        older_decks[deck_id] = d

    newer_decks = {}
    for d in newer.get("decks", []):
        deck_id = str(d.get("deckId", d.get("name", "")))
        newer_decks[deck_id] = d

    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    modified: list[dict[str, Any]] = []

    all_ids = set(older_decks.keys()) | set(newer_decks.keys())

    for deck_id in sorted(all_ids):
        old_deck = older_decks.get(deck_id)
        new_deck = newer_decks.get(deck_id)

        if old_deck is None and new_deck is not None:
            added.append({
                "deckId": deck_id,
                "name": new_deck.get("name", "?"),
            })
        elif old_deck is not None and new_deck is None:
            removed.append({
                "deckId": deck_id,
                "name": old_deck.get("name", "?"),
            })
        elif old_deck and new_deck:
            old_cards = _extract_deck_card_counts(old_deck)
            new_cards = _extract_deck_card_counts(new_deck)
            if old_cards != new_cards:
                modified.append({
                    "deckId": deck_id,
                    "name": new_deck.get("name", old_deck.get("name", "?")),
                    "oldUniqueCards": len(old_cards),
                    "newUniqueCards": len(new_cards),
                    "cardChanges": _diff_deck_cards(old_cards, new_cards),
                })

    return {
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "modified": len(modified),
        },
        "added": added,
        "removed": removed,
        "modified": modified,
    }


def _diff_deck_cards(
    older: dict[str, int],
    newer: dict[str, int],
) -> dict[str, Any]:
    """Diff the card counts of a single deck between two snapshots."""
    all_ids = set(older.keys()) | set(newer.keys())
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    changed: list[dict[str, Any]] = []

    for cid in sorted(all_ids):
        old_qty = older.get(cid, 0)
        new_qty = newer.get(cid, 0)
        if old_qty == 0 and new_qty > 0:
            added.append({"cardId": cid, "count": new_qty})
        elif old_qty > 0 and new_qty == 0:
            removed.append({"cardId": cid, "oldCount": old_qty})
        elif new_qty != old_qty:
            changed.append({
                "cardId": cid,
                "oldCount": old_qty,
                "newCount": new_qty,
                "delta": new_qty - old_qty,
            })

    return {"added": added, "removed": removed, "changed": changed}


# ---------------------------------------------------------------------------
# Deck card extraction
# ---------------------------------------------------------------------------

def _extract_deck_card_counts(deck: dict[str, Any]) -> dict[str, int]:
    """Extract a normalized {cardId_str: count} dict from a deck.

    Supports all known deck formats:
    - Deck-scan format: cards = {pile: [{cardId, count}, ...]}
    - cardsById format: cardsById = {pile: {grpId_str: qty}}
    - Log-export format: mainDeck = [{grpId, quantity}, ...]
    """
    result: dict[str, int] = {}

    # Deck-scan format: cards = {pile_key: [{cardId, name, count}, ...]}
    cards = deck.get("cards")
    if isinstance(cards, dict):
        for pile_key in ("mainboard", "sideboard", "commandZone", "companions"):
            pile = cards.get(pile_key, [])
            if isinstance(pile, list):
                for card in pile:
                    if isinstance(card, dict):
                        cid = str(card.get("cardId", card.get("grpId", "")))
                        if cid:
                            cnt = _safe_int(card.get("count", card.get("quantity", 1)))
                            result[cid] = result.get(cid, 0) + cnt
            elif isinstance(pile, dict):
                for cid, cnt in pile.items():
                    result[str(cid)] = result.get(str(cid), 0) + _safe_int(cnt)
    elif isinstance(cards, list):
        # Flat card list — treat as mainboard
        for card in cards:
            if isinstance(card, dict):
                cid = str(card.get("cardId", card.get("grpId", "")))
                if cid:
                    cnt = _safe_int(card.get("count", card.get("quantity", 1)))
                    result[cid] = result.get(cid, 0) + cnt

    # cardsById format
    cards_by_id = deck.get("cardsById")
    if isinstance(cards_by_id, dict):
        for pile_key in ("mainboard", "sideboard", "commandZone", "companions"):
            pile = cards_by_id.get(pile_key, {})
            if isinstance(pile, dict):
                for cid, cnt in pile.items():
                    result[str(cid)] = result.get(str(cid), 0) + _safe_int(cnt)

    # Log-export format fallback: mainDeck / sideboard
    if not result:
        main_deck = deck.get("mainDeck", [])
        if isinstance(main_deck, list):
            for card in main_deck:
                if isinstance(card, dict):
                    cid = str(card.get("grpId", card.get("cardId", "")))
                    if cid:
                        cnt = _safe_int(card.get("quantity", card.get("count", 1)))
                        result[cid] = result.get(cid, 0) + cnt

        sb = deck.get("sideboard", [])
        if isinstance(sb, list):
            for card in sb:
                if isinstance(card, dict):
                    cid = str(card.get("grpId", card.get("cardId", "")))
                    if cid:
                        cnt = _safe_int(card.get("quantity", card.get("count", 1)))
                        result[cid] = result.get(cid, 0) + cnt

    return result


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _normalize_card_dict(cards: Any) -> dict[str, int]:
    """Normalize a card dict to {str: int}, handling various input formats."""
    if not isinstance(cards, dict):
        return {}
    result: dict[str, int] = {}
    for k, v in cards.items():
        try:
            result[str(k)] = int(v)
        except (ValueError, TypeError):
            continue
    return result


def _safe_int(value: Any, default: int = 0) -> int:
    """Safely convert a value to int."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


def _find_snapshot(
    snapshots: list[dict[str, Any]],
    timestamp: str,
) -> dict[str, Any] | None:
    """Find a snapshot by timestamp (exact or prefix match)."""
    # Exact match
    for s in snapshots:
        if s["timestamp"] == timestamp:
            return s
    # Prefix match (e.g., "2026-08-01" matches "2026-08-01T10:00:00Z")
    for s in snapshots:
        if s["timestamp"].startswith(timestamp):
            return s
    return None


def _load_snapshot_file(
    history_dir: Path,
    entry: dict[str, Any],
    file_type: str,
) -> dict[str, Any] | None:
    """Load a specific file from a snapshot entry."""
    files = entry.get("files", {})
    filename = files.get(file_type)
    if not filename:
        return None
    return _read_json(history_dir / filename)


# ---------------------------------------------------------------------------
# Formatting helpers (for CLI output)
# ---------------------------------------------------------------------------

def format_diff_summary(diff: dict[str, Any]) -> str:
    """Format a diff result as a human-readable string for CLI output."""
    if "error" in diff:
        return f"ERROR: {diff['error']}"

    lines: list[str] = []
    lines.append(f"Diff: {diff['older']} → {diff['newer']}")
    lines.append("")

    coll_diff = diff.get("collectionDiff")
    if coll_diff and "error" not in coll_diff:
        summary = coll_diff["summary"]
        lines.append("=== Collection ===")
        lines.append(
            f"  +{summary['added']} neu, "
            f"+{summary['increased']} erhöht, "
            f"-{summary['removed']} entfernt, "
            f"-{summary['decreased']} reduziert, "
            f"{summary['unchanged']} unverändert"
        )
        lines.append(
            f"  Netto: {'+' if summary['netChange'] >= 0 else ''}"
            f"{summary['netChange']} Karten"
        )

        # Detail: added cards
        for card in coll_diff.get("added", [])[:20]:
            lines.append(f"  +{card['count']}x Card {card['cardId']} (neu)")
        for card in coll_diff.get("increased", [])[:20]:
            lines.append(
                f"  +{card['delta']}x Card {card['cardId']} "
                f"(jetzt {card['newCount']}x, war {card['oldCount']}x)"
            )
        for card in coll_diff.get("removed", [])[:20]:
            lines.append(f"  -{card['oldCount']}x Card {card['cardId']} (entfernt)")
        for card in coll_diff.get("decreased", [])[:20]:
            lines.append(
                f"  -{card['delta']}x Card {card['cardId']} "
                f"(jetzt {card['newCount']}x, war {card['oldCount']}x)"
            )

        # Wildcards
        wc = coll_diff.get("wildcardDiff")
        if wc:
            lines.append("  Wildcards:")
            for key, change in sorted(wc.items()):
                lines.append(
                    f"    {key}: {change['old']} → {change['new']} "
                    f"({'+' if change['delta'] >= 0 else ''}{change['delta']})"
                )
        lines.append("")

    decks_diff = diff.get("decksDiff")
    if decks_diff:
        summary = decks_diff["summary"]
        lines.append("=== Decks ===")
        lines.append(
            f"  +{summary['added']} neu, "
            f"-{summary['removed']} entfernt, "
            f"~{summary['modified']} verändert"
        )
        for d in decks_diff.get("added", []):
            lines.append(f"  + {d['name']} ({d['deckId']})")
        for d in decks_diff.get("removed", []):
            lines.append(f"  - {d['name']} ({d['deckId']})")
        for d in decks_diff.get("modified", []):
            lines.append(f"  ~ {d['name']} ({d['deckId']})")
        lines.append("")

    return "\n".join(lines)


def format_snapshot_list(snapshots: list[dict[str, Any]]) -> str:
    """Format snapshot list as a human-readable string for CLI output."""
    if not snapshots:
        return "Keine Snapshots vorhanden."

    lines: list[str] = [f"{len(snapshots)} Snapshots:", ""]
    for i, s in enumerate(snapshots):
        ts = s["timestamp"]
        files = s.get("files", {})
        stats = []
        if "collectionStats" in s:
            cs = s["collectionStats"]
            stats.append(f"{cs['uniqueCards']} unique, {cs['totalCards']} total")
        if "deckStats" in s:
            ds = s["deckStats"]
            stats.append(f"{ds['deckCount']} decks")
        file_types = ", ".join(files.keys()) if files else "keine Dateien"
        stat_str = f" ({', '.join(stats)})" if stats else ""
        marker = " ← latest" if i == len(snapshots) - 1 else ""
        lines.append(f"  [{i}] {ts}{stat_str}{marker}")
        lines.append(f"      files: {file_types}")

    return "\n".join(lines)