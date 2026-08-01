"""Workflow-Status prüfen und zusammenstellen.

Liefert einen einheitlichen Status-Dict mit allen Phasen,
damit GUI und CLI den aktuellen Zustand anzeigen können.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from advisor.llm_config import LLMConfig, load_config


@dataclass
class PhaseStatus:
    name: str
    available: bool
    detail: str = ""
    warnings: list[str] = field(default_factory=list)


@dataclass
class WorkflowStatus:
    timestamp: str
    output_dir: str
    phases: list[PhaseStatus] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "outputDir": self.output_dir,
            "phases": [
                {
                    "name": p.name,
                    "available": p.available,
                    "detail": p.detail,
                    "warnings": p.warnings,
                }
                for p in self.phases
            ],
        }


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _file_status(path: Path, label: str) -> PhaseStatus:
    if not path.exists():
        return PhaseStatus(name=label, available=False, detail=f"{label} nicht gefunden")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PhaseStatus(name=label, available=True, detail=f"{path.name} ({len(json.dumps(data))} Bytes)")
    except (OSError, json.JSONDecodeError) as exc:
        return PhaseStatus(name=label, available=False, detail=f"{path.name} ungültig: {exc}")


def _check_llm_reachable(config: LLMConfig) -> PhaseStatus:
    """Teste ob LLM-Endpoint erreichbar ist."""
    import urllib.request
    import urllib.error

    url = config.endpoint
    if not url:
        return PhaseStatus(name="LLM-Endpoint", available=False, detail="Kein Endpoint konfiguriert")

    try:
        # Check /v1/models (GET) — der Chat-Completion-Endpoint ist POST-only und gibt 404 auf GET
        models_url = config.endpoint.replace("/v1/chat/completions", "/v1/models")
        req = urllib.request.Request(models_url, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return PhaseStatus(
                    name="LLM-Endpoint",
                    available=True,
                    detail=f"Erreichbar ({resp.status})",
                )
            return PhaseStatus(name="LLM-Endpoint", available=False, detail=f"HTTP {resp.status}")
    except Exception as exc:
        return PhaseStatus(
            name="LLM-Endpoint",
            available=False,
            detail=f"Nicht erreichbar: {exc}",
        )


def collect_workflow_status(output_dir: Path) -> WorkflowStatus:
    """Sammle den vollständigen Workflow-Status."""
    status = WorkflowStatus(timestamp=_iso_now(), output_dir=str(output_dir))

    # Phase 1: Collection
    collection_path = output_dir / "collection.json"
    status.phases.append(_file_status(collection_path, "Collection"))

    # Phase 2: Decks
    decks_path = output_dir / "decks.json"
    status.phases.append(_file_status(decks_path, "Decks"))

    # Phase 3: Container-Deck-Export
    container_index = output_dir / "container" / "index.json"
    if container_index.exists():
        try:
            data = json.loads(container_index.read_text(encoding="utf-8"))
            deck_count = data.get("deckCount", 0)
            status.phases.append(
                PhaseStatus(name="Container", available=True, detail=f"{deck_count} Decks")
            )
        except Exception:
            status.phases.append(PhaseStatus(name="Container", available=False, detail="Ungültig"))
    else:
        status.phases.append(PhaseStatus(name="Container", available=False, detail="Kein Container"))

    # Phase 4: Advisor-Result
    advisor_path = output_dir / "advisor-result.json"
    status.phases.append(_file_status(advisor_path, "Advisor-Result"))

    # Phase 5: LLM-Erreichbarkeit
    config = load_config()
    status.phases.append(_check_llm_reachable(config))

    return status


def format_status_table(status: WorkflowStatus) -> str:
    """Formatiere den Status als lesbares ASCII-Table."""
    lines = [
        f"MTGA Advisor Workflow Status",
        f"Ausgabeverzeichnis: {status.output_dir}",
        f"Timestamp: {status.timestamp}",
        "",
        f"{'Phase':<20} {'Status':<10} {'Detail'}",
        "-" * 70,
    ]

    for phase in status.phases:
        icon = "✓" if phase.available else "✗"
        warnings = ""
        if phase.warnings:
            warnings = "  ⚠ " + "; ".join(phase.warnings)
        lines.append(f"{phase.name:<20} {icon:<10} {phase.detail}{warnings}")

    return "\n".join(lines)
