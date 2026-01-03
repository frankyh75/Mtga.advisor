from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[2]))

from parser.log_paths import (  # noqa: E402
    MissingLogsError,
    OS_MATRIX,
    PathConfig,
    build_strategies,
    build_validation_tasks,
    discover_logs,
)


def test_missing_logs_error_includes_hint(tmp_path: Path) -> None:
    config = PathConfig(windows_local_low=tmp_path / "LocalLow" / "MTGA")

    with pytest.raises(MissingLogsError) as excinfo:
        discover_logs("windows", config)

    message = str(excinfo.value)
    assert "Keine MTGA-Logdateien gefunden" in message
    assert "LocalLow" in message


def test_validation_tasks_for_unresolved_strategies(tmp_path: Path) -> None:
    config = PathConfig(
        windows_local_low=tmp_path / "LocalLow" / "MTGA",
        windows_steam_userdata=tmp_path / "Steam" / "userdata",
    )
    strategies = build_strategies("windows", config)

    tasks = build_validation_tasks(strategies, found=[])

    task_ids = {task.task_id for task in tasks}
    assert "validate-windows-standalone" in task_ids
    assert "validate-windows-steam" in task_ids


def test_os_matrix_maps_platform_variants() -> None:
    assert OS_MATRIX["windows"] == ("standalone", "steam")
    assert OS_MATRIX["macos"] == ("standalone", "steam")
