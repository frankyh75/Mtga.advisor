from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DECK_SCHEMA = "arena-deck.v1"
DECK_LINE_RE = re.compile(r"^(?P<count>\d+)\s+(?P<name>.+?)(?:\s+\([^)]+\)\s+\d+)?$")


def import_arena_deck(
    text: str,
    *,
    card_db: dict[int, dict[str, Any]],
    deck_format: str,
    name: str | None = None,
) -> dict[str, Any]:
    """Parse Arena text export into arena_deck.json payload."""
    mainboard: list[dict[str, Any]] = []
    sideboard: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    section = "mainboard"
    deck_name = name or "Imported Deck"
    # Resolve duplicates: for same normalized name, prefer newest set (handles
    # multiple Arena-IDs for the same card across different printings).
    resolved_db = _resolve_card_db(card_db)
    name_to_cards = _build_name_index(resolved_db)

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
        count, card_name = parsed
        candidates = name_to_cards.get(_normalize_name(card_name), [])
        if not candidates:
            unresolved.append({"line": line, "name": card_name, "reason": "unknown-card-name"})
            continue
        if len(candidates) > 1:
            ambiguous.append(
                {
                    "line": line,
                    "name": card_name,
                    "candidates": candidates[:20],
                    "truncated": len(candidates) > 20,
                }
            )
            continue

        entry = dict(candidates[0])
        entry["count"] = count
        if section == "sideboard":
            sideboard.append(entry)
        else:
            mainboard.append(entry)

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
            "warnings": _warnings(unresolved, ambiguous),
        },
    }
    return payload


