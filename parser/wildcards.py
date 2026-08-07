"""Wildcard-Export aus MTGA-Logs.

Extrahiert die InventoryInfo aus StartHook-Events in Player.log-Dateien
und schreibt sie als wildcards.json (Schema wildcards.v1).

Die Wildcards (WildCardCommons/UnCommons/Rares/Mythics, Gold, Gems) stecken
im StartHook-Event unter ``InventoryInfo``. Der Parser extrahiert sie bereits
via :func:`parser.start_hook.parse_start_hook` — dieses Modul sammelt sie
zeilenweise (analog zu :func:`parser.decks._extract_decks_from_raw_json_lines`)
und schreibt das Artefakt.

Referenz: mtgatool-desktop InStartHook.ts
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
from typing import Any, Iterable

from .start_hook import parse_start_hook
from .decks import _extract_json_block  # bestehender JSON-Block-Extraktor


@dataclass(frozen=True)
class WildcardExportPaths:
    """Pfade der Wildcard-Export-Artefakte."""

    wildcards: Path
    run_report: Path


def export_wildcards(paths: Iterable[Path], output_dir: Path) -> WildcardExportPaths:
    """Extrahiere Wildcards aus MTGA-Logs und schreibe Artefakte.

    Liest die Logs zeilenweise (nur Zeilen mit ``InventoryInfo`` oder
    ``DeckSummaries`` — StartHook-Events enthalten beides), extrahiert
    robust den JSON-Block und sammelt das Inventory via
    :func:`parser.start_hook.parse_start_hook`.

    Letzter Login gewinnt (wie bei Decks).

    Args:
        paths: Zu parsende Log-Dateien.
        output_dir: Ausgabeverzeichnis.

    Returns:
        WildcardExportPaths mit Pfaden zu wildcards.json und run-report.
    """
    sorted_paths = sorted({Path(path) for path in paths})
    output_dir.mkdir(parents=True, exist_ok=True)

    inventory, log_count, warnings = _extract_inventories_from_raw_json_lines(sorted_paths)

    payload = _build_wildcards_payload(inventory, sorted_paths, log_count, warnings)
    wildcards_path = output_dir / "wildcards.json"
    _write_json(wildcards_path, payload)

    run_report_path = output_dir / "run-report-wildcards.json"
    _write_json(run_report_path, _build_run_report(sorted_paths, wildcards_path, warnings))

    return WildcardExportPaths(wildcards=wildcards_path, run_report=run_report_path)


def _extract_inventories_from_raw_json_lines(
    paths: list[Path],
) -> tuple[dict[str, Any] | None, int, list[str]]:
    """Zeilenweiser Pfad: sammle das letzte Inventory aus StartHook-Events.

    Returns:
        Tuple von (inventory, logCount, warnings). ``inventory`` ist None
        wenn kein StartHook mit InventoryInfo gefunden wurde.
    """
    inventory: dict[str, Any] | None = None
    log_count = 0
    warnings: list[str] = []

    for path in paths:
        found_in_file = False
        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                for line_number, raw_line in enumerate(handle, start=1):
                    # Quick filter: nur Zeilen mit StartHook-Kennzeichen
                    if '"DeckSummaries"' not in raw_line and '"InventoryInfo"' not in raw_line:
                        continue

                    payload = _extract_json_block(raw_line)
                    if payload is None:
                        continue

                    parsed, hook_warnings = parse_start_hook(payload)
                    if hook_warnings:
                        warnings.extend(
                            f"{path.name}:{line_number}: {w}" for w in hook_warnings
                        )
                    if parsed is not None and parsed.inventory:
                        inventory = parsed.inventory
                        found_in_file = True
        except OSError as exc:
            warnings.append(f"{path.name}: konnte nicht gelesen werden ({exc})")

        if found_in_file:
            log_count += 1

    return inventory, log_count, warnings


def _build_wildcards_payload(
    inventory: dict[str, Any] | None,
    log_paths: list[Path],
    log_count: int,
    warnings: list[str],
) -> dict:
    """Baue das wildcards.json Payload (Schema wildcards.v1)."""
    wc_commons = _safe_int(inventory, "WildCardCommons")
    wc_uncommons = _safe_int(inventory, "WildCardUnCommons")
    wc_rares = _safe_int(inventory, "WildCardRares")
    wc_mythics = _safe_int(inventory, "WildCardMythics")
    gold = _safe_int(inventory, "Gold")
    gems = _safe_int(inventory, "Gems")
    vault = _safe_int(inventory, "TotalVaultProgress")

    missing_fields: list[str] = []
    if wc_commons is None:
        missing_fields.append("WildCardCommons")
    if wc_uncommons is None:
        missing_fields.append("WildCardUnCommons")
    if wc_rares is None:
        missing_fields.append("WildCardRares")
    if wc_mythics is None:
        missing_fields.append("WildCardMythics")

    final_warnings: list[str] = list(warnings)
    if inventory is None:
        final_warnings.append("Kein StartHook mit InventoryInfo in den Logs gefunden.")
    if missing_fields:
        final_warnings.append(
            "Fehlende Wildcard-Felder im Inventory: " + ", ".join(missing_fields)
        )

    return {
        "schema": "wildcards.v1",
        "source": "local-logs",
        "wildcards": {
            "commons": wc_commons,
            "uncommons": wc_uncommons,
            "rares": wc_rares,
            "mythics": wc_mythics,
        },
        "currency": {
            "gold": gold,
            "gems": gems,
        },
        "vaultProgress": vault,
        "diagnostics": {
            "logCount": log_count,
            "logs": [p.as_posix() for p in log_paths],
            "warnings": final_warnings,
        },
    }


def _build_run_report(
    log_paths: list[Path],
    wildcards_path: Path,
    warnings: list[str],
) -> dict:
    """Baue den run-report für Wildcard-Export."""
    from .decks import _iso_now

    ts = _iso_now()
    return {
        "schema": "run-report-wildcards.v1",
        "runId": f"run-wildcards-{ts}",
        "startedAt": ts,
        "finishedAt": ts,
        "source": "local-logs",
        "logs": [p.as_posix() for p in log_paths],
        "outputs": {
            "wildcards": wildcards_path.as_posix(),
        },
        "diagnostics": {
            "warnings": warnings,
            "evidence": ["start-hook-parser"],
        },
    }


def _safe_int(data: dict[str, Any] | None, key: str) -> int | None:
    """Extrahiere einen int-Wert sicher (None bei fehlendem/falschem Typ)."""
    if data is None:
        return None
    value = data.get(key)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )