"""Workflow-Orchestrierung: Scan → Deck-Export → Advisor-Complete → Advisor-LLM."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workflow.status import collect_workflow_status, format_status_table, WorkflowStatus


def run_workflow(
    output_dir: Path,
    *,
    skip_scan: bool = False,
    skip_decks: bool = False,
    skip_advisor_complete: bool = False,
    skip_advisor_llm: bool = False,
    llm_endpoint: str | None = None,
) -> dict[str, Any]:
    """Führe den kompletten Workflow aus.

    Reihenfolge:
    1. Scan/Collection (memory or log-based)
    2. Deck-Export (StartHook)
    3. advisor complete (regelbasiert)
    4. advisor llm (optional)
    5. Status-Anzeige

    Gibt einen Status-Report zurück.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    from parser.log_paths import PathConfig as _PathConfig, detect_platform, discover_logs
    from parser.log_paths import MissingLogsError as _MissingLogsError

    # Phase 1: Collection
    collection_result = {}
    if not skip_scan:
        from scanner.memory_scanner import scan_collection_detailed, write_collection_artifacts
        from parser.export import export_collection

        try:
            platform = detect_platform()
            if platform == "macos":
                # Memory-Scan versuchen
                result = scan_collection_detailed(
                    input_fn=lambda prompt: "Y",
                    print_fn=lambda *args, **kwargs: None,
                    debug=False,
                )
                if result:
                    col_path, report_path = write_collection_artifacts(
                        result.collection, output_dir, scan_result=result
                    )
                    collection_result = {"source": "memory-scan", "collection_path": str(col_path)}
                    print(f"  Collection: Memory-Scan erfolgreich → {col_path}")
                else:
                    raise RuntimeError("Memory-Scan leer")

            # Fallback: Log-basierter Export
            if not collection_result:
                cfg = _PathConfig()
                discovery = discover_logs(platform, cfg)
                log_paths = discovery.found
                export_paths = export_collection(log_paths, output_dir)
                collection_result = {"source": "logs", "collection_path": str(export_paths.collection)}
                print(f"  Collection: Log-Export erfolgreich → {export_paths.collection}")
        except (_MissingLogsError, FileNotFoundError) as exc:
            collection_result = {"source": "error", "error": str(exc)}
            print(f"  Collection: {exc}")
        except Exception as exc:
            collection_result = {"source": "error", "error": str(exc)}
            print(f"  Collection: Fehler → {exc}")
    else:
        collection_result = {"source": "skipped"}

    # Phase 2: Deck-Export
    decks_result = {}
    if not skip_decks:
        from parser.decks import export_decks

        try:
            platform = detect_platform()
            cfg = _PathConfig()
            discovery = discover_logs(platform, cfg)
            log_paths = discovery.found
            export_paths = export_decks(log_paths, output_dir)
            decks_result = {"source": "StartHook", "decks_path": str(export_paths.decks)}
            print(f"  Decks: StartHook-Export erfolgreich → {export_paths.decks}")
        except (_MissingLogsError, FileNotFoundError) as exc:
            decks_result = {"source": "error", "error": str(exc)}
            print(f"  Decks: {exc}")
        except Exception as exc:
            decks_result = {"source": "error", "error": str(exc)}
            print(f"  Decks: Fehler → {exc}")
    else:
        decks_result = {"source": "skipped"}

    # Phase 3: Advisor Complete
    advisor_complete_result = {}
    if not skip_advisor_complete:
        from advisor.completion import build_completion_advice, load_json, write_advisor_result
        from scanner.card_database import load_card_database

        collection = load_json(output_dir / "collection.json")
        if collection:
            try:
                card_db = load_card_database()
                # Finde ein Deck für die Analyse
                deck_path = output_dir / "arena_deck.json"
                if deck_path.exists():
                    deck = load_json(deck_path)
                    result = build_completion_advice(
                        collection=collection,
                        deck=deck,
                        card_db=card_db,
                    )
                    result_path = write_advisor_result(result, output_dir)
                    advisor_complete_result = {
                        "source": "complete",
                        "advisor_path": result_path,
                        "completion_score": result["summary"]["completionScore"],
                    }
                    print(f"  Advisor: Regelbasiert → {result['summary']['completionScore']}% Completion")
                else:
                    advisor_complete_result = {"source": "no-deck"}
                    print("  Advisor: Kein arena_deck.json vorhanden")
            except Exception as exc:
                advisor_complete_result = {"source": "error", "error": str(exc)}
                print(f"  Advisor: Fehler → {exc}")
        else:
            advisor_complete_result = {"source": "no-collection"}
            print("  Advisor: Keine collection.json")
    else:
        advisor_complete_result = {"source": "skipped"}

    # Phase 4: Advisor LLM (optional)
    advisor_llm_result = {}
    if not skip_advisor_llm:
        from advisor.llm_advisor import run_llm_advisor
        from advisor.llm_config import LLMConfig

        cli_overrides = {}
        if llm_endpoint:
            cli_overrides["endpoint"] = llm_endpoint

        try:
            result = run_llm_advisor(
                collection_path=output_dir / "collection.json",
                decks_path=output_dir / "decks.json",
                output_dir=output_dir,
                cli_overrides=cli_overrides,
            )
            advisor_llm_result = {
                "source": "llm",
                "model": result.model,
                "warnings": result.warnings,
                "completed": len(result.rawResponse) > 0,
            }
            if result.rawResponse:
                print(f"  Advisor: LLM erfolgreich ({len(result.rawResponse)} Zeichen)")
            else:
                print("  Advisor: LLM leer")
        except Exception as exc:
            advisor_llm_result = {"source": "error", "error": str(exc)}
            print(f"  Advisor: LLM-Fehler → {exc}")
    else:
        advisor_llm_result = {"source": "skipped"}

    # Status sammeln
    final_status = collect_workflow_status(output_dir)

    report = {
        "workflow": {
            "output_dir": str(output_dir),
            "phases": {
                "collection": collection_result,
                "decks": decks_result,
                "advisor_complete": advisor_complete_result,
                "advisor_llm": advisor_llm_result,
            },
            "status": final_status.to_dict(),
        }
    }

    return report


def print_workflow_report(report: dict[str, Any]) -> None:
    """Drucke den Workflow-Report als lesbares Format."""
    workflow = report.get("workflow", {})
    status = workflow.get("status", {})

    print("\n" + "=" * 60)
    print("MTGA Advisor Workflow — Abschluss")
    print("=" * 60)

    # Status-Tabelle
    print(format_status_table(WorkflowStatus(
        timestamp=status.get("timestamp", ""),
        output_dir=status.get("outputDir", ""),
    )))

    # Phase-Ergebnisse
    phases = workflow.get("phases", {})
    for phase_name, result in phases.items():
        print(f"\n--- {phase_name} ---")
        if "error" in result:
            print(f"  FEHLER: {result['error']}")
        elif result.get("source") == "skipped":
            print("  (Übersprungen)")
        else:
            for key, value in result.items():
                if key != "error":
                    print(f"  {key}: {value}")

    print("\n" + "=" * 60)