def write_deck(deck: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "arena_deck.json"
    path.write_text(json.dumps(deck, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _resolve_card_db(card_db: dict[int, dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Resolve duplicate card names: keep newest set entry per normalized name.
    
    Scryfall provides multiple Arena IDs for the same card (reprints).
    This deduplicates by keeping only the entry from the newest set.
    """
    name_to_best: dict[str, dict[int, dict[str, Any]]] = {}
    
    for arena_id, meta in card_db.items():
        name = meta.get("name")
        if not name:
            continue
        key = _normalize_name(str(name))
        
        if key not in name_to_best:
            name_to_best[key] = {arena_id: meta}
        else:
            # Keep only if this entry has a newer set
            best_aid, best_meta = next(iter(name_to_best[key].items()))
            best_set = best_meta.get("set", "")
            new_set = meta.get("set", "")
            
            if new_set and (not best_set or _is_newer_set(new_set, best_set)):
                # New entry is newer or best has no set
                name_to_best[key] = {arena_id: meta}
            # Else keep existing (newer or equal)
    
    # Flatten back to arena_id -> meta
    result: dict[int, dict[str, Any]] = {}
    for entries in name_to_best.values():
        result.update(entries)
    
    return result


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


def _parse_deck_line(line: str) -> tuple[int, str] | None:
    match = DECK_LINE_RE.match(line)
    if not match:
        return None
    return int(match.group("count")), match.group("name").strip()


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", name).strip().lower()


# Standard set codes in chronological order (abbreviated)
_SET_ORDER = [
    "2X2", "AER", "AKH", "ALK", "ALP", "ANC", "AOC", "ANT", "AOA", "ARB",
    "ARM", "AVR", "BFC", "BNG", "BRO", "C18", "C21", "CEI", "CHK", "CLK",
    "CN2", "CON", "CMM", "CSP", "DDC", "DDD", "DDP", "DGR", "DMU", "DOM",
    "DST", "EVE", "FDN", "FKF", "FOE", "FUT", "GPT", "GRN", "GK1", "GK2",
    "H16", "H17", "H18", "H20", "HOU", "IKO", "IMA", "JMP", "KTK", "KTK",
    "LIN", "LGN", "MM2", "M10", "M11", "M12", "M13", "M14", "M15", "M19",
    "M20", "M21", "M22", "M23", "M24", "M25", "MOM", "MP2", "NFO", "NPH",
    "NEO", "NLE", "OGW", "ONS", "PHI", "PKC", "RNA", "RYI", "SNC", "SOI",
    "STX", "THS", "TSP", "TDC", "TDM", "TIB", "TOR", "UNH", "VOW", "WAR",
    "WWK", "WTH", "ZNR", "ZND", "AFA", "AFD", "AFC", "AFL", "AFR", "AKR",
    "ARC", "ARB", "ARE", "ARR", "ASA", "ATH", "AVR", "BBD", "BCR", "BFA",
    "BIG", "BNG", "BO1", "BO2", "BO3", "BOS", "BOT", "BRM", "BTB", "BTD",
    "CLB", "CLB", "CMD", "COE", "COC", "COM", "CON", "COP", "CRN", "CNS",
    "CSA", "CSM", "CTP", "DBL", "DBC", "DD2", "DD3", "DD4", "DD5", "DD6",
    "DD7", "DDC", "DDD", "DDH", "DDI", "DDJ", "DDK", "DDL", "DDM", "DDN",
    "DDO", "DDP", "DDQ", "DDR", "DDS", "DDT", "DDU", "DDV", "DDW", "DDX",
    "DDY", "DDZ", "DGR", "DKA", "DKB", "DKC", "DKD", "DKE", "DKF", "DLK",
    "DMA", "DMC", "DMU", "DOM", "DSK", "DST", "DTK", "EOC", "EON", "ERA",
    "ESD", "ETH", "EVE", "EXP", "FEM", "FIR", "FLM", "FOE", "FTR", "FUN",
    "GPT", "GRN", "GTC", "GTP", "H10", "H11", "H12", "H13", "H14", "H15",
    "H16", "H17", "H18", "H19", "H20", "H21", "H22", "H23", "H24", "H25",
    "HOP", "HOU", "ICA", "IMA", "INR", "INO", "ISU", "JMP", "JMP", "KTK",
    "LIN", "LGN", "MM2", "M10", "M11", "M12", "M13", "M14", "M15", "M19",
    "M20", "M21", "M22", "M23", "M24", "M25", "MOM", "MP2", "NFO", "NPH",
    "NEO", "NLE", "NCC", "NCG", "NCS", "NPH", "NSA", "OC2", "OC2", "OC3",
    "OC4", "OGW", "OKI", "ONS", "OP2", "OTJ", "PC2", "PHI", "PKA", "PKC",
    "PKM", "PLC", "POJ", "PP1", "PP2", "PP3", "PP4", "PP5", "PP6", "PP7",
    "PP8", "PP9", "PPA", "PPB", "PPC", "PPD", "PPE", "PPF", "PPG", "PPH",
    "PPI", "PPJ", "PPK", "PPL", "PPM", "PPN", "PPO", "PPP", "PPQ", "PPR",
    "PPS", "PPT", "PPU", "PPV", "PPW", "PPX", "PPY", "PPZ", "PTK", "PZ1",
    "PZ2", "PYL", "RAV", "RNA", "RTR", "RYI", "S10", "S11", "S12", "S13",
    "S14", "S15", "S16", "S17", "S18", "S19", "S20", "S21", "S22", "S23",
    "S24", "S25", "SAR", "SCG", "SCG", "SNC", "SOI", "SPG", "SPS", "SRM",
    "STA", "STH", "STX", "SUP", "SUS", "TAB", "TAM", "TCG", "TDM", "THB",
    "THS", "THP", "THR", "TMB", "TMC", "TMG", "TMY", "TOR", "TSC", "TTD",
    "TTR", "TUST", "TUSK", "TWS", "UDC", "UMA", "UNH", "UNK", "USG", "V19",
    "V20", "V21", "V22", "V23", "V24", "V25", "V26", "V27", "V28", "V29",
    "V30", "VAN", "VAR", "VOW", "WAR", "WTH", "WWK", "ZNR", "ZND", "ZOO",
]


def _set_order_key(set_code: str) -> int:
    """Gibt eine sortierbare Zahl für einen Set-Code zurück."""
    try:
        return _SET_ORDER.index(set_code.upper())
    except ValueError:
        # Unbekannte Sets am Ende
        return len(_SET_ORDER) + len(set_code)


def _is_newer_set(newer: str, older: str) -> bool:
    """Prüft ob neueres Set als älteres."""
    return _set_order_key(newer) > _set_order_key(older)


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


def _warnings(unresolved: list[dict[str, Any]], ambiguous: list[dict[str, Any]]) -> list[str]:
    warnings: list[str] = []
    if unresolved:
        warnings.append("unresolved-deck-lines")
    if ambiguous:
        warnings.append("ambiguous-card-names")
    return warnings


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
